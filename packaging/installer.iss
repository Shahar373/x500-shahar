; Inno Setup script for X500 Flight Station.
; Build (after PyInstaller):  ISCC.exe /DAppVersion=0.1.0 packaging\installer.iss
; Produces packaging\Output\X500FlightStation-Setup-<version>.exe
;
; User settings, profiles, known_hosts and reports live in %APPDATA% /
; Documents and are NOT removed on uninstall. Nothing is installed outside the
; chosen folder except Start-menu / optional desktop shortcuts.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "X500 Flight Station"
#define AppExe "X500FlightStation.exe"

[Setup]
AppId={{6B0E2C1A-5D7E-4C61-9C2B-0F8A7D9E3A51}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=X500 Flight Station
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=Output
OutputBaseFilename=X500FlightStation-Setup-{#AppVersion}
SetupIconFile=..\src\x500fs\assets\app.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "hebrew"; MessagesFile: "compiler:Languages\Hebrew.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\X500FlightStation\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
