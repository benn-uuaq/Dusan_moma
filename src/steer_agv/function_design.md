# STEER AGV 함수 구성 설계

- 작성일: 2026-11-19
- 대상: ROS 2 Humble / Python 우선 구현, C++ 후속 구현 / `QUAD_STEER_AGV_CONTROLLER`
- 상태: 구현 전 설계. 아래 파일·클래스·함수는 구현할 구성이며 현재 존재하는 코드 목록이 아니다.
- 기준: 원본 시나리오·PLC 통신·오류 문서와 사용자 확정 사항. JERK는 100%, 수식은 별도 파일로 분리한다.
- 범위: 전체 함수 구성, 입출력, 상태 소유권, 호출 순서와 검증 경계를 기록한다. 이번 작업은 이 문서만 생성하며 `plan.md`를 수정하지 않는다.
- 언어 결정: 사용자 요청에 따라 Python으로 먼저 시작한다. 기존 plan.md의 C++ 우선 제안 대신 이 문서의 두 버전 구성을 구현 기준으로 삼는다.

## 1. 구성 원칙과 파일 배치

ROS 서비스·타이머는 `AgvControllerNode`, TCP 통신은 `PlcClient`, 레지스터 변환은 `PlcCodec`이 담당한다. `PlcClient`와 `PlcCodec` 두 클래스는 하나의 `plc_client.py` 파일에 함께 정의한다. 주행 상태와 odom 상태는 각 담당 클래스가 보관하고 수식은 별도 `agv_math.py` 함수를 import해서 호출한다. Python은 `rclpy`/`ament_python`, 후속 C++는 `rclcpp`/`ament_cmake`로 구성한다.

두 구현은 독립된 ROS 패키지 이름을 사용하고 공통 vehicle_interfaces·토픽·서비스·단위·파라미터 의미를 유지한다. 같은 PLC에 두 제어기를 동시에 연결하지 않도록 bringup에서 선택한 한 버전만 실행한다.

```text
steer_agv/
├── function_design.md
├── steer_agv_controller_py/
│   ├── package.xml
│   ├── setup.py
│   ├── setup.cfg
│   ├── resource/steer_agv_controller_py
│   ├── steer_agv_controller_py/
│   │   ├── __init__.py
│   │   ├── main.py
│   │   ├── controller_types.py
│   │   ├── math_types.py
│   │   ├── agv_controller_node.py
│   │   ├── agv_math.py
│   │   ├── plc_client.py
│   │   ├── odometry_estimator.py
│   │   ├── motion_controller.py
│   │   ├── job_manager.py
│   │   ├── safety_monitor.py
│   │   └── status_publisher.py
│   └── test/
│       ├── test_agv_math.py
│       ├── test_plc_codec.py
│       ├── test_plc_client.py
│       ├── test_job_manager.py
│       └── test_controller_flow.py
├── steer_agv_controller_cpp/    # 후속 구현
│   ├── package.xml
│   ├── CMakeLists.txt
│   ├── include/steer_agv_controller_cpp/
│   │   ├── agv_math.hpp
│   │   └── 각 클래스의 헤더
│   ├── src/
│   │   ├── agv_math.cpp
│   │   └── 각 클래스의 구현
│   └── test/
├── steer_agv_bringup/
│   ├── config/controller.yaml
│   └── launch/controller.launch.py
├── steer_agv_description/
└── validation/cases/           # 두 버전 공통 JSON 입출력
```

Python `math_types.py`에는 수식 입력·출력용 dataclass와 타입 힌트를 정의하고 `agv_math.py`에는 실제 계산 함수를 둔다. `motion_controller.py`와 `odometry_estimator.py`는 `from . import agv_math`로 불러 호출한다. 계산 모듈에서 노드나 제어 클래스를 import하지 않아 순환 의존성을 피한다.

후속 C++에서는 동일한 함수 역할을 `agv_math.hpp` 선언과 `agv_math.cpp` 구현, `steer_agv::math` 네임스페이스에 대응시킨다. CMake 계산 라이브러리를 제어 모듈과 테스트에 링크하며 `.cpp`를 직접 include하지 않는다. Python 1차 버전에서 C++ 확장 모듈을 필수로 만들지 않는다.

계산 모듈은 ROS·PLC·네트워크·로그 발행·파라미터 서버에 의존하지 않는다. 내부 계산은 m, m/s, m/s², m/s³, rad, s를 사용한다. PLC 워드와 0.01deg 정수 처리는 `PlcCodec` 경계에 둔다.

