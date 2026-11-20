"""RCS 알람 목록 — RCS · 로봇 · 차량에서 날 수 있는 알람을 한데 모은다.

  1.알람        코드 · 언제 나나 · 수준 · ERUT 보고 · 풀림 · 작업자 조치
  2.ERUT 거절   ERUT 요청을 RCS 가 받지 않을 때 답하는 번호(알람이 아니라 응답)
  3.코드 대역   ERUT 표준 · 3S 제조사(E9xxx) · 3S 알림(M) 번호 대역

원천은 코드다 — operator-ui app.py · services/erut_session.py · erut_errors.py ·
vehicle_adapters.py, 로봇 태스크 레지스터 299, 로봇 노드(robot/status/*). 코드를 고치면
여기도 맞추고 다시 만든다.
실행:  python3 tools/build_alarm_list_xlsx.py
"""

import math
import sys
from pathlib import Path

from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_modbus_interface_xlsx import NOTE_FONT, rows, sheet  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "RCS_알람_목록_20261119.xlsx"

#: 수준 — ERUT error.level 과 같은 말을 쓴다.
ESTOP, STOP, WARN, MSG, SHOW = "estop", "stop", "warning", "알림", "화면만"
RESET = "리셋(초기화)"
MANUAL = "현장 조치 → 리셋"
LIST = "알람 목록 · 활동 줄"

# ================================================================= 1. 알람
HEAD = ["구분", "코드", "이름", "원천 (어디서 오나)", "언제 나나", "수준", "ERUT 보고", "풀림", "작업자 조치"]
WIDTHS = [8, 8, 24, 26, 54, 9, 18, 22, 40]

