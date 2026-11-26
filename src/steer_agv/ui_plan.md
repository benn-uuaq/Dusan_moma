# Steer AGV 조작 UI 설계 및 사용법

조작 패널은 Python 컨트롤러에 내장하거나 독립 실행할 수 있는 PyQt5 클래스 `Panel`이다. 내장 실행에서는 Qt가 메인 스레드, ROS 제어 콜백이 `agv-control` 스레드, PLC 소켓이 `plc-tcp` 스레드를 사용한다. 창과 제어 루프는 최근 이벤트를 보관하는 메모리 버퍼로 연결된다.

## 1. 실행과 연결

ROS 2 Humble 워크스페이스에서 UI를 처음 빌드하거나 UI 패키지를 갱신할 때 다음 명령을 실행한다.

```bash
cd /home/rnd08/ros2_ws
source /opt/ros/humble/setup.bash
source install/setup.bash
colcon build --symlink-install --packages-select steer_agv_controller_py steer_agv_operator_ui steer_agv_bringup
source install/setup.bash
ros2 launch steer_agv_bringup controller.launch.py debug_ui:=true plc_enabled:=true
```

내장 화면은 시작할 때 자동 연결된다. 아래 명령은 컨트롤러를 새로 시작할 때 사용한다. 기존 컨트롤러에 조작·수신 화면만 연결할 때는 같은 ROS 도메인에서 `ros2 run steer_agv_operator_ui operator_ui`를 사용한다.

| 목적 | 컨트롤러 실행 명령 |
| --- | --- |
| PLC 읽기와 화면 모니터링 | `ros2 launch steer_agv_bringup controller.launch.py debug_ui:=true plc_enabled:=true` |
| 실제 PLC 읽기·쓰기 및 조작 | `ros2 launch steer_agv_bringup controller.launch.py debug_ui:=true plc_enabled:=true plc_writes_enabled:=true` |

**UI 연결 ON**을 누르면 노드 `steer_agv_operator_ui`가 구독과 서비스 클라이언트를 생성한다. **UI 연결 OFF** 또는 창 닫기는 UI 쪽 연결을 종료한다. 컨트롤러와 접수된 작업은 계속 실행된다. 주행 정지를 요청할 때는 **STOP**을 누르고 차량 상태와 속도를 확인한다.

## 2. 조작 탭

직접 입력칸 오른쪽에는 컨트롤러가 가장 최근 처리한 주행 또는 리프트 요청의 원본 값과 수락·거부 결과가 표시된다. 컨트롤러가 `job_feedback_topic`으로 지정한 토픽(기본값 `/vehicle/last_job_request`)에 마지막 결과 한 건을 보관하므로, 같은 컨트롤러가 실행되는 동안 UI를 나중에 연결해도 조회할 수 있다. 상단 차량 상태의 목표 거리는 등록 작업 기준이다.

화면 상단에는 차량 상태·오류 코드·작업 ID·이동 거리·속도와 PLC 진단 메시지가 표시된다. 조작 탭 하단에는 최근 명령 요청과 컨트롤러 응답이 최대 100개 텍스트 블록까지 남는다.

| 버튼 | ROS 서비스와 요청 | 사용 조건 또는 의미 |
| --- | --- | --- |
| 작업 등록 | `/vehicle/set_job` · `SetJob` | 새 작업 ID, 목표 거리(m), 주행 속도(m/s), 기준 반경(m), 이격 거리(m)를 입력한다. 음수 거리는 후진이다. |
| RUN / PAUSE / STOP | `/vehicle/robot_control` · `RobotControl.state` | 각각 `RUNNING`, `PAUSE`, `STOP`을 요청한다. |
| FINISH | `/vehicle/robot_control` · `state="FINISH"` | 자동 모드 정지 및 리프트 유휴 시 DM103=32를 요청한다. PLC에서 HOLD를 해제하고 DM002=32를 보고하면 차량 상태가 FINISH로 표시된다. |
| 작업 취소 / RESET | `/vehicle/robot_control` · `job_cancel=1` / `reset=1` | 취소는 등록 작업을 폐기한다. RESET은 컨트롤러가 정지 조건과 고장 원인 해소 여부를 확인한다. |
| 전진 / 후진 / 좌회전 / 우회전 | `/vehicle/manual_command` · `ManualCommand` | 클릭당 한 번 요청한다. 수동 주행 명령의 기본 유효 시간은 0.4초다. |
| 아웃트리거 고정 / 해제 | `/vehicle/manual_command` · `cmd_outrg_set=2` / `1` | 정지 상태에서 요청하고 PLC 수신 탭의 아웃트리거 값을 확인한다. |
| 리프트 이동 | `/vehicle/lift_control` · `SetMvLift` | 작업 ID, 목표 높이(m), 리프트 속도(m/s)를 입력한다. 컨트롤러는 아웃트리거 고정과 PLC HOLD 상태을 검사한다. |

**자동 주행 순서**