## 2. 데이터와 상태 소유권

| 타입/상태 | 주요 내용 | 소유·갱신 위치 |
| --- | --- | --- |
| `ControllerConfig` | 주기, 주소, timeout, 기하, 속도/가감속/jerk, 허용 오차 | Node가 검증 후 제어 주기 경계에서 교체 |
| `PlcSnapshot` | 고속·저속 피드백, 각각의 sequence/수신 시각/유효성, 연결 세대 | PlcClient가 완성본을 제공, 제어 측은 읽기 전용 |
| `WheelSample` | 네 휠 누적 거리·조향각(DM021~024, 0.01°/count → rad)·속도, 샘플 시각 | PlcCodec 변환 결과 |
| `OdometryState` | pose, BODY 속도, 이전 휠 샘플, 이번 샘플 이동량 | OdometryEstimator |
| `ProfileState` | 현재 지령 속도·가속도 | MotionController |
| `PathReference` | 원주 기준 방향, 반경, 회전 방향 | MotionController |
| `JobState` | job_id, 목표, 진행 거리, 실행/일시정지/취소/완료 단계 | JobManager |
| `ManualState` | 수동 스위치, 마지막 요청 수신 시각 | JobManager |
| `LiftRequest` | job_id, 높이, Auto 속도, 접수/실행 상태 | JobManager |
| `SafetyDecision` | 실행 허용, 정지 요구, 속도 상한, 원인/오류 | SafetyMonitor가 계산, Node가 최종 지령에 적용 |
| `PlcCommand` | SI 단위 속도·각도·목표값·명령·수동 출력 | Node가 구성, PlcCodec이 워드로 변환 |
| `WriteEvent` | RESET 같은 단발 요청의 로컬 식별자와 값 | Node에서 생성, PlcClient가 ACK 결과 반환 |
| 송신 캐시 | 대기값·전송 중 값·OK 확인값 | PlcClient 통신 스레드 |
| `OperationResult` | 요청 접수 성공/실패와 이유 | 서비스 처리 결과 |
| `CalcResult[T]` | Python dataclass 기반 계산 결과 또는 오류 사유. C++는 동등한 CalcResult<T> | 계산 함수 반환, 호출자가 정책 결정 |

계산 함수에 필요한 이전 속도·가속도·pose도 인자로 넘긴다. 계산 함수 내부에 숨겨진 전역 상태나 현재 시각 조회를 두지 않는다. invalid 입력을 정상 0으로 바꿔 반환하지 않는다. CalcResult는 성공 시 값, 실패 시 오류 종류·이유를 가지며 동시 성공/실패 상태는 허용하지 않는다.

## 3. 실행 구조와 10ms 순서

제어 상태의 변경 주체는 제어 루프 하나로 제한한다. ROS 서비스 콜백은 요청을 전달하고, 네트워크 스레드는 피드백과 ACK 결과를 전달한다. 공유 큐/스냅샷의 동기화 범위를 작게 유지하며 제어 루프에서 TCP 응답을 기다리지 않는다.

```text
ROS 서비스 ── 요청 큐 ──┐
                      ▼
               control_tick() 100 Hz
                      │
        ┌─────────────┼──────────────┐
        ▼             ▼              ▼
    JobManager  MotionController  OdometryEstimator
                      │              │
                      └──────┬───────┘
                             ▼
                  agv_math.py 계산 함수

control_tick() ── 지령/이벤트 ──> PlcClient 통신 스레드 ──> PLC
control_tick() <─ 스냅샷/ACK ─── PlcClient 통신 스레드 <── PLC
control_tick() ── 상태 스냅샷 ──> StatusPublisher ──> RCS/RViz2
```

`control_tick()` 처리 순서:

1. 이번 주기에 사용할 설정·단조 시각·PLC 스냅샷·ACK 결과를 고정한다.
2. 새 PLC 샘플에서 STATE 전이와 AUTO_READY 에지를 처리한다. INITIALIZE 진입 시 초기화는 한 번만 수행한다.
3. 새 휠 샘플이면 odom을 갱신한다. 최초·재접속 샘플은 기준만 설정한다.
4. 새 odom 이동량을 기존 작업에 한 번만 적산한다. 새 작업 등록 이전의 이동을 새 작업에 포함하지 않는다.
5. 통신·비상정지·장애물·수동 만료를 평가한다.
6. 대기 중 요청을 최신 상태로 검증·적용하고 접수 결과를 돌려준다. 정지/취소 요청을 뒤의 RUN 요청이 덮어쓰지 않도록 우선순위를 적용한다.
7. 현재 작업의 남은 거리·도달 조건을 갱신하고 속도 프로파일·BODY 각도차를 계산한다.
8. 정지·감속·모드·초기화 조건을 최종 지령에 적용한다. 일반 프로파일 갱신이 이를 다시 덮어쓰지 못하게 한다.
9. 지령을 인코딩해 통신 측에 전달한다. 일반 값은 변경 시 쓰고 RESET은 이벤트로 전달한다.
10. 상태·odom·이벤트를 설정된 주기에 발행한다.

