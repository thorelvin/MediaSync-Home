#define ProductName "MediaSync Home"
#define ProductExeName "MediaSyncHome0B.exe"

#ifndef SourceDir
  #error SourceDir must point to the Nuitka standalone directory.
#endif
#ifndef MetadataDir
  #error MetadataDir must point to generated installer metadata.
#endif
#ifndef AppVersion
  #error AppVersion must be defined.
#endif
#ifndef AppVersionNumeric
  #error AppVersionNumeric must be defined.
#endif
#ifndef OutputDir
  #error OutputDir must be defined.
#endif

[Setup]
AppId={{D582CE2D-8CA0-4E38-A626-714B1C0EB7AB}
AppName={#ProductName}
AppVersion={#AppVersion}
AppVerName={#ProductName} {#AppVersion} (local unsigned alpha)
AppPublisher=MediaSync Home
VersionInfoVersion={#AppVersionNumeric}
VersionInfoProductName={#ProductName}
VersionInfoDescription=MediaSync Home local unsigned alpha installer
VersionInfoCompany=MediaSync Home
DefaultDirName={localappdata}\Programs\MediaSync Home
DefaultGroupName=MediaSync Home
DisableProgramGroupPage=yes
AllowNoIcons=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=MediaSyncHome-Setup-{#AppVersion}-unsigned
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
RestartApplications=no
ChangesAssociations=no
ChangesEnvironment=no
UninstallDisplayIcon={app}\{#ProductExeName}
UninstallDisplayName={#ProductName} {#AppVersion}
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "norwegian"; MessagesFile: "compiler:Languages\Norwegian.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#MetadataDir}\dependency-manifest.json"; DestDir: "{app}\licenses"; Flags: ignoreversion
Source: "{#MetadataDir}\THIRD_PARTY_NOTICES.txt"; DestDir: "{app}\licenses"; Flags: ignoreversion
Source: "{#MetadataDir}\LOCAL_ALPHA_README.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MetadataDir}\LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MetadataDir}\licenses\*"; DestDir: "{app}\licenses\packages"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

[Icons]
Name: "{autoprograms}\MediaSync Home"; Filename: "{app}\{#ProductExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\MediaSync Home"; Filename: "{app}\{#ProductExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#ProductExeName}"; Description: "Launch MediaSync Home"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[Code]
function IsMediaSyncRunning(): Boolean;
var
  Locator: Variant;
  Services: Variant;
  Processes: Variant;
begin
  Locator := CreateOleObject('WbemScripting.SWbemLocator');
  Services := Locator.ConnectServer('', 'root\CIMV2');
  Processes := Services.ExecQuery(
    'SELECT ProcessId FROM Win32_Process WHERE Name = ''{#ProductExeName}''');
  Result := Processes.Count > 0;
end;

function RunningMessage(): String;
begin
  if ActiveLanguage = 'norwegian' then
    Result := 'MediaSync Home kjører fortsatt. Lukk programmet og vent til backupaktiviteten er avsluttet før du fortsetter.'
  else
    Result := 'MediaSync Home is still running. Close the app and wait for backup activity to finish before continuing.';
end;

function CleanupMessage(): String;
begin
  if ActiveLanguage = 'norwegian' then
    Result := 'MediaSync Home kunne ikke fjerne alle verifiserte planlagte oppgaver. Avinstallasjonen er stoppet for å unngå å etterlate oppgaver som peker til et slettet program.'
  else
    Result := 'MediaSync Home could not remove every verified scheduled task. Uninstall was stopped to avoid leaving tasks that point to a deleted application.';
end;

function TryStopMediaSync(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  if FileExists(ExpandConstant('{app}\{#ProductExeName}')) then
    Result := Exec(
      ExpandConstant('{app}\{#ProductExeName}'),
      '--shutdown-local-preview-host --timeout-seconds 15',
      ExpandConstant('{app}'),
      SW_HIDE,
      ewWaitUntilTerminated,
      ResultCode) and (ResultCode = 0);
end;

function StopRunningMediaSync(): Boolean;
var
  Attempt: Integer;
begin
  if not IsMediaSyncRunning() then
  begin
    Result := True;
    Exit;
  end;
  TryStopMediaSync();
  for Attempt := 1 to 150 do
  begin
    if not IsMediaSyncRunning() then
    begin
      Result := True;
      Exit;
    end;
    Sleep(100);
  end;
  Result := False;
end;

function CleanupOwnedScheduledTasks(): Boolean;
var
  ResultCode: Integer;
begin
  Result := False;
  if not FileExists(ExpandConstant('{app}\{#ProductExeName}')) then
    Exit;
  Result := Exec(
    ExpandConstant('{app}\{#ProductExeName}'),
    '--cleanup-owned-scheduled-tasks',
    ExpandConstant('{app}'),
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode) and (ResultCode = 0);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  try
    if not StopRunningMediaSync() then
      Result := RunningMessage();
  except
    Result := 'MediaSync Home could not verify that the application is stopped. Close it and try again.';
  end;
end;

function InitializeUninstall(): Boolean;
begin
  Result := False;
  try
    Result := StopRunningMediaSync();
  except
    Result := False;
  end;
  if not Result then
  begin
    MsgBox(RunningMessage(), mbError, MB_OK);
    Exit;
  end;
  try
    Result := CleanupOwnedScheduledTasks();
  except
    Result := False;
  end;
  if not Result then
    MsgBox(CleanupMessage(), mbError, MB_OK);
end;
