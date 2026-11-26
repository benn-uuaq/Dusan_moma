"""단일 TCP 작업 스레드로 PLC를 읽고 불변 스냅샷으로 제어 노드에 전달한다."""

from concurrent.futures import Future
from dataclasses import dataclass, replace
from functools import cached_property
from math import isfinite, radians
import socket
import struct
from threading import Event, Lock, Thread
from time import monotonic

from .cpu_temperature import UNAVAILABLE, read_cpu_temperature_word


@dataclass(frozen=True)
class WheelSample:
    """한 휠의 누적거리, 속도, 조향각(SI 단위) 피드백."""
    distance_m: float
    speed_mps: float
    angle_rad: float


@dataclass(frozen=True)
class FastFeedback:
    """DM000~039에서 빠른 주기로 읽는 운전·휠 피드백."""
    life: int
    mode: int
    state: int
    busy: bool
    error_code: int
    wheels: tuple[WheelSample, ...]  # 순서: FL, FR, RL, RR
    outrig: bool
    lift_height_m: float
    guide_error_m: float
    guide_error_rad: float
    estop: bool
    auto_ready: bool
    feedback_speed_mmps: int

    @cached_property
    def invalid_wheel_dms(self) -> tuple[int, ...]:
        """유효하지 않은 휠 거리·속도의 하위 워드 주소를 기록한다."""
        addresses = []
        for index, wheel in enumerate(self.wheels):
            if not isfinite(wheel.distance_m):
                addresses.append(5 + 2 * index)
            if not isfinite(wheel.speed_mps):
                addresses.append(13 + 2 * index)
        return tuple(addresses)

    @property
    def measurement_fault(self) -> int:
        if self.invalid_wheel_dms:
            return 102  # DRIVE_ENCODER_FAULT
        if not isfinite(self.lift_height_m):
            return 203  # LIFT_POSITION_ERROR
        return 0


@dataclass(frozen=True)
class SlowFeedback:
    """DM050~099에서 느린 주기로 읽는 장애물·입출력·배터리 피드백."""
    front_obstacle: int
    rear_obstacle: int
    guide_distance_m: tuple[float, float]
    input_words: tuple[int, ...]
    output_words: tuple[int, ...]
    battery_voltage: float
    battery_soc_percent: float
    battery_temperature_c: float
    charging: bool

    @property
    def obstacle_stop(self) -> bool:
        """장애물 워드의 0번 비트(정지)를 해석한다."""
        return bool((self.front_obstacle | self.rear_obstacle) & 1)

    @property
    def obstacle_slow(self) -> bool:
        """장애물 워드의 1번 비트(감속)를 해석한다."""
        return bool((self.front_obstacle | self.rear_obstacle) & 2)


@dataclass(frozen=True)
class PlcSnapshot:
    """한 시점의 연결 상태와 fast/slow 피드백을 묶은 불변 자료."""
    generation: int = 0
    connected: bool = False
    fast: FastFeedback | None = None
    slow: SlowFeedback | None = None
    fast_at: float | None = None
    slow_at: float | None = None
    life_changed_at: float | None = None
    error: str = ""
    interlock_epoch: int = 0

    def interlocks(self):
        """변경 시 기존 명령을 폐기해야 하는 안전 상태만 추려 반환한다."""
        if not self.connected or not self.fast or not self.slow:
            return None
        f = self.fast
        return (f.mode, f.estop, f.error_code, f.state in (8, 16), f.outrig,
                self.slow.obstacle_stop, f.measurement_fault)


def stopped_words():
    """통신 지연이나 오류 때 제출할 중립·정지 레지스터 값을 만든다."""
    return {103: 0, 106: 0, 109: 0, **dict.fromkeys(range(120, 128), 0)}


