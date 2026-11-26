# AGV 가상 차량(테스트 PLC + AGV 제어 노드) 사용법

AGV 담당이 만든 **실제 차량 제어 프로그램**과 **테스트 PLC**를 PC 한 대에서 띄워, RCS 를 실제 차량에 붙이듯이
시험하는 방법이다(2026-11-23 받음, 2026-11-24 새 판을 `src/steer_agv` 로 받음 — **고치지 않는다**).

우리 쪽 **AGV 시뮬레이터**(`vehicle_sim`)도 같은 규칙으로 맞춰 두었다. 둘의 쓰임새는 이렇게 나눈다.

| | AGV 가상 차량 (이 문서) | AGV 시뮬레이터 (`vehicle_sim`) |
|---|---|---|
| 무엇 | AGV 담당의 실제 제어 노드 + 가짜 PLC | 3S 가 만든 흉내 노드 |
| 쓰임 | 실제 차량 프로그램과 RCS 를 맞춰 볼 때 | RCS 화면 · 순서 시험, 자동 시험(pytest) |
| 주행 | 직진만(조향 없음), PLC 값을 직접 바꿔 장애 흉내 | 원주 주행 거리, 화면에 차 · 다리 · 리프트 그림 |
| 리프트 | 테스트 PLC 는 안 움직임 → **리프트 보조**를 같이 띄운다 | 움직인다 |
| 캘리브레이션 | `measure_lap` 거절(아직 QR 없음) | ALIGN 동작(정렬 뒤 STOP — RCS 가 HOLD) |

**둘을 같이 띄우지 않는다.** 둘 다 `/vehicle/*` 토픽 · 서비스를 연다(RCS 는 차량 모의기가 이미 보이면 AGV 노드를
띄우지 않는다).

---

## 1. 들어 있는 것

| 패키지 (`src/steer_agv/…`) | 하는 일 |
|---|---|
| `steer_agv_controller_py` | **AGV 제어 노드** `QUAD_STEER_AGV_CONTROLLER` — `/vehicle/*` 서비스 · 상태를 열고 PLC 와 TCP(Keyence 상위 링크)로 10 ms 마다 주고받는다 |
| `steer_agv_bringup` | 실행 파일(`controller.launch.py`)과 설정(`config/controller.yaml` — 기본 PLC 192.168.11.10:**8501**(실제 PLC 포트 — AGV 담당 실행 커맨드 `src/steer_agv/command` 도 8501). RCS 가 띄울 때 연결 설정 값으로 덮어 넘긴다) |
| `steer_agv_test_plc` | **테스트 PLC** — 127.0.0.1:8501 에서 PLC 대신 답하고, DM 값을 창에서 바꿀 수 있다 |
| `steer_agv_operator_ui` | 제어 노드의 조작 패널만 따로 띄우기(`ros2 run steer_agv_operator_ui operator_ui`) |
| `urdf` (패키지 이름 `steer_agv_description`) | 차량 모델 — RViz2 에서 이동 경로를 본다 |
| `steer_agv_marker` | ArUco 마커로 차량 위치 재기(카메라 필요, 선택) |
| `steer_agv_sim_test` | Gazebo 주행 시험(Gazebo · `four_steer_amr_plugin` 필요 — 이 PC 에는 없다) |
| `1.Documets/` | AGV 담당 문서 — 시나리오 · PLC 통신방식(DM 주소표) · 오류 코드 |

인터페이스는 `src/vehicle_interfaces` 를 같이 쓴다(AGV 판에는 사본이 없다). 빌드:
`colcon build --packages-select steer_agv_description steer_agv_bringup steer_agv_controller_py steer_agv_marker steer_agv_operator_ui steer_agv_sim_test steer_agv_test_plc`
(소스 폴더는 바뀌지 않는다). AGV 노드의 일별 PLC · 주행 기록 폴더(`debug_log_dir`)는 기본값이 AGV 개발자 PC
경로(`/home/rnd08/…`)라, RCS 는 띄울 때 `~/.ros/log/steer_agv_debug` 로 바꿔 넘긴다. 작업 끝(FINISH)은 `RobotControl.srv` 의 `string state` 로 보낸다 —
`state = "FINISH"`(2026-11-23 AGV 측과 정함, 3S 판에 넣었던 `job_finish` 칸은 지웠다).

