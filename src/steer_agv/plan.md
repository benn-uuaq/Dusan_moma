# 4륜 조향 AGV ROS 2 개발 계획 및 문서 검토

- 작성일: 2026-11-19
- 재검토: 2026-11-19, 최신 원본 3종과 수정된 SetJob/SetMvLift 및 현재 인터페이스 생성 목록 대조.
- 추가 반영: 2026-11-19 사용자 답변. 문서와 설명이 다르면 이번 답변을 우선하며 계산상 해석은 별도로 표시한다.
- JERK 변경: 사용자 지시에 따라 100%(`jerk_ratio=1.0`)로 적용한다.
- 프로젝트: `/home/rnd08/ros2_ws/src/new_codex_projects/steer_agv`
- 환경: ROS 2 Humble. 현재는 계획 단계이며 제어 코드·빌드·실차 검증은 수행하지 않았다.
- 표시 기준: 문서에서 확인한 요구사항, 구현 제안, 미확정 계약을 구분한다.

## 1. 검토 자료와 이번 결론

원본은 `1.Documets/시나리오`, `1.Documets/PLC 통신방식`, `1.Documets/error코드`다. 실제 파일에는 `.txt` 확장자가 없다. `../vehicle_interfaces`의 메시지·서비스·CMake 생성 목록도 대조했다. 이번 변경은 `plan.md`와 원본 시나리오의 JERK 100% 반영이며 인터페이스는 수정하지 않는다.

이전 검토의 반경/standoff 역순 매핑, 리프트 서비스 대소문자, PAUSE 시 FAILED 여부는 최신 시나리오에서 해소됐다. TCP, 읽기 범위, 생존 신호 주기, 상태 입력 주소, 속도 0의 가감속 처리도 명시됐다. `SetJob.srv`에는 실제로 radius가 추가됐다. 이 항목들을 다시 미정으로 남기지 않는다.

이번 답변으로 +Y 전방/+X 우측, 다음 스캔 원주 방향 대비 BODY 각도차, jerk의 1초 기준, 후진 가감속 부호, DM107 단순 전달, PLC HOLD 그대로 보고, E-STOP 쓰기 제외, PLC OK 확인, watchdog 파라미터화, 장애물 bit0/bit1, INITIALIZE 진입 시 초기화가 정리됐다. 이 내용을 반복 확인 항목으로 남기지 않는다. 답변이 비어 있는 RESET의 PLC 내부 소비 방식과 기존 3.2절 계약은 별도로 유지한다.

## 2. 최신 문서에서 확인한 요구사항

| 구분 | 확정된 문서 내용 |
| --- | --- |
| 노드 이름 | `QUAD_STEER_AGV_CONTROLLER` |
| 제어 주기 | 10 ms, 100 Hz |
| PLC 연결 | TCP SOCKET, `192.168.11.10:8501` |
| 고속 읽기 | DM0~DM39, 40워드, 10 ms 주기 |
| 저속 읽기 | DM50~DM99, 50워드, 100 ms 주기 |
| 읽기 형식 | `RDS`의 `.U`로 읽은 후 각 변수 타입에 맞게 해석 |
| PLC 생존 신호 | 1 ms마다 갱신, 3초 이상 갱신되지 않으면 701 PLC_COMM_LOST 및 정지 |
| PC 생존 신호 | DM100 LIFE를 1초마다 1 증가시켜 DM101 CPU 온도와 함께 송신, PLC는 미갱신 시 정지 |
| 일반 쓰기 | 값이 변경될 때만 전송 |
| RESET 특례 | reset 요청 수신 시 DM104 전송, PLC `OK` 확인 후 로컬 값을 0으로 초기화하되 그 0은 송신하지 않음 |
| 데이터 단위 | 내부 m·m/s·rad, PLC mm·mm/s 및 수신 각도(DM021~024, DM029) 0.01deg, DM109 송신 각도 0.01deg |
| 스케일 | 0.1 단위 수신값은 ×0.1, 송신값은 ×10. 휠 거리·속도 및 리프트 높이 DINT → ROS float |
| 차량 한계 | 최대 속도 980 mm/s=0.98 m/s, 휠 지름 250 mm, 조향 ±110°, 최대 조향 각속도 90°/s |
| 가감속 | 목표 속도의 수치(m/s)를 가감속도 수치(m/s²)로 사용, 목표 속도 0이면 설정된 ACCELATION/DECCELATION 파라미터 사용 |
| jerk | 1초 기준 목표 가속도 증가 비율 100%. 초당 목표 가속도의 100% 변화로 두며 6절에 수식 명시 |
| 작업 접수 | SetJob으로 등록 후 RobotControl RUNNING에서 시작 |
| 반경 매핑 | `TARGET_RADIUS←radius`, `GUIDE_OFFSET_DIST←standoff` |
| 기본 기하 파라미터 | TARGET_RADIUS=1.25 m, GUIDE_OFFSET_DIST=1.0 m, BODY_OFFSET=0.6 m |
| PLC 목표값 | DM107은 제어상 의미 없이 상위 거리값만 전달, DM106은 컨트롤러 속도 연산값 전달. 도달 판정은 컨트롤러 odom 기준 |
| 조향 책임 | 현재 BODY 각도와 현재 속도로 다음 스캔까지 이동한 원주 방향의 각도차를 TARGET_THETA로 계산. 각 휠 속도·조향각 계산은 PLC/H/W 담당 |
| 문서 좌표 | +Y 전방, +X 우측. ROS에는 x_ros=y_document, y_ros=-x_document로 변환 |
| HOLD 보고 | PLC가 OUTRIG_STATE를 확인해 HOLD를 보고하며 컨트롤러는 읽은 상태를 그대로 전달 |
| 리프트 | `/vehicle/lift_control`, SetMvLift, DM108 목표 높이·DM112 속도. set_speed는 Auto에서만 적용, 수동 속도는 PLC 담당 |
| 수동 주행 | 200 ms마다 명령 갱신, 갱신이 끊기면 정지 |
| 비상정지 | DM030 실제 상태 읽기, `/vehicle/robot_estop`은 AGV 발행 → RCS 구독. PLC E-STOP 쓰기는 하지 않음 |
| 장애물 | DM050/051 bit0=정지, bit1=감속. 경고 처리 정책은 E-STOP과 동일 |
| 초기화 | PLC STATE가 INITIALIZE(16)로 변경될 때 컨트롤러 초기화 처리 |
| 물리 시작 버튼 | DM031 AUTO_READY |
| 주행 속도 피드백 | DM032 FEEDBACK_SPEED, 부호 있는 16비트 mm/s로 해석해 DM106과 비교 기록 |
| 충전 상태 | DM062 CHARGING |
| 수평 상태 | 현재 `level_state=-1` 사용 |
| 캘리브레이션 상태 | IDLE/RUNNING/DONE/PAUSED/FAILED, STOP 시 FAILED |
| 외부 상태·명령 | RobotStatus.state 및 RobotControl.state의 일시정지는 `PAUSE` 표기로 변경 |
| QR | 토픽 이름 파라미터 `/sensor/qr`만 준비, 실제 subscribe는 추후 적용 |
| ALIGN | 현재 단계에서는 PLC 거리 센서 1·2로 유추 |

