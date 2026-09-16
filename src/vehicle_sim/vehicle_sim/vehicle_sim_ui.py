"""차량 모의기 화면 — 움직이는 모습을 눈으로 본다.

`vehicle_sim_node` 의 모의 차량을 그대로 돌리면서, 그 상태를 Tk 창에 그린다.

  바퀴       주행 중이면 돈다(속도에 비례, 전진·후진 방향대로)
  아웃트리거  다리 3개가 내려가고 올라간다(개별 상승·하강도 보인다)
  리프트     기둥 위 발판이 lift_h 만큼 오르내린다
  경광등     빨강(오류) · 노랑(움직이는 중) · 초록(정지·대기) 3색

실행:
    ros2 run vehicle_sim vehicle_sim_ui
    ros2 launch vehicle_sim vehicle_sim.launch.py ui:=true

ROS 는 배경 스레드에서 돌고 Tk 는 주 스레드에서 돈다. 화면은 노드의
`snapshot()` 을 주기적으로 읽기만 한다(쓰지 않는다).
"""

from __future__ import annotations

import math
import threading
import tkinter as tk
from tkinter import ttk

import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.parameter import Parameter

from vehicle_sim.vehicle_sim_node import VehicleSim

BG = "#11161C"
BODY = "#2E6DA4"
BODY_EDGE = "#8CC1EA"
GROUND = "#3A4750"
DARK = "#333C44"
TEXT = "#E6EDF3"

#: 경광등 색 (꺼짐, 켜짐)
LAMPS = (("#5A1B1B", "#FF3B30"), ("#5A4A15", "#FFCC00"), ("#17441F", "#34C759"))


def use_visible_cursor(root: tk.Tk) -> None:
    """WSLg 에서 Tk 창 위 마우스 포인터가 사라지지 않게 직접 지정한다."""
    root.configure(cursor="left_ptr")
    root.option_add("*Toplevel.cursor", "left_ptr")
    root.option_add("*Dialog.cursor", "left_ptr")


