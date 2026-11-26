#!/usr/bin/env python3
"""AGV 테스트 PLC 보조 — 리프트 높이 · 수동 아웃트리거를 흉내 낸다 (2026-11-23).

AGV 담당이 준 테스트 PLC(`ros2 run steer_agv_test_plc test_plc_ui`)는 주행(RUN → 바퀴 거리)과 HOLD · FINISH
(아웃트리거 DM025)만 흉내 내고 **리프트 높이(DM026 · DM027)는 움직이지 않는다**. 그래서 RCS 가 리프트를
올리면(lift_control → DM108 목표 높이) 끝나지 않는다. 이 도구는 테스트 PLC 에 두 번째 TCP 손님으로 붙어

  - 리프트: DM026/027(높이, float32 mm)을 DM108(목표 높이 mm) 쪽으로 움직인다. 아웃트리거가 고정(DM025 = 1)일
            때만, 속도는 자동 모드(DM001 = 2)면 DM112(mm/s), 수동 모드면 --manual-speed.
  - 수동 아웃트리거: 수동 모드에서 DM126(MAN_OUTRIG_SET) = 1 이면 DM025 = 1, DM127(MAN_OUTRIG_RESET) = 1 이면 0.

테스트 PLC 코드(src/steer_agv)는 고치지 않는다. 실제 PLC 에는 쓰지 말 것 — 기본 주소가 127.0.0.1 이다.

실행 (테스트 PLC 를 먼저 띄운 뒤):
    python3 tools/steer_agv_test_plc_lift.py            # 127.0.0.1:8501
    python3 tools/steer_agv_test_plc_lift.py --port 8501 --manual-speed 50
"""

from __future__ import annotations

import argparse
import socket
import struct
import sys
import time


def exchange(conn: socket.socket, command: str) -> str:
    conn.sendall(command.encode("ascii") + b"\r")
    buffer = b""
    while not buffer.endswith(b"\r\n"):
        chunk = conn.recv(512)
        if not chunk:
            raise ConnectionError("테스트 PLC 가 연결을 끊었습니다")
        buffer += chunk
    return buffer[:-2].decode("ascii")


def read_words(conn: socket.socket, address: int, count: int) -> list[int]:
    reply = exchange(conn, f"RDS DM{address:03d}.U {count}")
    words = [int(w) for w in reply.split()]
    if len(words) != count:
        raise ValueError(f"DM{address} 읽기 응답이 이상합니다: {reply!r}")
    return words


def write_words(conn: socket.socket, address: int, words: list[int]) -> None:
    reply = exchange(conn, f"WRS DM{address:03d}.U {len(words)} " + " ".join(map(str, words)))
    if reply != "OK":
        raise ValueError(f"DM{address} 쓰기 거절: {reply!r}")


def float_words(value_mm: float) -> list[int]:
    low, high = struct.unpack("<HH", struct.pack("<f", float(value_mm)))
    return [low, high]


def words_float(low: int, high: int) -> float:
    return struct.unpack("<f", struct.pack("<HH", low, high))[0]


def run(host: str, port: int, manual_speed: float, period: float) -> None:
    with socket.create_connection((host, port), timeout=2.0) as conn:
        print(f"테스트 PLC {host}:{port} 에 붙었습니다 — 리프트 높이 · 수동 아웃트리거를 흉내 냅니다"
              "(Ctrl+C 로 끝).", flush=True)
        last = time.monotonic()
        shown = None
        while True:
            time.sleep(period)
            now = time.monotonic()
            dt, last = now - last, now
            fast = read_words(conn, 0, 32)             # DM000~031
            cmd = read_words(conn, 100, 28)            # DM100~127
            mode, outrig = fast[1], fast[25]
            height = words_float(fast[26], fast[27])
            target, lift_speed = cmd[108 - 100], cmd[112 - 100]
            if mode in (0, 1):                         # 수동 아웃트리거
                if cmd[126 - 100] and not outrig:
                    write_words(conn, 25, [1])
                    outrig = 1
                elif cmd[127 - 100] and outrig:
                    write_words(conn, 25, [0])
                    outrig = 0
            speed = lift_speed if mode == 2 else manual_speed
            if outrig and speed > 0 and abs(target - height) > 0.05:
                step = min(abs(target - height), speed * dt)
                height += step if target > height else -step
                write_words(conn, 26, float_words(height))
            state = (mode, outrig, round(height), target)
            if state != shown:
                print(f"모드 {mode} · 아웃트리거 {'고정' if outrig else '해제'} · 리프트 {height:,.0f} → {target} mm",
                      flush=True)
                shown = state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--manual-speed", type=float, default=50.0, help="수동 모드 리프트 속도 mm/s")
    parser.add_argument("--period", type=float, default=0.05, help="갱신 주기 s")
    args = parser.parse_args()
    try:
        run(args.host, args.port, args.manual_speed, args.period)
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError) as exc:
        print(f"테스트 PLC 보조를 끝냅니다 — {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