제어 dt, 다음 스캔 예측 시간, 휠 샘플 dt를 구분한다. 프로파일은 실제 제어 경과시간, 다음 스캔 예측은 설정된 스캔 주기, odom 속도는 유효 휠 샘플 간 시간을 사용한다. 과도한 주기 지연을 정상 10ms로 숨기지 않는다.

Python에서는 TCP I/O 스레드와 제어 실행 경로를 분리하고 `queue`·잠금으로 스냅샷 전달을 보호한다. GIL이 공유 상태의 일관성을 자동 보장한다고 가정하지 않는다. 100Hz는 목표 주기이며 Python 또는 C++ 선택만으로 실시간성을 보장하지 않는다. 실제 주기 지터·샘플 나이·계산 시간을 측정하고 그 결과를 후속 C++ 비교 기준으로 사용한다.

## 4. AgvControllerNode — 전체 조정과 서비스

| 함수 | 입력 | 출력/동작 |
| --- | --- | --- |
| `load_parameters()` | ROS 파라미터 | 검증된 ControllerConfig |
| `validate_parameter_update()` | 변경 요청 | 범위·상호 조건 검증 결과, 다음 주기 적용 예약 |
| `control_tick()` | 타이머 호출 | 3절의 제어 순서 실행 |
| `apply_pending_requests()` | 요청 큐, 최신 상태, SafetyDecision | JobManager 적용 결과, 서비스 응답 |
| `handle_plc_state_transition()` | 이전/현재 STATE, 연결 세대 | INITIALIZE 진입 이벤트 처리 |
| `initialize_controller_state()` | 초기화 이벤트 | 프로파일·작업 실행 상태 등 초기화, 휠 기준 재설정 조정 |
| `compose_plc_command()` | 작업 출력, 프로파일, 안전 판단 | 최종 PlcCommand |
| `apply_safety_override()` | 일반 지령, SafetyDecision | 최종 정지/감속/허용 지령 |
| `on_set_job()` | SetJob 요청 | 작업 등록 요청과 접수 결과 |
| `on_robot_control()` | RobotControl 요청 | RUNNING/STOP/PAUSE/취소/reset 처리 요청 |
| `on_manual_command()` | ManualCommand 요청, 수신 시각 | 수동 요청과 갱신 시각 전달 |
| `on_lift_control()` | SetMvLift 요청 | 리프트 요청 전달 |
| `on_measure_lap()` | MeasureLap 요청 | 기능 지원 여부 검증 및 캘리브레이션 요청 |
| `on_reset_odometry()` | x/y/yaw | 정지 조건 검증 후 pose 초기화 요청. 서비스 이름/타입은 후속 선정 |

콜백에서는 PLC 쓰기를 직접 수행하지 않는다. 서비스의 success는 JobManager가 실제로 접수한 결과이며 물리 동작 완료가 아니다. 접수 결과를 기다리는 방식은 제어 콜백을 막지 않는 지연 응답 방식으로 구현한다. 큐 등록만으로 무조건 success=true를 응답하지 않는다.

INITIALIZE=16이 지속되는 동안 초기화를 매번 반복하지 않는다. 첫 유효 샘플이 16일 때와 재접속 시의 이벤트 중복을 구분한다. 초기화 이벤트는 명시적 `reset_pose(x,y,yaw)`와 별개이며 자동으로 odom을 원점으로 옮기지 않는다.

## 5. plc_client.py — 통신과 변환 통합 파일

이 파일에 `PlcCodec`과 `PlcClient`를 함께 배치한다. 클래스별 역할과 아래 함수 목록은 유지하되 별도 codec 구현 파일은 만들지 않는다. 외부에서 필요한 클래스는 `from .plc_client import PlcClient, PlcCodec` 형태로 가져온다. `PlcClient`는 같은 파일의 `PlcCodec`을 호출해 송수신 데이터를 변환한다.

