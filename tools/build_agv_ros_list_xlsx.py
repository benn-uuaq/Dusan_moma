"""AGV 노드 — ROS 2 노드 · 토픽 · 서비스 · 메시지 목록 (AGV 담당에게 주는 문서).

RCS_AGV_인터페이스 문서가 읽기 어렵다고 해서 AGV 쪽에서 보는 순서로 다시 짰다(2026-11-19).
같은 날 고침: 아웃트리거 개별 제어를 뺐고(전체 내리기 · 올리기만), 리프트를 SetMvLift 로 따로 뺐다
(자동 set_mv_lift_auto · 수동 set_mv_lift_manual).
2026-11-20: AGV 담당이 고친 vehicle_interfaces 에 맞췄다 — 리프트 자동은 SetMvLift(set_mv_lift) 하나, 수동은
ManualCommand(cmd_mv_lift · lift_height, 속도는 PLC), SetJob.radius · ResetOdometry 추가, PLC 전용 필드 · 서비스
없음(PLC 끊김은 error_code 701 로 보고 다시 연결은 robot_control reset). vehicle_interfaces 는 AGV 쪽 판이 기준이다.
2026-11-20 오후: RobotControl 에 job_finish 를 더했다 — 차량은 도착한 뒤 홀딩을 잡고 기다리다가, 받으면 홀딩을 풀고
아웃트리거를 올려 이동할 수 있는 상태가 된다. RCS 는 다음 주행 전에(리프트를 내린 뒤) 보낸다.

  1.한눈에        두 노드 사이에 오가는 9개를 그림으로
  2.목록          AGV 노드가 발행 · 구독 · 서버로 열 것 9개 — 이름 · 타입은 그대로 복사
  3.필드          메시지 · 서비스 필드 하나에 한 줄 — 넣는 값 · 예 · RCS 가 쓰는 곳
  4.상태·값       state 등 정해진 값, 상태별로 명령을 받는지
  5.흐름 예       주고받는 순서(사다리 그림)
  6.오류 번호     robot_status.error_code (build_rcs_agv_interface_xlsx 의 표를 그대로 가져온다)
  7.확인 명령     ros2 명령으로 AGV 노드만 시험하기
  8.원문          vehicle_interfaces 의 .msg · .srv 파일 그대로

값이 바뀌면 코드(operator-ui services/vehicle_* · src/vehicle_sim)를 먼저 맞추고 다시 만든다. src/vehicle_interfaces 는
AGV 담당 판을 그대로 쓴다(3S 가 고치지 않는다).
실행:  python3 tools/build_agv_ros_list_xlsx.py
"""

import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_modbus_interface_xlsx import (  # noqa: E402
    BODY_FONT, BOLD_BODY, BORDER, CENTER, FONT, HEAD_FILL, HEAD_FONT, LEFT, NOTE_FONT, SECT_FILL, SECT_FONT,
    TITLE_FONT,
)
from build_rcs_agv_interface_xlsx import ERROR_HEAD, ERROR_RULES, ERRORS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "AGV_노드_토픽_서비스_목록_20261119.xlsx"
IFACE = ROOT / "src" / "vehicle_interfaces"
NS = "/vehicle"
DATE = "2026-11-20"

CODE_FONT = Font(name="Consolas", size=9)
CODE_BOLD = Font(name="Consolas", size=10, bold=True)
SUB_FILL = PatternFill("solid", fgColor="FFF2F2F2")
TOPIC_FILL = PatternFill("solid", fgColor="FFE2EFDA")      # 토픽
SERVICE_FILL = PatternFill("solid", fgColor="FFDDEBF7")    # 서비스
BOX_FILL = PatternFill("solid", fgColor="FF1F3864")        # 노드 상자
BOX_FONT = Font(name=FONT, size=11, bold=True, color="FFFFFFFF")
ARROW_FONT = Font(name=FONT, size=12, bold=True, color="FF1F3864")
OK_FONT = Font(name=FONT, size=9, bold=True, color="FF375623")
NO_FONT = Font(name=FONT, size=9, bold=True, color="FFC00000")
NO_WRAP = Alignment(wrap_text=False, vertical="center", horizontal="center")   # 화살표


# ================================================================= 1. 한눈에
#: (종류, 왼쪽 화살표, 이름, 타입 · 때, 오른쪽 화살표). ◀ = AGV → RCS, ▶ = RCS → AGV
DIAGRAM = [
    ("sub", "토픽 — 계속 흐르는 값"),
    ("topic", "◀━━", f"{NS}/robot_status", "RobotStatus  ·  AGV 가 10 Hz 로 계속 발행", "━━━"),
    ("topic", "◀━━", f"{NS}/start_request", "std_msgs/Empty  ·  차량 「작업 시작」 버튼 누를 때", "━━━"),
    ("topic", "━━━", f"{NS}/robot_estop", "std_msgs/Bool  ·  로봇 비상정지 — 바뀔 때 + 0.5 초마다", "━━▶"),
    ("sub", "서비스 — RCS 가 부르면(▶ 요청) AGV 가 바로 답한다(◀ 응답)"),
    ("service", "◀━━", f"{NS}/set_job", "SetJob  ·  주행 한 번 설정(거리 · 속도 · 거리 유지 · 모재 반지름)", "━━▶"),
    ("service", "◀━━", f"{NS}/robot_control", "RobotControl  ·  출발 · 정지 · 일시정지 · 리셋(PLC 다시 연결 포함) · 작업 끝", "━━▶"),
    ("service", "◀━━", f"{NS}/manual_command", "ManualCommand  ·  수동 조그 · 아웃트리거 전체 · 수동 리프트 · 원점", "━━▶"),
    ("service", "◀━━", f"{NS}/set_mv_lift", "SetMvLift  ·  리프트 높이 — 자동 동작 중(작업 순서)", "━━▶"),
    ("service", "◀━━", f"{NS}/measure_lap", "MeasureLap  ·  캘리브레이션 주행(원통 한 바퀴 · 곡판 QR 정렬)", "━━▶"),
    ("service", "◀━━", f"{NS}/reset_odometry", "ResetOdometry  ·  차량 위치(x · y · yaw) 다시 맞추기", "━━▶"),
]

#: 준비]

#: 준비 — (항목, 내용)
SETUP = [
    ("ROS 2", "Humble. RCS PC 와 AGV PC 가 같은 네트워크 · 같은 ROS_DOMAIN_ID(기본 0) · RMW 기본(Fast DDS). "
              "방화벽에서 DDS(UDP 7400~7500 대)를 막지 않는다"),
    ("메시지 패키지", "vehicle_interfaces — AGV 담당 판(2026-11-20)이 기준이다. RCS PC · AGV PC 가 같은 파일을 빌드해 "
                 "쓴다: colcon build --packages-select vehicle_interfaces. 필드 이름 · 타입이 글자까지 같아야 "
                 "통신된다 — 고치면 서로 파일을 다시 주고받는다"),
    ("AGV 노드", "AGV 담당이 만든다. 노드 이름 · 패키지 이름은 자유. 노드 하나가 위 9개를 모두 연다 — 토픽 2개 발행, "
               "1개 구독, 서비스 6개 서버. PLC 통신은 이 노드 안에서만 한다(RCS 는 PLC 를 직접 보지 않는다 — "
               "끊기면 error_code 701 로 알린다)"),
    ("RCS 노드", "/smr_operator_ui (3S RCS PC) — 반대편: 구독 2 · 발행 1 · 서비스 클라이언트 6"),
    ("네임스페이스", "모든 이름 앞 /vehicle. RCS 는 환경변수 SMR_VEHICLE_NS 로 바꿀 수 있다(AGV 와 같아야 한다)"),
    ("QoS · 단위", "QoS 는 모두 ROS 2 기본값(reliable · volatile · keep last 10). 거리 m · 속도 m/s · 각도 rad · 배터리 %"),
    ("서비스 응답", "받자마자(수 ms 안) 응답한다 — 「받았다 / 거절(까닭)」만. 다 끝났는지는 robot_status 로 알린다"),
    ("참고 구현", "3S 모의기 vehicle_sim 이 AGV 노드 대신 같은 9개를 같은 규칙으로 연다"
               "(dusan_ws/src/vehicle_sim/vehicle_sim/vehicle_sim_node.py). 시험은 7.확인 명령"),
]


