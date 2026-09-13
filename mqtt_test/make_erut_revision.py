"""ERUT-3S MQTT 인터페이스 문서 수정본을 만든다. (원본은 건드리지 않는다)

보강 두 가지
  1. 상시 상태 모니터링용 telemetry 토픽
  2. ERUT → 모마 요청에 빠진 값들 (원통 두께, 구역 분할 계획, 겹침, 속도)

주의: openpyxl 의 insert_rows 는 **병합 범위를 따라 옮겨주지 않는다.**
그대로 쓰면 위쪽 병합이 새 줄을 덮어 내용이 삼켜지므로 병합을 직접 민다.
amount=0 으로 부르면 행이 날아가므로 빈 삽입은 하지 않는다.
"""
import shutil
from copy import copy

import openpyxl
from openpyxl.styles import PatternFill

SRC = "ERUT-3S_MQTT_인터페이스_20260812.xlsx"
DST = "ERUT-3S_MQTT_인터페이스_20260814_3S수정본.xlsx"

NEW = PatternFill("solid", fgColor="FFFFF2CC")   # 새로 넣은 줄
EDIT = PatternFill("solid", fgColor="FFDDEBF7")  # 원본 줄인데 내용을 고친 것


def restyle(ws, src_row, dst_row, ncols, fill=None):
    ws.row_dimensions[dst_row].height = ws.row_dimensions[src_row].height
    for col in range(1, ncols + 1):
        s, d = ws.cell(row=src_row, column=col), ws.cell(row=dst_row, column=col)
        d.font, d.border = copy(s.font), copy(s.border)
        d.alignment, d.number_format = copy(s.alignment), s.number_format
        d.fill = fill if fill else copy(s.fill)


def insert(ws, at, rows, ncols, fill=NEW):
    """`at` 위치에 줄을 끼워 넣고 아래쪽 병합 범위도 함께 민다."""
    if not rows:
        raise ValueError("빈 삽입은 행을 망가뜨린다")
    n = len(rows)
    moved = []
    for rng in list(ws.merged_cells.ranges):
        if rng.min_row >= at:
            moved.append((rng.min_col, rng.min_row, rng.max_col, rng.max_row))
            ws.unmerge_cells(str(rng))

    ws.insert_rows(at, amount=n)

    for min_col, min_row, max_col, max_row in moved:
        ws.merge_cells(start_row=min_row + n, start_column=min_col,
                       end_row=max_row + n, end_column=max_col)

    sample = at + n                      # 밀려난 원래 줄을 서식 표본으로
    for offset, values in enumerate(rows):
        r = at + offset
        restyle(ws, sample, r, ncols, fill=fill)
        for col, value in enumerate(values, 1):
            if value is not None:
                ws.cell(row=r, column=col, value=value)


def edit(ws, row, ncols, **cols):
    """원본 줄의 내용을 고치고 표시색을 입힌다. cols 는 c4=..., c5=... 형태."""
    for key, value in cols.items():
        ws.cell(row=row, column=int(key[1:]), value=value)
    for col in range(1, ncols + 1):
        ws.cell(row=row, column=col).fill = EDIT


shutil.copyfile(SRC, DST)
wb = openpyxl.load_workbook(DST)

# ══════════════════════════════════════════════ 탭1. ERUT 발행·구독
ws = wb["1.ERUT 발행·구독"]
insert(ws, 25, [(
    "erut/{장치ID}/evt/telemetry", "상시 상태(모니터링)",
    "장치가 1초 주기로 상시 발행 (요청 없이)", 0, "✗", "상태",
    "화면 상시 표시·기록용. 유실돼도 검사 판정에는 쓰지 않음",
    "[3S 추가] 장치 상태를 계속 지켜보기 위한 통로. 지금은 query 로 물어봐야만 "
    "state·progress·battery 를 알 수 있어 ERUT 화면이 실시간 상태를 그릴 수 없다. "
    "evt/status 는 접속 생존 확인용(30~60초, retained)이라 모니터링 주기로 쓰기엔 느리다.",
)], ncols=8)