class PlcCodec:
    """Keyence 상위 링크 ASCII 프레임과 PLC 레지스터 값을 변환한다."""
    @staticmethod
    def encode_scaled(value, scale, signed=False):
        """SI 값을 배율 적용한 UINT16 워드로 바꾸고 범위를 검사한다."""
        scaled = value * scale
        if not isfinite(scaled):
            raise ValueError("nonfinite PLC target")
        word = round(scaled)
        lower, upper = (-32768, 32767) if signed else (0, 65535)
        if not lower <= word <= upper:
            raise ValueError("target outside PLC register range")
        return word & 65535

    @staticmethod
    def build_write_request(address, value):
        """허용 목록에 있는 DM 주소만 WR 요청으로 직렬화한다."""
        allowed = {100, 103, 104, *range(106, 113), *range(120, 128)}
        if address not in allowed or not isinstance(value, int) or not 0 <= value <= 65535:
            raise ValueError("invalid PLC write")
        if address == 104 and value != 1:
            raise ValueError("RESET only accepts a pulse of 1")
        if address == 103 and value not in (0, 1, 2, 4, 16, 32):
            raise ValueError("invalid DM103 command")
        return f"WR DM{address} {value}\r".encode("ascii")

    @staticmethod
    def build_read_request(address: int, count: int) -> bytes:
        """연속 UINT16 레지스터 읽기 요청을 만든다."""
        return f"RDS DM{address}.U {count}\r".encode("ascii")

    @staticmethod
    def parse_words(frame: bytes, count: int) -> tuple[int, ...]:
        """PLC의 공백 구분 응답을 지정 개수의 UINT16 튜플로 검증한다."""
        fields = frame.split()
        if len(fields) != count or any(not field.isdigit() or len(field) > 5 for field in fields):
            raise ValueError("invalid PLC word response")
        words = tuple(map(int, fields))
        if any(word > 65535 for word in words):
            raise ValueError("PLC word outside UINT16 range")
        return words

    @staticmethod
    def decode_int16(word: int) -> int:
        """UINT16 비트 패턴을 부호 있는 INT16으로 해석한다."""
        return word - 65536 if word >= 32768 else word

    @staticmethod
    def decode_dint(low: int, high: int) -> int:
        """하위·상위 워드를 부호 있는 32비트 정수로 복원한다."""
        return struct.unpack("<i", struct.pack("<HH", low, high))[0]

    @classmethod
    def decode_fast_data(cls, words: tuple[int, ...]) -> FastFeedback:
        """고속 DM 블록을 SI 단위의 강타입 피드백으로 변환한다."""
        wheels = tuple(
            WheelSample(
                distance_m=cls.decode_dint(words[5 + 2 * index], words[6 + 2 * index]) / 1000,
                speed_mps=cls.decode_dint(words[13 + 2 * index], words[14 + 2 * index]) / 1000,
                angle_rad=radians(cls.decode_int16(words[21 + index]) / 100),
            )
            for index in range(4)
        )
        return FastFeedback(
            life=words[0],
            mode=words[1],
            state=words[2],
            busy=bool(words[3]),
            error_code=words[4],
            wheels=wheels,
            outrig=bool(words[25]),
            lift_height_m=cls.decode_dint(words[26], words[27]) / 1000,
            guide_error_m=cls.decode_int16(words[28]) / 1000,
            guide_error_rad=radians(cls.decode_int16(words[29]) / 100),
            estop=bool(words[30]),
            auto_ready=bool(words[31]),
            feedback_speed_mmps=cls.decode_int16(words[32]),
        )

    @staticmethod
    def decode_slow_data(words: tuple[int, ...]) -> SlowFeedback:
        """저속 DM 블록을 장애물·배터리 피드백으로 변환한다."""
        return SlowFeedback(
            front_obstacle=words[0],
            rear_obstacle=words[1],
            guide_distance_m=(words[2] / 1000, words[3] / 1000),
            input_words=words[4:7],
            output_words=words[7:9],
            battery_voltage=words[9] / 10,
            battery_soc_percent=words[10] / 10,
            battery_temperature_c=words[11] / 10,
            charging=bool(words[12]),
        )


