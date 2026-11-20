# vehicle_interfaces — 차량(AMR·리프트·아웃트리거) 제어 인터페이스

차량 제어 담당자가 준 정의를 ROS 2 인터페이스 패키지로 옮긴 것이다.

| 종류 | 이름 | RCS 가 쓰는 곳 |
|---|---|---|
| msg | `RobotStatus` (+ `Position6D`) | 상태 구독 → 수동 제어 화면, 이동 도착 판정, 상단 AMR 표시 |
| srv | `RobotControl` | 출발 `RUNNING`, 일시정지 `PAUSED`, 정지 `STOP`, 리셋, 작업 취소 |
| srv | `SetJob` | 자동 이동 한 번 설정 |
| srv | `ManualCommand` | 수동 조그·아웃트리거·리프트, 자동 순서의 아웃트리거 고정·리프트 이동 |
| srv | `MeasureLap` | 캘리브레이션 주행 — 원통 `mode=LAP`(한 바퀴), 곡판 `mode=ALIGN`(QR 정렬만) |
| topic | `start_request` (std_msgs/Empty) | 차량 「작업 시작」 버튼 → RCS 계측(캘리브레이션) 시작 |
| topic | `robot_estop` (std_msgs/Bool, RCS → 차량) | 로봇 컨트롤러 비상정지 → 차량도 비상정지 (2026-11-18) |

토픽·서비스 이름(가정): `vehicle/robot_status`, `vehicle/robot_control`, `vehicle/set_job`,
`vehicle/manual_command`, `vehicle/measure_lap`, `vehicle/start_request`, `vehicle/robot_estop`. 앞의 `vehicle` 은 RCS 환경변수 `SMR_VEHICLE_NS`, 모의기
파라미터 `namespace` 로 바꾼다.

## 받은 자료에서 바꾼 것

- `RobotControl.srv` 응답에 `bool success` 가 세 번 있었다 — 같은 이름은 쓸 수 없어 하나로 뒀다.
- `Position6D` 정의가 빠져 있어 `x y z roll pitch yaw` (float64) 로 뒀다.
- `ManualCommand.cmd_trn_right`(우회전) — 받은 자료의 `cmd_trl_right` 는 오타라 고쳤다(2026-11-17).
  필드 이름이 차량 쪽 패키지와 **글자까지 같아야** 통신이 된다.

## 가정한 동작 규칙 (차량 담당자 확인 필요)

RCS(`operator-ui/.../services/vehicle_adapters.py`)와 모의기(`vehicle_sim`)가 같은 규칙을 쓴다.

1. `SetJob.set_dist` = **이번 이동**에서 갈 거리 [m] (부호 = 방향). `RobotControl("RUNNING")` 으로
   출발하고 다 가면 `state = "STOP"`, `mv_dist ≈ set_dist`. 아웃트리거가 고정돼 있으면 차량이 먼저 푼다.
   `set_speed` 는 RCS 차량 설정의 주행 속도, `standoff` 는 차량이 지킬 모재 거리 [m] 다.
2. 아웃트리거 고정/해제 = `ManualCommand.cmd_outrg_set` 2 / 1 → `hold = "SET" / "RELEASE"`.
3. 리프트 = `ManualCommand.cmd_mv_lift = 1`, `lift_height` = 목표 높이 [m]. 아웃트리거 고정에서만 움직인다.
4. `ManualCommand` 는 `RUNNING·PAUSED·ERROR` 가 아닐 때 받는다.
5. 조그는 누르는 동안 200 ms 마다 다시 보내고, 떼면 모든 필드 0/false 를 보낸다.
6. `MeasureLap` 은 캘리브레이션 주행이다(2026-11-18 `mode` 추가). 진행 · 결과는 `RobotStatus` 의
   `lap_state`(RUNNING → DONE/FAILED) · `lap_path_m` · `lap_origin_ok` · `lap_error` 로 낸다.
   - `mode = "LAP"`(빈 글자도 LAP) — 원통: 원점(QR)에서 모재를 한 바퀴 돌아 원점(QR)에 선다.
     `lap_path_m` = 차량 중심이 달린 거리, `lap_origin_ok` = QR 을 다시 찾아 섰다.
   - `mode = "ALIGN"` — 곡판: 한 바퀴 돌지 않는다. **판 가운데**의 QR 앞에 놓인 차량이 QR 마커에 맞춰
     정렬(거리 `standoff` · 방향)만 하고 선다. `lap_origin_ok` = QR 에 맞춰 섰다. `expected_dist` 는 0.
     곡판은 그 뒤 스캔 때도 차량이 움직이지 않는다 — RCS 가 `set_job` 을 보내지 않고 아웃트리거 · 리프트만
     쓴다(2026-11-18).
   - 두 경우 모두 `RobotControl` PAUSED · RUNNING 으로 멈추고 잇고, STOP 이면 `lap_state = FAILED`.
7. 비상정지(2026-11-18) — 로봇 컨트롤러 · PLC 어느 쪽 버튼이 눌려도 차량 · 로봇이 모두 선다.
   - 실제 동시 정지는 **안전 회로 직결**(PLC 비상정지 ↔ 로봇 컨트롤러 외부 비상정지 입력 · 비상정지 출력,
     2채널)이 맡는다. 아래 토픽 · 칸은 알림 · 작업 정리 · ERUT 보고용이다.
   - `RobotStatus.estop` = 차량(PLC) 비상정지 중(차량 버튼이든 로봇 쪽 요청 · 안전 회로로 건 것이든).
   - `robot_estop` = RCS 가 로봇 컨트롤러 비상정지 상태를 바뀌면 바로 + 0.5 초마다 낸다. true 면 차량도
     비상정지하고 움직이는 명령을 거절한다. false 가 되면 차량 리셋 규칙대로 푼다.

**확인할 것**: 토픽·서비스 실제 이름과 패키지 이름 / `set_dist` 가 이번 이동 거리인지
누적 목표인지 / `offset_dist·offset_height·total_*` 의 뜻 / 자동 작업 중 리프트 명령 경로
(`ManualCommand` 가 수동 모드 전용이면 자동은 어떻게?) / 출발 전 아웃트리거 해제를 차량이
하는지 / `RobotControl.state` 에 들어갈 수 있는 값 / 조그의 명령 유지 방식(주기 재전송?).

## 실행

```bash
colcon build --packages-select vehicle_interfaces vehicle_sim
source install/setup.bash
ros2 launch vehicle_sim vehicle_sim.launch.py            # 화면 있는 모의기
ros2 launch vehicle_sim vehicle_sim.launch.py ui:=false  # 화면 없이
```

모의기 화면(`vehicle_sim_ui`)은 움직임을 눈으로 보여 준다 — 바퀴 회전(주행),
아웃트리거 다리 3개, 리프트 발판, 3색 경광등(빨강 오류 / 노랑 움직이는 중 /
초록 정지·대기). 창의 버튼으로 오류를 넣고 풀고 정지시킬 수 있다.

RCS: 연결 설정 → **차량 제어 = ROS 차량 노드** → 저장. 상태가 들어오면 상단 AMR 이 초록이 되고
수동 제어 화면의 버튼이 열린다. 모의기 오류 시험: `ros2 param set /vehicle_sim fault_code 21`.
