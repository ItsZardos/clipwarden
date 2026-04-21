; Inno Setup 6 - per-user install, centralised autostart via ClipWarden.exe flags.
; Build PyInstaller output first: pyinstaller build/ClipWarden.spec --clean
; Then: iscc build/installer.iss

#define MyAppName "ClipWarden"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Ethan Tharp"
#define MyAppURL "https://ethantharp.dev"
#define MyAppExeName "ClipWarden.exe"

[Setup]
AppId={{E2B4F6A8-0C1D-4E3F-9A5B-7C8D9EAF0B1C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
DisableDirPage=yes
DisableProgramGroupPage=no
OutputDir=..\dist
OutputBaseFilename=ClipWarden-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "autostart"; Description: "Start ClipWarden automatically when Windows starts"; GroupDescription: "Optional:"; Flags: unchecked

[Files]
; PyInstaller produces ClipWarden-Portable.exe (the portable release
; artifact). The installer copies it to {app}\ClipWarden.exe so the
; installed binary keeps the canonical process name referenced by the
; autostart Run key, the PE OriginalFilename resource, and the Start
; Menu shortcut.
Source: "..\dist\ClipWarden-Portable.exe"; DestDir: "{app}"; DestName: "{#MyAppExeName}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"

[Run]
; Post-install: optional launch (tray is default with no args).
Filename: "{app}\{#MyAppExeName}"; Description: "Launch ClipWarden"; Flags: nowait postinstall skipifsilent unchecked
; Wire Run key through the frozen exe (single source of truth with uninstall).
Filename: "{app}\{#MyAppExeName}"; Parameters: "--install-autostart"; StatusMsg: "Configuring autostart..."; Tasks: autostart; Flags: runhidden waituntilterminated

[UninstallRun]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--uninstall-autostart"; RunOnceId: "ClipWardenRemoveAutostart"; Flags: runhidden skipifdoesntexist

[InstallDelete]
; Nothing - user data lives under %APPDATA%\ClipWarden\ and is preserved.
