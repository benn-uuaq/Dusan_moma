"""ROS 2 상태 토픽을 구독해 화면에 전달하는 Qt 서비스.

`elite_robot_controller`의 `robot_control_node`가 발행하는 자세 토픽을 받는다.
구독 콜백은 ROS 실행기 스레드에서 호출되므로 값은 Qt 시그널로만 내보낸다.
시그널은 큐 연결로 GUI 스레드에서 처리된다.

ROS가 없는 환경에서도 UI가 실행되어야 하므로 rclpy는 선택 의존성으로 다룬다.
"""

from __future__ import annotations

import threading

from PyQt6.QtCore import QObject, pyqtSignal

try:  # ROS가 설치되지 않은 환경에서도 화면은 그대로 동작해야 한다.
    import rclpy
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node
    from std_msgs.msg import (
        Bool, Float32MultiArray, Int32, Int32MultiArray, String,
    )
    from std_srvs.srv import Trigger
    from rcl_interfaces.srv import SetParameters
    from rcl_interfaces.msg import Parameter, ParameterValue, ParameterType

    ROS_AVAILABLE = True
except ImportError:  # pragma: no cover - ROS 미설치 환경에서만 실행된다.
    ROS_AVAILABLE = False

try:
    # 레지스터 맵은 ROS 패키지가 갖고 있다. 워크스페이스를 소싱하면
    # 그대로 가져다 쓸 수 있어 주소 정의를 UI에 복제하지 않는다.
    from elite_robot_controller import register_map

    REGISTER_MAP_AVAILABLE = True
except ImportError:  # pragma: no cover - ROS 워크스페이스 미소싱 환경.
    REGISTER_MAP_AVAILABLE = False


class RosTopics:
    """robot_control_node가 발행하는 Topic 이름."""

    ROBOT_MODE = "robot/status/robot_mode"
    CONTROL_METHOD = "robot/status/control_method"
    OPERATION_MODE = "robot/status/operation_mode"
    TCP_POSE = "robot/status/tcp_pose"
    TCP_POSE_ZERO = "robot/status/tcp_pose_zero"
    JOINT_POSITION = "robot/status/joint_position"
    ALARMS = "robot/status/alarms"
    CONNECTED = "robot/status/connected"
    SCAN_STATE = "robot/status/scan_state"
    TASK_STATE = "robot/status/task_state"
    SPEED_SCALE = "robot/status/speed_scale"

    DASHBOARD = "robot/dashboard"

    LINEAR_SPEED = "robot/command/linear_speed"
    SPEED_RATIO = "robot/command/speed_ratio"
    HOME_JOINT = "robot/command/home_joint"
    START_POSE = "robot/command/start_pose"
    WORK_AREA = "robot/command/work_area"
    JOG_JOINT = "robot/command/jog_joint"
    # 스캔 시작 허가(레지스터 267). 로봇이 원점에서 기다리는 것을 푼다.
    SCAN_GO = "robot/command/scan_go"
    JOG_TCP = "robot/command/jog_tcp"

    MOVE_HOME = "robot/command/move_home"


# 레지스터 원값을 운영자가 읽을 수 있는 문구로 바꾼다. 값 구분은
# ws_elt의 robot_gui_dashboard가 쓰던 것을 그대로 따르되, 콘솔 폭이
# 1280 px로 고정되어 있어 상태 행에서 잘리지 않도록 문구를 줄였다.
#: 태스크 상태(레지스터 500). 로봇 컨트롤러가 그대로 준다.
TASK_STATE_NAMES = {1: "실행 중", 2: "일시 중지", 3: "중지됨"}

ROBOT_MODE_NAMES = {
    0: "DISCONNECTED", 1: "CONFIRM_SAFETY", 2: "BOOTING", 3: "POWER_OFF",
    4: "POWER_ON", 5: "IDLE", 6: "BACKDRIVE", 7: "RUNNING",
    8: "UPDATING_FW", 9: "WAIT_CALIB",
}
CONTROL_METHOD_NAMES = {
    0: "원격 미개방", 1: "로컬 제어", 2: "원격 제어",
}
OPERATION_MODE_NAMES = {
    -1: "지정 안 됨", 0: "자동", 1: "수동",
}

# 로봇 컨트롤러가 받는 속도 비율 범위. 이 밖의 값(특히 0)은 "값 없음"으로 본다.
SPEED_SCALE_MIN, SPEED_SCALE_MAX = 2, 100