# ================================================================= 2. 목록
LIST_HEAD = ["번호", "AGV 노드가", "종류", "이름 (그대로 복사)", "타입 (그대로 복사)", "언제 · 주기", "무엇", "AGV 가 지킬 것"]
LIST = [
    ("section", "AGV 가 발행한다 (publisher) — RCS 가 구독"),
    ("1", "발행", "토픽", f"{NS}/robot_status", "vehicle_interfaces/msg/RobotStatus",
     "10 Hz 로 계속. 2 초 넘게 안 오면 RCS 는 「차량 끊김」",
     "차량 상태 전부 — 상태 · 이동 거리 · 리프트 높이 · 아웃트리거 · 오류 · 안전 · 배터리 · 캘리브레이션 결과",
     "움직이는 동안 state = RUNNING 이 적어도 한 번은 실려야 RCS 가 도착을 판정한다(짧은 이동도 — 그래서 10 Hz). "
     "job_id 는 받은 set_job · measure_lap 의 것을 그대로 싣는다"),
    ("2", "발행", "토픽", f"{NS}/start_request", "std_msgs/msg/Empty",
     "차량 「작업 시작」 버튼을 누를 때 한 번", "RCS 에 「계측(캘리브레이션) 시작」을 부탁한다",
     "차량은 스스로 움직이지 않는다 — RCS 가 이어서 measure_lap 등을 보낸다"),
    ("section", "AGV 가 구독한다 (subscriber) — RCS 가 발행"),
    ("3", "구독", "토픽", f"{NS}/robot_estop", "std_msgs/msg/Bool",
     "바뀌면 바로 + 0.5 초마다 같은 값", "로봇 컨트롤러 비상정지 상태 (data true = 눌림)",
     "true 면 차량도 비상정지 · 움직이는 명령 거절 · robot_status.estop = true. false 가 되면 차량 리셋 규칙대로 푼다. "
     "실제 동시 정지는 안전 회로 직결(PLC ↔ 로봇 컨트롤러)이 맡고, 이 토픽은 알림이다"),
    ("section", "AGV 가 서비스 서버가 된다 (service server) — RCS 가 호출(client)"),
    ("4", "서버", "서비스", f"{NS}/set_job", "vehicle_interfaces/srv/SetJob",
     "구간 이동마다", "주행 한 번 설정 — 이번에 갈 거리 · 속도 · 모재 거리 유지 · 모재 반지름",
     "받기만 하고 출발하지 않는다 — 출발은 robot_control RUNNING. 받으면 mv_dist = 0 으로"),
    ("5", "서버", "서비스", f"{NS}/robot_control", "vehicle_interfaces/srv/RobotControl",
     "출발 · 정지 · 일시정지 · 재개 · 오류 리셋 때", "state = RUNNING · STOP · PAUSED, reset = 1",
     "STOP 은 언제든 받는다. 그 밖에는 4.상태·값 의 표대로 받거나 거절(success = false + 까닭). "
     "PLC 끊김(701) 중 reset = 1 이면 PLC 에 다시 붙어 본다 — RCS 「PLC 다시 연결」"),
    ("6", "서버", "서비스", f"{NS}/manual_command", "vehicle_interfaces/srv/ManualCommand",
     "자동 순서의 아웃트리거 내리기 · 올리기 때 / 수동 조그는 누르는 동안 200 ms 마다 / 수동 리프트",
     "아웃트리거 전체 내리기(고정) · 올리기(해제), 전후진 · 회전 조그, 수동 리프트(cmd_mv_lift · lift_height), 원점 / 초기화",
     "0 / false 필드 = 명령 없음. 조그가 0.5 초 넘게 안 오면 스스로 선다. 아웃트리거는 균형을 맞춰 세 개를 함께 "
     "움직인다(개별 명령 없음). 수동 리프트 속도는 PLC 가 정한다. 자동 동작 중 리프트는 7"),
    ("7", "서버", "서비스", f"{NS}/set_mv_lift", "vehicle_interfaces/srv/SetMvLift",
     "자동 동작 중 — 구역마다(구역 높이) · 이동 전(0)", "리프트를 목표 높이로 — 높이 · 속도",
     "아웃트리거 고정(hold SET)에서만. 받으면 바로 응답, 도착은 robot_status.lift_h(목표 ±2 mm). set_speed 는 "
     "자동에서만 쓴다. 리프트가 움직이면 PLC 가 로봇을 안전정지시킨다 — RCS 는 안전재설정(safety reset)을 받은 것을 "
     "확인한 뒤에 로봇을 출발시킨다"),
    ("8", "서버", "서비스", f"{NS}/measure_lap", "vehicle_interfaces/srv/MeasureLap",
     "캘리브레이션마다", "원통: 모재를 한 바퀴(mode LAP) · 곡판: 판 가운데 QR 에 정렬만(mode ALIGN)",
     "받으면 바로 출발. 진행 · 결과는 robot_status 의 lap_state · lap_path_m · lap_origin_ok · lap_error"),
    ("9", "서버", "서비스", f"{NS}/reset_odometry", "vehicle_interfaces/srv/ResetOdometry",
     "RCS 수동 제어 화면 「위치 다시 맞추기」를 누를 때", "차량 위치를 받은 x · y [m] · yaw [rad] 로 다시 맞춘다",
     "받으면 robot_status.position 의 x · y · yaw 를 그 값으로 바꾼다(z · roll · pitch 는 그대로). 움직이는 중이면 "
     "거절해도 된다. RCS 는 자동 작업 중에는 보내지 않는다"),
]


# ================================================================= 3. 필드]


