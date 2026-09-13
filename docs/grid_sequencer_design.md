# 격자 자동 진행 상태 머신 설계

`job_cmd`(작업 계획 수신) 이후 `1A`부터 `12F`까지 전부 스캔하는 것은 **이 RCS(Operator UI)의 책임**이다. 이 문서는 그 진행을 담당하는 상태 머신을 설계한다.

## 1. 작업 모델

원통을 펼친 직사각형을 격자로 나눈다. 열은 AMR이 정차하는 원주 구역, 행은 리프트 높이다.

```
 F │ 1F │ 2F │ 3F │ …  │11F │12F │
 … │ 1… │ 2… │ 3… │ …  │11… │12… │
 B │ 1B │ 2B │ 3B │ …  │11B │12B │
 A │ 1A │ 2A │ 3A │ …  │11A │12A │
   └────┴────┴────┴────┴────┴────┘
     1    2    3    …   11   12      ← AMR 정차 구역 (열)
```

- **열(1~12)** = AMR이 원주를 돌며 정차하는 위치. 차량은 각 구역 **중앙**에 선다.
- **행(A~F)** = 리프트 높이. 한 열 안에서 리프트만 올리며 A → B → … → F로 올라간다.
- **셀 하나(예: `1A`)** = Cobot이 ㄹ자로 훑는 작은 직사각형 영역.

### 핵심: 로봇은 항상 같은 좌표로 움직인다

리프트가 올라가면 로봇 기준 좌표계도 통째로 올라간다. 그래서 **로봇 입장에서 `1A`와 `1B`는 완전히 같은 동작**이다. 바뀌는 것은 리프트 높이뿐이다.

이 사실이 설계에 주는 결과:

- 로봇에게 보내는 작업 영역 값(`app_width`/`app_height`/`scan_h`/`overlap`, 레지스터 256~259)은 **작업 시작 때 한 번만 쓰면 되고, 셀이 바뀌어도 다시 쓸 필요가 없다.** (이전 설계에서 셀마다 다시 쓴다고 본 것은 틀렸다.)
- 시퀀서가 셀마다 하는 일은 **리프트를 한 칸 올리고 → 로봇에 작업 시작 신호를 보내는 것** 두 가지뿐이다.

### 한 사이클

```
AMR이 1구역 중앙에 정차
  └ 리프트 A 높이 → 로봇 작업 시작 → 로봇이 1A를 ㄹ자로 스캔 → 홈 복귀 후 정지
  └ 리프트 B 높이 → 로봇 작업 시작 → 1B 스캔 → 홈 복귀 후 정지
  └ …
  └ 리프트 F 높이 → 로봇 작업 시작 → 1F 스캔 → 홈 복귀 후 정지
AMR이 2구역으로 이동 → 리프트 A 높이로 하강 → 2A부터 반복
  …
12F까지 끝나면 전체 작업 완료
```

### 두 종류의 "행"을 혼동하지 말 것

| | 무엇 | 몇 개인가 | 누가 관리 |
|---|---|---|---|
| **격자 행 (A~F)** | 리프트 높이 단계. 셀 단위 | MQTT `row_count` | **RCS 시퀀서** (리프트 제어) |
| **ㄹ자 패스** | 셀 하나 안에서 스캐너가 좌우로 훑는 줄 | 로봇이 `cell_height`/`scan_h`/`overlap`로 계산 → 레지스터 292 | **로봇 태스크** |

`RectWorkView.plan_dimensions()["rows"]`가 계산하는 것은 **아래쪽(ㄹ자 패스)**이지 격자 행이 아니다. 격자 행 수는 계산하지 않고 MQTT가 준 `row_count`를 그대로 쓴다.

## 2. MQTT 작업 계획 (`job_cmd`)

외부(MC)가 보내는 것은 **격자 분할 정보 + 셀 크기 + 겹침 허용치**다. 지금 몇 번째 셀인지는 보내지 않는다 — 순회는 RCS가 한다.