ALARMS = [
    ("section", "A. 비상정지 · 안전 — 어느 쪽 버튼이든 차량 · 로봇이 모두 선다(안전 회로 직결 + RCS)"),
    ("안전", "E1002", "E_STOP (차량)", "차량 노드 /vehicle/robot_status.estop",
     "차량(PLC) 비상정지 버튼이 눌렸다 — 로봇 쪽 요청(robot_estop) · 안전 회로로 PLC 가 건 것 포함",
     ESTOP, "evt/error (detail 「차량(PLC)」)",
     "두 쪽 비상정지가 다 풀리면 저절로 해제(cleared)", "원인 확인 → 버튼 해제 → 차량 · 로봇 리셋. 하던 작업은 실패로 끝났다 — 처음부터"),
    ("안전", "E1002", "E_STOP (로봇)", "로봇 노드 robot/status/estop (30001 is_emergency_stopped)",
     "로봇 컨트롤러 비상정지(펜던트 · 컨트롤러 버튼 · 외부 비상정지 입력). RCS 가 차량에도 robot_estop = true 를 보낸다. "
     "차량 · 로봇이 같이 눌려도 E1002 는 하나(detail 에 둘 다)",
     ESTOP, "evt/error (detail 「로봇 컨트롤러」)", "위와 같다", "위와 같다. 상단 Cobot 램프 「비상정지」"),
    ("안전", "E9204", "ROBOT_SAFETY_STOP (로봇 안전정지)", "로봇 Config 입력 CI0 · CI1 / safety_mode 3 · 5 · 12",
     "차량이 움직이는 동안 PLC 가 로봇에 거는 안전정지(비상정지 아님)",
     SHOW, "안 보냄 (그 사이 로봇 알람도 안 보냄)", "CI0 · CI1 이 꺼진 뒤 CI2 · CI3(안전재설정)",
     "기다린다 — 로봇 출발은 풀릴 때까지 미룬다. Cobot 램프 「안전정지」 · 「재설정 대기」, I/O SAFETY 줄"),
    ("안전", "E9305", "VEHICLE_OBSTACLE_STOP (접근 감지 정지)", "/vehicle/robot_status.obstacle_stop",
     "안전 스캐너가 사람 · 물체를 보고 차량이 섰다", STOP + " (일시정지)",
     "안 보냄 (activity paused 로 보인다)", "사람이 「재개」", "주변 확인 후 「재개」"),
    ("안전", "E9306", "VEHICLE_NOT_LEVEL (수평 안 맞음)", "/vehicle/robot_status.level_state = 0",
     "아웃트리거 고정 뒤 수평이 허용 범위 밖 — 로봇 출발 조건(309)이 0 이라 로봇이 기다린다", SHOW,
     "안 보냄 (오래 가면 로봇 E9108)", "수평이 맞으면 저절로", "아웃트리거 · 바닥 확인"),

    ("section", "B. 로봇 태스크가 멈추며 남긴 코드 — 레지스터 299 (태스크가 halt 하기 전에 쓴다. 다음 시작(init)에서 0 → 해제)"),
    ("로봇", "E3001", "PROBE_CONTACT_FAILED", "299 = 1 (probe: center)", "3점 측정 — 가운데 탐촉자가 벽을 못 찾았다",
     STOP, "evt/error + 작업 완료 500", "299 가 0 이 되면 해제(다음 시작)", "탐촉자 · 거리(probe_dist) · 모재 위치 확인 후 다시 시작"),
    ("로봇", "E3001", "PROBE_CONTACT_FAILED", "299 = 2 (probe: left)", "3점 측정 — 왼쪽(원점) 탐촉 실패", STOP,
     "evt/error + 작업 완료 500", "위와 같다", "위와 같다"),
    ("로봇", "E3001", "PROBE_CONTACT_FAILED", "299 = 3 (probe: right)", "3점 측정 — 오른쪽 탐촉 실패", STOP,
     "evt/error + 작업 완료 500", "위와 같다", "위와 같다"),
    ("로봇", "E9104", "ARC_ZERO", "299 = 4", "호 길이(256) · 반지름(260)이 0 — 작업 영역 값이 없다", STOP,
     "evt/error + 작업 완료 500", "위와 같다", "캘리브레이션(모재 지름) · 구간 값 확인"),
    ("로봇", "E3003", "MARKER_CONTACT_FAILED", "299 = 5", "마킹 자리에서 벽을 못 찾았다", STOP,
     "evt/error + 마킹 완료 500", "위와 같다", "마킹 자리 · 모재 확인"),
    ("로봇", "E3002", "PROBE_CONTACT_LOST", "299 = 6", "스캔 중 눌림을 보정 한계(press_max)까지 해도 유지 못 했다(센서판)",
     STOP, "evt/error + 작업 완료 500", "위와 같다", "탐촉자 · 모재 표면 확인 후 그 구역 처음부터"),
    ("로봇", "E9107", "SCAN_IK_FAILED", "299 = 7", "스캔 경로에 역기구학 해가 없다", STOP,
     "evt/error + 작업 완료 500", "위와 같다", "로봇 위치 · 자세 · 구역 크기 확인"),
    ("로봇", "E9108", "VEHICLE_HOLD_TIMEOUT", "299 = 8", "차량 고정 확인(309)을 약 2 분 기다려도 못 받았다", STOP,
     "evt/error + 작업 완료 500", "위와 같다", "아웃트리거 고정 · 차량 정지 · 수평 확인"),
    ("로봇", "E9109", "SCAN_ROWS_OVER_LIMIT", "299 = 9 (v6)", "줄 수가 로봇 한도(200 줄)를 넘었다", STOP,
     "evt/error + 작업 완료 500", "위와 같다", "구역 높이 · 이동높이 확인"),
    ("로봇", "E91nn", "ROBOT_TASK_STOPPED", "299 = 그 밖의 값 nn", "표에 없는 태스크 코드로 멈췄다", STOP,
     "evt/error + 작업 완료 500", "위와 같다", "펜던트 메시지 확인"),

    ("section", "C. 로봇 — RCS 가 로봇 상태를 보고 낸다"),
    ("로봇", "E3002", "PROBE_CONTACT_LOST", "RCS — 눌림 상태(288 = 2)", "스캔 중 눌림 센서가 1 초 넘게 떨어져 있다(299 = 6 이 먼저 왔으면 안 낸다)",
     STOP, "evt/error (probe: center)", MANUAL, "탐촉자 · 모재 확인 후 그 구역 처음부터"),
    ("로봇", "E9201", "ROBOT_ALARM", "로봇 노드 robot/status/alarms (30001 알람)",
     "로봇 컨트롤러가 알람을 냈다(같은 알람 2 초 안 반복은 하나로). 안전정지 중 · 직후 3 초 · 차량 이동 중에는 알람 목록에만",
     WARN, "evt/error (detail = 알람 글)", "10 초 잠잠하면 저절로 해제", "펜던트 알람 확인. 태스크가 멈췄으면 E9202 가 따로 난다"),
    ("로봇", "E9202", "ROBOT_TASK_STOPPED", "로봇 노드 task_state(500) = 3",
     "스캔 중 RCS 가 세우지 않았는데 태스크가 멈췄다(1.5 초 기다려 본다) — 역기구학 · 특이점 · 보호정지 등. 30 초 안의 로봇 알람을 까닭으로 싣는다",
     STOP, "evt/error + 작업 완료 500", RESET, "펜던트에서 원인 확인 → 리셋 → 그 구역 처음부터"),
    ("로봇", "E9203", "ROBOT_START_FAILED", "RCS — play 뒤 task_state",
     "스캔 태스크를 틀었는데 20 초 안에 돌지 않았다 — 로봇 연결 · 태스크 파일 · 원격 제어 모드", STOP,
     "evt/error + 작업 완료 500", RESET, "로봇 연결 · 펜던트 태스크 · 원격 모드 확인"),
    ("로봇", "E9304", "WORK_AREA_NOT_SET", "RCS — 보낸 작업 영역",
     "스캔을 틀려는데 호 길이 · 반지름이 0(캘리브레이션 · 구간 값을 아직 못 받음) — 로봇까지 가지 않고 막는다", STOP,
     "evt/error", MANUAL, "캘리브레이션 · 구간 설정 먼저"),
    ("로봇", "E2002", "HOME_FAILED", "RCS — 홈 플래그(276)",
     "홈(이동 안전 자세)으로 거두라고 했는데 home_within_ms(속도 100 % 에서 20 초, 비율에 반비례) 안에 못 거뒀다",
     STOP, "evt/error", "홈에 닿으면 저절로 해제 · 리셋하면 다시 거둔다", "로봇 · 주변 확인 후 리셋"),
    ("로봇", "E2002", "HOME_FAILED (home 명령)", "ERUT home 명령(3S 확장)", "ERUT 가 시킨 홈 이동이 120 초 안에 끝나지 않았다",
     STOP, "home 완료 500 (evt/error 아님)", "-", "로봇 확인"),
    ("로봇", "E2003", "HOME_LOST_WHILE_MOVING", "RCS — 홈 플래그(276)",
     "차량 · 리프트가 움직이는 중에 로봇이 홈을 벗어났다 — 차량 · 리프트를 세운다", STOP, "evt/error", MANUAL,
     "로봇이 왜 움직였는지 확인 → 홈 → 리셋"),
    ("로봇", "E9205", "ROBOT_LINK_LOST (로봇 연결 끊김)", "로봇 노드 robot/status/connected",
     "운영자가 끊지 않았는데 로봇 연결이 끊겼다(응답 없음 10 초)", "알림", "안 보냄 (evt/status 로 보인다)",
     "다시 붙으면 저절로(자동 재연결)", "로봇 전원 · 네트워크 확인"),
    ("로봇", "E9206", "ROBOT_COMMAND_FAILED (로봇 명령 실패)", "로봇 노드 서비스 응답", "play · stop · 태스크 불러오기 등 로봇 명령이 실패했다(출발 실패면 E9203 의 까닭이 된다)",
     "알림", "안 보냄", "-", "로봇 연결 · 원격 모드 확인"),
    ("로봇", "E9207", "ROBOT_PAUSE_REJECTED (일시정지 거절)", "로봇 노드 pause 응답", "로봇이 pause 를 거절했다 — 대신 stop 한다. 재개하면 그 구역을 처음부터",
     "알림", "안 보냄", "-", "-"),
    ("로봇", "E9208", "ROBOT_RESUME_RESTART (재개 불가 — 구역 처음부터)", "RCS — 재개 때 task_state", "재개하려는데 태스크가 일시정지 상태가 아니다 — 그 구역을 처음부터 다시 한다",
     "알림", "안 보냄", "-", "-"),
    ("로봇", "E9209", "ROBOT_REGISTER_WRITE_FAILED (스캔 속도 · 시작 허가 못 보냄)", "RCS → 로봇 레지스터 307 · 267", "스캔 속도 비율이나 시작 허가를 로봇에 쓰지 못했다",
     "알림", "안 보냄", "-", "제어 노드 · 로봇 연결 확인"),

    ("section", "D. 차량 — 차량 · 리프트 · 아웃트리거 동작 실패는 모두 E2001"),
    ("차량", "E2001", "DRIVE_ERROR (시간 초과)", "RCS — 차량 어댑터",
     "제한 시간 안에 끝나지 않았다 — 주행 120 초 + 거리 ÷ 속도 × 1.5, 아웃트리거 30 초, 리프트 180 초",
     STOP, "evt/error (detail 「차량 동작 실패 — …」)", MANUAL, "차량 · 리프트 확인"),
    ("차량", "E2001", "DRIVE_ERROR (거절)", "차량 노드 서비스 응답 success = false", "set_job · robot_control · manual_command · set_mv_lift 를 차량이 거절했다",
     STOP, "evt/error", MANUAL, "차량 상태 · 모드 확인(수동 명령은 RUNNING · PAUSED · ERROR 에서 거절)"),
    ("차량", "E2001", "DRIVE_ERROR (차량 오류)", "/vehicle/robot_status.error_code ≠ 0 · state = ERROR",
     "차량이 오류(error_code · error_msg — RCS_AGV 인터페이스 「차량 오류 번호」)를 냈다. 움직이던 중이거나 작업 중"
     "(구역 검사 · 마킹 · 캘리브레이션)이면 작업을 세운다. 쉬는 중이면 알람 목록에만 뜨고 ERUT 에는 안 보낸다",
     STOP, "evt/error (작업 중일 때)", "리셋 — RCS 알람 리셋 · ERUT reset 이 차량에도 reset = 1 을 보낸다",
     "차량 오류 번호 확인 → 원인 제거 → 리셋"),
    ("차량", "E2001", "DRIVE_ERROR (PLC 연결 실패 — 701)", "/vehicle/robot_status.error_code = 701 (PLC_COMM_LOST)",
     "차량 노드가 PLC 와 끊겼다(2026-11-20). 상단 「PLC」 · 연결 설정 「PLC 연결」이 「연결 실패」(error_msg 에 까닭). 처리는 위 "
     "차량 오류와 같다(작업 중이면 세우고, 쉬는 중이면 알람 목록만)", STOP, "evt/error (작업 중일 때)",
     "PLC 가 다시 붙으면 차량 노드가 701 을 스스로 푼다",
     "PLC 전원 · 배선 확인 → 연결 설정 「PLC 다시 연결」(차량에 reset = 1)"),
    ("차량", "E2001", "DRIVE_ERROR (못 보냄)", "RCS — 차량 서비스", "차량 서비스가 없어 명령을 보내지 못했다", STOP, "evt/error",
     MANUAL, "차량 노드가 떠 있는지 확인"),
    ("차량", "E1004", "CALIBRATION_FAILED", "measure_lap · /vehicle/robot_status.lap_*",
     "캘리브레이션 주행 실패 — lap_state FAILED · 원점 QR 을 다시 못 찾음(원통) · QR 정렬 못 함(곡판) · 최대 시간 넘김 · "
     "measure_lap 거절 · 곡판인데 measure_lap(ALIGN)이 없음",
     STOP, "calibrate 완료 500 (evt/error 아님)", "-", "QR · 차량 확인 후 calibrate 다시"),
    ("차량", "E9310", "LOCAL_CALIBRATION_FAILED (계측 실패)", "RCS 「계측 시작」 · 차량 「작업 시작」 버튼", "위 E1004 와 같은 까닭 — RCS 단독 계측이라 ERUT 에 안 낸다",
     "알림", "안 보냄", "-", "위와 같다"),
    ("차량", "E9307", "VEHICLE_STATUS_MISSING (차량 상태 안 들어옴)", "/vehicle/robot_status", "작업을 시작하는데 차량 상태가 2 초 넘게 안 온다 — 첫 동작에서 멈춘다",
     "알림", "안 보냄", "들어오면 저절로", "차량 노드 · 네트워크 · ROS_DOMAIN_ID 확인"),
    ("차량", "E9308", "VEHICLE_INTERFACE_MISSING (차량 인터페이스 없음)", "RCS — vehicle_interfaces", "ROS 워크스페이스를 소싱하지 않아 차량 인터페이스를 못 쓴다",
     "알림", "안 보냄", "-", "source install/setup.bash 뒤 RCS 다시 시작"),
    ("차량", "E9309", "VEHICLE_DUMMY_MODE (차량 제어가 더미)", "RCS 연결 설정", "차량 상태가 들어오는데 차량 제어가 「더미」로 고정돼 있다",
     "알림", "안 보냄", "-", "연결 설정 → 차량 제어 「자동」 또는 「ROS 차량 노드」"),

    ("section", "E. 작업 · 설정"),
    ("작업", "E1003", "CALIBRATION_LOST", "(규격 — 지금 RCS 가 내는 경로 없음)",
     "차량이 원점 자리를 벗어나 좌표계를 믿을 수 없다 — 차량이 자리 이탈을 알리는 칸이 생기면 낸다", STOP, "evt/error", RESET,
     "calibrate 다시"),
    ("작업", "E9401", "STOPPED_BY_OPERATOR", "RCS 「정지」", "ERUT 작업 중에 RCS 화면에서 작업자가 정지했다", "-",
     "작업 완료 5xx 의 까닭", "-", "-"),
    ("작업", "E9403", "MARK_FAILED", "마킹", "마킹을 다 못 찍었다(찍은 점 · 못 찍은 점을 싣는다)", "-", "mark 완료 500 의 까닭", "-",
     "못 찍은 점 확인"),
    ("작업", "M1001", "HOME_NOT_ALLOWED", "ERUT home 명령", "로봇이 동작 중이라 홈 이동을 받을 수 없다", MSG, "evt/message", "-", "-"),
    ("작업", "M2001", "ARC_LIMIT_CLAMPED", "RCS 작업 영역", "작업 호 길이가 로봇 좌우 한계(현 700 mm)를 넘어 최대값으로 줄여 진행한다",
     MSG, "evt/message (ERUT 작업일 때만)", "-", "작업 영역 가로 확인"),
    ("작업", "M2002", "AREA_APPLY_PENDING", "RCS → 로봇 작업 영역", "로봇 연결 · 태스크 상태 때문에 작업 영역을 아직 못 보냈다 — 조건이 풀리면 다시 보낸다",
     MSG, "evt/message (ERUT 작업일 때만)", "저절로", "-"),
    ("작업", "M2003", "HOLDER_MISMATCH", "ERUT holder.channel_count", "ERUT 홀더 채널 수가 RCS 장착 홀더(5축 · 8축)와 다르다 — 로봇은 RCS 설정으로 움직인다",
     MSG, "evt/message", "-", "홀더 설정 확인"),
    ("작업", "E9404", "LOCAL_START_REFUSED (검사 시작 거절)", "RCS 「검사 시작」", "작업 영역 세로가 한 구간 최대(1200 mm)를 넘거나, 좌 · 우 상승으로 줄이 200 개를 넘는다",
     "알림", "안 보냄", "-", "작업 영역 세로 · 상승 높이 확인"),
    ("작업", "E9405", "HOLDER_CHANGE_REFUSED (홀더 바꾸기 거절)", "RCS Cobot 설정", "작업 중에 장착 홀더를 바꾸려 했다", "알림", "안 보냄", "-", "작업을 마친 뒤 바꾼다"),

    ("section", "F. 연결 · 기록 — 상단 배지 · 화면 표시"),
    ("연결", "E9501", "ERUT_LINK_DOWN (ERUT 연결 안 됨)", "ERUT MQTT 브로커", "브로커에 붙지 못했다(접속 정보 · 계정 · 네트워크)",
     SHOW, "-", "붙으면 저절로", "연결 설정(주소 · 포트 · 계정) · 네트워크 확인"),
    ("연결", "E9502", "ERUT_CLIENT_ID_CONFLICT (ID 중복)", "ERUT MQTT 브로커",
     "같은 장치 ID 의 다른 RCS 와 번갈아 끊겨 다시 붙기를 멈췄다(상단 ERUT 배지 「ID 중복」)", SHOW, "-",
     "다른 RCS 를 끄고 다시 연결", "같은 장치 ID 를 쓰는 다른 PC 확인"),
    ("연결", "E9503", "VEHICLE_LINK_LOST (AMR 끊김)", "/vehicle/robot_status", "차량 상태가 2 초 넘게 안 온다(상단 AMR 배지)", SHOW, "-", "들어오면 저절로", "차량 노드 확인"),
    ("연결", "E9504", "TPAC_NO_REQUEST (TPAC 요청 없음)", "TPAC 브리지 화면", "TPAC 이 5 초 넘게 브리지를 묻지 않는다", SHOW, "-",
     "물어오면 저절로", "TPAC 전원 · 네트워크 · TPAC 의 브리지 주소 확인"),
    ("연결", "E9505", "TPAC_PORT_BUSY (TPAC 포트 못 엶)", "TPAC 브리지 화면", "TPAC 이 읽어 갈 502 포트를 열지 못했다(다른 브리지 · RCS · 포트프록시가 쓰는 중)",
     SHOW, "-", "포트를 비우고 서버 시작", "그 포트를 쓰는 프로그램 확인(netstat · ss)"),
    ("기록", "E9506", "RECORD_WRITE_FAILED (운영 기록 문제)", "RCS 운영 기록", "작업 기록 파일을 쓰지 못했다", "알림", "안 보냄", "-", "저장 공간 · 권한 확인"),
]