# ══════════════════════════════════════════════ 탭2. 3S 발행·구독
ws = wb["2.3S 발행·구독"]
insert(ws, 12, [(
    "erut/{장치ID}/evt/telemetry", "상시 상태",
    "접속 중 1초 주기 상시 발행. retain ✗ — 지나간 값이 남으면 멈춘 장치가 살아 있는 것처럼 보인다",
    0, "✗", "상태",
    "state, job_id, cell, progress, battery, charging, lift_height, speed, position{x,y}, errors[]",
    "[3S 추가] ERUT 화면의 실시간 모니터링용. QoS 0 이라 유실돼도 무방하며 "
    "작업 성패 판정에는 쓰지 않는다(판정은 evt/complete 로만).",
)], ncols=8)

wb.save(DST)
print("탭1·2 완료")

# ══════════════════════════════════════════════ 탭3. 정상 시나리오
wb = openpyxl.load_workbook(DST)
ws = wb["3.정상 시나리오"]

# ① 접속 확인 — 상시 telemetry 를 흐름에 명시 (4번 뒤)
insert(ws, 10, [
    ("2-1", "로봇 → ERUT", "erut/robot1/evt/telemetry",
     '{ "timestamp": 1786500000500,\n'
     '  "content": { "state": "idle", "battery": 85, "charging": false,\n'
     '    "speed": 100, "lift_height": 320, "errors": [] } }',
     "[3S 추가] 접속된 동안 1초 주기로 계속 발행. ERUT 는 구독만 하면 상태를 실시간으로 "
     "볼 수 있다. query 는 시작·재개 직전의 결정용, telemetry 는 상시 감시용이라 역할이 다르다."),
    (None, None, None, None,
     "[3S 검토 요청] LWT 는 접속이 끊길 때 브로커가 대신 발행하는 1회성 유언이라 "
     "케이블은 살아 있는데 프로그램만 멈춘 경우를 잡지 못한다. telemetry 가 N 초"
     "(예: 5초) 이상 끊기면 오프라인으로 보는 감시 규칙을 양측에 두는 것을 제안한다."),
], ncols=5)

# ② 캘리브레이션 — 원통 두께 추가 (calibrate 는 2줄 밀려 16행)
edit(ws, 16, 5,
     c4='{ "timestamp": 1786500020000,\n'
        '  "content": { "req_id": "req-20260812-000002",\n'
        '    "diameter": 1200, "height": 2400, "thickness": 500 } }',
     c5="모재 정보 전달 (mm).\n"
        "[3S 추가] thickness — MC 쪽 기존 job_cmd 명세에는 있는데 이 문서에는 없어 "
        "양쪽을 맞췄다. 다만 현재 3S 는 이 값을 받아 저장만 하고 로봇 동작·검사 조건에 "
        "쓰지 않는다. 실제로 필요한 값인지, 필요하다면 어디에 쓰이는지 확인 필요. "
        "안 쓰는 값이면 양쪽에서 함께 빼는 것도 방법이다.")

# ③ 검사 준비 — 구역 분할 계획·겹침·속도 추가 (prepare 는 23행)
edit(ws, 23, 5,
     c4='{ "timestamp": 1786500070000,\n'
        '  "content": { "req_id": "req-20260812-000003",\n'
        '    "action": "prepare", "job_id": "jb00000001", "surface": "outer",\n'
        '    "area": { "start": { "x": 0, "y": 1000 },\n'
        '              "end": { "x": 1000, "y": 1500 } },\n'
        '    "plan": { "columns": 12, "rows": 6,\n'
        '              "cell_width": 600, "cell_height": 800, "overlap": 20 },\n'
        '    "scan": { "pitch": 5, "speed": 40, "speed_ratio": 100 } } }',
     c5="구간(작업) 준비. \n"
        "surface: 검사면 식별 값(고정 목록 아님 — 화면에서 정의해 장치와 합의, 예: outer), \n"
        "좌표는 검사면 기준 x·y mm (ERUT 엔코더 좌표와 같은 기준, 원점=캘리브레이션 원점). \n"
        "scan.pitch=스캔 줄 간격(한 줄 스캔 후 다음 줄까지 띄우는 거리).\n"
        "\n[3S 추가] plan — 모재를 펼친 면을 열(차량 정차 구역) × 행(리프트 높이)으로 나눈 "
        "계획. 모마는 셀 하나(cell_width × cell_height)씩 ㄹ자로 스캔하므로 이 값이 없으면 "
        "한 번에 닿지 못하는 영역을 받게 된다.\n"
        "[3S 추가] overlap — 셀 사이 겹침 허용(mm). 리프트 상승량(cell_height − overlap)과 "
        "스캔 줄 간격을 정한다.\n"
        "[3S 추가] scan.speed_ratio — 로봇 전체 동작 속도 비율(2~100 %).")