```json
{
  "timestamp": "1784727779111",
  "job_id": "jb00000001",
  "job_info": { "diameter": "2500", "height": "6000",
                "target_distance": "8560" },
  "plan": {
    "column_count": "12",
    "row_count": "6",
    "cell_width": "600",
    "cell_height": "800",
    "overlap": "20"
  }
}
```

| 필드 | 의미 | 쓰이는 곳 |
|---|---|---|
| `plan.column_count` | AMR 정차 구역 수 (열) | 시퀀서 순회 범위, OrbitView |
| `plan.row_count` | 리프트 단계 수 (행 A~F) | 시퀀서 순회 범위, 셀 이름 |
| `plan.cell_width` | 셀 하나의 가로 폭 (mm) | 로봇 `app_width`(256) |
| `plan.cell_height` | 셀 하나의 세로 높이 (mm) | 로봇 `app_height`(257), **리프트 상승 피치 계산** |
| `plan.overlap` | 겹침 허용 (mm) | 로봇 `overlap`(259), **리프트 상승 피치 계산** |

**리프트 상승 피치 = `cell_height - overlap`.** 행 A→B로 갈 때 리프트가 올라가는 양이다. 셀 안 ㄹ자 패스 피치(`scan_h - overlap`)와 같은 `overlap` 값을 쓴다 — "겹침 허용 정도"는 한 값이라고 보았다.

**`scan_h`는 MQTT로 받지 않는다.** UT 스캐너가 한 자리에서 훑는 세로 유효높이는 **장비 고유값**이지 작업 계획이 아니므로, UT 설정 화면의 로컬 설정으로 둔다. 로봇에게 레지스터 258로 보내는 것은 그대로지만 출처가 MQTT가 아니라 로컬 설정이다.

## 3. 로봇에서 이미 나오는 신호

`robot_task/scripts/*.script`를 grep해 확인한 결과, 로봇 태스크가 진행 상태를 레지스터 290~298에 이미 쓰고 있다. 지금까지 아무도 읽지 않았을 뿐이다.

| 레지스터 | 의미 | 쓰는 곳 |
|---|---|---|
| `290` | 상태 | `0`=대기, `1`=탐색(`dus_seek_begin`), `2`=probe(`dus_probe_c`), `3`=원점복귀(`dus_goto_zero`), `4`=ㄹ자 스캔 중, `5`=**완료**(`dus_finish`, `dus_finish_end`), `9`=기타 |
| `291` | `row_idx` | 셀 안에서 지금 몇 번째 ㄹ자 패스인지 |
| `292` | `rows` | 이 셀의 전체 ㄹ자 패스 수 |
| `293` | `alive` | 동작 중 증가하는 heartbeat |
| `294` | `zero_ok` | 원점 설정 여부 |
| `295` | `finished` | `1`이면 이번 셀 스캔 완료 |
| `296` | `pitch` | ㄹ자 상승 피치 |
| `298` | 진행률 | 완료 시 `100` |

**"이 셀 끝났다" = `290 == 5` && `295 == 1`.** 시퀀서가 기다릴 신호는 이것이다.

## 4. 상태 머신 (`JobSequencer`)

`operator-ui/src/smr_operator_ui/services/job_sequencer.py` 신설. `InspectionSimulator`와 같은 계층의 `QObject`. **지금 어느 셀을 하고 있는지에 대한 유일한 소유자**가 된다.

### 상태

| 상태 | 뜻 |
|---|---|
| `IDLE` | 작업 계획 없음 |
| `MOVING_AMR(col)` | AMR이 `col` 구역으로 이동 중 |
| `MOVING_LIFT(col,row)` | 리프트를 `row` 높이로 올리는(또는 내리는) 중 |
| `SCANNING(col,row)` | 로봇이 셀 스캔 중 (레지스터 290이 1~4) |
| `PAUSED` | 직전 상태를 기억하고 멈춤 |
| `DONE` | 12F까지 완료 |
| `STOPPED` | 외부 정지 |

### 입력 (시그널로 받음)

