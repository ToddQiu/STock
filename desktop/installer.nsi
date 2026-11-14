Unicode true
!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"

!ifndef REG_KEY
  !define REG_KEY "Software\STockDesktop"
!endif
!ifndef UNINSTALL_REG_KEY
  !define UNINSTALL_REG_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\STockDesktop"
!endif

Name "STock"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\STock"
InstallDirRegKey HKCU "${REG_KEY}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 32
SetDatablockOptimize on
ShowInstDetails show
ShowUninstDetails show
VIProductVersion "1.0.4.0"
VIAddVersionKey /LANG=1033 "ProductName" "STock"
VIAddVersionKey /LANG=1033 "FileDescription" "STock Windows desktop installer"
VIAddVersionKey /LANG=1033 "FileVersion" "1.0.4"
VIAddVersionKey /LANG=1033 "LegalCopyright" "STock"

!define MUI_ICON "${PAYLOAD}\resources\app\logo.ico"
!define MUI_UNICON "${PAYLOAD}\resources\app\logo.ico"
!define MUI_ABORTWARNING
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

Var NoShortcuts
Function .onInit
  SetShellVarContext current
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "STock requires 64-bit Windows." /SD IDOK
    Abort
  ${EndIf}
  ${GetParameters} $0
  ClearErrors
  ${GetOptions} $0 "/NOSHORTCUTS" $1
  ${IfNot} ${Errors}
    StrCpy $NoShortcuts "1"
  ${EndIf}
FunctionEnd

Function CheckProgramFile
  Pop $0
  IfFileExists "$0" 0 check_done
  check_retry:
  ; OPEN_EXISTING with no sharing checks executable locks without changing contents.
  System::Call 'kernel32::CreateFileW(w r0, i 0x40000000, i 0, p 0, i 3, i 0, p 0) p.r1'
  ${If} $1 == -1
    IfSilent check_failed 0
    MessageBox MB_ICONEXCLAMATION|MB_RETRYCANCEL "无法更新程序文件，请完全退出 STock 后重试（包括后台窗口）。若仍失败，请检查安装目录权限。$\r$\n$0" IDRETRY check_retry
    check_failed:
    SetErrorLevel 1
    Abort
  ${EndIf}
  System::Call 'kernel32::CloseHandle(p r1)'
  check_done:
FunctionEnd

Section "STock" Main
  Push "$INSTDIR\STock.exe"
  Call CheckProgramFile
  Push "$INSTDIR\resources\app\backend\STockServer.exe"
  Call CheckProgramFile
  Push "$INSTDIR\resources\app\runtime\node.exe"
  Call CheckProgramFile
  SetOutPath "$INSTDIR"
  SetOverwrite on
  ClearErrors
  File /r "${PAYLOAD}\*"
  ${If} ${Errors}
    MessageBox MB_ICONSTOP "程序文件未能完整更新，请退出 STock 后重新运行安装包。原有用户数据保留。" /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "${REG_KEY}" "InstallDir" "$INSTDIR"
  WriteRegDWORD HKCU "${REG_KEY}" "Shortcuts" 0
  ${If} $NoShortcuts != "1"
    CreateDirectory "$SMPROGRAMS\STock"
    CreateShortCut "$SMPROGRAMS\STock\STock.lnk" "$INSTDIR\STock.exe"
    CreateShortCut "$DESKTOP\STock.lnk" "$INSTDIR\STock.exe"
    WriteRegDWORD HKCU "${REG_KEY}" "Shortcuts" 1
  ${EndIf}
  WriteRegStr HKCU "${UNINSTALL_REG_KEY}" "DisplayName" "STock"
  WriteRegStr HKCU "${UNINSTALL_REG_KEY}" "DisplayVersion" "1.0.4"
  WriteRegStr HKCU "${UNINSTALL_REG_KEY}" "DisplayIcon" "$INSTDIR\STock.exe"
  WriteRegStr HKCU "${UNINSTALL_REG_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_REG_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINSTALL_REG_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINSTALL_REG_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_REG_KEY}" "NoRepair" 1
  ${If} ${Errors}
    SetErrorLevel 1
    Abort
  ${EndIf}
  SetErrorLevel 0
SectionEnd

Function un.onInit
  SetShellVarContext current
FunctionEnd

Section "Uninstall"
  ReadRegStr $0 HKCU "${REG_KEY}" "InstallDir"
  ${If} $0 == $INSTDIR
    ReadRegDWORD $1 HKCU "${REG_KEY}" "Shortcuts"
    ${If} $1 == 1
      Delete "$DESKTOP\STock.lnk"
      Delete "$SMPROGRAMS\STock\STock.lnk"
      RMDir "$SMPROGRAMS\STock"
    ${EndIf}
    DeleteRegKey HKCU "${REG_KEY}"
    DeleteRegKey HKCU "${UNINSTALL_REG_KEY}"
  ${EndIf}
  !include "${UNINSTALL_FILES}"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
SectionEnd
