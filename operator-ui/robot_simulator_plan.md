# Robot Simulator 구현 계획

## 1. 목적

Robot Simulator는 실제 AMR과 Cobot 없이 검사 동작과 좌표 변화를 재현하는 화면 없는 백그라운드 Worker이다.

주요 역할은 다음과 같다.

- 검사대상의 지름과 높이를 기준으로 전체 검사 경로를 생성한다.
- AMR이 검사대상 외곽을 따라 약 500 mm 간격으로 이동하는 동작을 재현한다.
- AMR 정지 후 Cobot이 800 × 800 mm 영역을 100 mm 간격의 `ㄹ`자 경로로 검사하는 동작을 재현한다.
- AMR 위치와 Cobot 검사점을 기준으로 `tcp_x`, `tcp_y`, `tcp_z`를 계산한다.
- Master Controller와 잠금이 적용된 공유 객체로 명령과 상태를 교환한다.
- MQTT와 별도의 Simulator UI는 사용하지 않는다.

---

## 2. 기본 동작 순서

하나의 검사 위치에서 다음 순서를 반드시 지킨다.

```text
Job 정보 확인
→ AMR 이동
→ AMR 정지
→ 정지 안정화 대기
→ Cobot 검사 자세 진입
→ 800 × 800 mm ㄹ자 검사
→ Cobot 안전 위치 복귀
→ 다음 AMR 위치로 이동
```

AMR 이동과 Cobot 검사는 동시에 수행하지 않는다.

전체 검사 순서는 다음과 같다.

```text
검사 경로 계산
→ 원주 방향 AMR 위치 선택
→ AMR 이동 및 정지
→ 첫 번째 높이 Band로 리프트 이동
→ Cobot 면 검사
→ 다음 높이 Band로 리프트 상승
→ 검사대상 최대 높이까지 Cobot 면 검사 반복
→ Cobot 안전 위치 복귀
→ 리프트 최저 위치로 하강
→ 원주 다음 위치로 이동
→ 마지막 검사 위치 완료 후 시작점으로 복귀
→ 원주 1회전이 닫히면 전체 검사 완료
```

---

## 3. 단위와 좌표계

### 3.1 내부 단위

Simulator 내부 계산은 다음 단위로 통일한다.

| 항목 | 단위 |
|---|---|
| 거리 | mm |
| 각도 계산 | rad |
| 화면 표시 각도 | degree |
| 시간 | ms |
| 속도 | mm/s |

Operator UI가 미터 단위를 사용하면 Master Controller에서 mm를 m로 변환한다.

### 3.2 World 좌표계

검사대상은 수직 원통으로 가정한다.

```text
원점             검사대상 바닥 중심
World X축        검사 시작점 방향
World Y축        X축에서 반시계 방향으로 90도
World Z축        바닥에서 위쪽 방향
원주 각도 θ      +X축에서 반시계 방향
```

검사대상 반지름:

```text
R = diameter_mm / 2
```

### 3.3 좌표 출력 기준

공유 상태의 `tcp_x`, `tcp_y`, `tcp_z`는 **World 좌표계의 mm 값**으로 정의한다.

Cobot Base 기준 좌표는 작업 반경 3,000 mm 이내인지 검사하기 위한 내부 계산에만 사용한다. 기본 공유 상태에는 Base 기준 TCP 좌표를 노출하지 않는다.

---

## 4. 필수 입력값

### 4.1 Job 정보

```python
JobInfo(
    job_id: str,
    diameter_mm: float,
    height_mm: float,
    thickness_mm: float,
    target_distance_mm: float,
)
```

현재 좌표 계산에서 직접 사용하는 값은 다음과 같다.

- `diameter_mm`: 원주 이동 각도 및 표면 좌표 계산
- `height_mm`: 높이 방향 검사 Band 계산

`thickness_mm`와 `target_distance_mm`는 Job 상태에 보관하며, 실제 장비 모델이 확정되면 충돌 검사와 접근 거리 계산에 사용한다.

