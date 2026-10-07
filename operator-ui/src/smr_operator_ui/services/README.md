# services

이 폴더는 화면에서 사용하는 **검사 동작과 데이터 저장 기능**을 제공합니다.

화면 위젯은 사용자 입력과 결과 표시를 담당하고, 시간이 걸리는 처리나 외부 시스템 연동은 이 폴더의 서비스 계층에서 담당합니다. 이를 통해 UI 코드와 업무 로직을 분리하고 화면 멈춤을 방지합니다.

## 주요 파일

### `simulator.py`

- 실제 검사 장비 없이 UI 동작을 시험할 수 있는 검사 시뮬레이터입니다.
- `InspectionSimulator`가 검사 시작, 일시 정지, 재개, 정지 동작을 처리합니다.
- `QTimer`로 검사 단계를 순서대로 진행합니다.
- 상태가 변경되면 `snapshot_changed`, 작업 메시지가 발생하면 `activity` 시그널을 전송합니다.

### `settings_service.py`

- 설정 화면과 PostgreSQL 저장소 사이를 연결하는 Qt 서비스입니다.
- 데이터 조회와 저장을 `QThreadPool`에서 비동기로 실행하여 UI가 멈추지 않도록 합니다.
- 작업 결과를 `loaded`, `saved`, `failed` 시그널로 화면에 전달합니다.
- 작업 중인 워커를 보관하여 실행 도중 제거되지 않도록 관리합니다.

### `settings_repository.py`

- 운영 설정값을 PostgreSQL에 저장하고 불러오는 저장소입니다.
- `SMR_DATABASE_URL` 환경 변수에서 데이터베이스 연결 정보를 읽습니다.
- `operator_settings` 테이블이 없으면 자동으로 생성합니다.
- 설정 범위(`scope`)와 항목 키(`key`)를 기준으로 JSONB 값을 조회하거나 갱신합니다.

### 사내 MC MQTT (`mqtt_server.py`, 2026-10-07 삭제)

- 사내 MC 규격(`job_cmd`·`mc_cmd`·`job_clear`·`probe_ack`·`mark_cmd`, `docs/mqtt_topic_form.md`)을 다루던 모듈입니다. 외부 통신을 ERUT 브로커 하나로 줄이면서 뺐습니다.
- MC `probe_ack` 가 하던 원점 해제는 RCS '검사 시작' 작업이면 `app.py` 가 접촉 뒤 `CONTACT_LEAD_MS`(2초) 지나 스스로 하고, ERUT 작업이면 `erut_session` 이 같은 시간 뒤에 합니다.

### `erut_client.py`

- ERUT(스테이션)와 주고받는 **전송 계층**입니다. 규격은 `mqtt_test/ERUT-3S_MQTT_인터페이스_*.xlsx`.
- RCS 의 외부 통신은 이 브로커 하나입니다. 봉투는 `{"timestamp": 숫자, "content": {...}}` 입니다. 같은 `doosan/robot/req/` 아래로 옛 사내 MC 규격(`job_cmd` 등, `MC_ACTIONS`)이 와도 남의 요청으로 보고 못 본 척합니다(501 로 답하지 않음).
- 구독: `doosan/robot/req/{9개 동작}`, `erut/status`. 발행: `erut/{장치ID}/res`·`evt/{ready,complete,progress,error,status}` — 규격(`ERUT-3S_MQTT_인터페이스_20260818.xlsx` 탭2)의 6개가 전부입니다.
- **`evt/telemetry`는 없습니다.** 한때 1초 주기 상시 상태로 넣었다가 규격에 없어 걷어냈습니다. 되살리지 않도록 `test_no_telemetry_topic_exists` 가 막고 있습니다.
- `evt/status`는 retained + LWT이고, **접속 시 + 30~60초 주기로 다시 발행**합니다(`STATUS_PERIOD_MS`). LWT는 1회성이라 프로그램이 굳은 경우를 못 잡는데, 상대가 이 갱신이 끊기는 것으로 굳음을 판정합니다.
- 규격의 query 응답에 `speed` 필드가 없어, 로봇 속도비율을 ERUT로 넘기던 배선(`set_speed_scale`)도 함께 걷어냈습니다.
- **online 통보는 접속이 붙은 뒤에 나갑니다.** `connect_async`가 비동기라 `start()` 시점에 발행하면 그냥 버려지고, 브로커에는 지난번 LWT의 `offline`이 남습니다.

