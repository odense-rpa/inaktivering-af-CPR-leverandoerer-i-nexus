# Inaktivering af CPR-leverandører i Nexus

Automatisering der validerer aktive leverandører i KMD Nexus med CPR-numre og inaktiverer leverandører, der ikke længere kan bekræftes.

1. **Henter aktive leverandører** fra KMD Nexus — kun dem med et CPR-nummer og status aktiv
2. **Validerer** hver leverandør mod Datafordeleren på tre punkter:
   - Borger har status **bopæl i Danmark** (status "01") — ellers inaktiveres leverandøren straks
   - **Navn** fra Datafordeleren stemmer 100% overens med navn i Nexus
   - **Adresse** fra Datafordeleren stemmer overens med adresse i Nexus (op til 10% Levenshtein-afstand tolereres som stavefejl)
3. **Inaktiverer** leverandører der ikke længere har bopæl i Danmark
4. **Sender til manuel behandling** leverandører hvor navn eller adresse ikke kan bekræftes

## Forudsætninger

- Python ≥ 3.13
- [`uv`](https://docs.astral.sh/uv/) til pakkehåndtering
- Adgang til **Automation Server** (arbejdskø)
- Adgang til **KMD Nexus** (produktion)
- Adgang til **Datafordeleren** med gyldigt klientcertifikat
- En **Odense SQL Server**-konto til tracking

## Konfiguration

Kopiér `.env.example` til `.env` og udfyld følgende:

| Variabel | Beskrivelse |
| -------- | ----------- |
| `CERTIFIKATER` | Sti til mappe med `datafordeler.crt` og `datafordeler.key` |
| _(Automation Server-variabler)_ | Ifølge `automation-server-client`-dokumentationen |

## Brug

```sh
# Fyld arbejdskøen med aktive CPR-leverandører fra Nexus
uv run python main.py --queue

# Behandl arbejdskøen
uv run python main.py
```

| Argument  | Beskrivelse                                       |
| --------- | ------------------------------------------------- |
| `--queue` | Fyld arbejdskøen og afslut (kør ingen behandling) |

## Afhængigheder

| Pakke                      | Formål                            |
| -------------------------- | --------------------------------- |
| `automation-server-client` | Arbejdskø-håndtering              |
| `kmd-nexus-client`         | Integration med KMD Nexus         |
| `datafordeler`             | CPR-opslag via Datafordeleren     |
| `odk-tools`                | Aktivitetssporing og rapportering |
| `rapidfuzz`                | Levenshtein-beregning til adressesammenligning |

## Manuel behandling

Elementer der ikke kan bekræftes automatisk sendes til rapporten `inaktivering_af_cpr_leverandoerer_i_nexus` under gruppen **Manuel**. Det sker i tre tilfælde:

| Årsag | Handling |
| ----- | -------- |
| Datafordeleren kan ikke finde et aktuelt navn | Verificér manuelt og inaktivér evt. i Nexus |
| Navn i Nexus stemmer ikke 100% overens med Datafordeleren | Verificér om det er samme person og ret evt. navn |
| Adresse i Nexus afviger mere end 10% fra Datafordeleren | Verificér om det er samme person og ret evt. adresse |

## Sikkerhed og GDPR

CPR-numre og tilhørende personoplysninger (navn, adresse) behandles i overensstemmelse med GDPR og Databeskyttelsesloven § 11.

- Robotten **inaktiverer kun** — den sletter eller eksporterer ikke persondata
- Personoplysninger gemmes midlertidigt i arbejdskøen under behandling — verificér retentionspolitikken i Automation Server
- Elementer sendt til manuel behandling indeholder CPR-nummer — sikr at rapporten kun er tilgængelig for autoriserede medarbejdere
- Ingen legitimationsoplysninger må lægges i dette repository
- `.env`-filen er ekskluderet via `.gitignore` og må aldrig committes
- Legitimationsoplysninger håndteres udelukkende via miljøvariabler (`.env`) og Automation Server Credentials
