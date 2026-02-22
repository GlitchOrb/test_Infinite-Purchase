#define MyAppName "Infinite Purchase"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Infinite Purchase"
#define MyAppExeName "InfinitePurchaseLauncher.exe"
#define MyMainExeName "InfinitePurchaseApp.exe"

[Setup]
AppId={{D63D9DE0-84C6-4AC5-8E98-8C79B2E91C53}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\InfinitePurchase
DefaultGroupName=Infinite Purchase
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=InfinitePurchaseInstaller
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "dist\InfinitePurchaseLauncher.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\InfinitePurchaseApp.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Infinite Purchase"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\Infinite Purchase"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Infinite Purchase"; Flags: nowait postinstall skipifsilent
