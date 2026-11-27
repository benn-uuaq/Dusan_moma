"""설비 전원을 켰을 때 로봇 컨트롤러 부팅을 기다렸다가 전원 ON · 브레이크 해제까지 한다(2026-11-27).

RCS · PLC 는 바로 켜지는데 로봇 컨트롤러는 부팅이 오래 걸린다. 켜자마자 「로봇 부팅 중」 창을 띄우고:

  1. 로봇 제어 노드가 로봇에 붙기를 기다린다(노드가 RECONNECT_S 마다 스스로 다시 붙는다)
  2. 전원 ON · 브레이크 해제 전에 **원격 모드 · 자동 모드**를 맞춘다(2026-11-27) — 원격 모드여야 30001 로
     보낸 스크립트를 받고, 자동 모드여야 안전정지(전원이 안 꺼지는 정지) · 안전 리셋이 먹는다.
       제어 방식(레지스터 71)이 Remote(2)가 아니면 29999 remoteControl -on 을 보내고 Remote 가 되기를 기다린다.
       작동 모드(레지스터 72)가 수동(1)이면 기다린다 — 29999 에 자동 모드로 바꾸는 명령이 없다(V2.15 대시보드
       명령표 · 사용 설명서). 펜던트에서 자동 모드로 바꾸거나 안전 IO 「작동 모드 입력」(SF09)으로 정한다.
  3. 로봇 모드(레지스터 66)를 보고
       POWER_OFF(3)  → 전원 ON(29999 robotControl -on)
       IDLE(5)       → 브레이크 해제(29999 brakeRelease)
       RUNNING(7)    → 끝 — 「로봇 부팅 완료」를 보이고 창을 스스로 닫는다
     BOOTING(2) · POWER_ON(4) 은 기다린다. CONFIRM_SAFETY(1) 는 펜던트에서 안전 설정을 확인해야
     한다 — 손대지 않고 알린다.
  3. 명령이 안 먹으면 RETRY_S 뒤 다시 보낸다.

창의 「닫기」를 누르면 자동 진행을 멈춘다(작업자가 손으로 하겠다는 뜻). 연결 설정에서 「연결 해제」를
눌러도 멈춘다.

**RCS 최초 시작 때만** 켠다(2026-11-27). 충돌 · 비상정지 · 에러로 로봇팔만 꺼진 경우(컨트롤러 · RCS 는 켜진
채)에는 RCS 가 켜면 안 된다 — RCS 는 비상정지로 꺼지지 않으므로, RCS 를 켤 때는 컨트롤러가 이미 떠 있어도
팔을 켠다(사용자 결정):
  * RCS 를 켠 뒤 한 번만 돈다 — 끝나거나 멈추면 다시는 안 돈다(이후 팔이 꺼져도 손대지 않는다).
  * 도는 중에 비상정지 · 안전정지가 들어오면 바로 멈춘다.
"""

from __future__ import annotations

import time
from typing import Callable

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout, QWidget

MODE_CONFIRM_SAFETY, MODE_BOOTING, MODE_POWER_OFF, MODE_POWER_ON, MODE_IDLE, MODE_RUNNING = 1, 2, 3, 4, 5, 7
#: 제어 방식(레지스터 71) Remote · 작동 모드(레지스터 72) 자동 · 수동.
CONTROL_REMOTE = 2
OPERATION_AUTOMATIC, OPERATION_MANUAL = 0, 1


class RobotBootDialog(QDialog):
    """「로봇 부팅 중」 창 — 모달이 아니다(그동안 다른 화면을 써도 된다)."""

    closed_by_user = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RobotBootDialog")
        self.setWindowTitle("로봇 부팅")
        self.setModal(False)
        # 글자가 잘리지 않게 넉넉히(2026-11-27 — 460 에서 「확인한 뒤 …」 줄이 잘렸다).
        self.setMinimumSize(640, 280)
        layout = QVBoxLayout(self)
        self.title = QLabel("로봇 부팅 중")
        self.title.setObjectName("SectionTitle")
        self.text = QLabel("")
        self.text.setWordWrap(True)
        self.text.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.close_button = QPushButton("닫기 (자동 진행 멈춤)")
        self.close_button.clicked.connect(self._user_close)
        layout.addWidget(self.title)
        layout.addWidget(self.text, 1)
        layout.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignRight)
        self._closing_by_code = False

    def show_state(self, title: str, text: str) -> None:
        self.title.setText(title)
        self.text.setText(text)

    def finish(self) -> None:
        self._closing_by_code = True
        self.close()

    def _user_close(self) -> None:
        self.closed_by_user.emit()
        self.finish()

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt 이름
        if not self._closing_by_code:
            self.closed_by_user.emit()      # 창의 X 로 닫아도 멈춘다
        super().closeEvent(event)


