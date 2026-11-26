"""연결 확인 — PLC 포트(TCP 접속) · 외부 PC(ping)를 RCS 가 직접 본다 (2026-11-24).

AGV 제어 노드와는 따로 본다. 노드가 PLC 에 붙었는지(차량 상태의 오류 701)와 별개로, 이 PC 에서 PLC
주소:포트로 TCP 접속이 되는지 확인하고 바로 끊는다(아무것도 보내지 않는다). 외부 PC 는 ping 으로 길이
있는지 본다. 둘 다 몇 초 걸릴 수 있어 백그라운드 스레드에서 돌리고 결과는 Qt 시그널로 돌려준다.
"""

from __future__ import annotations

import errno
import ipaddress
import re
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    #: "open" · "refused" · "timeout" · "unreachable" · "bad_address" · "error" (TCP)
    #: "reply" · "no_reply" (ping)
    kind: str
    text: str
    ms: float | None = None


_UNREACHABLE = {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EHOSTDOWN, errno.EADDRNOTAVAIL}


def _valid_host(host: str) -> bool:
    host = host.strip()
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        # 이름도 받는다(글자 · 숫자 · 점 · 하이픈).
        return bool(re.fullmatch(r"[A-Za-z0-9.-]+", host))


def probe_tcp(host: str, port: int, timeout_s: float = 2.0) -> ProbeResult:
    """host:port 로 TCP 접속만 해 보고 바로 끊는다."""
    host = str(host or "").strip()
    target = f"{host}:{int(port)}"
    if not _valid_host(host) or not 0 < int(port) < 65536:
        return ProbeResult(False, "bad_address", f"주소가 올바르지 않습니다 — {target}")
    started = time.monotonic()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout_s):
            ms = (time.monotonic() - started) * 1000.0
        return ProbeResult(True, "open", f"열림 — {target} 접속됨 ({ms:.0f} ms)", ms)
    except ConnectionRefusedError:
        return ProbeResult(False, "refused",
                           f"닫힘 — {host} 는 응답했지만 {int(port)} 포트를 받지 않습니다(포트 번호 · PLC 설정 확인)")
    except (socket.timeout, TimeoutError):
        return ProbeResult(False, "timeout",
                           f"응답 없음 — {timeout_s:g} s 안에 {target} 의 답이 없습니다(전원 · 케이블 · 주소 확인)")
    except socket.gaierror:
        return ProbeResult(False, "bad_address", f"주소를 찾을 수 없습니다 — {host}")
    except OSError as exc:
        if exc.errno in _UNREACHABLE:
            return ProbeResult(False, "unreachable",
                               f"경로 없음 — 이 PC 에서 {host} 로 갈 길이 없습니다(같은 대역 주소 · 케이블 확인)")
        return ProbeResult(False, "error", f"접속 실패 — {target}: {exc.strerror or exc}")


def ping(host: str, timeout_s: float = 1.0) -> ProbeResult:
    """ping 한 번. 응답 시간 [ms] 을 싣는다."""
    host = str(host or "").strip()
    if not _valid_host(host):
        return ProbeResult(False, "bad_address", f"주소가 올바르지 않습니다 — {host or '(비어 있음)'}")
    if sys.platform.startswith("win"):
        command = ["ping", "-n", "1", "-w", str(int(timeout_s * 1000)), host]
    else:
        command = ["ping", "-c", "1", "-W", str(max(int(round(timeout_s)), 1)), host]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout_s + 3.0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ProbeResult(False, "error", f"ping 을 실행하지 못했습니다 — {exc}")
    match = re.search(r"time[=<]\s*([0-9.]+)\s*ms", done.stdout)
    if done.returncode == 0:
        ms = float(match.group(1)) if match else None
        return ProbeResult(True, "reply", f"ping 응답 — {host}" + (f" ({ms:.1f} ms)" if ms is not None else ""), ms)
    return ProbeResult(False, "no_reply", f"ping 응답 없음 — {host}(전원 · 케이블 · 주소 · 그 PC 방화벽 확인)")


class LinkProbe(QObject):
    """확인을 백그라운드에서 돌리고 결과를 `finished(이름, 결과)` 로 낸다(GUI 를 막지 않는다)."""

    finished = pyqtSignal(str, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._busy: set[str] = set()
        self._lock = threading.Lock()

    def busy(self, name: str) -> bool:
        with self._lock:
            return name in self._busy

    def run(self, name: str, check: Callable[[], ProbeResult]) -> bool:
        """`check()` 를 따로 돌린다. 같은 이름이 아직 도는 중이면 False(두 번 누름)."""
        with self._lock:
            if name in self._busy:
                return False
            self._busy.add(name)

        def work() -> None:
            try:
                result = check()
            except Exception as exc:  # noqa: BLE001 — 확인이 죽어도 결과는 돌려준다
                result = ProbeResult(False, "error", f"확인 중 오류 — {exc}")
            with self._lock:
                self._busy.discard(name)
            self.finished.emit(name, result)

        threading.Thread(target=work, name=f"probe-{name}", daemon=True).start()
        return True