class PlcClient:
    """요청과 응답을 한 스레드에서 순차 처리하는 PLC TCP 클라이언트."""
    def __init__(
        self,
        host: str,
        port: int,
        response_timeout: float,
        retry_period: float,
        fast_period: float = 0.01,
        slow_period: float = 0.1,
        writes_enabled: bool = False,
        command_timeout: float = 0.25,
        debug=None,
    ):
        if not host or not 1 <= port <= 65535:
            raise ValueError("invalid PLC endpoint")
        for period in (response_timeout, retry_period, fast_period, slow_period, command_timeout):
            if not isfinite(period) or period <= 0:
                raise ValueError("PLC periods and timeouts must be positive and finite")
        self._endpoint = (host, port)
        self.debug = debug
        self._response_timeout = response_timeout
        self._retry_period = retry_period
        self._fast_period = fast_period
        self._slow_period = slow_period
        self._stop = Event()
        self._lock = Lock()
        self._snapshot = PlcSnapshot()
        # 키는 DM 주소, 값은 해당 연결에서 실제 읽거나 전송한 마지막 워드다.
        self._rx_words = {}
        self._tx_words = {}
        self._writes_enabled = writes_enabled
        self._command_timeout = command_timeout
        self._desired = None
        self._reset = None
        self._life = 0
        self._cpu_sample = (0.0, UNAVAILABLE)
        self._cpu_thread = None
        # 소켓은 이 스레드만 소유한다. ROS 콜백은 스냅샷과 희망 출력만 교환한다.
        self._thread = Thread(target=self._run, name="plc-tcp")

    def start(self) -> None:
        """PLC 작업 스레드를 시작한다."""
        if self._writes_enabled:
            # 센서 조회가 지연되어도 PLC 스캔과 LIFE 전송은 계속 진행한다.
            self._cpu_thread = Thread(target=self._sample_cpu, name="pc-cpu-temp", daemon=True)
            self._cpu_thread.start()
        self._thread.start()

    def _sample_cpu(self):
        while not self._stop.is_set():
            started = monotonic()
            try:
                word = read_cpu_temperature_word()
            except OSError:
                word = UNAVAILABLE
            with self._lock:
                self._cpu_sample = (started, word)
            self._stop.wait(1.0)

    def close(self) -> None:
        """작업 스레드에 종료를 알리고 안전 출력 후 끝날 때까지 기다린다."""
        self._stop.set()
        self._thread.join()
        if self._cpu_thread is not None:
            self._cpu_thread.join(timeout=1.0)

    def take_latest_snapshot(self) -> PlcSnapshot:
        """잠금 아래에서 최신 스냅샷 참조를 가져온다."""
        with self._lock:
            return self._snapshot

    def _publish(self, snapshot: PlcSnapshot) -> None:
        """새 피드백을 공개하고 인터록 변화 세대를 증가시킨다."""
        with self._lock:
            old = self._snapshot
            changed = old.interlocks() != snapshot.interlocks() or old.generation != snapshot.generation
            self._snapshot = replace(snapshot, interlock_epoch=old.interlock_epoch + int(changed))
            if not snapshot.connected:
                self._desired = None
                if self._reset is not None:
                    if not self._reset.done():
                        self._reset.set_result(False)
                    self._reset = None

    def submit(self, sample, words):
        """읽었던 인터록 세대가 아직 같을 때만 희망 출력을 등록한다."""
        with self._lock:
            if self._writes_enabled and sample.connected and sample.interlock_epoch == self._snapshot.interlock_epoch:
                self._desired = (sample.interlock_epoch, monotonic(), dict(words))

    def request_reset(self):
        """RESET 1회 쓰기를 예약하고 PLC OK 결과를 받을 Future를 반환한다."""
        with self._lock:
            if not self._writes_enabled or not self._snapshot.connected or self._reset is not None:
                raise ValueError("PLC RESET unavailable")
            self._reset = Future()
            return self._reset

    def _read_words(self, connection: socket.socket, address: int, count: int) -> tuple[int, ...]:
        words = PlcCodec.parse_words(self._exchange(connection, PlcCodec.build_read_request(address, count)), count)
        with self._lock:
            self._rx_words.update(enumerate(words, start=address))
        return words

    def register_feedback(self):
        """ROS 발행 중에도 통신 스레드가 다음 프레임을 처리할 수 있게 복사한다."""
        with self._lock:
            return self._rx_words.copy(), self._tx_words.copy()

    def _exchange(self, connection, request, writes=()):
        """요청 하나를 보내고 CRLF로 끝나는 응답 하나를 완전히 수신한다."""
        deadline = monotonic() + self._response_timeout
        connection.settimeout(self._response_timeout)
        connection.sendall(request)
        if writes:
            with self._lock:
                for address, word in writes:
                    self._tx_words[address] = (word, "응답 대기")
        if self.debug:
            self.debug.record("plc", f"TX {request!r}")
        buffer = bytearray()
        # 종료 중에도 응답 하나를 끝까지 소비한 뒤에만 같은 스트림을 재사용한다.
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("PLC response timed out")
            connection.settimeout(remaining)
            chunk = connection.recv(512)
            if not chunk:
                raise ConnectionError("PLC closed the connection")
            buffer.extend(chunk)
            if len(buffer) > 512:
                raise ValueError("PLC response frame too long")
            if b"\r\n" in buffer:
                frame, trailing = bytes(buffer).split(b"\r\n", 1)
                if self.debug:
                    self.debug.record("plc", f"RX {bytes(buffer)!r}")
                if trailing:
                    raise ValueError("unexpected data after PLC response")
                return frame

    def _write(self, connection, address, word):
        """DM 쓰기 후 PLC의 OK 응답까지 확인한다."""
        request = PlcCodec.build_write_request(address, word)
        self._write_frame(connection, request, ((address, word),))

    def _write_life(self, connection, now):
        with self._lock:
            sampled, temperature = self._cpu_sample
        if now - sampled >= 3.0:
            temperature = UNAVAILABLE
        self._life = (self._life + 1) & 65535
        # 두 워드를 하나의 프레임으로 보내므로 DM102를 건드리지 않는다.
        request = f"WRS DM100.U 2 {self._life} {temperature}\r".encode("ascii")
        self._write_frame(connection, request, ((100, self._life), (101, temperature)))

    def _write_frame(self, connection, request, writes):
        reply = self._exchange(connection, request, writes)
        with self._lock:
            ack = reply.decode("ascii", errors="replace")
            for address, word in writes:
                self._tx_words[address] = (word, ack)
        if reply != b"OK":
            raise ValueError(f"PLC rejected write to DM{writes[0][0]}: {ack}")

    def _flush_one(self, connection, cache, next_life):
        """RESET, PC LIFE, 변경된 출력 순으로 한정된 쓰기 작업을 수행한다."""
        with self._lock:
            desired, reset, sample = self._desired, self._reset, self._snapshot
        now = monotonic()
        active = desired and desired[0] == sample.interlock_epoch and now - desired[1] < self._command_timeout
        words = desired[2] if active else stopped_words()
        if (not active and (desired is None or (desired[2].get(103) == 2
                and now-desired[1] < self._command_timeout)) and sample.fast.mode == 2
                and sample.fast.state == 2 and sample.fast.outrig and not sample.fast.estop
                and not sample.fast.error_code and not sample.slow.obstacle_stop):
            # PLC는 HOLD 승인 과정에서 OUTRIG를 설정한다. ROS가 그 전이를 인식할
            # 동안 이동 출력은 중립으로 두고 HOLD 클램프만 유지한다.
            words[103] = 2
        if (not active and sample.fast.state == 32 and sample.fast.mode == 2
                and not (sample.fast.estop or sample.fast.error_code or sample.slow.obstacle_stop)
                and (desired is None or (desired[2].get(103) == 32
                     and now - desired[1] < self._command_timeout))):
            # OUTRIG 해제 에지에서 ROS가 새 스냅샷을 읽기 전까지 FINISH를 유지한다.
            words[103] = 32
        # 먼저 출력을 중립화하고 목표값을 쓴 뒤, 마지막에 RUN을 활성화한다.
        order = sorted(words, key=lambda a: (
            0 if a == 103 and words[a] in (0, 4) else
            1 if (120 <= a <= 127 or a == 106) and words[a] == 0 else
            4 if a == 103 else 2 if a == 112 else 3, a))
        changed = [a for a in order if cache.get(a) != words[a]]
        if reset is not None and not reset.cancelled():
            if reset.set_running_or_notify_cancel():
                self._write(connection, 104, 1)
                reset.set_result(True)
            with self._lock:
                self._reset = None
        elif reset is not None:
            with self._lock:
                self._reset = None
        if active and now >= next_life and not (changed and changed[0] == 103 and words[103] == 0):
            self._write_life(connection, now)
            next_life = now + 1.0
        for address in changed:
            with self._lock:
                latest = self._desired
            if active and (latest is None or latest[0] != desired[0] or latest[2] != words
                           or monotonic() - latest[1] >= self._command_timeout):
                break
            self._write(connection, address, words[address])
            cache[address] = words[address]
            if monotonic() - now >= self._fast_period:
                break
        return next_life

    def _poll(self, connection: socket.socket, generation: int) -> None:
        """fast/slow 읽기와 출력 쓰기를 주기적으로 직렬 수행한다."""
        fast = slow = None
        fast_at = slow_at = life_changed_at = None
        next_fast = next_slow = monotonic()
        next_life = next_fast + 1.0
        cache = {}
        while not self._stop.is_set():
            now = monotonic()
            if now >= next_fast:
                sample = PlcCodec.decode_fast_data(self._read_words(connection, 0, 40))
                fast_at = monotonic()
                if fast is None or sample.life != fast.life:
                    life_changed_at = fast_at
                fast = sample
                next_fast = max(next_fast + self._fast_period, fast_at)
            if not self._stop.is_set() and monotonic() >= next_slow:
                slow = PlcCodec.decode_slow_data(self._read_words(connection, 50, 50))
                slow_at = monotonic()
                next_slow = max(next_slow + self._slow_period, slow_at)
            self._publish(PlcSnapshot(generation, True, fast, slow, fast_at, slow_at, life_changed_at))
            if self._writes_enabled and not self._stop.is_set():
                next_life = self._flush_one(connection, cache, next_life)
            self._stop.wait(max(0.0, min(next_fast, next_slow) - monotonic()))
        if self._writes_enabled:
            for address, word in stopped_words().items():
                if cache.get(address) != word:
                    self._write(connection, address, word)

    def _run(self) -> None:
        """연결이 끊기면 정지 스냅샷을 공개하고 설정 주기로 재접속한다."""
        generation = 0
        while not self._stop.is_set():
            try:
                with socket.create_connection(self._endpoint, timeout=self._response_timeout) as connection:
                    with self._lock:
                        self._rx_words.clear()
                        self._tx_words.clear()
                    if self.debug:
                        self.debug.record("plc", f"CONNECTED {self._endpoint}")
                    generation += 1
                    self._poll(connection, generation)
            except (OSError, ValueError) as exc:
                with self._lock:
                    self._rx_words.clear()
                    for addr, (word, ack) in self._tx_words.items():
                        if ack == "응답 대기":
                            self._tx_words[addr] = (word, "응답 미확인")
                if self.debug:
                    self.debug.record("plc", f"ERROR {self._endpoint} {exc}")
                self._publish(PlcSnapshot(generation=generation, error=str(exc)))
                self._stop.wait(self._retry_period)
        self._publish(PlcSnapshot(generation=generation))