### PLC 코드 및 주소

| 구분 | 코드 | 의미 |
| --- | --- | --- |
| MODE, DM001 읽기 | 0 / 1 / 2 | COBOT Manual / DRIVE Manual / Auto |
| COMMAND, DM103 쓰기 | 0 / 1 / 2 / 4 / 16 / 32 | STOP / RUN / HOLD / PAUSE / INITIALIZE / FINISH. 명령 8은 오류로 거부한다. |
| STATE, DM002 읽기 | 0 / 1 / 2 / 4 / 8 / 16 / 32 | STOP(작업 없음) / RUNNING / HOLDING / PAUSING(작업 있음) / ERROR / INITIALIZE / FINISH |

최신 쓰기 주소표에서 DM102 MODE는 삭제됐다. MODE는 읽어 동작 허용 여부를 판단하고 PC가 DM102로 변경하지 않는다. STATE 값이 2의 거듭제곱이어도 복합 비트마스크라고 가정하지 않는다.*변경 금지*

주요 읽기 매핑은 DM005~012 휠 거리, DM013~020 휠 속도, DM021~024 조향각, DM025 아웃트리거, DM026~027 단일 LIFT_HEIGHT의 LOW/HIGH, DM028~029 가이드 오차, DM030 비상정지, DM031 시작 신호, DM050~051 장애물 상태, DM052~053 거리 센서, DM059~061 배터리, DM062 충전 상태다.

쓰기 매핑은 DM100 LIFE, DM101 PC CPU 온도(UINT, 0.1°C, 미측정 65535), DM103 COMMAND, DM104 RESET, DM105 INITIALIZE, DM106 속도, DM107 거리, DM108 높이, DM109 조향각, DM110 거리 유지, DM111 반경, DM112 리프트 속도, DM120~127 수동 명령이다. DM102는 예약한다.

## 3. 사용자 확정 사항 및 남은 계약

### 3.1 제어·PLC 계약

| 항목 | 사용자 설명 및 상태 | 적용 계획 |
| --- | --- | --- |
| 좌표계 | +Y 전방, +X 우측으로 확정. | 6절의 ROS 좌표 변환을 적용한다. 원본 좌표를 ROS 축에 그대로 대입하지 않는다. |
| 조향각 기준 | 현재 BODY 각도와 현재 속도로 다음 스캔까지 진행한 원주 방향 사이 각도차다. 휠 각도는 PLC가 계산한다. | 컨트롤러는 다음 스캔의 경로 방향 대비 BODY 각도차만 계산·전송한다. 개별 휠 지령 생성은 구현하지 않는다. |
| jerk 100% | 1초 동안 목표 가속도까지 증가할 때의 비율. | jerk_limit=(100/100)×목표 가속도 크기/(1 s). 단위는 m/s³이며 예시를 6절에 기록한다. |
| 가감속 파라미터 | 후진 시 가감속 부호를 변경한다. | 전진 가속 +/감속 -, 후진 가속 -/감속 +. 한계값은 양의 크기로 유지하고 signed 가속도에 방향을 적용한다. 속도 0에서는 설정 가감속도 사용 |
| DM107 역할 | 제어상 의미 없고 상위 값을 그냥 전달한다. | 거리 지령 전달만 수행. 도달 판단·감속·정지는 컨트롤러 odom으로 처리하고 PLC 위치제어 완료를 기다리지 않는다. |
| HOLD | PLC가 OUTRIG_STATE 확인 후 HOLD를 보고한다. | 읽은 PLC HOLD 상태를 그대로 매핑한다. 컨트롤러가 별도의 OUTRIG_STATE 조건으로 HOLD 보고를 다시 판정하거나 지연하지 않는다. 리프트 실행 인터록은 별개 |
| E-STOP 쓰기 | 쓰지 않으며 목록에서 삭제한다. | 현재 원본에서 쓰기 항목 삭제를 확인했다. 읽기 DM030은 유지하고 쓰기 경로는 만들지 않는다. |
| RESET 소비 방식 | 이번 답변에서 별도 설명 없음. PC의 0 미송신 요구는 유지. | 유효 요청마다 reset=1 송신, 일반 변경 감지에서 제외. PLC 자체 해제/쓰기 이벤트 소비 및 불명확한 ACK의 재시도 방식만 후속 확인 |
| 송신 확인 | PLC OK 응답이다. | OK 수신 후 로컬 요청을 지운다. 오류 해제 완료는 실제 PLC 오류/상태 피드백으로 구분한다. |
| watchdog | 파라미터로 생성해 수정 가능하게 한다. | LIFE timeout·시작 유예·응답 timeout을 분리한다. PLC LIFE 미갱신 기본값은 3초. PLC 측 timeout은 PC 파라미터만 바꿔도 PLC에 적용되는 것으로 간주하지 않는다. |
| 장애물 상태 | 정지 bit0, 감속 bit1, 경고는 E-STOP과 동일하게 처리. | bit0 마스크 0x0001, bit1 마스크 0x0002. 정지를 감속보다 우선하고 감속만으로 obstacle_stop=true를 만들지 않는다. 별도 경고 bit는 임의 지정하지 않는다. |
| INITIALIZE | STATE가 INITIALIZE로 변경되면 초기화한다. | STATE의 16 진입을 감지해 초기화 루틴을 한 번 실행한다. 16을 읽는 매 스캔마다 반복하지 않는다. DM105 송신 요청과 읽기 STATE 전이는 구분한다. |

### 3.2 RCS 및 캘리브레이션 계약

