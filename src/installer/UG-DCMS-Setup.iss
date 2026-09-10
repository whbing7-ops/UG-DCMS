#define MyAppName "UG-DCMS"
#define MyAppVersion "1.0.0-rc2.24"
#define MyAppPublisher "UG"
#define MyAppURL "http://localhost:8080"

[Setup]
AppId={{8C6A9E76-49B8-4C92-B11E-6E239E75D4A2}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={commonappdata}\UG-DCMS
DisableDirPage=yes
DisableProgramGroupPage=yes
DefaultGroupName=UG-DCMS
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=output
OutputBaseFilename=UG-DCMS-Setup-1.0.0-rc2.24
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
Uninstallable=yes
CreateUninstallRegKey=yes
SetupLogging=yes
RestartIfNeededByRun=no

[Files]
Source: "..\backend\*"; DestDir: "{app}\payload\backend"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\frontend\*"; DestDir: "{app}\payload\frontend"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\db\*"; DestDir: "{app}\payload\db"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\windows\*"; DestDir: "{app}\windows"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
Name: "{app}\data\files"; Permissions: users-modify
Name: "{app}\logs"; Permissions: users-modify
Name: "{app}\postgres"

[Run]
Filename: "{cmd}"; Parameters: "/c start """" ""http://localhost:8080"""; Description: "打开 UG-DCMS"; Flags: postinstall nowait skipifsilent; Check: InstallSucceeded

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\uninstall-services.ps1"""; Flags: runhidden waituntilterminated; RunOnceId: "UGDCMS-RemoveServices"

[Icons]
Name: "{group}\UG-DCMS"; Filename: "{cmd}"; Parameters: "/c start """" ""http://localhost:8080"""
Name: "{commondesktop}\UG-DCMS"; Filename: "{cmd}"; Parameters: "/c start """" ""http://localhost:8080"""

[Code]
var
  ProvisionSucceeded: Boolean;

function ReadLastError(): String;
var
  ErrorPath: String;
  Lines: TArrayOfString;
  I: Integer;
begin
  ErrorPath := ExpandConstant('{app}\logs\LAST-ERROR.txt');
  Result := '';
  if FileExists(ErrorPath) and LoadStringsFromFile(ErrorPath, Lines) then
  begin
    if GetArrayLength(Lines) > 0 then
    begin
      for I := 0 to GetArrayLength(Lines) - 1 do
      begin
        if Result <> '' then
          Result := Result + #13#10;
        Result := Result + Lines[I];
      end;
    end;
  end;
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  ProvisionSucceeded := False;
  if not IsWin64 then
  begin
    MsgBox('UG-DCMS 仅支持 64 位 Windows。', mbError, MB_OK);
    Result := False;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  PS, Args: String;
begin
  if CurStep = ssPostInstall then
  begin
    WizardForm.StatusLabel.Caption := '正在自动配置 UG-DCMS。详细进度请查看弹出的配置窗口...';
    PS := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
    Args := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\windows\install-oneclick.ps1') + '" -InstallDir "' + ExpandConstant('{app}') + '"';
    if not Exec(PS, Args, ExpandConstant('{app}'), SW_SHOW, ewWaitUntilTerminated, ResultCode) then
      RaiseException('无法启动 UG-DCMS 自动配置程序。');
    if ResultCode <> 0 then
    begin
      if ReadLastError() <> '' then
        RaiseException('UG-DCMS 自动配置失败，错误码：' + IntToStr(ResultCode) + '.' + #13#10 + #13#10 + ReadLastError() + #13#10 + #13#10 + '永久安装日志：' + ExpandConstant('{app}\logs\install.log'))
      else
        RaiseException('UG-DCMS 自动配置失败，错误码：' + IntToStr(ResultCode) + '. 永久安装日志：' + ExpandConstant('{app}\logs\install.log'));
    end;
    ProvisionSucceeded := True;
  end;
end;

function InstallSucceeded(): Boolean;
begin
  Result := ProvisionSucceeded and FileExists(ExpandConstant('{app}\INSTALLATION-STATUS.txt'));
end;
