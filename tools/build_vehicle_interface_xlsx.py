"""RCS <-> 차량 제어 노드(AGV 주행 · 리프트 · 아웃트리거) 연계 인터페이스 표를 만든다.

차량 제어 노드와 그 아래 PLC 코드는 3S 차량 담당이 만든다(2026-11-12). 이 표는 RCS 와 그 노드
사이에서 무엇을 주고받는지를, **왜 필요한지(요구 출처)** 와 함께 정리한다.

  요구 출처   ERUT 요청 — ERUT MQTT 규격(if-0.12)을 지키려고 차량 값 · 동작이 필요한 것
              RCS 필요  — RCS 가 작업 순서 · 인터락 · 화면 · 수동 조작을 돌리려고 필요한 것
              공통      — 둘 다
  범위        ERUT <-> RCS(MQTT) 노드는 뺀다(ERUT 규격서). RCS <-> 로봇(Modbus)은 인터락에 쓰는 것만.
  PLC         RCS 는 PLC 에 관여하지 않는다(통신 · 연결 상태 모두 차량 노드가 맡는다, 2026-11-17).
              RCS 에 필요한 PLC 신호는 차량 노드가 차량 상태(RobotStatus)로 가공해 준다.

  0. 읽는 법           목적 · 역할 · 범례 · 단위
  1. 노드·통신         RCS <-> 차량 노드 토픽 · 서비스
  2. 메시지·서비스     필드별 타입 · 단위 · 값 · 요구 출처 · RCS 가 쓰는 곳
  3. 원주 거리 환산     모재 표면 이동 → 차량 실제 이동 (거리 유지 입력값 · 계산기)
  4. 명령 순서         주행 · 아웃트리거 · 리프트 · 일시정지 · 조그 · ERUT 구역 · 캘리브레이션
  5. 인터락·안전       로봇 홈(276) · 차량 고정 확인(309) · 오류 · 끊김 · 비상정지 · 수평
  6. ERUT 보고 값      차량 값이 ERUT 의 어느 칸으로 나가나 (ERUT 요청의 근거)
  7. PLC              RCS 는 관여하지 않는다 — 차량 노드가 맡는다
  8. 정할 것           RCS · 차량 담당이 함께 정할 것

출처: src/vehicle_interfaces (msg · srv), src/vehicle_sim/vehicle_sim/vehicle_sim_node.py,
      operator-ui/src/smr_operator_ui/services/{vehicle_client,vehicle_adapters,erut_session}.py,
      operator-ui/src/smr_operator_ui/app.py, docs/vehicle_request_calibration_lap_battery.md,
      ERUT MQTT 표준 인터페이스 if-0.12

값을 고칠 때는 코드를 먼저 고치고 이 스크립트를 다시 돌린다.
실행:  python3 tools/build_vehicle_interface_xlsx.py
"""

import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import PatternFill

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_modbus_interface_xlsx import (  # noqa: E402
    BOLD_BODY, BODY_FONT, BORDER, CENTER, LEFT, NOTE_FONT, rows, sheet,
)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "3S-AGV_PLC_연계_인터페이스_20261106.xlsx"

#: 요구 출처
ERUT = "ERUT 요청"
RCS = "RCS 필요"
BOTH = "공통"
#: 요구 출처 칸 색
SOURCE_FILLS = {
    ERUT: PatternFill("solid", fgColor="FFDDEBF7"),     # 파랑
    RCS: PatternFill("solid", fgColor="FFE2EFDA"),      # 초록
    BOTH: PatternFill("solid", fgColor="FFEDE2F6"),     # 보라
}
#: 상태
DEFINED = "정의됨"                 # vehicle_interfaces 에 있다
CHECK = "확인 필요"               # 있지만 뜻 · 값을 RCS 가 가정했다
NEW = "정의됨 · AGV 구현"          # 정의(vehicle_interfaces)와 RCS 는 끝났다 — AGV 가 채운다(2026-11-17)
STATE_FILLS = {
    CHECK: PatternFill("solid", fgColor="FFFFF2CC"),    # 노랑
    NEW: PatternFill("solid", fgColor="FFFCE4D6"),      # 주황
}


# ================================================================= 0. 읽는 법
README = [
    ("section", "이 문서"),
    ("목적", "RCS(3S 운전 프로그램)와 차량 제어 노드(3S 차량 담당이 만드는 ROS 2 노드 — AGV 주행 · 리프트 · "
     "아웃트리거 · 센서 · PLC)가 무엇을 어떤 단위로 주고받는지 정한다. 항목마다 **왜 필요한지(요구 출처)** 를 "
     "적어 ERUT 규격 때문에 필요한 것과 RCS 운전에 필요한 것을 가른다."),
    ("범위", "RCS ↔ 차량 제어 노드. ERUT ↔ RCS(MQTT) 노드는 뺐다 — ERUT MQTT 표준 인터페이스(if-0.12)를 본다. "
     "RCS ↔ 로봇(Modbus)은 차량 인터락에 쓰는 레지스터만 적었다(3S-Elite_Modbus_인터페이스)."),
    ("기준", "RCS 2026-11-12 판 · vehicle_interfaces 패키지(msg · srv) · ERUT MQTT 표준 인터페이스 if-0.12"),
    ("지금 상태", "실제 차량 노드는 아직 없다. RCS 는 더미(가상)나 모의기(ros2 launch vehicle_sim "
     "vehicle_sim.launch.py — 이 표와 같은 인터페이스)로 돈다. RCS 연결 설정 「차량 제어」 = 자동 / 더미 / "
     "ROS 차량 노드."),
    ("section", "누가 무엇을 맡나"),
    ("RCS (3S)", "작업 순서(차량 이동 → 아웃트리거 고정 → 리프트 → 로봇 스캔), 인터락, 화면 · 수동 조작, "
     "ERUT MQTT. 차량에는 ROS 2 서비스로 명령하고 상태 토픽을 구독한다. 모재 표면 거리를 차량 주행 거리로 "
     "환산해 보낸다(3.원주 거리 환산)."),
    ("차량 제어 노드 (3S 차량 담당)", "AGV 주행 · 리프트 · 아웃트리거 · 센서 · 배터리 · PLC 입출력. RCS 명령을 "
     "받아 실행하고 상태를 낸다. PLC 신호(비상정지 · 안전 스캐너 · 접지 · 수평 등)는 이 노드가 읽어 "
     "RobotStatus 칸으로 가공해 준다."),
    ("차량용 PLC", "차량 제어 노드만 통신한다. RCS 는 연결 상태만 본다(7.PLC)."),
    ("로봇 제어 노드 (3S)", "협동로봇 Modbus · 대시보드. 차량과 직접 통신하지 않는다 — 홈 플래그(276) · 차량 "
     "고정 확인(309)만 RCS 를 거쳐 오간다(5.인터락)."),
    ("section", "요구 출처 (칸 색)"),
    ("fill", SOURCE_FILLS[ERUT], ERUT, "ERUT 규격을 지키려고 필요한 것 — ERUT 가 보는 값(배터리 · 충전 · 위치 · "
     "오류)이나 ERUT 명령(구역 검사 · 캘리브레이션 · 일시정지 · 정지)을 차량이 해야 하는 것. 근거 칸에 "
     "ERUT 쪽 이름을 적었다(6.ERUT 보고 값)."),
    ("fill", SOURCE_FILLS[RCS], RCS, "RCS 가 작업 순서 · 도착 판정 · 인터락 · 화면 · 수동 조작을 하려고 필요한 것. "
     "ERUT 규격에는 없다."),
    ("fill", SOURCE_FILLS[BOTH], BOTH, "둘 다 — 예: 리프트 높이는 RCS 의 도착 판정에도, ERUT 위치 보고에도 쓴다."),
    ("section", "상태 칸"),
    (DEFINED, "vehicle_interfaces 에 있고 RCS · 모의기가 그대로 쓴다."),
    ("fill", STATE_FILLS[CHECK], CHECK, "칸은 있지만 뜻 · 값을 RCS 가 가정했다 — 차량 담당이 맞는지 본다(8.정할 것)."),
    ("fill", STATE_FILLS[NEW], NEW, "정의(vehicle_interfaces)와 RCS 처리는 끝났다(2026-11-17) — AGV 노드가 채우면 바로 붙는다. 못 채우면 기본값(모름)으로 두면 RCS 가 조건에서 뺀다. 칸 이름 · 기본값은 RCS_AGV_인터페이스_20261117.xlsx 가 최신."),
    ("section", "단위"),
    ("차량 인터페이스", "거리 · 높이 m, 속도 m/s, 각도 rad"),
    ("RCS 내부 · ERUT", "mm, mm/s 정수 — RCS 가 m ↔ mm 로 바꾼다(차량 쪽은 m 그대로)."),
    ("배터리", "% 정수 0~100 (ERUT 규격이 소수를 금지)"),
    ("section", "RCS 차량 설정 (설정 / 진단 → 차량 설정)"),
    ("값", "차량 폭 · 모재 거리 유지(→ 주행 배율 · SetJob.standoff) · 차량 주행 속도(기본 0.1 m/s) · 캘리브레이션 "
     "최대 시간(명령 때 계산) · 리프트 한계 높이 · 로봇 베이스 높이 · 로봇팔 상승 한계. 차량 노드에 보내는 것은 주행 "
     "거리 · 속도 · 거리 유지 · 리프트 목표 높이뿐이다."),
    ("section", "나누는 원칙 (2026-11-12)"),
    ("RCS 가 관리", "격자 · 구역 크기 · 구역 사이 이동 거리 · 리프트를 몇 mm 로 올릴지 · 주행 배율 환산. 차량 노드는 "
     "이것을 몰라도 된다."),
    ("차량 노드가 할 일", "받은 거리만큼 달리고, 받은 높이로 리프트를 옮기고, 아웃트리거를 고정 · 해제하고, 상태를 낸다. "
     "그래서 SetJob 은 job_id · set_dist · set_speed · standoff 만 받는다(예전 offset_* · total_* 는 뺐다)."),
]

