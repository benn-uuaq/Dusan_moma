"""로봇 화면(VNC)을 연다 — 티치 펜던트를 달지 않는 로봇이라 화면을 볼 장치가 필요하다.

연결 설정 화면의 「로봇 VNC 열기」가 쓴다(2026-11-05). 실행 환경마다 여는 프로그램이 다르다.

* Windows(단독 실행 파일)·WSL: RealVNC Viewer 바로가기(REALVNC_LNK)를 로봇 IP 와 함께 연다.
  WSL 에서는 Windows 의 PowerShell 로 연다 — Windows 프로그램이라 WSL 안에서 직접 못 띄운다.
* 우분투 PC: 우분투 기본 원격 데스크톱 클라이언트 Remmina 로 `vnc://IP:5900` 에 붙는다
  (Remmina 는 RDP·VNC 둘 다 하지만 로봇 화면은 VNC 다). 없으면 vncviewer 계열을 찾는다.

창만 띄우고 기다리지 않는다. 비밀번호를 묻는 건 각 뷰어가 한다.
"""

from __future__ import annotations

import ipaddress
import os
import platform
import shutil
import subprocess
import sys

#: RealVNC Viewer 시작 메뉴 바로가기(대상: C:\Program Files\RealVNC\VNC Viewer\vncviewer.exe).
REALVNC_LNK = r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\RealVNC\RealVNC Viewer.lnk"
#: WSL 에서 본 같은 바로가기 — 설치 여부만 여기서 확인한다.
REALVNC_LNK_WSL = "/mnt/c/ProgramData/Microsoft/Windows/Start Menu/Programs/RealVNC/RealVNC Viewer.lnk"
#: WSL 에서 Windows 프로그램을 띄울 PowerShell. PATH 에 Windows 경로가 안 붙은 WSL 도 있다.
POWERSHELL_WSL = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
#: 로봇 컨트롤러의 VNC 포트.
VNC_PORT = 5900

#: 우분투에서 찾아볼 뷰어 — (실행 파일, 인자 만들기). 앞의 것이 먼저.
LINUX_VIEWERS = (
    ("remmina", lambda ip: ["-c", f"vnc://{ip}:{VNC_PORT}"]),
    # TigerVNC · RealVNC(리눅스) 공통 — host::port 는 포트 번호.
    ("vncviewer", lambda ip: [f"{ip}::{VNC_PORT}"]),
)


def detect_platform() -> str:
    """"windows" · "wsl" · "linux" 중 하나."""
    if sys.platform.startswith("win"):
        return "windows"
    if os.environ.get("WSL_DISTRO_NAME") or "microsoft" in platform.uname().release.lower():
        return "wsl"
    return "linux"


def _valid_ip(ip: str) -> bool:
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return False
    return True


def open_robot_vnc(ip: str, *, system: str | None = None, popen=subprocess.Popen,
                   which=shutil.which, exists=os.path.exists, startfile=None) -> tuple[bool, str]:
    """로봇 IP 의 VNC 화면을 연다. (성공 여부, 화면에 띄울 문구)를 돌려준다."""
    ip = (ip or "").strip()
    if not _valid_ip(ip):
        return False, f"로봇 IP 가 올바르지 않습니다: '{ip}'"
    system = system or detect_platform()

    try:
        if system == "windows":
            if not exists(REALVNC_LNK):
                return False, "RealVNC Viewer 가 설치되어 있지 않습니다(시작 메뉴 바로가기 없음)."
            (startfile or os.startfile)(REALVNC_LNK, "open", ip)
            return True, f"RealVNC Viewer 로 로봇 화면({ip})을 엽니다."

        if system == "wsl":
            if not exists(REALVNC_LNK_WSL):
                return False, "Windows 에 RealVNC Viewer 가 설치되어 있지 않습니다(시작 메뉴 바로가기 없음)."
            shell = POWERSHELL_WSL if exists(POWERSHELL_WSL) else "powershell.exe"
            command = f"Start-Process -FilePath '{REALVNC_LNK}' -ArgumentList '{ip}'"
            # WSL 폴더(\\wsl.localhost\...)에서 Windows 프로그램을 띄우면 경로 경고가
            # 나므로 C:\ 에서 띄운다.
            popen([shell, "-NoProfile", "-NonInteractive", "-Command", command],
                  cwd="/mnt/c" if exists("/mnt/c") else None,
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True, f"RealVNC Viewer 로 로봇 화면({ip})을 엽니다."

        for program, arguments in LINUX_VIEWERS:
            path = which(program)
            if path:
                popen([path, *arguments(ip)], stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL, start_new_session=True)
                return True, f"{program} 로 로봇 화면({ip}:{VNC_PORT})을 엽니다."
        return False, ("VNC 뷰어가 없습니다. 우분투 원격 데스크톱(Remmina)을 설치하세요: "
                       "sudo apt install remmina remmina-plugin-vnc")
    except OSError as exc:
        return False, f"로봇 화면을 열지 못했습니다: {exc}"