# ================================================================= 2. ERUT 거절
REFUSALS = [
    ("section", "요청 자체 검사 — 원점과 상관없이 요청만 보고(먼저 본다, 400 → 501 → 409)"),
    ("E6001", "BAD_REQUEST", "400",
     "칸이 없거나 값이 틀림 · geometry 없음(calibrate) · 모르는 surface · 속도 두 칸이 없거나 2 % 미만 · 100 % 초과 · pitch · holder 값이 "
     "틀림 · 구역 크기 0 이하 · 모재 밖 구역 · 곡판인데 판 가운데를 중심으로 하지 않는 구역 · 줄 수 200 초과"),
    ("E6005", "REQUEST_EXPIRED", "400", "일을 시키는 요청이 보낸 시각보다 10 초 넘게 늦게 왔다"),
    ("E6501", "NOT_IMPLEMENTED", "501", "모르는 동작 · shapes 에 없는 모양 · 장비 마커로 찍는 마킹(marking)"),
    ("section", "상태 · 원점 판정"),
    ("E6104", "CALIBRATION_REQUIRED", "409", "원점이 없음(calibrated = false) · 원점을 잡을 때와 모양 · 지름(±1 mm 넘게)이 다름"),
    ("E6101", "BUSY", "409", "다른 일을 하는 중 · 일시정지해 둔 작업이 있음 · 펜던트에서 튼 태스크가 도는 중"),
    ("E6103", "FAULT_ACTIVE", "409", "장애 · 비상정지 상태"),
    ("E6102", "NOT_RESUMABLE", "409", "멈춘 작업이 없음 · 멈춘 자리를 지키지 못함(밀렸거나 태스크가 섰다)"),
    ("section", "원점 기준 값 검사 — 원점 판정 뒤"),
    ("E6001", "BAD_REQUEST", "400", "원통: start.x 가 0 이상 둘레 미만이 아님 · 구역 가로가 둘레를 넘음"),
    ("E6002", "AREA_WIDTH", "400", "구역 가로가 로봇이 한 번에 훑는 최대 가로(현 700 mm 기준, 지름마다 다름)를 넘음 — error.max_width"),
    ("E6003", "AREA_HEIGHT", "400", "구역 세로가 한 구간 최대를 넘음 · 리프트 한계 + 팔로 닿는 높이 밖 — error.max_height"),
    ("section", "마킹"),
    ("E6004", "WRONG_POINT", "400", "mark_next 의 점이 기다리는 점이 아님 — error.waiting_point_id"),
]


