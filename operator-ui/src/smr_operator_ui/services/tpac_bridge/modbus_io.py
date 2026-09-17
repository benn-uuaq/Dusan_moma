#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
modbus_io.py — pyModbusTCP 기반 Modbus 입출력 계층
==================================================
기존 robot.py 와 같은 라이브러리(pyModbusTCP)를 사용한다.

  RobotModbus  : robot.py 의 Robot_modbus 를 상속. 기존 메서드(set_coil, get_coil,
                 set_register, get_all_registers ...)를 그대로 쓰면서
                 unit_id / 다중 쓰기 / 오류 전달을 추가했다.
  ServerBridge : 이 프로그램이 Modbus TCP 서버가 되어 외부에 값을 제공.
                 Holding Register(FC3) 와 Input Register(FC4) 양쪽에 같은 값을 넣어
                 외부에서 어느 쪽으로 읽어도 동작한다.
"""

import time
import threading

from pyModbusTCP.client import ModbusClient
from pyModbusTCP.server import ModbusServer, DataBank, DataHandler

# ---------------------------------------------------------------- robot.py 연동
try:
    from robot import Robot_modbus as _Base       # 같은 폴더의 robot.py
    HAVE_ROBOT_PY = True
except Exception:                                 # robot.py 가 없어도 단독 동작
    _Base = object
    HAVE_ROBOT_PY = False


class RobotModbus(_Base):
    """
    로봇(또는 외부 장비)의 Modbus TCP 서버에 접속하는 클라이언트.

    robot.py 가 같은 폴더에 있으면 Robot_modbus 를 상속하므로
    set_coil / get_coil / set_register / get_all_registers 등을 그대로 쓸 수 있다.
    """

    def __init__(self, host, port=502, unit_id=1, timeout=1.0,
                 read_fc="auto", write_fc="auto", on_log=None):
        """
        read_fc  : 3(홀딩) / 4(입력) / "auto" — auto 는 FC3 먼저 시도 후 FC4 로 전환
        write_fc : 16(다중) / 6(단일 반복) / "auto"
        """
        if HAVE_ROBOT_PY:
            super().__init__(host, port)
        else:
            self.host, self.port = host, port
            self.client = None
            self.is_running = False
        self.unit_id = int(unit_id)
        self.timeout = float(timeout)
        self.read_fc = read_fc
        self.write_fc = write_fc
        self._log = on_log or (lambda m: None)
        self._read_fc_used = None       # auto 로 확정된 기능코드
        self._write_fc_used = None

    # ------------------------------------------------------------------
    def connect(self, quiet=True):
        """unit_id / timeout 을 반영해 접속. 성공 시 True."""
        self.client = ModbusClient(host=self.host, port=int(self.port),
                                   unit_id=self.unit_id, timeout=self.timeout,
                                   auto_open=True, auto_close=False)
        try:
            if self.client.is_open:
                self.client.close()
            ok = bool(self.client.open())
        except Exception:
            ok = False
        self.is_running = ok
        if not quiet:
            print(f"[Modbus] {self.host}:{self.port} "
                  f"{'접속 성공' if ok else '접속 실패'}")
        if ok and self.read_fc == "auto":
            self.auto_select_read_fc()
        return ok

    def disconnect(self):
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass
        self.is_running = False

    @property
    def is_open(self):
        try:
            return bool(self.client is not None and self.client.is_open)
        except Exception:
            return False

    def probe_read_fc(self, addr=63):
        """
        FC3 / FC4 를 '1워드' 로 시험해 어느 기능코드가 지원되는지 확정한다.
        큰 블록으로 시험하면 개수 제한 때문에 실패한 것을 기능코드 미지원으로
        오판하게 되므로, 반드시 1워드로 확인해야 한다.
        반환: {3: (성공여부, 결과/오류), 4: (...)}
        """
        res = {}
        for fc in (3, 4):
            res[fc] = self._read_once(fc, addr, 1)
        return res

    def auto_select_read_fc(self, addr=63):
        """probe 결과로 읽기 기능코드를 확정하고 로그를 남긴다."""
        res = self.probe_read_fc(addr)
        for fc in (3, 4):
            ok, r = res[fc]
            self._log(f"[Modbus] FC{fc} 1워드 시험 → "
                      f"{'OK ' + str(r) if ok else '실패: ' + str(r)}")
        if res[3][0]:
            self._read_fc_used = 3
        elif res[4][0]:
            self._read_fc_used = 4
        else:
            self._read_fc_used = None
            self._log("[Modbus] FC3/FC4 모두 실패 — 주소나 Unit ID 확인 필요")
            return None
        self._log(f"[Modbus] 읽기 기능코드 FC{self._read_fc_used} 사용")
        return self._read_fc_used

    def reconnect(self):
        try:
            self.client.close()
        except Exception:
            pass
        try:
            return bool(self.client.open())
        except Exception:
            return False

    # ------------------------------------------------------------------
    def _read_once(self, fc, addr, count):
        """지정 기능코드로 1회 읽기. (성공여부, 결과 또는 오류문자열)"""
        fn = (self.client.read_holding_registers if fc == 3
              else self.client.read_input_registers)
        try:
            regs = fn(int(addr), int(count))
        except ValueError as e:          # 주소/개수 범위 오류
            return False, str(e)
        if regs is None:
            return False, self._err()
        if len(regs) != count:
            return False, f"길이 불일치 {len(regs)}"
        return True, list(regs)

    def read_hr(self, addr, count=1):
        """
        레지스터 읽기. read_fc 설정에 따라 FC3(홀딩) 또는 FC4(입력) 사용.
        "auto" 면 FC3 → FC4 순으로 시도하고, 성공한 쪽을 이후 계속 쓴다.
        실패하면 IOError.
        (Robot_modbus.get_all_registers 는 실패 시 [] 를 주지만,
         중계기에서는 실패 원인을 알아야 하므로 예외로 올린다.)
        """
        if self.client is None:
            raise IOError("클라이언트가 열리지 않았습니다")

        # 사용할 기능코드 결정
        if self.read_fc in (3, 4, "3", "4"):
            order = [int(self.read_fc)]
        elif self._read_fc_used:
            order = [self._read_fc_used]
        else:
            order = [3, 4]

        errs = []
        for fc in order:
            ok, res = self._read_once(fc, addr, count)
            if ok:
                if self._read_fc_used != fc and len(order) > 1:
                    self._log(f"[Modbus] 읽기 기능코드 FC{fc} 사용으로 확정")
                self._read_fc_used = fc
                return res
            errs.append(f"FC{fc}: {res}")

        # 기능코드는 접속 시 1워드로 확정했으므로, 여기서 실패하면
        # 기능코드 문제가 아니라 주소/개수 문제일 가능성이 높다.
        hint = ""
        if count > 1:
            hint = f" (개수 {count} 가 너무 많을 수 있음 — 블록 크기를 줄여보세요)"
        raise IOError(f"read({addr},{count}) 실패 — " + " | ".join(errs) + hint)

    def write_hr(self, addr, values):
        """
        레지스터 쓰기. write_fc 설정에 따라 FC16(다중) 또는 FC6(단일 반복).
        "auto" 면 FC16 먼저 시도하고 안 되면 FC6 으로 전환. 실패하면 IOError.
        """
        if self.client is None:
            raise IOError("클라이언트가 열리지 않았습니다")
        vals = [int(v) & 0xFFFF for v in values]

        if self.write_fc in (6, 16, "6", "16"):
            order = [int(self.write_fc)]
        elif self._write_fc_used:
            order = [self._write_fc_used]
        else:
            order = [16, 6]

        errs = []
        for fc in order:
            try:
                if fc == 16:
                    ok = bool(self.client.write_multiple_registers(int(addr), vals))
                else:
                    ok = all(self.client.write_single_register(int(addr) + i, v)
                             for i, v in enumerate(vals))
            except ValueError as e:
                ok, err = False, str(e)
            else:
                err = self._err()
            if ok:
                if self._write_fc_used != fc and len(order) > 1:
                    self._log(f"[Modbus] 쓰기 기능코드 FC{fc} 사용으로 확정")
                self._write_fc_used = fc
                return
            errs.append(f"FC{fc}: {err}")

        if len(order) == 1 and self._write_fc_used and self.write_fc == "auto":
            self._write_fc_used = None
        raise IOError(f"write({addr},{len(vals)}) 실패 — " + " | ".join(errs))

    # ------------------------------------------------------------------
    @property
    def read_fc_used(self):
        return self._read_fc_used

    @property
    def write_fc_used(self):
        return self._write_fc_used

    # ------------------------------------------------------------------
    def _err(self):
        """pyModbusTCP 의 마지막 오류 문자열."""
        try:
            return f"{self.client.last_error_as_txt} / {self.client.last_except_as_txt}"
        except Exception:
            return "unknown"


# ============================================================================
class LoggingDataHandler(DataHandler):
    """
    외부 마스터(엔코더 블록 등)가 보내는 모든 요청을 기록하는 핸들러.
    어떤 기능코드로 어느 주소를 몇 개 읽어가는지 확인할 수 있다.

    delay_ms: 응답을 이만큼 늦춘다.
      폴링 속도 제한이 없는 장치는 응답이 즉시 오면 초당 수백~수천 번을
      쏟아내다가 스스로 주저앉는다. 실제 로봇이라면 네트워크 지연이
      자연스러운 제동이 되지만, 메모리에서 즉답하는 이 서버는 그게 없다.
      응답을 조금 늦춰 주는 것만으로 장치가 안정적으로 동작한다.
    """

    def __init__(self, data_bank=None, on_request=None, delay_ms=0):
        super().__init__(data_bank=data_bank)
        self.on_request = on_request or (lambda *_: None)
        self.delay_ms = float(delay_ms)
        self.requests = []          # (시각, client, fc, addr, count)
        self._last_resp = {}        # client -> 마지막 응답 시각
        self._rl_lock = threading.Lock()

    def _throttle(self, who):
        """
        최소 응답 간격 유지 (속도 제한).
        고정 지연이 아니라 '너무 빨리 다시 물으면 그만큼만 기다리게' 한다.
          - 띄엄띄엄 오는 요청(접속 확인 등)은 지연 없이 즉답 → 타임아웃 안 남
          - 쉬지 않고 몰아치는 요청만 제동 → 장비가 스스로 죽는 것을 막음
        """
        if self.delay_ms <= 0:
            return
        gap = self.delay_ms / 1000.0
        with self._rl_lock:
            now = time.monotonic()
            last = self._last_resp.get(who, 0.0)
            wait = gap - (now - last)
            self._last_resp[who] = now if wait <= 0 else now + wait
        if wait > 0:
            time.sleep(min(wait, gap))

    def _note(self, fc, address, count, srv_info):
        who = "?"
        try:
            who = srv_info.client.address
        except Exception:
            pass
        self._throttle(who)
        try:
            who = f"{srv_info.client.address}:{srv_info.client.port}"
        except Exception:
            pass
        rec = (time.time(), who, fc, int(address), int(count))
        self.requests.append(rec)
        self.on_request(*rec)

    # ---- 읽기 ---------------------------------------------------------
    def read_h_regs(self, address, count, srv_info):
        self._note(3, address, count, srv_info)
        return super().read_h_regs(address, count, srv_info)

    def read_i_regs(self, address, count, srv_info):
        self._note(4, address, count, srv_info)
        return super().read_i_regs(address, count, srv_info)

    def read_coils(self, address, count, srv_info):
        self._note(1, address, count, srv_info)
        return super().read_coils(address, count, srv_info)

    def read_d_inputs(self, address, count, srv_info):
        self._note(2, address, count, srv_info)
        return super().read_d_inputs(address, count, srv_info)

    # ---- 쓰기 ---------------------------------------------------------
    def write_h_regs(self, address, words, srv_info):
        self._note(16, address, len(words), srv_info)
        return super().write_h_regs(address, words, srv_info)

    def write_coils(self, address, bits, srv_info):
        self._note(15, address, len(bits), srv_info)
        return super().write_coils(address, bits, srv_info)


# ============================================================================
class ServerBridge:
    """
    Modbus TCP 서버. no_block 모드로 백그라운드 스레드에서 돌아간다.
    set_hr() 로 넣은 값은 Holding Register 와 Input Register 양쪽에 반영된다.
    """

    def __init__(self, log=None, on_request=None, delay_ms=0):
        self._log = log or (lambda m: None)
        self.on_request = on_request        # None 이면 요청 로깅 안 함
        self.delay_ms = float(delay_ms)     # 응답 지연 (폭주하는 장치용 제동)
        self.bank = None
        self.server = None
        self.handler = None
        self.running = False
        self._lock = threading.Lock()

    @property
    def requests(self):
        return self.handler.requests if self.handler is not None else []

    # ------------------------------------------------------------------
    def start(self, host="0.0.0.0", port=502):
        if self.running:
            self.stop()
        try:
            self.bank = DataBank()
            if self.on_request is not None or self.delay_ms > 0:
                self.handler = LoggingDataHandler(data_bank=self.bank,
                                                  on_request=self.on_request,
                                                  delay_ms=self.delay_ms)
                self.server = ModbusServer(host=host, port=int(port),
                                           no_block=True, data_hdl=self.handler)
            else:
                self.handler = None
                self.server = ModbusServer(host=host, port=int(port),
                                           no_block=True, data_bank=self.bank)
            self.server.start()
        except Exception as e:                          # noqa: BLE001
            self.running = False
            self._log(f"서버 시작 실패: {e}")
            return False
        self.running = True
        self._log(f"서버 시작 {host}:{port}")
        return True

    def stop(self):
        if not self.running:
            return
        self.running = False
        try:
            self.server.stop()
        except Exception as e:                          # noqa: BLE001
            self._log(f"서버 종료 예외: {e}")
        self._log("서버 정지")

    # ------------------------------------------------------------------
    def set_hr(self, addr, values):
        """홀딩/입력 레지스터 동시 갱신. 스레드 세이프."""
        if not self.running or self.bank is None:
            return False
        vals = [int(v) & 0xFFFF for v in values]
        try:
            with self._lock:
                self.bank.set_holding_registers(int(addr), vals)
                self.bank.set_input_registers(int(addr), vals)
            return True
        except Exception as e:                          # noqa: BLE001
            self._log(f"서버 값 갱신 오류: {e}")
            return False

    def set_coils(self, addr, bits):
        """코일(FC1)과 디스크리트 입력(FC2) 동시 갱신. 스레드 세이프."""
        if not self.running or self.bank is None:
            return False
        vals = [bool(b) for b in bits]
        try:
            with self._lock:
                self.bank.set_coils(int(addr), vals)
                self.bank.set_discrete_inputs(int(addr), vals)
            return True
        except Exception as e:                          # noqa: BLE001
            self._log(f"서버 코일 갱신 오류: {e}")
            return False

    def get_coils(self, addr, count=1):
        if not self.running or self.bank is None:
            return []
        try:
            v = self.bank.get_coils(int(addr), int(count))
            return [int(b) for b in v] if v else []
        except Exception:
            return []

    def get_hr(self, addr, count=1):
        if not self.running or self.bank is None:
            return []
        try:
            v = self.bank.get_holding_registers(int(addr), int(count))
            return list(v) if v else []
        except Exception:
            return []