| 항목 | 재검토 결과 | 적용 또는 확인 계획 |
| --- | --- | --- |
| SetJob.radius | 현재 실제 srv에 `float64 radius`가 추가됐다. | 추가 작업은 완료로 분류. RCS/AGV 양쪽 재빌드 및 타입 일치 검증은 별도 필요 |
| SetMvLift 생성 | 파일과 서비스 이름은 일치하지만 CMake 생성 목록에는 여전히 없다. | 구현 단계에서 `srv/SetMvLift.srv` 등록 |
| 리프트 허가 책임 | 수정 SetMvLift.srv는 협동로봇 safety reset 수신 확인 주체를 RCS로 명시한다. | RCS가 확인 후 요청하는 계약으로 반영. 컨트롤러의 별도 로봇 허가 입력을 새 필수 조건으로 만들지 않는다. 실제 아웃트리거·모드·오류는 컨트롤러에서 확인 |
| 수동 리프트 속도 | 수동 속도는 PLC가 조절한다고 명시됐다. | 별도 ROS 수동 리프트 속도 파라미터 요구를 제거. Auto set_speed의 기계 상한은 필요 |
| PAUSE와 PAUSED | 최신 시나리오에서 명령/state는 PAUSE, lap_state는 PAUSED다. 기존 MeasureLap.srv와 RobotStatus.msg 주석은 이전 표기를 유지한다. | 서로 다른 필드의 의도된 매핑으로 계획한다. RCS 문자열 처리와 주석을 동기화하고 구형 PAUSED 명령 별칭은 임의로 추가하지 않는다. 문자열이므로 빌드만으로 호환성을 검증할 수 없음 |
| 일반 STOP | RobotControl 설명은 여전히 정지·일시정지지만 PLC STOP은 작업 없음이다. | 일반 STOP의 작업 보존 여부만 남은 충돌로 분리. 캘리브레이션 STOP=FAILED는 확정 |
| 작업 취소 | job_cancel=1은 취소, 거리 보존은 PAUSE다. | 취소 후 재개 금지안, job_id·mv_dist 결과 유지 기간 및 일반 작업 PAUSE 허용 범위 확정 |
| ALIGN 의미 | 상세 서비스는 QR 중앙 정렬, 최신 CONTROL은 거리 센서 1·2 기반 추론이다. | 거리·방향 정렬은 센서로 구현하되 원점 QR/판 중앙 식별과는 구분. 완료 조건과 lap_origin_ok 의미를 갱신해야 함 |
| QR 미연동 LAP | QR subscribe는 명시적으로 후속 범위다. | 현재 LAP은 미지원 사유로 접수 거절하는 안. QR 없이 출발하거나 거리만 채워 성공 처리하지 않음. QR 기능 연결 후 전체 LAP 활성화 |
| 센서 기하 | 센서 위치는 있으나 광축·장착 회전·유효 범위·곡면 측정 보정 규칙이 없다. | 동일 좌표계인지 확인하고 광축/표면 기하를 정의. 단순 두 거리 차이로 QR 정렬 완료를 주장하지 않음 |
| 수평 판단 | 현재 level_state=-1로 지정됐다. | IMU 구독·수평 측정을 초기 구현 필수 조건에서 제외. -1을 수평 성공으로 바꾸지 않음 |
| 시작 버튼 | DM031=AUTO_READY가 명시됐다. | 상승 에지당 한 번 발행. 접속 시 이미 1인 경우, 재접속·채터링 처리 규칙 확정 |
| 수동 허용 모드 | COBOT Manual/DRIVE Manual 코드가 있으나 장치별 허용표는 없다. | 주행·리프트·아웃트리거·초기화별 허용 MODE 확정 |

### 3.3 데이터 표현

- 고속 40워드, 저속 50워드 읽기로 범위를 수정한다. DM100과 저속 읽기 범위의 중복은 해소됐다. 미정의 예약 워드는 읽더라도 명령·상태로 해석하지 않는다.
- DINT와 LOW/HIGH 워드 표기는 확정됐다. 두 숫자 워드를 `low | (high << 16)`으로 조합해 비트 재해석하는 안을 알려진 실제 샘플로 검증한다. ASCII 숫자 응답을 TCP 바이트 순서와 혼동하지 않는다.
- `.U`로 읽은 INT16 원시값이 32768 이상이면 65536을 빼 부호를 복원한다. BOOL은 PLC가 허용하는 값 범위를 정해 검증한다.
- 수신 각도는 INT16(raw)×0.01×π/180으로 rad 변환, DM109는 rad×180/π×100을 정수화한다. 정수 반올림 정책을 통일한다. 원시값 250은 2.50°다.
- DM107의 signed 16비트 mm 표현 범위는 -32.768~32.767 m다. 단순 전달 요구도 범위 검사를 생략하는 뜻은 아니다. 초과 거리는 거절하거나 합의된 주소 확장/분할 계약을 사용하며 임의 절삭·wrap하지 않는다.
- UINT16 mm의 높이·반경·오프셋 상한은 65.535 m다. 타입 범위와 실제 기계 한계는 별도로 검사한다. DM106 속도 크기는 980 mm/s 이하, DM112 리프트 속도 상한은 별도다.
- LIFE는 UINT16의 순환을 허용한다. +1/1 ms 카운터라면 65.536초마다 wrap하므로 수치 증가 여부가 아니라 값 변화와 마지막 변화 시각으로 감시한다. 읽기는 10 ms이므로 모든 카운트 수신을 요구하지 않는다.
- NaN/Inf는 입력·센서 변환·연산 결과에서 검출하고 해당 명령 거절 또는 측정 무효화로 처리한다. 0으로 대체해 정상값처럼 사용하지 않는다.

## 4. ROS 2 패키지 및 인터페이스

10 ms 제어를 위해 C++/ament_cmake를 우선 제안한다. 통신 I/O와 제어 타이머를 분리하고 타임스탬프·유효성이 있는 스냅샷을 전달한다. 일반 ROS 타이머만으로 실시간 마감 준수를 보장하지 않으며 지터를 측정한다.

```text
ros2_ws/src/new_codex_projects/
├── vehicle_interfaces/          # 기존 공통 메시지·서비스 사용
└── steer_agv/                   # 프로젝트 묶음, 자체 package.xml 없음
    ├── 1.Documets/
    ├── plan.md
    ├── steer_agv_controller/    # PLC, odom, 제어, 작업/오류, RCS
    ├── steer_agv_description/   # URDF/Xacro, 센서·휠 프레임
    └── steer_agv_bringup/       # launch, YAML, RViz
```

기존 ros2_ws에서 빌드하며 중첩 워크스페이스나 vehicle_interfaces 복제본을 만들지 않는다. 패키지 이름과 실행 노드 이름 `QUAD_STEER_AGV_CONTROLLER`는 구분한다. 서비스 콜백의 명령은 단일 적용 경로로 전달해 제어 상태와의 동시 수정을 방지한다.

### 수식 계산 파일 분리 — 채택한 구성

사용자 요청에 따라 수식은 별도 `agv_math.cpp`에 구현하고, 제어·오도메트리 모듈은 헤더에 선언된 함수를 호출한다. 아래는 구현할 파일 구조이며 현재 파일을 생성한 것은 아니다.

```text
steer_agv_controller/
├── include/steer_agv_controller/
│   ├── agv_math.hpp
│   ├── motion_controller.hpp
│   └── odometry_estimator.hpp
├── src/
│   ├── agv_math.cpp
│   ├── motion_controller.cpp
│   └── odometry_estimator.cpp
└── test/
    └── test_agv_math.cpp
```

`agv_math.hpp`에는 계산용 입력·결과 구조체와 함수 선언을, `agv_math.cpp`에는 실제 수식을 둔다. 네임스페이스는 `steer_agv::math`로 구성하고 CMake에서 계산 라이브러리로 빌드해 컨트롤러와 수식 단위 테스트가 함께 링크한다. `.cpp`를 직접 include하지 않는다.