# ================================================================= 3. 3S 코드 대역
BANDS = [
    ("section", "ERUT 표준 번호 — 규격 그대로(E1xxx 안전 · 원점, E2xxx 구동 · 홈, E3xxx 탐촉자, E4xxx 내부, E5xxx 배터리, E6xxx 요청)"),
    ("E1002 · E1003 · E1004", "표준", "비상정지 · 원점 잃음 · 원점 잡기 실패"),
    ("E2001 · E2002 · E2003", "표준", "구동(차량 · 리프트 · 아웃트리거) · 홈 거두기 실패 · 이동 중 홈 벗어남"),
    ("E3001 · E3002 · E3003", "표준", "탐촉자 접촉 실패 · 접촉 잃음 · 마커 접촉 실패 (error.probe 로 어느 탐촉자인지)"),
    ("E6001 ~ E6501", "표준", "요청 거절(응답) — 2.ERUT 거절"),
    ("section", "3S 제조사 번호 E9xxx — 이름은 3S 가 정한다. 「ERUT 보고」가 안 보냄인 것은 RCS 화면 · 기록에만 쓰는 번호다"),
    ("E91xx", "로봇 태스크", "로봇 태스크가 멈추며 남긴 코드(레지스터 299 = nn → E91nn). E9104 · E9107 · E9108 · E9109 를 쓴다"),
    ("E92xx", "로봇", "로봇 컨트롤러 알람 · 태스크 상태 · 연결 · 명령(E9201 ~ E9209)"),
    ("E93xx", "차량 · 작업 영역", "작업 영역 · 차량 상태 · 계측(E9304 ~ E9310). E9301 · E9303 은 옛 번호라 다시 쓰지 않는다(지금 E2001 · E2003)"),
    ("E94xx", "운영", "작업자 정지 · 마킹 · RCS 화면 거절(E9401 · E9403 ~ E9405). E9402 는 옛 번호(지금 E1004)"),
    ("E95xx", "연결 · 기록", "ERUT · 차량 · TPAC 연결, 운영 기록(E9501 ~ E9506)"),
    ("section", "3S 알림 번호 M — evt/message (장애가 아닌 안내)"),
    ("M1xxx", "홈", "M1001 HOME_NOT_ALLOWED"),
    ("M2xxx", "작업 영역 · 홀더", "M2001 ARC_LIMIT_CLAMPED · M2002 AREA_APPLY_PENDING · M2003 HOLDER_MISMATCH"),
]