### 4.2 캘리브레이션 정보

지름과 높이만으로는 AMR 중심과 Cobot Base 위치를 유일하게 계산할 수 없다. 다음 값은 장비 실측 또는 설계값으로 반드시 추가해야 한다.

```python
RobotCalibration(
    amr_surface_clearance_mm: float,
    amr_center_height_mm: float,
    cobot_base_offset_x_mm: float,
    cobot_base_offset_y_mm: float,
    cobot_base_offset_z_mm: float,
    tool_surface_offset_mm: float,
    cobot_work_radius_mm: float,
    lift_min_height_mm: float,
    lift_max_height_mm: float,
)
```

| 값 | 확정값 | 의미 |
|---|---:|---|
| `amr_surface_clearance_mm` | `1000` | 검사대상 표면에서 AMR 중심까지의 거리 |
| `amr_center_height_mm` | `0` | World 바닥에서 AMR 기준 원점까지의 높이 |
| `cobot_base_offset_x_mm` | `0` | AMR 기준 Cobot Base X 오프셋 |
| `cobot_base_offset_y_mm` | `0` | AMR 기준 Cobot Base Y 오프셋 |
| `cobot_base_offset_z_mm` | `800` | 리프트 높이가 0일 때 Cobot Base 높이 |
| `tool_surface_offset_mm` | `0` | TCP가 검사대상 표면에 붙어 있다고 가정 |
| `cobot_work_radius_mm` | `3000` | Cobot Base에서 TCP까지의 최대 작업 반경 |
| `lift_min_height_mm` | `0` | 리프트 최저 위치 |
| `lift_max_height_mm` | `[확정 필요]` | 리프트가 이동할 수 있는 최대 높이 |

Cobot Base는 리프트에 부착되어 있으므로 World Z 좌표는 다음과 같다.

```text
cobot_base_z = 800 + lift_height_mm
```

### 4.3 동작 설정값

```python
SimulationConfig(
    amr_step_mm=500,
    amr_velocity_mm_s=300,
    amr_acceleration_mm_s2=300,
    amr_settle_time_ms=1000,
    scan_width_mm=800,
    scan_height_mm=800,
    scan_interval_mm=100,
    cobot_velocity_mm_s=150,
    inspection_start_z_mm=500,
)
```

| 설정 | 확정값 |
|---|---:|
| AMR 이동 간격 | `500 mm` |
| AMR 이동 속도 | `300 mm/s` |
| AMR 가속도 | `300 mm/s²` |
| AMR 정지 안정화 시간 | `1,000 ms` |
| Cobot 검사 속도 | `150 mm/s` |
| 검사 시작 높이 | 지면에서 `500 mm` |
| Cobot 검사점 정지시간 | `[확정 필요]` |

---

## 5. AMR 원주 이동 계획

### 5.1 이동 거리

AMR은 검사대상 외곽을 따라 자신의 중심 경로를 기준으로 원주 방향 500 mm씩 이동한 후 정지한다. AMR 중심은 검사대상 표면에서 1,000 mm 떨어져 있다.

```text
검사대상 반지름 R = diameter_mm / 2
AMR 이동 반지름 R_amr = R + 1000
AMR 이동 원주 C_amr = 2π × R_amr
기본 이동 거리 S = 500 mm
원주 검사 위치 수 N = ceil(C_amr / S)
```

각 검사 위치의 원주 누적 거리는 다음과 같다.

```text
s_i = i × 500
i = 0, 1, ..., N - 1
```

각 위치의 원주 각도:

```text
θ_i = s_i / R_amr
```

마지막 위치에서 시작점까지 남은 거리는 500 mm 이하가 된다.

```text
마지막 복귀 거리 = C_amr - ((N - 1) × 500)
```

시작점과 동일한 `s = C_amr` 위치는 중복 검사하지 않는다.