# ================================================================= 1. 노드·통신
NODES = [
    ("section", "노드"),
    ("노드", "차량 제어 노드", "ROS 2 노드 — 이름 미정", "-", "-",
     "AGV 주행 · 리프트 · 아웃트리거 · 센서 · 배터리 · PLC. RCS 명령 실행, 상태 발행", BOTH,
     "RCS 가 차량을 움직이는 유일한 길", "3S 차량 담당 개발 예정 — 모의기 /vehicle_sim 이 대신"),
    ("노드", "RCS — smr_operator_ui", "ROS 2 노드 /smr_operator_ui", "-", "-",
     "차량 상태 구독 · 명령 서비스 호출 · 작업 순서 · ERUT MQTT", BOTH, "-", "있음"),
    ("section", "RCS ↔ 차량 제어 노드 (<ns> = RCS 환경변수 SMR_VEHICLE_NS, 기본 vehicle)"),
    ("토픽", "<ns>/robot_status", "vehicle_interfaces/msg/RobotStatus", "차량 → RCS",
     "1 Hz 이상(모의기 10 Hz). RCS 는 2 초 넘게 안 오면 끊김",
     "차량 상태 · 위치 · 리프트 높이 · 오류 · (추가) 배터리 · 비상정지 · 수평", BOTH,
     "RCS: 도착 판정 · 인터락 · 화면 / ERUT: 배터리 · 충전 · 위치 · 오류 보고(6.ERUT 보고 값)",
     "있음 — 더한 칸은 정의됨, AGV 가 채운다(2.메시지)"),
    ("서비스", "<ns>/set_job", "vehicle_interfaces/srv/SetJob", "RCS → 차량", "이동마다",
     "자동 주행 한 번 설정(거리 · 속도 · 거리 유지)", BOTH,
     "ERUT: start 구역 x 로 이동 / RCS: RCS 격자 작업의 열 이동", "있음 — 이름은 가정"),
    ("서비스", "<ns>/robot_control", "vehicle_interfaces/srv/RobotControl", "RCS → 차량", "요청마다",
     "출발 · 재개(RUNNING) · 일시정지(PAUSED) · 정지(STOP) · 오류 리셋 · 작업 취소", BOTH,
     "ERUT: pause · resume · abort · reset / RCS: 정지 버튼 · 수동 화면 오류 리셋", "있음 — 이름은 가정"),
    ("서비스", "<ns>/manual_command", "vehicle_interfaces/srv/ManualCommand", "RCS → 차량",
     "조그는 누르는 동안 200 ms 마다 다시",
     "수동 조그 · 아웃트리거 고정/해제 · 개별 · 리프트 높이 · 원점/초기화", RCS,
     "RCS 수동 제어 화면. 지금은 자동 순서의 아웃트리거 · 리프트도 이것으로 보낸다(8.정할 것 6)",
     "있음 — 이름은 가정"),
    ("서비스", "<ns>/measure_lap (제안)", "vehicle_interfaces/srv/MeasureLap", "RCS → 차량", "캘리브레이션마다",
     "mode LAP(원통): 원점(QR)에서 출발해 모재를 한 바퀴 돌고 원점에 선다 — 달린 거리 · 원점 인식 결과. "
     "mode ALIGN(곡판, 2026-11-18): 한 바퀴 돌지 않고 원점 QR 마커에 정렬만 하고 선다", ERUT,
     "ERUT calibrate(geometry.shape 로 mode 를 고른다) → evt/complete · calibrated. 원통은 total_length_mm 도. "
     "없으면 원통은 SetJob 거리 이동으로 대신, 곡판은 실패", NEW),
    ("section", "참고 — RCS ↔ 로봇 (차량 인터락에 쓰는 것만, 차량 노드와 무관)"),
    ("토픽", "robot/status/at_home", "std_msgs/Int32 (레지스터 276)", "로봇 노드 → RCS", "10 Hz",
     "로봇이 홈(접힌 자세)인가 — 1 일 때만 RCS 가 차량 · 리프트 · 아웃트리거 해제를 보낸다", BOTH,
     "ERUT: 이동 중에는 home 이어야 한다(벗어나면 E2003) / RCS: 충돌 방지", "있음"),
    ("토픽", "robot/command/vehicle_ready", "std_msgs/Int32 (레지스터 309)", "RCS → 로봇 노드",
     "0.5 초마다 판단, 바뀌면 · 5 초마다 다시", "차량이 굳었다 — 1 이어야 로봇이 팔을 뻗는다(5.인터락 2)", RCS,
     "차량 상태(hold · state · error · 수평)로 RCS 가 정한다", "있음"),
    ("토픽", "robot/command/scan_z_offset", "std_msgs/Int32 (레지스터 358)", "RCS → 로봇 노드",
     "구역마다 리프트를 정할 때 · 마킹 전 0", "리프트 대신 팔을 들어 스캔할 높이 [mm]", RCS,
     "리프트를 덜 올리는 운전(구역이 팔이 닿는 높이 안이면)", "있음(v6)"),
]