| 계산 함수 | 역할 | 호출 위치 |
| --- | --- | --- |
| `transform_document_to_ros()` | +Y 전방/+X 우측 좌표를 ROS 좌표로 변환 | 차량 기하 설정, OdometryEstimator |
| `normalize_angle()` | 각도차를 ±π 기준으로 정규화 | BODY 각도차 및 odom 계산 |
| `calculate_center_radius()` | radius + standoff + body_offset | MotionController |
| `calculate_body_angle_error()` | 현재 속도와 다음 스캔 원주 방향에 대한 BODY 각도차 | MotionController |
| `calculate_jerk_limit()` | 목표 가감속도와 jerk_ratio로 jerk 한계 계산 | 속도 프로파일 계산 |
| `estimate_stopping_distance()` | 현재 속도·가속도·감속·jerk를 반영한 정지 거리 | MotionController |
| `step_speed_profile()` | 현재 속도·가속도·남은 거리·제한·dt로 다음 속도/가속도 계산 | MotionController |
| `estimate_body_delta()` | 휠 거리 차분·실측 조향각·기하로 BODY 이동/회전량 추정 | OdometryEstimator |
| `integrate_pose()` | 현재 pose와 BODY 이동량을 결합해 다음 pose 계산 | OdometryEstimator |
| `estimate_alignment_error()` | 센서 기하와 거리로 정렬 오차 계산. 기하 확정 후 구현 | ALIGN 제어 |

계산 함수는 ROS 노드, PLC 통신, 파라미터 서버, 로그 발행, 작업 상태를 직접 다루지 않는다. 필요한 값은 인자로 받고 결과로 반환한다. 시간도 내부에서 읽지 않고 호출자가 `dt`를 전달한다. 동일 입력은 동일 결과를 내도록 하며 숨겨진 전역 상태를 두지 않는다.

MotionController는 현재 프로파일 속도·가속도·원주 기준 방향을 보관하고, OdometryEstimator는 이전 휠 샘플과 현재 pose를 보관한다. 계산 함수는 전달받은 상태로 다음 상태를 계산할 뿐이며 실제 상태 갱신·초기화·명령 우선순위는 호출자가 담당한다. 특히 BODY 각도차 양자화로 남는 경로 방향 오차는 MotionController에서 유지한다.

수식 내부의 0 이하 반경·잘못된 dt·NaN/Inf·계산 불능 결과는 명시적인 계산 실패로 반환한다. 실패를 정상값 0으로 바꾸지 않고 호출자가 명령 거절·측정 무효화·정지 등 해당 정책을 적용한다. 계산 단위는 SI로 통일하며 PLC 워드 조합·DM109의 0.01deg 정수 인코딩·프로토콜 처리는 PlcCodec에 유지한다.

호출 흐름은 `control_tick() → MotionController/OdometryEstimator → steer_agv::math 함수 → 결과 검증·상태 갱신 → PLC 지령 구성/ROS 발행`이다. 수식을 수정할 때 제어 루프에 동일 수식을 복제하지 않고 계산 모듈을 수정한다.

| 종류 | 이름 | 타입 및 내용 |
| --- | --- | --- |
| 서비스 | `/vehicle/set_job` | SetJob, radius 포함 |
| 서비스 | `/vehicle/robot_control` | RobotControl |
| 서비스 | `/vehicle/manual_command` | ManualCommand |
| 서비스 | `/vehicle/measure_lap` | MeasureLap, 현재/후속 지원 범위 구분 |
| 서비스 | `/vehicle/lift_control` | SetMvLift, CMake 생성 등록 필요 |
| 서비스 | 이름·타입 미정 | 정지 중 odom x/y/yaw 초기화안 |
| 발행 | `/vehicle/robot_status` | RobotStatus |
| 발행 | `/vehicle/start_request` | std_msgs/Empty, DM031 상승 에지 |
| 발행 | `/vehicle/robot_estop` | std_msgs/Bool, DM030 실제 피드백 |
| 발행 | `/odom`, `/joint_states`, `/vehicle/path` | Odometry, JointState, Path |
| TF | `odom → base_link → 휠/센서` | 컨트롤러·robot_state_publisher 발행 책임 분리 |
| 파라미터만 준비 | `/sensor/qr` | QR 토픽 이름 기본값. 현재 subscriber 생성 안 함 |

YAML은 TCP 주소/포트, 주기, timeout, 속도 제한, 기본 기하값 1.25/1.0/0.6 m, 속도 0용 가감속도, 수동 만료시간, 센서 기하와 허용 오차를 관리한다. 아직 수치가 없는 항목은 구현 시 합의하거나 명시적 필수값으로 둔다. 상태·경로 토픽 주기/QoS는 RCS와 정하고 Path 점 개수는 제한한다.

watchdog 설정은 `plc_life_timeout_s`(기본 3.0), `startup_grace_s`, `plc_response_timeout_s`로 분리해 수정할 수 있게 한다. PLC 쪽 PC LIFE timeout은 별도의 PLC 설정이며 해당 쓰기 주소가 없는 상태에서 ROS 파라미터 변경을 PLC 변경으로 취급하지 않는다. `jerk_ratio` 기본값은 1.0, 속도 0용 가감속도는 양의 크기로 설정한다. 설정 변경은 한 제어 주기에 일관되게 적용한다.

## 5. PLC 통신 및 쓰기 정책

1. TCP 프레임 버퍼로 분할·합쳐진 수신을 처리한다. 송신 CR, 수신 CRLF, RD/RDS/WR/WRS와 OK/E0/E1 응답을 파싱한다. 요청 ID가 없으므로 연결 내 요청/응답을 직렬화하고 timeout 뒤 늦은 응답을 다음 요청에 오인 매칭하지 않는다.
2. 고속 읽기는 `RDS DM0.U 40\r`, 저속 읽기는 `RDS DM50.U 50\r`로 구성한다. 응답 수량·숫자 범위·데이터 나이를 검사하고 부분 응답을 상태에 반영하지 않는다.
3. FLOAT 두 워드와 네 휠 측정의 PLC 스캔 일관성을 검증한다. 필요하면 PLC 스냅샷 규약을 추가한다.
4. 10 ms마다 제어 지령을 계산하되 일반 쓰기는 PLC 단위로 인코딩한 값이 마지막 수락값과 달라질 때만 전송한다. 목표값·대기값·ACK 확인값을 구분하고 실패한 쓰기를 완료 캐시에 넣지 않는다.
   주행 중 DM106도 마지막 PLC OK를 받은 속도 워드와 달라질 때만 쓴다. 비교 단위는 1 mm/s이며 재접속 시에는 캐시를 새로 시작한다.
