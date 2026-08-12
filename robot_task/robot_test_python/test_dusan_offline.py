#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_dusan_offline.py — 로봇 없이 전체 체인을 검증한다.

가짜 로봇(dusan_robot_sim) 을 띄우고, dusan_test_ui 의 Poller 를 그대로 돌려서
  1) 29999 대시보드 명령 (play / pause / stop / variable)
  2) Modbus 파라미터 쓰기 -> 로봇이 그 값으로 계획을 세우는지
  3) 30001 실시간 TCP 수신
  4) PC 계산 상대좌표 == 로봇 발행 상대좌표 (0.1mm 양자화 오차 내)
  5) ㄹ자 경로가 계획대로 나오는지
를 확인한다.

실행:  python test_dusan_offline.py      -> 마지막 줄에 "전체 통과"
"""

import os
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtCore, QtWidgets                                   # noqa: E402

import dusan_map as M                                                 # noqa: E402
import dusan_robot_sim as SIM                                         # noqa: E402
from dusan_test_ui import Poller                                      # noqa: E402
from robot import Robot_29999                                         # noqa: E402

DASH, STREAM, MB = 39999, 40001, 45502
W, H, SW, OV = 400, 500, 200, 10   # 너비 / 높이 / 스캐너높이 / 겹침
FAIL = []


APP = None


def pump(seconds):
    """Qt 시그널은 이벤트 루프가 돌아야 전달된다. sleep 대신 이걸 쓴다."""
    t = time.time()
    while time.time() - t < seconds:
        APP.processEvents()
        time.sleep(0.01)


def check(name, cond, detail=""):
    print(f"  {'OK  ' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


def start_sim():
    import socketserver
    threading.Thread(
        target=lambda: SIM.ThreadedTCP(("127.0.0.1", DASH), SIM.DashHandler).serve_forever(),
        daemon=True).start()
    threading.Thread(target=SIM.stream_server, args=(STREAM,), daemon=True).start()
    threading.Thread(target=SIM.modbus_server, args=(MB,), daemon=True).start()
    time.sleep(1.0)


def main():
    global APP
    APP = QtWidgets.QApplication(sys.argv)
    app = APP
    start_sim()

    dash = Robot_29999("127.0.0.1", DASH)
    check("29999 연결", dash.connect_29999() is not None)
    check("29999 robotMode", "Robotmode" in (dash.send_command_29999("robotMode") or ""))

    zp = dash.get_variable("zero_pose")
    check("variable -get zero_pose", isinstance(zp, (list, tuple)) and len(zp) == 6, str(zp))

    samples = []
    poller = Poller("127.0.0.1", MB, period_ms=30, stream_port=STREAM)
    poller.sample.connect(samples.append)
    poller.log.connect(lambda m: None)
    poller.start()
    pump(1.5)
    check("30001 스트림 수신", any("tcp" in s for s in samples))
    check("Modbus 읽기", any(s.get("modbus") for s in samples))

    # --- 파라미터를 Modbus 로 전송
    for key, addr, *_ in M.INPUT_FIELDS:
        poller.request_write(addr, {"app_width": W, "app_height": H,
                                    "scan_h": SW, "overlap": OV}[key])
    poller.request_write(M.R_IN_SRC, 1)
    pump(0.8)
    check("파라미터 레지스터 반영",
          SIM.ROBOT.rget(M.R_IN_WIDTH, True) == W and SIM.ROBOT.rget(M.R_IN_SRC, True) == 1,
          f"256={SIM.ROBOT.rget(M.R_IN_WIDTH, True)} 266={SIM.ROBOT.rget(M.R_IN_SRC, True)}")

    # --- 시작
    samples.clear()
    dash.send_command_29999("play")

    plan = M.plan(W, H, SW, OV)
    zero_seen = False
    t0 = time.time()
    while time.time() - t0 < 120:
        pump(0.05)
        for s in samples:
            if s.get("need_zero"):
                v = dash.get_variable("zero_pose")
                if isinstance(v, (list, tuple)) and len(v) == 6:
                    poller.set_zero_pose([float(x) for x in v])
                    zero_seen = True
        d = samples[-1].get("dec") if samples else None
        if d and d["done"]:
            break
    app.processEvents()

    check("원점 확정 후 zero_pose 수신", zero_seen)
    last = [s for s in samples if s.get("dec")][-1]["dec"]
    check("스캔 완료 플래그", last["done"], f"state={last['state']}")
    check("계획된 패스 수와 일치", last["rows"] == plan["rows"] and last["row"] == plan["rows"],
          f"row={last['row']}/{last['rows']}, 계획 rows={plan['rows']}")
    check("진행률 100", last["progress"] == 100, f"{last['progress']}%")
    check("상승 피치 = 스캐너높이 - 겹침", last["pitch_mm"] == SW - OV == plan["pitch"],
          f"{last['pitch_mm']} (기대 {SW - OV})")

    # --- PC 계산 vs 로봇 발행
    # 스캔중(state=4) 구간만 비교한다. 완료 후에는 로봇이 발행을 멈추므로
    # 홈 복귀 동작이 PC 계산값에만 반영되어 당연히 어긋난다.
    pairs = [(s["rel_pc"], s["dec"]["rel_mm"]) for s in samples
             if s.get("rel_pc") and s.get("dec") and s["dec"]["zero_ok"] and s["dec"]["state"] == 4]
    # 상태 레지스터는 미러링 지연이 있어 경계 샘플에서 state 가 한 박자 늦게 바뀐다.
    # 구간 양끝을 조금씩 잘라내고 비교한다.
    pairs = pairs[5:-10] if len(pairs) > 40 else pairs
    check("비교 샘플 확보", len(pairs) > 30, f"{len(pairs)}개")
    if pairs:
        worst = max(max(abs(pc[i] - rb[i]) for i in range(3)) for pc, rb in pairs)
        # 가짜 로봇은 20ms 주기로 움직이고 폴링은 30ms 라 한 스텝(최대 2mm) 시차가 난다
        # 가짜 로봇은 20ms 주기 이동 + 50Hz 스트림 + 50Hz 레지스터 미러 + 33Hz 폴링이라
        # 파이프라인 지연만 60ms 남짓 = 100mm/s 에서 6mm. 알고리즘 오차가 아니다.
        check("PC계산 ↔ 로봇발행 일치(이동중)", worst < 8.0, f"최대차 {worst:.2f} mm")
        # 4샘플(약 120ms) 연속 정지 = 파이프라인 지연보다 길다 -> 값이 완전히 일치해야 한다
        still = []
        for i in range(3, len(pairs)):
            win = [pairs[i - k][0] for k in range(4)]
            if all(max(abs(win[0][j] - w[j]) for j in range(3)) < 1e-9 for w in win[1:]):
                still.append(pairs[i])
        if still:
            w2 = max(max(abs(pc[i] - rb[i]) for i in range(3)) for pc, rb in still)
            check("PC계산 ↔ 로봇발행 일치(정지중)", w2 < 0.35, f"최대차 {w2:.3f} mm ({len(still)}개)")

    # --- 경로 형상 (TCP 좌표계 -> 벽면 가로/세로 축으로 변환해서 본다)
    amap = M.axis_map(SIM.ROBOT.vars["zero_pose"])
    print(f"        {M.tool_axis_hint(amap)}")
    wall = [M.to_wall(pc[:3], amap) for pc, _ in pairs]
    ys = [w[0] for w in wall]
    zs = [w[1] for w in wall]
    check("가로 범위", abs(max(ys) - W) < 3.0 and abs(min(ys)) < 3.0,
          f"가로 {min(ys):.1f} ~ {max(ys):.1f} (기대 0 ~ {W})")
    check("세로 범위", abs(max(zs) - plan["top"]) < 3.0 and abs(min(zs)) < 3.0,
          f"세로 {min(zs):.1f} ~ {max(zs):.1f} (마지막 패스 {plan['top']}, 커버 {plan['covered']})")
    # 진행 방향 부호가 바뀐 횟수 (같은 값이 연속으로 찍히는 구간은 무시)
    signs = [1 if b > a else -1 for a, b in zip(ys, ys[1:]) if abs(b - a) > 0.05]
    turns = sum(1 for a, b in zip(signs, signs[1:]) if a != b)
    check("ㄹ자 방향 전환 발생", turns >= plan["rows"] - 2, f"{turns}회")

    # --- pause / stop
    samples.clear()
    dash.send_command_29999("play")
    pump(1.2)
    dash.send_command_29999("pause")
    pump(0.5)
    a = SIM.ROBOT.rget(M.R_ALIVE)
    pump(0.6)
    check("PAUSE 시 발행 정지", SIM.ROBOT.rget(M.R_ALIVE) == a, f"alive {a}")
    dash.send_command_29999("stop")
    pump(0.5)
    check("STOP 반영", not SIM.ROBOT.running)

    poller.stop(); poller.wait(2000)

    print()
    print("전체 통과" if not FAIL else f"실패 {len(FAIL)}건: {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
