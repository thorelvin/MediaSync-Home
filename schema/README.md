# Maskinlesbare kontrakter

Alle filer i denne katalogen er utkast eller blokkerte plassholdere. Status og eierport finnes i `contracts-manifest.yaml`.

`catalog.sql` og `recovery.sql` er bevisst ikke implementert. 0A.4 skal først måle én eller to databaser; Milepæl 1 produserer deretter faktiske migrasjoner, composite constraints og negative tester. Kommentarplassholderne må aldri brukes som produktdatabase.

## Autoritetsregel

En kontrakt blir autoritativ bare når:

1. styrende ADR-er har `owner_decision = OWNER_ACCEPTED`;
2. kontrakten har eksplisitt schema-/protokollversjon;
3. manifeststatus er `frozen`;
4. validerings-, kompatibilitets- og driftstester består;
5. dokumentasjon, genererte typer og migrasjoner er synkronisert.

Codex kan ikke sette `OWNER_ACCEPTED` på vegne av prosjekteieren.

## Kanonisk JSON og hashes

For kontrakter som bruker `JCS-RFC8785`:

- objektet serialiseres med JSON Canonicalization Scheme (RFC 8785);
- UTF-8 uten BOM brukes;
- ingen egen Unicode-normalisering legges til uten en ny kontraktversjon;
- hashfeltet som skal beregnes, utelates fra objektet før kanonisering;
- algoritme-, scope- og canonicalization-felter er med i det kanoniske objektet;
- `BLAKE3-256` uttrykkes som 64 små heksadesimale tegn.

`marker_checksum` beregnes over hele endpointmarkøren uten `marker_checksum`-feltet. `payload_hash` beregnes bare over `payload`-objektet.

JSON Schema-validering skal bruke en Draft 2020-12-validator med formatkontroll aktivert, slik at `uuid` og `date-time` faktisk håndheves.
