"""Windows 단독판용 작은 ROS 대역 — 한 프로세스 안에서 노드끼리 잇는다.

리눅스 RCS 는 화면(RosStatusClient)과 로봇 제어 노드(RobotControlNode)가 ROS 2
토픽 · 서비스 · 파라미터로 이야기한다. Windows 에는 ROS 가 없으므로, 두 쪽 코드를
**고치지 않고** 그대로 쓰도록 같은 이름의 모듈(rclpy · std_msgs · std_srvs ·
rcl_interfaces)을 이 파일이 만들어 `sys.modules` 에 넣는다(`install()`).

흉내 내는 것은 두 쪽이 실제로 쓰는 만큼이다.
  · 토픽: create_publisher / create_subscription — 구독한 노드의 실행기 스레드에서
    콜백이 돈다(실제 ROS 처럼 한 노드의 콜백은 하나씩 차례로 돈다).
  · 서비스: create_service / create_client(service_is_ready · call_async) — 서비스
    콜백은 서비스를 가진 노드의 실행기에서 돌고, 결과는 future 로 돌려준다.
  · 파라미터: declare_parameter / get_parameter, 노드마다 `<노드>/set_parameters`.
  · 타이머: create_timer, 로거: get_logger.
  · 실행기: SingleThreadedExecutor(add_node · spin · spin_once · shutdown), rclpy.spin.
QoS 는 무시한다(한 프로세스 안이라 잃는 메시지가 없다).
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
import time
import types
from typing import Any, Callable

_log = logging.getLogger("liteross")


# ---------------------------------------------------------------- 메시지
class _Message:
    """필드 기본값을 가진 간단한 메시지. 생성자 키워드로 값을 준다."""

    _fields: dict[str, Any] = {}

    def __init__(self, **values: Any) -> None:
        for name, default in self._fields.items():
            setattr(self, name, list(default) if isinstance(default, list) else default)
        for name, value in values.items():
            setattr(self, name, value)

    def __repr__(self) -> str:  # pragma: no cover - 디버깅용
        body = ", ".join(f"{k}={getattr(self, k)!r}" for k in self._fields)
        return f"{type(self).__name__}({body})"


def _message(type_name: str, /, **fields: Any) -> type:
    return type(type_name, (_Message,), {"_fields": fields})


Bool = _message("Bool", data=False)
String = _message("String", data="")
Int32 = _message("Int32", data=0)
Int32MultiArray = _message("Int32MultiArray", data=[])
Float32MultiArray = _message("Float32MultiArray", data=[])


class ParameterType:
    PARAMETER_NOT_SET = 0
    PARAMETER_BOOL = 1
    PARAMETER_INTEGER = 2
    PARAMETER_DOUBLE = 3
    PARAMETER_STRING = 4


ParameterValue = _message("ParameterValue", type=0, bool_value=False, integer_value=0,
                          double_value=0.0, string_value="")
Parameter = _message("Parameter", name="", value=None)
SetParametersResult = _message("SetParametersResult", successful=False, reason="")


class _Service:
    """서비스 형식 — Request · Response 를 가진다."""

    Request: type
    Response: type


class Trigger(_Service):
    Request = _message("TriggerRequest")
    Response = _message("TriggerResponse", success=False, message="")


class SetParameters(_Service):
    Request = _message("SetParametersRequest", parameters=[])
    Response = _message("SetParametersResponse", results=[])


def _to_parameter_value(value: Any) -> Any:
    if isinstance(value, bool):
        return ParameterValue(type=ParameterType.PARAMETER_BOOL, bool_value=value)
    if isinstance(value, int):
        return ParameterValue(type=ParameterType.PARAMETER_INTEGER, integer_value=value)
    if isinstance(value, float):
        return ParameterValue(type=ParameterType.PARAMETER_DOUBLE, double_value=value)
    if isinstance(value, str):
        return ParameterValue(type=ParameterType.PARAMETER_STRING, string_value=value)
    return ParameterValue()


def _from_parameter_value(value: Any) -> Any:
    kind = getattr(value, "type", ParameterType.PARAMETER_NOT_SET)
    return {
        ParameterType.PARAMETER_BOOL: getattr(value, "bool_value", False),
        ParameterType.PARAMETER_INTEGER: getattr(value, "integer_value", 0),
        ParameterType.PARAMETER_DOUBLE: getattr(value, "double_value", 0.0),
        ParameterType.PARAMETER_STRING: getattr(value, "string_value", ""),
    }.get(kind)


# ---------------------------------------------------------------- 버스
def _name(text: str) -> str:
    """'/robot/x' 와 'robot/x' 를 같은 이름으로 본다."""
    return str(text).strip().lstrip("/")


class _Bus:
    """프로세스 하나 안의 토픽 · 서비스 등록부."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.subscriptions: dict[str, list["_Subscription"]] = {}
        self.services: dict[str, "_ServiceServer"] = {}
        self.ok = False

    def reset(self) -> None:
        with self.lock:
            self.subscriptions.clear()
            self.services.clear()


