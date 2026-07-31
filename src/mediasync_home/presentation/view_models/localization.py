from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum


class LanguageCode(str, Enum):
    NORWEGIAN = "nb"
    ENGLISH = "en"


@dataclass(frozen=True)
class SettingsText:
    appearance_title: str
    appearance_detail: str
    theme: str
    theme_system: str
    theme_light: str
    theme_dark: str
    density: str
    density_comfortable: str
    density_compact: str
    reduced_motion: str
    language: str
    defaults_title: str
    defaults_detail: str
    retention: str
    retention_value: str
    performance: str
    performance_value: str
    quarantine: str
    quarantine_value: str
    notifications: str
    notifications_value: str
    storage_title: str
    storage_detail: str
    storage_status: str
    state_usage: str
    free_space: str
    data_location: str
    capacity_ready: str
    capacity_warning: str
    capacity_blocked: str
    capacity_unavailable: str
    about_title: str
    about_detail: str
    version: str
    privacy_report: str
    open_data_folder: str
    copy_diagnostics: str
    diagnostics_copied: str
    preference_save_failed: str
    open_data_folder_failed: str


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
    plan: str
    create_backup_tooltip: str
    add_target_tooltip: str
    remove_target_tooltip: str
    back_tooltip: str
    saved_jobs: str
    jobs_empty: str
    jobs_unavailable: str
    previous_page_tooltip: str
    next_page_tooltip: str
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

    @property
    def start_backup(self) -> str:
        return "Start backup"

    @property
    def start_backup_tooltip(self) -> str:
        if self.language_tooltip_prefix == "Language":
            return "Start the sealed backup plan"
        return "Start den forseglede backupplanen"

    @property
    def run_backup(self) -> str:
        return "Run backup" if self.language_tooltip_prefix == "Language" else "Kjør backup"

    @property
    def run_backup_tooltip(self) -> str:
        if self.language_tooltip_prefix == "Language":
            return "Check for changes, then run safe changes"
        return "Kontroller endringer, og kjør deretter trygge endringer"

    @property
    def checking_backup(self) -> str:
        if self.language_tooltip_prefix == "Language":
            return "Checking changes..."
        return "Kontrollerer endringer..."

    @property
    def checking_backup_tooltip(self) -> str:
        if self.language_tooltip_prefix == "Language":
            return "The Engine Host is checking this backup"
        return "Engine Host kontrollerer denne backupen"

    @property
    def backup_check_complete(self) -> str:
        if self.language_tooltip_prefix == "Language":
            return "Changes are ready"
        return "Endringene er klare"

    @property
    def no_backup_changes(self) -> str:
        if self.language_tooltip_prefix == "Language":
            return "No backup changes found"
        return "Ingen backupendringer funnet"

    @property
    def backup_check_failed(self) -> str:
        if self.language_tooltip_prefix == "Language":
            return "Backup check failed"
        return "Backupkontrollen mislyktes"

    @property
    def backup_queued(self) -> str:
        return "Backup queued" if self.language_tooltip_prefix == "Language" else "Backup er lagt i kø"

    @property
    def pause_backup(self) -> str:
        return "Pause" if self.language_tooltip_prefix == "Language" else "Pause"

    @property
    def pause_backup_tooltip(self) -> str:
        if self.language_tooltip_prefix == "Language":
            return "Pause after the current safe file operation"
        return "Pause etter gjeldende sikre filoperasjon"

    @property
    def resume_backup(self) -> str:
        return "Resume" if self.language_tooltip_prefix == "Language" else "Fortsett"

    @property
    def resume_backup_tooltip(self) -> str:
        if self.language_tooltip_prefix == "Language":
            return "Resume with fresh target access checks"
        return "Fortsett med ny kontroll av måltilgang"

    @property
    def stop_after_active_file(self) -> str:
        if self.language_tooltip_prefix == "Language":
            return "Stop after current file"
        return "Stopp etter aktiv fil"

    @property
    def stop_after_active_file_tooltip(self) -> str:
        if self.language_tooltip_prefix == "Language":
            return "Finish the current file safely, then stop the backup"
        return "Fullfør aktiv fil trygt, og stopp deretter backupen"

    @property
    def stopping_after_active_file(self) -> str:
        if self.language_tooltip_prefix == "Language":
            return "Stopping after current file"
        return "Stopper etter aktiv fil"

    @property
    def run_progress(self) -> str:
        return "Backup progress" if self.language_tooltip_prefix == "Language" else "Backupfremdrift"

    @property
    def changes(self) -> str:
        return "Changes" if self.language_tooltip_prefix == "Language" else "Endringer"

    @property
    def all_targets(self) -> str:
        return "All targets" if self.language_tooltip_prefix == "Language" else "Alle mål"

    @property
    def all_changes(self) -> str:
        return "All changes" if self.language_tooltip_prefix == "Language" else "Alle endringer"

    @property
    def attention_changes(self) -> str:
        if self.language_tooltip_prefix == "Language":
            return "Needs attention"
        return "Krever oppmerksomhet"

    @property
    def safe_changes(self) -> str:
        return "Safe changes" if self.language_tooltip_prefix == "Language" else "Trygge endringer"

    @property
    def no_plan_changes(self) -> str:
        if self.language_tooltip_prefix == "Language":
            return "No checked changes are ready."
        return "Ingen kontrollerte endringer er klare."

    @property
    def no_filtered_changes(self) -> str:
        if self.language_tooltip_prefix == "Language":
            return "No changes match these filters."
        return "Ingen endringer samsvarer med filtrene."

    @property
    def decision(self) -> str:
        return "Decision" if self.language_tooltip_prefix == "Language" else "Beslutning"

    @property
    def change_type(self) -> str:
        return "Change" if self.language_tooltip_prefix == "Language" else "Endring"

    @property
    def path(self) -> str:
        return "Path" if self.language_tooltip_prefix == "Language" else "Sti"

    @property
    def reason_code(self) -> str:
        return "Reason code" if self.language_tooltip_prefix == "Language" else "Årsakskode"

    @property
    def precondition(self) -> str:
        return "Precondition" if self.language_tooltip_prefix == "Language" else "Forhåndsvilkår"

    @property
    def planned_size(self) -> str:
        return "Planned size" if self.language_tooltip_prefix == "Language" else "Planlagt størrelse"

    @property
    def run_result(self) -> str:
        return "Backup result" if self.language_tooltip_prefix == "Language" else "Backupresultat"

    @property
    def retry_target(self) -> str:
        return "Retry target" if self.language_tooltip_prefix == "Language" else "Prøv målet på nytt"

    @property
    def retry_target_tooltip(self) -> str:
        if self.language_tooltip_prefix == "Language":
            return "Check again, then retry only the selected failed target"
        return "Kontroller på nytt, og prøv bare det valgte mislykkede målet"

    @property
    def failed_target(self) -> str:
        return "Failed target" if self.language_tooltip_prefix == "Language" else "Mislykket mål"

    @property
    def last_successful_backup(self) -> str:
        if self.language_tooltip_prefix == "Language":
            return "Last successful"
        return "Siste vellykkede"

    @property
    def no_successful_backup(self) -> str:
        if self.language_tooltip_prefix == "Language":
            return "No successful backup"
        return "Ingen vellykket backup"

    @property
    def operation_count(self) -> str:
        return "operations" if self.language_tooltip_prefix == "Language" else "operasjoner"

    @property
    def current_file(self) -> str:
        return "Current file" if self.language_tooltip_prefix == "Language" else "Aktiv fil"

    @property
    def calculating_eta(self) -> str:
        return "Calculating remaining time" if self.language_tooltip_prefix == "Language" else "Beregner gjenstående tid"

    @property
    def remaining(self) -> str:
        return "remaining" if self.language_tooltip_prefix == "Language" else "igjen"

    @property
    def pause_requested(self) -> str:
        return "Pausing safely..." if self.language_tooltip_prefix == "Language" else "Pauser trygt..."

    @property
    def history_activities(self) -> str:
        return "Activity history" if self.language_tooltip_prefix == "Language" else "Aktivitetshistorikk"

    @property
    def all_activities(self) -> str:
        return "All activities" if self.language_tooltip_prefix == "Language" else "Alle aktiviteter"

    @property
    def controls(self) -> str:
        return "Controls" if self.language_tooltip_prefix == "Language" else "Kontroller"

    @property
    def backup_runs(self) -> str:
        return "Backup runs" if self.language_tooltip_prefix == "Language" else "Backupkjøringer"

    @property
    def all_jobs(self) -> str:
        return "All jobs" if self.language_tooltip_prefix == "Language" else "Alle jobber"

    @property
    def history_empty(self) -> str:
        return (
            "No activities match the selected filters."
            if self.language_tooltip_prefix == "Language"
            else "Ingen aktiviteter samsvarer med valgte filtre."
        )

    @property
    def history_unavailable(self) -> str:
        return (
            "History is not available."
            if self.language_tooltip_prefix == "Language"
            else "Historikken er ikke tilgjengelig."
        )

    @property
    def activity_type(self) -> str:
        return "Type" if self.language_tooltip_prefix == "Language" else "Type"

    @property
    def status(self) -> str:
        return "Status" if self.language_tooltip_prefix == "Language" else "Status"

    @property
    def started(self) -> str:
        return "Started" if self.language_tooltip_prefix == "Language" else "Startet"

    @property
    def finished(self) -> str:
        return "Finished" if self.language_tooltip_prefix == "Language" else "Fullført"

    @property
    def duration(self) -> str:
        return "Duration" if self.language_tooltip_prefix == "Language" else "Varighet"

    @property
    def operations(self) -> str:
        return "Operations" if self.language_tooltip_prefix == "Language" else "Operasjoner"

    @property
    def transferred(self) -> str:
        return "Transferred" if self.language_tooltip_prefix == "Language" else "Overført"

    @property
    def average_speed(self) -> str:
        return (
            "Average speed"
            if self.language_tooltip_prefix == "Language"
            else "Gjennomsnittshastighet"
        )

    @property
    def warnings_errors(self) -> str:
        return (
            "Warnings / errors"
            if self.language_tooltip_prefix == "Language"
            else "Varsler / feil"
        )

    @property
    def trigger(self) -> str:
        return "Trigger" if self.language_tooltip_prefix == "Language" else "Utløser"

    @property
    def identifiers(self) -> str:
        return "Identifiers" if self.language_tooltip_prefix == "Language" else "Identifikatorer"

    @property
    def activity_targets(self) -> str:
        return "Targets included" if self.language_tooltip_prefix == "Language" else "Mål som inngikk"

    @property
    def activity_control(self) -> str:
        return "Control" if self.language_tooltip_prefix == "Language" else "Kontroll"

    @property
    def activity_backup(self) -> str:
        return "Backup" if self.language_tooltip_prefix == "Language" else "Backup"

    @property
    def file_results(self) -> str:
        return "File results" if self.language_tooltip_prefix == "Language" else "Filresultater"

    @property
    def file_results_unavailable(self) -> str:
        if self.language_tooltip_prefix == "Language":
            return "File results are not available."
        return "Filresultater er ikke tilgjengelige."

    @property
    def file_results_empty(self) -> str:
        if self.language_tooltip_prefix == "Language":
            return "This backup has no file results to show."
        return "Denne backupen har ingen filresultater å vise."

    @property
    def file_audit_not_found(self) -> str:
        if self.language_tooltip_prefix == "Language":
            return "No persisted audit evidence was found for this operation."
        return "Ingen lagret revisjonsevidens ble funnet for denne operasjonen."

    @property
    def file_result(self) -> str:
        return "Result" if self.language_tooltip_prefix == "Language" else "Resultat"

    @property
    def verification(self) -> str:
        return "Verification" if self.language_tooltip_prefix == "Language" else "Verifisering"

    @property
    def durability(self) -> str:
        return "Durability" if self.language_tooltip_prefix == "Language" else "Varig lagring"

    @property
    def attempts(self) -> str:
        return "Attempts" if self.language_tooltip_prefix == "Language" else "Forsøk"

    @property
    def last_error(self) -> str:
        return "Last error" if self.language_tooltip_prefix == "Language" else "Siste feil"

    @property
    def file_attempts(self) -> str:
        return "Attempt history" if self.language_tooltip_prefix == "Language" else "Forsøkshistorikk"

    @property
    def no_terminal_outcome(self) -> str:
        if self.language_tooltip_prefix == "Language":
            return "No terminal outcome yet"
        return "Ingen avsluttende resultat ennå"

    @property
    def no_error(self) -> str:
        return "None" if self.language_tooltip_prefix == "Language" else "Ingen"


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
    plan="Plan",
    create_backup_tooltip=(
        "Opprett jobb og registrer valgte mål som skrivbare MediaSync-endepunkter"
    ),
    add_target_tooltip="Legg til målmappe",
    remove_target_tooltip="Fjern målmappe",
    back_tooltip="Tilbake",
    saved_jobs="Lagrede backupjobber",
    jobs_empty="Ingen lagrede backupjobber",
    jobs_unavailable="Jobblisten er ikke tilgjengelig.",
    previous_page_tooltip="Forrige side",
    next_page_tooltip="Neste side",
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
    plan="Plan",
    create_backup_tooltip=(
        "Create the job and register selected targets as writable MediaSync endpoints"
    ),
    add_target_tooltip="Add target folder",
    remove_target_tooltip="Remove target folder",
    back_tooltip="Back",
    saved_jobs="Saved backup jobs",
    jobs_empty="No saved backup jobs",
    jobs_unavailable="The job list is not available.",
    previous_page_tooltip="Previous page",
    next_page_tooltip="Next page",
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


