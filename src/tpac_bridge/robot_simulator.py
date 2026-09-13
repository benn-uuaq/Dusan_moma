#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
robot_simulator.py — 실제 로봇 없이 테스트하기 위한 가짜 로봇 Modbus TCP 서버.
(pyModbusTCP 기반)

    python robot_simulator.py --port 5502

그 다음 modbus_bridge.py 에서 로봇 IP = 127.0.0.1, 포트 = 5502 로 연결하면
관절값/TCP 값이 사인파로 움직이는 것을 볼 수 있다.

외부 수신 테스트용 더미 서버로도 쓸 수 있다:
    python robot_simulator.py --port 5600 --static
"""

import argparse
import math
import time

from modbus_io import ServerBridge
from robot_map import (REG_JOINT_POS, REG_JOINT_SPD, REG_JOINT_CUR,
                       REG_JOINT_TMP, REG_TCP_POSE, REG_TCP_SCAN,
                       REG_WORK_SPEED, REG_RUNNING_STATE, to_uint16)


def run(host="127.0.0.1", port=5502, static=False, quiet=False):
    sb = ServerBridge(log=(lambda m: None) if quiet else (lambda m: print("[sim]", m)))
    if not sb.start(host, port):
        raise SystemExit("서버 시작 실패")
    print(f"[sim] 가짜 로봇 서버 {host}:{port}  (Ctrl+C 로 종료)")

    if static:
        print("[sim] static 모드 — 값 갱신 없이 수신 전용으로 동작")
        try:
            while True:
                time.sleep(1)
                print("[sim] 수신값 addr 0~11:", sb.get_hr(0, 12))
        except KeyboardInterrupt:
            sb.stop()
            return

    t0 = time.time()
    try:
        while True:
            t = time.time() - t0
            sb.set_hr(63, [2, 15, 0])                       # 컨트롤러 버전
            # 66:mode 67:power 68:pstop 69:estop 70:reduced 71:control 72:operation
            sb.set_hr(66, [7, 1, 0, 0, 0, 2, 0])
            sb.set_hr(REG_RUNNING_STATE, [1])

            sb.set_hr(REG_JOINT_POS,
                      [to_uint16(1500 * math.sin(t * 0.5 + i * 0.7)) for i in range(6)])
            sb.set_hr(REG_JOINT_SPD,
                      [to_uint16(750 * math.cos(t * 0.5 + i * 0.7)) for i in range(6)])
            sb.set_hr(REG_JOINT_CUR, [to_uint16(500 + i * 37) for i in range(6)])
            sb.set_hr(REG_JOINT_TMP, [to_uint16(35 + i) for i in range(6)])

            tcp = [4000 + 1000 * math.sin(t * 0.4),        # X 0.1mm
                   -1500 + 800 * math.cos(t * 0.4),        # Y
                   3000 + 500 * math.sin(t * 0.8),         # Z
                   3140 * math.sin(t * 0.3),               # Rx mRad
                   200 * math.cos(t * 0.3),                # Ry
                   1570 * math.sin(t * 0.2)]               # Rz
            sb.set_hr(REG_TCP_POSE, [to_uint16(v) for v in tcp])   # 384~389
            sb.set_hr(REG_WORK_SPEED, [250, 80])                    # 306,307

            # 스캔 좌표(280~285): 제로점 기준 상대좌표.
            # 로봇 태스크처럼 스캔 중일 때만 값을 쓰고 그 외에는 0.
            if (t % 20) < 10:
                zero = [4000, -1500, 3000, 0, 0, 0]
                sb.set_hr(REG_TCP_SCAN,
                          [to_uint16(tcp[i] - zero[i]) for i in range(6)])
            else:
                sb.set_hr(REG_TCP_SCAN, [0] * 6)

            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\n[sim] 종료")
        sb.stop()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5502)
    ap.add_argument("--static", action="store_true", help="값 갱신 없이 수신 전용")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    run(a.host, a.port, a.static, a.quiet)