# ================================================================= 2. 메시지·서비스
FIELDS = [
    ("section", "RobotStatus.msg — 차량 → RCS, <ns>/robot_status"),
    ("RobotStatus", "header", "std_msgs/Header", "-", "stamp · frame_id", "상태 시각", RCS,
     "안 씀 — 끊김은 받은 시각으로 본다", DEFINED),
    ("RobotStatus", "state", "string", "-", "STOP · RUNNING · PAUSED · HOLD · ERROR",
     "차량 상태. HOLD = 아웃트리거로 고정돼 선 상태(모의기)", BOTH,
     "RCS: 도착(RUNNING 을 본 뒤 STOP/HOLD) · 고정 확인(309) · 수동 명령 허가 / ERUT: activity(moving · "
     "paused · error)", CHECK),
    ("RobotStatus", "hold", "string", "-", "SET · RELEASE", "아웃트리거 고정 / 해제", RCS,
     "아웃트리거 도착 판정 · 고정 확인(309)", DEFINED),
    ("RobotStatus", "error_code", "int32", "-", "0 정상, 그 외 오류", "오류 번호", BOTH,
     "RCS: 0 이 아니면 309 = 0, 하던 이동 실패 / ERUT: evt/error E2001 DRIVE_ERROR(stop)", CHECK),
    ("RobotStatus", "error_msg", "string", "-", "", "오류 설명", BOTH,
     "RCS 알람 / ERUT error.detail", DEFINED),
    ("RobotStatus", "job_id", "string", "-", "SetJob 의 job_id", "지금 하는 작업", RCS,
     "자기가 보낸 SetJob 의 상태만 골라 도착을 본다", DEFINED),
    ("RobotStatus", "set_dist", "float64", "m", "", "목표 이동 거리", RCS, "표시용", CHECK),
    ("RobotStatus", "mv_dist", "float64", "m", "이번 이동에서 0 → set_dist", "이동 거리(차량 주행 거리)",
     BOTH, "RCS: 위치 = 출발 위치 + mv_dist, |mv_dist − set_dist| ≤ 2 mm 면 도착 / ERUT: query "
     "location.vehicle_mm(표면 거리로 환산)", CHECK),
    ("RobotStatus", "speed", "float64", "m/s", "", "현재 이동 속도", RCS, "표시용", DEFINED),
    ("RobotStatus", "sen1_dist / sen2_dist", "float64", "m ?", "", "센서 1 · 2 측정 거리", RCS,
     "지금 안 씀. 모재까지 거리라면 거리 유지 확인 · 캘리브레이션에 쓴다", CHECK),
    ("RobotStatus", "position (Position6D)", "x y z · roll pitch yaw", "m · rad", "", "차량 위치 · 자세", RCS,
     "화면 표시. roll · pitch 는 수평 확인 근거", CHECK),
    ("RobotStatus", "lift_h", "float64", "m", "0 ~ 리프트 최대", "리프트 높이", BOTH,
     "RCS: 리프트 도착(±2 mm) · 화면 / ERUT: query location.lift_mm", DEFINED),
    ("RobotStatus", "battery_pct", "uint8", "%", "0~100 정수, 모르면 255", "배터리 잔량", ERUT,
     "ERUT evt/status · query 의 battery (규격: 배터리가 없으면 칸을 뺀다)", NEW),
    ("RobotStatus", "charging", "bool", "-", "", "충전 중", ERUT, "ERUT evt/status · query 의 charging", NEW),
    ("RobotStatus", "estop", "bool", "-", "", "차량 비상정지 눌림", BOTH,
     "ERUT: evt/error E1002 E_STOP(estop) / RCS: 로봇 · 작업을 세운다", NEW),
    ("RobotStatus", "obstacle_stop", "bool", "-", "", "접근 감지(라이다 · 근접 센서)로 선 상태", RCS,
     "작업을 일시정지로 보고 알람(구매사양: 설정 거리 안 접근 시 일시정지). ERUT 에는 지금 칸이 없다 — "
     "규격 다음 판 후보", NEW),
    ("RobotStatus", "level_state (+ roll · pitch)", "int8 · float64", "- · rad", "1 맞음 · 0 안 맞음 · -1 모름", "아웃트리거 수평 보정 완료",
     RCS, "고정 확인(309) 조건 — 수평이 맞을 때만 로봇 검사 허가", NEW),
    ("RobotStatus", "standoff_meas", "float64", "m", "", "지금 잰 거리 — 차량 안쪽 면 ↔ 모재 표면", RCS,
     "거리 유지 확인 · 화면(sen1/sen2 가 이 값이면 그 칸을 쓴다)", NEW),
    ("RobotStatus", "lap_state · lap_path_m · lap_origin_ok · lap_error", "string · float64 · bool · string",
     "- · m", "IDLE · RUNNING · DONE · FAILED",
     "캘리브레이션 주행 결과 — LAP: 한 바퀴 달린 거리 · 원점(QR)을 다시 찾았나 / ALIGN: QR 에 맞춰 섰나", ERUT,
     "ERUT evt/complete(calibrate) · calibrated. total_length_mm 는 원통만(곡판은 판 가로를 재지 않는다)", NEW),
    ("section", "Position6D.msg"),
    ("Position6D", "x · y · z / roll · pitch · yaw", "float64", "m / rad", "", "위치 · 자세", RCS, "표시용", CHECK),
    ("section", "SetJob.srv — RCS → 차량, <ns>/set_job (자동 주행 한 번 — 받은 거리 · 속도로 달리기만 한다)"),
    ("SetJob 요청", "job_id", "string", "-", "RCS-0001, RCS-0002 … (이동마다 1씩)", "작업 식별자", RCS,
     "상태의 job_id 로 자기 이동을 가려낸다", DEFINED),
    ("SetJob 요청", "set_dist", "float64", "m", "부호 = 방향(+ 원주 진행 방향)",
     "이번 이동에서 차량 중심이 달릴 거리 = 목표 − 지금. RCS 가 모재 표면 거리를 주행 배율로 환산해 보낸다",
     BOTH, "ERUT: start 구역 가운데(area x)로 / RCS: 격자 열 이동", CHECK),
    ("SetJob 요청", "set_speed", "float64", "m/s", "RCS 차량 설정 「차량 주행 속도」(기본 0.1)", "목표 속도", RCS,
     "ERUT 규격에는 차량 속도가 없다(scan.speed 는 로봇 스캔 속도)", CHECK),
    ("SetJob 요청", "(뺐다) offset_dist · offset_height · total_distance · total_height", "-", "-", "-",
     "예전 정의에 있던 오프셋 · 작업 전체 크기", RCS,
     "격자 · 구역 크기 · 리프트 높이는 RCS 가 관리한다 — 차량은 받은 거리만 달리면 되므로 뺐다(2026-11-12)",
     "뺐음"),
    ("SetJob 요청", "standoff", "float64", "m", "0 ~ 2.0", "거리 유지 목표 — 차량 안쪽 면 ↔ 모재 표면", RCS,
     "차량이 모재를 돌며 지킬 거리. RCS 가 차량 설정 「모재 거리 유지」를 이동마다 보낸다(2026-11-12 정함)", DEFINED),
    ("SetJob 응답", "success · message", "bool · string", "-", "", "접수 여부 · 사유", RCS,
     "success 면 RobotControl(RUNNING)으로 출발. false 면 이동 실패", DEFINED),
    ("section", "RobotControl.srv — RCS → 차량, <ns>/robot_control"),
    ("RobotControl 요청", "state", "string", "-", "RUNNING · PAUSED · STOP",
     "RUNNING 출발 · 재개, PAUSED 일시정지, STOP 정지", BOTH,
     "ERUT: pause · resume · abort / RCS: 출발(SetJob 뒤) · 정지 버튼 · 이동 취소", CHECK),
    ("RobotControl 요청", "reset", "uint8", "-", "1 = 오류 리셋", "오류 해제", BOTH,
     "ERUT: reset(장애 해제) / RCS: 수동 화면 「오류 리셋」", CHECK),
    ("RobotControl 요청", "job_cancel", "uint8", "-", "1 = 작업 취소", "SetJob 취소", BOTH,
     "ERUT: abort / RCS: 정지 — 지금은 STOP 으로 대신(예약)", CHECK),
    ("RobotControl 응답", "success · message", "bool · string", "-", "", "접수 여부 · 사유", RCS,
     "false 면 이동 실패(알람)", DEFINED),
    ("section", "ManualCommand.srv — RCS → 차량, <ns>/manual_command"),
    ("ManualCommand 요청", "cmd_mv_fwd / cmd_mv_rear", "bool", "-", "true 동안 이동", "수동 전진 / 후진", RCS,
     "수동 화면 — 누르는 동안 200 ms 마다, 떼면 모든 필드 0/false", CHECK),
    ("ManualCommand 요청", "cmd_trn_left / cmd_trn_right", "bool", "-", "true 동안 회전", "수동 좌 · 우회전", RCS,
     "수동 화면 (받은 자료의 cmd_trl_right 는 오타라 cmd_trn_right 로 고쳤다, 2026-11-17)", DEFINED),
    ("ManualCommand 요청", "cmd_outrg_set", "uint8", "-", "0 없음 · 1 해제 · 2 고정", "아웃트리거 전체 고정/해제", RCS,
     "수동 화면 + 자동 순서(주행 전 해제 · 도착 후 고정). hold 로 끝 판정", CHECK),
    ("ManualCommand 요청", "cmd_man_outrg1 · 2 · 3", "uint8", "-", "0 없음 · 1 상승 · 2 하강", "아웃트리거 개별 상하",
     RCS, "수동 화면", DEFINED),
    ("ManualCommand 요청", "cmd_mv_lift + lift_height", "uint8 + float64", "- / m",
     "1 = 이동, lift_height = 목표 절대 높이", "리프트를 목표 높이로(아웃트리거 고정에서만)", BOTH,
     "ERUT: start 구역 높이(area y) / RCS: 수동 화면 · 주행 전 0", CHECK),
    ("ManualCommand 요청", "cmd_init", "uint8", "-", "1 = 원점/초기화", "수동 모드 원점/초기화", RCS,
     "수동 화면 「원점/초기화」", DEFINED),
    ("ManualCommand 응답", "success · message", "bool · string", "-",
     "수동 모드가 아니면 false", "접수 여부", RCS, "false 면 그 동작 실패", DEFINED),
    ("section", "MeasureLap.srv (제안) — RCS → 차량, <ns>/measure_lap"),
    ("MeasureLap 요청", "job_id · speed · standoff", "string · float64 · float64", "- · m/s · m", "",
     "캘리브레이션 주행 시작 — 속도는 RCS 차량 설정 「차량 주행 속도」, 거리 유지는 「모재 거리 유지」", ERUT,
     "ERUT calibrate · RCS 「계측 시작」 · 차량 「작업 시작」 버튼", NEW),
    ("MeasureLap 요청", "mode (2026-11-18)", "string", "-", "LAP · ALIGN (빈 글자 = LAP)",
     "LAP = 원통 한 바퀴. ALIGN = 곡판 — 한 바퀴 돌지 않고 판 왼쪽 끝의 원점 QR 마커에 정렬(거리 · 방향)만",
     ERUT, "RCS 가 geometry.shape(cylinder · curved_plate)로 고른다", NEW),
    ("MeasureLap 요청", "expected_dist", "float64", "m", "",
     "LAP: 한 바퀴 거리 2π(R + 거리 유지 + 폭/2) — 안전 한도 · 참고. ALIGN: 0", ERUT, "RCS 계산", NEW),
    ("MeasureLap 응답", "success · message", "bool · string", "-", "", "접수 여부", ERUT,
     "거절 → ERUT calibrate 실패", NEW),
]