### `erut_session.py`

- ERUT 표준 인터페이스 **if-0.8** 의 검사로봇 쪽 **프로토콜 계층**입니다. 표준 동작 9개(`calibrate`/`prepare`/`start`/`pause`/`resume`/`abort`/`reset`/`mark`/`query`)에 3S 확장 `home`·`mark_next` 를 더했고, 모르는 동작은 501 로 답합니다.
- 상시 발행: `evt/status`(state 는 online/offline 만, 활동은 `activity` 8값 — 5초 + 바뀌면 바로), `evt/contact`(탐촉자 접촉 — ERUT 가 물을 켠다, 2초), `evt/info`(자기소개, 접속 시), `evt/progress`(2초, 정수·뒤로 안 감).
- `evt/home`(if-0.5): 이동 안전 자세 home / deployed. 홈 플래그(276)로 정하고 모르면 deployed. 붙을 때 + 바뀔 때만 냅니다. ERUT 는 이동 명령(calibrate·prepare·다음 구역 start) 직전에 query 의 `home` 으로 판단하므로, 캘리브레이션 뒤·이동 전에는 RCS 가 로봇을 홈으로 거둡니다.
- 일시정지(if-0.6): `activity=paused` 는 장비가 **실제로 선 뒤에만** 냅니다 — 로봇 태스크가 실행 중이 아니고(pause 로 2) TCP 가 0.5초 가만히 있고, 차량·리프트·아웃트리거가 멈춘 뒤. 그 전까지는 하던 활동(running·calibrating 등) 그대로이고 resume 은 409 입니다. 선 자리를 잡아 두었다가 팔(2 mm)·차량·리프트(5 mm)가 옮겨졌거나, 스캔 중 멈췄는데 태스크가 일시정지(2)가 아니면(pause 거절 뒤 stop·펜던트 stop) `resumable=false` 이고 resume 은 **412 NOT_RESUMABLE** 입니다 — ERUT 는 abort → query → home → start 로 처음부터 다시 합니다. 멈춘 채 abort 를 받으면 작업을 버리고 idle → 홈으로 거둡니다. 초기화(reset)로 장애가 풀릴 때도, 멈춰 둔 작업이 없고 팔이 펴져 있으면 스스로 홈으로 거둡니다(쉬는 동안 home).
- 받는 상태는 탭9(초안)대로: idle·ready 에서만 새 일을 받고, 다른 일 중이면 409, 장애 중이면 423. pause·abort·reset·query 는 늘 받는다.
- 장애 해제는 발생 때와 같은 code 에 `cleared=true` 입니다. stop·estop 장애는 하던 일을 5xx 완료로 끝냅니다(비상정지 뒤에는 이어 가지 않는다 — if-0.4).
- 진행률·완료에는 표준 `pos{x,y}` 와 3S 확장 `location{cell, vehicle_mm, lift_mm, robot}` 을 싣습니다 — 앞 구역 완료를 못 받은 채 다음 구역이 와도 어느 구역 값인지 가를 수 있게.
- 아직 비어 있는 것: 배터리·충전과 캘리브레이션 `total_length_mm`(둘 다 차량 개발자에게 받기로 했다 — `docs/vehicle_request_calibration_lap_battery.md`). 받기 전에는 칸을 싣지 않습니다(규격: 없으면 빼라, 0 으로 채우지 말라).
- 마킹은 마커가 ERUT 것이라 `mark_positioning` 입니다 — 점에 붙으면 `evt/mark_ready`, ERUT 가 `req/mark_next` 를 보내면 다음 점.
- `calibrate` 는 **차량(AGV) 캘리브레이션**입니다(ERUT 확인 2026-10-02). 모재 지름을 받아 작업 영역 반지름(지름/2)을 잡고, 로봇은 홈에 둔 채 차량이 외벽을 따라 한 바퀴 돌아 좌표계를 잡습니다. 달린 거리가 `total_length_mm`, 계획 둘레(π·지름)와의 차이가 `calibration_error_mm` 입니다.
- 순서: `prepare` = 차량·리프트만 구간 자리 → `evt/ready`(로봇은 홈). `start` = 로봇 3점 측정 → 시작점에 붙어 대기 → `evt/contact`(attached) → `CONTACT_LEAD_MS`(2초) 뒤 스캔 허가 → 적심 → 스캔. prepare 없이 온 start 는 차량 이동부터 한 번에 갑니다.
- `plan.cell_width`/`cell_height`가 없으면 `area`의 start·end 차이로 셀 크기를 잡습니다. 두 값은 같은 뜻이라 어느 쪽이 와도 되게 했습니다.
- 브로커에 남아 있던(retain) 요청은 실행하지 않고 버립니다 — 붙을 때마다 다시 와서 캘리브레이션·구간이 또 돈다(규격 if-0.6 탭6: 요청은 retain 하지 않는다).
- **재전송이면 다시 실행하지 않고 이전 응답만 되돌립니다**(규격 탭2 중요사항). 재전송으로 보는 것은 같은 동작·같은 `req_id`·**같은 내용**이고, 앞 요청을 **받아들였고**(거절은 안 남김), 받은 지 30초 안이거나 그 일이 아직 돌 때뿐입니다. 작업 정지 뒤 같은 `req_id` 로 다시 보낸 calibrate·prepare, 값이 바뀐 요청, 428 을 받았던 요청을 조건이 풀린 뒤 다시 보낸 것은 새 요청으로 처리합니다(실기 2026-10-02).
- ERUT가 `offline`이 되면 헛검사를 막으려 진행 중 작업을 일시정지합니다.

