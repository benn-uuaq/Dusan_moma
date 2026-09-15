"""차량(AMR·리프트·아웃트리거) 제어 노드와 이야기하는 Qt 서비스.

차량 담당자가 준 인터페이스(ROS 패키지 `vehicle_interfaces`)를 쓴다.

  구독   <ns>/robot_status    RobotStatus   상태 (주기 발행)
  서비스 <ns>/robot_control   RobotControl  출발·일시정지·정지·리셋·작업 취소
         <ns>/set_job         SetJob        이동 작업 설정
         <ns>/manual_command  ManualCommand 수동 조그·아웃트리거·리프트

<ns> 는 `SMR_VEHICLE_NS` 환경변수(기본 "vehicle"). 실제 이름은 차량 담당자와
맞춰야 한다 — 받은 자료에 토픽·서비스 이름이 없었다.

로봇 쪽 `RosStatusClient` 와 **같은 노드·실행기**에 붙는다(add_extension).
콜백은 ROS 스레드에서 오므로 값은 Qt 시그널로만 내보낸다.
ROS 나 vehicle_interfaces 가 없으면 조용히 꺼진 채로 둔다(화면은 그대로 뜬다).
"""

from __future__ import annotations

import os
import time
from typing import Any

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

try:
    from vehicle_interfaces.msg import RobotStatus
    from vehicle_interfaces.srv import ManualCommand, RobotControl, SetJob

    VEHICLE_AVAILABLE = True
except ImportError:  # pragma: no cover - 워크스페이스를 소싱하지 않은 환경
    VEHICLE_AVAILABLE = False

#: ManualCommand 의 필드 기본값. 보내지 않은 필드는 "명령 없음"이다.
MANUAL_FIELDS: dict[str, Any] = {
    "cmd_mv_fwd": False, "cmd_mv_rear": False,
    "cmd_trn_left": False, "cmd_trl_right": False,   # 받은 자료의 이름 그대로
    "cmd_outrg_set": 0, "cmd_man_outrg1": 0, "cmd_man_outrg2": 0, "cmd_man_outrg3": 0,
    "cmd_mv_lift": 0, "cmd_init": 0, "lift_height": 0.0,
}


def status_to_dict(msg) -> dict[str, Any]:
    """RobotStatus -> 사전. 단위는 받은 그대로(m, m/s)."""
    return {
        "state": str(msg.state), "hold": str(msg.hold),
        "error_code": int(msg.error_code), "error_msg": str(msg.error_msg),
        "job_id": str(msg.job_id),
        "set_dist": float(msg.set_dist), "mv_dist": float(msg.mv_dist),
        "speed": float(msg.speed),
        "sen1_dist": float(msg.sen1_dist), "sen2_dist": float(msg.sen2_dist),
        "x": float(msg.position.x), "y": float(msg.position.y), "z": float(msg.position.z),
        "roll": float(msg.position.roll), "pitch": float(msg.position.pitch),
        "yaw": float(msg.position.yaw),
        "lift_h": float(msg.lift_h),
    }


