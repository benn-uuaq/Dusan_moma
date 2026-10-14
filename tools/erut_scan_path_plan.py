"""3S 검사로봇의 구역 안 ㄹ자 스캔 경로 계산 — ERUT 스캔 계획 그리기용.

RCS 메인 화면 「작업 영역」 그림(operator-ui/src/smr_operator_ui/components/
rect_work_view.py 의 plan_dimensions · ideal_path)과 로봇 태스크
(robot_task/dusan_task_v5/scripts/dus5_init.script)가 쓰는 규칙을 그대로 옮겼다.
표준 라이브러리만 쓴다.

좌표 (구역 하나 안)
    u : 가로 — 원점(구역 왼쪽 끝 = area.start.x)에서 호를 따라 오른쪽으로 잰 거리 [mm]
    v : 세로 — 구역 아래 끝(area.start.y)에서 위로 잰 높이 [mm]
    검사면 좌표로는 x = area.start.x + u, y = area.start.y + v 다(ERUT 규격 탭5 77행 —
    x 가 둘레를 넘으면 x − 둘레로 감는다).

경로가 정해지는 규칙
    1. 줄 사이 간격 수  gaps  = ceil(구역 세로 / scan_h)
       줄 수            rows  = gaps + 1
       피치(상승량)      pitch = 구역 세로 / gaps        (scan_h 를 넘지 않는다)
       줄 안 겹침        overlap = scan_h − pitch        (결과로 나오는 값)
    2. 첫 줄은 아래 끝(v = 0), 마지막 줄은 위 끝(v = 구역 세로)을 **프로브 중심**이
       지난다 — 5축 십자는 첫 줄 ㅗ 자세로 밑면, 마지막 줄 ㅜ 자세로 윗면을 가운데
       프로브가 직접 훑는다. 그래서 줄 위치를 0..세로로 균등 분할한다.
    3. 원점(u = 0, v = 0)에서 오른쪽으로 출발해 구역 가로 끝까지 가고, pitch 만큼
       올라가 왼쪽으로 돌아오기를 반복한다(ㄹ자). 좌우 끝도 프로브 중심 기준이라
       여백 없이 0..구역 가로를 오간다.
    4. 한 줄이 실제로 검사하는 띠는 그 줄의 v 에서 위아래로 scan_h / 2 씩이다.

scan_h (스캔 상승 높이 제한 = 프로브 유효 세로 커버)
    장착한 EOAT 에 따라 다르다. RCS Cobot 설정에서 넣는다(기본 5축 30 mm,
    8축 167.5 mm). 같은 구역이라도 EOAT 가 바뀌면 줄 수가 달라진다.

예) 140 × 140 mm, 5축(scan_h 30) → gaps 5 · rows 6 · pitch 28.0 · overlap 2.0
"""

from __future__ import annotations

from math import asin, ceil, floor, sin

#: EOAT 종류별 기본 스캔 상승 높이 제한(= 유효 세로 커버) [mm]. 현장 값은 RCS 설정.
DEFAULT_SCAN_H_MM = {5: 30.0, 8: 167.5}

#: 로봇 팔이 좌우로 닿을 수 있는 현(두 끝점 사이 직선거리) 한계 [mm].
MAX_PROBE_CHORD_MM = 700.0


def plan_rows(height_mm: float, scan_h_mm: float) -> dict:
    """구역 세로와 scan_h 로 줄 수 · 피치 · 겹침을 낸다.

    반환: {gaps, rows, pitch, overlap, top, covered}
      top     마지막 줄의 v (= 구역 세로)
      covered 검사되는 맨 위 높이 = top + scan_h / 2 (띠가 구역 밖으로 조금 넘친다)
    """
    if scan_h_mm <= 0:
        raise ValueError("scan_h_mm 는 0 보다 커야 한다")
    height = max(float(height_mm), 0.0)
    if height <= 0:
        gaps, rows, pitch = 0, 1, 0.0
    else:
        gaps = int(ceil(height / scan_h_mm))
        rows = gaps + 1
        pitch = height / gaps
    overlap = max(scan_h_mm - pitch, 0.0) if rows > 1 else 0.0
    top = (rows - 1) * pitch
    return {"gaps": gaps, "rows": rows, "pitch": pitch, "overlap": overlap,
            "top": top, "covered": top + scan_h_mm / 2.0}


