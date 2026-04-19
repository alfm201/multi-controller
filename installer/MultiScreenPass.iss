#ifndef MyAppVersion
  #error MyAppVersion must be passed from the build script
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
#ifndef MyUpdaterExeName
  #error MyUpdaterExeName must be passed from the build script
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
CloseApplications=force
CloseApplicationsFilter=*.exe
RestartApplications=no

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#MyDistDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MyDistDir}\{#MyRecoveryExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MyDistDir}\{#MyWatchdogExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MyDistDir}\{#MyUpdaterExeName}.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[Code]
var
  DeleteUserDataOnUninstall: Boolean;

function AppUserDataRoot(const BaseDir: string): string;
begin
  if BaseDir = '' then begin
    Result := '';
    exit;
  end;
  Result := AddBackslash(BaseDir) + 'MultiScreenPass';
end;

procedure DeleteTreeIfPresent(const Path: string);
begin
  if (Path <> '') and DirExists(Path) then begin
    DelTree(Path, True, True, True);
  end;
end;

function ConfirmDeleteUserData(): Boolean;
var
  Form: TSetupForm;
  DescriptionLabel: TNewStaticText;
  PathLabel: TNewStaticText;
  DeleteDataCheckBox: TNewCheckBox;
  KeepDataHintLabel: TNewStaticText;
  OkButton: TNewButton;
  CancelButton: TNewButton;
  ButtonWidth: Integer;
begin
  Result := False;
  Form := CreateCustomForm(ScaleX(420), ScaleY(186), False, True);
  try
    Form.Caption := '{#MyAppName} 제거 옵션';

    DescriptionLabel := TNewStaticText.Create(Form);
    DescriptionLabel.Parent := Form;
    DescriptionLabel.Left := ScaleX(16);
    DescriptionLabel.Top := ScaleY(16);
    DescriptionLabel.Width := ScaleX(388);
    DescriptionLabel.AutoSize := False;
    DescriptionLabel.WordWrap := True;
    DescriptionLabel.Caption :=
      '{#MyAppName} 제거 후에도 사용자 설정과 로그는 기본적으로 보존됩니다.';

    PathLabel := TNewStaticText.Create(Form);
    PathLabel.Parent := Form;
    PathLabel.Left := ScaleX(16);
    PathLabel.Top := ScaleY(52);
    PathLabel.Width := ScaleX(388);
    PathLabel.AutoSize := False;
    PathLabel.WordWrap := True;
    PathLabel.Caption :=
      '삭제 대상: %LOCALAPPDATA%\MultiScreenPass\ 아래의 설정, 로그, 업데이트, tools, backup 파일';

    DeleteDataCheckBox := TNewCheckBox.Create(Form);
    DeleteDataCheckBox.Parent := Form;
    DeleteDataCheckBox.Left := ScaleX(16);
    DeleteDataCheckBox.Top := ScaleY(94);
    DeleteDataCheckBox.Width := ScaleX(388);
    DeleteDataCheckBox.Caption := '사용자 설정, 로그, 업데이트 파일도 함께 삭제';
    DeleteDataCheckBox.Checked := False;

    KeepDataHintLabel := TNewStaticText.Create(Form);
    KeepDataHintLabel.Parent := Form;
    KeepDataHintLabel.Left := ScaleX(36);
    KeepDataHintLabel.Top := ScaleY(120);
    KeepDataHintLabel.Width := ScaleX(368);
    KeepDataHintLabel.AutoSize := False;
    KeepDataHintLabel.WordWrap := True;
    KeepDataHintLabel.Caption := '선택하지 않으면 설정과 로그는 유지됩니다.';

    OkButton := TNewButton.Create(Form);
    OkButton.Parent := Form;
    OkButton.Caption := '확인';
    OkButton.ModalResult := mrOk;
    OkButton.Default := True;
    OkButton.Top := Form.ClientHeight - ScaleY(40);
    OkButton.Height := ScaleY(23);

    CancelButton := TNewButton.Create(Form);
    CancelButton.Parent := Form;
    CancelButton.Caption := '취소';
    CancelButton.ModalResult := mrCancel;
    CancelButton.Cancel := True;
    CancelButton.Top := Form.ClientHeight - ScaleY(40);
    CancelButton.Height := ScaleY(23);

    ButtonWidth := Form.CalculateButtonWidth([OkButton.Caption, CancelButton.Caption]);
    OkButton.Width := ButtonWidth;
    CancelButton.Width := ButtonWidth;
    CancelButton.Left := Form.ClientWidth - CancelButton.Width - ScaleX(16);
    OkButton.Left := CancelButton.Left - OkButton.Width - ScaleX(8);

    Form.ActiveControl := OkButton;

    Result := Form.ShowModal() = mrOk;
    if Result then begin
      DeleteUserDataOnUninstall := DeleteDataCheckBox.Checked;
    end;
  finally
    Form.Free();
  end;
end;

function InitializeUninstall(): Boolean;
begin
  DeleteUserDataOnUninstall := False;
  Result := ConfirmDeleteUserData();
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  LocalAppDataRoot: string;
  RoamingAppDataRoot: string;
begin
  if (CurUninstallStep <> usPostUninstall) or (not DeleteUserDataOnUninstall) then begin
    exit;
  end;

  LocalAppDataRoot := AppUserDataRoot(ExpandConstant('{localappdata}'));
  RoamingAppDataRoot := AppUserDataRoot(ExpandConstant('{userappdata}'));

  DeleteTreeIfPresent(LocalAppDataRoot);
  if CompareText(LocalAppDataRoot, RoamingAppDataRoot) <> 0 then begin
    DeleteTreeIfPresent(RoamingAppDataRoot);
  end;
end;
