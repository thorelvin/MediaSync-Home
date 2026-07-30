from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum


class LanguageCode(str, Enum):
    NORWEGIAN = "nb"
    ENGLISH = "en"


@dataclass(frozen=True)
class ShellText:
    local_preview: str
    refresh_engine_status: str
    language_tooltip_prefix: str
    dashboard: str
    jobs: str
    history: str
    settings: str
    setup_title: str
    setup_subtitle: str
    setup_steps: tuple[str, str, str, str]
    source: str
    target: str
    defaults: str
    retention: str
    revision: str
    create_backup_tooltip: str
    job_detail_targets_heading: str
    engine_host: str
    scope: str
    contract: str
    mutation_policy: str
    activity: str
    no_active_runs: str
    attention: str
    target_freshness: str
    next_action: str


NB_TEXT = ShellText(
    local_preview="Lokal forhåndsvisning",
    refresh_engine_status="Oppdater motorstatus",
    language_tooltip_prefix="Språk",
    dashboard="Oversikt",
    jobs="Jobber",
    history="Historikk",
    settings="Innstillinger",
    setup_title="Lag din første backup",
    setup_subtitle="Velg én mappe og opptil tre mål. Sikker standard er valgt.",
    setup_steps=(
        "Hva vil du beskytte?",
        "Hvor vil du ha kopier?",
        "Hvordan skal backupen fungere?",
        "Kontroller og opprett",
    ),
    source="Kilde",
    target="Mål",
    defaults="Standard",
    retention="Bevaring",
    revision="Revisjon",
    create_backup_tooltip="Opprett backup når kilde og minst ett mål er valgt",
    job_detail_targets_heading="Målsteder",
    engine_host="Engine Host",
    scope="Scope",
    contract="Kontrakt",
    mutation_policy="Mutasjonspolicy",
    activity="Aktivitet",
    no_active_runs="Ingen aktive kjøringer",
    attention="Oppmerksomhet",
    target_freshness="Ferskhet per mål",
    next_action="Neste handling",
)


EN_TEXT = ShellText(
    local_preview="Local preview",
    refresh_engine_status="Refresh engine status",
    language_tooltip_prefix="Language",
    dashboard="Dashboard",
    jobs="Jobs",
    history="History",
    settings="Settings",
    setup_title="Create your first backup",
    setup_subtitle="Choose one folder and up to three targets. Safe defaults are selected.",
    setup_steps=(
        "What do you want to protect?",
        "Where should copies go?",
        "How should backup work?",
        "Review and create",
    ),
    source="Source",
    target="Target",
    defaults="Defaults",
    retention="Retention",
    revision="Revision",
    create_backup_tooltip="Create backup when source and at least one target are selected",
    job_detail_targets_heading="Target locations",
    engine_host="Engine Host",
    scope="Scope",
    contract="Contract",
    mutation_policy="Mutation policy",
    activity="Activity",
    no_active_runs="No active runs",
    attention="Attention",
    target_freshness="Freshness per target",
    next_action="Next action",
)