### `job_sequencer.py`

- `1A`부터 `12F`까지 격자(셀)를 하나씩 자동으로 순회하는 상태 머신입니다. **지금 어느 셀인지에 대한 유일한 소유자**입니다.
- 열(1~12)은 AMR이 정차하는 원주 구역, 행(A~F)은 리프트 높이입니다. 셀 하나가 Cobot이 ㄹ자로 훑는 영역입니다.
- **리프트가 올라가면 로봇 기준 좌표계도 함께 올라가므로 로봇 입장에서는 모든 셀이 같은 동작입니다.** 그래서 작업 영역(레지스터 256~259)은 작업 시작 때 한 번만 쓰고, 셀마다 하는 일은 ① 리프트를 한 칸 올리거나 AMR을 옮기고 ② 제로점에서 로봇을 play하는 것 두 가지뿐입니다.
- 셀 완료는 로봇 태스크가 쓰는 레지스터로 판정합니다(`290 == 5 && 295 == 1`). 완료 상태는 다음 셀을 시작할 때까지 계속 들어오고 play 직후에도 직전 셀의 완료 상태가 남아 있으므로, **로봇이 "완료 아님"을 한 번 거친 뒤 다시 완료가 되는 변화**를 잡습니다. 시간이 아니라 상태 변화로 판정해 로봇이 늦게 출발해도 안전합니다.
- 동작은 전부 시그널로 넘기고 직접 하지 않습니다. `app.py`의 `_connect_sequencer()`가 로봇·리프트·AMR·화면·MQTT에 잇습니다.
- **셀마다 로봇을 다시 시작할 때는 `remote_control_on` → `stop` → (약 1.2초) → `play` 순서를 지켜야 합니다.** 원격 제어 모드가 아니면 컨트롤러가 `play`/`stop`을 모두 거부하고("not supported in local control mode"), 이미 RUNNING 인 태스크에 `play` 만 보내면 `Failed to execute: play` 로 거부됩니다. 게다가 `dus_init` 은 태스크가 시작될 때만 작업 영역(256~259)을 읽으므로 멈췄다 켜야 새 셀 치수가 반영됩니다.
- **`play` 응답 문자열로 성공을 판단하지 않습니다.** 이미 돌고 있어 거부되었을 뿐 로봇은 정상인 경우가 있어, 실제 진행은 `scan_state`(290~298)와 `task_state`(500)로만 봅니다.
- 설계 배경은 `docs/grid_sequencer_design.md`를 참고하세요.

### `motion_adapters.py`

- `DummyMotionAdapter`가 리프트와 AMR 이동을 흉내 냅니다. 이동 요청을 받으면 잠시 뒤 `arrived`를 돌려줍니다.
- 실제 PLC와 차량이 아직 없어 더미로 둡니다. `JobSequencer`와 시그널로만 이어져 있으므로, **실제 경로가 생기면 시퀀서를 고치지 않고 이 파일만 갈아끼우면 됩니다.**

### `ros_status_client.py`

