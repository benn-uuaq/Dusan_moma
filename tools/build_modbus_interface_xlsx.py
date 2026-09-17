"""Elite 협동로봇(CS612) <-> RCS 모드버스 인터페이스 표를 만든다.

모드버스만 정리한다(29999 대시보드·30001 스크립트는 여기 넣지 않는다).

  1. 전송속도     로봇 쪽 / 브리지 쪽 주기·지연·실시간 적합성
  2. 로봇 레지스터 Elite 주소별 표
  3. 브리지 레지스터 브리지가 TPAC 에 내주는 자리 (TCP·스캔 좌표만)

출처: src/elite_robot_controller/config/modbus_registers.json,
      robot_task/dusan_task_v4·v5/scripts/*.script,
      operator-ui/.../services/tpac_bridge/{bridge_core,robot_map}.py

실행:  python3 tools/build_modbus_interface_xlsx.py
"""

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "3S-Elite_Modbus_인터페이스_20260916.xlsx"

FONT = "맑은 고딕"
HEAD_FILL = PatternFill("solid", fgColor="FF2F5496")
SECT_FILL = PatternFill("solid", fgColor="FFD6E4F0")
NEW_FILL = PatternFill("solid", fgColor="FFFFF2CC")      # 새로 만든 주소
HEAD_FONT = Font(name=FONT, size=10, bold=True, color="FFFFFFFF")
SECT_FONT = Font(name=FONT, size=10, bold=True, color="FF1F3864")
BODY_FONT = Font(name=FONT, size=9)
BOLD_BODY = Font(name=FONT, size=9, bold=True)
TITLE_FONT = Font(name=FONT, size=13, bold=True, color="FF1F3864")
NOTE_FONT = Font(name=FONT, size=9, color="FF595959")
EDGE = Side(style="thin", color="FFBFBFBF")
BORDER = Border(left=EDGE, right=EDGE, top=EDGE, bottom=EDGE)
LEFT = Alignment(wrap_text=True, vertical="center", horizontal="left")
CENTER = Alignment(wrap_text=True, vertical="center", horizontal="center")


def sheet(wb, name, title, note, headers, widths):
    ws = wb.create_sheet(name)
    ws.cell(row=1, column=1, value=title).font = TITLE_FONT
    cell = ws.cell(row=2, column=1, value=note)
    cell.font, cell.alignment = NOTE_FONT, LEFT
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(headers))
    ws.row_dimensions[2].height = 28
    for col, (head, width) in enumerate(zip(headers, widths), start=1):
        c = ws.cell(row=4, column=col, value=head)
        c.font, c.fill, c.alignment, c.border = HEAD_FONT, HEAD_FILL, CENTER, BORDER
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.row_dimensions[4].height = 26
    ws.freeze_panes = ws.cell(row=5, column=1)
    return ws


def rows(ws, data, ncols, start=5, left_from=3):
    """("section", 제목) / ("fill", 색, 값들...) / (값들...)"""
    r = start
    for item in data:
        fill = None
        if item and item[0] == "section":
            for col in range(1, ncols + 1):
                c = ws.cell(row=r, column=col)
                c.fill, c.border = SECT_FILL, BORDER
            c = ws.cell(row=r, column=1, value=item[1])
            c.font, c.alignment = SECT_FONT, LEFT
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=ncols)
            ws.row_dimensions[r].height = 20
            r += 1
            continue
        if item and item[0] == "fill":
            fill, item = item[1], item[2:]
        for col in range(1, ncols + 1):
            c = ws.cell(row=r, column=col,
                        value=item[col - 1] if col - 1 < len(item) else None)
            c.font, c.border = BODY_FONT, BORDER
            c.alignment = LEFT if col >= left_from else CENTER
            if fill is not None:
                c.fill = fill
        ws.row_dimensions[r].height = 22
        r += 1
    return r


def merge_first(ws, groups, start=5):
    """첫 칸을 그룹 단위로 병합한다(참고 이미지의 Robot Brand 칸처럼)."""
    r = start
    for label, count in groups:
        ws.merge_cells(start_row=r, start_column=1, end_row=r + count - 1, end_column=1)
        c = ws.cell(row=r, column=1, value=label)
        c.font, c.alignment, c.border = BOLD_BODY, CENTER, BORDER
        r += count


# ================================================================= 1. 전송속도
SPEED_GROUPS = [("Elite CS612 (로봇)", 3), ("RCS 브리지", 2)]
SPEED = [
    (None, "Modbus TCP — RCS 로봇 제어 노드가 읽기/쓰기", "10 Hz (100 ms 주기)",
     "≤ 100 ms (폴링 주기)", "상태 감시·설정에 적합 / 실시간 제어에는 부적합"),
    (None, "Modbus TCP — RCS 브리지가 읽기", "10 Hz (기본 100 ms, 화면에서 조정)",
     "≤ 100 ms", "TPAC 좌표 공급에 적합"),
    (None, "로봇 태스크가 자기 레지스터에 쓰기", "제어주기마다 (약 125 Hz)",
     "-", "좌표·상태 갱신의 원천"),
    (None, "Modbus TCP 서버 — TPAC 이 읽어 감", "TPAC 이 요청하는 주기",
     "요청당 수 ms", "요청 즉시 응답 (FC3 · FC4 모두)"),
    (None, "내주는 값의 신선도", "10 Hz (로봇 폴링 주기)",
     "≤ 100 ms + TPAC 요청 간격", "C-scan 좌표로 충분"),
]