_NB_TO_EN = {
    "Aldri kontrollert": "Never checked",
    "Alle brukerfiler": "All user files",
    "Auto - anbefalt": "Auto - recommended",
    "Avvist": "Rejected",
    "Bevaring": "Retention",
    "Blokkert": "Blocked",
    "Blokkerende problem": "Blocking issue",
    "Dekningsadvarsel": "Coverage warning",
    "Ekstra filer på målet beholdes": "Extra files on the target are kept",
    "Fortsett": "Continue",
    "Følg fremdrift per mål.": "Follow progress per target.",
    "Følg opp NAS etter aktiv kopiering.": "Follow up NAS after active copying.",
    "Gjenoppretter": "Recovering",
    "Ingen aktiv jobbrevisjon funnet.": "No active job revision found.",
    "Ingen aktiv revisjon": "No active revision",
    "Ingen aktive kjøringer": "No active runs",
    "Ingen backupjobb": "No backup job",
    "Ingen forseglet plan å vise.": "No sealed plan to show.",
    "Ingen forseglede planendepunkter å vise.": "No sealed plan endpoints to show.",
    "Ingen handling kreves nå.": "No action is required now.",
    "Ingen kilde valgt": "No source selected",
    "Ingen kildesnapshot å vise.": "No source snapshot to inspect.",
    "Ingen lagret backupjobb": "No saved backup job",
    "Ingen mål konfigurert": "No targets configured",
    "Ingen mål valgt": "No targets selected",
    "Ingen mål å vise.": "No targets to show.",
    "Ingen endepunkter.": "No endpoint rows.",
    "Ingen planrader.": "No plan operations.",
    "Ingen snapshothelserader.": "No snapshot health rows.",
    "Inaktiv": "Inactive",
    "Ikke konfigurert": "Not configured",
    "Innstillinger": "Settings",
    "Jobben finnes ikke": "Job not found",
    "Jobber": "Jobs",
    "Kilde": "Source",
    "Kildeendepunkt": "Source endpoint",
    "Kilde mangler": "Source missing",
    "Klar": "Ready",
    "Koble til NAS.": "Connect NAS.",
    "Kontroller backupen når analysefunksjonen er tilgjengelig.": (
        "Check the backup when analysis is available."
    ),
    "Kontroller resultatet før neste backup.": "Check the result before the next backup.",
    "Kontrollerer": "Checking",
    "Kontrollerer mål før lease og revalidering.": "Checking targets before lease and revalidation.",
    "Kontrollerer måltilgang.": "Checking target access.",
    "Kontrakt": "Contract",
    "Kopierer": "Copying",
    "Kopiering pågår.": "Copying is in progress.",
    "Lag din første backup": "Create your first backup",
    "Lav": "Low",
    "Lokal forhåndsvisning": "Local preview",
    "Mål": "Target",
    "Målendepunkt": "Target endpoint",
    "Målsteder": "Target locations",
    "Mutasjoner aktivert": "Mutations enabled",
    "Mutasjonspolicy": "Mutation policy",
    "Neste handling": "Next action",
    "Normal": "Normal",
    "Oppdatert": "Up to date",
    "Oppdater backup": "Update backup",
    "Opprett backup når kilde og mål er klare.": "Create backup when source and target are ready.",
    "Opprett eller velg en backupjobb.": "Create or select a backup job.",
    "Opprett og kontroller endringer": "Create and check changes",
    "Opprett mappe": "Create folder",
    "Pauset": "Paused",
    "Planforhåndsvisning": "Plan preview",
    "Planendepunkter": "Plan endpoints",
    "Planendepunktvisningen er ikke tilgjengelig.": "Plan endpoint read model is not available.",
    "Planvisningen er ikke tilgjengelig.": "Plan read model is not available.",
    "Protokoll utilgjengelig": "Protocol unavailable",
    "Protokoll venter": "Protocol pending",
    "Revisjon": "Revision",
    "Se gjennom blokkeringen før ny kjøring.": "Review the block before a new run.",
    "Se gjennom målfeilen.": "Review the target error.",
    "Sist sikkerhetskopiert": "Last backed up",
    "Skrivebeskyttet lokal forhåndsvisning": "Read-only local preview",
    "Skrivebeskyttet målendepunkt": "Read-only target endpoint",
    "Snapshothelse": "Snapshot health",
    "Snapshothelsevisningen er ikke tilgjengelig.": "Snapshot health read model is not available.",
    "Standard": "Defaults",
    "Standard kontroll": "Standard verification",
    "Standardvalg ikke lastet": "Defaults not loaded",
    "Starter": "Starting",
    "Tidligere versjoner beholdes i 30 dager": "Previous versions are kept for 30 days",
    "Til vurdering": "Review",
    "Tilkoblet": "Connected",
    "Trenger oppmerksomhet": "Needs attention",
    "Ukjent": "Unknown",
    "Historikk": "History",
    "Aktivitet": "Activity",
    "Oppmerksomhet": "Attention",
    "Oversikt": "Dashboard",
    "Ferskhet per mål": "Freshness per target",
    "Verifiserer": "Verifying",
    "Vent på neste statusoppdatering.": "Wait for the next status update.",
    "Vent til kopiering er ferdig.": "Wait until copying is finished.",
    "Venter": "Waiting",
    "Venter på lokal 0B-kjøringsmotor.": "Waiting for the local 0B run engine.",
    "Venter på målbehandling.": "Waiting for target processing.",
    "Venter på analyse og kjøring.": "Waiting for analysis and run.",
}