`PlcCodec`은 소켓·송신 캐시를 소유하지 않으므로 파일을 합쳐도 변환 함수는 독립적으로 시험할 수 있다. 테스트 파일은 역할별로 유지하며 `test_plc_codec.py`와 `test_plc_client.py` 모두 같은 `plc_client` 모듈에서 시험 대상을 가져온다. 수식 전용 `agv_math.py`는 그대로 분리한다.

### PlcClient

| 함수 | 입력 | 출력/동작 |
| --- | --- | --- |
| `connect()` / `disconnect()` | TCP 설정 / 종료 원인 | 연결 관리, 연결 세대 갱신 |
| `run_io_loop()` | 종료 신호, 요청 큐 | 단일 TCP 연결의 요청·응답 순차 처리 |
| `schedule_reads()` | 단조 시각 | DM0~39 10ms, DM50~99 100ms 읽기 예약 |
| `update_pc_life()` | 시각, 제어 루프 생존 여부 | 1초 주기 DM100 증가값과 DM101 CPU 온도 동시 쓰기 |
| `queue_changed_writes()` | 인코딩된 일반 지령 | 마지막 OK 값과 다른 쓰기 예약 |
| `queue_reset_event()` | 요청 ID, reset=1 | 일반 값 비교와 별개인 단발 쓰기 예약 |
| `send_next_request()` | 대기 요청, 현재 전송 상태 | 한 번에 하나의 요청 송신 |
| `consume_received_bytes()` | TCP 수신 바이트 | CRLF 프레임 조립, 부분 수신 보관 |
| `process_response()` | 응답 프레임, 전송 중 요청 | 피드백 또는 ACK/프로토콜 오류 |
| `handle_disconnect()` | 연결/응답 실패 | 데이터 유효성 해제, 이전 실행 명령 재생 방지 |
| `take_latest_snapshot()` | 없음 | 고속/저속 데이터별 freshness를 가진 읽기 전용 복사본 |
| `take_write_results()` | 없음 | 완료된 요청 ID·OK/실패 결과 |

읽기 명령은 `RDS DM0.U 40\r`, `RDS DM50.U 50\r`다. PLC 프로토콜에는 요청 ID가 없으므로 로컬 ID는 내부 추적에만 사용한다. 응답 timeout 뒤 같은 연결의 늦은 응답을 새 요청의 응답으로 오인하지 않도록 연결/프레임 동기화를 복구한다.

일반 송신 캐시는 OK 수신 시에만 갱신한다. 대기 중 속도 지령은 최신 값으로 대체하되 모드/목표값/COMMAND의 적용 순서를 깨지 않는다. 정지 시 오래된 대기 RUN·이동 지령을 무효화한다. 이미 전송 중인 요청은 전송하지 않은 것으로 되돌릴 수 없으므로 응답 이후 정지 우선 처리를 수행한다.

RESET=1은 요청마다 송신한다. OK 후 로컬 요청만 지우고 RESET=0은 보내지 않는다. WRS가 DM104에 불필요한 0/이전 1을 포함하지 않게 블록을 분리한다. DM100과 DM101은 WRS 한 프레임에 담고 DM102와 미정의 주소는 건너뛴다. PLC의 RESET 내부 소비 및 ACK 불명확 시 재시도 정책은 후속 계약으로 둔다.

### PlcCodec

| 함수 | 입력 | 출력/동작 |
| --- | --- | --- |
| `decode_fast_data()` | 검증된 40개 UINT16 워드 | 상태·휠·리프트·비상정지·시작 신호 |
| `decode_slow_data()` | 검증된 50개 UINT16 워드 | 장애물·거리 센서·배터리·충전 |
| `decode_dint()` | LOW/HIGH 워드 | 부호 있는 32비트 정수 |
| `decode_int16()` | UINT16 원시값 | 부호 복원 값 |
| `encode_command()` | SI 단위 PlcCommand | 범위 검사된 주소/워드 목록 |
| `encode_angle()` | rad | 0.01deg signed 정수 |
| `build_read_request()` / `build_write_request()` | 주소·수량·워드 | CR로 종료되는 PLC 명령 문자열 |

주소 스케일, 정수 반올림·범위 검사를 여기서 통일한다. 휠 거리·속도와 리프트 높이는 LOW/HIGH 워드를 DINT로 복원하고 1000으로 나누어 SI 단위 실수로 환산한다. DM107은 전달 전 인코딩만 하며 odom 도달 판정과 연결하지 않는다.