1. PLC 수신 탭에서 자동 모드(`DM001=2`)와 차량 상태를 확인한다. 작업은 STOP, HOLD 또는 FINISH에서 등록할 수 있다. FINISH에서 새 작업 등록 및 작업 취소는 STOP 전이를 요청한다.
2. 조작 탭에서 새 작업 ID와 주행 값을 입력하고 **작업 등록**을 누른다. 로그에서 등록 수락을 확인한다.
3. PLC가 STOP 또는 PAUSE이고 아웃트리거가 해제된 상태에서 **RUN**을 누른다. 이후 차량 상태, 이동 거리, 실제 속도를 확인한다.

## 3. PLC 수신 데이터 탭

각 항목은 읽기 전용 입력칸에 최신 값 하나를 표시한다. 화면의 `0`은 수신한 값이고 `—`는 표시 가능한 값이 없다는 뜻이다.

| 표시 항목 | ROS 메시지 필드 |
| --- | --- |
| TCP 연결 성공 횟수, PLC LIFE | `connection_generation`, `life` |
| 운전 모드(DM001), PLC 상태(DM002), PLC 오류 코드 | `mode`, `state`, `error_code` |
| 비상정지, 아웃트리거 고정, 자동 운전 준비 | `estop`, `outrig`, `auto_ready` |
| 장애물 정지·감속 | `obstacle_stop`, `obstacle_slow` |
| 배터리 잔량(%), 충전 중 | `battery_soc_percent`, `charging` |
| 리프트 높이, 센서 1·2 거리(모두 m) | `lift_h`, `sen1_dist`, `sen2_dist` |

리프트·거리 값은 `/vehicle/robot_status` (`vehicle_interfaces/msg/RobotStatus`)에서 가져온다. 나머지는 `/diagnostics` (`diagnostic_msgs/msg/DiagnosticArray`) 중 `hardware_id=steer_agv_plc`인 항목에서 가져온다. TCP 연결 성공 횟수는 PC에서 집계한 값이다.

차량 상태가 1초 넘게 오지 않으면 리프트·거리 칸을 비운다. PLC 진단이 1초 넘게 오지 않으면 PLC 탭 전체를 비운다. PLC 오류 코드 칸은 PLC가 보고한 값이고, 상단 차량 오류에는 컨트롤러가 감지한 통신 오류(701)도 포함된다.

## 4. 진단과 명령 결과 읽기

**PLC 전체 DM** 탭은 컨트롤러의 `/vehicle/plc_dm` 토픽을 구독한다. 내장 화면과 독립 조작 화면에서 동일하게 사용할 수 있다.

- **PLC → PC · 수신**에는 DM000~099가 표시된다. 실제로 폴링하는 DM000~039와 DM050~099의 응답을 UINT16 원시 워드로 보여준다. FLOAT32 신호는 L/H 두 행을 함께 읽는다. DM040~049는 현재 폴링 대상 밖이므로 `—`로 남는다.
- **PC → PLC · 송신**에는 DM100~127 주소별 마지막 전송 워드와 쓰기 응답이 표시된다. `OK`는 PLC 쓰기 수락이며, 응답 대기 중 연결이 끊기면 `응답 미확인`으로 표시한다. 연결 종료 후에도 직전 송신 결과를 확인할 수 있고, 재연결 시 이전 연결의 값은 초기화한다.
- 거래가 없는 주소는 `—`, 실제 영점은 `0`이다. 시간순 프레임 이력은 기존 내장 **PLC 송수신 로그**에서 확인한다.

| 화면 표시 | 해석과 확인 방법 |
| --- | --- |
| `PLC polling disabled` | 컨트롤러의 PLC 읽기가 꺼져 있다. 컨트롤러 실행 인자 `plc_enabled`를 확인한다. |
| `timed out`, TCP 연결 성공 횟수 0 | 해당 PLC 클라이언트가 아직 TCP 연결에 성공하지 못했다. PLC 주소·포트와 네트워크 경로를 확인한다. |
| TCP 연결 성공 횟수 1 이상 | 이전 또는 현재 연결 성공 이력이 있다. 현재 읽기 상태는 진단 메시지와 LIFE 변화를 함께 보고 판단한다. |
| `PLC rejected write to DM…` | 명시된 DM 쓰기에 PLC가 OK 이외의 응답을 반환했다. 단순 시간 초과만으로는 실패한 명령 주소를 알 수 없다. |
| `RUN · 거부 · no resumable automatic drive job` | 미등록 또는 완료된 작업, 진행 중인 리프트 작업, 자동 모드 해제 중 하나에 해당한다. 작업 등록 응답과 DM001을 확인한다. |
| 요청 전송 / 수락 / 거부 | 각각 ROS 서비스 요청 전송, 컨트롤러 접수, 컨트롤러 거부를 뜻한다. 실제 동작은 PLC 상태와 차량 피드백에서 확인한다. |
| 응답 시간 초과 | UI가 5초 안에 서비스 결과를 받지 못했다. 명령 실행 여부는 미확인이므로 재요청 전 차량 상태를 확인한다. |

## 5. 내장 디버깅 탭

