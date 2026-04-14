#ifndef MyAppVersion
  #define MyAppVersion "0.3.18"
#endif
#ifndef MySourceRoot
  #error MySourceRoot must be passed from the build script
#endif
#ifndef MyDistDir
  #error MyDistDir must be passed from the build script
#endif
#ifndef MyOutputDir
  #error MyOutputDir must be passed from the build script
#endif
#ifndef MyIconFile
  #error MyIconFile must be passed from the build script
#endif
#ifndef MyRecoveryExeName
  #error MyRecoveryExeName must be passed from the build script
#endif

#define MyAppName "Multi Screen Pass"
#define MyAppExeName "MultiScreenPass.exe"
#define MyWatchdogExeName "MultiScreenPassRecoveryWatchdog.exe"
#define MyAppId "{{6D07092D-7559-4A3A-840D-2F3A6A7FE1B7}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppName}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
OutputDir={#MyOutputDir}
OutputBaseFilename=MultiScreenPass-Setup-{#MyAppVersion}
SetupIconFile={#MyIconFile}
DisableDirPage=no
DisableProgramGroupPage=yes

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "Create desktop icon"; GroupDescription: "Additional tasks:"; Flags: unchecked

[Files]
Source: "{#MyDistDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MyDistDir}\{#MyRecoveryExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MyDistDir}\{#MyWatchdogExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
