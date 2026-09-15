"""폴더 경로(데이터 저장 위치 등)를 고르는 입력칸.

누르면 키보드가 아니라 **폴더 선택 창**이 뜬다.

  * WSL 에서 돌 때: Windows 탐색기식 폴더 선택 창. RCS 창은 WSLg 로 뜨지만
    사용자는 Windows 를 쓰고 있으므로 그쪽 창이 자연스럽다. PowerShell 로
    Windows 의 공용 파일 대화상자(IFileOpenDialog, 폴더 모드)를 띄운다 —
    PowerShell 5 의 FolderBrowserDialog 는 옛날 트리 창이라 쓰지 않는다.
  * 그 밖의 리눅스(실제 우분투 장비): Qt 폴더 선택 창. 데스크톱이 있으면
    그 데스크톱의 기본 창으로 뜬다.

Windows 창은 별도 프로세스라 기다리는 동안 RCS 화면이 멈추지 않게
QProcess 로 비동기 실행한다. PowerShell 을 못 띄우면 Qt 창으로 넘어간다.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
from collections.abc import Callable

from PyQt6.QtCore import QObject, QProcess, Qt, pyqtSignal
from PyQt6.QtWidgets import QFileDialog, QWidget

from smr_operator_ui.keypad import TouchLineEdit

_POWERSHELL_FALLBACK = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"

# Windows 공용 파일 대화상자를 폴더 선택 모드로 띄운다.
# 인터페이스 선언 순서는 shobjidl.h 의 vtable 순서 그대로여야 한다.
_PICKER_CS = r"""
using System;
using System.IO;
using System.Runtime.InteropServices;

public static class SmrFolderPicker {
    [ComImport, Guid("DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7")]
    private class FileOpenDialogRCW {}

