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
DisableDirPage=no
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

; The install location can be reviewed/changed in the normal wizard. SRA
; research files are NEVER installed into {app}; they are per-user.
; Interactive uninstall offers explicit opt-in deletion. Silent uninstall,
; including GitHub CI and enterprise tooling, ALWAYS preserves user data.
[Code]
var
  RemovePersonalFiles: Boolean;

// Refuse recursive deletion when the private root or any child is a junction /
// symlink. This prevents an opt-in cleanup from crossing into another folder.
function GetFileAttributesW(FileName: String): LongWord;
  external 'GetFileAttributesW@kernel32.dll stdcall';

function HasLinkedChildren(const Folder: String; Depth: Integer): Boolean;
var
  Item: TFindRec;
  FullName: String;
begin
  Result := False;
  if Depth > 64 then
  begin
    Result := True;
    Exit;
  end;
  if FindFirst(AddBackslash(Folder) + '*', Item) then
  begin
    try
      repeat
        if (Item.Name <> '.') and (Item.Name <> '..') then
        begin
          if (Item.Attributes and FILE_ATTRIBUTE_REPARSE_POINT) <> 0 then
          begin
            Result := True;
            Exit;
          end;
          if (Item.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
          begin
            FullName := AddBackslash(Folder) + Item.Name;
            if HasLinkedChildren(FullName, Depth + 1) then
            begin
              Result := True;
              Exit;
            end;
          end;
        end;
      until not FindNext(Item);
    finally
      FindClose(Item);
    end;
  end;
end;

function CleanupRootIsSafe(const Folder: String): Boolean;
var
  Attributes: LongWord;
begin
  Attributes := GetFileAttributesW(Folder);
  Result := (Attributes <> $FFFFFFFF) and
    ((Attributes and FILE_ATTRIBUTE_REPARSE_POINT) = 0) and
    (not HasLinkedChildren(Folder, 0));
end;

function InitializeUninstall(): Boolean;
var
  Choice: Integer;
begin
  Result := True;
  RemovePersonalFiles := False;
  if UninstallSilent then
    Exit;

  Choice := MsgBox(
    'SRA will uninstall the application and remove its shortcuts.' + #13#10 + #13#10 +
    'Do you also want to DELETE local SRA research data and settings?' + #13#10 +
    'This includes copies of imported PDFs, notes, SQLite databases, API credentials, and SRA-managed caches in:' + #13#10 +
    ExpandConstant('{localappdata}\SRA') + #13#10 + #13#10 +
    'YES = DELETE this SRA user-data folder (irreversible).' + #13#10 +
    'NO = keep it for a future install (recommended).' + #13#10 +
    'CANCEL = do not uninstall.' + #13#10 + #13#10 +
    'PDF files stored elsewhere, custom SRA_HOME/SRA_DATA_DIR locations, and shared external caches will NOT be deleted.',
    mbConfirmation, MB_YESNOCANCEL);
  if Choice = IDCANCEL then
  begin
    Result := False;
    Exit;
  end;
  if Choice = IDYES then
    RemovePersonalFiles := MsgBox(
      'FINAL CONFIRMATION' + #13#10 + #13#10 +
      'Permanently erase the SRA user-data folder?' + #13#10 +
      ExpandConstant('{localappdata}\SRA') + #13#10 + #13#10 +
      'If you do not have a backup, choose NO.',
      mbConfirmation, MB_YESNO) = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  UserDirectory: String;
begin
  if (CurUninstallStep = usPostUninstall) and RemovePersonalFiles then
  begin
    UserDirectory := ExpandConstant('{localappdata}\SRA');
    if DirExists(UserDirectory) then
    begin
      if not CleanupRootIsSafe(UserDirectory) then
        MsgBox('Automatic removal stopped: SRA data contains a symbolic link, a junction,' + #13#10 +
               'or a directory that could not be safely inspected.' + #13#10 +
               'Please review it manually: ' + UserDirectory,
               mbInformation, MB_OK)
      else if not DelTree(UserDirectory, True, True, True) then
        MsgBox('The application was removed, but Windows could not completely remove SRA user data.' + #13#10 +
               'Please review the remaining files in: ' + UserDirectory,
               mbInformation, MB_OK);
    end;
  end;
end;