- `elite_robot_controller`의 `robot_control_node`가 발행하는 상태 토픽을 구독합니다.

| Topic | 시그널 | 내용 |
| --- | --- | --- |
| `robot/status/tcp_pose` | `tcp_pose_changed(list)` | 현재 TCP (기본 프레임, Modbus 384~389) |
| `robot/status/tcp_pose_zero` | `tcp_pose_zero_changed(list)` | 원점 기준 상대 pose |
| `robot/status/robot_mode` | `robot_mode_changed(int, str)` | 로봇 모드 |
| `robot/status/control_method` | `control_method_changed(int, str)` | 제어 방식 |
| `robot/status/operation_mode` | `operation_mode_changed(int, str)` | 운전 모드 |
| `robot/status/alarms` | `alarm_received(str)` | 알람 문구 |
| `robot/status/connected` | `connected_changed(bool)` | 세 채널 연결 여부 |
| `robot/status/scan_state` | `scan_state_changed(list)` | 로봇 태스크의 스캔 진행 상태 (Modbus 290~298) |
| `robot/status/task_state` | - | 태스크 상태 (Modbus 500). 노드가 발행하며 모니터링용 |
| `robot/status/speed_scale` | `speed_scale_changed(int)` | 로봇 동작 속도 비율 % (Modbus 17) |

- `scan_state`는 환산 없이 정수 그대로 옵니다. 순서는 `state, row_idx, rows, alive, zero_ok, finished, pitch, ...` 이며 `JobSequencer`가 셀 완료(`state == 5 && finished == 1`)를 판정하는 데 씁니다. 길이는 검사하지 않습니다 — 뒤쪽 항목(진행률 등)은 아직 쓰지 않으므로 레지스터 개수가 달라져도 순회는 계속 동작합니다.
- 자세 값은 `[X, Y, Z, Rx, Ry, Rz]` 순서이며 위치는 mm, 회전은 mrad 단위입니다. 성분이 6개가 아니면 화면에 일부 값만 반영되지 않도록 버리고 `error_occurred`를 보냅니다.
- 상태 토픽은 레지스터 원값과 표시 문구를 함께 전달합니다. 화면이 값을 다시 해석하지 않아도 되고, 정의에 없는 값도 `알 수 없음 (원값)`으로 표시해 버리지 않습니다.
- 표시 문구는 `ROBOT_MODE_NAMES`, `CONTROL_METHOD_NAMES`, `OPERATION_MODE_NAMES`에 있습니다. 값 구분은 `ws_elt`의 `robot_gui_dashboard`를 따르되, 콘솔 폭이 1280 px로 고정되어 있어 상태 행에서 잘리지 않도록 문구를 줄였습니다.
- **`robot/status/connected` 는 상태가 바뀔 때만 시그널로 내보냅니다.** 노드는 늦게 구독한 쪽도 값을 받도록 이 토픽을 10 Hz 로 계속 발행하는데, 그대로 흘려보내면 `_restore_robot_settings()` 같은 구독자가 초당 열 번 저장값을 다시 밀어 넣습니다. 운영자가 속도 바로 바꾼 값이 100 ms 안에 저장값(100 %)으로 덮어써지던 원인이었습니다.
- 구독 콜백은 ROS 실행기 스레드에서 실행되므로 값은 Qt 시그널로만 전달합니다.
- `COMMAND_SERVICES`는 명령 키를 (서비스 이름, 필요한 쓰기 레지스터)로 잇습니다. 레지스터가 `None`이면 Modbus를 쓰지 않는 명령이라 주소 확인 없이 바로 보냅니다. 연결·전원·브레이크·프로그램 제어는 29999 소켓으로 나가므로 여기에 해당합니다.
- 로봇에 값을 보낼 때도 이 서비스를 씁니다. 값이 있는 명령은 `send_value()`로 토픽에 실어 보내고, 값이 없는 한 번짜리 명령은 `call_command()`로 서비스를 호출합니다. 응답은 기다리지 않고 `command_result` 시그널로 전달해 GUI 스레드를 막지 않습니다.
- **UI는 Modbus에 직접 연결하지 않습니다.** 쓰기는 모두 `robot_control_node`를 거칩니다. 연결이 한 곳이라 제어 경로가 갈라지지 않습니다.
- 레지스터 주소는 ROS 패키지의 `config/modbus_registers.json`을 그대로 읽습니다(`writable()`). 주소가 없으면 전송을 막고 화면은 버튼을 잠급니다. UI에 주소를 복제하지 않기 위한 구조이며, ROS 워크스페이스를 소싱하지 않으면 모든 명령이 비활성화됩니다.
- **rclpy는 선택 의존성입니다.** ROS가 없는 환경에서는 `available`이 `False`가 되고 화면은 그대로 동작합니다. Windows 배포본에서 ROS 없이 실행할 수 있어야 하므로 이 구조를 유지합니다.
- `rclpy.init()`을 이 서비스가 호출한 경우에만 `rclpy.shutdown()`을 수행합니다.