```
plan_received(plan)          ← app.py, MQTT job_cmd
paused() / resumed()         ← app.py, MQTT mc_cmd stop/run
stopped()                    ← app.py, MQTT job_clear
scan_state_changed(state, row_idx, rows, finished)   ← ros_status_client (레지스터 290~298)
lift_arrived()               ← 리프트 더미 어댑터 (§7)
amr_arrived()                ← AMR 더미 어댑터 (§7)
```

### 출력 (시그널로 보냄)

```
cell_changed(col, row, label)        → RectWorkView 이름표, OrbitView AMR 위치
lift_target_requested(height_mm)     → 리프트 더미 어댑터 (§7)
amr_move_requested(col)              → AMR 더미 어댑터 (§7)
robot_start_requested()              → 로봇에 작업 시작 신호 (§5)
work_area_requested(w,h,scan_h,ov)   → 작업 시작 때 딱 한 번, 레지스터 256~259
job_complete()
```

### 전이

```
IDLE --plan_received--> work_area_requested (1회) --> MOVING_AMR(1)

MOVING_AMR(col) --amr_arrived--> MOVING_LIFT(col, 0)

MOVING_LIFT(col,row) --lift_arrived--> robot_start_requested --> SCANNING(col,row)

SCANNING(col,row) --scan_state(290==5 && 295==1)-->
    row+1 < row_count  ?  MOVING_LIFT(col, row+1)      # 같은 열, 리프트 한 칸 위
    col+1 < column_count ? MOVING_AMR(col+1)           # 다음 열 (리프트는 row 0으로 하강)
    아니면               DONE

임의 상태 --paused()--> PAUSED(직전 상태 기억)
PAUSED --resumed()--> 직전 상태 복귀
임의 상태 --stopped()--> STOPPED
```

리프트 목표 높이: `base_height + row_index * (cell_height - overlap)`

셀 이름표: `f"{col+1}{chr(ord('A') + row)}"` → `1A`, `1B`, … `12F`

### 왜 별도 클래스인가

`app.py`가 지금처럼 MQTT 페이로드를 직접 해석해 화면을 갱신하는 방식으로는 이 진행을 담을 수 없다. 셀 위치는 여러 입력(로봇 완료, 리프트 도착, AMR 도착, 일시정지)에 따라 바뀌는 상태이므로 소유자가 하나 있어야 한다. `app.py`는 시그널 연결만 하고 판단은 `JobSequencer`가 한다 — `InspectionSimulator`와 같은 구조다.

## 5. 로봇에 "작업 시작" 신호 보내기 — 제로점에서 재시작

**로봇은 셀마다 제로점(zero point)에서 다시 시작한다.** `param_src`(266) 재기록이 아니라, 태스크를 멈췄다가 다시 재생하는 방식이다. 실장비(VM)에서 확인한 결과, 순서가 중요하다.

```
1. remoteControl -on   원격 제어 모드로 전환
2. stop                돌고 있는 태스크를 멈춘다
3. (약 1.2초 대기)      컨트롤러가 태스크를 정리할 시간
4. play                제로점에서 다시 시작
```

### 왜 이 순서여야 하는가 (실장비에서 확인한 것)

| 빠뜨리면 | 로봇이 돌려주는 말 |
|---|---|
| `remoteControl -on` | `Command [play] is not supported in local control mode. Please close local control mode first.` |
| `stop` | `Failed to execute: play` — 태스크가 이미 RUNNING 이라 거부한다 |

- **원격 제어 모드가 아니면 `play`도 `stop`도 안 먹는다.** 펜던트를 만지면 로컬 제어로 돌아가므로 셀마다 켜 준다. 제어 모드는 레지스터 `71`로 확인한다(`1`=로컬, `2`=원격).
- **`stop` 없이 `play`만 보내면 거부된다.** 한 셀을 끝낸 뒤에도 태스크는 RUNNING 으로 남아 있다.
- **`dus_init`은 태스크가 시작될 때 한 번만 작업 영역(256~259)을 읽는다.** 그래서 멈췄다 켜지 않으면 이전 값으로 계속 돈다. 실제로 원통 전체 높이(6000 mm)가 레지스터에 남아 있어 로봇이 46행을 긁던 것을, 셀 높이(800 mm)를 쓰고 재시작하니 6행으로 바로잡혔다.

