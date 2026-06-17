import argparse
import asyncio
import logging
import os

from automation_server_client import (
    AutomationServer,
    Workqueue,
    Credential,
    WorkItemStatus,
)
from kmd_nexus_client import NexusClientManager
from datafordeler import Datafordeler as DatafordelerClient
from odk_tools.tracking import Tracker
from odk_tools.reporting import report
from rapidfuzz.distance import Levenshtein


logger = logging.getLogger(__name__)
nexus: NexusClientManager | None = None
datafordeler: DatafordelerClient | None = None
tracker: Tracker | None = None
proces_navn = "Inaktivering af CPR-leverandører i Nexus"


def beregn_levenshtein(nexus_adresse: str, datafordeler_adresse: str) -> float:
    """Beregn Levenshtein-afstand som procentdel af Nexus-navnets længde.

    Matcher Blue Prism-logikken: (distance / Len(nexus_navn)) * 100.
    Returnerer en procentværdi, f.eks. 9.5 for 9.5%.
    """
    if not nexus_adresse or not datafordeler_adresse:
        raise ValueError("Begge adresser skal være ikke-tomme strenge.")

    distance = Levenshtein.distance(nexus_adresse.lower(), datafordeler_adresse.lower())
    return (distance / len(nexus_adresse)) * 100


async def populate_queue(workqueue: Workqueue):
    logger.info("Henter aktive CPR-leverandører fra Nexus...")

    alle_leverandører = nexus.nexus_client.get(
        "https://odensekommune.nexus.kmd.dk/api/core/mobile/odensekommune/v2/suppliers"
    ).json()

    # cvrNumber kan være None eller en tom streng for CPR-leverandører, så vi tjekker begge muligheder. Tak Nexus...
    aktive_leverandører = [
        leverandør
        for leverandør in alle_leverandører
        if (leverandør.get("cvrNumber") == "" or leverandør.get("cvrNumber") is None)
        and leverandør.get("active")
        and leverandør.get("type") == "external"
    ]

    for leverandør in aktive_leverandører:
        self_leverandør = nexus.nexus_client.get(
            leverandør["_links"]["self"]["href"]
        ).json()
        if self_leverandør["cprNumber"] is None or self_leverandør["cprNumber"] == "":
            continue
        data = {
            "id": leverandør["id"],
            "CPR": self_leverandør["cprNumber"],
            "navn": leverandør["name"],
            "adresse": self_leverandør.get("address", {}).get("addressLine1", ""),
            "postnummer": self_leverandør.get("address", {}).get("postalCode", ""),
            "postdistrikt": self_leverandør.get("address", {}).get(
                "postalDistrict", ""
            ),
        }
        workqueue.add_item(data=data, reference=data["CPR"])


