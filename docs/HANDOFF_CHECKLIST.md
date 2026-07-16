# Overleveringssjekkliste før Codex

## 1. Etabler en ren baseline

- Pakk spesifikasjonen ut i repository-roten.
- Installer eksakte valideringsavhengigheter med `python -m pip install -r requirements-handoff.txt`.
- Kjør `python tools/validate_handoff.py --verify-bundle` og lagre resultatet før første endring. Manglende bibliotek er en feil, ikke en advarsel.
- Bekreft at repositoryet ikke inneholder hemmeligheter, personlige filstier eller reelle NAS-legitimasjoner.
- Bekreft at `git status` bare viser den forventede spesifikasjonspakken.
- Opprett en baseline-commit før Codex endrer noe.

Anbefalt navngivning:

```text
chore/spec-baseline-v2.9.2
spike/0a0-environment-preflight
spike/0a1-process-and-ipc
spike/0a2-endpoint-ownership
spike/0a3-recovery-and-paths
spike/0a4-sqlite-and-capacity
spike/0a5-windows-argv-and-packaging
spike/0a6-decision-review
```

## 2. Bekreft labgrenser

Filsystemprober kan bare bruke en dedikert labrot. Roten må inneholde `.mediasync_test_root` med minst:

```json
{
  "purpose": "MEDIASYNC_ARCHITECTURE_SPIKE",
  "run_id": "00000000-0000-0000-0000-000000000000",
  "created_utc": "2026-07-15T00:00:00Z",
  "expected_root_identity": "lab-specific-identity",
  "cleanup_allowed": true
}
```

Før mutasjon eller cleanup skal harnesset kontrollere:

- at markøren finnes og kan parses;
- at `purpose` er korrekt;
- at `run_id` matcher aktiv kjøring;
- at faktisk rotidentitet matcher `expected_root_identity`;
- at stien ikke er diskrot, brukerprofilens standardmapper eller en produksjonsdeling;
- at cleanup er uttrykkelig tillatt.

Ved avvik skal harnesset feile lukket og ikke endre noe.

## 3. Kjør bare én arbeidspakke

Gjeldende arbeidspakke står i `AGENTS.md`. Codex skal ikke selv endre den til neste arbeidspakke og fortsette.

For hver arbeidspakke:

1. opprett egen branch;
2. bruk arbeidspakkens tillatte filliste;
3. registrer eksakte kommandoer og råartefakter;
4. marker hvert eksperiment `PASS`, `FAIL`, `BLOCKED` eller `INCONCLUSIVE`;
5. gjennomgå diff og rapport;
6. commit eller forkast arbeidet;
7. velg neste arbeidspakke manuelt.

## 4. Eierport for ADR-er

Codex kan oppdatere `evidence_status` med:

```text
PROPOSED
EVIDENCE_COMPLETE
RECOMMENDED
BLOCKED
```

Bare prosjekteieren kan oppdatere `owner_decision` med:

```text
PENDING
OWNER_ACCEPTED
REJECTED
DEFERRED_WITH_SCOPE_REDUCTION
SUPERSEDED
```

Ingen kontrakt kan få status `frozen` før tilhørende ADR-er har `owner_decision = OWNER_ACCEPTED`.

## 5. Delvise blockers

Mangler eksempelvis en andre Windows-klient, skal bare det berørte SMB-eksperimentet markeres blokkert. Codex kan fortsatt bygge ikke-muterende harness, dokumentere manuell kjøreveiledning og gjennomføre uavhengige lokale eksperimenter.

En blocker kan bare utsettes til senere dersom prosjekteieren eksplisitt reduserer produktgarantien. Eksempel: første versjon kan blokkere alle mål som eies av en annen installasjon, i stedet for å støtte takeover.

## 6. Klar for 0B

Milepæl 0B kan først åpnes når:

- alle obligatoriske 0A-arbeidspakker er `passed`, eller eksplisitt avgrenset av en eiergodkjent scope-reduksjon;
- nødvendige ADR-er har `owner_decision = OWNER_ACCEPTED`;
- ingen sikkerhetskritisk blocker skjules av mock eller antakelse;
- `docs/ARCHITECTURE_SPIKE_REPORT.md` inneholder reproduserbare bevis;
- `docs/IMPLEMENTATION_STATUS.md` og `docs/DECISION_REGISTER.md` er oppdatert;
- kontraktsmanifestet fortsatt viser utkast som utkast og bare godkjente kontrakter som `frozen`.