## 6. agv_math.py — 수식 전용

아래 표의 타입은 math_types.py에 정의할 설계 타입이다. 입력 객체는 변경하지 않고 다음 상태를 새 결과로 반환한다. 수식 함수끼리는 호출할 수 있지만 상태 저장 클래스나 PLC를 역으로 호출하지 않는다. C++에서도 같은 입출력 의미와 실패 조건을 유지한다.

| 함수 | 입력 | 반환 |
| --- | --- | --- |
| `transform_document_to_ros()` | 문서 좌표 Vector3 | CalcResult[Vector3], ROS 좌표 |
| `normalize_angle()` | rad 각도 | CalcResult[float], 정규화된 각도 |
| `calculate_center_radius()` | radius, standoff, body_offset | CalcResult[float], 중심 반경 m |
| `calculate_body_angle_error()` | 중심 반경, signed 현재 속도, scan_dt, 경로 방향, BODY 방향, 회전 부호 | CalcResult[float], 각도차 rad |
| `calculate_jerk_limit()` | 양의 가감속도 한계, jerk_ratio | CalcResult[float], m/s³ |
| `estimate_stopping_distance()` | ProfileState, 감속·jerk 한계 | CalcResult[float], 정지 거리 m |
| `step_speed_profile()` | ProfileState, 목표 속도, 남은 거리, 한계, dt | CalcResult[ProfileState], 다음 속도·가속도 |
| `estimate_body_delta()` | 휠 이동량·대표 조향각·휠 위치 | CalcResult[BodyDelta], BODY 이동/회전 및 적합 잔차 |
| `integrate_pose()` | 현재 Pose2D, BodyDelta | CalcResult[Pose2D], 다음 pose |
| `estimate_alignment_error()` | 센서 거리·기하·목표 standoff | CalcResult[AlignmentError], 거리·방향 오차. 기하 확정 후 구현 |

### 좌표와 각도차

```text
x_ros = y_document
y_ros = -x_document
z_ros = z_document

radius_center = radius + standoff + body_offset
heading_step = turn_sign * current_speed * scan_dt / radius_center
next_path_heading = current_path_heading + heading_step
target_theta = normalize_angle(next_path_heading - current_body_heading)
```

BODY가 현재 원주 방향과 일치하면 각도차는 heading_step이다. 후진은 current_speed 부호로 처리하며 BODY 목표 방향에 임의로 π를 더하지 않는다. 경로 기준 방향은 MotionController가 유지한다. 계산마다 경로 기준을 실제 BODY 방향으로 덮어쓰면 피드백 오차가 사라지므로 그렇게 처리하지 않는다.

기본 반경 2.85 m·속도 0.1 m/s·10ms의 방향 변화는 약 0.0201°다. DM109로 변환하면 회전 방향에 따라 +2 또는 -2 카운트가 된다. 내부 경로 기준·오차를 실수 정밀도로 보존하고 양자화 동작을 시험한다. 수식 함수 안에서 PLC 정수로 반올림하지 않는다.

### JERK 100%와 후진

```text
jerk_ratio = 1.0
jerk_limit = jerk_ratio * acceleration_limit / 1 second
allowed_acceleration_change = jerk_limit * dt

forward_acceleration = +accel_limit
forward_deceleration = -decel_limit
reverse_acceleration = -accel_limit
reverse_deceleration = +decel_limit
```

가속도 0에서 목표 가속도 0.1 m/s²까지 1초에 증가하며 jerk는 0.1 m/s³다. 10ms당 가속도 변화는 최대 0.001 m/s²다. 목표 속도 도달 시간과 구분한다. 목표 속도 0에서는 설정된 ACCELATION/DECCELATION을 사용한다.

정지 거리에는 현재 가속도와 jerk 제한을 반영한다. `speed²/(2×deceleration)`만으로 처리하지 않는다. 프로파일 함수는 짧은 거리의 최고 속도 감소, 감속 전환, 정지 시 반대 방향으로 속도가 넘어가지 않는 처리를 포함한다. 수치 해법을 사용한다면 제어 주기 내 계산량 상한을 둔다.

반경≤0, dt≤0, NaN/Inf, 기하 불능·추정 불능은 CalcResult 실패로 반환한다. 호출자가 실패를 처리하며 정상 0으로 간주하지 않는다.

## 7. OdometryEstimator / MotionController

### OdometryEstimator

