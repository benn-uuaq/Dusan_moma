#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
modbus_spy.py — Modbus TCP 중계 감시기 (투명 프록시)
=====================================================
외부 장치와 로봇(또는 브리지) 사이에 끼워 넣으면, 오가는 모든 Modbus 요청과
응답을 해독해서 기록한다. 장치 프로그램을 고칠 수 없어도 무엇을 어떻게
읽어가는지 전부 볼 수 있고, 통신이 끊기는 순간의 마지막 교신도 남는다.

    [외부 장치 프로그램] --> [modbus_spy (이 PC)] --> [로봇 또는 브리지]

사용법
    # 장치가 로봇을 직접 볼 때: 장치 설정의 IP를 이 PC로, 포트를 5555로 바꾸고
    python modbus_spy.py --listen 5555 --target 192.168.1.101:502

    # 장치가 브리지를 볼 때
    python modbus_spy.py --listen 5555 --target 127.0.0.1:5020

    # 로봇 없이 장치 요청만 보고 싶을 때 (더미 응답을 돌려줌)
    python modbus_spy.py --listen 5555 --fake

Ctrl+C 로 끝내면 요약과 파일이 남는다.
    modbus_spy_YYYYmmdd_HHMMSS.csv          모든 프레임
    modbus_spy_YYYYmmdd_HHMMSS_summary.txt  요약 + 끊기기 직전 교신