5. LIFE는 1초마다 값이 바뀌므로 변경 시 쓰기 정책과 양립한다. PLC LIFE는 마지막 변화 이후 경과시간이 plc_life_timeout_s(기본 3초) 이상이면 701 및 정지 처리한다. 시작 유예와 응답 timeout은 별도 파라미터로 적용하고 경과시간에는 단조 시계를 사용한다.
6. DM104 RESET은 일반 상태값과 다른 요청 이벤트로 처리한다. 유효한 reset=1 요청마다 전송하고 PLC OK 확인 뒤 로컬 요청만 0으로 지운다. 그 0은 전송하지 않으며 다음 reset=1도 누락하지 않는다. timeout으로 적용 여부가 불명확한 요청의 재시도는 PLC 소비 규칙에 맞춘다.
7. 연속 WRS가 DM104를 끼워 넣어 RESET=0을 전송하거나 이전 1을 재실행하지 않게 블록을 분리한다. LIFE와 CPU 온도는 WRS DM100.U 2로 함께 쓰고 DM102 및 예약 영역은 건너뛴다. DM105 INITIALIZE의 단발/레벨 규칙은 별도 확정한다.
8. 실제 MODE를 읽고 목표값을 먼저 적용한 뒤 COMMAND를 보내는 순서를 계획한다. 동일 COMMAND 값이 반복돼도 PLC가 새 작업을 알아채는지, STOP/ACK 등 중간 단계가 필요한지 검증한다.
9. 수동 서비스가 같은 true를 반복하더라도 수신 시각은 갱신한다. PLC 쓰기 생략과 RCS 수신 만료는 별개다. 만료 시 관련 비트를 false로 전환하고 그 변경을 전송한다.
10. 시작·재접속 때 쓰기 캐시를 신뢰하지 않고 실제 상태와 안전한 초기값을 재동기화한다. 이전 RUN·RESET·수동 명령은 자동 재실행하지 않는다. 정지 지령이 캐시에 의해 생략되지 않도록 초기 동기화 규칙을 정한다.
11. 통신 단절 시 PC가 정지 쓰기를 성공시킬 수 없다는 점을 반영하여 PLC 측 PC LIFE watchdog으로 실제 정지를 검증한다. 제어 루프가 멈췄는데 통신 스레드만 LIFE를 계속 갱신하는 상황도 감지하도록 설계한다.
12. 정상/최악 RTT, 제어 주기 지터·누락, 샘플 나이, 연속 실패·재접속을 기록한다. E0/E1의 상세 의미와 실패 분류는 PLC 사양으로 확정한다.
13. DM002가 INITIALIZE(16)로 진입할 때 컨트롤러 초기화 이벤트를 발생시킨다. 초기화 중 스냅샷을 계속 읽되 같은 상태 유지로 이벤트를 재실행하지 않는다. 최초 유효 샘플이 16인 경우 한 번 처리하는 안이며 재접속 시 동일 초기화의 중복 실행을 구분한다.

## 6. 좌표·오도메트리·주행·ALIGN

### 좌표와 TF

문서 좌표는 사용자 설명대로 +Y 전방, +X 우측이다. 휠 위치는 FL=(-0.410,0.680), FR=(0.410,0.680), RL=(-0.410,-0.680), RR=(0.410,-0.680) m로 관리한다. ROS base_link는 +x 전방/+y 좌측, z는 상방으로 두어 다음 변환을 적용한다.

```text
x_ros = y_document
y_ros = -x_document
z_ros = z_document

FL_ros = ( 0.680,  0.410)
FR_ros = ( 0.680, -0.410)
RL_ros = (-0.680,  0.410)
RR_ros = (-0.680, -0.410)
```

거리 센서 위치는 문서 기준 S1=(0.6,0.81,0.7) m, S2=(0.6,-0.81,0.7) m다. ROS에서는 각각 (0.81,-0.6,0.7), (-0.81,-0.6,0.7) m이고 간격은 1.62 m다. 센서 광축은 위치와 별도로 설정한다. PLC가 계산해 보내는 실제 휠 조향각은 odom 입력으로 단위·영점·부호를 매핑하며, 이것은 컨트롤러가 휠 조향 지령을 계산한다는 뜻이 아니다.

### 오도메트리

- 휠 누적 거리 차분·조향각으로 접지점 이동을 구하고 차량 평면 강체 운동과 결합해 중심 이동·회전량을 추정한다. 네 휠 최소제곱 결합과 잔차 검증을 제안한다.
- 조향 변화 중 대표 각도, 휠 구름 부호, 슬립 영향을 시험한다. 이미 mm인 휠 이동량에 지름을 다시 곱하지 않는다. 지름은 JointState 회전 계산 등에 사용한다.
- 첫 샘플·재접속·엔코더 초기화 시 기준만 재설정한다. 값 튐을 이동으로 누적하지 않는다. odom 초기화도 휠 기준과 경로 처리까지 함께 수행한다.
- x/y/yaw, 속도, 공분산, TF/Path를 제공한다. 휠 기반 평면 오도메트리로 z/roll/pitch를 실측했다고 표시하지 않는다.
- 작업 거리는 시작점과 현재점 사이 직선거리가 아니라 차량 중심 경로를 누적한다. 후진 mv_dist의 부호 규칙은 RCS와 정하고 lap_path_m은 경로 길이로 관리한다.

### 속도와 조향

- SetJob은 입력을 검증하고 저장만 한다. RUNNING에서 실행하고 방향은 set_dist 부호로 정한다. set_speed는 양의 크기로 받는 안을 제안한다.
- 상한은 0.98 m/s다. 목표 속도 크기 0.1 m/s이면 가감속도 크기는 0.1 m/s²로 적용한다. 목표 속도가 0이면 설정된 ACCELATION/DECCELATION을 사용한다.
- 후진에서는 signed 가감속 부호를 반전한다. 방향 계수 direction=+1(전진)/-1(후진)에 대해 가속 시 acceleration=direction×accel_limit, 감속 시 acceleration=-direction×decel_limit이다. 정지 감속 방향은 현재 이동 방향을 사용하고 속도가 0을 넘어 반대 방향으로 튀지 않게 한다.
- jerk 계산은 `jerk_limit=jerk_ratio×acceleration_limit/(1 s)`다. 비율 100%=1.0이면 1초에 목표 가속도의 100%만큼 변하고, 가속도 0에서 목표까지 선형 증가하는 데 1초가 걸린다. 예: 목표 가속도 0.1 m/s²이면 jerk 한계는 0.1 m/s³, 10 ms당 가속도 변화 한계는 0.001 m/s²다. 이 1초는 목표 속도가 아닌 목표 가속도에 도달하는 시간이다.
- 매 주기 signed 가속도를 목표 가속도 쪽으로 jerk_limit×dt 이내에서 변경한다. 감속에도 해당 decel_limit 기준을 적용하고 후진 jerk 부호는 가속도 변화 방향에 따라 결정한다.
- 짧은 거리에서는 최고 속도를 낮추고 실제 속도·남은 거리로 감속한다. 거리 오차·정지 속도·유지 시간으로 도달을 판단한다. 진행 없음·도달 시간 초과를 검출한다.
- DM107에는 SetJob.set_dist를 m→mm 변환·범위 검사 후 전달하고 DM106에는 속도 프로파일 결과를 mm/s로 전송한다. 각각 변경 시에만 쓴다. DM107은 제어상 의미가 없으므로 거리 도달·정지·완료 조건에 사용하지 않는다.
- 반경·거리 유지·BODY_OFFSET의 합을 차량 중심 경로 반경 R로 사용한다. 기본 합은 2.85 m다. `TARGET_RADIUS←radius`, `GUIDE_OFFSET_DIST←standoff`는 확정 매핑으로 적용한다.
- 기본값과 SetJob의 작업별 값은 구분하여 검증 성공 후 일괄 적용한다. 0이 명시된 요청을 무조건 기본값으로 대체하지 않는다. 작업 종료 후 유지/복원 정책과 MeasureLap의 반경 선택은 정한다.
- TARGET_THETA는 다음 스캔의 원주 진행 방향과 현재 BODY 방향의 차이다. 아래 계산안으로 구성하며 개별 휠 조향각·속도 분배는 PLC에 맡긴다. 휠 조향 ±110°·90°/s 사양을 BODY 각도차와 동일한 제어량으로 혼동하지 않는다.