_EN_TO_NB = {value: key for key, value in _NB_TO_EN.items()}
_EN_TO_NB.update(
    {
        "Already current": "Allerede oppdatert",
        "Blocked": "Blokkert",
        "Copy new": "Kopier ny",
        "Create folder": "Opprett mappe",
        "Deferred": "Utsatt",
        "Disconnected": "Frakoblet",
        "Engine Host is not connected yet.": "Engine Host er ikke tilkoblet ennå.",
        "Engine Host is reachable and reporting health.": (
            "Engine Host er tilgjengelig og rapporterer helse."
        ),
        "Engine Host rejected the status request": "Engine Host avviste statusforespørselen",
        "Engine Host responded without a status payload.": (
            "Engine Host svarte uten statusinnhold."
        ),
        "Engine status is unavailable.": "Motorstatus er utilgjengelig.",
        "Cataloged files": "Katalogf\u00f8rte filer",
        "Catalog read model is not available.": "Katalogvisningen er ikke tilgjengelig.",
        "Choose a source folder.": "Velg en kildemappe.",
        "Choose a target folder.": "Velg en målmappe.",
        "Choose source folder": "Velg kildemappe",
        "Choose target folder": "Velg målmappe",
        "Completed and blocked runs will appear here.": (
            "Fullførte og blokkerte kjøringer vises her."
        ),
        "High": "Høy",
        "Latest run": "Siste kjøring",
        "Local preview draft": "Lokalt forhåndsvisningsutkast",
        "Local preview draft is ready. Durable GUI backup creation is not enabled yet.": (
            "Lokalt forhåndsvisningsutkast er klart. Varig backupoppretting fra GUI er ikke aktivert ennå."
        ),
        "Local preview draft is ready. Connect an Engine Host before creating durable backup changes.": (
            "Lokalt forhåndsvisningsutkast er klart. Koble til en Engine Host før varige backupendringer opprettes."
        ),
        "Local preview settings will appear here.": "Lokale forhåndsvisningsinnstillinger vises her.",
        "Blocking issue": "Blokkerende problem",
        "Coverage warning": "Dekningsadvarsel",
        "No cataloged files.": "Ingen katalogf\u00f8rte filer.",
        "No cataloged files to show.": "Ingen katalogf\u00f8rte filer \u00e5 vise.",
        "No sealed plan to show.": "Ingen forseglet plan å vise.",
        "No sealed plan endpoints to show.": "Ingen forseglede planendepunkter å vise.",
        "No endpoint rows.": "Ingen endepunkter.",
        "No plan operations.": "Ingen planrader.",
        "No source snapshot to inspect.": "Ingen kildesnapshot å vise.",
        "No snapshot health rows.": "Ingen snapshothelserader.",
        "Operation": "Operasjon",
        "Endpoint": "Endepunkt",
        "Snapshot health": "Snapshothelse",
        "Snapshot health read model is not available.": (
            "Snapshothelsevisningen er ikke tilgjengelig."
        ),
        "Plan endpoints": "Planendepunkter",
        "Plan endpoint read model is not available.": "Planendepunktvisningen er ikke tilgjengelig.",
        "Plan preview": "Planforhåndsvisning",
        "Plan read model is not available.": "Planvisningen er ikke tilgjengelig.",
        "Rejected": "Avvist",
        "Review": "Til vurdering",
        "Review safe defaults.": "Kontroller trygge standardvalg.",
        "Saved backup jobs will appear here.": "Lagrede backupjobber vises her.",
        "Start a local Engine Host to refresh live status.": (
            "Start en lokal Engine Host for å oppdatere live-status."
        ),
    }
)


def shell_text(language_code: LanguageCode) -> ShellText:
    if language_code is LanguageCode.ENGLISH:
        return EN_TEXT
    return NB_TEXT


def normalize_language_code(language_code: str) -> LanguageCode | None:
    try:
        return LanguageCode(language_code)
    except ValueError:
        return None


def localize_display_value(language_code: LanguageCode, value: str) -> str:
    if language_code is LanguageCode.ENGLISH:
        return _to_english(value)
    return _to_norwegian(value)


def _to_english(value: str) -> str:
    if value in _NB_TO_EN:
        return _NB_TO_EN[value]
    translated = _translate_delimited(value, " · ", _to_english)
    if translated != value:
        return translated
    translated = _translate_delimited(value, " - ", _to_english)
    if translated != value:
        return translated
    translated = _translate_colon_value(value, _to_english)
    if translated != value:
        return translated
    translated = _translate_count_summary_to_english(value)
    if translated != value:
        return translated
    translated = _translate_prefix_to_english(value)
    if translated != value:
        return translated
    translated = _translate_plan_summary_to_english(value)
    if translated != value:
        return translated
    translated = _translate_endpoint_summary_to_english(value)
    if translated != value:
        return translated
    translated = _translate_endpoint_role_to_english(value)
    if translated != value:
        return translated
    translated = _translate_snapshot_summary_to_english(value)
    if translated != value:
        return translated
    translated = _translate_catalog_summary_to_english(value)
    if translated != value:
        return translated
    return value