NB_SETTINGS_TEXT = SettingsText(
    appearance_title="Utseende og språk",
    appearance_detail="Endringer brukes med en gang og lagres for denne Windows-brukeren.",
    theme="Tema",
    theme_system="System",
    theme_light="Lys",
    theme_dark="Mørk",
    density="Tetthet",
    density_comfortable="Komfortabel",
    density_compact="Kompakt",
    reduced_motion="Reduser bevegelse",
    language="Språk",
    defaults_title="Trygge standarder",
    defaults_detail=(
        "Disse verdiene håndheves for nye jobber. Flere valg kommer når motoren kan "
        "bruke dem sikkert."
    ),
    retention="Versjonsbevaring",
    retention_value="Tidligere versjoner beholdes i 30 dager",
    performance="Ytelsesprofil",
    performance_value="Auto - anbefalt",
    quarantine="Karanteneperiode",
    quarantine_value="Ikke konfigurerbar i denne versjonen",
    notifications="Varsler",
    notifications_value="Ikke aktivert i lokal forhåndsvisning",
    storage_title="Lagring og vedlikehold",
    storage_detail="Statusen kommer direkte fra Engine Host og oppdateres med motorstatus.",
    storage_status="Status",
    state_usage="Database, logger og lokal tilstand",
    free_space="Ledig lokal plass",
    data_location="Datamappe",
    capacity_ready="Klar",
    capacity_warning="Krever oppmerksomhet",
    capacity_blocked="Blokkert - frigjør lokal plass",
    capacity_unavailable="Kapasitetsmåling er ikke tilgjengelig",
    about_title="Om og diagnostikk",
    about_detail="Den kopierbare rapporten utelater brukernavn og private filstier.",
    version="Versjon",
    privacy_report="Personvernbevisst rapport",
    open_data_folder="Åpne datamappe",
    copy_diagnostics="Kopier diagnostikk",
    diagnostics_copied="Diagnostikk kopiert",
    preference_save_failed="Kunne ikke lagre innstillingen",
    open_data_folder_failed="Kunne ikke åpne datamappen",
)