class RobotBootAssist(QObject):
    """부팅을 기다렸다가 전원 ON · 브레이크 해제. 명령은 `send(이름)` 으로 보낸다(RosStatusClient.call_command)."""

    #: 같은 명령을 다시 보내기까지 [s] — 전원 ON 은 POWER_ON(4) 을 지나 IDLE 이 되기까지 시간이 걸린다.
    RETRY_S = 20.0
    #: 이만큼 지나도 끝나지 않으면 창에 확인할 것을 적는다(자동 진행은 계속) [s].
    WARN_S = 600.0
    #: 「부팅 완료」를 보여 주고 창을 닫기까지 [ms].
    DONE_CLOSE_MS = 3000

    activity = pyqtSignal(str)
    finished = pyqtSignal(bool)        # True = RUNNING 까지 됨, False = 작업자가 멈춤

    def __init__(self, send: Callable[[str], object], parent: QWidget | None = None,
                 dialog: RobotBootDialog | None = None) -> None:
        super().__init__(parent)
        self._send = send
        self.dialog = dialog if dialog is not None else RobotBootDialog(parent)
        self.dialog.closed_by_user.connect(lambda: self.cancel("작업자가 닫았습니다"))
        self._active = False
        self._connected = False
        self._mode: int | None = None
        self._control: int | None = None       # 제어 방식(71)
        self._operation: int | None = None     # 작동 모드(72)
        self._sent: dict[str, float] = {}
        self._started = 0.0

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

    @property
    def active(self) -> bool:
        return self._active

    def start(self) -> None:
        if self._active:
            return
        self._active = True
        self._started = time.monotonic()
        self._sent.clear()
        self.dialog.show()
        self._timer.start()
        self._refresh()

    def cancel(self, why: str = "") -> None:
        if not self._active:
            return
        self._active = False
        self._timer.stop()
        self.dialog.finish()
        self.activity.emit(f"로봇 부팅 자동 진행을 멈췄습니다{' — ' + why if why else ''}. "
                           "전원 · 브레이크는 Cobot 수동 제어에서 하세요.")
        self.finished.emit(False)

    def on_connected(self, connected: bool) -> None:
        self._connected = bool(connected)
        if not connected:
            self._mode = None
        self._refresh()

    def on_mode(self, code: int, _name: str = "") -> None:
        self._mode = int(code)
        self._refresh()

    def on_control_method(self, code: int, _name: str = "") -> None:
        self._control = int(code)
        self._refresh()

    def on_operation_mode(self, code: int, _name: str = "") -> None:
        self._operation = int(code)
        self._refresh()

    def on_fault(self, active: bool) -> None:
        """비상정지 · 안전정지가 들어왔다 — 자동 진행을 바로 멈춘다(사고 뒤에는 RCS 가 켜지 않는다)."""
        if active:
            self.cancel("비상정지 · 안전정지가 들어왔습니다")

    def on_command_result(self, name: str, ok: bool, message: str) -> None:
        """전원 ON · 브레이크 해제가 거절됐다 — 창에 적고 RETRY_S 뒤 다시 보낸다."""
        if self._active and name in ("power_on", "brake_release") and not ok:
            self._failed = f"{'전원 ON' if name == 'power_on' else '브레이크 해제'} 거절 — {message}"
            self._refresh()

    # ------------------------------------------------------------------
    def _tick(self) -> None:
        self._refresh()

    def _elapsed(self) -> int:
        return int(time.monotonic() - self._started)

    def _send_once(self, name: str) -> bool:
        """name 을 보낸다 — 방금 보냈으면(RETRY_S 안) 건너뛴다. 보냈으면 True."""
        now = time.monotonic()
        if now - self._sent.get(name, -1e9) < self.RETRY_S:
            return False
        self._sent[name] = now
        self._failed = ""
        self._send(name)
        return True

    def _modes_ready(self, tail: str) -> bool:
        """전원 ON · 브레이크 해제 전에 원격 모드 · 자동 모드인가. 아니면 맞추거나 기다리고 False."""
        if self._control != CONTROL_REMOTE:
            if self._send_once("remote_control_on"):
                self.activity.emit("로봇 부팅 — 원격 모드를 켭니다(remoteControl -on).")
            self.dialog.show_state("로봇 부팅 중", "원격 모드로 바꾸는 중입니다 — 원격 모드여야 RCS 명령(30001)을 받습니다."
                                   + tail)
            return False
        if self._operation == OPERATION_MANUAL:
            self.dialog.show_state("로봇 부팅 중 — 자동 모드 필요",
                                   "로봇이 수동 모드입니다 — 펜던트에서 자동 모드로 바꾸세요(자동 모드여야 안전정지 · "
                                   "안전 리셋이 먹습니다). 바뀌면 이어서 전원 ON · 브레이크 해제를 합니다." + tail)
            return False
        return True

    def _refresh(self) -> None:
        if not self._active:
            return
        elapsed = self._elapsed()
        failed = getattr(self, "_failed", "")
        tail = f"\n\n⚠ {failed}" if failed else ""
        if elapsed > self.WARN_S:
            tail += (f"\n\n{int(self.WARN_S // 60)}분 넘게 걸리고 있습니다 — 로봇 컨트롤러 전원 · 랜 케이블 · "
                     "연결 설정의 로봇 IP 를 확인하세요.")
        if not self._connected:
            self.dialog.show_state("로봇 부팅 중", f"로봇 컨트롤러 부팅을 기다립니다 — {elapsed} 초 경과.\n"
                                   "부팅이 끝나 연결되면 전원 ON · 브레이크 해제까지 자동으로 합니다." + tail)
            return
        mode = self._mode
        if mode is None or mode in (0, MODE_BOOTING):
            self.dialog.show_state("로봇 부팅 중", f"연결됐습니다 — 컨트롤러가 준비되기를 기다립니다({elapsed} 초)." + tail)
        elif mode == MODE_CONFIRM_SAFETY:
            self.dialog.show_state("로봇 부팅 중", "안전 설정 확인이 필요합니다 — 펜던트에서 안전 설정을 확인하세요. "
                                   "확인되면 이어서 자동으로 진행합니다." + tail)
        elif mode in (MODE_POWER_OFF, MODE_IDLE) and not self._modes_ready(tail):
            return
        elif mode == MODE_POWER_OFF:
            if self._send_once("power_on"):
                self.activity.emit("로봇 부팅 — 전원 ON 을 보냈습니다.")
            self.dialog.show_state("로봇 부팅 중", "전원을 켜는 중입니다(전원 ON)." + tail)
        elif mode == MODE_POWER_ON:
            self.dialog.show_state("로봇 부팅 중", "전원이 들어오는 중입니다 — 끝나면 브레이크를 풉니다." + tail)
        elif mode == MODE_IDLE:
            if self._send_once("brake_release"):
                self.activity.emit("로봇 부팅 — 브레이크 해제를 보냈습니다.")
            self.dialog.show_state("로봇 부팅 중", "브레이크를 푸는 중입니다(브레이크 해제)." + tail)
        elif mode == MODE_RUNNING:
            self._done()
        else:
            self.dialog.show_state("로봇 부팅 중", f"로봇 모드 {mode} — 기다립니다." + tail)

    def _done(self) -> None:
        self._active = False
        self._timer.stop()
        self.dialog.show_state("로봇 부팅 완료", "로봇 부팅 완료 — 전원 ON · 브레이크 해제가 끝났습니다.")
        self.dialog.close_button.setEnabled(False)
        self.activity.emit("로봇 부팅 완료 — 전원 ON · 브레이크 해제가 끝났습니다.")
        QTimer.singleShot(self.DONE_CLOSE_MS, self.dialog.finish)
        self.finished.emit(True)