def _to_norwegian(value: str) -> str:
    if value in _EN_TO_NB:
        return _EN_TO_NB[value]
    translated = _translate_protocol_to_norwegian(value)
    if translated != value:
        return translated
    translated = _translate_plan_summary_to_norwegian(value)
    if translated != value:
        return translated
    translated = _translate_endpoint_summary_to_norwegian(value)
    if translated != value:
        return translated
    translated = _translate_endpoint_role_to_norwegian(value)
    if translated != value:
        return translated
    translated = _translate_snapshot_summary_to_norwegian(value)
    if translated != value:
        return translated
    translated = _translate_catalog_summary_to_norwegian(value)
    if translated != value:
        return translated
    translated = _translate_delimited(value, " · ", _to_norwegian)
    if translated != value:
        return translated
    translated = _translate_delimited(value, " - ", _to_norwegian)
    if translated != value:
        return translated
    translated = _translate_colon_value(value, _to_norwegian)
    if translated != value:
        return translated
    translated = _translate_count_summary_to_norwegian(value)
    if translated != value:
        return translated
    return value


def _translate_delimited(
    value: str,
    delimiter: str,
    translator: Callable[[str], str],
) -> str:
    if delimiter not in value:
        return value
    parts = value.split(delimiter)
    translated = [translator(part) for part in parts]
    if translated == parts:
        return value
    return delimiter.join(translated)


def _translate_colon_value(value: str, translator: Callable[[str], str]) -> str:
    head, separator, tail = value.partition(": ")
    if not separator:
        return value
    translated_head = translator(head)
    translated_tail = translator(tail)
    if translated_head == head and translated_tail == tail:
        return value
    return f"{translated_head}: {translated_tail}"


def _translate_count_summary_to_english(value: str) -> str:
    translated = value
    replacements = (
        ("1 mål", "1 target"),
        (" mål", " targets"),
        ("1 uavhengig enhet", "1 independent device"),
        (" uavhengige enheter", " independent devices"),
    )
    for source, target in replacements:
        translated = translated.replace(source, target)
    return translated


def _translate_count_summary_to_norwegian(value: str) -> str:
    translated = value
    replacements = (
        ("1 target", "1 mål"),
        (" targets", " mål"),
        ("1 independent device", "1 uavhengig enhet"),
        (" independent devices", " uavhengige enheter"),
    )
    for source, target in replacements:
        translated = translated.replace(source, target)
    return translated


def _translate_prefix_to_english(value: str) -> str:
    prefixes = (
        ("Revisjon: ", "Revision: "),
        ("Siste kjøring: ", "Latest run: "),
        ("Aktivitet: ", "Activity: "),
        ("Oppmerksomhet: ", "Attention: "),
        ("Ferskhet per mål: ", "Freshness per target: "),
        ("Neste handling: ", "Next action: "),
        ("Protokoll ", "Protocol "),
    )
    for source, target in prefixes:
        if value.startswith(source):
            return f"{target}{_to_english(value.removeprefix(source))}"
    return value


def _translate_protocol_to_norwegian(value: str) -> str:
    if value.startswith("Protocol ") and " / schema " in value:
        protocol, schema = value.removeprefix("Protocol ").split(" / schema ", 1)
        return f"Protokoll {protocol} / skjema {schema}"
    return value


def _translate_plan_summary_to_english(value: str) -> str:
    match = re.fullmatch(
        r"(?P<count>\d+) operasjon(?:er)? fra (?P<plan>.+)\.(?P<more> Flere operasjoner finnes\.)?",
        value,
    )
    if match is None:
        return value
    operation_word = "operation" if match.group("count") == "1" else "operations"
    more = " More operations exist." if match.group("more") else ""
    return f"{match.group('count')} {operation_word} from {match.group('plan')}.{more}"


def _translate_plan_summary_to_norwegian(value: str) -> str:
    match = re.fullmatch(
        r"(?P<count>\d+) operation(?:s)? from (?P<plan>.+)\.(?P<more> More operations exist\.)?",
        value,
    )
    if match is None:
        return value
    operation_word = "operasjon" if match.group("count") == "1" else "operasjoner"
    more = " Flere operasjoner finnes." if match.group("more") else ""
    return f"{match.group('count')} {operation_word} fra {match.group('plan')}.{more}"


def _translate_endpoint_summary_to_english(value: str) -> str:
    match = re.fullmatch(
        r"(?P<count>\d+) endepunkt(?:er)? fra (?P<plan>.+)\.(?P<more> Flere endepunkter finnes\.)?",
        value,
    )
    if match is None:
        return value
    endpoint_word = "endpoint" if match.group("count") == "1" else "endpoints"
    more = " More endpoints exist." if match.group("more") else ""
    return f"{match.group('count')} {endpoint_word} from {match.group('plan')}.{more}"


