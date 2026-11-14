; Inno Setup 安裝檔腳本，由 packaging\build.bat 編譯（需要 Inno Setup 6）
; 使用者資料（資料庫、.env、產出檔）放在 %LOCALAPPDATA%\ResearchBenchmarking，更新或解除安裝都不會動到。

#define AppName "系所學術表現分析系統"
#define AppExe "ResearchBenchmarking.exe"
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F0B3A52-4C1E-4E8B-9A57-2D6C1B7E9F41}
AppName={#AppName}
AppVersion={#AppVersion}
; 視窗標題與「應用程式」清單只顯示名稱，不加版本號
AppVerName={#AppName}
AppPublisher=國立中央大學研究發展處
DefaultDirName={autopf}\ResearchBenchmarking
DisableDirPage=no
DisableProgramGroupPage=yes
; 不需要系統管理員權限，預設只裝給目前的使用者
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=ResearchBenchmarking-Setup-{#AppVersion}
SetupIconFile=..\app\web\static\app.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "cht"; MessagesFile: "ChineseTraditional.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; 更新時先清掉舊版的程式檔，避免留下已移除的套件
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\ResearchBenchmarking\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