### 다음 스캔 BODY 각도차 계산안

현재 주행 속도 current_speed는 부호가 있는 차량 중심 실측 속도, scan_dt는 제어 주기(기본 0.01 s)다. 원주 기준 방향 current_path_heading과 현재 BODY 방향 current_body_heading은 같은 ROS 각도 기준으로 유지한다. turn_sign은 원주 회전 방향을 나타내며 후진은 current_speed의 부호로 처리한다.

```text
radius_center = radius + standoff + body_offset
travel_next = current_speed * scan_dt
heading_step = turn_sign * travel_next / radius_center
next_path_heading = current_path_heading + heading_step
target_theta = wrap_to_pi(next_path_heading - current_body_heading)
```

현재 BODY가 원주 방향과 일치하면 target_theta=heading_step이다. 방향 오차가 있으면 현재 원주 방향 오차도 포함한다. 순수 후진 때문에 BODY 목표 방향에 π를 더하지 않는다. radius_center>0과 계산값 유효성을 검사하고 현재 경로 기준 방향은 이동량과 경로 기하로 유지한다.

내부는 rad로 계산하고 DM109에는 0.01deg 정수로 변환한다. 기본 반경 2.85 m, 속도 0.1 m/s, 주기 10 ms에서 한 주기 방향 변화는 약 0.0201°로 회전 방향에 따라 +2 또는 -2 카운트가 된다. 원주 기준 방향과 BODY 피드백 오차를 고정밀도로 유지해 미반영 오차가 다음 주기에 남게 하고, 실제 전송의 양자화 영향을 모의 시험한다. 매 주기 경로 방향을 현재 BODY 방향으로 덮어써 이 오차를 소실시키지 않는다.

### 단계별 ALIGN/LAP

- 현재 QR은 `/sensor/qr` 문자열 파라미터만 둔다. 메시지 타입과 subscriber는 후속 범위다. level_state는 -1을 유지한다.
- ALIGN은 DM052/053의 거리로 거리·방향 오차를 추정한다. 평면에 평행 광축으로 측정한다는 조건이 성립하면 센서 간격과 거리 차이로 방향 오차를 구할 수 있지만, 원통 곡률·센서 축을 확인하기 전 해당 식을 일반식으로 사용하지 않는다.
- 두 거리 센서만으로 판 중앙 QR이나 원점 ID를 식별할 수 없다. 따라서 센서 정렬 성공을 `lap_origin_ok=true`로 변환하지 않는다. QR 없는 ALIGN의 완료 판정·RCS 수락 조건을 합의하기 전에는 정식 ALIGN 접수도 미지원으로 거절하는 안이다. 센서 오차 계산과 모의 정렬 검증은 먼저 진행할 수 있다.
- QR 구독 구현 전 LAP은 실행하지 않는다. 후속 단계에서 QR ID·pose·품질·시각, 원점 이탈·최소 진행·동일 원점 재검출·정지 확인을 구현한다. expected_dist는 안전 한도 입력이며 거리만 채웠다고 DONE을 보고하지 않는다.

## 7. 작업 상태·리프트·비상정지

외부 state는 최신 문서의 STOP/RUNNING/PAUSE/HOLD/ERROR/INITIALIZE/FINISH 를 사용한다. 내부에는 작업 대기·주행·감속·고정 대기·리프트·초기화 단계를 별도로 둔다.

| 사건 | 처리 계획 |
| --- | --- |
| SetJob 수락 | job_id·거리·속도·standoff·radius 저장, 출발 안 함 |
| RUNNING | 실제 모드·비상정지·오류·장애물·장치 인터록 확인 후 시작/재개 |
| 일반 PAUSE | 거리/작업 보존, COMMAND=4, STATE=4에 외부 state=PAUSE 대응안 |
| 일반 STOP | 정지 수행, 작업 폐기/보존 계약은 3.2절의 잔여 충돌 확인 필요 |
| job_cancel=1 | 작업 취소, 해당 작업 재개 금지안 |
| 목표 도달 | 컨트롤러 odom 도달·정지 확인 → COMMAND=2(HOLD) 요청 → PLC가 보고한 HOLD 상태를 그대로 전달 |
| 리프트 요청 | RCS가 로봇 safety reset 수신 확인 후 호출. 컨트롤러는 실제 고정·모드·오류 확인 후 목표 높이 전달, Auto 속도는 DM112에 전달 |
| MeasureLap 수락 | 지원 기능과 입력 유효성 확인 후 즉시 시작. 현재 미연동 기능은 시작 전 거절 |
| 캘리브레이션 PAUSE | 정지 후 state=PAUSE, lap_state=PAUSED, 경로·작업 보존 |
| 캘리브레이션 RUNNING | 보존된 작업 재개, lap_state=RUNNING |
| 캘리브레이션 STOP | 작업 종료, lap_state=FAILED 및 lap_error |
| INITIALIZE=16 | PLC STATE의 16 진입 시 초기화 루틴 한 번 실행. 유지 중 반복 초기화 금지, 후속 PLC 상태를 추적 |
| FINISH=32 | RCS가 `/vehicle/robot_control.state="FINISH"` 요청 → 자동 모드·차량 정지·리프트 유휴 확인 → DM103=32 전송 → PLC가 HOLD를 해제하고 DM002=32 보고 → RobotStatus.state="FINISH". FINISH에서 유효한 새 SetJob을 수락하거나 job_cancel=1을 받으면 DM103=0(STOP)을 전송한다. 새 작업은 PLC의 STOP 피드백 이후 별도 RUNNING 요청으로 시작한다. |
| 오류 | 정지 및 ERROR, 실행 중 캘리브레이션은 FAILED/lap_error |
| 리셋 | 원인 제거·PLC 요청 처리·실제 해제 확인 후 STOP 또는 실제 고정에 따른 HOLD |