### 진행 여부는 상태 레지스터로만 판단한다

`play` 명령의 **응답 문자열로 성공을 판단하지 않는다.** 이미 돌고 있어서 거부되었을 뿐 로봇은 멀쩡히 동작 중인 경우가 있기 때문이다. 실제 진행은 다음 둘로 본다.

| 레지스터 | 읽는 법 | 의미 |
|---|---|---|
| `500` | **input register(0x04)** | 태스크 상태. `1`=실행 중, `2`=일시 중지, `3`=중지됨 |
| `290~298` | holding register | 스캔 진행 상태(§3) |

29999로 상태를 물으면 매번 명령을 던져야 하므로 Modbus 쪽으로 상시 모니터링한다. 노드가 `robot/status/task_state`(500)와 `robot/status/scan_state`(290~298)로 발행한다.

한 셀의 로봇 쪽 한 사이클:

```
제로점에서 재시작
  → 탐색/probe (state 1~3)
  → 원점 설정 (zero_ok=1, state=4)
  → ㄹ자 스캔하며 제로점 기준 TCP 좌표를 280~285에 발행
  → 피니시 자세 도달, 제로점 좌표 발행 종료 (state=5, finished=1)
  → 홈 복귀 후 정지
```

**피니시 자세에서 제로점 좌표 발행이 끝나야 그 셀이 완료된 것**이다. 이는 `dus_init.script`의 `pub_rel()` 게이트(`state == 4`일 때만 280~285에 실제 값, 그 외에는 `[0,0,0,0,0,0]`)와 정확히 맞물린다.

## 6. 제로점 좌표 + 격자 번호 내보내기 (RCS의 핵심 출력)

두 좌표의 용도가 다르다. 섞으면 안 된다.

| 좌표 | 레지스터 | 토픽 | 용도 |
|---|---|---|---|
| 실제 로봇 동작 좌표 (베이스 프레임) | `384~389` | `robot/status/tcp_pose` | **모니터링 전용.** Cobot 수동 제어 화면 표시용 |
| **제로점 기준 TCP 좌표** | `280~285` | `robot/status/tcp_pose_zero` | **스캐너 관리 시스템으로 나가는 실제 데이터** |

RCS가 할 일은 **제로점 좌표에 "지금 어느 격자인지"를 붙여서 내보내는 것**이다. 스캐너 관리 시스템은 이 좌표가 `1A`의 것인지 `7C`의 것인지 알아야 원통 전체에서 위치를 복원할 수 있다. 격자 번호를 아는 것은 시퀀서뿐이므로, 이 결합은 RCS에서만 할 수 있다.

발행 채널은 이미 규격에 있는 **T-008 `doosan/robot/tcp`** (Operator UI → MC, 10 Hz)를 쓰고, 격자 필드를 추가한다:

```json
{
  "timestamp": "1784727779111",
  "cell": "1A",
  "x": "1205", "y": "852", "z": "1208", "yaw": "-1.214"
}
```

- `x`/`y`/`z`는 `robot/status/tcp_pose_zero`에서 온 **제로점 기준** 값이다. 기존 T-008이 절대 좌표로 적혀 있던 부분이라 문서에 명시했다.
- `cell`은 `JobSequencer.current_cell()`이 돌려주는 현재 격자 이름이다. 열/행을 따로 싣지 않는다 — `1A` 하나로 둘 다 읽히고, 필드가 갈라지면 서로 어긋날 여지만 생긴다.
- `yaw`는 로봇이 mrad으로 주므로 1000으로 나눠 radian으로 보낸다.
- 스캔 중이 아닐 때는 로봇이 `[0,0,0,0,0,0]`을 내보내므로 그대로 0이 나간다. 10 Hz 상시 발행 규격을 따라 발행 자체는 멈추지 않는다. 작업 시작 전이라 격자가 없으면 `cell`은 빈 문자열이다.

