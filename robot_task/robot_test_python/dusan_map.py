#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dusan_map.py — dusan_v1 태스크의 Modbus 레지스터 맵 + pose 수학

로봇 태스크는 자기 Modbus 서버의 범용 레지스터 256~383 을
write_port_register / read_port_register 로 직접 읽고 쓴다.
외부(이 PC)에서는 홀딩 레지스터(FC3/FC6/FC16)로 같은 주소를 본다.
주소는 펜던트에 등록된 신호 이름(tcp_x=260.., zero_x=270..)에 맞춰 배치했다.
"""

import math

# --------------------------------------------------------------- 입력 (외부 -> 로봇)
R_IN_WIDTH   = 256   # 사각형 좌우 너비 [mm]
R_IN_HEIGHT  = 257   # 사각형 높이 [mm]
R_IN_SCANH   = 258   # 스캐너 세로 유효높이 [mm]
R_IN_OVERLAP = 259   # 세로 겹침 [mm]
R_IN_SRC     = 266   # 0 = 로봇 변수값 사용, 1 = 256~259 레지스터 사용

# --------------------------------------------------------------- 출력 (로봇 -> 외부)
# 회전 성분은 로봇 자체 레지스터 384~389 와 같은 mRad 를 쓴다.
R_TCP   = 260        # 260~262 절대 TCP X,Y,Z [0.1mm] / 263~265 Rx,Ry,Rz [mRad]
R_ZERO  = 270        # 270~272 원점 pose X,Y,Z [0.1mm] / 273~275 Rx,Ry,Rz [mRad]
R_REL   = 280        # 280~282 원점기준 상대 X,Y,Z [0.1mm] / 283~285 Rx,Ry,Rz [mRad]
R_STATE = 290        # 0 대기 / 1 원점탐색 / 4 스캔중 / 5 완료 / 9 오류
R_ROW   = 291        # 완료한 가로 패스 수
R_ROWS  = 292        # 전체 가로 패스 수
R_ALIVE = 293        # 워치독 카운터 0~30000
R_ZOK   = 294        # 원점 확정 0/1
R_DONE  = 295        # 스캔 완료 0/1
R_PITCH = 296        # 상승 피치 = scan_h - overlap [mm]
R_PATH  = 297        # 누적 이동거리 [cm]
R_PROG  = 298        # 진행률 [%]

BLOCK_START = 256
BLOCK_COUNT = 64     # 256~319 를 한 번에 읽는다

STATE_TXT = {0: "대기", 1: "원점탐색", 2: "3점측정", 3: "원점복귀",
             4: "스캔중", 5: "완료", 9: "오류"}

INPUT_FIELDS = [
    ("app_width",  R_IN_WIDTH,   "너비",       "mm", 500),
    ("app_height", R_IN_HEIGHT,  "높이",       "mm", 500),
    ("scan_h",     R_IN_SCANH,   "스캐너 높이", "mm", 200),
    ("overlap",    R_IN_OVERLAP, "겹침",       "mm", 10),
]


def to_signed(v):
    v = int(v) & 0xFFFF
    return v - 0x10000 if v >= 0x8000 else v


def to_unsigned(v):
    return int(round(v)) & 0xFFFF


def _pose6(regs, base):
    """returns (mm 3개, deg 3개). 레지스터 원본은 0.1mm 와 mRad 다."""
    g = lambda a: to_signed(regs.get(a, 0))                    # noqa: E731
    d = 180.0 / math.pi / 1000.0                               # mRad -> deg
    return ([g(base) / 10.0, g(base + 1) / 10.0, g(base + 2) / 10.0],
            [g(base + 3) * d, g(base + 4) * d, g(base + 5) * d])


def decode(regs):
    """regs: {주소: 원시 16bit} -> 사람이 읽는 값"""
    g = lambda a: to_signed(regs.get(a, 0))                    # noqa: E731
    u = lambda a: int(regs.get(a, 0))                          # noqa: E731
    tcp_mm, tcp_deg = _pose6(regs, R_TCP)
    zero_mm, zero_deg = _pose6(regs, R_ZERO)
    rel_mm, rel_deg = _pose6(regs, R_REL)
    return dict(
        tcp_mm=tcp_mm, tcp_deg=tcp_deg,
        zero_mm=zero_mm, zero_deg=zero_deg,
        rel_mm=rel_mm, rel_deg=rel_deg,
        path_mm=g(R_PATH) * 10.0,
        progress=g(R_PROG),
        state=u(R_STATE),
        row=g(R_ROW),
        rows=g(R_ROWS),
        alive=u(R_ALIVE),
        zero_ok=bool(u(R_ZOK)),
        done=bool(u(R_DONE)),
        pitch_mm=g(R_PITCH),
        param=dict(app_width=g(R_IN_WIDTH), app_height=g(R_IN_HEIGHT),
                   scan_h=g(R_IN_SCANH), overlap=g(R_IN_OVERLAP),
                   param_src=g(R_IN_SRC)),
    )


def plan(app_width, app_height, scan_h, overlap):
    """태스크 init 과 동일한 행 수 / 피치 계산.

    상승 피치는 항상 (스캐너 높이 - 겹침) 고정이다. 균등 재분배하지 않는다.
    스캐너가 한 자리에서 scan_h 만큼을 훑으므로, 마지막 패스 위치가
    app_height - scan_h 이상이면 사각형 전체가 덮인다.
    """
    if app_width <= 0:
        app_width = 500
    if app_height < 0:
        app_height = 0
    if scan_h <= 0:
        scan_h = 200
    if overlap < 0:
        overlap = 0
    if overlap >= scan_h:
        overlap = scan_h - 10
    pitch = scan_h - overlap
    if pitch <= 0:
        pitch = 10
    rows = 1
    if app_height > scan_h:
        while (rows - 1) * pitch + scan_h < app_height:
            rows += 1
    top = (rows - 1) * pitch                 # 마지막 패스의 높이
    return dict(pitch=pitch, rows=rows, top=top,
                covered=top + scan_h,        # 실제 커버되는 상단
                path_len=rows * app_width + top)


def ideal_path(app_width, app_height, scan_h, overlap):
    """원점(0,0) 기준 이상적인 ㄹ자 경로 [(가로 mm, 세로 mm), ...].
    가로 + = 오른쪽(로봇 베이스 Y-), 세로 + = 위(베이스 Z+)."""
    p = plan(app_width, app_height, scan_h, overlap)
    pts = [(0.0, 0.0)]
    h, v, sign = 0.0, 0.0, 1
    for i in range(p["rows"]):
        h = app_width if sign > 0 else 0.0
        pts.append((h, v))
        sign = -sign
        if i < p["rows"] - 1:
            v += p["pitch"]
            pts.append((h, v))
    return pts


# =============================================================== pose 수학
# Elite/UR 계열 pose = [x, y, z, rx, ry, rz] (m, 회전벡터 rad)

def rotvec_to_mat(rx, ry, rz):
    th = math.sqrt(rx * rx + ry * ry + rz * rz)
    if th < 1e-12:
        return [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    kx, ky, kz = rx / th, ry / th, rz / th
    c, s = math.cos(th), math.sin(th)
    v = 1 - c
    return [
        [kx * kx * v + c,      kx * ky * v - kz * s, kx * kz * v + ky * s],
        [ky * kx * v + kz * s, ky * ky * v + c,      ky * kz * v - kx * s],
        [kz * kx * v - ky * s, kz * ky * v + kx * s, kz * kz * v + c],
    ]


def mat_to_rotvec(R):
    c = max(-1.0, min(1.0, (R[0][0] + R[1][1] + R[2][2] - 1) / 2.0))
    th = math.acos(c)
    if th < 1e-9:
        return (0.0, 0.0, 0.0)
    if abs(math.pi - th) < 1e-6:                      # 180도 근처 특이점
        d = [R[0][0], R[1][1], R[2][2]]
        i = d.index(max(d))
        k = [0.0, 0.0, 0.0]
        k[i] = math.sqrt(max(0.0, (R[i][i] + 1) / 2.0))
        j, l = (i + 1) % 3, (i + 2) % 3
        if k[i] > 1e-12:
            k[j] = (R[i][j] + R[j][i]) / (4 * k[i])
            k[l] = (R[i][l] + R[l][i]) / (4 * k[i])
        return (k[0] * th, k[1] * th, k[2] * th)
    s = 2 * math.sin(th)
    return ((R[2][1] - R[1][2]) / s * th,
            (R[0][2] - R[2][0]) / s * th,
            (R[1][0] - R[0][1]) / s * th)


def pose_inv(p):
    R = rotvec_to_mat(p[3], p[4], p[5])
    Rt = [[R[j][i] for j in range(3)] for i in range(3)]
    t = [-sum(Rt[i][j] * p[j] for j in range(3)) for i in range(3)]
    rv = mat_to_rotvec(Rt)
    return [t[0], t[1], t[2], rv[0], rv[1], rv[2]]


def pose_trans(a, b):
    """a 좌표계에서 본 b -> 베이스 기준 pose (URScript pose_trans 와 동일)"""
    Ra = rotvec_to_mat(a[3], a[4], a[5])
    Rb = rotvec_to_mat(b[3], b[4], b[5])
    t = [a[i] + sum(Ra[i][j] * b[j] for j in range(3)) for i in range(3)]
    R = [[sum(Ra[i][k] * Rb[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    rv = mat_to_rotvec(R)
    return [t[0], t[1], t[2], rv[0], rv[1], rv[2]]


def relative_to_zero(zero_pose, cur_pose):
    """로봇 태스크의 pose_trans(pose_inv(zero_pose), cur) 과 같은 계산.
    반환: (x_mm, y_mm, z_mm, rx_deg, ry_deg, rz_deg)"""
    r = pose_trans(pose_inv(zero_pose), cur_pose)
    d = 180.0 / math.pi
    return (r[0] * 1000, r[1] * 1000, r[2] * 1000, r[3] * d, r[4] * d, r[5] * d)


# =============================================================== 벽면 좌표 변환
# 상대좌표는 "원점에서의 TCP 좌표계" 기준이라 베이스축과 축이 다르다.
# rel_tcp = R_zero^T (p_cur - p_zero) 이므로, R_zero 를 다시 곱하면 베이스 기준
# 변위가 그대로 나온다. 거기서 가로 = -Y, 세로 = +Z 를 취하면 끝이다.
# 툴 자세가 벽과 정렬돼 있든 아니든 항상 정확하다(추정이 아니다).

def axis_map(zero_pose):
    """to_wall 에 넘길 회전행렬. zero_pose 의 자세만 쓴다."""
    return rotvec_to_mat(zero_pose[3], zero_pose[4], zero_pose[5])


DEFAULT_AMAP = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]


def to_wall(rel_mm, R):
    """TCP 좌표계 상대변위 -> (가로 mm, 세로 mm). 가로 + = 오른쪽, 세로 + = 위."""
    h, v, _ = to_wall3(rel_mm, R)
    return h, v


def to_wall3(rel_mm, R):
    """TCP 좌표계 상대변위 -> (가로, 세로, 깊이) mm.

    로봇이 발행하는 상대좌표는 "원점에서의 TCP 좌표계" 기준이라
    베이스축과 축이 섞여 있다. 원점 자세 R 을 다시 곱하면 베이스 기준
    변위가 그대로 나오고, 거기서 읽으면 된다.
        가로 = -Y (오른쪽이 +)
        세로 = +Z (위가 +)
        깊이 = +X (벽 쪽이 +)
    추정이 아니라 정확한 역변환이라 툴 자세와 무관하게 항상 맞는다.
    """
    base = [sum(R[i][j] * rel_mm[j] for j in range(3)) for i in range(3)]
    return -base[1], base[2], base[0]


def tool_axis_hint(R):
    """툴이 벽을 얼마나 정면으로 보고 있는지 한 줄 설명 (참고용)."""
    # 베이스 Y-(오른쪽), Z+(위) 가 TCP 좌표계에서 어느 축에 실리는지
    names = []
    for label, e in (("가로", (0.0, -1.0, 0.0)), ("세로", (0.0, 0.0, 1.0))):
        v = [sum(R[j][i] * e[j] for j in range(3)) for i in range(3)]
        i = max(range(3), key=lambda k: abs(v[k]))
        names.append(f"{label}≈{'-' if v[i] < 0 else '+'}{AXIS_NAME[i]}({abs(v[i]):.2f})")
    return "  ".join(names)


AXIS_NAME = ["X", "Y", "Z"]