상태 조회의 실제 피드백과 서비스 접수 성공을 구분한다. 리프트 목표는 고정 전에 보류할 수 있으나 오래된 작업의 높이를 다음 작업에서 자동 실행하지 않도록 job_id·취소·만료 정책을 정한다. 리프트 job_id의 RobotStatus 반영 및 완료 판정도 합의한다.

HOLD는 PLC가 OUTRIG_STATE를 확인한 결과이므로 컨트롤러에서 이중 판정하지 않는다. 읽은 STATE=2를 외부 HOLD로 매핑하고 OUTRIG_STATE는 별도 상태와 리프트 인터록에 사용한다. 초기화는 STATE=16 진입 이벤트에 연결하며 작업/프로파일 등 내부 초기화 대상과 명시적 odom pose 초기화 API를 구분한다. 이번 지시만으로 매 초기화 시 odom 좌표를 임의로 (0,0,0)으로 바꾸지는 않는다. 외부 INITIALIZE 상태 문자열은 RCS 기존 계약과의 동기화 대상으로 남긴다.

비상정지·오류·장애물을 주행 명령보다 우선 적용한다. DM030으로 robot_estop과 RobotStatus.estop을 일관되게 갱신한다. 해제돼도 자동 재출발하지 않고 리셋 규칙을 따른다. 안전 회로가 실제 동시 정지를 담당한다. 시나리오의 estop 설명에 남은 토픽 입력 표현은 새 발행 방향과 맞게 정리할 대상이다.

DM050/051은 각각 bit0(0x0001)=정지, bit1(0x0002)=감속으로 해석한다. 정지 bit는 obstacle_stop에 반영하고 감속 bit만 있으면 감속 처리한다. 동시 검출 시 정지가 우선이다. 경고는 사용자 지시에 따라 E-STOP과 같은 동작 차단 정책을 적용하되 별도 경고 bit를 새로 지정하거나 PLC E-STOP 쓰기를 만들지 않는다. 감속 속도 제한과 방향별 센서 적용은 설정으로 관리하고, 실제 estop 피드백은 DM030에서 읽는다.

reset+RUNNING 복합 요청, 실행 중 새 작업, 중복 job_id, 동시 전진/후진, HOLD 상태 주행, 취소·리셋 재요청의 처리 순서를 정의한다. INITIALIZE와 RESET 같은 이벤트는 수동 연속 스위치와 같은 중복 억제 규칙으로 처리하지 않는다.

## 8. 오류와 상태값 매핑

오류표는 여전히 3S 제안이다. DM004 원시 코드와 RCS 코드가 같은지 확인하고 다르면 명시적 매핑을 둔다. 미지 코드를 정상으로 취급하지 않는다. 여러 오류 발생 시 대표 코드 우선순위와 전체 로그를 관리한다.

| 분류 | 코드·이름 |
| --- | --- |
| 주행 | 101 DRIVE_MOTOR_FAULT, 102 DRIVE_ENCODER_FAULT, 103 DRIVE_TARGET_NOT_REACHED, 104 STANDOFF_LOST, 105 ALIGN_FAILED |
| 리프트 | 201 LIFT_MOTOR_FAULT, 202 LIFT_LIMIT, 203 LIFT_POSITION_ERROR, 204 LIFT_BRAKE_FAULT |
| 아웃트리거 | 301 OUTRIGGER_SET_FAILED, 302 OUTRIGGER_RELEASE_FAILED, 303 LEVEL_FAILED, 304 OUTRIGGER_LOST |
| 센서 | 401 DISTANCE_SENSOR_FAULT, 402 QR_NOT_FOUND, 403 IMU_FAULT |
| 전원 | 501 BATTERY_CRITICAL, 502 CHARGER_FAULT |
| 안전 | 601 SAFETY_SCANNER_FAULT, 602 SAFETY_CIRCUIT_FAULT |
| 통신 | 701 PLC_COMM_LOST, 702 DRIVER_COMM_LOST |
| 내부 | 901 NODE_INTERNAL |

- 오류는 error_code≠0, error_msg="이름 — 상세", state=ERROR로 보고하고 이동·리프트·아웃트리거 명령을 거절한다. 서비스 단순 거절은 success=false/message로만 알린다.
- 비상정지 눌림·정상 장애물 감지·일반 SOC는 각각 별도 상태 필드다. 장치 고장과 위험 배터리는 오류표를 따른다.
- 원인 제거 후 명시적 reset이 공통 규칙이다. 701/702의 "다시 붙으면"은 복구 선행 조건으로 해석하는 안이며 자동 오류 해제 여부는 확인한다. 901은 원문대로 노드 재시작 대상이다.
- DM062→charging, DM030→estop, DM060의 0.1%→battery_pct로 변환한다. SOC 무효는 255, 현재 level_state=-1, standoff 미측정은 -1을 사용한다.
- level_state=-1인 초기 구현에서 IMU 미연동만으로 403을 발생시키지 않는다. QR 미지원 명령 거절도 실행 중 QR 탐색 실패 402와 구분한다.
- estop/charging의 bool은 미확인을 표현하지 못한다. 시작·단절 상태의 기본 false를 정상 확인으로 사용하지 않고 데이터 유효성과 통신 오류로 동작을 제한한다. 프로세스 종료 시에는 오류 발행도 불가능하므로 외부 상태 수신 timeout과 PLC watchdog으로 감지한다.

## 9. 구현 순서와 검증

| 단계 | 작업 | 완료 기준 |
| --- | --- | --- |
| 1. 기반 | 패키지·YAML·launch·모의 PLC, SetMvLift 생성 등록, 기존 radius 타입 검증 | 기존 워크스페이스 선택 빌드 및 모의 실행 |
| 2. 통신 | TCP/40·50워드 읽기, DINT/단위, 변경 쓰기·RESET 이벤트·watchdog | 응답 분할·오류·단절·반복 RESET·미정의 주소 차단 시험 |
| 3. 피드백·시각화 | 확정된 +Y 전방/+X 우측 변환, PLC HOLD 전달, INITIALIZE 진입, odom/URDF/TF/Path | 직선·후진·곡선·초기화 방향 및 단위 검증 |
| 4. 작업 제어 | 1초 비율 jerk·후진 부호·다음 스캔 BODY 각도차, 일반 STOP 계약 반영, 수동/리프트 | 목표 도달, 짧은 거리, 취소·재개, 인터록·모드 검증 |
| 5. 센서 정렬 | 센서 기하와 완료 계약 확정 후 ALIGN, level_state=-1 | 거리·방향 추정 및 QR 완료 오인 없음 |
| 6. 후속 QR | QR 메시지·subscriber 및 원점 판정, LAP/QR 정렬 연동 | 원점 이탈·재진입, QR 상실·거리/시간 초과 검증 |
| 7. 통합 | RCS 문자열/타입 동기화, 저속 실차 및 지터 측정 | 거리·각도 허용 오차 충족, 단절 시 PLC 정지 확인 |