class VehicleClient(QObject):
    """차량 상태를 받아 시그널로 내고, 명령 서비스를 부른다."""

    #: 상태 한 건(사전). 주기 발행이라 자주 온다.
    status_changed = pyqtSignal(dict)
    #: 상태가 들어오는지(STALE_S 안에 받았는지). 바뀔 때만 나간다.
    online_changed = pyqtSignal(bool)
    #: (명령 이름, 접수 성공, 문구) — "set_job" / "robot_control" / "manual_command"
    command_result = pyqtSignal(str, bool, str)

    STALE_S = 2.0

    def __init__(self, ros_client=None, namespace: str | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.namespace = (namespace or os.environ.get("SMR_VEHICLE_NS", "vehicle")).strip("/")
        self._node = None
        self._clients: dict[str, Any] = {}
        self._last: dict[str, Any] = {}
        self._last_time = 0.0
        self._online = False
        self._watch = QTimer(self)
        self._watch.setInterval(500)
        self._watch.timeout.connect(self._check_online)
        self._watch.start()
        if ros_client is not None and VEHICLE_AVAILABLE:
            ros_client.add_extension(self._attach)

    @property
    def available(self) -> bool:
        """ROS 노드에 붙어 명령을 보낼 수 있는가."""
        return self._node is not None

    @property
    def online(self) -> bool:
        return self._online

    @property
    def last_status(self) -> dict[str, Any]:
        return dict(self._last)

    def topic(self, name: str) -> str:
        return f"{self.namespace}/{name}" if self.namespace else name

    # ------------------------------------------------------------ ROS 쪽
    def _attach(self, node) -> None:
        self._node = node
        node.create_subscription(RobotStatus, self.topic("robot_status"), self._on_status, 10)
        self._clients = {
            "robot_control": node.create_client(RobotControl, self.topic("robot_control")),
            "set_job": node.create_client(SetJob, self.topic("set_job")),
            "manual_command": node.create_client(ManualCommand, self.topic("manual_command")),
        }

    def _on_status(self, msg) -> None:
        self._last = status_to_dict(msg)
        self._last_time = time.monotonic()
        self.status_changed.emit(dict(self._last))

    def _check_online(self) -> None:
        online = self._last_time > 0 and time.monotonic() - self._last_time < self.STALE_S
        if online != self._online:
            self._online = online
            self.online_changed.emit(online)

    def _call(self, name: str, request) -> bool:
        client = self._clients.get(name)
        if client is None:
            self.command_result.emit(name, False, "차량 인터페이스를 쓸 수 없습니다(ROS·vehicle_interfaces 확인)")
            return False
        if not client.service_is_ready():
            self.command_result.emit(name, False, f"차량 서비스가 없습니다: {self.topic(name)}")
            return False
        future = client.call_async(request)
        future.add_done_callback(lambda f, n=name: self._on_done(n, f))
        return True

    def _on_done(self, name: str, future) -> None:
        try:
            res = future.result()
            self.command_result.emit(name, bool(res.success), str(res.message))
        except Exception as exc:  # noqa: BLE001
            self.command_result.emit(name, False, f"응답 실패: {exc}")

    # ------------------------------------------------------------ 명령
    def set_job(self, job_id: str, set_dist: float, set_speed: float,
                offset_dist: float = 0.0, offset_height: float = 0.0,
                total_distance: float = 0.0, total_height: float = 0.0) -> bool:
        """이동 작업 설정. 거리 m, 속도 m/s."""
        if not VEHICLE_AVAILABLE:
            return self._call("set_job", None)
        req = SetJob.Request()
        req.job_id = str(job_id)
        req.set_dist = float(set_dist)
        req.set_speed = float(set_speed)
        req.offset_dist = float(offset_dist)
        req.offset_height = float(offset_height)
        req.total_distance = float(total_distance)
        req.total_height = float(total_height)
        return self._call("set_job", req)

    def control(self, state: str = "", reset: bool = False, job_cancel: bool = False) -> bool:
        """출발("RUNNING")·일시정지("PAUSED")·정지("STOP")·리셋·작업 취소."""
        if not VEHICLE_AVAILABLE:
            return self._call("robot_control", None)
        req = RobotControl.Request()
        req.state = str(state)
        req.reset = 1 if reset else 0
        req.job_cancel = 1 if job_cancel else 0
        return self._call("robot_control", req)

    def manual(self, **fields: Any) -> bool:
        """수동 명령. 넘기지 않은 필드는 '명령 없음'(0/False)으로 보낸다."""
        unknown = set(fields) - set(MANUAL_FIELDS)
        if unknown:
            raise ValueError(f"ManualCommand 에 없는 필드: {sorted(unknown)}")
        if not VEHICLE_AVAILABLE:
            return self._call("manual_command", None)
        req = ManualCommand.Request()
        for key, default in MANUAL_FIELDS.items():
            setattr(req, key, type(default)(fields.get(key, default)))
        return self._call("manual_command", req)