# ================================================================= 4. 명령 순서
SEQUENCES = [
    ("section", "A. 자동 주행 한 번"),
    ("A 주행", BOTH, "1", "SetJob(job_id, set_dist[m, 차량 주행 거리], set_speed, standoff)",
     "접수하면 success = true", "-", "거절 → 이동 실패(알람 · ERUT E2001)"),
    ("A 주행", BOTH, "2", "RobotControl(state = RUNNING)", "출발. state RUNNING, mv_dist 증가", "-",
     "거절 → 이동 실패"),
    ("A 주행", BOTH, "3", "-", "다 가면 state STOP(또는 HOLD), mv_dist ≈ set_dist",
     "RUNNING 을 본 뒤 STOP/HOLD 이고 |mv_dist − set_dist| ≤ 2 mm",
     "제한 시간(이동 거리 ÷ 속도로 계산 + 여유 — 8.정할 것 15) · state ERROR → 실패"),
    ("section", "B. 아웃트리거 고정 / 해제"),
    ("B 아웃트리거", RCS, "1", "ManualCommand(cmd_outrg_set = 2 고정 / 1 해제)",
     "고정되면 hold SET(state HOLD), 풀리면 hold RELEASE", "hold 가 목표가 되면 끝", "30 초 · ERROR · 거절 → 실패"),
    ("section", "C. 리프트 높이"),
    ("C 리프트", BOTH, "1", "ManualCommand(cmd_mv_lift = 1, lift_height = 목표 m)",
     "아웃트리거 고정(hold SET)에서만 움직인다", "|lift_h − 목표| ≤ 2 mm", "180 초 · ERROR · 거절 → 실패"),
    ("section", "D. 일시정지 · 재개 · 정지 · 리셋"),
    ("D 제어", BOTH, "일시정지", "RobotControl(PAUSED) — ERUT pause · RCS 일시정지", "그 자리에 선다(PAUSED)",
     "-", "-"),
    ("D 제어", BOTH, "재개", "RobotControl(RUNNING) — ERUT resume · RCS 재개", "남은 거리를 이어서", "A-3", "-"),
    ("D 제어", BOTH, "정지", "RobotControl(STOP) — ERUT abort · RCS 정지 · 이동 취소", "선다(STOP)", "-", "-"),
    ("D 제어", BOTH, "리셋", "RobotControl(reset = 1) — ERUT reset · 수동 화면 오류 리셋", "오류를 풀고 STOP/HOLD",
     "-", "-"),
    ("section", "E. 수동 조그 · 아웃트리거 개별 (RCS 수동 제어 화면)"),
    ("E 조그", RCS, "누르는 동안", "ManualCommand(cmd_mv_fwd 등 = true) 200 ms 마다 다시",
     "명령이 오는 동안만 움직이고 끊기면 스스로 선다(모의기 deadman 0.5 초)", "-",
     "로봇이 홈이 아니거나 동작 중이면 RCS 가 버튼을 잠근다"),
    ("E 조그", RCS, "뗄 때", "ManualCommand(모든 필드 0/false)", "선다", "-", "-"),
    ("section", "F. ERUT 구역 검사 한 번 (start) — 차량이 하는 부분"),
    ("F 구역", ERUT, "1", "(로봇이 홈이고 태스크가 안 돌 때만)", "-", "at_home = 1", "아니면 기다린다"),
    ("F 구역", ERUT, "2", "C(리프트 0) → B(해제) — 올라가 있거나 고정돼 있으면", "주행 전 내리고 풀기",
     "C · B 끝 판정", "C · B 와 같음"),
    ("F 구역", ERUT, "3", "A — 구역 가로 가운데(area x)까지, 표면 거리 × 주행 배율", "주행", "A-3", "A 와 같음"),
    ("F 구역", ERUT, "4", "B(고정) → C(구역 높이 area y — 팔로 닿으면 리프트는 그대로, 358 로 팔을 든다)",
     "고정 → 리프트", "B · C 끝 판정", "닿을 수 없는 높이는 RCS 가 ERUT 400 E6003 으로 미리 거절"),
    ("F 구역", ERUT, "5", "vehicle_ready(309) = 1 → 로봇 스캔", "선 채로 있는다", "-",
     "차량이 움직이거나 오류 · 수평 아님이면 309 = 0 → 로봇이 기다린다"),
    ("section", "G. ERUT 캘리브레이션 = 차량 한 바퀴"),
    ("G 캘리브레이션", ERUT, "지금(임시)", "B(해제) → A(set_dist = π × 지름 × 주행 배율)", "그만큼 달린다",
     "ERUT total_length_mm = 달린 거리 ÷ 주행 배율", "실제 한 바퀴 측정 · 원점 인식이 아니다"),
    ("G 캘리브레이션", ERUT, "최대 시간", "-",
     "RCS 차량 설정 「캘리브레이션 최대 시간」. 캘리브레이션 명령을 받으면 RCS 가 계산해 넣는다: 2π(R + 거리 유지 + "
     "폭/2) ÷ 차량 주행 속도 × 1.5(정방향 회전 · 가감속 여유) — 사람이 넣은 값은 그때까지만",
     "그 값이 한 바퀴 제한 시간이고, ERUT 에 evt/info timings.complete_within_ms.calibrate 로 알린다",
     "넘기면 실패. ERUT 는 그 시간이 지나면 query — calibrating 이면 계속 기다린다. 실측 후 여유 배율을 맞춘다"),
    ("G 캘리브레이션", ERUT, "요청", "MeasureLap(job_id, mode = LAP, speed, standoff, expected_dist)",
     "원점(QR)에서 출발해 한 바퀴 돌고 원점에 선다. lap_path_m · lap_origin_ok 를 준다", "lap_state DONE",
     "lap_state FAILED + lap_error → ERUT 실패"),
    ("section", "G-2. ERUT 캘리브레이션 = 곡판 QR 정렬 (geometry.shape = curved_plate, 2026-11-18)"),
    ("G-2 곡판", ERUT, "앞 순서", "로봇 홈 확인 → C(리프트 0) → B(해제)", "원통과 같다", "-",
     "차량은 판 왼쪽 끝의 QR 앞에 놓여 있다(작업자가 놓는다)"),
    ("G-2 곡판", ERUT, "요청", "MeasureLap(job_id, mode = ALIGN, speed, standoff, expected_dist = 0)",
     "한 바퀴 돌지 않는다 — QR 마커에 맞춰 거리(standoff) · 방향을 정렬하고 선다. 그 자리가 x = 0(판 왼쪽 끝)",
     "lap_state DONE · lap_origin_ok = true", "lap_origin_ok false · FAILED · 최대 시간 넘김 → ERUT 실패(E1004)"),
    ("G-2 곡판", ERUT, "최대 시간", "-", "RCS 차량 설정 「곡판 QR 정렬 최대 시간」(기본 120 초)",
     "곡판 calibrate 를 받으면 그 값이 「캘리브레이션 최대 시간」이 되고 ERUT 에 complete_within_ms.calibrate 로 알린다",
     "실측해 맞춘다"),
    ("G-2 곡판", ERUT, "결과", "-", "evt/complete(calibrate) — origin (0,0) · calibration_error_mm",
     "total_length_mm 는 싣지 않는다(판 가로를 재지 않는다 — 규격상 「싣기를 권한다」 선택 칸)",
     "measure_lap 이 없는 차량이면 곡판 원점을 못 잡는다 → 실패"),
    ("G-2 곡판", ERUT, "그 뒤 구역 이동", "A(set_dist = 구역 x 가운데 × 주행 배율)",
     "x 는 판 왼쪽 끝(QR)에서 오른쪽(+)으로 판 가로까지 — 이음매가 없어 감지 않는다. 배율은 원통과 같다(같은 곡률)",
     "F 와 같다", "판 밖 구역은 RCS 가 ERUT 400 E6001 로 미리 거절"),
]