_BUS = _Bus()

#: 노드 이름 → 시작 파라미터. 노드가 declare_parameter 할 때 기본값 대신 쓴다
#: (ros2 run 의 --ros-args -p 와 같다). 단독판이 저장된 로봇 주소로 바로 붙게 한다.
_OVERRIDES: dict[str, dict[str, Any]] = {}


def set_parameter_overrides(node_name: str, values: dict[str, Any]) -> None:
    _OVERRIDES[_name(node_name)] = dict(values)


class _Future:
    def __init__(self) -> None:
        self._done = threading.Event()
        self._result: Any = None
        self._exception: BaseException | None = None
        self._callbacks: list[Callable[["_Future"], None]] = []
        self._lock = threading.Lock()

    def done(self) -> bool:
        return self._done.is_set()

    def result(self) -> Any:
        if self._exception is not None:
            raise self._exception
        return self._result

    def exception(self) -> BaseException | None:
        return self._exception

    def _finish(self, result: Any = None, exception: BaseException | None = None) -> None:
        with self._lock:
            self._result, self._exception = result, exception
            self._done.set()
            callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            _safe(callback, self)

    def add_done_callback(self, callback: Callable[["_Future"], None]) -> None:
        with self._lock:
            if not self._done.is_set():
                self._callbacks.append(callback)
                return
        _safe(callback, self)


def _safe(callback: Callable, *args: Any) -> None:
    try:
        callback(*args)
    except Exception:  # pragma: no cover - 콜백 예외는 알리고 계속 돈다
        _log.exception("콜백 실행 중 예외")


class _Publisher:
    def __init__(self, topic: str) -> None:
        self.topic = _name(topic)

    def publish(self, msg: Any) -> None:
        with _BUS.lock:
            subscriptions = list(_BUS.subscriptions.get(self.topic, ()))
        for subscription in subscriptions:
            subscription.node._post(subscription.callback, msg)

    def destroy(self) -> None:
        pass


class _Subscription:
    def __init__(self, node: "Node", topic: str, callback: Callable) -> None:
        self.node, self.topic, self.callback = node, _name(topic), callback


class _ServiceServer:
    def __init__(self, node: "Node", srv_type: type, name: str, callback: Callable) -> None:
        self.node, self.srv_type, self.name, self.callback = node, srv_type, _name(name), callback

    def _handle(self, request: Any, future: _Future) -> None:
        try:
            response = self.callback(request, self.srv_type.Response())
        except Exception as exc:  # pragma: no cover - 서비스 안의 예외
            _log.exception("서비스 %s 처리 중 예외", self.name)
            future._finish(exception=exc)
            return
        future._finish(result=response)


class _Client:
    def __init__(self, srv_type: type, name: str) -> None:
        self.srv_type, self.name = srv_type, _name(name)

    def _server(self) -> _ServiceServer | None:
        with _BUS.lock:
            return _BUS.services.get(self.name)

    def service_is_ready(self) -> bool:
        return self._server() is not None

    def wait_for_service(self, timeout_sec: float | None = None) -> bool:
        deadline = None if timeout_sec is None else time.monotonic() + timeout_sec
        while not self.service_is_ready():
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(0.05)
        return True

    def call_async(self, request: Any) -> _Future:
        future = _Future()
        server = self._server()
        if server is None:
            future._finish(exception=RuntimeError(f"서비스 없음: {self.name}"))
            return future
        server.node._post(server._handle, request, future)
        return future

    def destroy(self) -> None:
        pass


class _Timer:
    def __init__(self, period: float, callback: Callable) -> None:
        self.period = max(float(period), 0.001)
        self.callback = callback
        self.due = time.monotonic() + self.period
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class _Logger:
    def __init__(self, name: str) -> None:
        self._log = logging.getLogger(name)

    def debug(self, msg: Any, *a: Any, **k: Any) -> None:
        self._log.debug(str(msg))

    def info(self, msg: Any, *a: Any, **k: Any) -> None:
        self._log.info(str(msg))

    def warn(self, msg: Any, *a: Any, **k: Any) -> None:
        self._log.warning(str(msg))

    warning = warn

    def error(self, msg: Any, *a: Any, **k: Any) -> None:
        self._log.error(str(msg))

    fatal = error