- **컨트롤러 인자**에는 노드가 적용한 PLC 주소·포트, 읽기·쓰기 설정, 타임아웃과 주행 제어 파라미터가 표시된다. 동적 파라미터 변경도 반영된다.
- **RCS 토픽·서비스 로그**에는 차량 서비스 6종의 실제 요청과 응답이 호출자와 관계없이 기록된다. 현재 시나리오의 RCS 명령은 서비스로 들어온다. 별도 RCS 토픽을 관찰하려면 실행 인자 `rcs_log_topics:="/rcs/input_a /rcs/input_b"`에 실제 토픽의 절대 이름을 공백으로 구분해 넣는다. 해당 메시지 타입이 로컬에 설치되어 있어야 한다.
- **PLC 송수신 로그**의 TX는 소켓 송신 완료, RX는 수신 프레임이다. RDS·WR 요청, 워드 응답, 쓰기의 OK·오류 응답, 연결 성공·통신 예외를 시간순으로 확인한다.
- 각 로그 창은 최근 2,000개 텍스트 블록을 메모리에 표시한다. UI 연결을 끈 동안에는 각 채널에 최근 2,000개 이벤트까지 대기한다. 창을 닫아도 차량 제어는 계속되며, 실행 터미널의 Ctrl+C로 컨트롤러를 종료한다.

### 날짜별 TXT 로그

컨트롤러는 UI 연결 여부와 관계없이 `debug_log_dir`에 로그를 저장한다. 기본 경로는 프로젝트의 `debug/` 폴더다. 해당 종류의 이벤트가 처음 발생하면 PC 현지 날짜로 파일을 만들고, 같은 날 재시작하면 이어 쓴다.

| 파일 | 기록 내용 |
| --- | --- |
| `rcs_YYYY-MM-DD.txt` | 서비스 요청·응답과 `rcs_log_topics`에 지정한 토픽 수신 |
| `plc_YYYY-MM-DD.txt` | PLC 요청·응답 프레임, 연결 성공 및 통신 예외 |
| `drive_fault_YYYY-MM-DD.txt` | 주행 오류 탭에 표시하는 103 발생 사유와 당시 작업·속도·거리 피드백 |

컨트롤러는 시작 시점과 실행 중 날짜가 바뀔 때 위 세 종류의 TXT를 정리한다. 삭제일은 파일 날짜의 다음 달 같은 일자이며, 그 일자가 없으면 다음 달 말일이다. 예를 들어 1월 31일 로그는 평년에 2월 28일에 만료된다.

## 6. 구현 위치

### 테스트 PLC로 DM 값 확인하기

테스트 PLC는 로컬 TCP 서버와 DM 편집기를 제공하는 별도 노드다. 같은 PC의 두 터미널에서 각각 워크스페이스 환경을 불러오고 동일한 테스트용 ROS 도메인을 선택한다.

```bash
source /home/rnd08/ros2_ws/install/setup.bash
export ROS_DOMAIN_ID=77
```

첫 터미널에서 `ros2 run steer_agv_test_plc test_plc_ui`를 실행한다. 두 번째 터미널에서는 아래 명령으로 컨트롤러를 로컬 테스트 서버에 연결한다.

```bash
ros2 launch steer_agv_bringup controller.launch.py debug_ui:=true plc_enabled:=true plc_writes_enabled:=true plc_host:=127.0.0.1 plc_port:=8501
```

DM 입력칸에 0~65535를 넣고 Enter를 누르면 다음 읽기 요청부터 반영된다. 예를 들어 DM060에 750을 입력하면 조작 패널에 배터리 잔량 75%가 표시된다. DM000 자동 증가를 끄면 PLC LIFE 정지 상황을 시험할 수 있다. DM100~127의 현재 워드 열에는 컨트롤러가 쓴 값도 반영된다.

**자동 주행 모의**는 기본으로 켜진다. 자동 모드에서 작업 등록 후 **RUN**을 누른다(RCS 서비스 문자열은 `RUNNING`). 테스트 PLC가 DM103=1을 받으면 DM002=1로 응답하고, DM106의 부호 있는 속도(mm/s)를 경과 시간에 따라 적분해 네 바퀴의 누적거리와 속도를 갱신한다. 컨트롤러 화면의 이동 거리 `mv_dist`에서 결과를 확인한다. 목표 도달 후 HOLD에서는 DM025가 고정 상태가 된다. 개별 휠 조향각과 원호 궤적까지 시험하려면 모의를 끄고 원하는 피드백을 직접 입력한다.

### 소스 파일

- [Panel 클래스](steer_agv_controller_py/steer_agv_controller_py/operator_ui.py) — 조작, 수신 데이터, 파라미터와 통신 로그 표시. Qt 타이머가 20 ms마다 화면을 갱신한다.
- [컨트롤러 상태 발행](steer_agv_controller_py/steer_agv_controller_py/status_publisher.py) — PLC 진단과 차량 상태 메시지의 필드 및 단위.
- [작업 처리](steer_agv_controller_py/steer_agv_controller_py/job_manager.py) — 작업 등록·재개와 수동·리프트 동작 허용 조건.
- [컨트롤러 설정](steer_agv_bringup/config/controller.yaml) — PLC 주소·포트와 통신·수동 명령 주기 설정.