# ================================================================= 5. 인터락·안전
INTERLOCKS = [
    ("section", "RCS 가 지키는 규칙"),
    ("1", BOTH, "차량 · 리프트를 움직이거나 아웃트리거를 풀 때", "RCS",
     "로봇 태스크가 실행 · 일시정지 중이 아니고, 로봇이 홈(at_home = 1)일 때만 보낸다. 수동 화면 버튼도 잠근다",
     "기다렸다가 풀리면 보낸다. 이동 중 홈을 벗어나면 이동을 멈추고 ERUT E2003", "있음"),
    ("2", RCS, "vehicle_ready(309) = 1", "RCS → 로봇",
     "아웃트리거 고정 + 이동 명령 없음 + 상태 수신 중 · hold SET · error_code 0 · state STOP/HOLD "
     "+ estop false · level_state ≠ 0 (모르면 뺀다)", "하나라도 아니면 0", "있음"),
    ("3", RCS, "로봇이 팔을 뻗을 때", "로봇 태스크(v6)", "309 = 1 일 때만 움직인다",
     "시간 초과면 299 = 8 → ERUT E9108", "있음"),
    ("4", RCS, "차량 상태가 끊김", "RCS", "2 초 넘게 RobotStatus 가 안 오면 끊김 — 309 = 0",
     "「자동」이면 쉬는 동안 더미로(작업 · 이동 중에는 안 바꾼다)", "있음"),
    ("5", BOTH, "차량 오류 · 명령 거절 · 시간 초과", "RCS", "하던 순서를 멈추고 알람",
     "ERUT evt/error E2001 DRIVE_ERROR(stop)", "있음"),
    ("section", "차량 노드에 기대하는 규칙"),
    ("7", RCS, "리프트", "차량 노드", "아웃트리거 고정(hold SET)에서만 움직인다", "거절(success false)", CHECK),
    ("8", RCS, "주행", "차량 노드", "아웃트리거가 고정돼 있으면 거절하거나 먼저 푼다(RCS 는 먼저 푼다)", "-", CHECK),
    ("9", RCS, "수동 명령", "차량 노드", "RUNNING · PAUSED · ERROR 에서는 받지 않는다", "success false", CHECK),
    ("10", BOTH, "비상정지", "차량 노드 → RCS", "차량 비상정지를 estop 으로 알린다",
     "RCS 가 로봇 · 작업을 세우고 ERUT E1002 E_STOP", NEW),
    ("11", RCS, "접근 감지", "차량 노드 → RCS", "안전 스캐너 정지를 obstacle_stop 으로 알린다",
     "RCS 가 작업을 일시정지 · 알람(ERUT 에는 지금 칸 없음)", NEW),
    ("12", RCS, "수평 보정", "차량 노드 → RCS", "아웃트리거 고정 뒤 roll · pitch 가 허용 범위면 level_state = 1",
     "false 면 309 = 0(로봇 검사 불가)", NEW),
]

# ================================================================= 6. ERUT 보고 값
ERUT_VALUES = [
    ("section", "차량 값 → ERUT (RCS 가 옮긴다)"),
    ("배터리 잔량", "RobotStatus.battery_pct", "evt/status.battery · res(query).battery",
     "0~100 정수. 255(모름 · 유선 급전)면 칸을 싣지 않는다 — 0 으로 채우면 ERUT 가 시작을 못 한다", NEW),
    ("충전 중", "RobotStatus.charging", "evt/status.charging · res(query).charging", "그대로", NEW),
    ("배터리 부족", "(ERUT 가 판단)", "구역 시작 409 E5001 LOW_BATTERY", "ERUT 가 정한 최소 미만이면 ERUT 가 막는다",
     "규격"),
    ("리프트 높이", "RobotStatus.lift_h", "res(query) location.lift_mm", "m → mm", DEFINED),
    ("차량 위치", "RobotStatus.mv_dist (출발 위치 더함)", "res(query) location.vehicle_mm",
     "차량 주행 거리 → 표면 거리(÷ 주행 배율)", CHECK),
    ("주행 오류", "RobotStatus.state ERROR · error_code · error_msg", "evt/error E2001 DRIVE_ERROR (stop)",
     "detail = error_msg", CHECK),
    ("비상정지", "RobotStatus.estop", "evt/error E1002 E_STOP (estop)", "-", NEW),
    ("자리 이탈", "(차량이 원점 자리를 떴다 — 충전 이동 등)", "evt/error E1003 CALIBRATION_LOST · calibrated = false",
     "차량이 스스로 자리를 뜨는 기능이 있으면 알려 줘야 한다", CHECK),
    ("캘리브레이션 결과", "MeasureLap → lap_path_m · lap_origin_ok", "evt/complete(calibrate).total_length_mm",
     "원통: 달린 거리 ÷ 주행 배율 = 표면 둘레. 곡판(ALIGN): 싣지 않는다 — lap_origin_ok 로 성공만 본다", NEW),
    ("캘리브레이션 최대 시간", "RCS 차량 설정 「캘리브레이션 최대 시간」 — 캘리브레이션 명령 때 계산값",
     "evt/info timings.complete_within_ms.calibrate (선택 — RCS 가 ERUT 에 알린다)",
     "ERUT 가 정해 주는 값이 아니다. 0 이면 싣지 않는다(ERUT 기본 5 분). 값이 바뀌면 evt/info 를 다시 낸다 — "
     "ERUT 는 기다리기 시작할 때 받은 값을 쓰므로, 명령 때 계산한 값은 다음 캘리브레이션부터 반영된다. "
     "Q-31 답(「보내지 않습니다」)을 고쳐 알린다", DEFINED),
    ("section", "ERUT 명령 → 차량 동작 (RCS 가 바꿔 보낸다)"),
    ("start", "SetJob + RobotControl(RUNNING) · ManualCommand(outrg · lift)", "req/start area.start/end x · y",
     "구역 가운데로 주행 → 고정 → 리프트(4.명령 순서 F)", DEFINED),
    ("calibrate", "MeasureLap (mode LAP · ALIGN. 없으면 원통만 SetJob 한 바퀴)",
     "req/calibrate geometry(shape · diameter · height · 곡판이면 width) — if-0.13",
     "원통: 원점(QR)에서 한 바퀴. 곡판: QR 정렬만(2026-11-18). 원점 인식 방식은 차량 쪽(ERUT Q-47: 차량팀 확인 후 회신)",
     NEW),
    ("pause · resume", "RobotControl(PAUSED · RUNNING)", "req/pause · req/resume", "그 자리에 서고 잇는다", CHECK),
    ("abort", "RobotControl(STOP · job_cancel)", "req/abort", "선다 — 로봇은 홈으로", CHECK),
    ("reset", "RobotControl(reset = 1)", "req/reset", "차량 오류 해제", CHECK),
    ("주행 방향", "SetJob.set_dist 부호", "규격: 위에서 보면 반시계(x+)", "실제 AGV 방향을 규격에 맞춘다(Q-47 답)",
     CHECK),
]