| 함수 | 입력 | 출력/상태 변화 |
| --- | --- | --- |
| `update()` | 새 WheelSample, 차량 기하 | agv_math.estimate_body_delta()/integrate_pose() 호출 후 odom 상태 갱신 |
| `reset_wheel_baseline()` | 현재 WheelSample | 누적 거리 차분 기준 재설정, pose 유지 |
| `reset_pose()` | 지정 x/y/yaw, 현재 휠 샘플 | pose와 휠 기준을 함께 재설정 |
| `state()` | 없음 | pose·속도·마지막 유효성 조회 |

중복 sequence는 무시하고 오래된/비정상 샘플은 실패로 보고한다. 10ms 타이머가 같은 샘플을 여러 번 읽어도 이동량은 한 번만 계산한다. 실측 휠 조향각을 odom에 사용하되 컨트롤러가 휠 조향 지령을 생성하지 않는다. 누적 거리가 mm로 들어오므로 휠 지름을 다시 곱하지 않는다.

### MotionController

| 함수 | 입력 | 출력/상태 변화 |
| --- | --- | --- |
| `start_profile()` | 접수된 작업, 현재 odom | 프로파일·경로 기준 설정 |
| `update_path_reference()` | 새 odom 이동량, 경로 기하 | 경로 기준 방향 갱신 |
| `update_speed_profile()` | 남은 거리, 목표 속도, 안전 속도 상한, dt | agv_math.step_speed_profile() 호출, ProfileState 갱신 |
| `calculate_body_angle_error()` | 현재 odom, scan_dt | agv_math.calculate_body_angle_error() 호출, BODY 지령각 반환 |
| `request_stop()` | 일반 정지/보호 정지 원인 | 실행 허용·프로파일 모드 변경, 최종 정지는 SafetyDecision과 조정 |
| `reset()` | 초기화 사유 | 프로파일·경로 실행 상태 초기화 |

같은 이름의 클래스 메서드와 math 함수는 역할이 다르다. 메서드는 상태와 입력을 준비하고 결과를 반영한다. math 함수는 계산만 한다. 클래스 메서드 안에 수식을 복제하지 않는다.

## 8. JobManager — 작업·수동·리프트·캘리브레이션

| 함수 | 입력 | 출력/상태 변화 |
| --- | --- | --- |
| `register_job()` | SetJob를 변환한 요청, 상태 | 입력·중복 ID 검증 후 저장. 아직 출발하지 않음 |
| `apply_control_request()` | RUNNING/STOP/PAUSE/reset/cancel | 작업 전이·명령 의도·접수 결과 |
| `update_progress()` | 새 차량 중심 이동량 | 누적 이동·잔여 거리 갱신 |
| `is_target_reached()` | 거리 오차, 실측 속도, 시각 | 거리/속도 허용 범위의 유지시간을 만족하는지 판단 |
| `complete_drive()` | 도달 판정 | 주행 완료 단계와 HOLD 명령 의도 |
| `apply_manual_request()` | 수동 값, 수신 시각, 상태 | 허용 모드 검증, 수동 출력·갱신 시각 저장 |
| `clear_manual_command()` | 만료/정지 사유 | 관련 수동 스위치 false로 전환 |
| `register_lift_request()` | 높이·Auto 속도·job_id | 모드·아웃트리거·오류 검증 후 요청 저장 |
| `update_lift()` | 실제 고정·높이·상태 | 실행 가능 시 지령 의도, 진행/완료 판단 |
| `register_calibration()` | MeasureLap 요청, 기능/센서 상태 | 지원 여부 검증 후 접수 또는 거절 |
| `update_calibration()` | odom·거리 센서·후속 QR 결과 | 정렬 진행 및 lap_* 갱신 |
| `cancel_job()` | 취소 사유 | 재개 금지, 관련 보류 동작 제거 |
| `reset_execution_state()` | INITIALIZE 이벤트 | 실행 중 작업 상태 초기화 정책 적용 |

이동량은 시작점과 현재점의 직선거리가 아닌 차량 중심 경로로 적산한다. DM107은 전달값일 뿐 이 판단에 사용하지 않는다. 일반 STOP의 작업 보존 정책은 아직 확정되지 않았으므로 구현 중 임의 선택하지 않는다. 캘리브레이션은 PAUSE 시 lap_state=PAUSED, RUNNING 시 재개, STOP 시 FAILED다.

리프트의 로봇 safety reset 확인 주체는 RCS다. 컨트롤러는 PLC 모드·실제 아웃트리거 고정·오류를 확인한다. Auto에서만 요청 속도를 DM112에 적용하고 수동 속도는 PLC에 맡긴다. 취소된 작업의 보류 리프트 요청이 다음 작업에서 실행되지 않게 한다.