# ④ 구간 검사 — start 도 같은 값 (start 는 29행)
edit(ws, 29, 5,
     c4='{ "timestamp": 1786500100000,\n'
        '  "content": { "req_id": "req-20260812-000004",\n'
        '    "job_id": "jb00000001", "surface": "outer",\n'
        '    "area": { "start": { "x": 0, "y": 1000 },\n'
        '              "end": { "x": 1000, "y": 1500 } },\n'
        '    "plan": { "columns": 12, "rows": 6,\n'
        '              "cell_width": 600, "cell_height": 800, "overlap": 20 },\n'
        '    "scan": { "pitch": 5, "speed": 40, "speed_ratio": 100 } } }',
     c5="매 start마다 그 구간의 좌표·스캔 값 전체를 전달\n"
        "\n[3S 추가] prepare 와 같은 plan·overlap·speed_ratio 를 포함한다. "
        "구간마다 분할이 달라질 수 있어 start 에도 실어 보낸다.")

wb.save(DST)
print("탭3 완료")

# ══════════════════════════════════════════════ 탭5. 값 정의
wb = openpyxl.load_workbook(DST)
ws = wb["5.값 정의"]

# 상시 상태 발행 필드 블록 뒤(50행)에 telemetry 필드 정의를 넣는다
insert(ws, 51, [
    (None, None, None),
    ("■ [3S 추가] 로봇 → ERUT: 상시 모니터링 발행 필드 — erut/{장치ID}/evt/telemetry (1초 주기, QoS 0, retain ✗)",
     None, None),
    ("필드", "설명", "포함 조건 · 예"),
    ("timestamp", "발행 시각 (UTC 밀리초 13자리)", "필수 · 1786500000500"),
    ("state", "현재 상태 (위 state 표의 7값 중 하나)", "필수 · \"running\""),
    ("job_id", "진행 중인 구간", "작업 중일 때만 · \"jb00000001\""),
    ("cell", "지금 스캔 중인 격자 이름 (열 번호 + 행 문자)", "작업 중일 때만 · \"1A\", \"12F\""),
    ("progress", "진행률 0~100", "작업 중일 때만 · 45"),
    ("battery", "배터리 잔량 %", "배터리 있는 장치만 · 72"),
    ("charging", "충전 중 여부", "배터리 있는 장치만 · false"),
    ("lift_height", "리프트 높이 (mm)", "리프트 있는 장치만 · 320"),
    ("speed", "로봇 동작 속도 비율 % (2~100)", "필수 · 100"),
    ("position", "현재 검사면 좌표 {x, y} (mm)", "작업 중일 때만 · { \"x\": 350, \"y\": 1200 }"),
    ("errors[]", "지금 걸려 있는 에러 코드 목록 (스냅샷)", "장애가 걸려 있을 때만 · [\"E2001\"]"),
    (None, None, None),
    ("■ [3S 추가] ERUT → 로봇: 구역 분할 계획 — prepare·start 의 content.plan",
     None, None),
    ("필드", "설명", "포함 조건 · 예"),
    ("columns", "모재를 펼친 면을 원주 방향으로 나눈 구역 수 (차량 정차 위치 수)", "필수 · 12"),
    ("rows", "한 구역을 세로로 나눈 칸 수 (리프트 높이 단계)", "필수 · 6"),
    ("cell_width", "셀 하나의 가로 폭 (mm) — 로봇이 한 번에 훑는 영역", "필수 · 600"),
    ("cell_height", "셀 하나의 세로 높이 (mm) — 모재 전체 높이가 아니다", "필수 · 800"),
    ("overlap", "셀 사이 겹침 허용 (mm). 리프트 상승량 = cell_height − overlap", "필수 · 20"),
    (None, None, None),
    ("■ [3S 추가] ERUT → 로봇: 그 밖에 보강한 필드", None, None),
    ("필드", "설명", "쓰이는 곳 · 예"),
    ("thickness", "모재 두께 (mm). MC 쪽 job_cmd 명세에는 있으나 현재 3S 는 저장만 하고 "
     "동작에 쓰지 않음 — 용도 확인 필요", "calibrate · 500"),
    ("scan.speed_ratio", "로봇 전체 동작 속도 비율 % (2~100). 작업 중에도 바꿀 수 있다",
     "prepare·start · 100"),
], ncols=3)