    [ComImport, Guid("42f85136-db7e-439c-85f1-e4075d135fc8"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IFileOpenDialog {
        [PreserveSig] int Show(IntPtr parent);
        void SetFileTypes(uint c, IntPtr f);
        void SetFileTypeIndex(uint i);
        void GetFileTypeIndex(out uint i);
        void Advise(IntPtr e, out uint c);
        void Unadvise(uint c);
        void SetOptions(uint o);
        void GetOptions(out uint o);
        void SetDefaultFolder(IShellItem si);
        void SetFolder(IShellItem si);
        void GetFolder(out IShellItem si);
        void GetCurrentSelection(out IShellItem si);
        void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string n);
        void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string n);
        void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string t);
        void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string t);
        void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string t);
        void GetResult(out IShellItem si);
        void AddPlace(IShellItem si, int fdap);
        void SetDefaultExtension([MarshalAs(UnmanagedType.LPWStr)] string e);
        void Close(int hr);
        void SetClientGuid(ref Guid g);
        void ClearClientData();
        void SetFilter(IntPtr f);
        void GetResults(out IntPtr e);
        void GetSelectedItems(out IntPtr e);
    }

    [ComImport, Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IShellItem {
        void BindToHandler(IntPtr pbc, ref Guid bhid, ref Guid riid, out IntPtr ppv);
        void GetParent(out IShellItem si);
        void GetDisplayName(uint sigdn, [MarshalAs(UnmanagedType.LPWStr)] out string name);
        void GetAttributes(uint mask, out uint attrs);
        void Compare(IShellItem si, uint hint, out int order);
    }

    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    private static extern void SHCreateItemFromParsingName(
        string path, IntPtr pbc, ref Guid riid, out IShellItem item);

    private const uint FOS_PICKFOLDERS = 0x20;
    private const uint FOS_FORCEFILESYSTEM = 0x40;
    private const uint FOS_PATHMUSTEXIST = 0x800;
    private const uint SIGDN_FILESYSPATH = 0x80058000;

    public static string Pick(string title, string start, IntPtr owner) {
        var dlg = (IFileOpenDialog)new FileOpenDialogRCW();
        dlg.SetOptions(FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST);
        dlg.SetTitle(title);
        // 지금 값의 폴더에서 연다. 아직 없는 폴더면 있는 상위 폴더에서.
        string dir = start;
        while (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir)) {
            dir = Path.GetDirectoryName(dir);
        }
        if (!string.IsNullOrEmpty(dir)) {
            Guid g = typeof(IShellItem).GUID;
            IShellItem si;
            SHCreateItemFromParsingName(dir, IntPtr.Zero, ref g, out si);
            dlg.SetFolder(si);
        }
        if (dlg.Show(owner) != 0) return null;       // 취소
        IShellItem res;
        dlg.GetResult(out res);
        string path;
        res.GetDisplayName(SIGDN_FILESYSPATH, out path);
        return path;
    }
}
"""

# 대화상자가 RCS(WSLg) 창 뒤에 숨지 않게, 보이지 않는 맨 앞 창을 주인으로 삼는다.
_PICKER_PS = """
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
Add-Type -TypeDefinition @'
{cs}
'@
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.FormBorderStyle = 'None'
$owner.StartPosition = 'CenterScreen'
$owner.Size = New-Object System.Drawing.Size(1, 1)
$owner.Opacity = 0
$owner.Show()
$owner.Activate()
try {{ $p = [SmrFolderPicker]::Pick({title}, {start}, $owner.Handle) }}
finally {{ $owner.Close() }}
if ($p) {{ [Console]::Out.Write($p) }}
"""


def running_in_wsl() -> bool:
    """WSL 안에서 도는가."""
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        with open("/proc/sys/kernel/osrelease", encoding="utf-8") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def windows_powershell() -> str | None:
    """WSL 에서 부를 수 있는 Windows PowerShell 경로."""
    found = shutil.which("powershell.exe")
    if found:
        return found
    return _POWERSHELL_FALLBACK if os.path.exists(_POWERSHELL_FALLBACK) else None


def _ps_quote(text: str) -> str:
    """PowerShell 작은따옴표 문자열로 감싼다('' 가 ' 하나)."""
    return "'" + text.replace("'", "''") + "'"


def windows_picker_args(title: str, start_windows_path: str) -> list[str]:
    """PowerShell 인자. 한글 제목·경로가 깨지지 않게 -EncodedCommand 로 넘긴다."""
    script = _PICKER_PS.format(cs=_PICKER_CS, title=_ps_quote(title),
                               start=_ps_quote(start_windows_path))
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return ["-NoProfile", "-STA", "-ExecutionPolicy", "Bypass",
            "-EncodedCommand", encoded]


def to_windows_path(path: str) -> str:
    """저장된 값을 Windows 대화상자의 시작 위치로 바꾼다.

    "D:/SMR/Data" 처럼 Windows 경로면 구분자만 바꾸고, "/home/..." 처럼
    리눅스 경로면 wslpath 로 바꾼다(\\\\wsl.localhost\\... 로 열린다).
    """
    path = path.strip()
    if not path:
        return ""
    if path.startswith("/"):
        try:
            out = subprocess.run(["wslpath", "-w", path], capture_output=True,
                                 text=True, timeout=3)
            return out.stdout.strip() if out.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""
    return path.replace("/", "\\")


def from_windows_path(path: str) -> str:
    """고른 Windows 경로를 화면 표기(D:/SMR/Data)로 바꾼다."""
    return path.strip().replace("\\", "/")


def to_local_path(path: str) -> str:
    """고른 경로를 이 프로그램이 파일을 쓸 수 있는 경로로 바꾼다.

    WSL 에서 Windows 창이 준 "D:/Export" 는 /mnt/d/Export 로,
    "//wsl.localhost/<배포판>/home/..." 는 /home/... 로 바꾼다.
    그 밖에는 그대로다.
    """
    path = (path or "").strip()
    unc = re.match(r"^[\\/]{2}(?:wsl\.localhost|wsl\$)[\\/][^\\/]+(.*)$", path, re.I)
    if unc:
        return unc.group(1).replace("\\", "/") or "/"
    drive = re.match(r"^([A-Za-z]):[\\/]*(.*)$", path)
    if drive and running_in_wsl():
        rest = drive.group(2).replace("\\", "/").strip("/")
        return f"/mnt/{drive.group(1).lower()}/{rest}".rstrip("/")
    return path


def open_in_file_manager(path: str) -> None:
    """폴더를 파일 관리자로 연다. WSL 이면 Windows 탐색기, 아니면 데스크톱 기본."""
    if running_in_wsl() and shutil.which("explorer.exe"):
        QProcess.startDetached("explorer.exe", [to_windows_path(path)])
        return
    from PyQt6.QtCore import QUrl
    from PyQt6.QtGui import QDesktopServices
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))