### `calibration.py`

- 3점 접촉으로 **실제 벽 반지름**을 재고 입력값과의 차이를 냅니다. ERUT 캘리브레이션이 차량 한 바퀴로 바뀐 뒤로는(2026-10-02) 구간 검사(start)에서 로봇이 시작점에 섰을 때 알림에만 남깁니다.
- 접촉 자세(레지스터 330~355)는 툴 **플랜지** 기준이라, 프로브 중심 오프셋(347~349, 로봇 태스크가 `eoat_ofs` 를 내보냅니다)만큼 옮긴 뒤 원을 맞춥니다. 오프셋을 무시하면 프로브 길이만큼 통째로 틀린 반지름이 나옵니다.
- 세 점의 높이 편차(`level_mm`)도 같이 냅니다 — 수평 보정이 붙으면 이 값이 그 입력이 됩니다.
- 접촉점이 0.1 mm 단위라 **반지름의 분해능은 1 mm 안팎**입니다. 이보다 작은 차이를 재려면 로봇 쪽에서 반지름을 계산해 내보내야 합니다.

### `reference_poses.py`

- 홈 관절값과 시작 포즈를 로봇 쪽 설정 파일(`robot_task/config/reference_poses.json`)에 저장하고 읽습니다.
- **이 파일이 원본입니다.** 같은 값을 Modbus 레지스터에도 쓰지만 레지스터는 전원을 내리면 사라집니다.
- 저장 위치는 `SMR_ROBOT_CONFIG_DIR` 환경 변수로 바꿀 수 있습니다.
- 파일 쓰기에 실패해도 화면 동작을 막지 않습니다. 로봇에는 이미 레지스터로 값이 전달된 뒤이기 때문입니다.

### `tpac_bridge/`