wb.save(DST)
print("탭5 완료")

# ══════════════════════════════════════════════ 탭6. 변경 이력 (신규)
wb = openpyxl.load_workbook(DST)
ws = wb.create_sheet("6.3S 수정 이력")
ws.column_dimensions["A"].width = 6
ws.column_dimensions["B"].width = 20
ws.column_dimensions["C"].width = 34
ws.column_dimensions["D"].width = 95

head_font = openpyxl.styles.Font(bold=True, size=10, color="FFFFFFFF")
head_fill = PatternFill("solid", fgColor="FF2F5496")
wrap = openpyxl.styles.Alignment(wrap_text=True, vertical="center")
thin = openpyxl.styles.Side(style="thin", color="FFBFBFBF")
border = openpyxl.styles.Border(left=thin, right=thin, top=thin, bottom=thin)

rows = [
    ("탭6. 3S 수정 이력 — 원본: ERUT-3S_MQTT_인터페이스_20260812.xlsx", None, None, None),
    ("수정한 칸은 옅은 노랑(신규) / 옅은 파랑(내용 보강)으로 표시했다. 협의 후 확정하면 색을 지운다.",
     None, None, None),
    (None, None, None, None),
    ("№", "요청", "위치", "무엇을 왜 바꿨나"),
    (1, "상시 상태 모니터링", "탭1 구독 표 / 탭2 발행 표 / 탭3 ①접속확인 / 탭5 필드 정의",
     "erut/{장치ID}/evt/telemetry 를 새로 넣었다. 1초 주기, QoS 0, retain ✗.\n"
     "• 왜: 지금 문서에서 장치 상태를 알 수 있는 길은 ERUT 가 req/query 로 물어보는 것뿐이다. "
     "query 는 '시작·재개 직전의 결정용'으로 못박혀 있어 상시 폴링에 쓸 수 없고, "
     "evt/status 는 접속 생존 확인용(30~60초, retained)이라 화면 모니터링 주기로는 느리다.\n"
     "• 그래서 접속 생존(evt/status)과 실시간 상태(evt/telemetry)를 나눴다. "
     "telemetry 는 retain 을 걸지 않는다 — 지나간 값이 남으면 멈춘 장치가 살아 있는 것처럼 보인다."),
    (2, "LWT 상시 감시 검토", "탭3 ①접속확인 (검토 요청 줄)",
     "LWT 자체는 양측 모두 걸려 있다. 다만 LWT 는 **접속이 끊길 때 브로커가 대신 내는 1회성 유언**이라, "
     "케이블은 살아 있는데 프로그램만 멈추거나 굳은 경우는 잡지 못한다.\n"
     "• 제안: telemetry 가 N 초(예: 5초) 이상 끊기면 상대를 오프라인으로 보는 감시 규칙을 "
     "양측에 둔다. 주기와 판정 시간은 협의 필요.\n"
     "• 3S 쪽 현황: LWT 설정은 되어 있으나 살아 있음을 알리는 주기 발행과, "
     "끊김을 판정해 화면에 반영하는 부분이 아직 붙어 있지 않다. telemetry 확정과 함께 구현한다."),
    (3, "원통 크기 보강", "탭3 ②캘리브레이션 / 탭5 필드 정의",
     "calibrate 에 thickness(모재 두께, mm)를 넣었다. 이 문서에는 diameter·height 만 있는데 "
     "MC 쪽 기존 job_cmd 명세에는 thickness·target_distance 가 함께 있어 양쪽을 맞춘 것이다.\n"
     "• 유의: 현재 3S 는 thickness 와 target_distance 를 받아 저장만 하고 로봇 동작이나 "
     "검사 조건 계산에 쓰지 않는다. 두 값의 실제 용도를 확정하고, 쓰지 않는 값이면 "
     "양쪽에서 함께 빼는 편이 낫다."),
    (4, "구역 분할 계획 보강", "탭3 ③검사준비·④구간검사 / 탭5 필드 정의",
     "prepare·start 의 content 에 plan 블록을 넣었다: columns, rows, cell_width, cell_height.\n"
     "• 왜: 모마는 모재를 펼친 면을 열(차량 정차 구역) × 행(리프트 높이)으로 나눠, "
     "셀 하나씩 ㄹ자로 스캔한다. 지금 문서의 area(start·end)만으로는 그 영역을 "
     "한 번에 훑으라는 뜻이 되어, 로봇이 한 번에 닿지 못하는 크기를 받게 된다.\n"
     "• cell_height 는 셀 하나의 높이이지 모재 전체 높이가 아니다 — 이 둘을 혼동하면 "
     "스캔 줄 수가 수십 배로 늘어난다."),
    (5, "겹침 허용 보강", "탭3 ③검사준비·④구간검사 / 탭5 필드 정의",
     "plan.overlap(mm)을 넣었다. 셀과 셀 사이 겹침이며, 리프트 상승량(cell_height − overlap)과 "
     "스캔 줄 간격을 정하는 값이라 없으면 미검사 띠가 생긴다."),
    (6, "속도 보강", "탭3 ③검사준비·④구간검사 / 탭5 필드 정의 / 탭2 telemetry",
     "scan.speed_ratio(로봇 전체 동작 속도 비율 2~100 %)를 넣고, telemetry 에 현재 speed 를 담았다.\n"
     "• 기존 scan.speed 는 스캔 이송 속도(mm/s)이고, speed_ratio 는 그 위에 곱하는 로봇 전체 배율로 "
     "서로 다른 값이다. 작업 중에도 바꿀 수 있다.\n"
     "• 안전 상한: 3S 로봇의 TCP 직선 속도는 안전 기준상 150 mm/s 를 넘지 않는다. "
     "speed_ratio 100 % 가 곧 150 mm/s 이며, 더 큰 값을 받아도 로봇이 자체적으로 자른다."),
    (None, None, None, None),
    ("협의 필요", None, None,
     "① telemetry 주기 1초가 적절한지 (부하·필요 해상도)\n"
     "② 끊김 판정 시간 N 초\n"
     "③ plan 을 prepare 에만 둘지, start 마다 다시 실을지 (구간마다 분할이 달라질 수 있어 "
     "현재는 둘 다에 실었다)\n"
     "④ telemetry 의 cell 표기법 — 현재 3S 내부는 열 번호 + 행 문자(1A, 12F)를 쓴다\n"
     "⑤ thickness·target_distance 의 용도 — 현재 3S 는 저장만 하고 쓰지 않는다. "
     "ERUT 쪽에서 검사 조건에 쓰는 값인지, 아니면 양쪽에서 빼도 되는지"),
]

for i, values in enumerate(rows, 1):
    for col, value in enumerate(values, 1):
        c = ws.cell(row=i, column=col, value=value)
        c.alignment = wrap
        if i == 4:
            c.font, c.fill, c.border = head_font, head_fill, border
        elif i >= 5 and values[0] is not None:
            c.border = border
            c.font = openpyxl.styles.Font(size=9)
ws.cell(row=1, column=1).font = openpyxl.styles.Font(bold=True, size=12)
for i in (5, 6, 7, 8, 9, 10):
    ws.row_dimensions[i].height = 78
ws.row_dimensions[12].height = 78

wb.save(DST)
print(f"완료: {DST}")
print("시트:", wb.sheetnames)