# ============================================================ 2. 로봇 레지스터
REGISTERS = [
    ("section", "컨트롤러 기본 제공 — 로봇이 스스로 채운다 (읽기 전용)"),
    ("17", "로봇 속도 비율", "R", "%", "2 ~ 100"),
    ("63 ~ 65", "컨트롤러 버전", "R", "-", "major / minor / bugfix"),
    ("66", "robot_mode", "R", "코드", "3 POWER_OFF / 4 POWER_ON / 5 IDLE / 7 RUNNING"),
    ("67 / 68 / 69", "전원 / 보호 정지 / 비상 정지", "R", "0 · 1", "1 = 켜짐 · 정지 중"),
    ("70", "감속 모드", "R", "0 · 1", "1 = 안전 감속 구간"),
    ("71", "제어 방식", "R", "코드", "0 원격 미개방 / 1 로컬 / 2 원격"),
    ("72", "운전 모드", "R", "코드", "-1 없음 / 0 자동 / 1 수동"),
    ("73 ~ 78", "관절 각도", "R", "mRad", "Base · Shoulder · Elbow · Wrist1 · 2 · 3"),
    ("84 ~ 89", "관절 속도", "R", "mRad/s", ""),
    ("95 ~ 100", "관절 전류", "R", "mA", ""),
    ("105 ~ 110", "관절 온도", "R", "℃", ""),
    ("384 ~ 389", "TCP pose (베이스 기준)", "R", "0.1 mm / mRad", "실제 TCP. 태스크가 안 돌아도 갱신된다"),
    ("400 ~ 405", "TCP 속도", "R", "mm/s · mRad/s", "환산 없이 실제 단위"),
    ("500", "태스크 상태", "R", "코드", "1 실행 중 / 2 일시 중지 / 3 중지됨"),

    ("section", "RCS → 로봇 — 작업 지시·설정 (태스크가 읽는다)"),
    ("256 / 257", "호 길이 / 격자 높이", "W", "mm", "로봇이 실제로 지나는 호 길이(현 아님)"),
    ("258 / 259", "스캐너 세로 커버 / 격자간 겹침", "W", "0.1 mm / mm", ""),
    ("260 ~ 263", "반지름 / 두께 / 프로브 가로·세로 커버", "W", "0.1 mm",
     "태스크는 읽기만 한다(셀마다 다시 읽는다)"),
    ("264", "EOAT 종류", "W", "코드", "0 없음 / 5 5축 십자 / 8 8축 직사각"),
    ("266", "param_src", "W", "0 · 1", "1 이어야 256~264 를 읽는다"),
    ("267", "스캔 시작 허가", "W", "0 · 1", "원점에서 선 로봇을 푼다 (로봇이 스스로 0 으로 지움)"),
    ("268 / 269", "마킹 자리 u / v", "W", "mm", "원점에서 호를 따라 / 위로"),
    ("306 / 307", "작업 속도 / 속도 비율", "W", "mm/s · %",
     "306 상한 150 mm/s. 307 은 태스크가 곱하지 않는다(비율은 컨트롤러가 적용)"),
    ("308", "pose_src", "W", "0 · 1", "1 이어야 310~321 을 읽는다"),
    ("fill", NEW_FILL, "309", "차량 고정 확인", "W", "0 · 1",
     "1 = 차량 정지 + 아웃트리거 고정 + 리프트 정지. 로봇은 1 이어야 움직인다"),
    ("310 ~ 315", "홈 관절값", "W", "mRad", "홈 플래그(276) 판정 기준"),
    ("316 ~ 321", "그리퍼 기준 pose", "W", "0.1 mm / mRad", "작업면에 붙였을 때의 목표"),

    ("section", "로봇 → RCS — 태스크가 발행 (좌표·진행 상태)"),
    ("270 ~ 275", "원점 pose", "R", "0.1 mm / mRad", "원점 확정 시 한 번 (350~355 와 같은 값)"),
    ("fill", NEW_FILL, "276", "홈 위치 플래그", "R", "0 · 1", "1 홈 / 0 홈 밖 (제어주기마다 갱신)"),
    ("280 ~ 285", "원점 기준 상대좌표", "R", "0.1 mm / mRad",
     "스캔 좌표. EOAT 중심 오프셋(펜던트 eoat5_offset / eoat8_offset)을 반영한 프로브 중심 기준"),
    ("286 / 287", "호 위 거리 / 누적 호 길이", "R", "0.1 mm / mm", "TPAC 스캔 축"),
    ("288 / 289", "눌림 상태 / 눌림 보정량", "R", "코드 · 0.01 mm",
     "0 감시 안 함 / 1 적정 / 2 덜 눌림 / 3 너무 눌림"),
    ("290", "진행 상태", "R", "코드",
     "0 대기 · 2 3점측정 · 3 원점복귀 · 4·6 스캔 · 5 완료 · 7 원점대기 · 8 적심 · 9 오류 · "
     "10~13 마킹 · 14 차량 고정 대기"),
    ("291 / 292", "완료 줄 수 / 전체 줄 수", "R", "개", ""),
    ("293", "alive 워치독", "R", "-", "제어주기마다 +1 (멈추면 발행이 끊긴 것)"),
    ("294 / 295", "원점 확정 / 격자 완료", "R", "0 · 1", "290 = 5 와 295 = 1 이 셀 완료"),
    ("296 / 297 / 298", "줄 간격 / 누적 이동 / 진행률", "R", "mm · cm · %", "누적 이동은 16bit 때문에 cm"),
    ("299", "정지 사유", "R", "코드",
     "0 정상 · 1~3 프로브 접촉 실패 · 4 호 없음 · 5 마킹 실패 · 6 눌림 유지 실패 · "
     "7 역기구학 해 없음 · 8 차량 고정 확인 시간 초과"),
    ("300 ~ 302", "3점 측정 X (중앙 / 좌 / 우)", "R", "0.1 mm", "벽면 2차식의 세 점"),
    ("303", "적정 눌림 보정량", "R", "0.1 mm", "중앙에서 센서로 잰 값"),
    ("304 / 305", "측정 점 수 / 검출 점 수", "R", "개", "검출 < 3 이면 보정값을 믿지 않는다"),
    ("330 ~ 335", "중앙 접점 pose", "R", "0.1 mm / mRad", "곡률 중심 계산용"),
    ("340 ~ 345", "우측 접점 pose", "R", "0.1 mm / mRad", ""),
    ("346", "arc_mode", "R", "코드", "v4 movec 전용 (v5 는 안 씀)"),
    ("350 ~ 355", "원점 pose", "R", "0.1 mm / mRad", "270~275 와 같은 값"),
]