마지막 검사 위치의 Cobot 검사와 리프트 하강이 끝나면 `마지막 복귀 거리`만큼 이동해 시작점으로 돌아온다. 이 복귀 위치에서는 Cobot 검사를 다시 수행하지 않는다.

AMR 중심이 500 mm 이동할 때 검사대상 표면에서 두 검사 중심점 사이의 호 길이는 다음과 같다.

```text
surface_step = R × (500 / R_amr)
```

`R_amr`가 `R`보다 크므로 표면 검사 중심 간격은 500 mm 이하이며, 800 mm 폭의 Cobot 검사 영역이 서로 겹쳐 미검사 구간이 생기지 않는다.

### 5.2 AMR World 좌표

AMR 중심의 회전 반지름:

```text
R_amr = R + amr_surface_clearance_mm
```

AMR 위치:

```text
amr_x = R_amr × cos(θ_i)
amr_y = R_amr × sin(θ_i)
amr_z = amr_center_height_mm
```

AMR은 항상 검사대상 중심을 바라본다고 가정한다.

```text
amr_yaw = normalize(θ_i + π)
```

### 5.3 AMR 이동 보간

한 위치에서 다음 위치로 순간 이동하지 않고 최대 속도 `300 mm/s`, 가속도 `300 mm/s²`의 사다리꼴 또는 삼각형 속도 프로파일로 좌표를 보간한다.

```text
angle_velocity = amr_velocity_mm_s / R_amr
θ(t + dt) = θ(t) + angle_velocity × dt
```

다음 검사 위치에 도착하면 다음 조건을 적용한다.

- 속도 `0 mm/s`
- 상태 `AMR_STOPPED`
- 안정화 시간 `1,000 ms` 대기
- 안정화 완료 후에만 Cobot 검사 시작

---

## 6. Cobot 800 × 800 mm 검사 경로

### 6.1 검사 영역

한 AMR 정지 위치에서 검사하는 표면 영역:

```text
가로 800 mm
세로 800 mm
검사점 간격 100 mm
```

각 방향의 검사점 수:

```text
point_count = (800 / 100) + 1 = 9
```

한 면의 전체 검사점 수:

```text
9 × 9 = 81 points
```

가로 좌표 `u`는 현재 AMR 정지 위치를 중심으로 하는 원주 접선 방향 거리다.

```text
u = -400, -300, -200, -100, 0, 100, 200, 300, 400
```

세로 좌표 `v`는 현재 높이 Band 시작점으로부터의 거리다.

```text
v = 0, 100, 200, 300, 400, 500, 600, 700, 800
```

### 6.2 ㄹ자 경로

첫 번째 행과 모든 짝수 행은 왼쪽에서 오른쪽으로 검사한다.

```text
-400 → -300 → ... → 400
```

홀수 행은 오른쪽에서 왼쪽으로 검사한다.

```text
400 → 300 → ... → -400
```

행 끝에서는 Z 방향으로 100 mm 이동한 뒤 반대 방향으로 검사한다.

```text
Row 0:  -400 →  400
                    ↓ 100
Row 1:   400 → -400
                    ↓ 100
Row 2:  -400 →  400
                    ↓
...
Row 8:  -400 →  400
```

한 면에서 TCP가 이동하는 기본 경로 길이는 방향 전환을 제외하면 다음과 같다.

```text
가로 이동 = 9 rows × 800 mm = 7,200 mm
세로 이동 = 8 transitions × 100 mm = 800 mm
합계 = 8,000 mm
```

---

## 7. 높이 방향 검사 Band

검사는 지면에서 500 mm 떨어진 위치부터 시작한다.

```text
inspection_min_z = 500
inspection_max_z = height_mm
inspection_height = height_mm - 500
```

`height_mm`가 500 이하이면 검사 가능한 높이가 없으므로 Job을 거부한다.

검사 가능 높이가 800 mm보다 크면 여러 높이 Band로 나눈다.

```text
band_count = ceil(inspection_height / 800)
```