# ================================================================= 3. 필드
FIELD_HEAD = ["필드", "타입", "단위", "기본값", "넣는 값 · 뜻", "예", "RCS 가 쓰는 곳"]
FIELDS = [
    ("section", f"1  {NS}/robot_status  —  vehicle_interfaces/msg/RobotStatus   (AGV 가 채워 10 Hz 로 발행)"),
    ("header", "std_msgs/Header", "-", "-", "stamp = 보낸 시각. frame_id 는 비워도 된다", "stamp: now()", "쓰지 않음"),
    ("state", "string", "-", "\"\"", "지금 상태 — STOP · HOLD · RUNNING · PAUSED · ERROR (4.상태·값)", "\"RUNNING\"",
     "도착 판정 · 화면 상태"),
    ("hold", "string", "-", "\"\"", "아웃트리거 — SET(고정 · 접지 끝) · RELEASE(해제 · 다 올림). 움직이는 중에는 앞 값 그대로",
     "\"SET\"", "아웃트리거 고정 · 해제 끝 판정. SET 이어야 로봇이 스캔한다"),
    ("error_code", "int32", "-", "0", "0 = 정상, 그 외 = 차량 오류 번호(6.오류 번호). 0 이 아니면 state = ERROR. "
     "PLC 통신이 끊기면 701(PLC_COMM_LOST) — 다시 붙으면 AGV 가 스스로 0 으로", "304",
     "알람 [E2001] · 작업 정지 · ERUT E2001. 701 이면 상단 「PLC」 · 연결 설정 「PLC 연결」이 「연결 실패」"),
    ("error_msg", "string", "-", "\"\"", "「번호 이름 — 상세」", "\"304 OUTRIGGER_LOST — 2번 다리 접지 풀림\"",
     "알람 글 · ERUT detail"),
    ("job_id", "string", "-", "\"\"",
     "마지막으로 받은 set_job · measure_lap 의 job_id 그대로(set_mv_lift 의 job_id 는 실어도 되고 안 실어도 된다)",
     "\"RCS-0007\"", "주행 도착 판정 — 다르면 RCS 는 그 상태를 이번 이동 것으로 보지 않는다(리프트는 lift_h 로만 본다)"),
    ("set_dist", "float64", "m", "0", "받은 set_job.set_dist 그대로", "0.350", "화면"),
    ("mv_dist", "float64", "m", "0", "이번 이동에서 차량 중심이 달린 거리 — set_job 받을 때 0, 부호는 set_dist 와 같게",
     "0.348", "도착 판정 — state STOP/HOLD 이고 |mv_dist − set_dist| ≤ 0.002 m"),
    ("speed", "float64", "m/s", "0", "지금 속도(서 있으면 0)", "0.100", "화면"),
    ("sen1_dist", "float64", "m", "0", "거리 센서 1", "2.013", "화면"),
    ("sen2_dist", "float64", "m", "0", "거리 센서 2", "2.009", "화면"),
    ("position", "vehicle_interfaces/msg/Position6D", "m · rad", "0",
     "x · y · z [m] · roll · pitch · yaw [rad] — 차량 위치 · 자세(기준은 AGV 가 정해 알려 준다)",
     "{x: 1.2, y: 0.0, z: 0.0, roll: 0.0, pitch: 0.003, yaw: 1.57}",
     "수동 화면 「위치 x / yaw」. reset_odometry 로 다시 맞춘다"),
    ("lift_h", "float64", "m", "0", "리프트 높이(맨 아래 0)", "1.200",
     "리프트 도착 판정 — |lift_h − set_height| ≤ 0.002 m"),
    ("battery_pct", "uint8", "%", "255", "배터리 잔량 0~100. 255 = 모름 · 배터리 없음", "76",
     "상단 배터리 · ERUT 상태 (255 면 싣지 않는다)"),
    ("charging", "bool", "-", "false", "충전 중", "false", "상단 배터리 · ERUT 상태"),
    ("estop", "bool", "-", "false", "차량(PLC) 비상정지 중 — 차량 버튼 · robot_estop · 안전 회로 어느 것으로 걸렸든 true",
     "false", "로봇 · 작업 정지 · ERUT E1002"),
    ("obstacle_stop", "bool", "-", "false", "접근 감지(안전 스캐너)로 섰다", "false", "작업 일시정지 · 알람"),
    ("level_state", "int8", "-", "-1", "아웃트리거 수평 — 1 맞음 · 0 안 맞음 · -1 모름", "1", "0 이면 로봇 스캔을 막는다"),
    ("standoff_meas", "float64", "m", "-1", "지금 잰 거리 — 차량 안쪽 면 ↔ 모재 표면. -1 = 모름", "2.004", "화면"),
    ("lap_state", "string", "-", "\"IDLE\"", "measure_lap 진행 — IDLE · RUNNING · DONE · FAILED", "\"DONE\"",
     "캘리브레이션 끝 판정"),
    ("lap_path_m", "float64", "m", "0", "이번 measure_lap 에서 차량 중심이 달린 거리(ALIGN 은 정렬하며 움직인 거리)", "15.692",
     "LAP: 모재 둘레 계산 → ERUT total_length_mm"),
    ("lap_origin_ok", "bool", "-", "false", "LAP: 원점 QR 을 다시 찾아 섰다 · ALIGN: QR 에 맞춰 섰다", "true",
     "DONE 이어도 false 면 캘리브레이션 실패"),
    ("lap_error", "string", "-", "\"\"", "FAILED 일 때 까닭", "\"QR not found\"", "알람 · ERUT detail"),
    ("note", "기본값 = 모름 — 아직 못 채우는 필드는 기본값 그대로 둔다. RCS 는 기본값을 「모름」으로 보고 조건에서 뺀다 "
             "(battery_pct 255 · level_state -1 · standoff_meas -1). state · hold · error_code · job_id · mv_dist · lift_h · "
             "estop · lap_* 는 꼭 채운다. PLC 연결 상태 필드는 없다 — RCS 는 상태가 오는지와 error_code 701 로 본다"),

    ("section", f"2  {NS}/start_request  —  std_msgs/msg/Empty   (AGV 가 발행)"),
    ("(필드 없음)", "-", "-", "-", "차량 「작업 시작」 버튼이 눌렸다는 뜻만 있다. 누를 때 한 번 낸다", "{}", "계측(캘리브레이션) 시작"),

    ("section", f"3  {NS}/robot_estop  —  std_msgs/msg/Bool   (RCS 가 발행 → AGV 가 구독)"),
    ("data", "bool", "-", "false", "true = 로봇 컨트롤러 비상정지 눌림 · false = 풀림. 바뀌면 바로 + 0.5 초마다 같은 값", "true",
     "(AGV 가 쓴다 — 2.목록 3)"),

    ("section", f"4  {NS}/set_job  —  vehicle_interfaces/srv/SetJob"),
    ("sub", "요청 — RCS 가 채워 보낸다"),
    ("job_id", "string", "-", "-", "이동마다 새 번호 — robot_status.job_id 로 돌려준다", "\"RCS-0007\"", "RCS-0001 부터 하나씩"),
    ("set_dist", "float64", "m", "-",
     "이번 이동에서 차량 중심이 달릴 거리. 부호 = 방향(+ 원주 진행 방향 — 실제 방향은 AGV 와 정한다). 누적이 아니다",
     "0.350", "구역 사이 표면 거리 × 주행 배율"),
    ("set_speed", "float64", "m/s", "-", "목표 속도", "0.100", "RCS 차량 설정 「차량 주행 속도」(기본 0.1)"),
    ("standoff", "float64", "m", "-", "달리며 지킬 거리 — 차량 안쪽 면 ↔ 모재 표면", "2.000", "RCS 차량 설정 「모재 거리 유지」"),
    ("radius", "float64", "m", "-", "목표 대상(모재) 반지름. 모르면 0", "2.500",
     "검사 대상 바깥 지름 / 2 (메인 화면 「검사 대상」 · ERUT calibrate) — 보낼 때마다 지금 값"),
    ("sub", "응답 — AGV 가 채워 답한다"),
    ("success", "bool", "-", "-", "받았으면 true(출발은 하지 않는다). 못 받으면 false", "true", "false 면 알람 · 그 이동 실패"),
    ("message", "string", "-", "-", "결과 · 거절 까닭", "\"Rejected: busy\"", "알람 글"),

    ("section", f"5  {NS}/robot_control  —  vehicle_interfaces/srv/RobotControl"),
    ("sub", "요청 — RCS 가 채워 보낸다"),
    ("state", "string", "-", "\"\"",
     "RUNNING = 출발(set_job 받은 뒤) · 재개(PAUSED 에서). STOP = 정지 — 무엇이든 멈춘다(measure_lap 은 lap_state FAILED). "
     "PAUSED = measure_lap 일시정지(이어서 RUNNING 으로 재개). 빈 글자 = 상태는 그대로(reset 만 보낼 때). "
     "RCS 가 보내는 값은 이 넷뿐", "\"RUNNING\"",
     "set_job 이동을 일시정지할 때는 STOP 을 보내고, 재개는 남은 거리로 새 set_job + RUNNING"),
    ("reset", "uint8", "-", "0", "1 = 오류 리셋 — 원인이 없어졌으면 error_code 0 · state STOP/HOLD. "
     "PLC 끊김(701) 중이면 PLC 에 다시 붙어 보고, 붙으면 701 을 푼다", "1",
     "수동 화면 「오류 리셋」 · RCS 알람 리셋 · ERUT reset · 연결 설정 「PLC 다시 연결」"),
    ("job_cancel", "uint8", "-", "0", "1 = 작업 취소 — 하던 이동을 멈추고 잊는다", "0", "지금은 보내지 않는다(STOP 으로 대신)"),
    ("job_finish", "uint8", "-", "0",
     "1 = 작업 끝 — 그 자리 일이 끝났다. 홀딩(브레이크)을 풀고 아웃트리거를 올려 이동할 수 있는 상태로(hold RELEASE · "
     "state STOP), 작업을 잊는다(job_id \"\"). 서 있으면 끝낼 작업이 없어도 받는다. 움직이는 중이면 거절", "1",
     "다음 주행 전 — 리프트를 0 으로 내린 뒤 보내고 hold RELEASE 를 기다린다(RCS 제한 30 초). 2026-11-20 추가"),
    ("sub", "응답 — AGV 가 채워 답한다"),
    ("success", "bool", "-", "-", "받았으면 true. 못 받으면 false(4.상태·값 의 표)", "false", "false 면 알람"),
    ("message", "string", "-", "-", "결과 · 거절 까닭", "\"Rejected: error — reset first\"", "알람 글"),

    ("section", f"6  {NS}/manual_command  —  vehicle_interfaces/srv/ManualCommand"),
    ("sub", "요청 — RCS 가 채워 보낸다. 보내지 않은 필드는 0 / false = 명령 없음"),
    ("cmd_mv_fwd", "bool", "-", "false", "true 동안 전진", "true",
     "조그 버튼 — 누르는 동안 200 ms 마다 다시, 떼면 모든 필드 0 / false"),
    ("cmd_mv_rear", "bool", "-", "false", "true 동안 후진", "false", "(위와 같다)"),
    ("cmd_trn_left", "bool", "-", "false", "true 동안 좌회전", "false", "(위와 같다)"),
    ("cmd_trn_right", "bool", "-", "false", "true 동안 우회전", "false", "(위와 같다)"),
    ("cmd_outrg_set", "uint8", "-", "0",
     "아웃트리거 전체 — 1 해제(올리기) · 2 고정(내리기) · 0 그대로. 세 개를 균형 맞춰 함께 움직인다(개별 명령 없음). "
     "끝나면 hold = RELEASE · SET", "2", "자동 순서(이동 전 해제 · 스캔 전 고정) · 수동 「전체 내리기 · 올리기」 버튼"),
    ("cmd_mv_lift", "uint8", "-", "0", "1 = lift_height 로 리프트 이동(수동) · 0 그대로. 아웃트리거 고정(hold SET)에서만. "
     "속도는 PLC 가 정한다", "1", "수동 「이 높이로 이동」 버튼"),
    ("cmd_init", "uint8", "-", "0", "1 = 수동 모드 원점 / 초기화", "0", "수동 버튼"),
    ("lift_height", "float64", "m", "0", "cmd_mv_lift 1 일 때 목표 높이 — 절대 높이(맨 아래 0). 리프트 한계 밖이면 거절",
     "0.800", "수동 화면에 작업자가 넣은 높이"),
    ("note", "2026-11-19 에 뺀 필드: cmd_man_outrg1~3(아웃트리거 개별 — 균형 때문에)"),
    ("sub", "응답 — AGV 가 채워 답한다"),
    ("success", "bool", "-", "-", "받았으면 true. 수동 모드가 아니면(RUNNING · PAUSED · ERROR) · 비상정지 · 조건 안 맞음이면 false",
     "false", "false 면 알람 · 그 동작 실패"),
    ("message", "string", "-", "-", "결과 · 거절 까닭", "\"Rejected: outriggers not set\"", "알람 글"),

    ("section", f"7  {NS}/set_mv_lift  —  vehicle_interfaces/srv/SetMvLift"),
    ("note", "자동 동작 중(RCS 작업 순서 — 구역 높이 · 이동 전 0)에만 보낸다. 수동 화면 리프트는 manual_command 의 "
             "cmd_mv_lift · lift_height. 아웃트리거 고정(hold SET)에서만 움직인다. 도착은 robot_status.lift_h. "
             "리프트 동작 때 RCS 는 협동로봇 safety reset 수신을 확인한다(srv 주석) — 안전정지가 안전재설정으로 풀린 것을 본 "
             "뒤에 로봇을 출발시킨다"),
    ("sub", "요청 — RCS 가 채워 보낸다"),
    ("job_id", "string", "-", "-", "명령마다 새 번호", "\"RCS-L0001\"", "RCS-L0001 부터 하나씩"),
    ("set_height", "float64", "m", "-", "목표 높이 — 절대 높이(맨 아래 0). 리프트 한계 밖이면 거절", "1.200",
     "구역 높이 · 이동 전 0"),
    ("set_speed", "float64", "m/s", "-", "리프트 속도 — auto mode 에서만 적용(수동 모드는 PLC 가 속도를 정한다)", "0.050",
     "RCS 차량 설정 「리프트 속도」(기본 50 mm/s)"),
    ("sub", "응답 — AGV 가 채워 답한다"),
    ("success", "bool", "-", "-", "받았으면 true — 바로 움직이기 시작한다. 거절이면 false", "true", "false 면 알람 · 그 동작 실패"),
    ("message", "string", "-", "-", "결과 · 거절 까닭", "\"Rejected: outriggers not set\"", "알람 글"),

    ("section", f"8  {NS}/measure_lap  —  vehicle_interfaces/srv/MeasureLap"),
    ("sub", "요청 — RCS 가 채워 보낸다"),
    ("job_id", "string", "-", "-", "캘리브레이션마다 새 번호 — robot_status.job_id 로 돌려준다", "\"LAP-0001\" · \"ALN-0001\"",
     "LAP- (원통) · ALN- (곡판)"),
    ("mode", "string", "-", "\"\"",
     "LAP = 원통: 원점 QR 에서 출발해 모재를 한 바퀴 돌고 원점 QR 에 선다. "
     "ALIGN = 곡판: 돌지 않고 판 가운데 QR 에 맞춰 거리 · 방향만 정렬하고 선다(곡판은 그 뒤 스캔 때도 차량이 움직이지 않는다). "
     "빈 글자 = LAP. 모르는 값은 거절", "\"LAP\"", "모재 모양(원통 · 곡판)"),
    ("speed", "float64", "m/s", "-", "주행 속도(ALIGN 은 정렬 움직임의 상한)", "0.100", "RCS 차량 설정 「차량 주행 속도」"),
    ("standoff", "float64", "m", "-", "거리 유지 — 차량 안쪽 면 ↔ 모재 표면(ALIGN 도 이 거리로 맞춰 선다)", "2.000",
     "RCS 차량 설정 「모재 거리 유지」"),
    ("expected_dist", "float64", "m", "-",
     "LAP: 한 바퀴 거리 2π(모재 반지름 + 거리 유지 + 차량 폭/2) — 안전 한도 · 참고(원점은 QR 로 찾는다). ALIGN: 0",
     "15.708", "RCS 계산"),
    ("sub", "응답 — AGV 가 채워 답한다"),
    ("success", "bool", "-", "-", "받아들였으면 true — 바로 출발한다. 거절이면 false", "true", "false 면 캘리브레이션 실패"),
    ("message", "string", "-", "-", "결과 · 거절 까닭", "\"Rejected: unknown mode CIRCLE\"", "알람 글"),

    ("section", f"9  {NS}/reset_odometry  —  vehicle_interfaces/srv/ResetOdometry"),
    ("sub", "요청 — RCS 가 채워 보낸다"),
    ("x", "float64", "m", "-", "차량 위치 x 를 이 값으로", "0.000", "수동 화면 「위치 다시 맞추기」 x"),
    ("y", "float64", "m", "-", "차량 위치 y 를 이 값으로", "0.000", "수동 화면 「위치 다시 맞추기」 y"),
    ("yaw", "float64", "rad", "-", "차량 방향 yaw 를 이 값으로", "0.000", "수동 화면 「위치 다시 맞추기」 yaw(화면은 ° · 보낼 때 rad)"),
    ("sub", "응답 — AGV 가 채워 답한다"),
    ("success", "bool", "-", "-", "바꿨으면 true. 못 바꾸면(예: 움직이는 중) false", "true", "알림 줄"),
    ("message", "string", "-", "-", "결과 · 거절 까닭", "\"Rejected: moving\"", "알림 줄"),
]


