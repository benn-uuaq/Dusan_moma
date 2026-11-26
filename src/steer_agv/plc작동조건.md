# PLC 작동 조건

ROS 컨트롤러가 RCS 요청을 처리할 때 읽는 조건과 PLC에 보내는 출력을 정리했다. 표의 **승인**은 서비스가 요청을 수락한 시점이다. 차량의 실제 동작 상태는 AGV_STATE(DM002), 아웃트리거 고정 여부는 OUTRIG STATE(DM025)로 확인한다. 표의 출력은 쓰기가 활성화되고 제어 루프의 인터록을 통과했을 때 적용된다.

## 공통 조건

**운전 허가**가 필요한 명령은 PLC 쓰기 활성, RESET 대기 없음, 고속·저속 피드백 최신(기본 0.5초 미만), AGV LIFE(DM000) 변화(기본 3초 미만), ERROR CODE(DM004)=0, E-STOP(DM030)=0, 전·후방 장애물 상태(DM050·051) bit 0=0을 요구한다. 컨트롤러에 유지된 고장, 유효하지 않은 측정값, 오류 또는 초기화 상태도 동작을 차단한다. DM050·051의 bit 1은 감속 신호다. AUTO_READY(DM031)의 상승은 시작 요청 토픽을 발행하며 아래 서비스의 승인 조건에는 포함되지 않는다.

## 명령별 조건

| 명령 | 승인 시 참조하는 PLC 신호·주소 | 추가 조건 | PLC 출력 | 상태 또는 완료 확인 |
| --- | --- | --- | --- | --- |
| Set_Job | 운전 허가, AGV_MODE(DM001)=2, AGV_STATE(DM002)=0·2·32 | 새 작업 ID, 유효한 목표값, 미완료 주행·리프트 없음. FINISH 요청 직후에는 DM002=32까지 대기 | 작업을 내부 등록. COMMAND(DM103)는 HOLD이면 2 유지, STOP·FINISH이면 0 | 서비스 승인 후 RUNNING 요청 대기 |
| RUNNING | 운전 허가, DM001=2, DM002=0 또는 4, OUTRIG STATE(DM025)=0 | 미완료 작업, 리프트 없음. 거리≠0이면 목표 속도>0. PAUSE 재개 시 DM002=4 확인 | COMMAND(DM103)=1, TARGET SPEED(DM106), TARGET POSITION(DM107), TARGET_THETA(DM109), GUIDE_OFFSET_DIST(DM110), TARGET_RADIUS(DM111) | DM002=1에서 주행 프로파일 진행 |
| HOLD (자동 도착) | 운전 허가, DM001=2, DM025=0. 휠 거리(DM005~012), 속도(DM013~020), 조향각(DM021~024)으로 거리와 정지 판정 | 잔여 거리 허용 오차, 속도 지령 거의 0, 추정 차체 속도가 정지 기준 이내로 설정 시간 유지 | DM103=2, DM106=0, DM109=0 | DM002=2 및 고정 여부 DM025 확인. RCS에서 HOLD를 직접 요청하는 경로는 없음 |
| PAUSE | 주행 중 안전 조건 정상. FINISH 명령 중 또는 DM002=32이면 일반 PAUSE 거부 | 작업과 진행 거리 보존. 진행 중인 RUN은 감속도·jerk 제한을 적용 | 감속 중 DM103=1과 DM106 속도 갱신. 지령 및 추정 속도가 정지 기준에 들면 DM103=4, DM106=0 | 요청 승인과 실제 정지는 별도. DM002=4에서 재개 가능 |
| STOP | FINISH 명령 중 또는 DM002=32이면 일반 STOP 거부 | 작업 보존. 접수 시 운전 허가 검사를 요구하지 않음 | DM103=0, DM106=0, 수동 스위치 해제 | 실제 정지는 DM002=0 확인 |
| 작업 취소 | 별도 MODE·STATE 승인 조건 없음. FINISH 중에도 허용 | job_cancel=1, RUNNING·FINISH와 동시 요청 거부. 작업 및 진행 거리 폐기 | DM103=0과 정지 출력 | 내부 작업 해제, 실제 정지는 DM002 확인 |
| FINISH | 운전 허가, DM001=2, DM002=0·2·4·32, 네 휠 속도(DM013~020)가 정지 기준 이내 | 활성 RUN·리프트 없음. STOP 전환 대기 중 재요청 거부 | DM103=32 | DM002=32 확인 후 새 작업 등록 가능 |
| RESET | 유효한 최신 피드백, 현재 고장 원인 해소, DM030=0, DM050·051 bit 0=0, DM002=0·2·4 | 쓰기 가능, 활성 RUN·수동·리프트 없음 | RESET(DM104)=1 | PLC의 쓰기 OK 수신 후 고장·비상정지·장애물 재확인 후 유지된 고장 해제 |
| 수동 주행 | 운전 허가, DM001=1, DM002=0·2, DM025=0 | 상반 방향 동시 요청 거부. 리프트·아웃트리거와 동시 작동 금지 | 전진·후진·좌회전·우회전(DM120~123). 장애물 감속 bit 1이면 주행 스위치 0 | 수동 명령 유효시간 경과 시 스위치 해제 |
| 수동 아웃트리거 | 운전 허가, DM001=0·1, DM002=0·2, 휠 속도(DM013~020)가 정지 기준 이내 | 주행·리프트와 동시 작동 금지 | 요청값 2는 MAN_OUTRIG_SET(DM126), 1은 MAN_OUTRIG_RESET(DM127) | OUTRIG STATE(DM025)로 확인 |
| 리프트 | 운전 허가, DM002=2 및 DM025≠0. lift_control은 DM001=0·1·2, manual_command는 0·1 | 작업 ID와 높이 범위 확인, 활성 RUN·FINISH·수동 스위치 없음. 자동은 유효한 양의 속도 필요 | TARGET HEIGHT(DM108), 자동 모드에서 LIFT_SPEED(DM112) | LIFT HEIGHT(DM026~027)가 목표 허용 오차에 들면 완료. HOLD·고정 해제 또는 모드 변경 시 작업 중단 및 현재 높이 유지 시도 |

AGV_STATE(DM002)=8은 ERROR 피드백이다. COMMAND(DM103)=8 또는 RCS의 ERROR 요청은 허용하지 않는다. DM002=16은 PLC 초기화 진입 피드백이며 수동 초기화 트리거(DM105) 쓰기는 구현되어 있지 않다.

근거 문서는 [PLC 통신방식](<./1.Documets/PLC 통신방식>)이다. 승인·출력 조건은 [컨트롤러](./steer_agv_controller_py/steer_agv_controller_py/agv_controller_node.py), [작업 관리자](./steer_agv_controller_py/steer_agv_controller_py/job_manager.py), [주행 제어](./steer_agv_controller_py/steer_agv_controller_py/motion_controller.py)를 기준으로 한다.