검증은 모의 PLC와 기록 샘플에서 시작한다. 실차 한계·오차·시간 임계값은 확정된 사양을 사용한다. 핵심 사례는 다음과 같다.

- INT16 음수·DINT 복원·수신 각도 250→2.50°→rad 및 DM109 송신 25°→2500·NaN/Inf 거절·거리/속도 한계.
- 정확히 40/50워드 읽기, 예약 영역 미사용, DM100/101 동시 쓰기, DM102 쓰기 없음, WRS가 DM104를 우발적으로 쓰지 않음.
- 일반 값 동일 시 쓰기 생략, 변경 쓰기 실패 후 처리, reset=1 두 번의 독립 요청, 로컬 reset=0 미송신, 재접속 이전 명령 미재실행.
- LIFE 변화·wrap·소켓 정상 중 LIFE 정지, 3초 경계, PC LIFE 단절에 대한 PLC 정지, 제어 스레드 정지 시 생존 신호 정책.
- 좌표 변환·좌우 휠 라벨·각도 영점, 곡선 경로 누적, 엔코더 초기화/점프, odom 초기화.
- 목표 속도 0에서 파라미터 가감속 사용, 짧은 이동, 후진, 도달 후 잔류 속도, 정지/취소/재개.
- jerk_ratio=1.0에서 가속도 0으로 시작해 1초 후 목표 가속도의 100% 도달, 후진 가속 음수/감속 양수, 양방향 정지 시 속도 부호 초과 없음.
- BODY 원주 방향 일치/불일치, 전진/후진의 다음 스캔 각도차, DM109 rad→0.01deg 변환과 작은 각도 양자화, 휠별 지령 미생성.
- PLC HOLD 직접 전달, STATE=16 진입 한 번/유지 중 0회/재진입 한 번 초기화, 장애물 값 0/1/2/3에 대한 정상/정지/감속/정지 처리.
- PLC OK 전 로컬 RESET 유지·OK 후 로컬만 0, watchdog 파라미터 변경 시 경계 동작, E-STOP 쓰기 없음, DM107이 도달 판정에 영향 없음.
- PAUSE 요청→state=PAUSE/lap_state=PAUSED, RUNNING 재개, 캘리브레이션 STOP→FAILED. 일반 STOP은 확정 정책에 맞춰 시험.
- DM030/031/062 상태 및 시작 에지, 수동 200 ms 수신 갱신과 별도 만료, PLC 수동 리프트 속도 유지.
- QR subscriber 없음과 미지원 명령 거절, 센서 정렬을 QR 원점 성공으로 오인하지 않음, level_state=-1 유지.
- RCS/AGV 동일 SetJob 타입, SetMvLift 생성 여부, 기존 문자열·주석 갱신, 10 ms 루프 시간·지터·샘플 나이.
- 계산 모듈을 ROS 노드·PLC 없이 단위 테스트한다. 좌표 변환, 각도 경계, JERK 100%, 후진 프로파일, 정지 거리, 휠 이동량 추정, 잘못된 입력의 실패 반환을 검증한다.

구현 이후 사용할 기본 명령:

```bash
cd /home/rnd08/ros2_ws
source /opt/ros/humble/setup.bash
colcon build --packages-up-to steer_agv_controller steer_agv_description steer_agv_bringup
source install/setup.bash
colcon test --packages-select steer_agv_controller
colcon test-result --verbose
```

위 패키지와 테스트는 아직 구현하지 않았다. 이번 검증은 최신 원본과 계획의 내용 대조 및 문서 일관성 확인이다.

## 10. 다음 단계 체크리스트

- [x] 노드 이름·0.98 m/s·TCP 접속·40/50워드 읽기·0.1 스케일·DINT 반영.
- [x] 반경/standoff 매핑, 기본값 1.25/1.0/0.6 m, 속도 0용 가감속 파라미터 반영.
- [x] 실제 SetJob.radius 추가 확인, lift_control 이름 통일 및 RCS 로봇 허가 책임 반영.
- [x] PLC LIFE 1 ms/감시 3초, 변경 시 쓰기, RESET 로컬 0 미송신 규칙 반영.
- [x] DM030/031/062, MODE 쓰기 삭제, STATE=16, level_state=-1 반영.
- [x] PAUSE 명령/state와 PAUSED lap_state, 캘리브레이션 STOP=FAILED 반영.
- [x] 새 휠·센서 좌표, QR 구독 후속 범위, 현재 ALIGN 센서 추론 요구 반영.
- [x] +Y 전방/+X 우측과 ROS 변환, 다음 스캔 원주 대비 BODY 각도차, PLC 휠 제어 책임 반영.
- [x] jerk 100%(jerk_ratio=1.0)의 1초 기준·수치 예시, 후진 가감속 부호 반영.
- [x] DM107 단순 전달·odom 도달 판정, PLC HOLD 직접 보고, E-STOP 쓰기 삭제 확인.
- [x] PLC OK 확인, watchdog 파라미터화, 정지 bit0/감속 bit1·경고 정책, INITIALIZE 진입 이벤트 반영.
- [x] 수식은 agv_math.hpp/agv_math.cpp로 분리하고 제어·odom 모듈에서 호출하는 구조 채택.
- [ ] 센서 광축·휠 피드백 각도 매핑, 경로 회전 방향 설정, BODY 각도차 양자화 검증.
- [ ] 가감속 기본 수치·감속 속도 제한·watchdog 세부 파라미터 값 설정.
- [ ] PLC RESET 내부 소비·반복 COMMAND·DM105 요청 규칙, ACK 불명확 시 재시도 정책 확인.
- [ ] 장치별 수동 모드 허용표 확정.
- [ ] 일반 STOP 작업 보존, INITIALIZE 외부 상태, 취소/중복 job_id 계약 확정.
- [ ] QR 없는 ALIGN 완료/원점 플래그 계약, MeasureLap 반경 선택·파라미터 유지 정책 확정.
- [ ] SetMvLift 생성 목록 등록, RCS/AGV 재빌드 및 PAUSE/PAUSED 의미·주석 동기화.
- [ ] 모의 PLC부터 구현·검증 진행.
- [ ] 계산 모듈 구현, CMake 라이브러리 연결 및 수식 단위 테스트 추가.

체크 완료는 문서 반영 또는 원본 수정 확인을 뜻하며 코드·실차 검증 완료를 뜻하지 않는다.