class VehicleSimUI:
    W, H = 900, 470
    TICK_MS = 50

    def __init__(self, root: tk.Tk, node: VehicleSim) -> None:
        self.root = root
        self.node = node
        self.wheel_angle = 0.0
        self.blink = 0.0
        root.title(f"차량 모의기 — {node.get_parameter('namespace').value}")
        root.configure(bg=BG)

        top = ttk.Frame(root, padding=8)
        top.pack(fill=tk.X)
        self.state_var = tk.StringVar(value="-")
        ttk.Label(top, textvariable=self.state_var, font=("Sans", 11, "bold")).pack(side=tk.LEFT)
        ttk.Button(top, text="오류 주입", command=lambda: self._fault(21)).pack(side=tk.RIGHT, padx=3)
        ttk.Button(top, text="오류 해제", command=lambda: self._fault(0)).pack(side=tk.RIGHT, padx=3)
        ttk.Button(top, text="정지", command=self._stop).pack(side=tk.RIGHT, padx=3)

        self.canvas = tk.Canvas(root, width=self.W, height=self.H, bg=BG, highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.info_var = tk.StringVar(value="")
        ttk.Label(root, textvariable=self.info_var, font=("Sans", 10)).pack(fill=tk.X, padx=10, pady=(0, 8))

        root.after(self.TICK_MS, self._tick)

    # ------------------------------------------------------------ 조작
    def _fault(self, code: int) -> None:
        self.node.set_parameters([Parameter("fault_code", value=int(code))])
        if code == 0:
            self.node.error_code, self.node.error_msg = 0, ""
            if self.node.state == "ERROR":
                self.node.state = "HOLD" if self.node.hold == "SET" else "STOP"

    def _stop(self) -> None:
        self.node._halt()

    # ------------------------------------------------------------ 그리기
    def _tick(self) -> None:
        snap = self.node.snapshot()
        dt = self.TICK_MS / 1000.0
        self.blink = (self.blink + dt) % 1.0
        # 바퀴는 실제 속도(m/s)로 돌린다. 반지름 0.15 m 로 보고 rad/s 환산.
        direction = 1.0 if (snap["jog"][0] >= 0 and snap["set_dist"] >= 0) else -1.0
        self.wheel_angle += snap["speed"] / 0.15 * dt * direction
        self._draw(snap)
        self.root.after(self.TICK_MS, self._tick)

    def _draw(self, s: dict) -> None:
        c = self.canvas
        c.delete("all")
        ground_y = 400
        c.create_rectangle(0, ground_y, self.W, self.H, fill=GROUND, outline="")
        for x in range(0, self.W, 40):     # 바닥 무늬 — 차가 서 있는 느낌
            c.create_line(x, ground_y, x + 20, ground_y + 10, fill="#2C363E")

        body_l, body_r, body_t, body_b = 210, 660, 300, 356
        c.create_rectangle(body_l, body_t, body_r, body_b, fill=BODY, outline=BODY_EDGE, width=2)
        c.create_text((body_l + body_r) / 2, (body_t + body_b) / 2, text="AMR",
                      fill="#DCEAF7", font=("Sans", 13, "bold"))

        self._draw_wheels(body_l, body_r, body_b, s)
        self._draw_outriggers(body_l, body_r, body_b, ground_y, s)
        self._draw_lift(body_l, body_r, body_t, s)
        self._draw_beacon(body_l + 46, body_t, s)
        self._draw_drive(body_r, body_t, s)
        self._text(s)

    def _draw_wheels(self, left: float, right: float, bottom: float, s: dict) -> None:
        c = self.canvas
        radius = 26
        for cx in (left + 60, right - 60):
            cy = bottom + radius - 4
            c.create_oval(cx - radius, cy - radius, cx + radius, cy + radius,
                          fill="#1A2026", outline="#98A6B3", width=3)
            for i in range(6):            # 돌아가는 스포크
                angle = self.wheel_angle + i * math.pi / 3
                c.create_line(cx, cy, cx + math.cos(angle) * (radius - 5),
                              cy + math.sin(angle) * (radius - 5), fill="#7FD1FF", width=3)
            c.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, fill="#98A6B3", outline="")

    def _draw_outriggers(self, left: float, right: float, bottom: float,
                         ground_y: float, s: dict) -> None:
        c = self.canvas
        reach = ground_y - bottom            # 다 내리면 바닥에 닿는다
        # 바퀴(left+60, right-60)와 겹치지 않게 앞·가운데·뒤에 세운다.
        spots = (left + 16, (left + right) / 2, right - 16)
        for i, ratio in enumerate(s["legs"]):
            x = spots[i]
            length = max(6.0, reach * ratio)
            color = "#FFCC00" if s["outrigger_moving"] else ("#34C759" if ratio > 0.99 else "#8CC1EA")
            c.create_rectangle(x - 7, bottom, x + 7, bottom + length, fill=DARK, outline=color, width=2)
            c.create_rectangle(x - 22, bottom + length, x + 22, bottom + length + 9,
                               fill=color, outline="")
            c.create_text(x, bottom + length + 24, text=f"{i + 1}", fill=TEXT, font=("Sans", 9))

    def _draw_lift(self, left: float, right: float, top: float, s: dict) -> None:
        c = self.canvas
        mast_x = (left + right) / 2
        mast_top, mast_bottom = 70, top
        c.create_rectangle(mast_x - 12, mast_top, mast_x + 12, mast_bottom,
                           fill="#1A2026", outline="#5A6A78")
        travel = mast_bottom - mast_top - 40
        ratio = 0.0 if s["lift_max"] <= 0 else min(1.0, s["lift_h"] / s["lift_max"])
        y = mast_bottom - 20 - travel * ratio
        color = "#FFCC00" if s["lift_moving"] else "#8CC1EA"
        c.create_rectangle(mast_x - 90, y - 12, mast_x + 90, y + 12, fill=BODY, outline=color, width=3)
        c.create_rectangle(mast_x - 34, y - 52, mast_x + 34, y - 12, fill="#24303A",
                           outline="#6F8496", width=2)
        c.create_text(mast_x, y - 32, text="로봇", fill=TEXT, font=("Sans", 10))
        c.create_text(mast_x + 130, y, text=f"{s['lift_h'] * 1000:,.0f} mm", fill=color,
                      font=("Sans", 12, "bold"))

    def _draw_beacon(self, x: float, top: float, s: dict) -> None:
        c = self.canvas
        moving = (abs(s["speed"]) > 1e-6 or s["lift_moving"] or s["outrigger_moving"])
        error = s["state"] == "ERROR"
        on = [False, False, False]
        if error:
            on[0] = self.blink < 0.65                     # 빨강 깜빡 — 오류
        elif moving:
            on[1] = self.blink < 0.65                     # 노랑 깜빡 — 움직이는 중
        elif s["state"] == "PAUSED" or s["hold"] == "SET":
            # 아웃트리거를 내리고 서 있는 동안(로봇이 검사 중)도 '작업 중'이다.
            on[1] = True                                  # 노랑 켜짐
        else:
            on[2] = True                                  # 초록 켜짐 — 정지·대기
        c.create_rectangle(x - 4, top - 96, x + 4, top, fill="#5A6A78", outline="")
        for i, (off_color, on_color) in enumerate(LAMPS):
            cy = top - 88 + i * 26
            color = on_color if on[i] else off_color
            c.create_oval(x - 15, cy - 11, x + 15, cy + 11, fill=color, outline="#1A2026", width=2)
            if on[i]:                                     # 켜진 등에 빛 번짐
                c.create_oval(x - 22, cy - 18, x + 22, cy + 18, outline=on_color)

    def _draw_drive(self, right: float, top: float, s: dict) -> None:
        if abs(s["speed"]) < 1e-6:
            return
        c = self.canvas
        forward = (s["jog"][0] > 0) or (s["jog"][0] == 0 and s["set_dist"] >= 0)
        x0, x1 = (right + 30, right + 110) if forward else (right + 110, right + 30)
        y = top - 20
        c.create_line(x0, y, x1, y, fill="#FFCC00", width=5, arrow=tk.LAST, arrowshape=(16, 20, 7))
        c.create_text((x0 + x1) / 2, y - 18, text=f"{s['speed']:.2f} m/s", fill="#FFCC00",
                      font=("Sans", 10, "bold"))

    def _text(self, s: dict) -> None:
        hold = {"SET": "고정", "RELEASE": "해제"}.get(s["hold"], s["hold"])
        error = "정상" if not s["error_code"] else f"{s['error_code']} {s['error_msg']}"
        self.state_var.set(f"상태 {s['state']}   아웃트리거 {hold}   오류 {error}")
        self.info_var.set(
            f"작업 {s['job_id'] or '-'}    이동 {s['mv_dist']:.3f} / {s['set_dist']:.3f} m    "
            f"속도 {s['speed']:.2f} m/s    누적 {s['odometer']:.3f} m    "
            f"리프트 {s['lift_h'] * 1000:,.0f} mm    다리 "
            + " ".join(f"{v * 100:3.0f}%" for v in s["legs"]))


def main() -> None:
    rclpy.init()
    node = VehicleSim()
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    spinner = threading.Thread(target=executor.spin, daemon=True)
    spinner.start()
    root = tk.Tk()
    use_visible_cursor(root)
    VehicleSimUI(root, node)
    try:
        root.mainloop()
    finally:
        # 실행기를 내린 뒤 스레드가 실제로 끝나기를 기다린다 — 안 기다리고
        # 노드를 지우면 종료할 때 "terminate called without an active exception"
        # 으로 죽는다.
        executor.shutdown()
        spinner.join(timeout=2.0)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