검사 가능 높이가 800 mm 이상이면 각 Band가 대상 높이를 벗어나지 않도록 마지막 Band를 아래로 이동해 이전 Band와 겹치게 한다.

```text
z_start(k) = min(500 + k × 800, height_mm - 800)
z_end(k) = z_start(k) + 800
```

예를 들어 높이가 2,500 mm이면 다음과 같다.

```text
Band 0: z =  500 ~ 1300
Band 1: z = 1300 ~ 2100
Band 2: z = 1700 ~ 2500
```

마지막 Band는 이전 Band와 일부 겹치지만 검사대상 위쪽으로 벗어나지 않는다.

검사 가능 높이가 800 mm 미만이면 하나의 축소 Band를 생성한다.

```text
z_start = 500
z_end = height_mm
v = 0, 100, 200, ... , inspection_height
```

마지막 높이가 정확히 100 mm 간격에 포함되지 않으면 `height_mm`를 마지막 검사점으로 추가한다.

### 7.1 리프트 높이

각 Band의 중심 높이와 Cobot Base 높이를 맞추도록 리프트 목표 높이를 계산한다.

```text
band_center_z = (z_start + z_end) / 2
lift_height = band_center_z - cobot_base_offset_z_mm
lift_height = band_center_z - 800
```

계산된 리프트 높이는 `lift_min_height_mm ~ lift_max_height_mm` 범위 안에 있어야 한다. 범위를 벗어나면 해당 Job을 실행하지 않고 경로 생성 오류로 처리한다.

### 7.2 Band 검사 순서

하나의 AMR 정지 위치에서 모든 높이 Band를 검사한다.

```text
AMR Station 0
  ├─ Band 0 검사
  ├─ Lift 상승
  ├─ Band 1 검사
  ├─ ...
  └─ 마지막 Band 검사 후 Lift 최저 위치 복귀

→ AMR Station 1로 이동
```

---

## 8. TCP World 좌표 역산

현재 AMR 검사 위치의 중심 각도를 `θ_i`, Cobot 검사점의 원주 접선 방향 거리를 `u`, 높이를 `z`라고 한다.

원통 표면의 검사점 각도:

```text
α = θ_i + (u / R)
```

표면에서 TCP까지의 법선 방향 오프셋을 적용한 TCP 반지름:

```text
R_tcp = R + tool_surface_offset_mm
```

World 기준 TCP 좌표:

```text
tcp_x = R_tcp × cos(α)
tcp_y = R_tcp × sin(α)
tcp_z = z_start + v
```

이 계산은 800 mm 가로 거리를 단순 직선이 아니라 원통 표면의 호 길이로 적용한다. 따라서 지름이 달라져도 TCP가 항상 원통 표면을 따라 이동한다.

검사 센서가 표면 바깥쪽에서 원통 중심 방향을 바라본다면 Tool 방향은 다음 법선 벡터를 기준으로 계산한다.

```text
normal_x = -cos(α)
normal_y = -sin(α)
normal_z = 0
```

초기 단계에서는 Cobot Tool이 검사 표면에 붙어 있다고 가정하고 `tcp_x`, `tcp_y`, `tcp_z` 위치만 계산한다. Tool 자세 `rx`, `ry`, `rz`는 계산하지 않는다.

---

## 9. Cobot 작업 반경 검증

공유 상태에는 World TCP 좌표만 저장하지만, 각 검사점이 Cobot 작업 반경 3,000 mm 안에 있는지 확인하기 위해 Base 기준 거리를 계산한다.

AMR Yaw를 `ψ`, Cobot Base의 World 좌표를 `(base_x, base_y, base_z)`라고 한다.

```text
dx = tcp_x - base_x
dy = tcp_y - base_y
dz = tcp_z - base_z
```

World 좌표를 Base 좌표로 역회전한다.

```text
tcp_base_x =  cos(ψ) × dx + sin(ψ) × dy
tcp_base_y = -sin(ψ) × dx + cos(ψ) × dy
tcp_base_z = dz
```