# ================================================================= 7. PLC
PLC = [
    ("section", "원칙 (2026-11-17)"),
    ("RCS ↔ PLC", "관여하지 않는다", "PLC 통신 · 연결 상태 · 주소표 · 입출력 · 프로토콜은 모두 차량(AMR) 노드(3S 차량 "
     "담당)가 맡는다. RCS 는 PLC 연결 상태도 보지 않는다 — PLC 가 끊겨 차량이 움직이지 못하면 차량 노드가 "
     "error_code · state ERROR 로 알린다", RCS, "-"),
    ("section", "PLC 신호가 필요한 RCS 기능 — 차량 노드가 RobotStatus 로 가공해 준다"),
    ("비상정지", "→ RobotStatus.estop", "로봇 · 작업 정지, ERUT E1002", BOTH, NEW),
    ("안전 스캐너", "→ RobotStatus.obstacle_stop", "작업 일시정지 · 알람", RCS, NEW),
    ("아웃트리거 접지", "→ RobotStatus.hold (SET · RELEASE)", "고정 판정 · 309", RCS, DEFINED),
    ("리프트 높이", "→ RobotStatus.lift_h", "리프트 도착 · ERUT location.lift_mm", BOTH, DEFINED),
    ("수평(roll · pitch)", "→ RobotStatus.level_state (+ roll · pitch)", "309 조건", RCS, NEW),
    ("주행 허가 · 리프트 브레이크 등 출력", "차량 노드 안에서만", "RCS 는 쓰지 않는다", RCS, "-"),
    ("section", "RCS 할 일"),
    ("상단 PLC 표시", "뺐다(2026-11-17)", "상단에는 AMR(차량 상태 수신)만 있다", RCS, "했음"),
    ("연결 설정의 PLC 칸", "뺐다(2026-11-17)", "IP · 포트 · 프로토콜 · 국번 — RCS 는 PLC 에 붙지 않는다", RCS, "했음"),
    ("I/O 화면의 PLC 자리표시자", "X000 · X001 · X010~ · Y000 · Y010 · D100 · D110 (config/plc_io.json)",
     "뺐다(2026-11-12) — I/O 화면은 밀착 센서(툴 입력)와 차량 상태(RobotStatus)만 본다", RCS, "했음"),
]

# ================================================================= 8. 정할 것
DECISIONS = [
    ("1", BOTH, "이름", "토픽 · 서비스 실제 이름, 패키지 이름(vehicle_interfaces 그대로인지), 네임스페이스",
     "글자까지 같아야 통신된다", "차량 담당"),
    ("2", BOTH, "SetJob.set_dist · RobotStatus.mv_dist", "이번 이동 거리인지 누적인지, 차량 중심 주행 거리인지, "
     "부호와 원주 방향(규격: 위에서 보면 반시계)", "도착 판정 · 위치 · ERUT vehicle_mm", "같이"),
    ("3", RCS, "SetJob 칸", "job_id · set_dist · set_speed · standoff — offset_* · total_* 는 뺐다(격자 · 크기는 "
     "RCS 가 관리, 2026-11-12)", "차량은 받은 거리만 달린다", "정함"),
    ("4", BOTH, "state 값", "RobotStatus.state · RobotControl.state 에 들어갈 값 전부와 뜻(HOLD 포함)",
     "도착 · 고정 확인 · ERUT activity", "차량 담당"),
    ("5", RCS, "주행 전 아웃트리거", "고정된 채 주행 명령이 오면 먼저 푸는지, 거절하는지", "RCS 는 먼저 풀고 보낸다",
     "차량 담당"),
    ("6", BOTH, "자동 순서의 리프트 · 아웃트리거", "ManualCommand 가 수동 모드 전용이면 자동 순서(ERUT 구역 높이)는 "
     "무엇으로 보내나 — 자동용 서비스를 따로 둘지", "ERUT 구역 높이 · 고정", "같이"),
    ("7", RCS, "조그 유지 방식", "200 ms 재전송 · deadman 시간", "떼는 신호를 놓쳐도 서야 한다", "같이"),
    ("8", BOTH, "error_code 표", "번호 · 뜻 · 리셋으로 풀리는지 · 정지 수준", "RCS 알람 · ERUT 장애 레벨 · detail",
     "차량 담당"),
    ("9", RCS, "sen1/sen2 · Position6D", "뜻 · 단위 · 좌표계 · 설치 위치", "거리 유지 확인 · 수평", "차량 담당"),
    ("10", ERUT, "배터리 · 충전", "battery_pct(기본 255 = 모름) · charging 칸은 정의됨 · RCS 완료 — 차량이 채운다",
     "ERUT 가 구역마다 배터리를 본다", "차량 담당"),
    ("11", ERUT, "캘리브레이션 주행", "MeasureLap.srv(job_id · mode · speed · standoff · expected_dist) + lap_* 칸은 "
     "정의됨 · RCS 완료. 남은 것: 원점 인식(ERUT QR) 방식 · 반복 정밀도 · 정지 오차",
     "ERUT calibrate 본체 — measure_lap 이 없으면 원통은 RCS 가 거리 이동으로 대신(곡판은 못 한다)", "차량 담당"),
    ("12", BOTH, "비상정지 · 접근 감지 · 수평", "estop · obstacle_stop · level_state(허용 기울기) — 칸은 정의됨",
     "로봇 검사 허가 · ERUT E1002", "차량 담당"),
    ("13", RCS, "PLC 연결 상태", "정함 — RCS 는 관여하지 않는다. PLC 가 끊겨 차량이 못 움직이면 차량 노드가 "
     "error_code · state ERROR 로 알린다", "-", "정함"),
    ("14", RCS, "PLC 진단 값", "정함 — RCS I/O 화면에 PLC 입출력은 보이지 않는다. 차량 값은 RobotStatus 칸으로만 본다",
     "-", "정함"),
    ("15", BOTH, "속도 · 제한 시간", "set_speed 허용 범위 · 기본값(0.1 m/s) · 리프트 속도. 제한 시간은 고정값이 "
     "아니다 — 구역 사이 주행은 120 초 + 거리 ÷ 속도 × 1.5, 캘리브레이션 한 바퀴는 차량 설정 「캘리브레이션 최대 "
     "시간」(명령 때 계산, 여유 × 1.5). 실측해 여유 배율을 맞춘다. 리프트 · 아웃트리거도 실측으로",
     "RCS 가 기다리는 시간 · ERUT 에 알리는 캘리브레이션 시간(6.ERUT 보고 값)", "같이 — 실측"),
    ("16", RCS, "상태 발행 주기", "RobotStatus 1 Hz 이상", "RCS 는 2 초 넘게 안 오면 끊김", "차량 담당"),
    ("17", RCS, "거리 유지 · 원주 거리 환산", "차량 폭 실측 · 회전 기준점이 폭 가운데인지. 거리 유지 값은 RCS 가 "
     "SetJob.standoff 로 보낸다(정함)",
     "모재 표면 이동에 차량은 (R + d + 폭/2)/R 배 더 달린다(3.원주 거리 환산)", "같이"),
    ("18", RCS, "리프트 한계 · 로봇 베이스 높이", "리프트 최대 높이, 리프트 최하단에서의 로봇 베이스 높이",
     "구역을 리프트로 올릴지 팔로 닿을지 RCS 가 정한다(차량 설정)", "차량 담당"),
    ("19", ERUT, "자리 이탈 알림", "충전 등으로 차량이 원점 자리를 스스로 뜨는 일이 있는지 — 있으면 알려 주는 칸",
     "ERUT E1003 CALIBRATION_LOST", "차량 담당"),
    ("20", RCS, "차량 물리 「작업 시작」 버튼", "차량 버튼은 RCS 「계측 시작」(캘리브레이션만)과 같은 일이다. 차량이 "
     "스스로 돌지 말고, 눌림을 <ns>/start_request(std_msgs/Empty)로 알린다 — RCS 가 같은 순서(로봇 홈 확인 · "
     "리프트 내림 · 아웃트리거 해제 → 한 바퀴, 곡판은 QR 정렬)로 돌린다(정함, RCS 완료)",
     "로봇이 펴진 채 차량이 돌면 안 된다 · ERUT 에 calibrating 으로 보여야 한다", "정함"),
    ("21", ERUT, "곡판 QR 정렬(mode ALIGN)", "QR 에 무엇을 맞추는지(거리 standoff · 방향 · 판 끝줄), 허용 오차, "
     "걸리는 시간, 차량을 QR 앞에 어떻게 놓는지. 한 바퀴는 돌지 않는다(2026-11-18 정함)",
     "곡판 원점 = 판 왼쪽 끝(x = 0). RCS 「곡판 QR 정렬 최대 시간」 기본 120 초", "차량 담당"),
]