class RosStatusClient(QObject):
    """자세 토픽을 구독하고 수신값을 Qt 시그널로 전달한다."""

    # [X, Y, Z, Rx, Ry, Rz] 순서의 6개 값을 그대로 전달한다.
    tcp_pose_changed = pyqtSignal(list)
    tcp_pose_zero_changed = pyqtSignal(list)
    joint_position_changed = pyqtSignal(list)
    # 상태는 (원값, 표시 문구)로 함께 전달해 화면이 다시 해석하지 않게 한다.
    robot_mode_changed = pyqtSignal(int, str)
    control_method_changed = pyqtSignal(int, str)
    operation_mode_changed = pyqtSignal(int, str)
    alarm_received = pyqtSignal(str)
    connected_changed = pyqtSignal(bool)
    command_result = pyqtSignal(str, bool, str)
    error_occurred = pyqtSignal(str)
    # 로봇 태스크의 스캔 진행 상태(레지스터 290~299)를 정수 그대로 전달한다.
    # (state, row_idx, rows, alive, zero_ok, finished, pitch, ..., probe_error)
    # 순서다. probe_error(마지막 값)는 app.py `_handle_probe_error`가 본다.
    scan_state_changed = pyqtSignal(list)
    # 로봇 컨트롤러가 주는 태스크 상태(레지스터 500). 1 = 실행 중.
    task_state_changed = pyqtSignal(int)
    # 로봇이 실제로 쓰고 있는 속도 비율[%]. 펜던트에서 바꿔도 여기로 온다.
    speed_scale_changed = pyqtSignal(int)

    POSE_LENGTH = 6
    # 290~298 중 격자 순회에 필요한 항목의 위치.
    SCAN_STATE_INDEX = 0
    SCAN_FINISHED_INDEX = 5
    # 한 셀의 ㄹ자 스캔이 끝났을 때 로봇이 쓰는 상태 값(dus_finish.script).
    SCAN_STATE_DONE = 5

    # 한 번짜리 명령: (서비스 이름, 필요한 쓰기 레지스터).
    # 레지스터가 None이면 Modbus를 쓰지 않는 명령이라 주소 확인이 필요 없다.
    # 대시보드 명령은 29999 소켓으로 나가므로 여기에 해당한다.
    # 로봇 제어 노드 이름. 파라미터 서비스 주소를 만드는 데 쓴다.
    ROBOT_NODE_NAME = "/robot_control_node"

    COMMAND_SERVICES = {
        "connect": (f"{RosTopics.DASHBOARD}/connect", None),
        "disconnect": (f"{RosTopics.DASHBOARD}/disconnect", None),
        "power_on": (f"{RosTopics.DASHBOARD}/power_on", None),
        "power_off": (f"{RosTopics.DASHBOARD}/power_off", None),
        "brake_release": (f"{RosTopics.DASHBOARD}/brake_release", None),
        "play": (f"{RosTopics.DASHBOARD}/play", None),
        # play/stop 은 원격 제어 모드에서만 먹는다.
        "remote_control_on": (f"{RosTopics.DASHBOARD}/remote_control_on", None),
        "pause": (f"{RosTopics.DASHBOARD}/pause", None),
        "stop": (f"{RosTopics.DASHBOARD}/stop", None),
        "home": (RosTopics.MOVE_HOME, None),
        # 태스크는 펜던트/로봇 쪽에서 고정이라 여기서 고르지 않는다 —
        # 지금 뭐가 올라가 있는지만 29999 "task -s"로 물어 보여준다.
        "task_status": (f"{RosTopics.DASHBOARD}/task_status", None),
    }

    def __init__(self, node_name: str = "smr_operator_ui", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._node_name = node_name
        self._node = None
        self._executor = None
        self._thread: threading.Thread | None = None
        self._owns_context = False
        self._publishers: dict[str, object] = {}
        self._pose_publishers: dict[str, object] = {}
        self._clients: dict[str, object] = {}
        self._registers = None
        # 연결 상태는 10 Hz 로 계속 오므로 바뀔 때만 알리려고 직전 값을 둔다.
        self._connected_last: bool | None = None
        # 속도 비율도 같은 이유로 직전 값을 들고 있는다.
        self._speed_scale_last: int | None = None
        if REGISTER_MAP_AVAILABLE:
            try:
                self._registers = register_map.load()
            except Exception:  # pragma: no cover - 설정 파일이 없을 때만 발생한다.
                self._registers = None

    def writable(self, name: str) -> bool:
        """레지스터 주소가 정해져 실제로 보낼 수 있는 명령인지 알려준다.

        주소를 모르면 UI에서 버튼을 잠가 엉뚱한 레지스터에 쓰지 않게 한다.
        """
        if self._registers is None:
            return False
        return self._registers.write_entry(name).available

    @property
    def available(self) -> bool:
        """rclpy를 불러올 수 있는지 알려준다."""
        return ROS_AVAILABLE

    def start(self) -> None:
        """ROS 노드를 만들고 별도 스레드에서 구독을 시작한다."""
        if not ROS_AVAILABLE:
            self.error_occurred.emit(
                "ROS 2를 사용할 수 없어 로봇 자세를 수신하지 않습니다."
            )
            return
        if self._node is not None:
            return

        try:
            if not rclpy.ok():
                rclpy.init(args=None)
                self._owns_context = True
            self._node = Node(self._node_name)
            self._node.create_subscription(
                Float32MultiArray, RosTopics.TCP_POSE, self._on_tcp_pose, 10
            )
            self._node.create_subscription(
                Float32MultiArray, RosTopics.TCP_POSE_ZERO, self._on_tcp_pose_zero, 10
            )
            self._node.create_subscription(
                Float32MultiArray, RosTopics.JOINT_POSITION, self._on_joint_position, 10
            )
            self._node.create_subscription(
                Int32, RosTopics.ROBOT_MODE, self._on_robot_mode, 10
            )
            self._node.create_subscription(
                Int32, RosTopics.CONTROL_METHOD, self._on_control_method, 10
            )
            self._node.create_subscription(
                Int32, RosTopics.OPERATION_MODE, self._on_operation_mode, 10
            )
            self._node.create_subscription(
                String, RosTopics.ALARMS, self._on_alarm, 10
            )
            self._node.create_subscription(
                Bool, RosTopics.CONNECTED, self._on_connected, 10
            )
            self._node.create_subscription(
                Int32MultiArray, RosTopics.SCAN_STATE, self._on_scan_state, 10
            )
            self._node.create_subscription(
                Int32, RosTopics.SPEED_SCALE, self._on_speed_scale, 10
            )
            self._node.create_subscription(
                Int32, RosTopics.TASK_STATE, self._on_task_state, 10
            )
            self._publishers = {
                "linear_speed": self._node.create_publisher(Int32, RosTopics.LINEAR_SPEED, 10),
                "speed_ratio": self._node.create_publisher(Int32, RosTopics.SPEED_RATIO, 10),
                "jog_joint": self._node.create_publisher(Int32, RosTopics.JOG_JOINT, 10),
                "jog_tcp": self._node.create_publisher(Int32, RosTopics.JOG_TCP, 10),
                "scan_go": self._node.create_publisher(Int32, RosTopics.SCAN_GO, 10),
            }
            self._pose_publishers = {
                "home_joint": self._node.create_publisher(
                    Float32MultiArray, RosTopics.HOME_JOINT, 10),
                "start_pose": self._node.create_publisher(
                    Float32MultiArray, RosTopics.START_POSE, 10),
                "work_area": self._node.create_publisher(
                    Float32MultiArray, RosTopics.WORK_AREA, 10),
            }
            self._clients = {
                key: self._node.create_client(Trigger, service)
                for key, (service, _) in self.COMMAND_SERVICES.items()
            }
            # 로봇 주소는 UI가 정한다 — 연결 직전에 제어 노드의 robot_ip
            # 파라미터를 이 값으로 바꾼 뒤 connect 를 부른다. 표준 파라미터
            # 서비스라 메시지를 새로 정의할 필요가 없다.
            self._param_client = self._node.create_client(
                SetParameters, f"{self.ROBOT_NODE_NAME}/set_parameters")
            self._executor = SingleThreadedExecutor()
            self._executor.add_node(self._node)
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        except Exception as exc:
            self._teardown()
            self.error_occurred.emit(f"ROS 2 연결 시작 실패: {exc}")

    def stop(self) -> None:
        """구독을 끝내고 실행기와 노드를 정리한다."""
        executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            # 실행기를 내린 뒤에도 spin이 남아 있으면 창이 닫히지 않는다.
            thread.join(timeout=2.0)
        self._teardown()

    def _teardown(self) -> None:
        """노드와 컨텍스트를 정리한다. 우리가 만든 컨텍스트만 종료한다."""
        node, self._node = self._node, None
        if node is not None:
            try:
                node.destroy_node()
            except Exception:  # pragma: no cover - 종료 중 오류는 무시한다.
                pass
        if self._owns_context:
            self._owns_context = False
            try:
                rclpy.shutdown()
            except Exception:  # pragma: no cover - 이미 종료된 경우가 있다.
                pass

    def _spin(self) -> None:
        """ROS 실행기를 돌린다. 종료 시 발생하는 예외는 알리지 않는다."""
        executor = self._executor
        try:
            if executor is not None:
                executor.spin()
        except Exception:  # pragma: no cover - stop() 중 실행기 해제로 발생한다.
            pass

    def send_value(self, name: str, value: int) -> bool:
        """작업 속도나 조그처럼 값이 있는 명령을 토픽으로 보낸다."""
        publisher = self._publishers.get(name)
        if publisher is None:
            self.command_result.emit(name, False, "ROS 2에 연결되어 있지 않습니다.")
            return False
        if not self.writable(name):
            self.command_result.emit(name, False, "Modbus 주소가 설정되지 않았습니다.")
            return False
        publisher.publish(Int32(data=int(value)))
        self.command_result.emit(name, True, f"{name} 전송: {value}")
        return True

    def send_pose(self, name: str, values: list) -> bool:
        """기준 위치 6개 성분을 로봇 레지스터에 쓰도록 보낸다."""
        publisher = self._pose_publishers.get(name)
        if publisher is None:
            self.command_result.emit(name, False, "ROS 2에 연결되어 있지 않습니다.")
            return False
        if not self.writable(name):
            self.command_result.emit(name, False, "Modbus 주소가 설정되지 않았습니다.")
            return False
        publisher.publish(Float32MultiArray(data=[float(v) for v in values]))
        self.command_result.emit(name, True, f"{name} 전송 완료")
        return True

    def send_jog(self, kind: str, axis: int, direction: int) -> bool:
        """조그 명령을 보낸다. 부호가 방향, 절댓값이 축 번호(1~6)다.

        0은 정지를 뜻하며 노드가 29999 stop으로 즉시 멈춘다.
        """
        code = 0 if direction == 0 else (axis + 1) * (1 if direction > 0 else -1)
        publisher = self._publishers.get(f"jog_{kind}")
        if publisher is None:
            return False
        publisher.publish(Int32(data=code))
        return True

    def node_is_running(self) -> bool:
        """로봇 제어 노드가 이미 떠 있는지 본다.

        노드가 제공하는 서비스가 잡히면 살아 있는 것이다. UI 가 노드를
        또 띄우지 않도록(같은 로봇에 두 노드가 붙으면 Modbus 소켓을 서로
        뺏는다) 이 값으로 판단한다.
        """
        client = self._clients.get("connect")
        if client is None:
            return False
        try:
            return bool(client.service_is_ready())
        except Exception:      # pragma: no cover - 통신 예외 경로.
            return False

    def set_robot_endpoint(self, ip: str, port: int | None = None) -> bool:
        """제어 노드가 붙을 로봇 주소를 바꾼다.

        노드는 connect 할 때마다 이 파라미터를 다시 읽으므로, 이걸 부른 뒤
        `call_command("connect")` 를 하면 새 주소로 붙는다. 응답을 기다리지
        않는 것은 다른 명령과 같은 이유다(GUI 스레드를 막지 않는다).
        """
        client = getattr(self, "_param_client", None)
        if client is None or not client.service_is_ready():
            return False
        parameters = [Parameter(
            name="robot_ip",
            value=ParameterValue(type=ParameterType.PARAMETER_STRING, string_value=str(ip)),
        )]
        if port:
            parameters.append(Parameter(
                name="modbus_port",
                value=ParameterValue(
                    type=ParameterType.PARAMETER_INTEGER, integer_value=int(port)),
            ))
        request = SetParameters.Request()
        request.parameters = parameters
        client.call_async(request)
        return True

    def call_command(self, name: str) -> bool:
        """위치 저장이나 홈 이동처럼 값이 없는 명령을 서비스로 호출한다.

        응답은 기다리지 않고 콜백에서 `command_result`로 전달한다. GUI
        스레드를 막지 않기 위해서다.
        """
        entry = self.COMMAND_SERVICES.get(name)
        client = self._clients.get(name)
        if entry is None or client is None:
            self.command_result.emit(name, False, "ROS 2에 연결되어 있지 않습니다.")
            return False
        register = entry[1]
        if register is not None and not self.writable(register):
            self.command_result.emit(name, False, "Modbus 주소가 설정되지 않았습니다.")
            return False
        if not client.service_is_ready():
            self.command_result.emit(name, False, "로봇 제어 노드가 응답하지 않습니다.")
            return False

        future = client.call_async(Trigger.Request())
        future.add_done_callback(lambda done: self._on_command_done(name, done))
        return True

    def _on_command_done(self, name: str, future) -> None:
        """서비스 응답을 화면이 쓸 수 있는 형태로 전달한다."""
        try:
            response = future.result()
        except Exception as exc:  # pragma: no cover - 통신 예외 경로.
            self.command_result.emit(name, False, f"명령 실패: {exc}")
            return
        self.command_result.emit(name, bool(response.success), str(response.message))

    def _on_tcp_pose(self, msg) -> None:
        self._emit_pose(msg, self.tcp_pose_changed)

    def _on_tcp_pose_zero(self, msg) -> None:
        self._emit_pose(msg, self.tcp_pose_zero_changed)

    def _on_joint_position(self, msg) -> None:
        self._emit_pose(msg, self.joint_position_changed)

    def _on_robot_mode(self, msg) -> None:
        self._emit_code(msg, ROBOT_MODE_NAMES, self.robot_mode_changed)

    def _on_control_method(self, msg) -> None:
        self._emit_code(msg, CONTROL_METHOD_NAMES, self.control_method_changed)

    def _on_operation_mode(self, msg) -> None:
        self._emit_code(msg, OPERATION_MODE_NAMES, self.operation_mode_changed)

    def _on_connected(self, msg) -> None:
        """연결 상태가 **바뀔 때만** 알린다.

        노드는 늦게 구독한 쪽도 값을 받도록 이 토픽을 10 Hz 로 계속 발행한다.
        그대로 흘려보내면 `_restore_robot_settings()` 같은 구독자가 초당 열 번
        저장값을 다시 밀어 넣어, 운영자가 방금 바꾼 속도를 곧바로 덮어쓴다.
        첫 수신은 이전 값이 없으므로 그대로 알린다.
        """
        connected = bool(msg.data)
        if connected == self._connected_last:
            return
        self._connected_last = connected
        self.connected_changed.emit(connected)

    def _on_alarm(self, msg) -> None:
        text = str(msg.data).strip()
        if text:
            self.alarm_received.emit(text)

    def _on_speed_scale(self, msg) -> None:
        """로봇 속도 비율을 전달한다. **범위 밖이면 버리고, 바뀔 때만 알린다.**

        노드는 이 값을 매 주기(10 Hz) 발행하므로 그대로 흘려보내면 같은 값이
        초당 수십 번 화면을 때린다. 그리고 로봇이 아직 값을 못 준 상태에서는
        `0` 이 오는데(파이썬 시뮬레이터가 그렇다), 0 을 그대로 쓰면 화면이
        하한인 2 % 로 눌려 "2 % ↔ 100 %" 를 오가는 것처럼 보인다.
        0 은 "속도가 2 %" 가 아니라 **값 없음**이므로 버리는 게 맞다.
        """
        percent = int(msg.data)
        if not SPEED_SCALE_MIN <= percent <= SPEED_SCALE_MAX:
            return
        if percent == self._speed_scale_last:
            return
        self._speed_scale_last = percent
        self.speed_scale_changed.emit(percent)

    def _on_task_state(self, msg) -> None:
        self.task_state_changed.emit(int(msg.data))

    def _on_scan_state(self, msg) -> None:
        """스캔 진행 상태를 정수 목록 그대로 전달한다.

        길이는 검사하지 않는다. 뒤쪽 항목(진행률 등)은 아직 쓰지 않으므로,
        레지스터 개수가 달라져도 앞쪽 상태만 읽으면 순회는 계속 동작한다.
        """
        self.scan_state_changed.emit([int(value) for value in msg.data])

    @staticmethod
    def _emit_code(msg, names: dict[int, str], signal) -> None:
        """레지스터 값과 그에 대응하는 문구를 함께 전달한다.

        정의에 없는 값도 버리지 않는다. 운영자가 원값을 보고 판단할 수
        있어야 하므로 알 수 없음으로 표시한다.
        """
        code = int(msg.data)
        signal.emit(code, names.get(code, f"알 수 없음 ({code})"))

    def _emit_pose(self, msg, signal) -> None:
        """길이가 맞는 자세만 전달해 화면에 일부 값만 반영되는 일을 막는다."""
        values = [float(value) for value in msg.data]
        if len(values) != self.POSE_LENGTH:
            self.error_occurred.emit(
                f"자세 값 개수가 {self.POSE_LENGTH}개가 아닙니다: {len(values)}개"
            )
            return
        signal.emit(values)
