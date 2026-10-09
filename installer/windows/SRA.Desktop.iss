; SRA complete Desktop installer: .NET client and frozen Python backend.
; Compile using ISCC.exe with /DAppVersion=x.y.z /DPublishDir=... /DOutputDir=...
#ifndef AppVersion
  #define AppVersion "0.1.1"
#endif
#ifndef PublishDir
  #define PublishDir "..\..\artifacts\win-x64"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\artifacts"
#endif

[Setup]
AppId=org.daseinzc.socialscienceresearchide.desktop
AppName=Social Science Research IDE
AppVersion={#AppVersion}
AppPublisher=daseinzc-509
AppPublisherURL=https://github.com/daseinzc-509/social-science-research-ide
AppSupportURL=https://github.com/daseinzc-509/social-science-research-ide/issues
AppUpdatesURL=https://github.com/daseinzc-509/social-science-research-ide/releases
DefaultDirName={localappdata}\Programs\SRA Desktop
DefaultGroupName=SRA Desktop
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename=SRA-Desktop-Setup-win-x64-{#AppVersion}
SetupIconFile=..\..\desktop\SRA.Desktop\Assets\sra.ico
UninstallDisplayIcon={app}\SRA.Desktop.exe
Compression=lzma
SolidCompression=yes
WizardStyle=modern
; Do not use the old FRONTEND_ONLY notice with a bundled backend.
; Information and license are linked in the installer About dialog.
CloseApplications=yes
RestartApplications=no
DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: unchecked

[Files]
Source: "{#PublishDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\SRA Desktop"; Filename: "{app}\SRA.Desktop.exe"
Name: "{autodesktop}\SRA Desktop"; Filename: "{app}\SRA.Desktop.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\SRA.Desktop.exe"; Description: "启动 SRA Desktop"; Flags: nowait postinstall skipifsilent
