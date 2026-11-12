; Inno Setup script — Xray Wi-Fi Share
#define MyAppName "Xray Wi-Fi Share"
#define MyAppVersion "1.1.3"
#define MyAppPublisher "Xray Wi-Fi Share"
#define MyAppExeName "XrayWifiShare.exe"
#define MyAppURL "https://github.com/RasmusVraa/xray-wifi-share"

[Setup]
AppId={{8F3C2A91-6D4E-4B7A-9C11-A1B2C3D4E5F6}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\XrayWifiShare
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=XrayWifiShare-Setup-{#MyAppVersion}
SetupIconFile=
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#MyAppName}
VersionInfoVersion={#MyAppVersion}
LicenseFile=

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительно:"; Flags: unchecked

[Files]
Source: "..\dist\XrayWifiShare\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Удалить {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; postinstall по умолчанию идёт unelevated → CreateProcess 740 на requireAdministrator.
; runascurrentuser: запуск с правами установщика (admin).
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить {#MyAppName}"; Flags: nowait postinstall skipifsilent runascurrentuser