# ================================================================= 3. 원주 거리 환산
STANDOFF_TEXT = [
    ("section", "왜 필요한가 (RCS 필요)"),
    ("모재 표면 200 mm ≠ 차량 200 mm", "", "",
     "차량은 모재(검사체)에서 거리 d(차량 안쪽 면 기준, 0~2 m)를 지키며 모재를 돌고, 차량의 회전 · "
     "주행 기준점(차량 가로 길이의 중심)은 안쪽 면에서 c 만큼 더 바깥에 있다. 같은 각도를 도는 동안 "
     "모재 표면은 s 만큼, 차량 기준점은 (R + d + c)/R 배 더 간다. ERUT 구역 좌표는 표면 기준이라 "
     "RCS 가 환산해 차량에 보낸다. 캘리브레이션도 같은 중심으로 돈다."),
    ("section", "기호"),
    ("R", "", "mm", "모재 반지름 = ERUT diameter ÷ 2 (바깥 지름 기준)"),
    ("d", "", "mm", "모재 거리 유지 — 벽 ↔ 차량 센서 면(차량 바깥 끝). 0 ~ 2000. RCS 차량 설정 입력값"),
    ("c", "", "mm", "차량 센서 면 ↔ 회전 · 주행 기준점(차량 폭의 가운데) = 차량 폭 ÷ 2 (RCS 차량 설정 「차량 폭」)"),
    ("s", "", "mm", "모재 표면 기준 이동 거리(호 길이). RCS · ERUT 구역 좌표가 쓰는 값"),
    ("L", "", "mm", "차량 기준점이 실제로 달릴 거리 — SetJob.set_dist"),
    ("section", "공식"),
    ("각도", "θ = s ÷ R", "rad", "모재 중심에서 본 회전 각도"),
    ("차량 실제 이동", "L = s × (R + d + c) ÷ R", "mm", "θ × (R + d + c) 와 같다"),
    ("거꾸로", "s = L × R ÷ (R + d + c)", "mm", "차량이 달린 거리를 모재 표면 기준으로 바꿀 때(mv_dist 환산)"),
    ("한 바퀴", "차량 경로 = 2π(R + d + c)", "mm", "모재 둘레 2πR 과의 비가 곧 배율 (R + d + c)/R — "
     "캘리브레이션 한 바퀴 실측으로 확인 · 보정한다"),
]

STANDOFF_AFTER = [
    ("section", "누가 환산하나 — RCS"),
    ("RCS 가 환산", "", "",
     "RCS 차량 설정의 「차량 폭」 · 「모재 거리 유지」와 ERUT 모재 지름으로 배율 (R + d + 폭/2) ÷ R 을 구해, "
     "구역 위치(모재 표면 x)에 곱한 차량 주행 거리를 SetJob.set_dist 로 보낸다. mv_dist 도 차량 주행 거리로 "
     "받는다. ERUT 에 내보내는 위치 · 진행 거리는 계속 모재 표면 기준이다."),
    ("캘리브레이션", "", "", "한 바퀴 = π × 지름 × 배율 만큼 달리고, ERUT total_length_mm 는 달린 거리 ÷ 배율(표면 둘레). "
     "곡판은 한 바퀴 돌지 않고 QR 정렬만 한다 — 배율은 같은 곡률이라 원통과 같은 식이다."),
    ("처음 값", "", "", "차량 폭 · 거리 유지가 0 이면 배율 1(예전과 같다) — 실측값을 넣어야 한다."),
    ("차량 노드에", "", "", "거리 d 를 SetJob.standoff 로 받아 지키며 돌고, 지금 잰 거리를 standoff_meas 로 준다. "
     "회전 · 주행 기준점이 차량 폭의 가운데가 아니면 그 위치를 알려 준다(8.정할 것 17)."),
]

CALC_INPUTS = (
    ("모재 지름 D", 1300, "mm", "ERUT diameter"),
    ("거리 유지 d", 500, "mm", "벽 ↔ 차량 센서 면 (0 ~ 2000) — RCS 차량 설정 「모재 거리 유지」"),
    ("센서 면 ↔ 차량 중심 c", 400, "mm", "차량 폭 ÷ 2 (예: 폭 800) — RCS 차량 설정 「차량 폭」의 절반"),
    ("모재 표면 이동 s", 200, "mm", "검사 때문에 옮길 거리"),
)
CALC_FILL = PatternFill("solid", fgColor="FFFFF2CC")


def _cell(ws, row, col, value, font=None, fill=None, align=None, fmt=None):
    c = ws.cell(row=row, column=col, value=value)
    c.font, c.border = font or BODY_FONT, BORDER
    c.alignment = align or (LEFT if col >= 4 else CENTER)
    if fill is not None:
        c.fill = fill
    if fmt:
        c.number_format = fmt
    return c


def _tint(ws, start, end, source_col=None, state_col=None) -> None:
    """요구 출처 · 상태 칸만 색칠한다(줄 전체가 아니라)."""
    for r in range(start, end):
        if source_col:
            cell = ws.cell(row=r, column=source_col)
            fill = SOURCE_FILLS.get(cell.value)
            if fill is not None:
                cell.fill = fill
                cell.font = BOLD_BODY
        if state_col:
            cell = ws.cell(row=r, column=state_col)
            fill = STATE_FILLS.get(cell.value)
            if fill is not None:
                cell.fill = fill


def _table(wb, name, title, note, headers, widths, data, left_from, source_col=None, state_col=None):
    ws = sheet(wb, name, title, note, headers, widths)
    end = rows(ws, data, len(headers), left_from=left_from)
    _tint(ws, 5, end, source_col, state_col)
    return ws