class FolderPicker(QObject):
    """폴더 선택 창 하나. 고르면 `chosen` 으로 화면 표기 경로를 낸다.

    WSL 이면 Windows 탐색기식 창(비동기), 아니면 Qt 창. 데이터 저장 위치
    칸과 로그 파일·오류 로그의 '내보내기'가 같이 쓴다. 시험에서는
    `chooser` 에 (시작 경로) -> 고른 경로|None 함수를 넣는다.
    """

    chosen = pyqtSignal(str)

    def __init__(self, title: str = "폴더 선택", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title = title
        self.chooser: Callable[[str], str | None] | None = None
        self._widget = parent
        self._process: QProcess | None = None

    @property
    def busy(self) -> bool:
        return self._process is not None

    def open(self, start: str = "") -> None:
        """창을 연다. 이미 떠 있으면 또 띄우지 않는다."""
        if self._process is not None:
            return
        if self.chooser is not None:
            self._accept(self.chooser(start))
            return
        powershell = windows_powershell() if running_in_wsl() else None
        if powershell:
            self._open_windows_picker(powershell, start)
        else:
            self._open_qt_picker(start)

    def _open_qt_picker(self, start: str) -> None:
        chosen = QFileDialog.getExistingDirectory(self._widget, self.title, to_local_path(start))
        self._accept(chosen or None)

    def _open_windows_picker(self, powershell: str, start: str) -> None:
        proc = QProcess(self)
        self._process = proc
        proc.finished.connect(self._on_windows_picker_finished)
        proc.errorOccurred.connect(lambda error, s=start: self._on_windows_picker_error(error, s))
        proc.start(powershell, windows_picker_args(self.title, to_windows_path(start)))

    def _on_windows_picker_finished(self, code: int, _status) -> None:
        proc, self._process = self._process, None
        if proc is None:
            return
        chosen = bytes(proc.readAllStandardOutput()).decode("utf-8", "replace").strip()
        proc.deleteLater()
        if code == 0 and chosen:
            self._accept(from_windows_path(chosen))

    def _on_windows_picker_error(self, error, start: str) -> None:
        if error != QProcess.ProcessError.FailedToStart:
            return
        # PowerShell 을 못 띄우면 Qt 창으로라도 고르게 한다.
        proc, self._process = self._process, None
        if proc is not None:
            proc.deleteLater()
        self._open_qt_picker(start)

    def _accept(self, chosen: str | None) -> None:
        if chosen:
            self.chosen.emit(chosen)


class FolderPathEdit(TouchLineEdit):
    """누르면 폴더 선택 창이 뜨는 경로 입력칸 (직접 타이핑 없음).

    `TouchLineEdit` 을 이어받아 설정 화면의 저장·불러오기는 그대로 탄다.
    시험에서는 `chooser` 에 (현재 경로) -> 고른 경로|None 함수를 넣어
    실제 창 없이 돌린다.
    """

    def __init__(self, text: str = "", title: str = "폴더 선택",
                 parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setReadOnly(True)
        self._picker = FolderPicker(title, self)
        self._picker.chosen.connect(self.setText)

    @property
    def title(self) -> str:
        return self._picker.title

    @property
    def chooser(self):
        return self._picker.chooser

    @chooser.setter
    def chooser(self, fn) -> None:
        self._picker.chooser = fn

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.open_picker()
            event.accept()
            return
        super(TouchLineEdit, self).mousePressEvent(event)

    def open_picker(self) -> None:
        """폴더 선택 창을 연다(지금 값에서 시작)."""
        self._picker.open(self.text())