# ================================================================= 4. 상태 · 값]


# ================================================================= 4. 상태 · 값
STATE_HEAD = ["state 값", "뜻", "언제 이 값으로 바꾸나"]
STATES = [
    ("STOP", "서 있다 · 아웃트리거 해제", "처음 · 이동이 끝남(해제 상태) · STOP 받음 · 리셋 뒤(해제 상태)"),
    ("HOLD", "서 있다 · 아웃트리거 고정", "고정이 끝남(hold = SET) · 고정 상태에서 STOP 받음 · 리셋 뒤(고정 상태)"),
    ("RUNNING", "주행 중 — set_job 이동 또는 measure_lap", "robot_control RUNNING 을 받아 움직이기 시작할 때 · PAUSED 에서 재개"),
    ("PAUSED", "measure_lap 일시정지(속도 0, 이어서 갈 수 있다)", "measure_lap 중에 robot_control PAUSED 를 받았을 때"),
    ("ERROR", "차량 오류 — error_code ≠ 0", "오류가 나면 바로. robot_control reset = 1 로 풀면 STOP 또는 HOLD"),
    ("note", "리프트 · 아웃트리거가 움직일 때는 state 를 바꾸지 않는다(STOP / HOLD 그대로) — RCS 는 lift_h · hold 로 끝을 본다. "
             "비상정지는 state 가 아니라 estop 필드로 알린다"),
    ("note", "set_job · measure_lap 이 끝까지 가서 서면(STOP) 홀딩(브레이크)을 잡고 robot_control job_finish 를 기다린다 — "
             "state 는 STOP 그대로. 그동안 새 set_job · measure_lap 은 거절한다. STOP 으로 도중에 멈춘 이동은 기다리지 않는다"
             "(RCS 일시정지 · 재개는 남은 거리로 새 set_job 을 보낸다)"),
]

VALUE_HEAD = ["필드", "값", "뜻"]
VALUES = [
    ("section", "robot_status 에 AGV 가 넣는 값"),
    ("hold", "SET · RELEASE", "아웃트리거 고정(접지 끝) · 해제(다 올림)"),
    ("lap_state", "IDLE · RUNNING · DONE · FAILED", "measure_lap 없음 · 진행 중 · 끝남(lap_origin_ok 와 같이) · 실패(lap_error 에 까닭)"),
    ("level_state", "1 · 0 · -1", "수평 맞음 · 안 맞음(로봇 스캔 막음) · 모름"),
    ("battery_pct", "0~100 · 255", "잔량 % · 모름(배터리 없음)"),
    ("error_code", "0 · 101~901", "정상 · 차량 오류 번호(6.오류 번호). 701 = PLC 통신 끊김"),
    ("section", "RCS 가 보내는 값"),
    ("robot_control.state", "RUNNING · STOP · PAUSED · \"\"", "출발 · 재개 / 정지 / measure_lap 일시정지 / 그대로(reset 만)"),
    ("robot_control.reset", "0 · 1", "없음 · 오류 리셋(PLC 끊김이면 다시 연결)"),
    ("robot_control.job_finish", "0 · 1", "없음 · 작업 끝(홀딩 해제 · 아웃트리거 올림 · 이동 가능)"),
    ("manual_command.cmd_outrg_set", "0 · 1 · 2", "그대로 · 해제(전체 올리기) · 고정(전체 내리기)"),
    ("manual_command.cmd_mv_lift", "0 · 1", "그대로 · lift_height 로 이동(수동, 속도는 PLC)"),
    ("리프트 명령", "set_mv_lift · manual_command.cmd_mv_lift", "자동 동작 중(RCS 작업 순서, set_speed) · 수동 제어 화면(PLC 속도)"),
    ("manual_command.cmd_init", "0 · 1", "없음 · 원점 / 초기화"),
    ("manual_command.cmd_mv_* · cmd_trn_*", "false · true", "멈춤 · 그 방향으로(누르는 동안 200 ms 마다)"),
    ("measure_lap.mode", "LAP · ALIGN · \"\"", "원통 한 바퀴 · 곡판 QR 정렬 · LAP"),
]