현재 QR은 `/sensor/qr` 파라미터만 준비한다. LAP의 실제 실행과 QR 원점 성공 판정은 구독 구현 후 연결한다. ALIGN은 센서 기하·완료 계약을 반영해 진행하며 두 거리만으로 QR 원점 성공을 보고하지 않는다. 미지원 기능은 시작 전에 이유를 반환한다.

## 9. SafetyMonitor / StatusPublisher

### SafetyMonitor

| 함수 | 입력 | 출력/판단 |
| --- | --- | --- |
| `check_plc_watchdog()` | LIFE, 샘플 시각, 연결, 설정 | LIFE 값 변화 감시, 기본 3초 미갱신 시 701 |
| `evaluate_obstacles()` | DM050/051, 적용 방향·설정 | bit0=정지, bit1=감속. 동시 발생 시 정지 우선 |
| `check_manual_timeout()` | 마지막 수동 요청 시각, 설정 | 수신 만료 여부 |
| `evaluate()` | PLC 상태, 작업 상태, 계산 유효성 | 종합 SafetyDecision |
| `can_reset_error()` | 실제 오류 원인·통신·상태 | 리셋 허용 또는 거절 이유 |

LIFE 값은 증가 여부가 아니라 변화 여부를 검사해 wrap을 허용한다. LIFE timeout, 시작 유예, TCP 응답 timeout은 각각 파라미터화한다. PLC 자체 PC LIFE timeout은 PC 설정만 바꿔 변경되는 것으로 취급하지 않는다.

DM030은 실제 E-STOP 피드백이다. PLC에 E-STOP은 쓰지 않는다. 경고는 E-STOP과 같은 동작 차단 정책으로 처리하되 미정의 경고 bit를 만들지 않는다. 감속 bit만 검출된 경우 obstacle_stop=true로 만들지 않는다. 비상정지 해제·통신 복구만으로 자동 출발하지 않는다.

### StatusPublisher

| 함수 | 입력 | 출력 |
| --- | --- | --- |
| `build_robot_status()` | PLC/작업/odom/오류 스냅샷 | RobotStatus 메시지 |
| `publish_robot_status()` | 완성된 상태 | `/vehicle/robot_status` |
| `publish_odometry()` | 유효 odom·휠 상태 | `/odom`, TF, `/joint_states` |
| `publish_path()` | pose, 경로 설정 | 제한된 점 개수의 `/vehicle/path` |
| `publish_events()` | 시작 버튼 에지·실제 estop | `/vehicle/start_request`, `/vehicle/robot_estop` |

PLC STATE=HOLD는 그대로 외부 HOLD에 매핑한다. 컨트롤러가 OUTRIG_STATE를 다시 조건으로 묶어 HOLD 보고를 지연하지 않는다. 로컬 통신 오류 등 별도 오류가 있는 경우에는 오류 정책을 함께 적용한다. OUTRIG_STATE의 리프트 인터록은 상태 보고와 분리한다.

level_state=-1, SOC 무효 시 battery_pct=255, 거리 미측정 시 standoff_meas=-1을 유지한다. 실제 charging은 DM062, 실제 estop은 DM030을 사용한다. bool 기본 false를 통신 정상의 증거로 사용하지 않는다. 실제 상태 문자열 PAUSE와 lap_state PAUSED를 구분한다.

## 10. 구현·검증 경계

| 검증 대상 | 핵심 사례 |
| --- | --- |
| agv_math | 좌표 변환, ±π 경계, 반경/NaN/Inf 검증, JERK 100%, 전후진 가감속, 정지 거리, 원주 각도차 |
| odom | 동일 샘플 중복, 휠 초기화·점프, 곡선 이동량, reset_pose 후 기준 일치 |
| PLC codec/client | 40/50워드, DINT·INT16, 부분/결합 수신, ACK 실패, 변경 쓰기, 반복 RESET, RESET=0 미송신 |
| 제어 흐름 | 정지 뒤 오래된 RUN 미적용, INITIALIZE 진입 한 번, HOLD 직접 전달, DM107 도달 판단 미사용 |
| 작업/수동 | SetJob 접수만으로 출발 안 함, 중복 ID, PAUSE/재개/취소, 수동 수신 만료, 리프트 인터록 |
| 상태/안전 | 장애물 값 0/1/2/3, LIFE wrap·3초 경계, DM030/031/062, 단절 후 자동 재출발 없음 |