# ========================================================= 3. 브리지 레지스터
BRIDGE = [
    ("400 ~ 405", "TCP pose — 스캔 좌표 (기본값)", "280 ~ 285  원점 기준 상대좌표", "0.1 mm / mRad"),
    ("400 ~ 405", "TCP pose — 절대 좌표로 바꿀 때", "384 ~ 389  실제 TCP (베이스 기준)", "0.1 mm / mRad"),
    ("410 ~ 415", "둘째 블록", "기본은 TCP 속도(로봇 400~405). '좌표만' 설정이면 같은 좌표", "mm/s · mRad/s 또는 0.1 mm / mRad"),
    ("420 ~ 425", "셋째 블록", "기본은 TCP 오프셋. '좌표만' 설정이면 같은 좌표", "mm / mRad 또는 0.1 mm / mRad"),
    ("-", "스캔 축 (C-scan)", "286 / 287  호 위 거리 · 누적 호 길이", "0.1 mm / mm"),
]


def main() -> None:
    wb = Workbook()
    wb.remove(wb.active)

    ws = sheet(
        wb, "1.전송속도",
        "모드버스 전송속도",
        "RCS 가 정한 주기다. 회선 자체의 왕복 시간은 현장 LAN 에서 따로 재야 한다.",
        ["장비", "구간", "주기", "지연", "실시간 적합성"],
        [24, 44, 30, 26, 44])
    rows(ws, SPEED, 5, left_from=2)
    merge_first(ws, SPEED_GROUPS)

    ws = sheet(
        wb, "2.로봇 레지스터",
        "Elite CS612 주소별 레지스터 (Modbus TCP 502 · 홀딩 FC3 / FC6)",
        "16bit 정수. 부호 있는 값은 signed 로 읽는다. 환산: mm = 값 ÷ 10, rad = 값 ÷ 1000. "
        "R = 로봇 → RCS, W = RCS → 로봇. 노랑은 이번에 새로 만든 주소. v4 · v5 가 같은 맵을 쓴다.",
        ["주소", "이름", "R/W", "단위", "값 · 비고"],
        [16, 34, 7, 18, 78])
    rows(ws, REGISTERS, 5, left_from=5)

    ws = sheet(
        wb, "3.브리지 레지스터",
        "RCS 브리지가 TPAC 에 내주는 자리 (UR Modbus 서버와 같은 주소)",
        "브리지는 좌표만 내준다 — 실제 TCP 또는 스캔용 좌표. TPAC 은 주소가 고정이라 "
        "UR 자리에 그대로 재현한다.",
        ["TPAC 이 읽는 주소", "내용", "로봇 쪽 원본", "단위"],
        [22, 40, 52, 34])
    rows(ws, BRIDGE, 4, left_from=2)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT)
    print(f"만들었습니다: {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