ACCEPT_HEAD = ["명령  /  지금 상태 →", "STOP", "HOLD", "RUNNING", "PAUSED", "ERROR", "비상정지 중", "거절할 때 message 예"]
ACCEPT = [
    ("set_job", "O", "O", "X", "X", "X", "X", "\"Rejected: busy\" · \"Rejected: error — reset first\" · "
     "\"Rejected: previous job not finished — send job_finish\""),
    ("robot_control  RUNNING", "O\n(set_job 뒤)", "O\n먼저 해제", "O", "O 재개", "X", "X",
     "\"Rejected: no job\" · \"Rejected: estop\""),
    ("robot_control  PAUSED", "X", "X", "O", "-", "X", "-", "\"Rejected: not running\""),
    ("robot_control  STOP", "O", "O", "O", "O", "O", "O", "(언제든 받는다)"),
    ("robot_control  job_finish = 1", "O", "O\n아웃트리거 올림", "X", "X", "X", "X",
     "\"Rejected: moving\" · \"Rejected: error — reset first\" · \"Rejected: estop\""),
    ("robot_control  reset = 1", "O", "O", "O", "O", "O 풀림", "O",
     "원인이 남으면 false + 까닭. estop 은 버튼을 풀어야 풀린다. 701 은 다시 연결을 시작하고 true — 붙으면 풀린다"),
    ("manual_command  조그", "O", "X", "X", "X", "X", "X", "\"Rejected: outriggers set\" · \"Rejected: Not in manual mode\""),
    ("manual_command  아웃트리거", "O", "O", "X", "X", "X", "X", "\"Rejected: Not in manual mode\""),
    ("manual_command  리프트", "X", "O", "X", "X", "X", "X",
     "\"Rejected: outriggers not set\" · \"Rejected: height … out of …\""),
    ("set_mv_lift", "X", "O", "X", "X", "X", "X",
     "\"Rejected: outriggers not set\" · \"Rejected: height … out of …\""),
    ("measure_lap", "O", "O\n먼저 해제", "X", "X", "X", "X", "\"Rejected: running\" · \"Rejected: unknown mode …\" · "
     "\"Rejected: previous job not finished — send job_finish\""),
    ("reset_odometry", "O", "O", "X", "X", "O", "O", "\"Rejected: moving\""),
    ("note", "O 받는다 · X 거절(success = false) · - 해당 없음. 「먼저 해제」 = 아웃트리거를 스스로 풀고 출발한다"
             "(RCS 는 보통 먼저 job_finish 를 보내 홀딩을 풀고 아웃트리거를 올린다). 거절은 그 명령에만 답하는 것이고 "
             "error_code 를 쓰지 않는다. set_job · measure_lap 은 끝난 작업에 job_finish 를 받기 전이면 거절한다"),
    ("note", "PLC 통신이 끊긴 동안은 error_code 701 · state ERROR 다 — 위 ERROR 열대로 움직이는 명령을 거절한다"
             "(\"Rejected: PLC not connected\" 처럼 까닭을 적는다). robot_control reset = 1 을 받으면 PLC 에 다시 붙어 본다"),
]


# ================================================================= 5. 흐름 예
FLOW_HEAD = ["순서", "RCS 가 보냄 (클라이언트 · 발행)", "", "AGV 가 보냄 (서버 응답 · 발행)", "설명"]
R, L = "━━▶", "◀━━"
FLOWS = [
    ("section", "A. 구간 이동 한 번 — 원통 스캔에서 구역과 구역 사이 (곡판은 1~5 없이 6 부터)"),
    ("1", "set_mv_lift\n{job_id: RCS-L0006, set_height: 0.0, set_speed: 0.05}", R, "", "리프트를 내린다(올라가 있을 때만)"),
    ("", "", L, "success: true", "바로 답한다"),
    ("", "", L, "robot_status  lift_h → 0.000", "|lift_h − 0| ≤ 2 mm 면 다음으로 (RCS 제한 180 초)"),
    ("2", "robot_control\n{job_finish: 1}", R, "", "작업 끝 — 앞 자리 일이 끝났다(2026-11-20)"),
    ("", "", L, "success: true  →  홀딩 해제 · 아웃트리거 올림  →  robot_status  hold: RELEASE · state: STOP · job_id: \"\"",
     "hold RELEASE 면 다음으로 (RCS 제한 30 초). 끝낼 작업이 없어도 success"),
    ("3", "set_job\n{job_id: RCS-0007, set_dist: 0.35, set_speed: 0.1, standoff: 2.0, radius: 2.5}", R, "",
     "이번에 갈 거리 · 모재 반지름"),
    ("", "", L, "success: true", "아직 출발하지 않는다 · mv_dist = 0"),
    ("4", "robot_control\n{state: RUNNING}", R, "", "출발"),
    ("", "", L, "success: true  →  robot_status  state: RUNNING · job_id: RCS-0007 · mv_dist 0 → 늘어남",
     "RUNNING 이 적어도 한 번은 실려야 한다"),
    ("5", "", L, "robot_status  state: STOP · mv_dist: 0.350",
     "|mv_dist − set_dist| ≤ 2 mm → 도착 (RCS 제한 120 초 + 달릴 시간 × 1.5). AGV 는 홀딩(브레이크)을 잡고 "
     "job_finish 를 기다린다"),
    ("6", "manual_command\n{cmd_outrg_set: 2}", R, "", "아웃트리거 고정"),
    ("", "", L, "success: true  →  robot_status  hold: SET · state: HOLD · level_state: 1", ""),
    ("7", "set_mv_lift\n{job_id: RCS-L0007, set_height: 1.2, set_speed: 0.05}", R, "", "구역 높이로"),
    ("", "", L, "success: true  →  robot_status  lift_h → 1.200",
     "리프트가 움직이는 동안 PLC 가 로봇을 안전정지 → 끝나면 안전재설정. RCS 는 안전재설정을 받아 풀린 것을 확인할 때까지 "
     "로봇 출발을 미룬다"),
    ("8", "", L, "robot_status (10 Hz 계속)",
     "로봇이 스캔하는 동안 그대로 서 있는다. hold SET · error_code 0 · estop false · level_state ≠ 0 이 깨지면 RCS 가 로봇을 세운다"),
    ("9", "(다음 구역 — 로봇이 홈에 온 뒤 다음 A 의 1 · 2)", R, "",
     "리프트 0 → job_finish. 같은 세로 줄의 다음 구역(리프트만 옮김)이면 job_finish 없이 7 부터"),

    ("section", "B. 캘리브레이션 — 원통 (mode LAP)"),
    ("0", "", L, "start_request  {}", "차량 「작업 시작」 버튼으로 시작할 때만"),
    ("1", "(A 의 1 · 2 — 리프트 0 · job_finish)", R, "", ""),
    ("2", "measure_lap\n{job_id: LAP-0001, mode: LAP, speed: 0.1, standoff: 2.0, expected_dist: 15.708}", R, "", ""),
    ("", "", L, "success: true  →  바로 출발", ""),
    ("3", "", L, "robot_status  lap_state: RUNNING · state: RUNNING · lap_path_m 늘어남", "모재를 따라 한 바퀴"),
    ("4", "", L, "robot_status  lap_state: DONE · lap_origin_ok: true · lap_path_m: 15.692 · state: STOP",
     "원점 QR 에 다시 섰다 — RCS 가 lap_path_m 으로 둘레를 잡는다 (제한 = RCS 「캘리브레이션 최대 시간」). "
     "홀딩을 잡고 job_finish 를 기다린다 — 다음 주행(A 의 2) 때 온다"),
    ("실패", "", L, "robot_status  lap_state: FAILED · lap_error: \"QR not found\"", "RCS 는 캘리브레이션 실패로 끝낸다"),

    ("section", "C. 캘리브레이션 — 곡판 (mode ALIGN) · QR 은 판 가운데, 차량은 그 앞에 놓여 있다"),
    ("1", "(A 의 1 · 2 — 리프트 0 · job_finish)", R, "", ""),
    ("2", "measure_lap\n{job_id: ALN-0001, mode: ALIGN, speed: 0.1, standoff: 2.0, expected_dist: 0.0}", R, "", ""),
    ("", "", L, "success: true", ""),
    ("3", "", L, "robot_status  lap_state: RUNNING · state: RUNNING", "QR 에 맞춰 거리 · 방향만 정렬한다(한 바퀴 돌지 않는다)"),
    ("4", "", L, "robot_status  lap_state: DONE · lap_origin_ok: true · state: STOP",
     "그 자리가 판 가운데. 곡판은 그 뒤 set_job 을 보내지 않는다 (제한 = 「곡판 QR 정렬 최대 시간」 기본 120 초). "
     "홀딩을 잡은 채 스캔하고, job_finish 는 다음 주행(다음 캘리브레이션 등) 전에 온다"),

    ("section", "D. 일시정지 · 재개 · 정지"),
    ("measure_lap 중", "robot_control  {state: PAUSED}", R, "success: true  →  state: PAUSED · speed 0", "일시정지"),
    ("", "robot_control  {state: RUNNING}", R, "success: true  →  state: RUNNING", "이어서 간다"),
    ("set_job 이동 중", "robot_control  {state: STOP}", R, "success: true  →  state: STOP", "RCS 의 일시정지"),
    ("", "set_job {남은 거리} → robot_control {state: RUNNING}", R, "", "RCS 의 재개 — 남은 거리로 새 이동"),
    ("정지", "robot_control  {state: STOP}", R, "success: true  →  멈춤 · measure_lap 이면 lap_state: FAILED", ""),

    ("section", "E. 비상정지 — 실제 동시 정지는 안전 회로 직결이 맡는다. 아래는 알림"),
    ("로봇 쪽 버튼", "robot_estop  {data: true}  (0.5 초마다 계속)", R,
     "차량 비상정지 → robot_status  estop: true", "움직이는 명령은 거절"),
    ("풀림", "robot_estop  {data: false}", R, "차량 리셋 규칙대로 풀고 estop: false", ""),
    ("차량 쪽 버튼", "", L, "robot_status  estop: true", "RCS 가 로봇 · 작업을 세우고 ERUT 에 알린다"),

    ("section", "F. 차량 오류 · 리셋"),
    ("발생", "", L, "robot_status  error_code: 304 · error_msg: \"304 OUTRIGGER_LOST — …\" · state: ERROR",
     "하던 동작을 멈추고 새 동작 명령은 거절. measure_lap 중이면 lap_state: FAILED 도"),
    ("리셋", "robot_control  {reset: 1}", R, "", "RCS 알람 리셋 · ERUT reset · 수동 「오류 리셋」"),
    ("", "", L, "원인 없음: success: true → error_code: 0 · state: STOP/HOLD\n원인 남음: success: false · message: 까닭", ""),

    ("section", "G. 수동 제어 화면 — 조그 · 아웃트리거 · 리프트 · 위치"),
    ("누름", "manual_command  {cmd_mv_fwd: true}  (200 ms 마다)", R, "success: true  →  전진", ""),
    ("뗌", "manual_command  {}  (모두 0 / false)", R, "success: true  →  선다", ""),
    ("끊김", "(0.5 초 넘게 안 옴)", "", "스스로 선다", "통신이 끊겨도 계속 달리지 않게"),
    ("아웃트리거", "manual_command  {cmd_outrg_set: 2}  /  {cmd_outrg_set: 1}", R,
     "success: true  →  세 개를 함께 내림 · 올림  →  hold: SET / RELEASE", "「전체 내리기 (고정)」 · 「전체 올리기 (해제)」 버튼"),
    ("리프트", "manual_command\n{cmd_mv_lift: 1, lift_height: 0.8}", R,
     "success: true  →  robot_status  lift_h → 0.800", "「이 높이로 이동」 버튼 — 속도는 PLC. 멈추려면 robot_control {state: STOP}"),
    ("위치", "reset_odometry\n{x: 0.0, y: 0.0, yaw: 0.0}", R,
     "success: true  →  robot_status  position x · y · yaw = 그 값", "「위치 다시 맞추기」 — 작업자가 넣은 값(확인 뒤)"),

    ("section", "H. PLC 연결 — 끊김 · 다시 연결 (PLC 전용 필드 · 서비스 없이 error_code 701 · reset 으로)"),
    ("끊김", "", L, "robot_status  error_code: 701 · error_msg: \"701 PLC_COMM_LOST — timeout …\" · state: ERROR",
     "하던 동작을 멈춘다. RCS: 상단 PLC 「연결 실패」 · 알람 [E2001] · 작업 중이면 작업을 세운다"),
    ("자동 재시도", "", L, "(robot_status 그대로 701)", "AGV 노드가 스스로 다시 붙어 봐도 된다(권장 5 초마다) — 하지 않아도 된다"),
    ("다시 연결", "robot_control  {reset: 1}", R, "success: true · message: \"PLC reconnecting\"",
     "RCS 연결 설정 「PLC 다시 연결」(701 일 때만 누를 수 있다). 시작했다만 답한다"),
    ("붙음", "", L, "robot_status  error_code: 0 · state: STOP/HOLD",
     "701 은 AGV 가 스스로 푼다. RCS: 「연결됨」 · 「PLC 가 다시 연결됐습니다」. 하던 작업은 실패로 끝났다 — 다시 시작은 사람이"),
    ("못 붙음", "", L, "robot_status  error_code: 701 그대로", "RCS 는 「연결 실패」 그대로 — 다시 누를 수 있다"),
]