def _fit_rows(ws, widths, start=5) -> None:
    """줄바꿈된 글이 잘리지 않게 줄 높이를 글 길이로 맞춘다(한글은 칸 너비 1.8 당 한 글자쯤)."""
    merged_rows = {rng.min_row for rng in ws.merged_cells.ranges}
    for r in range(start, ws.max_row + 1):
        if r in merged_rows:
            continue
        lines = 1
        for c, width in enumerate(widths, start=1):
            text = ws.cell(row=r, column=c).value
            if not isinstance(text, str) or not text:
                continue
            per_line = max(int(width / 1.8), 4)
            lines = max(lines, sum(math.ceil(len(part) / per_line) or 1 for part in text.split("\n")))
        ws.row_dimensions[r].height = max(22, 15 * lines + 6)


def main() -> None:
    wb = Workbook()
    wb.remove(wb.active)
    ws = sheet(wb, "1.알람", "RCS 알람 목록 — RCS · 로봇 · 차량 (2026-11-19)",
               "RCS 화면의 알람 목록 · 활동 줄 · 상단 배지에 뜨는 것과 ERUT 로 보내는 것. 「수준」은 ERUT error.level "
               "(estop · stop · warning) 이고, stop 이상이면 로봇을 세우고 하던 작업을 실패로 끝낸다. 「알림」은 화면 알람 목록에만, "
               "「화면만」은 램프 · 배지 · 활동 줄에만 뜬다. 리셋 = RCS 알람 리셋 또는 ERUT reset. 「ERUT 보고」가 안 보냄 · - 인 "
               "번호는 RCS 화면 · 기록용이다(번호 대역은 3.코드 대역). RCS 알람 목록에는 「시각  [번호] 글」로 뜨고 운영 기록"
               "(알람이벤트) 의 코드 칸에도 같은 번호가 남는다.",
               HEAD, WIDTHS)
    rows(ws, ALARMS, len(HEAD), left_from=3)
    _fit_rows(ws, WIDTHS)

    ws = sheet(wb, "2.ERUT 거절", "ERUT 요청을 받지 않을 때 답하는 번호 (알람 아님 — 응답)",
               "RCS 가 ERUT 요청(res)에 거절로 답하는 경우. 장비가 움직이기 전에 답하므로 알람 목록에는 안 뜨고 활동 줄에만 "
               "「ERUT … 거절」로 남는다. 여러 잘못이 겹치면 위 표의 순서(400 → 501 → 409)로 답한다(ERUT R-17).",
               ["번호", "이름", "status", "RCS 가 거절하는 때"], [10, 24, 9, 110])
    rows(ws, REFUSALS, 4, left_from=4)
    _fit_rows(ws, [10, 24, 9, 110])

    ws = sheet(wb, "3.코드 대역", "번호 대역 — ERUT 표준 · 3S 제조사(E9xxx) · 3S 알림(M)",
               "새 알람에 번호를 줄 때 이 대역을 따른다. 같은 상황은 누가 알아채든 같은 번호다(ERUT 규격).",
               ["번호", "구분", "뜻"], [26, 18, 100])
    rows(ws, BANDS, 3, left_from=3)
    _fit_rows(ws, [26, 18, 100])

    for ws in wb.worksheets:
        ws.sheet_view.zoomScale = 90
        ws.cell(row=2, column=1).font = NOTE_FONT
    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT)
    print(f"만들었습니다: {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