Cobot Base의 World 좌표는 AMR 위치, AMR Yaw, 리프트 높이와 Base 오프셋을 조합해 계산한다.

```text
base_x = amr_x
base_y = amr_y
base_z = 800 + lift_height
```

작업 반경:

```text
reach_distance =
    sqrt(tcp_base_x² + tcp_base_y² + tcp_base_z²)
```

다음 조건을 만족해야 한다.

```text
reach_distance <= 3000
```

하나라도 작업 반경을 벗어나는 Waypoint가 있으면 검사 시작 전에 Job 전체를 거부하고 어떤 Band와 검사점이 범위를 벗어났는지 기록한다.

---

## 10. Simulator 상태 머신

```text
IDLE
  └─ LOAD_JOB
      ↓
READY
  └─ RUN
      ↓
AMR_MOVING
  └─ 위치 도착
      ↓
AMR_SETTLING
  └─ 안정화 완료
      ↓
LIFT_POSITIONING
  └─ 현재 높이 Band 도착
      ↓
COBOT_APPROACHING
  └─ 검사 시작점 도착
      ↓
COBOT_SCANNING
  └─ 현재 Band 검사점 완료
      ↓
COBOT_RETRACTING
  ├─ 다음 높이 Band 존재 → LIFT_POSITIONING
  └─ 현재 Station의 전체 높이 완료 → LIFT_RETURNING
      ↓
LIFT_RETURNING
  ├─ 다음 원주 위치 존재 → AMR_MOVING
  └─ 마지막 검사 위치 완료 → AMR_RETURNING_HOME
      ↓
AMR_RETURNING_HOME
  └─ 시작점 도착 → COMPLETED
```

모든 실행 상태에서 다음 명령을 처리한다.

| 명령 | 처리 |
|---|---|
| `STOP` | 현재 위치와 검사점 번호를 보존하고 `PAUSED` |
| `EMS` | 이동을 즉시 중단하고 `EMERGENCY_STOP` |
| `RESET` | 오류 또는 EMS를 해제하고 보존된 위치의 `PAUSED` 상태로 전환 |
| `JOB_CLEAR` | 경로와 진행 상태를 삭제하고 `IDLE` |
| `SHUTDOWN` | Worker 루프를 안전하게 종료 |

`STOP` 또는 `EMS` 시 AMR 위치, 리프트 높이, 높이 Band, Cobot 행과 열 인덱스를 보존한다. `EMS` 해제 후에는 자동 재개하지 않고 `PAUSED`에서 다음 `RUN` 명령을 기다린다.

---

## 11. 공유 명령과 상태

### 11.1 공유 명령

```python
class RobotCommand(Enum):
    NONE = "none"
    LOAD_JOB = "load_job"
    RUN = "run"
    STOP = "stop"
    EMS = "ems"
    RESET = "reset"
    JOB_CLEAR = "job_clear"
    SHUTDOWN = "shutdown"
```

각 명령에는 증가하는 `command_sequence`를 부여해 같은 명령을 중복 실행하지 않는다.

### 11.2 공유 상태 스냅샷

```python
@dataclass(frozen=True)
class RobotSnapshot:
    simulator_state: RobotState
    job_id: str | None

    amr_x: float
    amr_y: float
    amr_z: float
    amr_yaw: float
    amr_velocity: float

    lift_height: float
    cobot_base_z: float

    tcp_x: float
    tcp_y: float
    tcp_z: float

    height_band_index: int
    height_band_count: int
    amr_position_index: int
    amr_position_count: int
    scan_row_index: int
    scan_column_index: int
    progress_percent: float

    paused: bool
    emergency_stopped: bool
    error_code: str | None
    updated_at_ms: int
```

### 11.3 공유 객체

`SharedRobotContext`가 다음 객체를 소유한다.

