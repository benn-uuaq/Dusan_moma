"""「로봇 VNC 열기」 — 환경마다 맞는 뷰어를 고른다(2026-11-05). 실제로 띄우지는 않는다."""

from smr_operator_ui.services import robot_vnc
from smr_operator_ui.services.robot_vnc import REALVNC_LNK, open_robot_vnc


class _Spy:
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))


def test_windows_opens_the_realvnc_shortcut_with_the_ip():
    start = _Spy()
    ok, message = open_robot_vnc("192.168.1.101", system="windows",
                                 exists=lambda path: True, startfile=start)
    assert ok and "RealVNC" in message
    assert start.calls == [((REALVNC_LNK, "open", "192.168.1.101"), {})]


def test_wsl_opens_the_windows_shortcut_through_powershell():
    popen = _Spy()
    ok, _message = open_robot_vnc("192.168.1.101", system="wsl",
                                  exists=lambda path: True, popen=popen)
    assert ok
    (argv,), kwargs = popen.calls[0]
    assert argv[0].endswith("powershell.exe")
    assert argv[-1] == f"Start-Process -FilePath '{REALVNC_LNK}' -ArgumentList '192.168.1.101'"
    assert kwargs["cwd"] == "/mnt/c", "WSL 폴더에서 띄우면 경로 경고가 뜬다"


def test_missing_realvnc_is_reported_not_launched():
    popen = _Spy()
    ok, message = open_robot_vnc("192.168.1.101", system="wsl",
                                 exists=lambda path: False, popen=popen)
    assert not ok and "설치" in message and popen.calls == []


def test_ubuntu_uses_remmina_vnc():
    popen = _Spy()
    ok, _message = open_robot_vnc(
        "192.168.1.101", system="linux", popen=popen,
        which=lambda name: f"/usr/bin/{name}" if name == "remmina" else None)
    assert ok
    (argv,), _kwargs = popen.calls[0]
    assert argv == ["/usr/bin/remmina", "-c", f"vnc://192.168.1.101:{robot_vnc.VNC_PORT}"]


def test_ubuntu_falls_back_to_vncviewer_then_explains():
    popen = _Spy()
    ok, _message = open_robot_vnc(
        "10.0.0.5", system="linux", popen=popen,
        which=lambda name: "/usr/bin/vncviewer" if name == "vncviewer" else None)
    assert ok and popen.calls[0][0][0] == ["/usr/bin/vncviewer", "10.0.0.5::5900"]

    ok, message = open_robot_vnc("10.0.0.5", system="linux", popen=popen,
                                 which=lambda name: None)
    assert not ok and "remmina" in message


def test_bad_ip_launches_nothing():
    popen = _Spy()
    for ip in ("", "robot; rm -rf /", "192.168.1"):
        ok, message = open_robot_vnc(ip, system="linux", popen=popen,
                                     which=lambda name: "/usr/bin/remmina")
        assert not ok and "IP" in message
    assert popen.calls == []