EN_SETTINGS_TEXT = SettingsText(
    appearance_title="Appearance and language",
    appearance_detail="Changes apply immediately and are saved for this Windows user.",
    theme="Theme",
    theme_system="System",
    theme_light="Light",
    theme_dark="Dark",
    density="Density",
    density_comfortable="Comfortable",
    density_compact="Compact",
    reduced_motion="Reduce motion",
    language="Language",
    defaults_title="Safe defaults",
    defaults_detail=(
        "These values are enforced for new jobs. More choices will appear when the "
        "engine can apply them safely."
    ),
    retention="Version retention",
    retention_value="Previous versions are kept for 30 days",
    performance="Performance profile",
    performance_value="Auto - recommended",
    quarantine="Quarantine period",
    quarantine_value="Not configurable in this version",
    notifications="Notifications",
    notifications_value="Not enabled in the local preview",
    storage_title="Storage and maintenance",
    storage_detail="Status comes directly from Engine Host and refreshes with engine status.",
    storage_status="Status",
    state_usage="Database, logs, and local state",
    free_space="Free local space",
    data_location="Data folder",
    capacity_ready="Ready",
    capacity_warning="Needs attention",
    capacity_blocked="Blocked - free local space",
    capacity_unavailable="Capacity measurement is unavailable",
    about_title="About and diagnostics",
    about_detail="The copied report omits usernames and private file paths.",
    version="Version",
    privacy_report="Privacy-aware report",
    open_data_folder="Open data folder",
    copy_diagnostics="Copy diagnostics",
    diagnostics_copied="Diagnostics copied",
    preference_save_failed="Could not save the setting",
    open_data_folder_failed="Could not open the data folder",
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
    "Fortsett backupen når du er klar.": "Resume the backup when you are ready.",
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
    "Ingen endringer": "No changes",
    "Ingen plan ennå.": "No plan yet.",
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
    "Kontroller målet og prøv igjen.": "Check the target and retry.",
    "Kontroller resultatet før neste backup.": "Check the result before the next backup.",
    "Kontrollerer": "Checking",
    "Klar til kontroll": "Ready for review",
    "Kontrollerer mål før lease og revalidering.": "Checking targets before lease and revalidation.",
    "Kontrollerer måltilgang.": "Checking target access.",
    "Kontrakt": "Contract",
    "Kopierer": "Copying",
    "Kopiering pågår.": "Copying is in progress.",
    "Kjør backupen på nytt når målet er klart.": (
        "Run the backup again when the target is ready."
    ),
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
    "Opprett og registrer": "Create and register",
    "Prøv målregistrering igjen": "Retry target registration",
    "Opprett mappe": "Create folder",
    "Pauset": "Paused",
    "Plan venter": "Plan waiting",
    "Planlegging feilet": "Planning failed",
    "Planstatus": "Plan status",
    "Planforhåndsvisning": "Plan preview",
    "Planendepunkter": "Plan endpoints",
    "Planendepunktvisningen er ikke tilgjengelig.": "Plan endpoint read model is not available.",
    "Planvisningen er ikke tilgjengelig.": "Plan read model is not available.",
    "Protokoll utilgjengelig": "Protocol unavailable",
    "Protokoll venter": "Protocol pending",
    "Revisjon": "Revision",
    "Registrering venter": "Registration pending",
    "Kun forhåndsvisning": "Preview only",
    "Se gjennom blokkeringen før ny kjøring.": "Review the block before a new run.",
    "Se gjennom målfeilen.": "Review the target error.",
    "Se gjennom målresultatet.": "Review the target result.",
    "Sist sikkerhetskopiert": "Last backed up",
    "Skrivebeskyttet lokal forhåndsvisning": "Read-only local preview",
    "Skrivebeskyttet målendepunkt": "Read-only target endpoint",
    "Skrivbar og registrert": "Writable and registered",
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
        "Continue with selected target folders.": "Fortsett med valgte målmapper.",
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
        "Blocking issue": "Blokkerende problem",
        "Backup job was created and saved.": "Backupjobben ble opprettet og lagret.",
        "Backup job was saved. Endpoint safety setup is pending.": (
            "Backupjobben ble lagret. Sikkerhetsoppsett for endepunkter venter."
        ),
        "Backup job and writable target registration were saved.": (
            "Backupjobben og registreringen av skrivbart mål ble lagret."
        ),
        "Backup job was saved, but target registration needs attention.": (
            "Backupjobben ble lagret, men målregistreringen trenger oppfølging."
        ),
        "Connected Engine Host does not support backup creation.": (
            "Tilkoblet Engine Host stÃ¸tter ikke oppretting av backup."
        ),
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


def settings_text(language_code: LanguageCode) -> SettingsText:
    if language_code is LanguageCode.ENGLISH:
        return EN_SETTINGS_TEXT
    return NB_SETTINGS_TEXT


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