- `JobInfo`
- `RobotCalibration`
- 최신 `RobotSnapshot`
- 최신 명령과 `command_sequence`
- `threading.RLock`
- `threading.Condition`
- 종료용 `threading.Event`

Master Controller와 Robot Simulator Worker는 공유 객체의 메서드만 사용하고 내부 변수를 직접 수정하지 않는다.

---

## 12. Worker 실행 구조

Robot Simulator에는 UI가 없으며 `QThread`에 연결된 Worker로 실행한다.

```text
Main Thread
├─ QApplication
├─ MasterController
└─ OperatorWindow

Robot Simulator QThread
└─ RobotSimulatorWorker
   ├─ 명령 대기
   ├─ 상태 머신 실행
   ├─ AMR 좌표 보간
   ├─ Cobot 경로 보간
   └─ 공유 Snapshot 갱신
```

Worker는 GUI 객체에 직접 접근하지 않는다.

상태 데이터는 공유 Snapshot에 기록하며, Qt Signal은 Master Controller에 상태 변경을 알리는 용도로만 사용한다.

---

## 13. 경로 생성 결과 모델

검사를 시작하기 전에 전체 경로를 생성한다.

```python
@dataclass(frozen=True)
class AmrStation:
    index: int
    arc_distance_mm: float
    angle_rad: float
    x_mm: float
    y_mm: float
    yaw_rad: float


@dataclass(frozen=True)
class TcpWaypoint:
    band_index: int
    station_index: int
    row_index: int
    column_index: int
    u_mm: float
    v_mm: float
    tcp_x_mm: float
    tcp_y_mm: float
    tcp_z_mm: float


@dataclass(frozen=True)
class HeightBand:
    index: int
    z_start_mm: float
    z_end_mm: float
    lift_height_mm: float
```

경로 생성기:

```python
InspectionPathPlanner.create_plan(
    job_info,
    calibration,
) -> InspectionPlan
```

좌표 생성과 시간에 따른 이동 시뮬레이션을 분리해 좌표 공식을 독립적으로 테스트한다.

---

## 14. 진행률 계산

전체 Cobot 검사점 수:

```text
total_points =
    amr_position_count
    × 각 높이 Band의 검사점 수 합계
```

800 mm 전체 Band는 81개 검사점을 갖는다. 검사 가능 높이가 800 mm 미만인 축소 Band는 실제로 생성된 검사점 수를 사용한다.

검사 진행률:

```text
progress_percent =
    completed_scan_points
    / total_points
    × 100
```

AMR 이동 중에는 검사점 완료 개수가 증가하지 않는다. 별도로 현재 이동 및 검사 단계를 상태에 표시한다.

---

## 15. 검증 항목

### 15.1 경로 계산 테스트

- AMR 이동 반지름으로 계산한 원주 검사 위치 수가 `ceil(2πR_amr / 500)`과 일치한다.
- 모든 일반 AMR 이동 거리는 500 mm이고 마지막 복귀 거리는 500 mm 이하이다.
- 시작점과 끝점이 중복 생성되지 않는다.
- 마지막 검사 후 시작점 복귀 이동에서는 Cobot 검사를 실행하지 않는다.
- AMR 좌표가 `R_amr` 반지름 위에 있다.
- AMR Yaw가 항상 검사대상 중심을 향한다.
- 한 검사 면에 정확히 81개의 TCP 검사점이 생성된다.
- 인접 검사점 간격이 100 mm다.
- 행마다 X 또는 원주 방향 진행 순서가 반전된다.
- 모든 TCP 점이 설정된 원통 반지름 위에 있다.
- `tcp_z`가 `500 ~ height_mm` 범위를 벗어나지 않는다.
- 모든 Waypoint와 Cobot Base 사이의 거리가 3,000 mm 이하이다.
- 계산된 리프트 높이가 최소·최대 이동 범위 안에 있다.

### 15.2 상태 머신 테스트

