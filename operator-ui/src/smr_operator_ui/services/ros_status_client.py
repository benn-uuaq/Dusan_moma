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
    from std_msgs.msg import Float32MultiArray, Int32, String
    from std_srvs.srv import Trigger

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
    ALARMS = "robot/status/alarms"

    LINEAR_SPEED = "robot/command/linear_speed"
    JOG_JOINT = "robot/command/jog_joint"
    JOG_TCP = "robot/command/jog_tcp"

    SAVE_HOME_POSE = "robot/command/save_home_pose"
    SAVE_START_POSE = "robot/command/save_start_pose"
    MOVE_HOME = "robot/command/move_home"


# 레지스터 원값을 운영자가 읽을 수 있는 문구로 바꾼다. 값 구분은
# ws_elt의 robot_gui_dashboard가 쓰던 것을 그대로 따르되, 콘솔 폭이
# 1280 px로 고정되어 있어 상태 행에서 잘리지 않도록 문구를 줄였다.
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


class RosStatusClient(QObject):
    """자세 토픽을 구독하고 수신값을 Qt 시그널로 전달한다."""

    # [X, Y, Z, Rx, Ry, Rz] 순서의 6개 값을 그대로 전달한다.
    tcp_pose_changed = pyqtSignal(list)
    tcp_pose_zero_changed = pyqtSignal(list)
    # 상태는 (원값, 표시 문구)로 함께 전달해 화면이 다시 해석하지 않게 한다.
    robot_mode_changed = pyqtSignal(int, str)
    control_method_changed = pyqtSignal(int, str)
    operation_mode_changed = pyqtSignal(int, str)
    alarm_received = pyqtSignal(str)
    command_result = pyqtSignal(str, bool, str)
    error_occurred = pyqtSignal(str)

    POSE_LENGTH = 6

    # 한 번짜리 명령과 그 서비스 이름, 그리고 레지스터 맵에서 확인할 쓰기 항목.
    COMMAND_SERVICES = {
        "save_home_pose": (RosTopics.SAVE_HOME_POSE, "save_home_pose"),
        "save_start_pose": (RosTopics.SAVE_START_POSE, "save_start_pose"),
        "home": (RosTopics.MOVE_HOME, "move_home"),
    }

    def __init__(self, node_name: str = "smr_operator_ui", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._node_name = node_name
        self._node = None
        self._executor = None
        self._thread: threading.Thread | None = None
        self._owns_context = False
        self._publishers: dict[str, object] = {}
        self._clients: dict[str, object] = {}
        self._registers = None
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
            self._publishers = {
                "linear_speed": self._node.create_publisher(Int32, RosTopics.LINEAR_SPEED, 10),
                "jog_joint": self._node.create_publisher(Int32, RosTopics.JOG_JOINT, 10),
                "jog_tcp": self._node.create_publisher(Int32, RosTopics.JOG_TCP, 10),
            }
            self._clients = {
                key: self._node.create_client(Trigger, service)
                for key, (service, _) in self.COMMAND_SERVICES.items()
            }
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
        if not self.writable(entry[1]):
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

    def _on_robot_mode(self, msg) -> None:
        self._emit_code(msg, ROBOT_MODE_NAMES, self.robot_mode_changed)

    def _on_control_method(self, msg) -> None:
        self._emit_code(msg, CONTROL_METHOD_NAMES, self.control_method_changed)

    def _on_operation_mode(self, msg) -> None:
        self._emit_code(msg, OPERATION_MODE_NAMES, self.operation_mode_changed)

    def _on_alarm(self, msg) -> None:
        text = str(msg.data).strip()
        if text:
            self.alarm_received.emit(text)

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