class _ParameterHandle:
    def __init__(self, name: str, value: Any) -> None:
        self.name, self.value = name, value

    def get_parameter_value(self) -> Any:
        return _to_parameter_value(self.value)


# ---------------------------------------------------------------- 노드
class Node:
    """rclpy.node.Node 대역. 콜백은 이 노드를 돌리는 실행기 스레드에서 돈다."""

    def __init__(self, node_name: str, **_kwargs: Any) -> None:
        self._node_name = _name(node_name)
        self._inbox: "queue.Queue[tuple]" = queue.Queue()
        self._timers: list[_Timer] = []
        self._params: dict[str, Any] = {}
        self._subscriptions: list[_Subscription] = []
        self._servers: list[_ServiceServer] = []
        self._logger = _Logger(self._node_name)
        self._destroyed = False
        self.create_service(SetParameters, f"{self._node_name}/set_parameters",
                            self._on_set_parameters)

    # ---- 이름 · 로거
    def get_name(self) -> str:
        return self._node_name

    def get_logger(self) -> _Logger:
        return self._logger

    # ---- 토픽
    def create_publisher(self, _msg_type: type, topic: str, _qos: Any = 10, **_k: Any) -> _Publisher:
        return _Publisher(topic)

    def create_subscription(self, _msg_type: type, topic: str, callback: Callable,
                            _qos: Any = 10, **_k: Any) -> _Subscription:
        subscription = _Subscription(self, topic, callback)
        with _BUS.lock:
            _BUS.subscriptions.setdefault(subscription.topic, []).append(subscription)
        self._subscriptions.append(subscription)
        return subscription

    # ---- 서비스
    def create_service(self, srv_type: type, name: str, callback: Callable, **_k: Any) -> _ServiceServer:
        server = _ServiceServer(self, srv_type, name, callback)
        with _BUS.lock:
            _BUS.services[server.name] = server
        self._servers.append(server)
        return server

    def create_client(self, srv_type: type, name: str, **_k: Any) -> _Client:
        return _Client(srv_type, name)

    # ---- 타이머
    def create_timer(self, period: float, callback: Callable, **_k: Any) -> _Timer:
        timer = _Timer(period, callback)
        self._timers.append(timer)
        return timer

    # ---- 파라미터
    def declare_parameter(self, name: str, value: Any = None, *_a: Any, **_k: Any) -> _ParameterHandle:
        overrides = _OVERRIDES.get(self._node_name, {})
        self._params.setdefault(name, overrides.get(name, value))
        return _ParameterHandle(name, self._params[name])

    def has_parameter(self, name: str) -> bool:
        return name in self._params

    def get_parameter(self, name: str) -> _ParameterHandle:
        if name not in self._params:
            raise KeyError(f"파라미터 없음: {name}")
        return _ParameterHandle(name, self._params[name])

    def _on_set_parameters(self, request: Any, response: Any) -> Any:
        results = []
        for parameter in getattr(request, "parameters", []) or []:
            self._params[parameter.name] = _from_parameter_value(parameter.value)
            results.append(SetParametersResult(successful=True))
        response.results = results
        return response

    # ---- 실행기에서 부른다
    def _post(self, callback: Callable, *args: Any) -> None:
        if not self._destroyed:
            self._inbox.put((callback, args))

    def _run_due_timers(self) -> float:
        """기한이 된 타이머를 돌리고, 다음 기한까지 남은 시간[s]을 돌려준다."""
        now = time.monotonic()
        wait = 0.05
        for timer in list(self._timers):
            if timer.cancelled:
                continue
            if now >= timer.due:
                _safe(timer.callback)
                # 늦게 돌았으면 밀린 만큼 한꺼번에 돌지 않고 다음 주기로 넘어간다.
                timer.due = max(timer.due + timer.period, time.monotonic())
            wait = min(wait, max(timer.due - time.monotonic(), 0.0))
        return wait

    def _run_inbox(self, timeout: float) -> None:
        try:
            callback, args = self._inbox.get(timeout=max(timeout, 0.0))
        except queue.Empty:
            return
        _safe(callback, *args)
        # 쌓인 것은 한 번에 비운다(타이머를 오래 미루지 않을 만큼만).
        deadline = time.monotonic() + 0.02
        while time.monotonic() < deadline:
            try:
                callback, args = self._inbox.get_nowait()
            except queue.Empty:
                return
            _safe(callback, *args)

    def destroy_node(self) -> None:
        self._destroyed = True
        with _BUS.lock:
            for subscription in self._subscriptions:
                bucket = _BUS.subscriptions.get(subscription.topic, [])
                if subscription in bucket:
                    bucket.remove(subscription)
            for server in self._servers:
                if _BUS.services.get(server.name) is server:
                    del _BUS.services[server.name]
        for timer in self._timers:
            timer.cancel()


