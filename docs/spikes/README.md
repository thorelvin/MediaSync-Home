# Milepæl 0A — arbeidspakker

0A gjennomføres sekvensielt. Hver fil er en egen Codex-økt, branch og gjennomgang.

| Rekkefølge | Arbeidspakke | Hovedbevis |
|---:|---|---|
| 1 | `0A.0_ENVIRONMENT_PREFLIGHT.md` | Miljø, sikkerhetsgrenser og kjørbarhetsklassifisering |
| 2 | `0A.1_PROCESS_AND_IPC.md` | Engine Host discovery, named pipe og Job Object |
| 3 | `0A.2_ENDPOINT_OWNERSHIP.md` | Kontrollområde, global SMB-lock og eksklusiv writer |
| 4 | `0A.3_RECOVERY_PATHS_AND_SOURCE_GUARD.md` | Korte objektstier, replace/recovery og source TOCTOU |
| 5 | `0A.4_SQLITE_AND_CAPACITY.md` | Én kontra to databaser, krasj og én million poster |
| 6 | `0A.5_WINDOWS_ARGUMENTS_AND_PACKAGING.md` | Systemsti, argv og ren Windows-build |
| 7 | `0A.6_DECISION_REVIEW.md` | Evidenssyntese, anbefaling og eiergodkjenning |

Et blokkert eksperiment stopper ikke automatisk andre uavhengige arbeidspakker. 0B forblir blokkert til prosjekteieren har akseptert nødvendige ADR-er eller eksplisitt redusert produktscope.