# ================================================================= 7. 확인 명령]


# ================================================================= 7. 확인 명령
CMD_HEAD = ["번호", "무엇을", "명령 (그대로 복사)", "이렇게 나오면 맞다"]
COMMANDS = [
    ("section", "준비 — 두 PC 모두"),
    ("", "환경", "source /opt/ros/humble/setup.bash\nsource ~/dusan_ws/install/setup.bash\nexport ROS_DOMAIN_ID=0",
     "AGV PC 는 자기 작업공간(vehicle_interfaces 를 빌드한 곳)"),
    ("", "메시지 패키지", "ros2 interface show vehicle_interfaces/msg/RobotStatus",
     "3.필드 · 8.원문 과 같은 필드가 나온다"),
    ("section", "AGV 노드가 떠 있나"),
    ("", "노드", "ros2 node list", "AGV 노드 이름이 보인다(RCS 가 켜져 있으면 /smr_operator_ui 도)"),
    ("", "토픽", "ros2 topic list -t | grep vehicle",
     "/vehicle/robot_status [vehicle_interfaces/msg/RobotStatus]\n/vehicle/start_request [std_msgs/msg/Empty]\n"
     "/vehicle/robot_estop [std_msgs/msg/Bool]"),
    ("", "서비스", "ros2 service list -t | grep vehicle",
     "/vehicle/set_job · /vehicle/robot_control · /vehicle/manual_command · /vehicle/set_mv_lift · "
     "/vehicle/measure_lap · /vehicle/reset_odometry (타입과 함께)"),
    ("section", "토픽"),
    ("1", "상태 주기", "ros2 topic hz /vehicle/robot_status", "average rate: 10.0 쯤"),
    ("1", "상태 내용", "ros2 topic echo /vehicle/robot_status", "state · hold · lift_h · estop … 가 계속 나온다"),
    ("2", "작업 시작 버튼", "ros2 topic echo /vehicle/start_request", "차량 버튼을 누르면 --- 가 한 번 나온다"),
    ("3", "로봇 비상정지", "ros2 topic pub -r 2 /vehicle/robot_estop std_msgs/msg/Bool \"{data: true}\"",
     "차량이 비상정지 · robot_status.estop: true. 멈추고 {data: false} 를 내면 풀린다"),
    ("section", "서비스 — RCS 없이 AGV 노드만 시험"),
    ("4", "이동 설정",
     "ros2 service call /vehicle/set_job vehicle_interfaces/srv/SetJob "
     "\"{job_id: 'T-1', set_dist: 0.2, set_speed: 0.1, standoff: 2.0, radius: 2.5}\"",
     "success=True. 차량은 아직 서 있다"),
    ("5", "출발",
     "ros2 service call /vehicle/robot_control vehicle_interfaces/srv/RobotControl \"{state: 'RUNNING'}\"",
     "robot_status: state RUNNING · job_id T-1 · mv_dist 0 → 0.2 → state STOP"),
    ("5", "정지", "ros2 service call /vehicle/robot_control vehicle_interfaces/srv/RobotControl \"{state: 'STOP'}\"",
     "멈춘다"),
    ("5", "오류 리셋", "ros2 service call /vehicle/robot_control vehicle_interfaces/srv/RobotControl \"{reset: 1}\"",
     "error_code 0 · state STOP/HOLD"),
    ("5", "작업 끝", "ros2 service call /vehicle/robot_control vehicle_interfaces/srv/RobotControl \"{job_finish: 1}\"",
     "홀딩 해제 · 아웃트리거 올림 → hold RELEASE · state STOP · job_id 빈 글자. 이 뒤에 다음 set_job 을 받는다"),
    ("6", "아웃트리거 고정",
     "ros2 service call /vehicle/manual_command vehicle_interfaces/srv/ManualCommand \"{cmd_outrg_set: 2}\"",
     "hold SET · state HOLD"),
    ("6", "리프트(수동)",
     "ros2 service call /vehicle/manual_command vehicle_interfaces/srv/ManualCommand "
     "\"{cmd_mv_lift: 1, lift_height: 0.3}\"",
     "lift_h → 0.3 (아웃트리거 고정 전이면 success=False)"),
    ("7", "리프트(자동)",
     "ros2 service call /vehicle/set_mv_lift vehicle_interfaces/srv/SetMvLift "
     "\"{job_id: 'T-L1', set_height: 0.5, set_speed: 0.05}\"",
     "lift_h → 0.5 (아웃트리거 고정 전이면 success=False)"),
    ("8", "캘리브레이션(원통)",
     "ros2 service call /vehicle/measure_lap vehicle_interfaces/srv/MeasureLap "
     "\"{job_id: 'L-1', mode: 'LAP', speed: 0.1, standoff: 2.0, expected_dist: 15.7}\"",
     "lap_state RUNNING → DONE · lap_origin_ok true · lap_path_m"),
    ("8", "캘리브레이션(곡판)",
     "ros2 service call /vehicle/measure_lap vehicle_interfaces/srv/MeasureLap "
     "\"{job_id: 'A-1', mode: 'ALIGN', speed: 0.1, standoff: 2.0, expected_dist: 0.0}\"",
     "lap_state RUNNING → DONE · lap_origin_ok true"),
    ("9", "위치 다시 맞추기",
     "ros2 service call /vehicle/reset_odometry vehicle_interfaces/srv/ResetOdometry \"{x: 0.0, y: 0.0, yaw: 0.0}\"",
     "success=True · robot_status.position x · y · yaw = 0"),
    ("5", "PLC 다시 연결",
     "ros2 service call /vehicle/robot_control vehicle_interfaces/srv/RobotControl \"{reset: 1}\"",
     "(701 일 때) success=True → 붙으면 error_code 0"),
    ("section", "참고 구현 — 3S 모의기로 같은 명령을 해 보고 비교"),
    ("", "모의기(화면)", "ros2 launch vehicle_sim vehicle_sim.launch.py", "AGV 노드 대신 같은 9개를 연다"),
    ("", "모의기(화면 없음)", "ros2 launch vehicle_sim vehicle_sim.launch.py ui:=false", ""),
    ("", "모의기 오류 넣기", "ros2 param set /vehicle_sim fault_code 304", "error_code 304 · state ERROR → reset 으로 푼다"),
    ("", "모의기 PLC 끊기", "ros2 param set /vehicle_sim plc_fail true",
     "error_code 701 · state ERROR. false 로 돌리고 robot_control {reset: 1} → 701 풀림"),
]


