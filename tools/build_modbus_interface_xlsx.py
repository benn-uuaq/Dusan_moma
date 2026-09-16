"""Elite 협동로봇(CS612) <-> RCS 모드버스 인터페이스 표를 만든다.

내용의 출처는 전부 이 저장소다. 값을 고칠 때는 아래 원본을 먼저 고치고
이 스크립트를 다시 돌린다(문서가 코드보다 오래되지 않게).

  src/elite_robot_controller/config/modbus_registers.json   RCS 가 읽고 쓰는 주소
  src/elite_robot_controller/.../robot_control_node.py      주기·토픽·29999/30001
  robot_task/dusan_task_v5/scripts/*.script                 태스크가 쓰는 주소
  robot_task/dusan_task_v4/scripts/*.script                 (v4 도 같은 맵)
  operator-ui/.../services/tpac_bridge/robot_map.py         TPAC(UR 호환) 변환
  robot_task/DUSAN_OVERVIEW.md                              전체 설명

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
NEW_FILL = PatternFill("solid", fgColor="FFFFF2CC")      # 이번에 새로 넣은 주소
WARN_FILL = PatternFill("solid", fgColor="FFFCE4D6")     # 주의해서 볼 줄
HEAD_FONT = Font(name=FONT, size=10, bold=True, color="FFFFFFFF")
SECT_FONT = Font(name=FONT, size=10, bold=True, color="FF1F3864")
BODY_FONT = Font(name=FONT, size=9)
TITLE_FONT = Font(name=FONT, size=13, bold=True, color="FF1F3864")
NOTE_FONT = Font(name=FONT, size=9, color="FF595959")
EDGE = Side(style="thin", color="FFBFBFBF")
BORDER = Border(left=EDGE, right=EDGE, top=EDGE, bottom=EDGE)
WRAP = Alignment(wrap_text=True, vertical="center")
WRAP_L = Alignment(wrap_text=True, vertical="center", horizontal="left")
CENTER = Alignment(wrap_text=True, vertical="center", horizontal="center")


def sheet(wb, name, title, note, headers, widths):
    ws = wb.create_sheet(name)
    ws.cell(row=1, column=1, value=title).font = TITLE_FONT
    ws.cell(row=2, column=1, value=note).font = NOTE_FONT
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(headers))
    ws.row_dimensions[2].height = 30
    ws.cell(row=2, column=1).alignment = WRAP_L
    for col, (head, width) in enumerate(zip(headers, widths), start=1):
        cell = ws.cell(row=4, column=col, value=head)
        cell.font, cell.fill, cell.alignment, cell.border = HEAD_FONT, HEAD_FILL, CENTER, BORDER
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.row_dimensions[4].height = 28
    ws.freeze_panes = ws.cell(row=5, column=1)
    return ws


def rows(ws, data, ncols, start=5):
    """data: ("section", 제목) 또는 (값들...) 또는 ("fill", 색, 값들...)"""
    r = start
    for item in data:
        fill = None
        if item and item[0] == "section":
            cell = ws.cell(row=r, column=1, value=item[1])
            cell.font, cell.fill, cell.alignment = SECT_FONT, SECT_FILL, WRAP_L
            for col in range(1, ncols + 1):
                c = ws.cell(row=r, column=col)
                c.fill, c.border = SECT_FILL, BORDER
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=ncols)
            ws.row_dimensions[r].height = 20
            r += 1
            continue
        if item and item[0] == "fill":
            fill, item = item[1], item[2:]
        for col in range(1, ncols + 1):
            value = item[col - 1] if col - 1 < len(item) else None
            cell = ws.cell(row=r, column=col, value=value)
            cell.font, cell.border = BODY_FONT, BORDER
            cell.alignment = CENTER if col <= 2 else WRAP_L
            if fill is not None:
                cell.fill = fill
        ws.row_dimensions[r].height = 26
        r += 1
    return r


# ===================================================================== 1. 채널
CHANNELS = [
    ("section", "로봇(Elite CS612) ↔ RCS"),
    ("Modbus TCP", "502", "RCS 로봇 제어 노드 ↔ 로봇", "10 Hz (100 ms 주기)",
     "최대 100 ms (폴링 주기)", "상태 감시·설정에 적합 / 실시간 제어에는 부적합",
     "레지스터 읽기 FC3(실패 시 FC4), 쓰기 FC6. 쓰기는 되읽어 확인하므로 1회당 +50 ms"),
    ("Modbus TCP", "502", "RCS TPAC 브리지 ↔ 로봇", "10 Hz (기본 100 ms, 화면에서 조정)",
     "최대 100 ms", "TPAC C-scan 좌표 공급에 적합",
     "제어 노드와 별개의 연결이다. 63~110 / 280~287 / 384~389 / 400~405 / 500 을 블록으로 읽는다"),
    ("Dashboard (텍스트)", "29999", "RCS → 로봇", "명령이 있을 때만",
     "명령당 수십 ms", "상태 조회·시작·정지용",
     "play / pause / stop / power / brakeRelease / task -p / variable -get / speed -set"),
    ("Primary (스크립트)", "30001", "RCS → 로봇", "명령이 있을 때만",
     "명령당 수십 ms", "조그·홈 이동용",
     "speedj / speedl (조그), move_home 프로그램 전송. 실시간 알람 스트림도 이 포트로 받는다"),
    ("section", "로봇 안 (태스크가 스스로 쓰는 값)"),
    ("write_port_register", "-", "로봇 태스크 → 자기 레지스터", "제어주기마다 (sync() 1회)",
     "-", "좌표·상태 발행의 원천",
     "발행 스레드(dus5_init 의 pub_thread)가 260~265 / 280~287 / 276 / 293 을 갱신한다"),
    ("servoj 루프", "-", "v5 스캔 줄", "250 Hz (sv_dt = 4 ms)",
     "-", "호를 그리며 눌림 보정",
     "한 주기마다 288(눌림 상태) / 289(보정량)를 갱신한다"),
    ("section", "RCS 바깥 (참고 — 이 문서의 범위는 아니다)"),
    ("Modbus TCP 서버", "502", "TPAC → RCS 브리지", "TPAC 가 요청할 때",
     "요청당 수 ms", "UR 호환 주소로 응답", "브리지가 서버가 되어 UR 자리(4번 탭)로 값을 내준다"),
    ("MQTT", "1883", "RCS ↔ ERUT / 사내 MC", "이벤트마다",
     "수십 ms", "작업 지시·진행 보고", "ERUT-3S MQTT 인터페이스 문서 참고"),
    ("ROS 2", "-", "RCS ↔ 차량 제어 노드", "10 Hz 상태 발행",
     "-", "차량·리프트·아웃트리거", "vehicle_interfaces (RobotStatus / RobotControl / SetJob / ManualCommand)"),
]

# ============================================================ 2. 주소별 레지스터
# (주소, 이름, 방향, 쓰는 주체, 단위·환산, 값·범위, 설명)
REGISTERS = [
    ("section", "■ 컨트롤러 기본 제공 — 로봇이 스스로 채운다 (태스크와 무관, 읽기 전용)"),
    ("17", "speed_scale", "로봇 → RCS", "컨트롤러", "%", "2 ~ 100",
     "로봇 동작 속도 비율. 펜던트에서 바꿔도 여기로 나온다. 설정은 레지스터가 아니라 29999 'speed -set N' 으로 한다"),
    ("63 ~ 65", "컨트롤러 버전", "로봇 → RCS", "컨트롤러", "-", "major / minor / bugfix",
     "TPAC 브리지가 읽어 UR 자리(256/257)로 넘긴다"),
    ("66", "robot_mode", "로봇 → RCS", "컨트롤러", "코드", "0 ~ 9",
     "0 DISCONNECTED / 3 POWER_OFF / 4 POWER_ON / 5 IDLE / 7 RUNNING (5번 탭)"),
    ("67", "power_on", "로봇 → RCS", "컨트롤러", "0/1", "0 꺼짐 / 1 켜짐", "전원 상태"),
    ("68", "protective_stop", "로봇 → RCS", "컨트롤러", "0/1", "1 = 보호 정지", "충돌·안전 한계로 선 상태"),
    ("69", "emergency_stop", "로봇 → RCS", "컨트롤러", "0/1", "1 = 비상 정지", "E-Stop 눌림"),
    ("70", "reduced_mode", "로봇 → RCS", "컨트롤러", "0/1", "1 = 감속 모드", "안전 감속 구간"),
    ("71", "control_method", "로봇 → RCS", "컨트롤러", "코드", "0 원격 미개방 / 1 로컬 / 2 원격",
     "RCS 명령을 받으려면 2(원격)여야 한다"),
    ("72", "operation_mode", "로봇 → RCS", "컨트롤러", "코드", "-1 지정 안 됨 / 0 자동 / 1 수동", "펜던트 운전 모드"),
    ("73 ~ 78", "관절 각도", "로봇 → RCS", "컨트롤러", "mRad (rad × 1000)", "±32.767 rad",
     "Base, Shoulder, Elbow, Wrist1, Wrist2, Wrist3 순서"),
    ("84 ~ 89", "관절 속도", "로봇 → RCS", "컨트롤러", "mRad/s", "signed", "TPAC 브리지만 읽는다"),
    ("95 ~ 100", "관절 전류", "로봇 → RCS", "컨트롤러", "mA", "signed", "TPAC 브리지만 읽는다"),
    ("105 ~ 110", "관절 온도", "로봇 → RCS", "컨트롤러", "℃", "signed", "TPAC 브리지만 읽는다"),
    ("384 ~ 389", "TCP pose (베이스 기준)", "로봇 → RCS", "컨트롤러",
     "X,Y,Z 0.1 mm / Rx,Ry,Rz mRad", "±3276.7 mm / ±32.767 rad",
     "현재 TCP. 태스크가 돌지 않아도 갱신되므로 RCS 의 기본 좌표 표시는 이 주소를 쓴다"),
    ("400 ~ 405", "TCP 속도", "로봇 → RCS", "컨트롤러", "X,Y,Z mm/s / Rx,Ry,Rz mRad/s", "signed",
     "이미 실제 단위라 환산이 없다. TPAC 브리지가 그대로 넘긴다"),
    ("500", "task_state", "로봇 → RCS", "컨트롤러", "코드", "1 실행 중 / 2 일시 중지 / 3 중지됨",
     "홀딩이 아니라 입력 레지스터라 FC3 실패 후 FC4 폴백으로 읽힌다. RCS 인터락의 한 축"),

    ("section", "■ RCS → 로봇 (작업 지시·설정) — 태스크가 읽는다"),
    ("256", "app_arc (호 길이)", "RCS → 로봇", "RCS", "mm", "0 ~ 32767",
     "격자 가로 = 로봇이 실제로 지나는 호 길이. 현(chord)이 아니다"),
    ("257", "app_height", "RCS → 로봇", "RCS", "mm", "0 ~ 32767", "격자 높이"),
    ("258", "scan_h", "RCS → 로봇", "RCS", "0.1 mm", "예: 300 = 30.0 mm",
     "스캐너(프로브) 세로 유효 커버. 실제로는 263 이 있으면 263 을 쓴다"),
    ("259", "overlap", "RCS → 로봇", "RCS", "mm", "0 ~", "격자끼리의 겹침 허용도(기록용). 격자 안 줄 간격은 로봇이 다시 계산한다"),
    ("fill", WARN_FILL, "260", "app_radius", "RCS → 로봇", "RCS", "0.1 mm", "예: 8346 = 834.6 mm",
     "도면 반지름(안쪽 면). ★ 태스크 시작 시 한 번만 읽고, 그 뒤에는 태스크가 이 자리에 TCP 를 쓴다(아래 260~265 참고)"),
    ("fill", WARN_FILL, "261", "app_thick", "RCS → 로봇", "RCS", "0.1 mm", "예: 100 = 10.0 mm",
     "제품 두께. 훑는 면 반지름 = 반지름 + 두께. ★ 260 과 같은 주의"),
    ("fill", WARN_FILL, "262", "eoat_w", "RCS → 로봇", "RCS", "0.1 mm", "5축 300 / 8축 750",
     "프로브 유효 가로 커버. ★ 260 과 같은 주의"),
    ("fill", WARN_FILL, "263", "eoat_h", "RCS → 로봇", "RCS", "0.1 mm", "5축 300 / 8축 1675",
     "프로브 유효 세로 커버 = 한 줄이 덮는 높이. ★ 260 과 같은 주의"),
    ("264", "eoat_type", "RCS → 로봇", "RCS", "코드", "0 없음 / 5 5축 십자 / 8 8축 직사각",
     "장착한 EOAT. 태스크가 종류별 오프셋을 빼 플랜지 기준 시작 자세를 다시 계산한다"),
    ("266", "param_src", "RCS → 로봇", "RCS", "0/1", "0 펜던트 변수 / 1 레지스터(256~264)",
     "1 이어야 256~264 를 읽는다. 차량 인터락(309)도 이 값이 1 일 때만 본다"),
    ("267", "scan_go", "RCS → 로봇", "RCS", "0/1", "1 = 스캔 시작 허가",
     "로봇이 원점에서 290 = 7 로 서서 기다린다. 로봇이 통과하면서 스스로 0 으로 지운다"),
    ("268 / 269", "mark_target u / v", "RCS → 로봇", "RCS", "mm", "0 ~ 32767",
     "마킹 자리. u = 원점에서 호를 따라 간 거리, v = 원점 높이에서 위로"),
    ("306", "linear_speed", "RCS → 로봇", "RCS", "mm/s", "0 = 변수값 유지",
     "스캔 외 이송 속도(v_l). 안전 상한 150 mm/s 로 잘린다"),
    ("307", "speed_ratio", "RCS → 로봇", "RCS", "%", "2 ~ 100",
     "모든 이송 속도에 곱한다. 실시간 반영은 29999 'speed -set' 이 하고 이 주소는 태스크 재시작용"),
    ("308", "pose_src", "RCS → 로봇", "RCS", "0/1", "0 펜던트 변수 / 1 레지스터(310~321)",
     "기준 위치를 RCS 가 준 값으로 쓸지 정한다"),
    ("fill", NEW_FILL, "309", "vehicle_ready", "RCS → 로봇", "RCS", "0/1", "1 = 차량 고정 확인",
     "★ 신규. 차량 정지 + 아웃트리거 고정 + 리프트 정지를 RCS 가 확인하면 1. 태스크는 이 값이 1 이어야 움직인다"),
    ("310 ~ 315", "Home_joint", "RCS → 로봇", "RCS", "mRad", "±32767",
     "홈 관절값 6개. 308 = 1 일 때 쓴다. 홈 플래그(276) 판정 기준도 이 값이다"),
    ("316 ~ 321", "gripper_target_pose", "RCS → 로봇", "RCS",
     "X,Y,Z 0.1 mm / Rx,Ry,Rz mRad", "±3276.7 mm",
     "그리퍼(EOAT) 기준점을 작업면에 붙였을 때의 목표 pose. 태스크가 오프셋을 빼 시작 자세를 만든다"),

    ("section", "■ 로봇 → RCS (태스크가 발행) — 좌표·진행 상태"),
    ("fill", WARN_FILL, "260 ~ 265", "TCP pose (베이스 기준)", "로봇 → RCS", "태스크",
     "X,Y,Z 0.1 mm / Rx,Ry,Rz mRad", "±3276.7 mm / ±32.767 rad",
     "★ 260~264 는 시작 전에는 RCS 입력(위 표), 태스크가 돌기 시작하면 발행 스레드가 TCP 로 덮어쓴다. "
     "그래서 RCS 는 태스크가 도는 중에 작업 영역을 보내지 않는다"),
    ("270 ~ 275", "zero_pose (원점)", "로봇 → RCS", "태스크", "0.1 mm / mRad", "±3276.7 mm",
     "원점(Y+ 끝 접점)을 확정한 순간 한 번 쓴다. 350~355 와 같은 값이다"),
    ("fill", NEW_FILL, "276", "home_flag", "로봇 → RCS", "태스크 + RCS 노드", "0/1", "1 홈 / 0 홈 밖",
     "★ 신규. 발행 스레드가 제어주기마다 관절을 Home_joint 와 비교해 쓴다(TCP 가 Home_pose 자리여도 1). "
     "태스크가 안 돌 때는 RCS 노드가 관리한다 — 조그를 보내면 0, 홈 이동이 끝나면 1"),
    ("280 ~ 285", "원점 기준 상대좌표", "로봇 → RCS", "태스크", "0.1 mm / mRad", "±3276.7 mm",
     "(현재 TCP − 원점) 을 베이스 좌표계에서 뺀 값에 Rz 180° 를 적용한 값. 원점이 없으면 0 을 쏜다"),
    ("286", "arc_s (호 위 거리)", "로봇 → RCS", "태스크", "0.1 mm", "0 ~ 32767",
     "이번 줄에서 호를 따라 실제로 지난 거리. TPAC 의 스캔 축이 이 값을 쓴다"),
    ("287", "누적 호 길이", "로봇 → RCS", "태스크", "mm", "0 ~ 32767", "완료한 줄까지 더한 누적 + 이번 줄의 arc_s"),
    ("288", "눌림 상태 (v5)", "로봇 → RCS", "태스크", "코드", "0 감시 안 함 / 1 적정 / 2 덜 눌림 / 3 너무 눌림",
     "servoj 스캔 중 중앙 접촉 센서 판정. 줄이 끝나면 0 으로 되돌린다"),
    ("289", "눌림 보정량 (v5)", "로봇 → RCS", "태스크", "0.01 mm", "±32767 (= ±327.67 mm)",
     "이번 줄에서 TCP Z 로 밀어 넣은(+) / 뺀(−) 누적 보정량"),
    ("290", "state (진행 상태)", "로봇 → RCS", "태스크", "코드", "0 ~ 14 (5번 탭)",
     "RCS 의 격자 순회가 이 값과 295 로 셀 완료를 판정한다"),
    ("291 / 292", "row_idx / rows", "로봇 → RCS", "태스크", "개", "0 ~ 400", "완료한 줄 수 / 이번 격자의 전체 줄 수"),
    ("293", "alive (워치독)", "로봇 → RCS", "태스크", "-", "0 ~ 30000 순환",
     "제어주기마다 +1. 값이 멈추면 발행이 끊긴 것이다(RCS 는 좌표 표시를 멈춘다)"),
    ("294", "zero_ok", "로봇 → RCS", "태스크", "0/1", "1 = 원점 확정", "이 값이 1 이 되면 270~275 를 읽어 둔다"),
    ("295", "finished", "로봇 → RCS", "태스크", "0/1", "1 = 이번 격자 완료", "290 = 5 와 함께 셀 완료 판정에 쓴다"),
    ("296", "pitch", "로봇 → RCS", "태스크", "mm", "0 ~ 32767", "줄 간격(= 격자 높이 / 칸 수). 겹침은 계산 결과다"),
    ("297", "누적 이동 거리", "로봇 → RCS", "태스크", "cm", "0 ~ 32767", "16bit 범위 때문에 mm 가 아니라 cm 다 (최대 327 m)"),
    ("298", "progress", "로봇 → RCS", "태스크", "%", "0 / 100", "완료 시 100. 세부 진행은 286/287 로 본다"),
    ("299", "오류 코드", "로봇 → RCS", "태스크", "코드", "0 정상 / 1 ~ 8 (5번 탭)",
     "태스크가 halt() 하기 직전에 남긴다. 다음 시작(init)에서 0 으로 되돌린다. RCS 가 알람 + MQTT evt/error 로 내보낸다"),
    ("300 / 301 / 302", "3점 측정 X (중앙 / 좌 / 우)", "로봇 → RCS", "태스크", "0.1 mm", "±3276.7 mm",
     "벽면 2차식을 세우는 세 점의 절대 X. 301 이 원점 X 다"),
    ("303", "press_off", "로봇 → RCS", "태스크", "0.1 mm", "±3276.7 mm",
     "중앙에서 센서로 잰 적정 눌림 보정량. 계산한 접점을 TCP +Z 로 이만큼 더 민다"),
    ("304 / 305", "n_probe / n_hit", "로봇 → RCS", "태스크", "개", "0 ~ 3",
     "측정한 점 수 / 그중 센서로 실제 검출한 점 수. n_hit < 3 이면 보정값을 믿으면 안 된다"),
    ("330 ~ 335", "중앙 접점 pose", "로봇 → RCS", "태스크", "0.1 mm / mRad", "±3276.7 mm", "호의 중앙점(회전 포함). 곡률 중심 계산에 쓴다"),
    ("340 ~ 345", "우측 접점 pose", "로봇 → RCS", "태스크", "0.1 mm / mRad", "±3276.7 mm", "호의 끝점(Y− 끝)"),
    ("346", "arc_mode", "로봇 → RCS", "태스크", "코드", "0 자세 고정 / 1 자세 보간",
     "v4 movec 의 mode. v5(servoj)에서는 쓰지 않지만 값은 그대로 내보낸다"),
    ("350 ~ 355", "원점 pose", "로봇 → RCS", "태스크", "0.1 mm / mRad", "±3276.7 mm", "270~275 와 같은 값(프로브 단계에서 먼저 쓴다)"),

    ("section", "■ 비어 있는 주소 (여유분)"),
    ("277 ~ 279", "-", "-", "-", "-", "-", "예약"),
    ("322 ~ 329 / 336 ~ 339 / 347 ~ 349 / 356 ~ 383", "-", "-", "-", "-", "-",
     "예약. 범용 레지스터는 256~383 까지다"),
]


# ==================================================== 3. RCS 변환 (ROS·화면·MQTT)
# (레지스터, 로봇 쪽 값, RCS ROS 토픽/서비스, 변환, 쓰이는 곳)
RCS_MAP = [
    ("section", "■ 읽기 — 로봇 제어 노드가 10 Hz 로 읽어 ROS 토픽으로 편다"),
    ("66", "robot_mode", "robot/status/robot_mode (Int32)", "코드 → 문구", "화면 상단 로봇 상태"),
    ("71", "control_method", "robot/status/control_method (Int32)", "코드 → 문구", "원격 제어 여부 표시"),
    ("72", "operation_mode", "robot/status/operation_mode (Int32)", "코드 → 문구", "자동/수동 표시"),
    ("73 ~ 78", "관절 각도 [mRad]", "robot/status/joint_position (Float32MultiArray)",
     "× 1 (mRad 그대로)", "코봇 수동 화면의 관절값"),
    ("384 ~ 389", "TCP pose [0.1 mm / mRad]", "robot/status/tcp_pose (Float32MultiArray)",
     "위치 × 0.1 → mm, 회전 × 1 → mRad", "화면 좌표 표시, 작업 기록"),
    ("280 ~ 285", "원점 기준 상대좌표", "robot/status/tcp_pose_zero (Float32MultiArray)",
     "위치 × 0.1 → mm, 회전 × 1 → mRad", "스캔좌표 기록 파일, 화면의 경로 그림"),
    ("290 ~ 299", "진행 상태 10개", "robot/status/scan_state (Int32MultiArray)", "환산 없음(정수 그대로)",
     "격자 순회의 셀 완료 판정, 원점 대기(290 = 7) 알림, 오류(299) 알람 + MQTT evt/error, "
     "차량 대기(290 = 14) 안내, alive(293) 워치독"),
    ("fill", NEW_FILL, "276", "home_flag", "robot/status/at_home (Int32)", "0/1 → bool (바뀔 때만 발행)",
     "★ 차량·리프트 인터락. 태스크 상태와 함께 본다"),
    ("500", "task_state", "robot/status/task_state (Int32)", "코드 그대로",
     "인터락, 작업 영역 전송 보류, 수동 제어 잠금"),
    ("17", "speed_scale", "robot/status/speed_scale (Int32)", "2~100 밖의 값은 버린다", "속도 막대 표시"),
    ("-", "30001 알람 스트림", "robot/status/alarms (String)", "코드 → 문구", "알람 목록"),
    ("-", "세 채널 연결 여부", "robot/status/connected (Bool)", "-", "상단 Cobot 배지"),

    ("section", "■ 쓰기 — RCS 화면·MQTT 지시가 레지스터로 내려간다"),
    ("256 ~ 264 (+266=1)", "작업 영역 9개", "robot/command/work_area (Float32MultiArray)",
     "0.1 mm 자리는 RCS 가 × 10 해서 보낸다", "ERUT job_cmd 의 grid, 화면의 작업 영역 편집. "
     "태스크가 도는 중에는 보내지 않고 보류한다"),
    ("267", "scan_go", "robot/command/scan_go (Int32)", "1 = 허가",
     "ERUT req/start 또는 사내 MC probe_ack 을 받으면 1 을 쓴다"),
    ("268 / 269", "mark_target", "robot/command/mark_target (Float32MultiArray)", "mm 정수",
     "ERUT req/mark 의 좌표"),
    ("306", "linear_speed", "robot/command/linear_speed (Int32)", "mm/s", "설정 화면의 작업 속도"),
    ("307", "speed_ratio", "robot/command/speed_ratio (Int32)", "%",
     "속도 막대. 29999 'speed -set' 으로도 같이 보낸다(실시간 반영)"),
    ("310 ~ 315 (+308=1)", "Home_joint", "robot/command/home_joint (Float32MultiArray)",
     "mRad 정수로 환산", "코봇 조그 화면의 '홈 위치 저장'"),
    ("316 ~ 321 (+308=1)", "gripper_target_pose", "robot/command/start_pose (Float32MultiArray)",
     "위치 mm ÷ 0.1, 회전 mRad", "코봇 조그 화면의 '시작 위치 저장'"),
    ("fill", NEW_FILL, "309", "vehicle_ready", "robot/command/vehicle_ready (Int32)",
     "0/1 (바뀔 때 + 5초마다 재전송)",
     "★ 차량 정지·아웃트리거 고정·리프트 정지를 RCS 가 판단해서 쓴다(0.5초마다 다시 계산)"),
    ("fill", NEW_FILL, "276", "home_flag 내리기", "robot/command/jog_joint · jog_tcp, robot/command/move_home",
     "0 = 홈 밖", "★ 태스크가 돌지 않을 때는 노드가 직접 관리한다. 조그를 보내면 0, 홈 이동이 끝나면 1"),

    ("section", "■ 레지스터가 아닌 채널 (29999 / 30001)"),
    ("-", "play / pause / stop", "robot/dashboard/play · pause · stop (Trigger)", "29999 텍스트 명령",
     "셀마다 태스크를 세웠다 다시 시작한다"),
    ("-", "power / brakeRelease / remote", "robot/dashboard/power_on · power_off · brake_release · remote_control_on",
     "29999 텍스트 명령", "로봇 준비 절차"),
    ("-", "task -p (태스크 교체)", "robot/dashboard/load_scan_task · load_mark_task", "29999 텍스트 명령",
     "스캔 ↔ 마킹 태스크 교체. 경로는 태스크 판 설정(dusan_v4 / dusan_v5)을 따른다"),
    ("-", "조그", "robot/command/jog_joint · jog_tcp (Int32)", "30001 speedj / speedl",
     "부호가 방향, 절댓값이 축 번호(1~6). 손을 떼면 29999 stop"),
    ("-", "홈 이동", "robot/command/move_home (Trigger)", "29999 stop → 30001 프로그램 전송",
     "TCP −Z 로 물러나고 → 홈 높이로 올라가고 → movej. 끝나면 276 에 1 을 쓴다"),
]

# ======================================================== 4. TPAC 변환 (UR 호환)
TPAC_MAP = [
    ("section", "■ 브리지가 로봇에서 읽는 자리 (기본 100 ms 주기)"),
    ("63 ~ 110", "버전·상태·관절(각도/속도/전류/온도)", "-", "한 블록(48워드)으로 읽는다"),
    ("280 ~ 287", "원점 기준 좌표 + 호 위치", "-", "여덟 워드를 한 번에 읽는다 — 나눠 읽으면 좌표와 호 위치가 다른 시점의 값이 된다"),
    ("384 ~ 389", "TCP pose (베이스)", "-", "'좌표 원본' 설정이 pose 일 때 쓴다"),
    ("400 ~ 405", "TCP 속도", "-", "로봇이 직접 주는 값이라 추정하지 않는다"),
    ("306 / 307", "작업 속도 / 속도 비율", "-", "화면 표시용"),
    ("500", "태스크 실행 상태", "-", "1 Running / 2 Pause / 3 Stopped"),

    ("section", "■ 브리지가 TPAC 에 내주는 자리 (UR Modbus 서버와 같은 주소)"),
    ("256 / 257", "컨트롤러 버전", "63 / 64", "UR 자리 그대로"),
    ("258", "Robot mode", "66", "7 = Running"),
    ("260", "isPowerOnRobot", "67", "0/1"),
    ("261", "isSecurityStopped", "68", "보호 정지"),
    ("262", "isEmergencyStopped", "69", "비상 정지"),
    ("263 / 264", "isTeachButtonPressed / isPowerButtonPressed", "-", "항상 0 (Elite 에는 해당 자리가 없다)"),
    ("265", "isSafetySignal…", "68 / 69", "둘 중 하나라도 서면 1"),
    ("270 ~ 275", "관절 각도 [mRad]", "73 ~ 78", "UR 의 관절 자리로 옮겨 넣는다"),
    ("400 ~ 405", "TCP pose", "384~389 또는 280~285", "'좌표 원본' 설정에 따라 절대 좌표 / 원점 기준 좌표"),
    ("410 ~ 415", "TCP 속도", "400 ~ 405", "mm/s, mRad/s"),
    ("420 ~ 425", "TCP offset", "-", "장치가 묻는 세 블록(좌표·속도·오프셋) 중 마지막 자리"),
    ("450 / 451", "로봇 전류 / I/O 전류", "95 ~ 100 합", "mA"),
    ("-", "스캔 축(C-scan)", "286 / 287", "좌표(현)가 아니라 호 길이를 그대로 넘긴다 — 눌리거나 비선형으로 밀리지 않게"),
]

# =============================================================== 5. 코드표
CODES = [
    ("section", "■ 290 — 진행 상태 (태스크가 쓴다)"),
    ("0", "대기 / 초기화", "dus_init 이 시작할 때"),
    ("2", "3점 측정 중", "중앙·좌·우 접점을 찾는 중"),
    ("3", "원점 복귀 중", "측정이 끝나고 원점으로 이동"),
    ("4", "스캔 중 (v4 movec)", "v4 의 ㄹ자 패스"),
    ("5", "완료", "이번 격자 끝. 295 = 1 과 함께 나온다. ★ 이 뒤에 홈으로 이동한다"),
    ("6", "스캔 구간 (v5)", "servoj 호 + 줄 바꿈 상승까지 포함. 이 동안만 280~285 가 살아 있다"),
    ("7", "원점 도착, 시작 신호 대기", "267 에 1 이 들어올 때까지 선다"),
    ("8", "적심(비비기)", "스캔 직전 검사면에 물을 퍼뜨린다"),
    ("9", "오류 정지", "299 에 사유가 있다"),
    ("10 ~ 13", "마킹 태스크", "10 이동 중 / 11 벽에 붙음 / 13 홈으로 / 12 홈 도착(끝)"),
    ("fill", NEW_FILL, "14", "차량 고정 대기", "★ 신규. 309 가 1 이 될 때까지 선다"),

    ("section", "■ 299 — 정지 사유 (0 = 정상)"),
    ("1 / 2 / 3", "중앙 / 좌측(원점) / 우측 프로브 벽 접촉 실패", "계산한 자리에서 벽을 못 찾았다"),
    ("4", "호 길이·반지름이 0", "작업 영역 값을 확인한다"),
    ("5", "마킹 자리에서 벽 접촉 실패", "마킹 태스크"),
    ("6", "스캔 중 눌림 유지 실패 (v5)", "한 줄 보정량이 press_max 를 넘었다"),
    ("7", "호 경로 역기구학 해 없음 (v5)", "로봇 위치·자세를 확인한다"),
    ("fill", NEW_FILL, "8", "차량 고정 확인 시간 초과", "★ 신규. 309 를 약 120초 기다려도 1 이 안 됐다"),

    ("section", "■ 288 — 눌림 상태 (v5 스캔 중)"),
    ("0", "감시 안 함", "스캔 줄이 아니다"),
    ("1", "적정", "두 센서가 적정 범위"),
    ("2", "덜 눌림", "TCP +Z 로 더 민다"),
    ("3", "너무 눌림", "TCP −Z 로 뺀다"),

    ("section", "■ 500 — 태스크 상태 (컨트롤러)"),
    ("1", "실행 중", "이 동안에는 차량·리프트를 움직이지 않는다"),
    ("2", "일시 중지", ""),
    ("3", "중지됨", "홈 이동까지 끝난 상태"),

    ("section", "■ 66 — robot_mode (컨트롤러)"),
    ("0 / 1 / 2", "DISCONNECTED / CONFIRM_SAFETY / BOOTING", ""),
    ("3 / 4 / 5", "POWER_OFF / POWER_ON / IDLE", ""),
    ("6 / 7", "BACKDRIVE / RUNNING", "7 이어야 태스크가 돈다"),
    ("8 / 9", "UPDATING_FW / WAIT_CALIB", ""),

    ("section", "■ 그 밖의 값"),
    ("71", "control_method", "0 원격 미개방 / 1 로컬 / 2 원격 — RCS 명령을 받으려면 2"),
    ("72", "operation_mode", "-1 지정 안 됨 / 0 자동 / 1 수동"),
    ("264", "eoat_type", "0 없음 / 5 5축 십자 / 8 8축 직사각"),
    ("266", "param_src", "0 펜던트 변수 / 1 레지스터(256~264). 차량 인터락도 1 일 때만 본다"),
    ("308", "pose_src", "0 펜던트 변수 / 1 레지스터(310~321)"),
    ("fill", NEW_FILL, "309", "vehicle_ready", "★ 0 = 차량이 움직이는 중 / 1 = 정지·고정 확인"),
    ("fill", NEW_FILL, "276", "home_flag", "★ 0 = 홈 밖 / 1 = 홈. 허용 오차는 펜던트 변수 home_tol_j(0.02 rad)·home_tol_p(5 mm)"),
]


def main() -> None:
    wb = Workbook()
    wb.remove(wb.active)

    ws = sheet(
        wb, "1.통신 채널",
        "3S ↔ Elite CS612 통신 채널",
        "로봇과 RCS 사이에 실제로 오가는 통로. 주기·지연은 우리 코드가 정한 값이며, "
        "회선 자체의 왕복 시간은 현장 LAN 에서 따로 재야 한다. "
        "(출처: robot_control_node.py, tpac_bridge/bridge_core.py, dusan_task_v5 스크립트)",
        ["프로토콜", "포트", "구간", "주기", "지연", "실시간 적합성", "쓰는 값 / 비고"],
        [18, 8, 30, 24, 22, 30, 62])
    rows(ws, CHANNELS, 7)

    ws = sheet(
        wb, "2.주소별 레지스터",
        "주소별 레지스터 맵 (Modbus TCP 502, 홀딩 레지스터 FC3 / FC6)",
        "모든 값은 16bit 정수다. 부호 있는 값은 반드시 signed 로 읽는다(unsigned 로 읽으면 -100 이 65436 으로 보인다). "
        "환산: mm = signed ÷ 10, m = signed ÷ 10000, rad = signed ÷ 1000. "
        "노랑 = 이번에 새로 만든 주소, 주황 = 한 주소를 두 가지로 쓰므로 주의할 자리. "
        "v4 · v5 태스크가 같은 맵을 쓴다.",
        ["주소", "이름", "방향", "쓰는 주체", "단위 · 환산", "값 · 범위", "설명"],
        [16, 26, 14, 14, 24, 28, 78])
    rows(ws, REGISTERS, 7)

    ws = sheet(
        wb, "3.RCS 변환",
        "RCS 가 변환해 주는 부분 — 레지스터 ↔ ROS 2 ↔ 화면 · MQTT",
        "로봇 제어 노드(elite_robot_controller)가 Modbus 를 ROS 토픽으로 바꾸고, "
        "운영 화면(operator-ui)이 그것을 화면 값과 MQTT 로 바꾼다. "
        "레지스터 주소는 config/modbus_registers.json 한 곳에서만 관리한다.",
        ["레지스터", "로봇 쪽 값", "ROS 토픽 / 서비스", "변환", "쓰이는 곳"],
        [22, 30, 46, 34, 62])
    rows(ws, RCS_MAP, 5)

    ws = sheet(
        wb, "4.TPAC 변환",
        "TPAC 변환 — Elite 주소를 UR Modbus 서버와 같은 자리로 내준다",
        "TPAC(UT 장비)은 UR 기준으로 만들어져 있어 주소가 고정이다. "
        "RCS 의 브리지가 로봇을 읽어 UR 자리에 그대로 재현하므로 TPAC 설정을 바꾸지 않아도 된다. "
        "브리지는 FC3 · FC4 모두 응답한다.",
        ["TPAC 가 읽는 주소 (UR 자리)", "내용", "로봇 쪽 원본 주소", "비고"],
        [26, 40, 24, 70])
    rows(ws, TPAC_MAP, 4)

    ws = sheet(
        wb, "5.코드표",
        "상태 · 오류 코드",
        "레지스터에 들어가는 숫자의 뜻. 태스크가 쓰는 값(290 / 299 / 288)과 "
        "컨트롤러가 주는 값(500 / 66 / 71 / 72)을 함께 모았다.",
        ["값", "뜻", "설명"],
        [16, 40, 86])
    rows(ws, CODES, 3)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT)
    print(f"만들었습니다: {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