## 7. 리프트와 AMR — 지금은 더미

실제 PLC와 차량이 아직 없으므로 **리프트/AMR 제어는 더미로 둔다.**

| | 현재 상태 | 더미 처리 |
|---|---|---|
| 리프트 | `config/plc_io.json`에 `Y010 Lift Brake`(OUT), `D100 Lift Height`(IN)가 더미 값으로만 존재 | `lift_target_requested(height_mm)`를 받으면 활동 로그만 남기고 즉시(또는 짧은 타이머 후) `lift_arrived()`를 돌려주는 더미 어댑터 |
| AMR | 제어 패키지 없음(`plan.md`의 `amr_*` 스켈레톤 미생성) | `amr_move_requested(col)`도 같은 방식으로 즉시 `amr_arrived()` |

더미 어댑터를 `JobSequencer` 밖에 두고 시그널로만 연결하면, 나중에 실제 PLC/AMR 경로가 생겼을 때 **시퀀서를 고치지 않고 어댑터만 교체**하면 된다. 이 구조 덕분에 지금도 `1A → 12F` 전체 순회를 끝까지 돌려볼 수 있다.

## 8. 구현 현황

| 단계 | 내용 | 상태 |
|---|---|---|
| 1 | MQTT `job_cmd`를 작업 계획(`plan`) 형태로 정리, `mqtt_test` 도구 갱신 | **완료** |
| 2 | `scan_h`를 MQTT에서 빼고 로컬 설정값 사용 | **완료** |
| 3 | `scan_state`(290~298) 읽기 → `robot/status/scan_state` 발행 → `ros_status_client` 구독 | **완료** |
| 4 | 리프트/AMR 더미 어댑터 + `JobSequencer` 신설, 셀 순회 완성 | **완료** |
| 5 | 셀마다 제로점에서 29999 play로 로봇 재시작 | **완료** |
| 6 | 제로점 좌표 + 격자 번호를 `doosan/robot/tcp`로 발행 | **완료** |
| 7 | 실제 PLC/AMR 경로가 생기면 더미 어댑터 교체 | 장비 대기 |

### 구현된 파일

| 파일 | 역할 |
|---|---|
| `operator-ui/.../services/job_sequencer.py` | 상태 머신 본체. `GridPlan`, `JobSequencer`, `CellStatus`, `cell_label()` |
| `operator-ui/.../services/motion_adapters.py` | `DummyMotionAdapter`. 리프트/AMR 이동을 흉내 낸다 |
| `operator-ui/.../app.py` | `_connect_sequencer()`가 시퀀서를 로봇·화면·MQTT에 잇는다 |
| `src/elite_robot_controller/.../robot_control_node.py` | `publish_raw()`로 `scan_state` 발행 |
| `config/modbus_registers.json` | `read.scan_state` (290, count 9, raw) |

### 검증 결과

실제 ROS 노드 + `elite_robot_simulator.py`와 붙여 2열 × 3행을 끝까지 돌린 결과:

```
방문한 셀: ['1A', '1B', '1C', '2A', '2B', '2C']
시퀀서 상태: DONE
로봇 명령 호출: play 6회 (셀마다 1회, 전부 성공)
로봇 레지스터 256~259: [600, 800, 150, 20]   ← 작업 시작 때 1회만 기록
job_state: 셀마다 waiting → executing → completed
tcp: 제로점 좌표에 격자 이름(cell) 부착 확인
```

AMR 이동은 열이 바뀔 때만(2회), 리프트는 행마다 `cell_height - overlap`씩 상승했다.

### 남은 것

- **리프트/AMR 실제 제어** — 실제 PLC와 차량이 없어 더미로 둔다. `motion_adapters.py`만 교체하면 되고 `JobSequencer`는 손대지 않는다.
- **로봇 태스크 쪽 확인** — 셀마다 제로점에서 play할 때, 펜던트의 `dusan_v1.task`가 매번 처음부터 다시 도는지 실장비에서 확인이 필요하다.