# ================================================================= 쓰기
def _units(text: str) -> float:
    """글자 너비(엑셀 칸 너비 단위). 한글 · 기호는 넓다."""
    return sum(1.75 if ord(ch) > 0x2E80 else (1.3 if ord(ch) > 0x2000 else 0.95) for ch in text)


def _setup(ws, title, note, widths, first_col=1):
    for col, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.cell(row=1, column=first_col, value=title).font = TITLE_FONT
    c = ws.cell(row=2, column=first_col, value=note)
    c.font, c.alignment = NOTE_FONT, LEFT
    ws.merge_cells(start_row=2, start_column=first_col, end_row=2, end_column=len(widths))
    ws.sheet_view.zoomScale = 90
    ws.sheet_view.showGridLines = False
    # 인쇄 · PDF — 가로, 폭을 한 장에 맞춘다
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0


def _header(ws, r, headers, first_col=1, groups=None):
    groups = groups or [(first_col + i, first_col + i) for i in range(len(headers))]
    for head, (c1, c2) in zip(headers, groups):
        for col in range(c1, c2 + 1):
            c = ws.cell(row=r, column=col)
            c.fill, c.border = HEAD_FILL, BORDER
        c = ws.cell(row=r, column=c1, value=head)
        c.font, c.alignment = HEAD_FONT, CENTER
        if c2 > c1:
            ws.merge_cells(start_row=r, start_column=c1, end_row=r, end_column=c2)
    ws.row_dimensions[r].height = 22
    return r + 1


def _band(ws, r, text, c1, c2, kind):
    """구역 제목(section) · 작은 제목(sub) · 메모(note) — 가로로 합친 한 줄."""
    for col in range(c1, c2 + 1):
        c = ws.cell(row=r, column=col)
        c.border = BORDER
        if kind == "section":
            c.fill = SECT_FILL
        elif kind == "sub":
            c.fill = SUB_FILL
    c = ws.cell(row=r, column=c1, value=text)
    c.font = {"section": SECT_FONT, "sub": BOLD_BODY, "note": NOTE_FONT}[kind]
    c.alignment = LEFT
    ws.merge_cells(start_row=r, start_column=c1, end_row=r, end_column=c2)


def _table(ws, r, data, ncols, first_col=1, groups=None, code=(), center=(), bold=(), fills=None):
    """data 를 쓴다. 튜플의 i 번째 값 → groups[i] 칸(합침). code · center · bold 는 값 번호(0 부터)."""
    groups = groups or [(first_col + i, first_col + i) for i in range(ncols)]
    last = groups[-1][1]
    for item in data:
        if item and item[0] in ("section", "sub", "note"):
            _band(ws, r, item[1], first_col, last, item[0])
            r += 1
            continue
        fill = fills(item) if fills else None
        for i, (value, (c1, c2)) in enumerate(zip(item, groups)):
            for col in range(c1, c2 + 1):
                c = ws.cell(row=r, column=col)
                c.border = BORDER
                if fill:
                    c.fill = fill
            c = ws.cell(row=r, column=c1, value=value if value != "" else None)
            c.font = CODE_FONT if i in code else (BOLD_BODY if i in bold else BODY_FONT)
            c.alignment = CENTER if i in center else LEFT
            if c2 > c1:
                ws.merge_cells(start_row=r, start_column=c1, end_row=r, end_column=c2)
        r += 1
    return r