async def process_workqueue(workqueue: Workqueue):
    logger.info("Behandler arbejdskø...")

    for item in workqueue:
        with item:
            data = item.data

            try:
                borger_status = datafordeler.hent_personoplysninger(data["CPR"])
                if (
                    borger_status["Person"]["status"] != "bopael_i_danmark"
                ):  # svarer til status "01" i Service platform format
                    leverandør = nexus.nexus_client.get(
                        f"https://odensekommune.nexus.kmd.dk:443/api/core/mobile/odensekommune/v2/suppliers/{data['id']}"
                    ).json()
                    leverandør = nexus.nexus_client.get(
                        leverandør["_links"]["self"]["href"]
                    ).json()
                    leverandør["active"] = False
                    nexus.organisationer.opdater_leverandør(leverandør)
                    tracker.track_task(process_name=proces_navn)
                    continue

                borgers_navne = next(
                    (
                        navn
                        for navn in borger_status["Person"]["Navne"]
                        if navn["Navn"]["status"] == "aktuel"
                    ),
                    None,
                )
                if not borgers_navne:
                    report(
                        report_id="inaktivering_af_cpr_leverandoerer_i_nexus",
                        group="Manuel",
                        json={
                            "CPR": data["CPR"],
                            "Årsag": "Datafordeler kan ikke finde et 'aktuelt' navn - så vi kan ikke være sikre",
                        },
                    )
                    continue

                # Finder borgers navn fra datafordeleren
                efternavn = borgers_navne["Navn"]["efternavn"]
                fornavn = borgers_navne["Navn"]["fornavne"]
                mellemnavn = borgers_navne["Navn"].get("mellemnavn", "")
                navn_dele = (
                    [fornavn, mellemnavn, efternavn]
                    if mellemnavn
                    else [fornavn, efternavn]
                )
                kombineret_navn = " ".join(navn_dele)

                if data["navn"] != kombineret_navn:
                    report(
                        report_id="inaktivering_af_cpr_leverandoerer_i_nexus",
                        group="Manuel",
                        json={
                            "CPR": data["CPR"],
                            "Årsag": "Navnet i Nexus matcher ikke navnet i Datafordeleren - så vi kan ikke være sikre",
                            "Nexus_navn": data["navn"],
                            "Datafordeler_navn": kombineret_navn,
                        },
                    )
                    continue

                borgers_datafordeler_adresse = datafordeler.hent_aktiv_adresse(
                    data["CPR"]
                )
                borgers_datafordeler_adresse_formateret = datafordeler.formater_adresse(
                    borgers_datafordeler_adresse["Adresseoplysninger"]
                )
                borgers_nexus_adresse = (
                    data["adresse"]
                    + ", "
                    + data["postnummer"]
                    + " "
                    + data["postdistrikt"]
                )
                if borgers_datafordeler_adresse_formateret == borgers_nexus_adresse:
                    continue
                else:
                    levenshtein_procent = beregn_levenshtein(
                        borgers_nexus_adresse, borgers_datafordeler_adresse_formateret
                    )
                    # Hvis Levenshtein-afstanden er 10% eller mindre, antager vi at det er en stavefejl og fortsætter uden manuel gennemgang
                    if levenshtein_procent <= 10:
                        continue
                    report(
                        report_id="inaktivering_af_cpr_leverandoerer_i_nexus",
                        group="Manuel",
                        json={
                            "CPR": data["CPR"],
                            "Årsag": "Adressen i Nexus matcher ikke adressen i Datafordeleren - så vi kan ikke være sikre",
                            "Nexus_adresse": borgers_nexus_adresse,
                            "Datafordeler_adresse": borgers_datafordeler_adresse_formateret,
                        },
                    )
                    continue
            except ValueError as e:
                logger.warning(
                    f"Borger med CPR {data['CPR']} blev ikke fundet i Datafordeleren: {e}"
                )
                report(
                    report_id="inaktivering_af_cpr_leverandoerer_i_nexus",
                    group="Manuel",
                    json={
                        "CPR": data["CPR"],
                        "Årsag": "Borger ikke fundet i Datafordeleren - så vi kan ikke være sikre",
                    },
                )
            except Exception as e:
                logger.error(f"Fejl ved behandling af element: {data}. Fejl: {e}")
                item.fail(str(e))
                tracker.track_partial_task(process_name=proces_navn)


async def main():
    global nexus, datafordeler, tracker

    ats = AutomationServer.from_environment()
    workqueue = ats.workqueue()

    tracking_credential = Credential.get_credential("Odense SQL Server")
    nexus_credential = Credential.get_credential("KMD Nexus - produktion")

    tracker = Tracker(
        username=tracking_credential.username, password=tracking_credential.password
    )

    nexus = NexusClientManager(
        client_id=nexus_credential.username,
        client_secret=nexus_credential.password,
        instance=nexus_credential.data["instance"],
        timeout=60,
    )

    certifikat_sti = os.getenv("CERTIFIKATER", "/certifikater")
    datafordeler = DatafordelerClient(
        certifikat_sti=os.path.join(certifikat_sti, "datafordeler.crt"),
        certifikat_nøglefil=os.path.join(certifikat_sti, "datafordeler.key"),
    )

    parser = argparse.ArgumentParser(description=proces_navn)
    parser.add_argument(
        "--queue",
        action="store_true",
        help="Fyld arbejdskøen med aktive CPR-leverandører og afslut",
    )
    args = parser.parse_args()

    if args.queue:
        workqueue.clear_workqueue(WorkItemStatus.NEW)
        await populate_queue(workqueue)
        return

    await process_workqueue(workqueue)


if __name__ == "__main__":
    asyncio.run(main())