"""

import argparse
import csv
import os
import socket
import struct
import sys
import threading
import time
from collections import deque
from datetime import datetime

FC_NAME = {
    1: "FC1 read_coils", 2: "FC2 read_discrete_inputs",
    3: "FC3 read_holding_registers", 4: "FC4 read_input_registers",
    5: "FC5 write_single_coil", 6: "FC6 write_single_register",
    15: "FC15 write_multiple_coils", 16: "FC16 write_multiple_registers",
    23: "FC23 read_write_multiple_registers",
}
EXC_NAME = {
    1: "ILLEGAL FUNCTION", 2: "ILLEGAL DATA ADDRESS", 3: "ILLEGAL DATA VALUE",
    4: "SLAVE DEVICE FAILURE", 5: "ACKNOWLEDGE", 6: "SLAVE DEVICE BUSY",
    8: "MEMORY PARITY ERROR", 10: "GATEWAY PATH UNAVAILABLE",
    11: "GATEWAY TARGET DEVICE FAILED TO RESPOND",
}


def s16(v):
    return v - 0x10000 if v >= 0x8000 else v


# ============================================================================
class Frame:
    """Modbus TCP 프레임 하나."""

    __slots__ = ("ts", "direction", "tid", "pid", "uid", "fc", "exc",
                 "addr", "count", "values", "raw", "note")

    def __init__(self, ts, direction, raw):
        self.ts = ts
        self.direction = direction        # "REQ" (장치→로봇) / "RSP" (로봇→장치)
        self.raw = raw
        self.tid, self.pid, length = struct.unpack(">HHH", raw[:6])
        self.uid = raw[6] if len(raw) > 6 else None
        self.fc = raw[7] if len(raw) > 7 else None
        self.exc = None
        self.addr = None
        self.count = None
        self.values = None
        self.note = ""
        self._parse(raw[8:])

    def _parse(self, body):
        if self.fc is None:
            return
        if self.fc & 0x80:                       # 예외 응답
            self.exc = body[0] if body else None
            self.fc &= 0x7F
            return
        try:
            if self.direction == "REQ":
                if self.fc in (1, 2, 3, 4):
                    self.addr, self.count = struct.unpack(">HH", body[:4])
                elif self.fc in (5, 6):
                    self.addr, val = struct.unpack(">HH", body[:4])
                    self.count, self.values = 1, [val]
                elif self.fc in (15, 16):
                    self.addr, self.count = struct.unpack(">HH", body[:4])
                    nb = body[4]
                    if self.fc == 16:
                        self.values = list(struct.unpack(
                            ">" + "H" * (nb // 2), body[5:5 + nb]))
            else:                                 # 응답
                if self.fc in (3, 4):
                    nb = body[0]
                    self.count = nb // 2
                    self.values = list(struct.unpack(
                        ">" + "H" * self.count, body[1:1 + nb]))
                elif self.fc in (1, 2):
                    nb = body[0]
                    self.values = list(body[1:1 + nb])
                elif self.fc in (5, 6):
                    self.addr, val = struct.unpack(">HH", body[:4])
                    self.values = [val]
                elif self.fc in (15, 16):
                    self.addr, self.count = struct.unpack(">HH", body[:4])
        except Exception as e:                    # noqa: BLE001
            self.note = f"파싱 실패({e})"

    # ------------------------------------------------------------------
    def describe(self, req=None):
        fc = FC_NAME.get(self.fc, f"FC{self.fc}")
        if self.exc is not None:
            return (f"예외응답  {fc}  0x{self.fc | 0x80:02X} "
                    f"예외 {self.exc} = {EXC_NAME.get(self.exc, '?')}")
        if self.direction == "REQ":
            return f"요청  {fc}  addr {self.addr}~{self.addr + self.count - 1} " \
                   f"({self.count}개)"
        # 응답
        where = ""
        if req is not None and req.addr is not None:
            where = f"addr {req.addr}~{req.addr + (self.count or 0) - 1} "
        vals = ""
        if self.values:
            show = self.values[:12]
            vals = "[" + " ".join(str(s16(v)) for v in show) + \
                   (" …" if len(self.values) > 12 else "") + "]"
        return f"응답  {fc}  {where}{vals}"


# ============================================================================
class SpySession:
    """장치 연결 1개에 대한 중계 + 기록."""

    def __init__(self, spy, cli_sock, cli_addr):
        self.spy = spy
        self.cli = cli_sock
        self.cli_addr = f"{cli_addr[0]}:{cli_addr[1]}"
        self.srv = None
        # 이 장치는 모든 요청에 같은 Transaction ID 를 쓴다.
        # 따라서 TID 로는 짝을 못 짓고, 보낸 순서(FIFO)로 맞춰야 한다.
        self.pending = deque()     # [요청 Frame, ...]
        self.inflight = threading.Event()
        self.inflight.set()
        self.alive = True
        self.opened = time.time()
        self.n_req = self.n_rsp = self.n_exc = 0
        self.closed_by = None

    # ------------------------------------------------------------------
    def start(self):
        self.spy.log(f"■ 장치 접속: {self.cli_addr}")
        if not self.spy.fake:
            try:
                self.srv = socket.create_connection(self.spy.target, timeout=5)
                self.srv.settimeout(None)
            except Exception as e:                # noqa: BLE001
                self.spy.log(f"  대상({self.spy.target[0]}:{self.spy.target[1]}) "
                             f"접속 실패: {e}")
                self.cli.close()
                return
        threading.Thread(target=self._pump_client, daemon=True).start()
        if self.srv:
            threading.Thread(target=self._pump_server, daemon=True).start()

    # ------------------------------------------------------------------
    def _read_frames(self, sock, direction):
        """MBAP 길이 필드를 보고 프레임 단위로 잘라서 내놓는다."""
        buf = b""
        while self.alive:
            try:
                chunk = sock.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while len(buf) >= 6:
                length = struct.unpack(">H", buf[4:6])[0]
                total = 6 + length
                if length == 0 or length > 300:      # 깨진 프레임
                    self.spy.log(f"  [경고] 이상한 길이 필드 {length} — "
                                 f"버퍼 초기화 ({buf[:12].hex()})")
                    buf = b""
                    break
                if len(buf) < total:
                    break
                yield buf[:total]
                buf = buf[total:]
        return

    def _pump_client(self):
        try:
            for raw in self._read_frames(self.cli, "REQ"):
                # --serialize: 앞 요청의 응답이 나갈 때까지 다음 요청을 붙잡아 둔다
                if self.spy.serialize:
                    self.inflight.wait(timeout=2.0)
                    self.inflight.clear()
                f = Frame(time.time(), "REQ", raw)
                self.n_req += 1
                self.pending.append(f)
                self.spy.record(self, f, None)
                if self.spy.fake:
                    self._reply_fake(f)
                else:
                    self.srv.sendall(raw)
        except Exception as e:                    # noqa: BLE001
            self.spy.log(f"  장치측 오류: {e}")
        finally:
            self._close("장치(클라이언트)")

    def _pump_server(self):
        try:
            for raw in self._read_frames(self.srv, "RSP"):
                f = Frame(time.time(), "RSP", raw)
                self.n_rsp += 1
                req = self.pending.popleft() if self.pending else None
                if f.exc is not None:
                    self.n_exc += 1
                self.spy.record(self, f, req)
                # --delay: 응답을 일부러 늦춰 장치가 처리할 틈을 준다
                if self.spy.delay_ms:
                    time.sleep(self.spy.delay_ms / 1000.0)
                self.cli.sendall(raw)
                self.inflight.set()
        except Exception as e:                    # noqa: BLE001
            self.spy.log(f"  대상측 오류: {e}")
        finally:
            self._close("대상(로봇/브리지)")

    def _reply_fake(self, f):
        """--fake 모드: 요청한 개수만큼 0 을 돌려준다."""
        if f.fc in (3, 4):
            nb = f.count * 2
            pdu = bytes([f.fc, nb]) + b"\x00" * nb
        elif f.fc in (1, 2):
            nb = (f.count + 7) // 8
            pdu = bytes([f.fc, nb]) + b"\x00" * nb
        elif f.fc in (5, 6, 15, 16):
            pdu = bytes([f.fc]) + f.raw[8:12]
        else:
            pdu = bytes([f.fc | 0x80, 1])
        head = struct.pack(">HHHB", f.tid, 0, len(pdu) + 1, f.uid)
        self.cli.sendall(head + pdu)

    # ------------------------------------------------------------------
    def _close(self, who):
        if not self.alive:
            return
        self.alive = False
        self.closed_by = who
        dur = time.time() - self.opened
        for s in (self.cli, self.srv):
            try:
                if s:
                    s.close()
            except Exception:
                pass
        self.spy.log(f"■ 연결 종료 ({who} 가 먼저 끊음) — {self.cli_addr}  "
                     f"유지 {dur:.1f}초, 요청 {self.n_req} 응답 {self.n_rsp} "
                     f"예외 {self.n_exc}")
        self.spy.on_session_end(self, who, dur)


# ============================================================================
class Spy:
    def __init__(self, listen, target, fake=False, outdir=".", verbose=True,
                 delay_ms=0, serialize=False):
        self.listen = listen
        self.target = target
        self.fake = fake
        self.delay_ms = float(delay_ms)
        self.serialize = bool(serialize)
        self.verbose = verbose
        self.outdir = outdir
        self.rows = []
        self.n_req = 0                      # 요청 누적 (매번 세지 않도록)
        self.recent = deque(maxlen=40)      # 끊기기 직전 교신 보관
        self.req_stat = {}                  # (fc, addr, count) -> 횟수
        self.exc_stat = {}
        self.sessions = []
        self.gaps = []                      # (시각, 간격) 1초 이상 공백
        self._last_req_ts = None
        self._lock = threading.Lock()
        self.started = time.time()
        self.stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.base = os.path.join(self.outdir, f"modbus_spy_{self.stamp}")
        self._csv_fp = None
        self._csv_w = None
        self._stall_saved = False

    # ------------------------------------------------------------------
    def log(self, msg):
        print(msg, flush=True)

    def record(self, sess, f, req):
        with self._lock:
            if f.direction == "REQ":
                self.n_req += 1
                key = (f.fc, f.addr, f.count)
                first = key not in self.req_stat
                self.req_stat[key] = self.req_stat.get(key, 0) + 1
                if self._last_req_ts is not None:
                    gap = f.ts - self._last_req_ts
                    if gap > 1.0:
                        self.gaps.append((f.ts, gap))
                        self.log(f"  [공백] 직전 요청 이후 {gap:.2f}초 동안 "
                                 f"아무 요청이 없었음")
                self._last_req_ts = f.ts
                if first:
                    self.log(f"  ★ 처음 보는 요청: {f.describe()}  "
                             f"(unit {f.uid})")
            elif f.exc is not None:
                k = (f.fc, f.exc)
                self.exc_stat[k] = self.exc_stat.get(k, 0) + 1

            lat = ""
            if f.direction == "RSP" and req is not None:
                lat = f"{(f.ts - req.ts) * 1000:.1f}"

            line = (f"{datetime.fromtimestamp(f.ts).strftime('%H:%M:%S.%f')[:-3]} "
                    f"{sess.cli_addr:>21} {f.direction} tid={f.tid:<5} "
                    f"{f.describe(req)}" + (f"  ({lat}ms)" if lat else ""))
            self.recent.append(line)
            if self.verbose:
                self.log("   " + line)

            row = {
                "time": datetime.fromtimestamp(f.ts).strftime("%H:%M:%S.%f")[:-3],
                "epoch": round(f.ts, 4),
                "client": sess.cli_addr,
                "dir": f.direction,
                "tid": f.tid,
                "unit": f.uid,
                "fc": f.fc,
                "exception": f.exc if f.exc is not None else "",
                "exception_name": EXC_NAME.get(f.exc, "") if f.exc else "",
                "addr": f.addr if f.addr is not None else
                        (req.addr if req and req.addr is not None else ""),
                "count": f.count if f.count is not None else "",
                "latency_ms": lat,
                "values": " ".join(str(s16(v)) for v in (f.values or [])),
                "raw_hex": f.raw.hex(),
                "note": f.note,
            }
            self.rows.append(row)
            self._csv_write(row)

    # ------------------------------------------------------------------
    def _csv_write(self, row):
        """프레임이 생길 때마다 바로 파일에 흘려 쓴다 (도중에 죽어도 남도록)."""
        try:
            if self._csv_fp is None:
                self._csv_fp = open(self.base + ".csv", "w", newline="",
                                    encoding="utf-8-sig")
                self._csv_w = csv.DictWriter(self._csv_fp,
                                             fieldnames=list(row.keys()))
                self._csv_w.writeheader()
            self._csv_w.writerow(row)
            if len(self.rows) % 50 == 0:
                self._csv_fp.flush()
        except Exception:
            pass

    def on_session_end(self, sess, who, dur):
        self.sessions.append({"client": sess.cli_addr, "dur": dur,
                              "req": sess.n_req, "rsp": sess.n_rsp,
                              "exc": sess.n_exc, "closed_by": who,
                              "last": list(self.recent)})

    # ------------------------------------------------------------------
    def heartbeat(self, period=1.0, stall_after=2.0):
        """
        1초마다 상태 한 줄. 요청이 stall_after 초 이상 끊기면 즉시 크게 알리고
        그 자리에서 요약을 저장한다. (Ctrl+C 를 누를 새도 없이 멈추는 경우 대비)
        """
        prev = 0
        prev_t = time.time()
        while True:
            time.sleep(period)
            now = time.time()
            total = self.n_req
            last = self._last_req_ts
            since = (now - last) if last else None
            dt = now - prev_t
            rate = (total - prev) / dt if dt > 0 else 0
            prev, prev_t = total, now

            if total == 0 and not self.sessions:
                self.log(f"  ── {datetime.now().strftime('%H:%M:%S')}  "
                         f"대기 중 — 외부 장치 프로그램의 IP/포트를 "
                         f"이 PC:{self.listen} 로 바꾸세요")
                continue

            stalled = since is not None and since >= stall_after
            if stalled:
                self.log("")
                self.log("  " + "★" * 34)
                self.log(f"  ★ 통신 멈춤 — 마지막 요청 이후 {since:.1f}초 경과")
                self.log(f"  ★ 마지막 요청 시각: "
                         f"{datetime.fromtimestamp(last).strftime('%H:%M:%S.%f')[:-3]}")
                self.log(f"  ★ 그때까지 누적 요청 {total}회")
                self.log("  " + "★" * 34)
                if not self._stall_saved:
                    self._stall_saved = True
                    self.log("  >> 지금 시점의 요약을 저장합니다.")
                    try:
                        self.save(suffix="_STALL")
                    except Exception as e:        # noqa: BLE001
                        self.log(f"  저장 실패: {e}")
                    self.log("  >> 멈추기 직전 마지막 교신 10개:")
                    for line in list(self.recent)[-10:]:
                        self.log("     " + line)
            else:
                if self._stall_saved:
                    self.log(f"  >> 통신이 다시 시작됐습니다 "
                             f"({datetime.now().strftime('%H:%M:%S')})")
                self._stall_saved = False
                self.log(f"  ── {datetime.now().strftime('%H:%M:%S')}  "
                         f"{rate:.0f} 요청/초  누적 {total}  정상")

    # ------------------------------------------------------------------
    def serve(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", self.listen))
        srv.listen(8)
        # Windows 에서는 accept() 에 갇혀 있으면 Ctrl+C 가 전달되지 않는다.
        # 짧은 타임아웃을 걸어 주기적으로 깨어나게 한다.
        srv.settimeout(0.5)
        tgt = "더미 응답(--fake)" if self.fake else f"{self.target[0]}:{self.target[1]}"
        self.log("=" * 74)
        self.log(f"Modbus 감시 시작 — 0.0.0.0:{self.listen}  →  {tgt}")
        if self.delay_ms:
            self.log(f"응답 지연 {self.delay_ms:.0f}ms 적용")
        if self.serialize:
            self.log("요청 직렬화 적용 (한 번에 하나씩만 진행)")
        self.log("외부 장치 프로그램의 IP/포트를 이 PC 와 위 포트로 바꾸세요.")
        self.log("Ctrl+C 로 종료하면 요약과 파일이 저장됩니다.")
        self.log("통신이 멈추면 그 자리에서 *_STALL_summary.txt 를 자동 저장합니다.")
        self.log("=" * 74)
        threading.Thread(target=self.heartbeat, daemon=True).start()
        while True:
            try:
                cli, addr = srv.accept()
            except socket.timeout:
                continue                       # Ctrl+C 를 받을 틈을 준다
            except OSError:
                break
            cli.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            SpySession(self, cli, addr).start()

    # ------------------------------------------------------------------
    def save(self, suffix=""):
        if not self.rows:
            self.log("\n기록된 프레임이 없습니다.")
            return
        base = self.base + suffix
        try:
            if self._csv_fp:
                self._csv_fp.flush()
        except Exception:
            pass

        lines = []
        A = lines.append
        A("=" * 70)
        A("Modbus 감시 요약")
        A("=" * 70)
        A(f"기록 시간   : {time.time() - self.started:.1f} 초")
        A(f"대상        : {'더미' if self.fake else '%s:%d' % self.target}")
        A(f"프레임 수   : {len(self.rows)}")

        A("")
        A("[외부 장치가 보낸 요청 종류]")
        A(f"  {'기능코드':<32} {'주소':>7} {'개수':>5} {'횟수':>7}")
        for (fc, addr, count), n in sorted(self.req_stat.items(),
                                           key=lambda kv: -kv[1]):
            A(f"  {FC_NAME.get(fc, 'FC%s' % fc):<32} {addr!s:>7} "
              f"{count!s:>5} {n:>7}")

        A("")
        A("[예외 응답]")
        if not self.exc_stat:
            A("  없음")
        for (fc, exc), n in sorted(self.exc_stat.items(), key=lambda kv: -kv[1]):
            A(f"  {FC_NAME.get(fc, 'FC%s' % fc)} → 예외 {exc} "
              f"{EXC_NAME.get(exc, '?')} : {n}회")

        A("")
        A("[요청이 1초 이상 끊긴 구간]")
        if not self.gaps:
            A("  없음")
        for t, g in self.gaps[:30]:
            A(f"  {datetime.fromtimestamp(t).strftime('%H:%M:%S')}  "
              f"{g:.2f}초 공백")

        A("")
        A("[연결 이력]")
        for i, s in enumerate(self.sessions, 1):
            A(f"  {i}. {s['client']}  유지 {s['dur']:.1f}초  "
              f"요청 {s['req']} 응답 {s['rsp']} 예외 {s['exc']}  "
              f"→ {s['closed_by']} 가 먼저 끊음")

        # 응답 지연 통계
        lat = [float(r["latency_ms"]) for r in self.rows if r["latency_ms"]]
        if lat:
            lat_sorted = sorted(lat)
            A("")
            A("[응답 지연]")
            A(f"  평균 {sum(lat) / len(lat):.1f}ms  "
              f"중앙 {lat_sorted[len(lat_sorted) // 2]:.1f}ms  "
              f"최대 {max(lat):.1f}ms")
            slow = [r for r in self.rows
                    if r["latency_ms"] and float(r["latency_ms"]) > 500]
            if slow:
                A(f"  500ms 넘은 응답 {len(slow)}회:")
                for r in slow[:10]:
                    A(f"    {r['time']} addr {r['addr']} {r['latency_ms']}ms")

        # 응답 없이 버려진 요청
        A("")
        A("[응답을 못 받은 요청]")
        req_n = sum(1 for r in self.rows if r["dir"] == "REQ")
        rsp_n = sum(1 for r in self.rows if r["dir"] == "RSP")
        A(f"  요청 {req_n} / 응답 {rsp_n} → 무응답 {req_n - rsp_n}건")

        A("")
        A("=" * 70)
        A("끊기기 직전 교신 (최근 40개)")
        A("=" * 70)
        last = self.sessions[-1]["last"] if self.sessions else list(self.recent)
        for line in last:
            A("  " + line)

        # 자동 진단
        A("")
        A("=" * 70)
        A("자동 진단")
        probs = []
        if self.exc_stat:
            for (fc, exc), n in self.exc_stat.items():
                probs.append(f"{FC_NAME.get(fc, fc)} 요청이 {n}회 거절됨 "
                             f"(예외 {exc} {EXC_NAME.get(exc, '')})")
        if self.sessions:
            byc = {}
            for s in self.sessions:
                byc[s["closed_by"]] = byc.get(s["closed_by"], 0) + 1
            for who, n in byc.items():
                probs.append(f"연결이 {n}회 끊겼고, 먼저 끊은 쪽은 '{who}'")
            short = [s for s in self.sessions if s["dur"] < 120]
            if short:
                probs.append(f"2분 안에 끊긴 연결이 {len(short)}건 "
                             f"(최단 {min(s['dur'] for s in short):.1f}초)")
        if req_n - rsp_n > 0:
            probs.append(f"응답을 못 받은 요청이 {req_n - rsp_n}건 — "
                         f"대상이 응답을 안 보내거나 도중에 끊었다")
        if lat and max(lat) > 1000:
            probs.append(f"응답이 최대 {max(lat):.0f}ms 걸림 — "
                         f"장치 타임아웃보다 길면 장치가 포기한다")
        if not probs:
            A("  뚜렷한 이상 없음")
        for p in probs:
            A(f"  - {p}")

        txt = "\n".join(lines)
        with open(base + "_summary.txt", "w", encoding="utf-8") as fp:
            fp.write(txt + "\n")
        if not suffix:
            self.log("\n" + txt)
        self.log(f"\n저장: {self.base}.csv")
        self.log(f"저장: {base}_summary.txt")


# ============================================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", type=int, default=5555, help="장치가 붙을 포트")
    ap.add_argument("--target", default="", help="실제 대상 host:port")
    ap.add_argument("--fake", action="store_true",
                    help="대상 없이 더미 응답(0)을 돌려주며 요청만 관찰")
    ap.add_argument("--quiet", action="store_true", help="매 프레임 출력 안 함")
    ap.add_argument("--delay", type=float, default=0,
                    help="응답을 이 밀리초만큼 늦춰서 전달 (장치가 너무 빠른 응답을 "
                         "처리 못 하는지 시험할 때)")
    ap.add_argument("--serialize", action="store_true",
                    help="앞 요청의 응답이 나가기 전에는 다음 요청을 보내지 않음 "
                         "(장치가 요청을 겹쳐 보내 꼬이는지 시험할 때)")
    ap.add_argument("--outdir", default=os.path.dirname(os.path.abspath(__file__)))
    a = ap.parse_args()

    target = None
    if not a.fake:
        if not a.target:
            ap.error("--target host:port 를 지정하거나 --fake 를 쓰세요")
        host, _, port = a.target.partition(":")
        target = (host, int(port or 502))

    spy = Spy(a.listen, target, a.fake, a.outdir, verbose=not a.quiet,
              delay_ms=a.delay, serialize=a.serialize)
    try:
        spy.serve()
    except KeyboardInterrupt:
        print("\n중지 요청됨 — 요약을 만드는 중…")
    finally:
        spy.save()


if __name__ == "__main__":
    main()