def write_standoff(wb) -> None:
    ws = sheet(wb, "3.원주 거리 환산", "원주 거리 환산 — 모재 표면 이동 → 차량 실제 이동 (RCS 필요)",
               "차량은 모재에서 거리 d 를 지키며 차량 가로 길이의 중심을 기준으로 돈다. 노란 칸을 바꾸면 계산기 "
               "값이 다시 계산된다(엑셀 수식).",
               ["항목", "값 · 식", "단위", "설명"], [26, 30, 8, 100])
    r = rows(ws, STANDOFF_TEXT, 4, left_from=2)

    for col in range(1, 5):
        ws.cell(row=r, column=col).fill, ws.cell(row=r, column=col).border = (
            PatternFill("solid", fgColor="FFD6E4F0"), BORDER)
    ws.cell(row=r, column=1, value="계산기 — 노란 칸을 바꾼다").font = BOLD_BODY
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
    r += 1
    # 입력 칸 주소: D · d · c · s 순서(CALC_INPUTS 와 같다).
    D_cell, d_cell, c_cell, s_cell = (f"B{r + i}" for i in range(len(CALC_INPUTS)))
    for name, value, unit, note in CALC_INPUTS:
        _cell(ws, r, 1, name)
        _cell(ws, r, 2, value, fill=CALC_FILL, fmt="0")
        _cell(ws, r, 3, unit)
        _cell(ws, r, 4, note)
        r += 1
    outputs = (
        ("모재 반지름 R", f"={D_cell}/2", "mm", "D ÷ 2", "0"),
        ("배율 (R + d + c) ÷ R", f"=({D_cell}/2+{d_cell}+{c_cell})/({D_cell}/2)", "-",
         "차량이 모재 표면보다 몇 배 더 달리나", "0.000"),
        ("차량 실제 이동 L", f"={s_cell}*({D_cell}/2+{d_cell}+{c_cell})/({D_cell}/2)", "mm",
         "s × 배율 — 이만큼 달려야 표면에서 s 만큼 옮겨진다", "0.0"),
        ("한 바퀴 차량 경로", f"=2*PI()*({D_cell}/2+{d_cell}+{c_cell})", "mm", "캘리브레이션 한 바퀴 예상 거리", "0"),
        ("모재 둘레", f"=PI()*{D_cell}", "mm", "π × D", "0"),
    )
    for name, formula, unit, note, fmt in outputs:
        _cell(ws, r, 1, name, font=BOLD_BODY)
        _cell(ws, r, 2, formula, fmt=fmt, font=BOLD_BODY)
        _cell(ws, r, 3, unit)
        _cell(ws, r, 4, note)
        r += 1

    for col in range(1, 5):
        ws.cell(row=r, column=col).fill, ws.cell(row=r, column=col).border = (
            PatternFill("solid", fgColor="FFD6E4F0"), BORDER)
    ws.cell(row=r, column=1, value="예 — 계산기의 D · c · s 그대로, 거리 유지 d 만 바꾸면").font = BOLD_BODY
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
    r += 1
    for col, head in enumerate(("거리 유지 d [mm]", "배율", "차량 실제 이동 L [mm]", "한 바퀴 차량 경로 [mm]"),
                               start=1):
        _cell(ws, r, col, head, font=BOLD_BODY, align=CENTER)
    r += 1
    for d in (0, 500, 1000, 1500, 2000):
        radius = f"({D_cell}/2)"
        _cell(ws, r, 1, d, fmt="0")
        _cell(ws, r, 2, f"=({radius}+A{r}+{c_cell})/{radius}", fmt="0.000", align=CENTER)
        _cell(ws, r, 3, f"={s_cell}*({radius}+A{r}+{c_cell})/{radius}", fmt="0.0", align=CENTER)
        _cell(ws, r, 4, f"=2*PI()*({radius}+A{r}+{c_cell})", fmt="0", align=CENTER)
        r += 1
    rows(ws, STANDOFF_AFTER, 4, start=r, left_from=2)


def write_readme(wb) -> None:
    ws = sheet(wb, "0.읽는 법", "RCS ↔ 차량 제어 노드 연계 인터페이스 — 2026-11-12 개정",
               "3S RCS 와 3S 차량 제어 노드(AGV · 리프트 · 아웃트리거 · PLC)가 주고받는 것을 요구 출처(ERUT 요청 · "
               "RCS 필요)와 함께 정리했다. 생성: tools/build_vehicle_interface_xlsx.py",
               ["항목", "내용"], [26, 120])
    end = rows(ws, README, 2, left_from=1)
    _tint(ws, 5, end, source_col=1, state_col=1)


def main() -> None:
    wb = Workbook()
    wb.remove(wb.active)
    write_readme(wb)

    _table(wb, "1.노드·통신", "RCS ↔ 차량 제어 노드 — 토픽 · 서비스",
           "ERUT ↔ RCS(MQTT)는 뺐다. <ns> 는 RCS 환경변수 SMR_VEHICLE_NS(기본 vehicle) — 모의기는 파라미터 "
           "namespace 로 맞춘다. 요구 출처: 파랑 ERUT 요청 · 초록 RCS 필요 · 보라 공통.",
           ["구분", "이름", "형식", "방향", "주기 · 시간", "용도", "요구 출처", "근거 · 쓰는 곳", "상태"],
           [8, 28, 30, 13, 24, 44, 11, 46, 22], NODES, left_from=2, source_col=7, state_col=9)

    _table(wb, "2.메시지·서비스", "메시지 · 서비스 필드 — 타입 · 단위 · 값 · 요구 출처",
           "필드 이름은 차량 노드와 글자까지 같아야 한다. 상태: 노랑 확인 필요(RCS 가 가정) · 주황 정의됨 · AGV 구현.",
           ["인터페이스", "필드", "타입", "단위", "값 · 범위", "뜻 · 용도", "요구 출처", "근거 · RCS 가 쓰는 곳", "상태"],
           [17, 26, 16, 8, 28, 32, 11, 52, 11], FIELDS, left_from=2, source_col=7, state_col=9)

    write_standoff(wb)

    _table(wb, "4.명령 순서", "명령 순서 — 무엇을 보내고 언제 끝났다고 보나",
           "RCS(services/vehicle_adapters.py)와 모의기(vehicle_sim)가 같은 규칙을 쓴다. 차량 노드가 다르게 "
           "움직이면 두 곳을 같이 고친다.",
           ["동작", "요구 출처", "순서", "RCS → 차량", "차량이 할 일 · 돌려줄 상태", "RCS 끝 판정", "제한 시간 · 실패"],
           [14, 11, 10, 44, 42, 34, 34], SEQUENCES, left_from=4, source_col=2)

    _table(wb, "5.인터락·안전", "인터락 · 안전",
           "로봇과 차량이 동시에 움직이지 않게 하는 규칙. 로봇 홈(276) · 차량 고정 확인(309)은 로봇 레지스터다"
           "(docs/3S-Elite_Modbus_인터페이스_20260916.xlsx).",
           ["번호", "요구 출처", "언제", "누가 보나", "규칙", "어기면 · 끊기면", "상태"],
           [6, 11, 26, 16, 58, 40, 12], INTERLOCKS, left_from=3, source_col=2, state_col=7)

    _table(wb, "6.ERUT 보고 값", "ERUT 보고 값 — 차량 값이 ERUT 의 어디로 나가나 (ERUT 요청의 근거)",
           "RCS 가 차량 상태를 ERUT MQTT 칸으로 옮기고, ERUT 명령을 차량 명령으로 바꾼다. ERUT 쪽 이름은 "
           "if-0.12 기준이다(MQTT 노드 자체는 ERUT 규격서에 있다).",
           ["항목", "차량 쪽", "ERUT 쪽", "RCS 처리", "상태"],
           [18, 40, 46, 56, 11], ERUT_VALUES, left_from=2, state_col=5)

    _table(wb, "7.PLC", "차량용 PLC — RCS 는 관여하지 않는다",
           "PLC 통신 · 주소표 · 입출력은 차량 제어 노드(3S 차량 담당)가 맡는다. RCS 에 필요한 PLC 신호는 차량 "
           "노드가 RobotStatus 칸으로 가공해 준다.",
           ["항목", "어디로", "내용", "요구 출처", "상태"],
           [24, 40, 70, 11, 16], PLC, left_from=2, source_col=4, state_col=5)

    _table(wb, "8.정할 것", "RCS · 차량 담당이 함께 정할 것",
           "누가 정하나: 차량 담당 = 차량 노드 쪽에서 정해 알려 준다 · 같이 = 둘이 맞춘다.",
           ["번호", "요구 출처", "대상", "내용", "왜 필요한가", "누가 정하나"],
           [6, 11, 28, 62, 44, 12], DECISIONS, left_from=3, source_col=2)

    for ws in wb.worksheets:
        ws.sheet_view.zoomScale = 90
    wb.worksheets[0]["A3"].font = NOTE_FONT
    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT)
    print(f"만들었습니다: {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
