# vehicle_interfaces — 차량(AMR·리프트·아웃트리거) 제어 인터페이스

차량 제어 담당자가 준 정의를 ROS 2 인터페이스 패키지로 옮긴 것이다.

| 종류 | 이름 | RCS 가 쓰는 곳 |
|---|---|---|
| msg | `RobotStatus` (+ `Position6D`) | 상태 구독 → 수동 제어 화면, 이동 도착 판정, 상단 AMR 표시 |
| srv | `RobotControl` | 출발 `RUNNING`, 일시정지 `PAUSED`, 정지 `STOP`, 리셋, 작업 취소 |
| srv | `SetJob` | 자동 이동 한 번 설정 |
| srv | `ManualCommand` | 수동 조그·아웃트리거·리프트, 자동 순서의 아웃트리거 고정·리프트 이동 |

토픽·서비스 이름(가정): `vehicle/robot_status`, `vehicle/robot_control`, `vehicle/set_job`,
`vehicle/manual_command`. 앞의 `vehicle` 은 RCS 환경변수 `SMR_VEHICLE_NS`, 모의기
파라미터 `namespace` 로 바꾼다.

## 받은 자료에서 바꾼 것

- `RobotControl.srv` 응답에 `bool success` 가 세 번 있었다 — 같은 이름은 쓸 수 없어 하나로 뒀다.
- `Position6D` 정의가 빠져 있어 `x y z roll pitch yaw` (float64) 로 뒀다.
- `ManualCommand.cmd_trl_right` 는 이름을 그대로 뒀다(`cmd_trn_right` 의 오타일 수 있다).
  필드 이름이 차량 쪽 패키지와 **글자까지 같아야** 통신이 된다.

## 가정한 동작 규칙 (차량 담당자 확인 필요)

RCS(`operator-ui/.../services/vehicle_adapters.py`)와 모의기(`vehicle_sim`)가 같은 규칙을 쓴다.

1. `SetJob.set_dist` = **이번 이동**에서 갈 거리 [m] (부호 = 방향). `RobotControl("RUNNING")` 으로
   출발하고 다 가면 `state = "STOP"`, `mv_dist ≈ set_dist`. 아웃트리거가 고정돼 있으면 차량이 먼저 푼다.
   `offset_dist` 는 0, `offset_height` 는 0, `total_distance/total_height` 는 작업 전체 크기 [m].
2. 아웃트리거 고정/해제 = `ManualCommand.cmd_outrg_set` 2 / 1 → `hold = "SET" / "RELEASE"`.
3. 리프트 = `ManualCommand.cmd_mv_lift = 1`, `lift_height` = 목표 높이 [m]. 아웃트리거 고정에서만 움직인다.
4. `ManualCommand` 는 `RUNNING·PAUSED·ERROR` 가 아닐 때 받는다.
5. 조그는 누르는 동안 200 ms 마다 다시 보내고, 떼면 모든 필드 0/false 를 보낸다.

**확인할 것**: 토픽·서비스 실제 이름과 패키지 이름 / `set_dist` 가 이번 이동 거리인지
누적 목표인지 / `offset_dist·offset_height·total_*` 의 뜻 / 자동 작업 중 리프트 명령 경로
(`ManualCommand` 가 수동 모드 전용이면 자동은 어떻게?) / 출발 전 아웃트리거 해제를 차량이
하는지 / `RobotControl.state` 에 들어갈 수 있는 값 / 조그의 명령 유지 방식(주기 재전송?).

## 실행

```bash
colcon build --packages-select vehicle_interfaces vehicle_sim
source install/setup.bash
ros2 launch vehicle_sim vehicle_sim.launch.py
```

RCS: 연결 설정 → **차량 제어 = ROS 차량 노드** → 저장. 상태가 들어오면 상단 AMR 이 초록이 되고
수동 제어 화면의 버튼이 열린다. 모의기 오류 시험: `ros2 param set /vehicle_sim fault_code 21`.