# ---------------------------------------------------------------- 실행기
class SingleThreadedExecutor:
    """붙인 노드들의 타이머 · 받은 메시지 · 서비스 요청을 한 스레드에서 차례로 돌린다."""

    def __init__(self, *_a: Any, **_k: Any) -> None:
        self._nodes: list[Node] = []
        self._running = threading.Event()

    def add_node(self, node: Node) -> bool:
        if node not in self._nodes:
            self._nodes.append(node)
        return True

    def remove_node(self, node: Node) -> None:
        if node in self._nodes:
            self._nodes.remove(node)

    def spin_once(self, timeout_sec: float | None = None) -> None:
        nodes = [n for n in self._nodes if not n._destroyed]
        if not nodes:
            time.sleep(timeout_sec or 0.05)
            return
        wait = min(node._run_due_timers() for node in nodes)
        if timeout_sec is not None:
            wait = min(wait, timeout_sec)
        share = wait / len(nodes)
        for node in nodes:
            node._run_inbox(share)

    def spin(self) -> None:
        self._running.set()
        while self._running.is_set() and _BUS.ok:
            self.spin_once(0.05)

    def shutdown(self, *_a: Any, **_k: Any) -> bool:
        self._running.clear()
        return True


MultiThreadedExecutor = SingleThreadedExecutor


# ---------------------------------------------------------------- rclpy 함수
def init(*_a: Any, **_k: Any) -> None:
    _BUS.ok = True


def ok(*_a: Any, **_k: Any) -> bool:
    return _BUS.ok


def shutdown(*_a: Any, **_k: Any) -> None:
    _BUS.ok = False


def spin(node: Node, executor: SingleThreadedExecutor | None = None) -> None:
    executor = executor or SingleThreadedExecutor()
    executor.add_node(node)
    executor.spin()


def spin_once(node: Node, executor: SingleThreadedExecutor | None = None,
              timeout_sec: float | None = None) -> None:
    executor = executor or SingleThreadedExecutor()
    executor.add_node(node)
    executor.spin_once(timeout_sec)


# ---------------------------------------------------------------- 설치
def _module(name: str, **attrs: Any) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    module.__liteross__ = True
    return module


def install() -> None:
    """같은 이름의 모듈을 sys.modules 에 넣는다. RCS · 로봇 노드를 import 하기 **전에** 부른다.

    진짜 ROS 가 깔려 있어도 이것이 이긴다(단독판은 한 프로세스 안에서만 잇는다).
    """
    rclpy = _module("rclpy", init=init, ok=ok, shutdown=shutdown, spin=spin,
                    spin_once=spin_once)
    rclpy.node = _module("rclpy.node", Node=Node)
    rclpy.executors = _module("rclpy.executors", SingleThreadedExecutor=SingleThreadedExecutor,
                              MultiThreadedExecutor=MultiThreadedExecutor)
    rclpy.qos = _module("rclpy.qos")
    std_msgs = _module("std_msgs")
    std_msgs.msg = _module("std_msgs.msg", Bool=Bool, String=String, Int32=Int32,
                           Int32MultiArray=Int32MultiArray,
                           Float32MultiArray=Float32MultiArray)
    std_srvs = _module("std_srvs")
    std_srvs.srv = _module("std_srvs.srv", Trigger=Trigger)
    rcl = _module("rcl_interfaces")
    rcl.msg = _module("rcl_interfaces.msg", Parameter=Parameter, ParameterValue=ParameterValue,
                      ParameterType=ParameterType, SetParametersResult=SetParametersResult)
    rcl.srv = _module("rcl_interfaces.srv", SetParameters=SetParameters)
    for module in (rclpy, rclpy.node, rclpy.executors, rclpy.qos, std_msgs, std_msgs.msg,
                   std_srvs, std_srvs.srv, rcl, rcl.msg, rcl.srv):
        sys.modules[module.__name__] = module
    init()


def installed() -> bool:
    return bool(getattr(sys.modules.get("rclpy"), "__liteross__", False))
