"""RCS 판 구분 — 리눅스 현장판(기본)과 Windows 단독판(TPAC 지원용).

단독판(`smr_operator_ui.standalone`)은 ERUT · 차량(AGV) · PLC 없이 RCS 하나로
작업 영역을 정하고 「검사 시작」으로 한 구역을 스캔한다. TPAC 은 RCS 의 Modbus
중계 서버(502)에서 스캔 좌표를 읽어 간다. 로봇 제어 노드는 별도 프로세스가 아니라
RCS 안에서 돈다(standalone/liteross.py 가 ROS 를 대신 잇는다).

단독판 실행기가 앱을 import 하기 **전에** `enable_standalone()` 을 부른다.
"""

STANDALONE = False

#: 단독판에서만 고를 수 있는 TPAC 전시용 로봇 태스크(robot_task/dusan_task_v5).
#: dusan_v5 폴더의 논센서판이다 — 3점 위치를 돈 뒤 원점에서 시작 신호를 기다리지
#: 않고, 좌우 비비기(적심) 없이 바로 스캔한다.
EXHIBITION_TASK = "dusan_v5_tpac_exhibition"


def enable_standalone() -> None:
    global STANDALONE
    STANDALONE = True