- 로봇 Modbus(502)를 직접 폴링해 관절/TCP 값을 읽고, TPAC 등 외부 장비가 읽어가는 Modbus TCP 서버로 다시 내보내는 계층입니다.
- `\\wsl.localhost\...\src\tpac_bridge`의 독립 실행형 `modbus_bridge.py`에서 UI와 무관한 세 파일(`robot_map.py`, `modbus_io.py`, `bridge_core.py`)을 그대로 옮겨 왔습니다. 로직은 손대지 않았고 같은 폴더 import만 패키지 상대 import로 바꿨습니다.
- **원본이 바뀌면 이 폴더도 손으로 맞춰야 합니다.** 자동 동기화는 없습니다.
- `RobotPoller`(로봇 폴링 스레드)와 `ExternalServer`(Modbus 서버)를 화면(`screens/tpac_bridge_screen.py`)이 직접 구성해 씁니다. `ros_status_client.py`가 이미 로봇을 폴링하고 있지만 그건 별개의 Modbus 접속입니다 — 이 서버는 운영 UI가 꺼지기 전까지 독립적으로 열려 있어야 하기 때문입니다.
- 화면은 `robot/status/connected`(ROS)를 구독해 로봇 읽기를 자동으로 켜고 끄며, 로봇 읽기가 실제로 붙으면 외부 제공 서버도 자동으로 켭니다 — robot_control_node가 이미 로봇에 붙어 있는데 이 화면에서 또 수동으로 연결을 누르게 하는 건 불합리하기 때문입니다.
- **포트 502(TPAC 기본값)로 서버를 열려면 리눅스에서 한 번 권한을 줘야 합니다.** 1024 미만 포트는 일반 사용자가 bind할 수 없어 `[Errno 13] Permission denied`로 실패합니다. **`python3`에 `setcap cap_net_bind_service`를 직접 걸면 안 됩니다** — ROS 2도 같은 `python3`를 쓰는데, capability가 걸린 실행 파일에서는 리눅스 동적 로더가 보안상 `LD_LIBRARY_PATH`를 무시해 버려 ROS 2가 라이브러리를 못 찾고 깨집니다(`ImportError: librcl_action.so`, 실제로 겪은 문제입니다). 대신 `authbind`로 운영 UI 프로세스 하나에만 권한을 줍니다 — 설정·실행 방법은 `operator-ui/README.md`의 "실행" 절 참고. 화면도 `[Errno 13]`을 감지하면 통신 로그에 같은 안내를 남깁니다.
- **"UR 주소 매핑"은 400~420에 걸쳐 있다** (`bridge_core.MIRROR_UR`): 400~405 TCP 위치(베이스 프레임), 410~415 TCP 속도(베이스/평면 공용), 420~425 TCP 위치(작업 평면 — 제로점 기준). 서버를 켤 때 `pose_source="pose"`로 고정해 400~405가 베이스 프레임이 되게 한다 — 기본값 `"scan"`을 그대로 두면 이 블록이 제로점 기준 좌표로 채워져 실제 로봇 레지스터(384~389)와 어긋난다.
- **420~425는 매뉴얼상 "TCP offset(툴 프레임)"이지만 로봇이 채워 주지 않아 항상 0이다** (`RobotData.tcp_off`). 이 자리를 대신 작업 평면(제로점 기준 스캔 좌표, 로봇 레지스터 280~285)으로 쓴다 — `screens/tpac_bridge_screen.py`의 `_write_plane_block()`이 매 폴링마다 `data.tcp_scan`을 직접 써 넣는다(bridge_core 는 이 좌표계를 모르는 우리 전용 확장). 기본 주소 420(화면에서 바꿀 수 있다).
- **속도(410~415)는 베이스/평면 공용이다.** 평면 좌표는 베이스 좌표를 제로점만큼 평행이동한 것뿐이라(회전 없음) 속도(위치 변화율)가 두 좌표계에서 같다 — 따로 계산해 넣지 않는다.
- **TCP 속도는 로봇이 레지스터 400~405로 직접 준다** (`robot_map.REG_TCP_SPEED`, mm/s·mRad/s로 이미 실제 단위). 원래 bridge_core는 좌표 변화량으로 속도를 추정했는데(`ExternalServer._calc_speed`, 폴링 주기에 따라 흔들림), `screens/tpac_bridge_screen.py`의 `_write_real_speed()`가 `server.update()` 직후 이 실제값으로 410~415를 덮어쓴다.
- 화면의 "읽은 값" 표는 TCP 위치·속도·스캔 위치만 보여준다. 관절값·로봇 상태 코드는 여전히 읽고(외부 UR 배치에 필요) 있지만 표에는 안 보여준다 — 확인할 게 많으면 정작 봐야 할 값이 묻힌다.
- 통신 로그는 같은 내용(TPAC이 반복해서 읽어가는 요청 등)을 새 줄로 계속 쌓지 않고, 이미 있는 줄을 맨 아래로 옮기기만 한다(`_append_log`) — 안 그러면 무한히 길어져서 새로운 내용(연결 끊김, 오류)이 파묻힌다.

### `__init__.py`

- 다른 모듈에서 공통으로 사용할 서비스 클래스를 공개합니다.
- 현재 `InspectionSimulator`, `SettingsService`, `ErutClient`, `ErutSession`, `RosStatusClient`, `JobSequencer`, `DummyMotionAdapter` 관련 클래스를 패키지 외부에 제공합니다.

## 사용 규칙

- 화면 객체를 서비스 내부에서 직접 생성하거나 조작하지 않습니다. 결과는 Qt 시그널로 전달합니다.
- 데이터베이스 및 장비 통신처럼 시간이 걸릴 수 있는 작업은 GUI 스레드에서 직접 실행하지 않습니다.
- 화면 표시용 문구나 스타일은 `screens` 또는 `styles`에서 관리합니다.
- 애플리케이션 상태 구조는 `state` 폴더의 모델을 사용합니다.
- 데이터베이스 SQL과 연결 처리는 저장소 클래스에 두고, 화면은 `SettingsService`만 사용합니다.
- 실제 장비 연동이 추가되면 시뮬레이터와 동일한 신호 및 상태 전달 방식을 유지해 화면 의존성을 줄입니다.