차량 규칙의 근거는 AGV 담당 문서 **`src/steer_agv/1.Documets/시나리오`** 다(COMMAND · STATE 값, FINISH, 서비스 · 토픽 이름). RCS 와
`vehicle_sim` 은 이 문서와 제어 노드 코드(`steer_agv_controller_py`)에 맞춘다.

## 2. 빌드

```bash
cd ~/dusan_ws
source /opt/ros/humble/setup.bash
colcon build
source install/setup.bash
```

필요한 것: PyQt5(`python3-pyqt5`), `ros-humble-xacro`, `ros-humble-robot-state-publisher`. 마커 PDF 를 새로 만들 때만
`python3-reportlab` 가 더 필요하다.

## 3. 띄우기 — AGV 제어 노드는 RCS 가 띄운다 (2026-11-24)

RCS 연결 설정 「차량」 칸:

| 칸 | 값 | 뜻 |
|---|---|---|
| 차량 제어 | **ROS 차량 노드 고정** | 이때만 RCS 가 AGV 제어 노드를 띄운다(자동 · 더미면 안 띄운다) |
| 받는 노드 위치 (AGV 제어 노드) | **RCS 내부 프로그램 (이 PC)** · 외부 PC (그 PC 에서 실행) | RCS 명령(/vehicle/*)을 받는 노드가 어디인가. 내부 = RCS 의 자식 프로세스(같은 ROS 도메인, RCS 가 꺼지면 같이 꺼짐). 외부 PC = 그 PC 에서 띄운 노드에 같은 ROS 도메인으로 붙음 |
| 외부 PC 주소 · 「외부 PC 확인」 | 예: 192.168.11.13 | 외부 PC 일 때만. ping 으로 길을 보고, ROS 로 그 PC 의 노드(차량 상태)가 보이는지 함께 알린다 — 안 보이면 볼 곳(도메인 · LOCALHOST_ONLY · WSL 방화벽 · fastdds 설정)을 적어 준다. ROS 는 주소가 아니라 도메인으로 붙는다 — 이 주소는 확인용 |
| PLC 주소 · 포트 | **192.168.11.10 · 8501**(실제 PLC) / 127.0.0.1 · 8501(테스트 PLC) | 내부 프로그램이 붙을 PLC |
| 「PLC 포트 확인」 | (버튼) | 노드와 따로, 이 PC 에서 PLC 주소:포트로 **TCP 접속만** 해 보고 끊는다(아무것도 안 보냄). 열림(ms) · 닫힘(PLC 는 답했지만 포트 거절) · 응답 없음 · 경로 없음. 내부 · 외부 어느 쪽이든 쓴다 |
| PLC 명령 쓰기 | 켬 | 내부 프로그램만. 끄면 읽기만 한다 — 움직이는 명령을 모두 거절한다("PLC writes disabled") |
| AGV 조작 패널 | 끔 · 켬 | 내부 프로그램만. 켜면 AGV 담당 조작 패널 창이 같이 뜬다(5장) |
| 받는 노드 | (표시, 3 초마다) | 지금 실제로 받는 노드 — 「RCS 내부 프로그램 (pid, PLC) — 차량 상태 수신 중」 · 「외부 PC … — 수신 중」 · 「… 노드가 안 보입니다」. 고른 위치와 다르면(내부로 골랐는데 외부 PC 노드가 받음 · 외부로 골랐는데 이 PC 의 노드가 받음) 노란 글로, 같은 도메인에 차량 노드가 둘 이상이면(명령이 모두에게 감) 그것도 알린다 |
| PLC (노드 보고) | (표시) | 받는 노드가 차량 상태로 알린 PLC 연결(오류 701 이면 연결 실패) — 「PLC 포트 확인」과 별개 |
| ROS 도메인 ID | 비움 · 0~232 | **RCS 를 다시 켜야 적용**. 비우면 셸 환경(`ROS_DOMAIN_ID`) 그대로. 외부 PC 는 같은 값이어야 한다 |

저장하면 바로 적용된다(PLC 주소를 바꾸면 노드를 다시 띄운다 — 작업 중이면 멈춘 뒤). 이미 이 PC 에서
agv_controller 가 돌거나 다른 PC 의 노드 · 차량 모의기가 vehicle/robot_status 를 내고 있으면 RCS 는 하나 더
띄우지 않는다. 노드 출력은 `~/.ros/log/smr_agv_controller.log` 에 남는다.

**실제 PLC(192.168.11.10:8501)** — 이 PC(또는 RCS 리눅스 PC)에 PLC 망 주소가 있어야 닿는다(2026-11-24 이 PC 는
이더넷 192.168.11.25 — 「PLC 포트 확인」으로 8500 · 8501 열림, 8504 는 닫힘을 확인했다). PLC 에 꽂은 랜 어댑터에 192.168.11.x 주소(예: 192.168.11.100/24)를
더한다 — Windows: 제어판 › 네트워크 연결 › 이더넷 › IPv4 › 고급 › IP 주소 추가, 우분투: 설정 › 네트워크 › 유선 ›
IPv4 수동(또는 `nmcli connection modify <연결> +ipv4.addresses 192.168.11.100/24`).

**테스트 PLC로 시험** — 바탕화면 아이콘 「AGV 테스트 PLC」(테스트 PLC + 리프트 보조)를 띄우고, RCS 연결 설정을
PLC 주소 127.0.0.1 · 포트 8501 로 둔다. 터미널로 띄울 때는:

```bash
source ~/dusan_ws/install/setup.bash
ros2 run steer_agv_test_plc test_plc_ui            # 창: 「Steer AGV · 테스트 PLC」
```

```bash
python3 ~/dusan_ws/tools/steer_agv_test_plc_lift.py   # 테스트 PLC 가 뜬 뒤
```

```bash
cd ~/dusan_ws/operator-ui && authbind --deep python3 -m smr_operator_ui
```

**외부 PC 에서 붙어 시험** — RCS 안의 AGV 노드는 RCS 와 같은 ROS 도메인으로 네트워크에 열린다(ROS_LOCALHOST_ONLY 가
1 이 아니면 — 연결 설정 「ROS 도메인」 줄에 보인다). 같은 망의 다른 PC 에서 도메인만 맞추면 된다.

```bash
export ROS_DOMAIN_ID=77          # RCS 연결 설정의 ROS 도메인과 같게
ros2 topic echo /vehicle/robot_status
```

```bash
ros2 run steer_agv_operator_ui operator_ui     # AGV 담당 조작 패널만 따로
```

거꾸로 AGV 노드를 다른 PC(AGV PC)에서 띄우면 RCS 연결 설정 「AGV 제어 노드」를 **외부**로 두고 그 PC 와 RCS 의
ROS 도메인을 맞춘다. 방화벽이 DDS(UDP 7400~)를 막으면 서로 안 보인다.

**주의 — ROS 도메인이 다르면 서로 안 보인다.** 2026-11-24 현장: 테스트 PLC · AGV 노드는 `export ROS_DOMAIN_ID=77`
터미널에서, RCS 는 다른 터미널(도메인 0)에서 띄워 RCS 가 「차량 상태가 들어오지 않습니다」 → 검사 시작 뒤
「차량 서비스가 없습니다: vehicle/set_job」로 멈췄다. 지금은 AGV 노드를 RCS 가 띄우므로 그럴 일이 없고, 차량이
안 보이면 RCS 가 검사 · 계측 시작을 거절하며 RCS 의 ROS 도메인을 알려 준다.

RViz2 로 움직임을 보려면 `rviz2` → Fixed Frame `odom`, TF · Path(`/vehicle/path`) 추가(차량 모델은 AGV 담당
`ros2 launch steer_agv_bringup controller.launch.py` 의 robot_state_publisher 가 있어야 보인다 — RCS 내장 실행은
제어 노드만 띄운다).

## 4. 테스트 PLC 창 다루기

DM 칸에 값을 넣고 **Enter** 를 누르면 다음 읽기부터 반영된다(0~65535 원시 값). 자주 쓰는 주소:

| DM | 뜻 | 값 | 시험할 때 |
|---|---|---|---|
| DM001 | 운전 모드 | 0 코봇 수동 · 1 주행 수동 · **2 자동**(처음 값) | RCS 수동 제어 화면은 0 · 1 에서만, 자동 작업은 2 에서만 된다 |
| DM002 | PLC 상태 | 0 STOP · 1 RUNNING · 2 HOLD · 4 PAUSE · 8 ERROR · 16 INITIALIZE · 32 FINISH | 「자동 주행 모의」가 켜져 있으면 저절로 바뀐다 |
| DM004 | PLC 오류 코드 | 0 정상, 그 외 번호 | 넣으면 RCS 에 차량 오류(E2001) — 0 으로 되돌린 뒤 RCS 「오류 리셋」 |
| DM025 | 아웃트리거 고정 | 0 · 1 | HOLD 면 1, FINISH 면 0 이 된다 |
| DM026 · 027 | 리프트 높이(float32 mm) | — | 리프트 보조가 채운다 |
| DM030 | 비상정지 | 0 · 1 | 1 → RCS E1002 · 작업 정지 |
| DM031 | 자동 운전 준비(차량 「작업 시작」 버튼) | 0 → 1 | 올라가는 순간 RCS 계측 시작(`/vehicle/start_request`) |
| DM050 · 051 | 앞 · 뒤 장애물 | bit0(1) 정지 · bit1(2) 감속 | 1 → RCS 가 작업을 일시정지 |
| DM060 | 배터리 잔량 | 0.1 % 단위 (750 = 75 %) | 상단 배터리 |
| DM062 | 충전 중 | 0 · 1 | |
| DM000 | PLC LIFE | 자동 증가 | 「DM000 LIFE 자동 증가」를 끄면 3 초 뒤 PLC 끊김(701) |

**PLC 끊김(701) 시험**: LIFE 자동 증가를 끈다 → 3 초 뒤 RCS 상단 PLC 「연결 실패」 · 알람. 다시 켠다 →
제어 노드는 붙지만 701 은 **걸어 둔다** → RCS 연결 설정 「PLC 다시 연결」(= 리셋)을 누르면 풀린다. 아직 안 붙었을 때
누르면 "PLC 가 아직 붙지 않았습니다" 로 거절된다.

명령 탭(DM100~127)에는 제어 노드가 쓴 값이 보인다 — DM103 명령(1 RUN · 2 HOLD · 4 PAUSE · 32 FINISH), DM106 속도,
DM107 목표 거리, DM108 리프트 목표 높이, DM112 리프트 속도.

## 5. AGV 제어 노드 조작 패널

`debug_ui:=true` 로 띄우면 창이 하나 더 뜬다. RCS 없이 차량만 시험할 때 쓴다 — 작업 등록 · RUN · PAUSE · STOP ·
FINISH · 작업 취소 · RESET · 수동 조그 · 아웃트리거 · 리프트 버튼, PLC 수신 값, 전체 DM, RCS 서비스 요청 · 응답 로그.
RCS 가 무엇을 보냈고 왜 거절됐는지는 이 창의 **RCS 토픽·서비스 로그**에서 본다.

## 6. RCS 가 차량과 주고받는 순서 (AGV 제어 노드 규칙)

RCS 를 이 규칙에 맞췄다(2026-11-23). `vehicle_sim` 도 같다.

| 무엇 | RCS 가 보냄 | 차량 |
|---|---|---|
| 구역 이동 | 리프트 0 → `robot_control FINISH` → `set_job`(새 job_id) → 차량이 STOP 이 되면 `robot_control RUNNING` | 다 가면 **스스로 HOLD**(아웃트리거 고정) |
| 같은 자리에서 고정만 | 거리 0 `set_job` → `RUNNING` | 바로 HOLD |
| 리프트 | `lift_control`(목표 m, 속도 m/s) | 아웃트리거 고정 · 서 있을 때만 |
| 일시정지 · 재개 | `PAUSE` → `RUNNING` (작업 · 간 거리를 그대로 잇는다) | |
| 정지 | `STOP` + `job_cancel = 1` (작업을 버린다) | |
| 작업 끝 | `robot_control FINISH` | 홀딩 해제 · 아웃트리거 올림 · state FINISH |
| 오류 리셋 · PLC 다시 연결 | `robot_control reset = 1` | 원인이 없어졌을 때만 받는다 |
| 위치 다시 맞추기 | `reset_odometry` | STOP · HOLD 이고 끝나지 않은 주행이 없을 때만 |

- **job_id** 는 제어 노드가 켜진 동안 한 번만 받는다. RCS 는 `RCS-<켠 날 · 시각>-0001` 처럼 번호를 매긴다.
- **비상정지 토픽** `/vehicle/robot_estop` 은 차량이 PLC 비상정지를 **내는** 토픽이다. RCS 는 더 이상 거기에 내지 않는다
  (로봇 컨트롤러 비상정지와 차량이 같이 서는 것은 안전 회로가 맡는다).
- **속도 상한**: RCS 차량 설정 「차량 주행 속도」 최대 980 mm/s, 「리프트 속도」 최대 200 mm/s(제어 노드가 받는 값).

## 7. 아직 안 되는 것 · 주의

| 항목 | 내용 |
|---|---|
| 캘리브레이션(`measure_lap`) | 2026-11-24부터 calibrate = **QR 정렬(`measure_lap` mode `ALIGN`) → HOLD(아웃트리거 고정) → 결과**다. 한 바퀴는 작업자가 수동으로 돈 뒤 차량을 QR 앞에 세워 둔다(RCS 는 `LAP` 을 쓰지 않는다). HOLD 명령이 따로 없어 RCS 는 거리 0 작업(SetJob 0 m + RUNNING → 도착 HOLD)으로 고정한다. 제어 노드는 QR 피드백이 정해질 때까지 `measure_lap` 을 거절하므로 지금은 **캘리브레이션이 실패**(E1004)로 끝난다 — 예전의 「SetJob 한 바퀴」 대신은 없앴다. 한도는 RCS 차량 설정 「정렬 최대 시간」(ERUT `complete_within_ms.calibrate` 도 같은 값) |
| 리프트 상태(`lift_state`) | `RobotStatus.msg` 에 `string lift_state`(STOP · RUNNING · PAUSE · FINISH — AGV 측과 정함)를 더했다(2026-11-24). RCS 는 리프트 명령 뒤 **FINISH 를 받고 나서 높이를 비교**해 도착을 확인한다(목표 ±5 mm, 다르면 두 높이와 함께 실패 · 움직이다 STOP 이면 실패). AGV 제어 노드는 아직 이 칸을 채우지 않는다 — 빈 값이면 RCS 는 예전처럼 높이만 본다. **메시지가 바뀌었으니 외부 PC 의 AGV 쪽도 같은 `RobotStatus.msg` 로 다시 빌드해야 한다**(안 맞으면 RCS 가 차량 상태를 못 받는다). 차량 모의기는 채운다(`lift_stop_error` 로 목표 옆에 서는 것 흉내) |
| 리프트 높이 상한 | 제어 노드 `max_lift_height` 기본 **1.5 m**(읽기 전용 파라미터). 실제 리프트는 **5000 mm** 라, RCS 가 내부 프로그램을 띄울 때 차량 설정 「리프트 한계 높이」(기본 5000 mm)를 `-p max_lift_height:=5.000` 으로 넘기고, 그 값을 바꿔 저장하면 노드를 다시 띄운다(작업 중이면 멈춘 뒤). 외부 PC 에서 띄우는 노드는 그 실행 명령에 같은 값을 붙여야 한다 |
| 수동 제어 화면 | 자동 모드(DM001 = 2)에서는 "manual mode required" 로 거절. 조그는 주행 수동(1) · 아웃트리거 해제에서만. 「원점(cmd_init)」은 거절(DM105 규약 미정) |
| 수평(level_state) | 제어 노드는 늘 -1(모름)로 낸다 — RCS 는 조건에서 뺀다 |
| 테스트 PLC 주행 | 직진만 흉내 낸다(바퀴 조향각 0). 원주 궤적은 모의를 끄고 값을 직접 넣어야 한다 |
| 실제 PLC | 192.168.11.10:8501 — RCS 연결 설정 PLC 주소 · 포트(3장). 리프트 보조는 **실제 PLC 에 쓰지 않는다** |