- AMR 이동 중 Cobot 검사가 시작되지 않는다.
- AMR 정지와 안정화 완료 후에만 Cobot이 작동한다.
- 현재 Station의 모든 높이 Band 검사와 리프트 하강이 완료된 후에만 AMR이 이동한다.
- `STOP` 시 현재 Station과 검사점 인덱스가 보존된다.
- `EMS` 상태에서 `RUN` 명령이 거부된다.
- `RESET` 후 보존된 위치의 `PAUSED` 상태로 전환된다.
- `JOB_CLEAR` 후 경로와 진행률이 초기화된다.
- `SHUTDOWN` 명령으로 QThread가 정상 종료된다.

### 15.3 좌표 연동 테스트

- 공유 Snapshot의 `amr_x`, `amr_y`, `amr_yaw`가 현재 Station과 일치한다.
- 공유 Snapshot의 `tcp_x`, `tcp_y`, `tcp_z`가 현재 Waypoint와 일치한다.
- 검사대상 지름 변경 시 AMR 각도와 TCP 원주 좌표가 다시 계산된다.
- 검사대상 높이 변경 시 높이 Band와 TCP Z 좌표가 다시 계산된다.

---

## 16. 구현 순서

1. 명령과 상태 Enum 정의
2. `JobInfo`, `RobotCalibration`, `RobotSnapshot` 작성
3. `SharedRobotContext`와 잠금 규칙 작성
4. AMR 원주 Station 생성기 구현
5. 높이 Band 생성기 구현
6. Band별 리프트 목표 높이 및 이동 경로 구현
7. Cobot `ㄹ`자 Waypoint 생성기 구현
8. World TCP 좌표와 Cobot 작업 반경 검증 구현
9. 상태 머신 구현
10. 시간 기반 AMR·리프트·Cobot 이동 보간 구현
11. 일시정지, EMS, Reset, Job Clear 구현
12. 경로와 상태 머신 단위 테스트 작성
13. Master Controller 연동
14. Operator UI 상태 표시 연동

---

## 17. 확정된 설계 조건

| 항목 | 확정 내용 |
|---|---|
| AMR 표면 이격거리 | `1,000 mm` |
| Cobot Base 오프셋 | AMR 기준 `(0, 0, 800) mm`, 리프트에 부착 |
| Cobot 작업 가능 반경 | `3,000 mm` |
| TCP 표면 오프셋 | `0 mm`, Tool이 검사 면에 붙어 있다고 가정 |
| TCP 출력 좌표계 | World 좌표 |
| 원주 진행 방향 | World 각도 증가 방향 |
| 원주 최초 이동 방향 | 시작 위치에서 좌측에서 우측 방향 |
| Cobot 첫 행 검사 방향 | 왼쪽에서 오른쪽 |
| 검사 시작 높이 | 지면에서 `500 mm` |
| 높이 변경 방식 | 한 Station에서 리프트를 상승시키며 Cobot 검사 반복 |
| 원주 위치 변경 조건 | 전체 높이 검사 및 리프트 하강 완료 |
| AMR 이동 속도 | `300 mm/s` |
| AMR 가속도 | `300 mm/s²` |
| AMR 정지 안정화 시간 | `1초` |
| Cobot 검사 속도 | `150 mm/s` |
| Cobot Tool 자세 | `rx`, `ry`, `rz` 없이 위치 좌표만 계산 |
| EMS 해제 상태 | 보존된 위치에서 `PAUSED`, `RUN` 명령 대기 |

---

## 18. 추가 확정이 필요한 항목

- [ ] 리프트 최대 높이
- [ ] 리프트 상승 및 하강 속도와 가속도
- [ ] Cobot이 각 검사점에서 정지하는 시간
- [ ] Cobot 검사 시작점 진입 및 안전 위치 복귀에 사용할 기준 좌표
- [ ] 검사대상 최대 허용 높이
- [ ] Cobot 작업 반경을 벗어난 Job을 수정할지 즉시 거부할지
- [ ] 축소 Band의 마지막 간격이 100 mm보다 작을 때 허용할지