def _translate_endpoint_summary_to_norwegian(value: str) -> str:
    match = re.fullmatch(
        r"(?P<count>\d+) endpoint(?:s)? from (?P<plan>.+)\.(?P<more> More endpoints exist\.)?",
        value,
    )
    if match is None:
        return value
    endpoint_word = "endepunkt" if match.group("count") == "1" else "endepunkter"
    more = " Flere endepunkter finnes." if match.group("more") else ""
    return f"{match.group('count')} {endpoint_word} fra {match.group('plan')}.{more}"


def _translate_endpoint_role_to_english(value: str) -> str:
    match = re.fullmatch(r"Målendepunkt (?P<ordinal>\d+)", value)
    if match is not None:
        return f"Target endpoint {match.group('ordinal')}"
    return value


def _translate_endpoint_role_to_norwegian(value: str) -> str:
    match = re.fullmatch(r"Target endpoint (?P<ordinal>\d+)", value)
    if match is not None:
        return f"Målendepunkt {match.group('ordinal')}"
    return value


def _translate_snapshot_summary_to_english(value: str) -> str:
    if value.startswith("Ingen blokkerende snapshotproblemer i ") and value.endswith("."):
        snapshot = value.removeprefix("Ingen blokkerende snapshotproblemer i ").removesuffix(".")
        return f"No blocking snapshot issues in {snapshot}."
    match = re.fullmatch(
        r"(?P<count>\d+) blokkerende problem(?:er)? i (?P<snapshot>.+)\.(?P<more> Flere snapshoterader finnes\.)?",
        value,
    )
    if match is not None:
        issue_word = "issue" if match.group("count") == "1" else "issues"
        more = " More snapshot rows exist." if match.group("more") else ""
        return f"{match.group('count')} blocking {issue_word} in {match.group('snapshot')}.{more}"
    match = re.fullmatch(
        r"(?P<count>\d+) dekningsadvarsel(?:er)? i (?P<snapshot>.+)\.(?P<more> Flere snapshoterader finnes\.)?",
        value,
    )
    if match is None:
        return value
    warning_word = "warning" if match.group("count") == "1" else "warnings"
    more = " More snapshot rows exist." if match.group("more") else ""
    return f"{match.group('count')} coverage {warning_word} in {match.group('snapshot')}.{more}"


def _translate_snapshot_summary_to_norwegian(value: str) -> str:
    if value.startswith("No blocking snapshot issues in ") and value.endswith("."):
        snapshot = value.removeprefix("No blocking snapshot issues in ").removesuffix(".")
        return f"Ingen blokkerende snapshotproblemer i {snapshot}."
    match = re.fullmatch(
        r"(?P<count>\d+) blocking issue(?:s)? in (?P<snapshot>.+)\.(?P<more> More snapshot rows exist\.)?",
        value,
    )
    if match is not None:
        issue_word = "problem" if match.group("count") == "1" else "problemer"
        more = " Flere snapshoterader finnes." if match.group("more") else ""
        return f"{match.group('count')} blokkerende {issue_word} i {match.group('snapshot')}.{more}"
    match = re.fullmatch(
        r"(?P<count>\d+) coverage warning(?:s)? in (?P<snapshot>.+)\.(?P<more> More snapshot rows exist\.)?",
        value,
    )
    if match is None:
        return value
    warning_word = "dekningsadvarsel" if match.group("count") == "1" else "dekningsadvarsler"
    more = " Flere snapshoterader finnes." if match.group("more") else ""
    return f"{match.group('count')} {warning_word} i {match.group('snapshot')}.{more}"


def _translate_catalog_summary_to_english(value: str) -> str:
    match = re.fullmatch(
        r"(?P<count>\d+) katalogført(?:e)? fil(?:er)?\.(?P<more> Flere katalogførte filer finnes\.)?",
        value,
    )
    if match is None:
        return value
    file_word = "cataloged file" if match.group("count") == "1" else "cataloged files"
    more = " More cataloged files exist." if match.group("more") else ""
    return f"{match.group('count')} {file_word}.{more}"


def _translate_catalog_summary_to_norwegian(value: str) -> str:
    match = re.fullmatch(
        r"(?P<count>\d+) cataloged file(?:s)?\.(?P<more> More cataloged files exist\.)?",
        value,
    )
    if match is None:
        return value
    file_word = "katalogf\u00f8rt fil" if match.group("count") == "1" else "katalogf\u00f8rte filer"
    more = " Flere katalogf\u00f8rte filer finnes." if match.group("more") else ""
    return f"{match.group('count')} {file_word}.{more}"
