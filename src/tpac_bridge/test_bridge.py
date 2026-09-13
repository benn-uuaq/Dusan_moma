#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_bridge.py — UI 없이 전체 체인 검증 (pyModbusTCP)

  가짜 로봇 서버 → RobotPoller → ExternalWriter → 가짜 외부 서버
                              → ExternalServer → 외부 마스터가 read

    python test_bridge.py
"""
import threading
import time

import modbus_io as mio
import robot_simulator
from bridge_core import (RobotPoller, ExternalWriter, ExternalServer,
                         MIRROR_UR)
from robot_map import (to_int16, clamp_int16, RAD2DEG, RobotData,
                       OUT_FORMATS, encode_payload, decode_payload,
                       REG_JOINT_POS, REG_TCP_POSE, to_uint16)

ROBOT_PORT, EXT_PORT, SRV_PORT = 5602, 5603, 5604
fails = []


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name} {extra}")
    if not cond:
        fails.append(name)


def main():
    print("1) 변환 함수 단위 검증")
    check("to_int16 음수", to_int16(0xFF9C) == -100, f"-> {to_int16(0xFF9C)}")
    check("to_int16 양수", to_int16(1500) == 1500)
    check("mRad→deg", abs(1500 / 1000 * RAD2DEG - 85.9437) < 0.01)
    check("clamp 상한", clamp_int16(48780) == 32767)
    check("clamp 하한", clamp_int16(-48780) == -32768)

    print("1-b) 데이터 타입 인코딩 왕복 검증 (음수 포함)")
    d0 = RobotData.from_registers({
        REG_JOINT_POS + i: to_uint16(v) for i, v in
        enumerate([1500, -1500, 0, -1, 3141, -3141])})
    for i, v in enumerate([4817, -1911, 2560, 3140, -2, -1366]):
        d0.tcp_raw[i] = v
    exp = d0.eng_values()
    for fmt in OUT_FORMATS:
        for wo in ("hi_lo", "lo_hi"):
            w = encode_payload(d0, fmt, wo)
            back = decode_payload(w, fmt, wo)
            tol = 0.06 if fmt.startswith("int16") else 0.002
            worst = max(abs(a - b) for a, b in zip(exp, back))
            check(f"{fmt:<13} {wo} 왕복 (워드 {len(w)})",
                  len(w) == OUT_FORMATS[fmt][1] and worst < tol,
                  f"최대오차 {worst:.4f}")
    w = encode_payload(d0, "float32", "hi_lo")
    w2 = encode_payload(d0, "float32", "lo_hi")
    check("워드 순서가 실제로 바뀜", w[0] == w2[1] and w[1] == w2[0],
          f"-> {w[:2]} vs {w2[:2]}")
    check("음수 관절 float 정확", abs(decode_payload(w, "float32")[1]
                                     - (-1500 / 1000 * RAD2DEG)) < 1e-3)

    print("2) robot.py 연동 확인")
    check("Robot_modbus 상속", mio.HAVE_ROBOT_PY,
          "-> robot.py 없음 (단독 모드)" if not mio.HAVE_ROBOT_PY else "-> OK")

    print("3) 가짜 로봇 서버 기동")
    threading.Thread(target=robot_simulator.run,
                     args=("127.0.0.1", ROBOT_PORT, False, True),
                     daemon=True).start()
    time.sleep(1.5)

    print("4) 가짜 외부(수신) 서버 기동")
    ext = mio.ServerBridge(log=lambda m: None)
    check("외부 서버 start", ext.start("127.0.0.1", EXT_PORT))

    print("5) 브리지 구성")
    FMT, WO = "float32", "hi_lo"
    writer = ExternalWriter(on_log=lambda m: print("     W:", m))
    check("writer open", writer.open("127.0.0.1", EXT_PORT, 1, 100,
                                     fmt=FMT, word_order=WO, send_status=True))
    server = ExternalServer(on_log=lambda m: print("     S:", m))
    check("서버 모드 start", server.start("127.0.0.1", SRV_PORT, 200, mirror=True,
                                       fmt=FMT, word_order=WO))
    NW = OUT_FORMATS[FMT][1]

    got = {}

    def on_data(d):
        got["d"] = d
        writer.write(d)
        server.update(d)

    poller = RobotPoller("127.0.0.1", ROBOT_PORT, 1, 100,
                         on_data=on_data, on_log=lambda m: print("     R:", m),
                         on_conn=lambda ok, m: print("     R conn:", ok, m))
    poller.start()
    time.sleep(2.5)

    print("6) 로봇 읽기 확인")
    d = got.get("d")
    check("데이터 수신", d is not None and d.ok)
    if d is None:
        return report()
    print(f"     joints raw = {d.joint_raw}")
    print(f"     joints deg = {[round(x, 2) for x in d.joint_deg]}")
    print(f"     tcp raw    = {d.tcp_raw}")
    print(f"     tcp conv   = {[round(x, 2) for x in d.tcp_conv]}")
    check("관절 범위 (|mRad| <= 1500)", all(abs(v) <= 1501 for v in d.joint_raw))
    check("robot_mode == 7 (RUNNING)", d.robot_mode == 7, f"-> {d.robot_mode}")
    check("running_state == 1", d.running_state == 1)
    check("control_method == 2 (Remote)", d.control_method == 2)
    check("컨트롤러 버전 2.15.0", d.version == (2, 15, 0), f"-> {d.version}")
    check("전류 읽기", d.joint_cur == [500 + i * 37 for i in range(6)],
          f"-> {d.joint_cur}")
    check("온도 읽기", d.joint_tmp == [35 + i for i in range(6)])
    check("TCP X 범위", 3000 <= d.tcp_raw[0] <= 5000, f"-> {d.tcp_raw[0]}")

    print(f"7) 외부 쓰기(Client 모드) 검증 — {FMT}, 외부 서버 addr 100~")
    time.sleep(0.4)
    d2 = got["d"]
    recv = ext.get_hr(100, NW + 8)
    dec = decode_payload(recv[:NW], FMT, WO)
    exp = d2.eng_values()
    print(f"     외부 해석 = {[round(x, 2) for x in dec]}")
    print(f"     로봇 원본 = {[round(x, 2) for x in exp]}")
    check(f"{NW}+8 워드 수신됨", len(recv) >= NW + 8)
    check("디코딩 값 일치",
          max(abs(a - b) for a, b in zip(dec, exp)) < 0.002,
          f"최대오차 {max(abs(a - b) for a, b in zip(dec, exp)):.5f}")
    check("상태워드 전송(+N)", recv[NW] == 7 and recv[NW + 1] == 1,
          f"-> {recv[NW:NW + 8]}")
    check("alive 카운터 동작", recv[NW + 7] > 1, f"-> {recv[NW + 7]}")
    check("tx_count 증가", writer.tx_count > 5, f"-> {writer.tx_count}")

    print("8) 외부 서버(Server 모드) 검증 — 외부 마스터가 read")
    cli = mio.RobotModbus("127.0.0.1", SRV_PORT, unit_id=1)
    check("마스터 연결", cli.connect())
    d3 = got["d"]
    blk = cli.read_hr(200, NW + 8)
    sta = blk[NW:NW + 8]
    dec = decode_payload(blk[:NW], FMT, WO)
    exp = d3.eng_values()
    mir_j = cli.read_hr(MIRROR_UR['joint'], 6)
    mir_t = cli.read_hr(MIRROR_UR['pose'], 6)
    mir_s = cli.read_hr(MIRROR_UR['speed'], 6)
    ireg = cli.client.read_input_registers(200, NW)          # FC4 도 확인
    print(f"     외부 해석 = {[round(x, 2) for x in dec]}")
    print(f"     status    = {sta}")
    check("디코딩 값 일치",
          max(abs(a - b) for a, b in zip(dec, exp)) < 0.002,
          f"최대오차 {max(abs(a - b) for a, b in zip(dec, exp)):.5f}")
    check("status 블록", sta[0] == 7 and sta[1] == 1, f"-> {sta}")
    check("alive 카운터 동작", sta[7] > 1, f"-> {sta[7]}")
    # 로봇이 계속 움직이므로 읽는 시점이 조금씩 다르다.
    # 주소 배선이 맞는지가 목적이므로 한 샘플 분량의 오차는 허용한다.
    def near(got_words, expect, tol, label):
        got_v = [to_int16(v) for v in got_words]
        worst = max(abs(a - b) for a, b in zip(got_v, expect))
        check(label, worst <= tol, f"최대차 {worst} (허용 {tol})\n"
              f"        got    {got_v}\n        expect {expect}")

    near(mir_j, d3.joint_raw, 200, f"미러링 관절 {MIRROR_UR['joint']}~ (INT16 raw)")
    near(mir_t, d3.pose_by_source("scan"), 400, f"미러링 pose {MIRROR_UR['pose']}~")
    

    print("8-a) 서버가 채우는 구간 판정")
    check("pose 구간 인식", server.covers(MIRROR_UR["pose"], 6) is not None)
    check("속도 구간 인식", server.covers(MIRROR_UR["speed"], 6) is not None)
    check("빈 주소는 경고", server.covers(900, 6) is None)
    check("걸쳐진 요청도 경고", server.covers(MIRROR_UR["pose"] + 4, 6) is None)
    # FC3 와 FC4 사이에도 로봇이 움직이므로 값 자체보다 '유효한 값인지' 를 본다
    dec4 = decode_payload(list(ireg), FMT, WO) if ireg else []
    check("FC4(입력 레지스터)도 응답",
          ireg is not None and len(ireg) == NW
          and all(abs(x) <= 200 for x in dec4[:6])
          and all(abs(x) <= 3000 for x in dec4[6:]),
          f"-> {[round(x, 2) for x in dec4]}")

    print("8-b) 갱신 도중에도 값이 찢어지지 않는지 (연속 100회 read)")
    torn = 0
    for _ in range(100):
        b = cli.read_hr(200, NW)
        v = decode_payload(b, FMT, WO)
        # 관절 각도는 ±180deg, TCP 는 ±3000mm 범위를 벗어날 수 없다
        if any(abs(x) > 200 for x in v[:6]) or any(abs(x) > 3000 for x in v[6:]):
            torn += 1
    check("찢어진 값 없음", torn == 0, f"-> {torn}/100")

    print("8-c) INT16 데이터 타입으로 전환해도 동작")
    server.stop()
    check("INT16 서버 재시작",
          server.start("127.0.0.1", SRV_PORT + 10, 0, mirror=False,
                       fmt="int16_scaled", word_order=WO))
    time.sleep(0.4)
    cli2 = mio.RobotModbus("127.0.0.1", SRV_PORT + 10, unit_id=1)
    cli2.connect()
    d4 = got["d"]
    b16 = cli2.read_hr(0, 20)
    dec16 = decode_payload(b16[:12], "int16_scaled", WO)
    check("INT16 scaled 디코딩",
          max(abs(a - b) for a, b in zip(dec16, d4.eng_values())) < 0.06,
          f"-> {[round(x, 2) for x in dec16]}")
    check("INT16 음수 표현 확인",
          all(-32768 <= to_int16(w) <= 32767 for w in b16[:12]))
    cli2.disconnect()

    print("9) 폴링 속도 / 연속성")
    n0 = poller.rx_count
    time.sleep(1.0)
    rate = poller.rx_count - n0
    check("폴링 ~10Hz (100ms)", 7 <= rate <= 12, f"-> {rate} Hz")
    check("로봇 읽기 오류 없음", poller.err_count == 0, f"-> {poller.err_count}")

    print("10) 통신 끊김 복구")
    writer.mb.client.close()                    # 외부 쓰기 소켓 강제 종료
    time.sleep(0.8)
    check("쓰기 오류 후에도 폴링 계속", poller.rx_count > n0 + rate - 2)
    tx0 = writer.tx_count
    time.sleep(1.2)
    check("쓰기 자동 복구", writer.tx_count > tx0, f"-> {tx0} → {writer.tx_count}")

    print("11) 정리")
    poller.stop()
    poller.join(timeout=3)
    check("폴러 종료", not poller.is_alive())
    writer.close(silent=True)
    server.stop()
    ext.stop()
    cli.disconnect()
    check("서버 정지", not server.running)

    report()


def report():
    print("\n" + "=" * 60)
    if fails:
        print(f"실패 {len(fails)}건: {fails}")
        raise SystemExit(1)
    print("전체 통과")


if __name__ == "__main__":
    main()