def _fit(ws, widths, start=3, skip=()):
    """줄바꿈된 글이 잘리지 않게 줄 높이를 글 길이로 맞춘다(가로로 합친 칸은 합친 너비로)."""
    spans = {}
    for rng in ws.merged_cells.ranges:
        if rng.min_row == rng.max_row:
            spans[(rng.min_row, rng.min_col)] = sum(widths[rng.min_col - 1:rng.max_col])
    for r in range(start, ws.max_row + 1):
        if r in skip:
            continue
        lines = 1
        for col in range(1, len(widths) + 1):
            text = ws.cell(row=r, column=col).value
            if not isinstance(text, str) or not text:
                continue
            room = max(spans.get((r, col), widths[col - 1]) - 1.5, 3)
            lines = max(lines, sum(max(1, -(-int(_units(part)) // int(room))) for part in text.split("\n")))
        ws.row_dimensions[r].height = max(20, 13 * lines + 7)


def _sheet_overview(wb):
    widths = [2, 24, 8, 30, 50, 8, 26]
    ws = wb.create_sheet("1.한눈에")
    _setup(ws, f"AGV 노드 ↔ RCS 노드 — ROS 2 토픽 · 서비스 한눈에 ({DATE})",
           "AGV 노드 하나가 토픽 2개를 발행하고, 1개를 구독하고, 서비스 6개의 서버가 된다. RCS 노드는 그 반대편이다. "
           "이름 앞 /vehicle 은 네임스페이스. 화살표: ▶ RCS → AGV · ◀ AGV → RCS", widths, first_col=2)
    r = 4
    for col, text in ((2, "3S RCS PC"), (7, "AGV PC")):
        c = ws.cell(row=r, column=col, value=text)
        c.font, c.alignment = NOTE_FONT, CENTER
    top = r + 1
    r = top
    for item in DIAGRAM:
        if item[0] == "sub":
            c = ws.cell(row=r, column=3, value=item[1])
            c.font, c.alignment = BOLD_BODY, CENTER
            ws.merge_cells(start_row=r, start_column=3, end_row=r, end_column=6)
            ws.row_dimensions[r].height = 24
            r += 1
            continue
        kind, left, name, what, right = item
        fill = TOPIC_FILL if kind == "topic" else SERVICE_FILL
        for col, value, font, align in ((3, left, ARROW_FONT, NO_WRAP), (4, name, CODE_BOLD, LEFT),
                                        (5, what, BODY_FONT, LEFT), (6, right, ARROW_FONT, NO_WRAP)):
            c = ws.cell(row=r, column=col, value=value)
            c.font, c.alignment, c.fill = font, align, fill
        ws.row_dimensions[r].height = 30
        r += 1
    bottom = r - 1
    for col, text in ((2, "RCS 노드\n\n/smr_operator_ui\n\n구독 2 · 발행 1\n서비스 클라이언트 6"),
                      (7, "AGV 노드\n(AGV 담당 · 이름 자유)\n\n발행 2 · 구독 1\n서비스 서버 6\n\nPLC 는 이 노드만 만진다")):
        ws.merge_cells(start_row=top, start_column=col, end_row=bottom, end_column=col)
        c = ws.cell(row=top, column=col, value=text)
        c.font, c.fill, c.alignment = BOX_FONT, BOX_FILL, CENTER
        for rr in range(top, bottom + 1):
            ws.cell(row=rr, column=col).fill = BOX_FILL
    r += 1
    c = ws.cell(row=r, column=3, value="초록 = 토픽 · 파랑 = 서비스. 필드 · 넣는 값은 3.필드, 순서는 5.흐름 예")
    c.font = NOTE_FONT
    ws.merge_cells(start_row=r, start_column=3, end_row=r, end_column=6)
    r += 2
    _band(ws, r, "준비", 2, 7, "section")
    r += 1
    first_setup = r
    r = _table(ws, r, SETUP, 2, first_col=2, groups=[(2, 2), (3, 7)], center=(0,), bold=(0,))
    _fit(ws, widths, start=first_setup)
    ws.page_setup.fitToHeight = 1          # 한 장에


def _sheet_list(wb):
    widths = [6, 10, 8, 28, 37, 30, 40, 58]
    ws = wb.create_sheet("2.목록")
    _setup(ws, f"AGV 노드가 열 토픽 · 서비스 9개 ({DATE})",
           "이름 · 타입은 그대로 복사해 쓴다(글자까지 같아야 통신된다). 필드는 3.필드, 받는 조건은 4.상태·값.", widths)
    r = _header(ws, 4, LIST_HEAD)

    def fills(item):
        return TOPIC_FILL if item[2] == "토픽" else SERVICE_FILL
    r = _table(ws, r, LIST, len(widths), code=(3, 4), center=(0, 1, 2), bold=(1,), fills=fills)
    _band(ws, r + 1, "모든 서비스: 받자마자(수 ms 안) 응답한다 — 「받았다 / 거절(까닭)」만. 다 끝났는지는 robot_status 로 "
                     "알린다. 거절은 success = false · message 로만 답하고 error_code 를 쓰지 않는다", 1, len(widths), "note")
    _fit(ws, widths, start=5)


def _sheet_fields(wb):
    widths = [18, 20, 8, 9, 62, 34, 44]
    ws = wb.create_sheet("3.필드")
    _setup(ws, f"메시지 · 서비스 필드 — 한 줄에 필드 하나 ({DATE})",
           "타입은 vehicle_interfaces 그대로(8.원문). 「기본값」은 .msg 에 적힌 값 — 서비스 요청 필드의 「-」는 RCS 가 늘 채운다는 뜻. "
           "번호는 2.목록 의 번호.", widths)
    r = _header(ws, 4, FIELD_HEAD)
    _table(ws, r, FIELDS, len(widths), code=(0, 1, 3, 5), center=(2, 3))
    _fit(ws, widths, start=5)


def _sheet_values(wb):
    widths = [30, 13, 13, 13, 13, 13, 13, 52]
    n = len(widths)
    ws = wb.create_sheet("4.상태·값")
    _setup(ws, f"정해진 값 · 상태별로 명령을 받는지 ({DATE})",
           "글자 값은 대문자 그대로. 아래 표의 동작은 3S 모의기(vehicle_sim)와 같다 — 실제 차량과 다르면 알려 준다.", widths)
    r = 4
    _band(ws, r, "1) robot_status.state — AGV 가 내는 값", 1, n, "section")
    r = _header(ws, r + 1, STATE_HEAD, groups=[(1, 1), (2, 4), (5, 8)])
    r = _table(ws, r, STATES, 3, groups=[(1, 1), (2, 4), (5, 8)], code=(0,), center=(0,))
    r += 1
    _band(ws, r, "2) 그 밖의 정해진 값", 1, n, "section")
    r = _header(ws, r + 1, VALUE_HEAD, groups=[(1, 1), (2, 4), (5, 8)])
    r = _table(ws, r, VALUES, 3, groups=[(1, 1), (2, 4), (5, 8)], code=(0, 1))
    r += 1
    _band(ws, r, "3) 명령을 받나 — 지금 상태(robot_status.state · estop)별", 1, n, "section")
    r = _header(ws, r + 1, ACCEPT_HEAD)
    first = r
    r = _table(ws, r, ACCEPT, n, code=(0, 7), center=(1, 2, 3, 4, 5, 6))
    for rr in range(first, r):
        for col in range(2, 8):
            c = ws.cell(row=rr, column=col)
            if isinstance(c.value, str) and c.value[:1] in "OX":
                c.font = OK_FONT if c.value.startswith("O") else NO_FONT
    _fit(ws, widths, start=5)


def _sheet_flows(wb):
    widths = [14, 52, 7, 56, 52]
    ws = wb.create_sheet("5.흐름 예")
    _setup(ws, f"주고받는 순서 — 위에서 아래로 ({DATE})",
           "▶ RCS → AGV (서비스 요청 · robot_estop) · ◀ AGV → RCS (서비스 응답 · robot_status · start_request). "
           "{ } 안은 ros2 명령에 쓰는 모양. 적지 않은 필드는 0 / false / 빈 글자.", widths)
    r = _header(ws, 4, FLOW_HEAD)
    first = r
    r = _table(ws, r, FLOWS, len(widths), code=(1, 3), center=(0, 2), bold=(0,))
    for rr in range(first, r):
        c = ws.cell(row=rr, column=3)
        if c.value:
            c.font = ARROW_FONT
    _fit(ws, widths, start=5)


def _sheet_errors(wb):
    widths = [11, 28, 46, 11, 13, 22, 40]
    n = len(widths)
    ws = wb.create_sheet("6.오류 번호")
    _setup(ws, f"차량 오류 번호 — robot_status.error_code ({DATE})",
           "번호표는 3S 제안이다 — 차량 담당이 PLC · 모터 드라이버 알람에 맞춰 번호 · 이름 · 뜻 · 리셋으로 풀리는지를 "
           "더하거나 고쳐 알려 준다.", widths)
    r = 4
    _band(ws, r, "보내는 규칙", 1, n, "section")
    r = _table(ws, r + 1, ERROR_RULES, 2, groups=[(1, 1), (2, n)], center=(0,), bold=(0,))
    r = _header(ws, r + 1, ERROR_HEAD)
    _table(ws, r, ERRORS, n, code=(0, 1), center=(0, 3, 4))
    _fit(ws, widths, start=5)


def _sheet_commands(wb):
    widths = [6, 18, 84, 52]
    ws = wb.create_sheet("7.확인 명령")
    _setup(ws, f"ros2 명령으로 확인하기 ({DATE})",
           "AGV 노드를 띄우고 RCS 없이 하나씩 해 본다. 번호는 2.목록 의 번호. 같은 명령을 3S 모의기에 해 보면 맞는 응답을 볼 수 있다.",
           widths)
    r = _header(ws, 4, CMD_HEAD)
    _table(ws, r, COMMANDS, len(widths), code=(2,), center=(0,), bold=(1,))
    _fit(ws, widths, start=5)


def _sheet_source(wb):
    widths = [3, 110]
    ws = wb.create_sheet("8.원문")
    _setup(ws, f"vehicle_interfaces 원문 — .msg · .srv 파일 그대로 ({DATE})",
           "dusan_ws/src/vehicle_interfaces(AGV 담당 판)에서 읽어 넣었다. # 뒤는 주석. --- 위가 요청, 아래가 응답.", widths)
    r = 4
    files = sorted((IFACE / "msg").glob("*.msg")) + sorted((IFACE / "srv").glob("*.srv"))
    order = ["RobotStatus.msg", "Position6D.msg", "SetJob.srv", "RobotControl.srv", "ManualCommand.srv",
             "SetMvLift.srv", "MeasureLap.srv", "ResetOdometry.srv"]
    files.sort(key=lambda p: order.index(p.name) if p.name in order else len(order))
    for path in files:
        kind = "msg" if path.suffix == ".msg" else "srv"
        _band(ws, r, f"vehicle_interfaces/{kind}/{path.stem}   ({path.relative_to(ROOT)})", 1, 2, "section")
        r += 1
        for line in path.read_text(encoding="utf-8").splitlines():
            c = ws.cell(row=r, column=2, value=line if line else None)
            c.font = CODE_FONT if not line.lstrip().startswith("#") else Font(name="Consolas", size=9, color="FF6A8759")
            c.alignment = Alignment(vertical="center", horizontal="left")
            r += 1
        r += 1


def main() -> None:
    wb = Workbook()
    wb.remove(wb.active)
    _sheet_overview(wb)
    _sheet_list(wb)
    _sheet_fields(wb)
    _sheet_values(wb)
    _sheet_flows(wb)
    _sheet_errors(wb)
    _sheet_commands(wb)
    _sheet_source(wb)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT)
    print(f"만들었습니다: {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
