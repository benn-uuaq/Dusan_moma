"""차량(AMR·리프트·아웃트리거) 제어 노드와 이야기하는 Qt 서비스.

차량 담당자가 준 인터페이스(ROS 패키지 `vehicle_interfaces`)를 쓴다. 이름 · 규칙은 AGV 담당이 만든 제어
노드(src/steer_agv — QUAD_STEER_AGV_CONTROLLER, 2026-11-23)와 AGV 문서
`src/steer_agv/1.Documets/시나리오` 에 맞췄다.

  구독   <ns>/robot_status    RobotStatus   상태 (주기 발행)
         <ns>/start_request   std_msgs/Empty 차량 「작업 시작」 버튼 → RCS 계측 시작
         (<ns>/robot_estop    std_msgs/Bool  AGV 가 PLC 비상정지를 낸다 — RCS 는 robot_status.estop 으로 본다)
  서비스 <ns>/robot_control   RobotControl  state RUNNING 출발 · 재개, PAUSE 일시정지(작업 유지), STOP 정지,
                                            FINISH 작업 끝(홀딩 해제 · 아웃트리거 올림), reset, job_cancel
         <ns>/set_job         SetJob        이동 작업 등록(radius = 모재 반지름). job_id 는 노드가 켜진 동안 한 번만
         <ns>/manual_command  ManualCommand 수동 모드에서만 — 조그 · 아웃트리거 전체 고정/해제 · 수동 리프트
         <ns>/lift_control    SetMvLift     리프트 — 자동 모드는 set_speed, 수동 모드는 PLC 속도
         <ns>/measure_lap     MeasureLap    캘리브레이션 한 바퀴 · QR 정렬(AGV 노드는 아직 거절 — LAP 은 SetJob 으로 대신)
         <ns>/reset_odometry  ResetOdometry 차량 위치(x · y · yaw)를 주는 값으로 다시 맞춘다

PLC 연결 상태 필드 · 서비스는 없다 — PLC 통신이 끊기면 차량 노드가 error_code 701(PLC_COMM_LOST)을 내고,
다시 붙은 뒤 reset 을 받아야 푼다.

<ns> 는 `SMR_VEHICLE_NS` 환경변수(기본 "vehicle" — AGV 노드는 /vehicle 로 고정).

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

try:  # 2026-11-17 에 더한 서비스 — 옛 빌드면 없다(그때는 SetJob 한 바퀴로 대신한다).
    from vehicle_interfaces.srv import MeasureLap
except ImportError:  # pragma: no cover
    MeasureLap = None

try:  # 2026-11-19 — 자동 동작 중 리프트. 옛 빌드면 없다(자동 리프트를 못 움직인다).
    from vehicle_interfaces.srv import SetMvLift
except ImportError:  # pragma: no cover
    SetMvLift = None

try:  # 2026-11-20 AGV 판 — 차량 위치 다시 맞추기. 옛 빌드면 없다.
    from vehicle_interfaces.srv import ResetOdometry
except ImportError:  # pragma: no cover
    ResetOdometry = None

#: 리프트 서비스 이름(SetMvLift) — AGV 노드 /vehicle/lift_control(2026-11-23). 수동 화면 리프트는
#: ManualCommand.cmd_mv_lift 로 간다(set_speed 는 자동 모드에서만, 수동 모드는 PLC 속도).
LIFT_SERVICE = "lift_control"

#: PLC 통신 끊김 — 차량 노드가 error_code 로 낸다(RCS_AGV 인터페이스 오류 표). PLC 연결 표시가 이것을 본다.
PLC_COMM_LOST = 701

#: 작업 끝(RobotControl state "FINISH") 결과의 command_result 이름 — 같은 robot_control 서비스지만
#: 출발 · 정지 · 리셋 결과와 섞이지 않게 따로 낸다.
FINISH_RESULT = "finish"

try:
    from std_msgs.msg import Empty
except ImportError:  # pragma: no cover
    Empty = None

#: ManualCommand 의 필드 기본값. 보내지 않은 필드는 "명령 없음"이다.
#: 2026-11-19 — 아웃트리거 개별(cmd_man_outrg1~3)은 뺐다(균형 때문에 전체로만).
#: 수동 리프트 = cmd_mv_lift 1 + lift_height [m] — 속도는 PLC 가 정한다(AGV 판 2026-11-20).
MANUAL_FIELDS: dict[str, Any] = {
    "cmd_mv_fwd": False, "cmd_mv_rear": False,
    "cmd_trn_left": False, "cmd_trn_right": False,   # 받은 자료의 cmd_trl_right 는 오타라 고쳤다(2026-11-17)
    "cmd_outrg_set": 0, "cmd_mv_lift": 0, "cmd_init": 0, "lift_height": 0.0,
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
        # 리프트 상태(2026-11-24, AGV 측과 정함): STOP · RUNNING · PAUSE · FINISH. 옛 빌드 · 안 채우는 AGV 면 "".
        "lift_state": str(getattr(msg, "lift_state", "") or "").strip().upper(),
        **_added_fields(msg),
    }


def _added_fields(msg) -> dict[str, Any]:
    """2026-11-17 에 더한 칸(RCS_AGV_인터페이스). 「모름」 값은 싣지 않는다 — RCS 는 없는 칸을 조건에서 뺀다.

      battery_pct 255 · level_state -1 · standoff_meas < 0 → 모름
    옛 빌드(칸이 없는 메시지)여도 돈다.
    """
    out: dict[str, Any] = {}
    battery = getattr(msg, "battery_pct", 255)
    if 0 <= int(battery) <= 100:
        out["battery_pct"] = int(battery)
        out["charging"] = bool(getattr(msg, "charging", False))
    for name in ("estop", "obstacle_stop"):
        if hasattr(msg, name):
            out[name] = bool(getattr(msg, name))
    level = int(getattr(msg, "level_state", -1))
    if level in (0, 1):
        out["level_ok"] = level == 1
    standoff = float(getattr(msg, "standoff_meas", -1.0))
    if standoff >= 0:
        out["standoff_meas"] = standoff
    if hasattr(msg, "lap_state"):
        out["lap_state"] = str(msg.lap_state or "IDLE")
        out["lap_path_m"] = float(msg.lap_path_m)
        out["lap_origin_ok"] = bool(msg.lap_origin_ok)
        out["lap_error"] = str(msg.lap_error)
    return out


#: AGV 제어 노드(src/steer_agv)의 거절 문구 → 작업자 안내(2026-11-24). 노드는 영어 한 줄로만 답한다 —
#: 「manual mode required」만 보고는 PLC 가 자동 모드라 수동 버튼이 안 먹는 줄 알기 어렵다.
#: PLC 모드(DM001): 0 COBOT 수동(아웃트리거 · 리프트) · 1 주행 수동 · 2 자동.
VEHICLE_REJECT_TEXTS: tuple[tuple[str, str], ...] = (
    ("manual mode required",
     "차량 PLC 가 수동 모드가 아닙니다(DM001 이 0 · 1 이 아님 — 자동이면 2). 차량 조작반에서 "
     "「주행 수동」(1 — 주행) 또는 「COBOT 수동」(0 — 아웃트리거 · 리프트)으로 바꾼 뒤 누르세요"),
    ("drive requires DRIVE Manual mode and released outriggers",
     "수동 주행은 PLC 모드 「주행 수동」(1)이고 아웃트리거가 해제돼 있을 때만 됩니다"),
    ("outrigger requires stationary vehicle and lift", "아웃트리거는 차량 · 리프트가 서 있을 때만 움직입니다"),
    ("opposing manual commands", "반대 방향 명령(전진+후진 · 좌+우)을 함께 보냈습니다"),
    ("invalid manual selector", "수동 명령 값이 올바르지 않습니다"),
    ("DM105 initialize trigger protocol is undefined",
     "원점/초기화는 아직 AGV 쪽 규약이 정해지지 않아 받지 않습니다"),
    ("manual switches are active", "수동 조그가 눌린 동안에는 받지 않습니다"),
    ("PLC writes disabled",
     "AGV 노드가 PLC 읽기 전용으로 떠 있습니다 — 연결 설정 「PLC 명령 쓰기」를 켜고 저장하세요"),
    ("lift requires PLC HOLD", "리프트는 차량이 HOLD(아웃트리거 고정, DM002 = 2)일 때만 움직입니다"),
    ("lift requires stopped and secured vehicle", "리프트는 차량이 서서 아웃트리거가 고정됐을 때만 움직입니다"),
    # 리프트 높이 상한은 차량 설정 「리프트 한계 높이」(RCS 가 AGV 노드 max_lift_height 로 넘긴다) — explain_reject 가
    # 그 값을 mm 로 채운다(입력이 mm 라 안내도 mm, 2026-11-24).
    ("lift height out of range", "리프트 높이가 허용 범위(0 ~ {lift_max} mm)를 벗어났습니다"),
    ("automatic lift speed out of range", "자동 리프트 속도가 허용 범위(0 초과 ~ 200 mm/s)를 벗어났습니다"),
    ("set_job requires idle automatic mode", "주행 작업은 PLC 자동 모드(2)에서 차량이 서 있을 때만 받습니다"),
    ("release outriggers and stop or pause before RUNNING",
     "출발하려면 아웃트리거가 해제되고 차량이 정지(또는 일시정지) 상태여야 합니다"),
    ("FINISH requires stationary automatic mode and idle lift",
     "작업 끝(FINISH)은 자동 모드에서 차량이 서 있고 리프트가 쉬고 있을 때만 됩니다"),
    ("reset cause still active", "리셋할 원인(오류 · 비상정지 · 접근 감지)이 아직 남아 있습니다"),
    ("reset requires stationary state", "리셋은 차량이 서 있을 때만 됩니다"),
    ("busy, missing or duplicate job_id", "작업 번호가 비었거나 이미 쓴 번호입니다(또는 다른 작업 중)"),
    ("QR origin and alignment completion feedback unavailable",
     "QR 정렬(measure_lap)은 아직 AGV 노드가 지원하지 않습니다"),
    ("odometry reset requires a stationary vehicle without a pending drive",
     "위치 맞추기는 차량이 서 있고 남은 주행이 없을 때만 됩니다"),
)


#: 「리프트 한계 높이」를 모를 때 쓰는 값 [mm] — 실제 리프트 한계(2026-11-24).
LIFT_MAX_DEFAULT_MM = 5000


def explain_reject(message: str, lift_max_mm: float | None = None) -> str:
    """AGV 노드 거절 문구를 작업자 안내로. 모르는 문구면 원문 그대로.

    `lift_max_mm` 는 차량 설정 「리프트 한계 높이」 — 리프트 높이 범위 안내에 mm 로 넣는다.
    """
    text = str(message or "")
    limit = float(lift_max_mm) if lift_max_mm and lift_max_mm > 0 else LIFT_MAX_DEFAULT_MM
    for key, korean in VEHICLE_REJECT_TEXTS:
        if key in text:
            return korean.replace("{lift_max}", f"{limit:,.0f}")
    return text


class VehicleClient(QObject):
    """차량 상태를 받아 시그널로 내고, 명령 서비스를 부른다."""

    #: 상태 한 건(사전). 주기 발행이라 자주 온다.
    status_changed = pyqtSignal(dict)
    #: 상태가 들어오는지(STALE_S 안에 받았는지). 바뀔 때만 나간다.
    online_changed = pyqtSignal(bool)
    #: (명령 이름, 접수 성공, 문구) — "set_job" / "robot_control" / "manual_command" / "measure_lap" /
    #: "lift_control" / "reset_odometry" / "finish"(robot_control FINISH)
    command_result = pyqtSignal(str, bool, str)
    #: 차량 「작업 시작」 버튼이 눌렸다(<ns>/start_request) — RCS 가 계측 시작을 돈다.
    start_requested = pyqtSignal()

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
        # set_job 번호 — AGV 노드는 켜진 동안 같은 job_id 를 다시 받지 않는다. RCS 를 다시 켜도 겹치지
        # 않게 켠 시각(일 · 시분초)을 앞에 붙인다: RCS-23153012-0001.
        self._session = time.strftime("%d%H%M%S")
        self._job_count = 0
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

    def status_publishers(self) -> list[str]:
        """차량 상태(robot_status)를 내는 노드 이름들 — 같은 ROS 도메인 안 전부(이 PC · 다른 PC).

        둘 이상이면 RCS 명령이 그 노드들에 다 간다(같은 /vehicle/* 이름). 못 물으면 빈 목록.
        """
        node = self._node
        if node is None:
            return []
        topic = self.topic("robot_status")
        if not topic.startswith("/"):
            topic = "/" + topic
        try:
            infos = node.get_publishers_info_by_topic(topic)
        except Exception:  # noqa: BLE001 — 그래프 조회 실패는 「모름」
            return []
        return [f"{(info.node_namespace or '').rstrip('/')}/{info.node_name}" for info in infos]

    # ------------------------------------------------------------ ROS 쪽
    def _attach(self, node) -> None:
        self._node = node
        node.create_subscription(RobotStatus, self.topic("robot_status"), self._on_status, 10)
        self._clients = {
            "robot_control": node.create_client(RobotControl, self.topic("robot_control")),
            "set_job": node.create_client(SetJob, self.topic("set_job")),
            "manual_command": node.create_client(ManualCommand, self.topic("manual_command")),
        }
        if MeasureLap is not None:
            self._clients["measure_lap"] = node.create_client(MeasureLap, self.topic("measure_lap"))
        if SetMvLift is not None:
            self._clients[LIFT_SERVICE] = node.create_client(SetMvLift, self.topic(LIFT_SERVICE))
        if ResetOdometry is not None:
            self._clients["reset_odometry"] = node.create_client(ResetOdometry, self.topic("reset_odometry"))
        if Empty is not None:
            node.create_subscription(Empty, self.topic("start_request"),
                                     lambda _msg: self.start_requested.emit(), 10)

    def _on_status(self, msg) -> None:
        self._last = status_to_dict(msg)
        self._last_time = time.monotonic()
        self.status_changed.emit(dict(self._last))

    def new_job_id(self, prefix: str = "RCS") -> str:
        """작업 번호 — RCS-<켠 시각>-0001 … (AGV 노드는 같은 job_id 를 두 번 받지 않는다). QR 정렬은 ALN."""
        self._job_count += 1
        return f"{prefix}-{self._session}-{self._job_count:04d}"

    def _check_online(self) -> None:
        online = self._last_time > 0 and time.monotonic() - self._last_time < self.STALE_S
        if online != self._online:
            self._online = online
            self.online_changed.emit(online)

    def _call(self, name: str, request, result_name: str = "") -> bool:
        """서비스 `name` 을 부른다. 결과는 command_result(result_name 또는 name, …)로 낸다."""
        label = result_name or name
        client = self._clients.get(name)
        if client is None:
            self.command_result.emit(label, False, "차량 인터페이스를 쓸 수 없습니다(ROS·vehicle_interfaces 확인)")
            return False
        if not client.service_is_ready():
            self.command_result.emit(label, False, f"차량 서비스가 없습니다: {self.topic(name)}")
            return False
        future = client.call_async(request)
        future.add_done_callback(lambda f, n=label: self._on_done(n, f))
        return True

    def _on_done(self, name: str, future) -> None:
        try:
            res = future.result()
            self.command_result.emit(name, bool(res.success), str(res.message))
        except Exception as exc:  # noqa: BLE001
            self.command_result.emit(name, False, f"응답 실패: {exc}")

    # ------------------------------------------------------------ 명령
    def set_job(self, job_id: str, set_dist: float, set_speed: float,
                standoff: float = 0.0, radius: float = 0.0) -> bool:
        """이동 작업 설정. 거리 m, 속도 m/s, 거리 유지 m, 모재 반지름 m.

        차량은 받은 거리만큼 달리기만 한다 — 격자 · 구역 크기는 RCS 가 관리한다(2026-11-12).
        `standoff` 는 차량이 모재를 돌며 지킬 거리(RCS 차량 설정 「모재 거리 유지」),
        `radius` 는 목표 대상(모재) 반지름 = 검사 대상 바깥 지름 / 2(AGV 판 2026-11-20). 모르면 0.
        """
        if not VEHICLE_AVAILABLE:
            return self._call("set_job", None)
        req = SetJob.Request()
        req.job_id = str(job_id)
        req.set_dist = float(set_dist)
        req.set_speed = float(set_speed)
        req.standoff = float(standoff)
        if hasattr(req, "radius"):            # 옛 빌드(radius 없음)여도 돈다
            req.radius = float(radius)
        return self._call("set_job", req)

    def supports(self, name: str) -> bool:
        """그 서비스를 차량 노드가 지금 내고 있는가(예: measure_lap — 없으면 다른 길로)."""
        client = self._clients.get(name)
        try:
            return client is not None and bool(client.service_is_ready())
        except Exception:  # noqa: BLE001
            return False

    def measure_lap(self, job_id: str, speed: float, standoff: float, expected_dist: float,
                    mode: str = "LAP") -> bool:
        """캘리브레이션 주행(MeasureLap). 속도 m/s, 거리 m. 결과는 상태의 lap_* 로 온다.

        mode: "LAP" 원통 한 바퀴 · "ALIGN" 곡판 — 판 가운데 QR 에 정렬만(expected_dist 0, 2026-11-18).
        """
        if not VEHICLE_AVAILABLE or MeasureLap is None:
            return self._call("measure_lap", None)
        req = MeasureLap.Request()
        req.job_id = str(job_id)
        if hasattr(req, "mode"):
            req.mode = str(mode)
        elif mode != "LAP":
            # 옛 정의(mode 칸 없음)로 빌드된 차량 패키지 — 곡판 정렬을 보낼 수 없다.
            self.command_result.emit("measure_lap", False,
                                     "MeasureLap 에 mode 칸이 없습니다 — vehicle_interfaces 를 다시 빌드하세요")
            return False
        req.speed = float(speed)
        req.standoff = float(standoff)
        req.expected_dist = float(expected_dist)
        return self._call("measure_lap", req)

    def control(self, state: str = "", reset: bool = False, job_cancel: bool = False) -> bool:
        """출발 · 재개("RUNNING") · 일시정지("PAUSE", 작업 유지) · 정지("STOP") · 리셋 · 작업 취소."""
        if not VEHICLE_AVAILABLE:
            return self._call("robot_control", None)
        req = RobotControl.Request()
        req.state = str(state)
        req.reset = 1 if reset else 0
        req.job_cancel = 1 if job_cancel else 0
        return self._call("robot_control", req)

    def finish_job(self) -> bool:
        """그 자리 작업을 끝낸다 — RobotControl(state = "FINISH"). 결과는 command_result("finish", …).

        AGV 문서(시나리오 6. CONTROL — COMMAND 32 FINISH)대로 `string state` 로 보낸다. 따로 칸(job_finish)은 없다.

        차량은 받으면 홀딩을 풀고 아웃트리거를 올려 이동할 수 있는 상태가 된다(state FINISH · hold RELEASE).
        RCS 는 다음 주행 전에, 리프트를 내린 뒤 보낸다(VehicleFinishAdapter).
        """
        if not VEHICLE_AVAILABLE:
            return self._call("robot_control", None, result_name=FINISH_RESULT)
        req = RobotControl.Request()
        req.state = "FINISH"
        return self._call("robot_control", req, result_name=FINISH_RESULT)

    def lift(self, job_id: str, height_m: float, speed_mps: float) -> bool:
        """자동 동작 중 리프트를 목표 높이로(SetMvLift, lift_control). 높이 m(절대, 맨 아래 0) · 속도 m/s.

        도착은 상태의 lift_h 로 본다(VehicleLiftAdapter). 수동 화면 리프트는 manual_lift 를 쓴다.
        """
        if not VEHICLE_AVAILABLE or SetMvLift is None:
            return self._call(LIFT_SERVICE, None)
        req = SetMvLift.Request()
        req.job_id = str(job_id)
        req.set_height = float(height_m)
        req.set_speed = float(speed_mps)
        return self._call(LIFT_SERVICE, req)

    def manual_lift(self, height_m: float) -> bool:
        """수동 화면 리프트 — ManualCommand(cmd_mv_lift 1, lift_height m). 속도는 PLC 가 정한다."""
        return self.manual(cmd_mv_lift=1, lift_height=float(height_m))

    def reset_odometry(self, x: float = 0.0, y: float = 0.0, yaw: float = 0.0) -> bool:
        """차량 위치를 (x m, y m, yaw rad) 로 다시 맞춘다(ResetOdometry). 결과는 command_result 로 온다."""
        if not VEHICLE_AVAILABLE or ResetOdometry is None:
            return self._call("reset_odometry", None)
        req = ResetOdometry.Request()
        req.x, req.y, req.yaw = float(x), float(y), float(yaw)
        return self._call("reset_odometry", req)

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