수식 테스트는 ROS 노드와 PLC 없이 실행할 수 있게 구성한다. TCP 동작은 모의 PLC로 시험하고 통합 테스트에서 요청→연산→쓰기→피드백의 흐름을 검증한다. 현재 문서는 설계이며 이 테스트들을 실행했다는 의미는 아니다.

Python은 pytest, 후속 C++는 ament_cmake_gtest로 검증한다. 동일한 JSON 입력·기대값·단위·허용 오차를 사용하고 버전별 독립 계산 검증과 상호 비교를 모두 수행한다.

후속 확정 항목은 PLC RESET 소비·DM105 요청 규약, 일반 STOP 보존 정책, INITIALIZE의 외부 상태 및 초기화 범위, 센서 기하/ALIGN 완료 계약 등이다. 함수 경계에 이 정책을 모아 두어 수식과 통신 프레임 코드를 함께 수정하지 않도록 한다.

## 11. Python 우선 구현과 C++ 후속 버전

### 공통 계약과 선택 실행

| 항목 | Python 1차 버전 | C++ 후속 버전 |
| --- | --- | --- |
| ROS 패키지 | steer_agv_controller_py | steer_agv_controller_cpp |
| ROS 클라이언트 | rclpy | rclcpp |
| 빌드 | ament_python | ament_cmake |
| 계산 모듈 | agv_math.py + math_types.py | agv_math.hpp + agv_math.cpp |
| 상태 타입 | dataclass, Enum, 타입 힌트 | struct, enum class |
| 함수 역할 | 본문의 클래스·함수 구성 | 같은 역할과 입출력 의미로 대응 |
| 단위 테스트 | pytest | ament_cmake_gtest |
| 공통 자료 | vehicle_interfaces, 설정 의미, JSON 검증 사례 | 동일 |

bringup의 `implementation:=python`을 기본으로 두고 후속 버전은 `implementation:=cpp`로 선택하는 구조로 만든다. 선택한 패키지 하나만 실행하며 사용자가 잘못된 값을 지정하거나 해당 구현이 설치되지 않았다면 명확한 실행 오류를 반환한다. C++ 미구현 상태에서 조용히 Python으로 대체하지 않는다.

공통 노드 이름은 QUAD_STEER_AGV_CONTROLLER, 서비스·토픽 이름과 메시지 타입도 동일하게 유지한다. 설정은 공통 controller.yaml을 사용하고 언어에 따른 차이가 필요한 내부 실행 설정은 별도로 분리한다. vehicle_interfaces는 기존 공통 패키지를 계속 사용하며 Python 버전이라고 메시지 생성 패키지까지 ament_python으로 바꾸지 않는다.

두 버전 동시 비교는 서로 분리된 모의 PLC와 ROS namespace에서만 수행한다. 실차 버전 전환은 정지 상태에서 기존 프로세스를 종료하고 새 버전을 시작한다. 실행 중 작업·RESET·수동 지령을 자동 인계하거나 재생하지 않는다.

### 구현 순서

1. Python 패키지 및 타입·agv_math.py부터 구현한다. 이 단계에는 C++ 컴파일 의존성을 추가하지 않는다.
2. Python의 단일 plc_client.py에 PlcClient/PlcCodec을 구현하고 모의 PLC 시험을 연결한다.
3. Python odom·프로파일·작업·보호·상태 발행을 구성하고 100Hz 주기 특성을 측정한다.
4. 검증된 동작을 언어 독립 사례로 저장한다. 기존 계산 오류를 그대로 정답으로 삼지 않도록 별도 수학적 기대값도 포함한다.
5. C++ 패키지에서 같은 계약을 구현하고 수식·PLC 워드·상태 전이 결과를 비교한다.
6. bringup 선택 실행과 두 구현의 독립 빌드를 검증한다. 타이밍 결과는 각 환경에서 따로 측정한다.

정수 워드·명령 순서는 동일한 결과를 요구하고, 부동소수 계산은 명시된 허용 오차로 비교한다. 각도 정규화 경계와 rad→0.01deg 반올림 정책을 통일해 언어 차이가 PLC 지령 차이로 이어지지 않게 한다. 시간 관련 시험은 동일한 dt와 가상 시각을 주입한다.

현재 단계의 산출물은 이 함수 설계 문서다. Python/C++ 구현 파일 생성 및 실행은 다음 구현 단계에서 진행한다.
