#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
probe_robot.py — 로봇 Modbus 서버가 무엇을 받아주는지 진단
============================================================
    python probe_robot.py 192.168.1.101
    python probe_robot.py 192.168.1.101 --soak 60     # 60초 지속 시험

확인하는 것
  1) Unit ID × 기능코드(FC3/FC4) — 반드시 1워드로 시험 (개수 제한과 구분)
  2) 한 번에 읽을 수 있는 워드 수
  3) 엔코더 블록과 똑같은 요청 (FC3 로 384/410 에서 6워드) 재현
  4) 지속 시험 — 간헐적으로 실패하는지, 성공률이 얼마인지
  5) 관심 주소 값 + 63~80 덤프

에러 응답이 오면 엔코더 블록처럼 '에러 프레임을 데이터로 착각했을 때' 보이는
숫자까지 같이 계산해서 보여준다.
"""

import argparse
import time

from pyModbusTCP.client import ModbusClient

FCNAME = {3: "FC3 read_holding_registers", 4: "FC4 read_input_registers"}
EXC = {
    1: "ILLEGAL FUNCTION (그 기능코드를 지원하지 않음)",
    2: "ILLEGAL DATA ADDRESS (주소 범위 밖)",
    3: "ILLEGAL DATA VALUE (개수 등 값이 잘못됨)",
    4: "SLAVE DEVICE FAILURE",
    5: "ACKNOWLEDGE",
    6: "SLAVE DEVICE BUSY (바빠서 거절)",
    10: "GATEWAY PATH UNAVAILABLE",
    11: "GATEWAY TARGET NO RESPONSE",
}
UNITS = [1, 255, 0, 2]


def read(cli, fc, addr, count):
    """(성공, 값 또는 오류설명, 예외코드)"""
    fn = cli.read_holding_registers if fc == 3 else cli.read_input_registers
    try:
        r = fn(addr, count)
    except ValueError as e:
        return False, f"범위 오류: {e}", None
    if r is None:
        exc = cli.last_except
        txt = f"{cli.last_error_as_txt}"
        if exc:
            txt += f" / 예외 0x{exc:02X} = {EXC.get(exc, '?')}"
        return False, txt, exc
    return True, list(r), None


def exception_frame_as_registers(unit, fc, exc):
    """
    에러 응답 프레임을 '레지스터 6개'로 잘못 파싱하면 어떤 숫자가 보이는지 계산.
    프레임: TID(2) PID(2)=0 LEN(2)=3 UID(1) FC|0x80(1) EXC(1)
    """
    return [
        ("TransactionID (요청마다 증가)", None),
        ("ProtocolID", 0x0000),
        ("Length", 0x0003),
        ("UnitID<<8 | FC+0x80", (unit << 8) | ((fc | 0x80) & 0xFF)),
        ("예외코드 조합", (exc << 8) | exc if exc else None),
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=502)
    ap.add_argument("--timeout", type=float, default=2.0)
    ap.add_argument("--soak", type=int, default=0,
                    help="지속 시험 시간(초). 간헐적 실패를 잡는다")
    a = ap.parse_args()

    print(f"대상 {a.host}:{a.port}\n" + "=" * 70)

    # ---------------- 1) Unit ID × 기능코드 (1워드) -------------------
    print("\n[1] Unit ID × 기능코드  — 1워드로 시험 (개수 제한과 구분하기 위해)")
    works = []
    for unit in UNITS:
        cli = ModbusClient(host=a.host, port=a.port, unit_id=unit,
                           timeout=a.timeout, auto_open=True)
        if not cli.open():
            print(f"  unit {unit:>3} : TCP 접속 실패")
            continue
        for fc in (3, 4):
            ok, res, exc = read(cli, fc, 63, 1)
            print(f"  unit {unit:>3} {FCNAME[fc]:<28} -> "
                  f"{'OK  ' + str(res) if ok else 'FAIL ' + str(res)}")
            if ok:
                works.append((unit, fc))
            elif exc:
                print("        └ 이 에러 프레임을 데이터로 착각하면 이런 숫자가 보임:")
                for label, val in exception_frame_as_registers(unit, fc, exc):
                    if val is not None:
                        print(f"           {label:<28} = {val}")
        cli.close()

    if not works:
        print("\n읽기가 되는 조합이 없습니다. 확인할 것:")
        print("  - 티치펜던트에서 Modbus 서버(슬레이브) 기능이 켜져 있는지")
        print("  - 원격 제어 모드인지")
        print("  - 다른 프로그램이 이미 접속을 점유하고 있지 않은지")
        return

    unit, fc = works[0]
    print(f"\n→ 동작 조합: unit {unit}, {FCNAME[fc]}")
    if len(works) > 1:
        print(f"  (다른 동작 조합도 있음: {works[1:]})")
    if fc == 4 and not any(f == 3 for _, f in works):
        print("  ※ FC3 는 1워드로도 거절됨 → 이 컨트롤러는 홀딩 레지스터 읽기를")
        print("     지원하지 않습니다. 엔코더 블록이 FC3 를 쓰면 절대 못 읽습니다.")

    cli = ModbusClient(host=a.host, port=a.port, unit_id=unit,
                       timeout=a.timeout, auto_open=True)
    cli.open()

    # ---------------- 2) 블록 크기 -----------------------------------
    print("\n[2] 한 번에 읽을 수 있는 워드 수 (주소 63)")
    max_ok = 0
    for count in (1, 6, 12, 16, 32, 48, 64, 100, 125):
        ok, res, exc = read(cli, fc, 63, count)
        print(f"  {count:>4} 워드 -> {'OK' if ok else 'FAIL ' + str(res)}")
        if ok:
            max_ok = count
        else:
            break

    # ---------------- 3) 엔코더 블록 요청 재현 ------------------------
    print("\n[3] 엔코더 블록과 같은 요청 재현 (6워드씩)")
    for label, addr in [("Position  384~389", 384),
                        ("Tool/Off  410~415", 410),
                        ("Speed     400~405", 400)]:
        line = f"  {label}: "
        for f in (3, 4):
            ok, res, exc = read(cli, f, addr, 6)
            line += f"FC{f} {'OK' if ok else 'FAIL'}  "
            if ok:
                line += f"{res}  "
        print(line)

    # ---------------- 4) 관심 주소 -----------------------------------
    print("\n[4] 주요 주소")
    for label, addr, count in [("63  컨트롤러 버전", 63, 3),
                               ("66  robot_mode", 66, 1),
                               ("73  관절 위치 6개", 73, 6),
                               ("384 TCP pose", 384, 6),
                               ("400 TCP 속도", 400, 6),
                               ("410 TCP offset", 410, 6),
                               ("420 (Reserved)", 420, 6),
                               ("500 운전 상태", 500, 1)]:
        ok, res, exc = read(cli, fc, addr, count)
        print(f"  {label:<20} -> {res if ok else 'FAIL ' + str(res)}")

    # ---------------- 5) 63~80 덤프 ----------------------------------
    print("\n[5] 63~80 덤프 (관절 주소 확인용)")
    ok, res, exc = read(cli, fc, 63, 18)
    if ok:
        for i, v in enumerate(res):
            sv = v - 0x10000 if v >= 0x8000 else v
            mark = " <-- 관절?" if 73 <= 63 + i <= 78 else ""
            print(f"  {63 + i:>4} : {v:>6} (signed {sv:>7}){mark}")

    # ---------------- 6) 지속 시험 -----------------------------------
    if a.soak > 0:
        print(f"\n[6] 지속 시험 {a.soak}초 — 간헐적 실패 / 성공률 측정")
        print("   (엔코더 블록처럼 384 에서 6워드를 100ms 간격으로 계속 읽음)")
        t0 = time.time()
        ok_n = fail_n = 0
        fail_kinds = {}
        streak = cur = 0
        last_report = t0
        while time.time() - t0 < a.soak:
            ok, res, exc = read(cli, fc, 384, 6)
            if ok:
                ok_n += 1
                cur = 0
            else:
                fail_n += 1
                cur += 1
                streak = max(streak, cur)
                k = str(res)[:60]
                fail_kinds[k] = fail_kinds.get(k, 0) + 1
            if time.time() - last_report >= 5:
                last_report = time.time()
                tot = ok_n + fail_n
                print(f"   {time.time() - t0:5.0f}s  성공 {ok_n} / 실패 {fail_n} "
                      f"({100 * ok_n / max(1, tot):.1f}% 성공)")
            time.sleep(0.1)
        tot = ok_n + fail_n
        print(f"\n   결과: 성공 {ok_n} / 실패 {fail_n}  "
              f"→ 성공률 {100 * ok_n / max(1, tot):.1f}%")
        print(f"   연속 실패 최대 {streak}회 (= 약 {streak * 0.1:.1f}초)")
        for k, v in sorted(fail_kinds.items(), key=lambda kv: -kv[1]):
            print(f"   실패 유형 {v:>5}회 : {k}")
        if fail_n and ok_n:
            print("\n   → 간헐적으로만 성공합니다. 엔코더 블록이 '잠깐 값이 나왔다가"
                  " 다시 고정값' 이 되는 증상과 일치합니다.")

    cli.close()

    # ---------------- 결론 -------------------------------------------
    print("\n" + "=" * 70)
    print("권장 설정")
    print(f"  Unit ID       : {unit}")
    print(f"  읽기 기능코드 : FC{fc}")
    print(f"  블록 크기     : {max_ok} 워드 이하")
    if not any(f == 3 for _, f in works):
        print("\n  ★ 엔코더 블록은 FC3 을 쓰는 것으로 보입니다(에러코드 0x83).")
        print("    로봇이 FC3 을 거절하므로 직결로는 해결되지 않습니다.")
        print("    브리지를 거쳐서 읽게 하세요 — 브리지 서버는 FC3/FC4 모두 응답합니다.")


if __name__ == "__main__":
    main()