def scan_path(width_mm: float, height_mm: float, scan_h_mm: float) -> list[tuple[float, float]]:
    """ㄹ자 경로의 꺾이는 점들 [(u, v), ...] — 원점 (0, 0) 에서 시작한다.

    (0,0) → (W,0) → (W,p) → (0,p) → (0,2p) → (W,2p) → … 마지막 줄 끝에서 끝난다.
    가로 이동은 검사면 위 호를 따라가는 거리(펼친 길이)다.
    """
    width = float(width_mm)
    rows = plan_rows(height_mm, scan_h_mm)
    points = [(0.0, 0.0)]
    u, v, rightward = 0.0, 0.0, True
    for i in range(rows["rows"]):
        u = width if rightward else 0.0
        points.append((u, v))                # 한 줄 훑기
        rightward = not rightward
        if i < rows["rows"] - 1:
            v += rows["pitch"]
            points.append((u, v))            # 끝에서 위로 한 피치
    return points


def scan_bands(height_mm: float, scan_h_mm: float) -> list[tuple[float, float]]:
    """줄마다 실제로 검사되는 띠 [(아래 v, 위 v), ...] — 줄의 v ± scan_h / 2."""
    rows = plan_rows(height_mm, scan_h_mm)
    half = scan_h_mm / 2.0
    return [(i * rows["pitch"] - half, i * rows["pitch"] + half) for i in range(rows["rows"])]


def to_surface(points: list[tuple[float, float]], start_x: float, start_y: float,
               circumference_mm: float = 0.0) -> list[tuple[float, float]]:
    """구역 안 (u, v) 를 검사면 좌표 (x, y) 로 옮긴다. 둘레를 주면 이음매에서 감는다."""
    out = []
    for u, v in points:
        x = start_x + u
        if circumference_mm > 0:
            x %= circumference_mm
        out.append((x, start_y + v))
    return out


def chord_mm(width_mm: float, radius_mm: float) -> float:
    """구역 가로(호 길이)를 검사면 반지름 R(프로브가 닿는 바깥면)에서 훑을 때 양 끝 사이
    직선거리 = 2R·sin(W / 2R)."""
    if radius_mm <= 0 or width_mm <= 0:
        return 0.0
    return 2.0 * radius_mm * sin(width_mm / (2.0 * radius_mm))


def max_width_mm(radius_mm: float) -> float:
    """현이 MAX_PROBE_CHORD_MM 를 넘지 않는 최대 구역 가로 = 2R·asin(700 / 2R) (정수 mm 버림).

    로봇이 evt/info 의 area_limit.max_width 로 알리는 값과 같다(지름 1690 → 721).
    반지름을 모르면 0(한계 없음).
    """
    if radius_mm <= 0:
        return 0.0
    ratio = MAX_PROBE_CHORD_MM / (2.0 * radius_mm)
    if ratio >= 1.0:
        return 0.0
    return float(floor(2.0 * radius_mm * asin(ratio)))


if __name__ == "__main__":
    W, H, SCAN_H = 140.0, 140.0, DEFAULT_SCAN_H_MM[5]
    r = plan_rows(H, SCAN_H)
    print(f"{W:g} × {H:g} mm (스캐너 {SCAN_H:g} / 겹침 {r['overlap']:.1f} mm 자동)")
    print(f"피치 {r['pitch']:.1f} mm  {r['rows']} 행")
    for point in scan_path(W, H, SCAN_H):
        print(f"  u {point[0]:7.1f}   v {point[1]:7.1f}")
    print("지름 1690 의 최대 구역 가로:", max_width_mm(845.0), "mm")
