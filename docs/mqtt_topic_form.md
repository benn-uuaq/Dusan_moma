# MQTT Topic 정의 양식

이 문서는 SMR 비파괴 검사 시스템에서 사용하는 MQTT Topic을 정리하기 위한 작성 양식이다.

Topic의 방향은 특정 프로그램의 관점에서 `송신/수신`으로 표현하지 않고, 실제 주체를 알 수 있도록 **발행자(Publisher)**와 **구독자(Subscriber)**로 기록한다.

> 작성 방법: `[작성]`, `<값>` 또는 예시 내용을 실제 값으로 교체한다. 사용하지 않는 항목은 `해당 없음`으로 표시한다.

---

## 1. MQTT 연결 정보

| 항목 | 입력값 |
|---|---|
| Broker 주소 | `127.0.0.1` |
| TCP 포트 | `1883 ` |
| WebSocket 포트 | `해당 없음` |
| TLS 사용 여부 | `미사용` |
| MQTT 버전 | `3.1.1` |
| 인증 방식 | `없음` |
| Client ID 규칙 | `[작성]` |
| Keep Alive | `[작성]초` |
| Clean Start / Clean Session | `[작성]` |
| 기본 QoS | `1` |
| 재연결 정책 | `1초부터 최대 30초까지 증가, 연결 실패 10회 후 자동 재접속 종료` |
| 운영 Broker | `emqx` |
| 개발 Broker | `Mosquitto` |

비밀번호, 인증서 개인키 등의 비밀정보는 이 문서에 직접 기록하지 않는다.

---

## 2. Topic 이름 규칙

### 2.1 기본 구조

```text
<system>/<site>/<device-type>/<device-id>/<category>/<name>
```

실제 적용 구조:

```text
<site>/<device-type>/<category>/<name>
```

### 2.2 경로 변수

| 변수 | 의미 | 허용값 또는 예시 |
|---|---|---|
| `system` | 시스템 구분 | `[예: smr-inspection]` |
| `site` | 현장 또는 설비 구분 | `[예: plant-01]` |
| `device-type` | 장비 종류 | `[예: smr, robot, ut, operator-ui]` |
| `device-id` | 장비 고유 ID | `[예: smr-01]` |
| `category` | 메시지 분류 | `[예: command, status, event, telemetry, response]` |
| `name` | 기능 또는 데이터 이름 | `[예: start, pose, alarm]` |

### 2.3 공통 규칙

| 항목 | 결정 내용 |
|---|---|
| 대소문자 | `[예: 소문자만 사용]` |
| 단어 구분자 | `[예: 하이픈(-)]` |
| 와일드카드 구독 허용 범위 | `[작성]` |
| 장비 ID 부여 방식 | `[작성]` |
| Topic 버전 표기 방식 | `[작성/해당 없음]` |
| Topic 규칙 | `json` |

---

## 3. Topic 전체 목록

Topic 하나를 추가할 때 아래 표에 먼저 한 줄로 등록하고, 필요한 경우 4장의 상세 양식을 작성한다.

| ID | Topic | 분류 | 발행자 | 구독자 | QoS | Retain | 주기/조건 | 상세 작성 |
|---|---|---|---|---|---:|---|---|---|
| `T-001` | `doosan/robot/req/mc_cmd` | `command` | `mc` | `Operator UI` | `1` | `N` | `사용자 또는 제어 로직에서 명령 발생 시` | `Y` |
| `T-002` | `doosan/robot/req/reset` | `command` | `mc` | `Operator UI` | `1` | `N` | `사용자 또는 제어 로직에서 명령 발생 시` | `Y` |
| `T-003` | `doosan/robot/req/ems` | `command` | `mc` | `Operator UI` | `1` | `N` | `사용자 또는 제어 로직에서 명령 발생 시` | `Y` |
| `T-004` | `doosan/robot/req/job_clear` | `command` | `mc` | `Operator UI` | `1` | `N` | `사용자 또는 제어 로직에서 명령 발생 시` | `Y` |
| `T-005` | `doosan/robot/req/job_cmd` | `command` | `mc` | `Operator UI` | `1` | `N` | `사용자 또는 제어 로직에서 명령 발생 시` | `Y` |
| `T-006` | `doosan/robot/robot_state` | `status` | `mc` | `Operator UI` | `1` | `N` | `상시 출력` | `Y` |
| `T-007` | `doosan/robot/error` | `status` | `mc` | `Operator UI` | `1` | `N` | `사용자 또는 제어 로직에서 명령 발생 시` | `Y` |
| `T-008` | `doosan/robot/tcp` | `status` | `mc` | `Operator UI` | `1` | `N` | `상시 출력` | `Y` |
| `T-009` | `doosan/robot/job_state` | `status` | `mc` | `Operator UI` | `1` | `N` | `상시 출력` | `Y` |
| `T-010` | `doosan/robot/req/speed` | `command` | `mc` | `Operator UI` | `1` | `N` | `속도 변경이 필요할 때` | `Y` |

---

## 4. Topic 상세 정의

아래 블록을 Topic 수만큼 복사해서 작성한다.

### T-001: AMR 및 Cobot 동작 명령

주어진 명세를 기준으로 작성한 첫 번째 Topic이다. `[확인 필요]`와 `[권장]` 표시는 아직 확정되지 않은 내용이다.

#### 기본 정보

| 항목 | 입력값 |
|---|---|
| Topic | `doosan/robot/req/mc_cmd` |
| 목적 | AMR과 Cobot에 실행할 동작 명령을 한 메시지로 전달한다. |
| 분류 | `command` |
| 발행자 | `MC` |
| 구독자 | `Operatior UI` |
| 명령 실행 대상 | `AMR`, `Cobot` |
| 발행 조건 | 사용자 조작 또는 제어 로직에서 AMR/Cobot 명령이 발생했을 때 |
| 발행 주기 | 명령 발생 시 1회 |
| QoS | `[1` |
| Retain | `N` |
| 메시지 만료 시간 | `10 minute` |
| 중요도 | `중요` |

#### Payload 형식

| 항목 | 입력값 |
|---|---|
| 데이터 형식 | `JSON` |
| 문자 인코딩 | `UTF-8` |
| 스키마 버전 | `[명세 없음]` |
| Timestamp 기준 | `UTC Unix Epoch 밀리초` |

주어진 Payload 예시:

```json
{
  "timestamp": "1784727720000",
  "amr": "stop",
  "cobot": "stop"
}
```

#### Payload 필드

| 필드 경로 | 자료형 | 필수 | 단위 | 허용값/범위 | 설명 | 값 예시 |
|---|---|---|---|---|---|---|
| `timestamp` | `string` | `Y` | `ms` | UTC Unix Epoch 밀리초로 추정 | 명령 생성 시각 | `"1784727720000"` |
| `amr` | `string` | `Y` | - | `run`, `stop`, `ems`, `lift` | AMR에 전달할 동작 명령 | `"stop"` |
| `cobot` | `string` | `Y` | - | `run`, `stop`, `ems`, `home` | Cobot에 전달할 동작 명령. `home` = 홈 이동 (로봇이 동작 중이면 거절 — RCS 로그에만 남김) | `"stop"` |

#### 명령값 정리

| 대상 | 명령값 | 의미 |
|---|---|---|
| AMR | `run` | `검사시작` |
| AMR | `stop` | `일시 정지` |


#### 유효성 및 예외 처리

| 상황 | 처리 방법 |
|---|---|
| 필수 필드 누락 | `명령 거부` |
| 허용되지 않은 명령값 | `명령 거부 및 오류 응답` |
| 중복 메시지 수신 | `timestamp 확인하여 중복 메세지 확인 및 최신 메세지 우선` |
| 오래된 메시지 수신 | `timestamp 확인하여 중복 메세지 확인 및 최신 메세지 우선` |
| AMR 또는 Cobot 한쪽만 명령하는 경우 | `명령이 수신되지 않은 쪽은 유지` |
| 처리 성공/실패 확인 | 응답 Topic이 정의되지 않아 별도 정의 필요 |

#### 확정이 필요한 항목

- [ ] 이 Topic을 실제로 발행하는 시스템
- [ ] 이 Topic을 구독하고 명령을 실행하는 시스템
- [ ] `timestamp`가 JSON 문자열인지 숫자인지
- [ ] `timestamp`가 UTC Unix Epoch 밀리초가 맞는지
- [ ] QoS를 `1`로 사용할지
- [ ] Retain을 사용하지 않을지
- [ ] `run`, `stop`, `ems`, `lift`의 정확한 동작 정의
- [ ] AMR과 Cobot 명령을 항상 동시에 전달해야 하는지
- [ ] 명령 처리 결과를 전달할 응답 Topic
- [ ] 중복 실행 방지를 위한 `message_id` 추가 여부



### T-002: 로봇 에러 리셋 명령

주어진 명세를 기준으로 작성한 첫 번째 Topic이다. `[확인 필요]`와 `[권장]` 표시는 아직 확정되지 않은 내용이다.

#### 기본 정보

| 항목 | 입력값 |
|---|---|
| Topic | `doosan/robot/req/reset` |
| 목적 | 에러 리셋. |
| 분류 | `command` |
| 발행자 | `MC` |
| 구독자 | `Operatior UI` |
| 명령 실행 대상 | `AMR`, `Cobot` |
| 발행 조건 | 사용자 조작 또는 제어 로직에서 AMR/Cobot 명령이 발생했을 때 |
| 발행 주기 | 명령 발생 시 1회 |
| QoS | `[1` |
| Retain | `N` |
| 메시지 만료 시간 | `10 minute` |
| 중요도 | `중요` |

#### Payload 형식

| 항목 | 입력값 |
|---|---|
| 데이터 형식 | `JSON` |
| 문자 인코딩 | `UTF-8` |
| 스키마 버전 | `[명세 없음]` |
| Timestamp 기준 | `UTC Unix Epoch 밀리초` |

주어진 Payload 예시:

```json
{
    "timestamp": "1784727779111",
    "request" : "true"
}
```

#### Payload 필드

| 필드 경로 | 자료형 | 필수 | 단위 | 허용값/범위 | 설명 | 값 예시 |
|---|---|---|---|---|---|---|
| `timestamp` | `string` | `Y` | `ms` | UTC Unix Epoch 밀리초로 추정 | 명령 생성 시각 | `"1784727720000"` |
| `request` | `string` | `Y` | - | `true`,`false`, `null` | 알람 리셋 명령 | `"true"` |

#### 명령값 정리

| 대상 | 명령값 | 의미 |
|---|---|---|
| request | `true` | 알람 리셋 |

#### 유효성 및 예외 처리

| 상황 | 처리 방법 |
|---|---|
| 필수 필드 누락 | `명령 거부` |
| 허용되지 않은 명령값 | `명령 거부 및 오류 응답` |
| 중복 메시지 수신 | `timestamp 확인하여 중복 메세지 확인 및 최신 메세지 우선` |
| 오래된 메시지 수신 | `timestamp 확인하여 중복 메세지 확인 및 최신 메세지 우선` |
| 처리 성공/실패 확인 | 응답 Topic이 정의되지 않아 별도 정의 필요 |



### T-003: 로봇 비상정지

주어진 명세를 기준으로 작성한 첫 번째 Topic이다. `[확인 필요]`와 `[권장]` 표시는 아직 확정되지 않은 내용이다.

#### 기본 정보

| 항목 | 입력값 |
|---|---|
| Topic | `doosan/robot/req/ems` |
| 목적 | 비상정지 |
| 분류 | `command` |
| 발행자 | `MC` |
| 구독자 | `Operatior UI` |
| 명령 실행 대상 | `AMR`, `Cobot` |
| 발행 조건 | 사용자 조작 또는 제어 로직에서 AMR/Cobot 명령이 발생했을 때 |
| 발행 주기 | 명령 발생 시 1회 |
| QoS | `[1` |
| Retain | `N` |
| 메시지 만료 시간 | `10 minute` |
| 중요도 | `중요` |

#### Payload 형식

| 항목 | 입력값 |
|---|---|
| 데이터 형식 | `JSON` |
| 문자 인코딩 | `UTF-8` |
| 스키마 버전 | `[명세 없음]` |
| Timestamp 기준 | `UTC Unix Epoch 밀리초` |

주어진 Payload 예시:

```json
{
    "timestamp": "1784727779111",
    "request" : "true"
}
```

#### Payload 필드

| 필드 경로 | 자료형 | 필수 | 단위 | 허용값/범위 | 설명 | 값 예시 |
|---|---|---|---|---|---|---|
| `timestamp` | `string` | `Y` | `ms` | UTC Unix Epoch 밀리초로 추정 | 명령 생성 시각 | `"1784727720000"` |
| `request` | `string` | `Y` | - | `true`,`false`, `null` | 비상정지 | `"true"` |

#### 명령값 정리

| 대상 | 명령값 | 의미 |
|---|---|---|
| request | `true` | 비상정지 |

#### 유효성 및 예외 처리

| 상황 | 처리 방법 |
|---|---|
| 필수 필드 누락 | `명령 거부` |
| 허용되지 않은 명령값 | `명령 거부 및 오류 응답` |
| 중복 메시지 수신 | `timestamp 확인하여 중복 메세지 확인 및 최신 메세지 우선` |
| 오래된 메시지 수신 | `timestamp 확인하여 중복 메세지 확인 및 최신 메세지 우선` |
| 처리 성공/실패 확인 | 응답 Topic이 정의되지 않아 별도 정의 필요 |




### T-004: 로봇 Job 초기화

주어진 명세를 기준으로 작성한 첫 번째 Topic이다. `[확인 필요]`와 `[권장]` 표시는 아직 확정되지 않은 내용이다.

#### 기본 정보

| 항목 | 입력값 |
|---|---|
| Topic | `doosan/robot/req/job_clear` |
| 목적 | 현재 작업중인 job을 중단하고 삭제함 |
| 분류 | `command` |
| 발행자 | `MC` |
| 구독자 | `Operatior UI` |
| 명령 실행 대상 | `AMR`, `Cobot` |
| 발행 조건 | 사용자 조작 또는 제어 로직에서 AMR/Cobot 명령이 발생했을 때 |
| 발행 주기 | 명령 발생 시 1회 |
| QoS | `[1` |
| Retain | `N` |
| 메시지 만료 시간 | `10 minute` |
| 중요도 | `중요` |

#### Payload 형식

| 항목 | 입력값 |
|---|---|
| 데이터 형식 | `JSON` |
| 문자 인코딩 | `UTF-8` |
| 스키마 버전 | `[명세 없음]` |
| Timestamp 기준 | `UTC Unix Epoch 밀리초` |

주어진 Payload 예시:

```json
{
    "timestamp": "1784727779111",
    "request" : "true"
}
```

#### Payload 필드

| 필드 경로 | 자료형 | 필수 | 단위 | 허용값/범위 | 설명 | 값 예시 |
|---|---|---|---|---|---|---|
| `timestamp` | `string` | `Y` | `ms` | UTC Unix Epoch 밀리초로 추정 | 명령 생성 시각 | `"1784727720000"` |
| `request` | `string` | `Y` | - | `true`,`false`, `null` | 현재 작업중인 job을 중단하고 삭제함 | `"true"` |

#### 명령값 정리

| 대상 | 명령값 | 의미 |
|---|---|---|
| request | `true` | 현재 작업중인 job을 중단하고 삭제함 |

#### 유효성 및 예외 처리

| 상황 | 처리 방법 |
|---|---|
| 필수 필드 누락 | `명령 거부` |
| 허용되지 않은 명령값 | `명령 거부 및 오류 응답` |
| 중복 메시지 수신 | `timestamp 확인하여 중복 메세지 확인 및 최신 메세지 우선` |
| 오래된 메시지 수신 | `timestamp 확인하여 중복 메세지 확인 및 최신 메세지 우선` |
| 처리 성공/실패 확인 | 응답 Topic이 정의되지 않아 별도 정의 필요 |



### T-005: 로봇 Job 명령

주어진 명세를 기준으로 작성한 첫 번째 Topic이다. `[확인 필요]`와 `[권장]` 표시는 아직 확정되지 않은 내용이다.

#### 기본 정보

| 항목 | 입력값 |
|---|---|
| Topic | `doosan/robot/req/job_cmd` |
| 목적 | 새로운 job 명령 |
| 분류 | `command` |
| 발행자 | `MC` |
| 구독자 | `Operatior UI` |
| 명령 실행 대상 | `AMR`, `Cobot` |
| 발행 조건 | 사용자 조작 또는 제어 로직에서 AMR/Cobot 명령이 발생했을 때 |
| 발행 주기 | 명령 발생 시 1회 |
| QoS | `[1` |
| Retain | `N` |
| 메시지 만료 시간 | `10 minute` |
| 중요도 | `중요` |

#### Payload 형식

| 항목 | 입력값 |
|---|---|
| 데이터 형식 | `JSON` |
| 문자 인코딩 | `UTF-8` |
| 스키마 버전 | `[명세 없음]` |
| Timestamp 기준 | `UTC Unix Epoch 밀리초` |

주어진 Payload 예시:

```json
{
    "timestamp": "1784727779111",
    "job_id" : "jb00000001",
    "job_info" : 
    {
        "diameter" : "2500",
        "height" : " 6000",
        "target_distance" : "8560"
    },
    "plan" :
    {
        "column_count" : "12",
        "row_count" : "6",
        "cell_width" : "600",
        "cell_height" : "800",
        "overlap" : "20"
    }
}
```

#### Payload 필드

| 필드 경로 | 자료형 | 필수 | 단위 | 허용값/범위 | 설명 | 값 예시 |
|---|---|---|---|---|---|---|
| `timestamp` | `string` | `Y` | `ms` | UTC Unix Epoch 밀리초로 추정 | 명령 생성 시각 | `"1784727720000"` |
| `job_id` | `string` | `Y` | - | `id` | 새로운 job의 id | `"jb00000001"` |
| `job_info` | `dictionary` | `Y` | - | `key` | job information | `"diameter", "height", "target_distance" 등 값의 대분류` |
| `diameter` | `string` | `Y` | - | `mm` | 검사 대상체(원통) 지름 | `"2500"` |
| `height` | `string` | `Y` | - | `mm` | 검사 대상체(원통) **전체** 높이. 격자 여러 행이 합쳐 덮는 높이이며, **셀 하나의 높이가 아니다** | `"6000"` |
| `target_distance` | `string` | `Y` | - | `mm` | 이동할 거리 | `"300"` |
| `plan` | `dictionary` | `Y` | - | `key` | 원통을 편 직사각형의 격자 분할 계획. 이 명령이 곧 "전체 작업 시작"이다 | 아래 5개 필드 |
| `plan.column_count` | `string` | `Y` | - | `1` 이상 | 열 수 = AMR이 원주를 돌며 정차하는 구역 수. 원통 크기에 따라 12보다 늘어날 수 있다 | `"12"` |
| `plan.row_count` | `string` | `Y` | - | `1` 이상 | 행 수 = 리프트 높이 단계 수(A, B, … F) | `"6"` |
| `plan.cell_width` | `string` | `Y` | - | `mm` | **셀 하나**의 가로 폭. 로봇 태스크의 `app_width`(레지스터 256)로 전달 | `"600"` |
| `plan.cell_height` | `string` | `Y` | - | `mm` | **셀 하나**의 세로 높이. 로봇 태스크의 `app_height`(레지스터 257)로 전달. 리프트 상승 피치(`cell_height - overlap`) 계산에도 쓴다 | `"800"` |
| `plan.overlap` | `string` | `Y` | - | `mm` | 겹침 허용. 로봇 태스크의 `overlap`(레지스터 259)로 전달 | `"20"` |

**격자 개념:** 원통을 편 직사각형을 격자로 나눈다. **열(1~12)은 AMR이 정차하는 원주 구역, 행(A~F)은 리프트 높이**다. 셀 하나(예: `1A`)가 Cobot이 ㄹ자로 훑는 영역이며, `1A → 1B → … → 1F → 2A → … → 12F` 순으로 진행한다.

> **⚠️ `job_info.height`와 `plan.cell_height`를 혼동하지 말 것.** `height`(예: 6000 mm)는 원통 **전체** 높이이고, Cobot이 한 번에 닿는 높이가 아니다. ㄹ자는 `cell_height`(예: 800 mm)짜리 **셀 하나**에만 그린다. 전체 높이를 셀 높이로 쓰면 ㄹ자 패스가 수십 행으로 늘어나 로봇이 실행할 수 없는 경로가 된다.

**`scan_h`는 이 payload에 없다.** UT 스캐너가 한 자리에서 훑는 세로 유효높이는 **장비 고유값**이라 작업 계획이 아니다. 수신 측(Operator UI)의 로컬 설정값을 그대로 써서 로봇의 레지스터 258로 전달한다.

**자동 진행:** 셀을 하나씩 넘어가는 것은 MC가 아니라 Operator UI/로봇 쪽 책임이다. 그래서 이 payload에는 지금 몇 번째 셀인지 담지 않는다. 상세 설계는 [`grid_sequencer_design.md`](grid_sequencer_design.md) 참고.

이 명령을 받은 뒤 구역과 격자를 하나씩 넘어가며 스캔을 이어가는 것은 **MC가 아니라 Operator UI/로봇 쪽의 책임**이다. MC는 `job_cmd`로 전체 작업을 한 번 시작시키고, 이후에는 `mc_cmd`(일시정지/재개)와 `job_clear`(정지)로만 개입한다. 즉 MC → Operator UI 방향으로 오가는 것은 ① 전체 작업 시작/일시정지/정지, ② 로봇이 ㄹ자를 그리기 위한 값(`job_info`, `scan`) 뿐이며, 지금 몇 번째 구역/격자를 스캔 중인지는 Operator UI가 내부적으로 추적한다(화면에는 `RectWorkView`의 격자 이름표로 표시).

**시작/일시정지/정지 매핑** (새 Topic을 만들지 않고 기존 Topic을 재사용한다):

| 동작 | Topic | 비고 |
|---|---|---|
| 전체 작업 시작 | `doosan/robot/req/job_cmd` | `job_info` + `scan` 값을 함께 실어 보낸다 |
| 일시정지 / 재개 | `doosan/robot/req/mc_cmd` | `cobot`(또는 `amr`) `"stop"` = 일시정지, `"run"` = 재개 |
| 로봇 홈 | `doosan/robot/req/mc_cmd` | `cobot` `"home"` — 작업 중이면 거절, 먼저 `job_clear`(정지) |
| 정지(중단) | `doosan/robot/req/job_clear` | 진행 중인 작업을 완전히 멈추고 정리한다 |

동작 순서는 다음과 같다: AMR이 구역에 도착 → Cobot이 격자 0을 ㄹ자로 스캔 → 완료 후 Cobot이 홈 위치로 복귀 → 리프트가 상승해 다음 격자 높이로 이동 → Cobot이 격자 1을 스캔 → (해당 구역의 마지막 격자까지 반복) → AMR이 다음 구역으로 이동 → 위 과정을 반복.
#### 명령값 정리

| 대상 | 명령값 | 의미 |
|---|---|---|
| job_id | `jb00000001` | 새로운 job의 id |
| job_info-diameter | `2500` | 검사 대상체 지름 |
| job_info-height | `6000` |  검사 대상체 높이 |
| job_info-target_distance | `300` | 이동할 거리 |


#### 유효성 및 예외 처리

| 상황 | 처리 방법 |
|---|---|
| 필수 필드 누락 | `명령 거부` |
| 허용되지 않은 명령값 | `명령 거부 및 오류 응답` |
| 중복 메시지 수신 | `timestamp 확인하여 중복 메세지 확인 및 최신 메세지 우선` |
| 오래된 메시지 수신 | `timestamp 확인하여 중복 메세지 확인 및 최신 메세지 우선` |
| 처리 성공/실패 확인 | 응답 Topic이 정의되지 않아 별도 정의 필요 |

### T-006: 로봇 state 상태

주어진 명세를 기준으로 작성한 첫 번째 Topic이다. `[확인 필요]`와 `[권장]` 표시는 아직 확정되지 않은 내용이다.

#### 기본 정보

| 항목 | 입력값 |
|---|---|
| Topic | `doosan/robot/robot_state` |
| 목적 | 새로운 job 명령 |
| 분류 | `state` |
| 발행자 | `Operatior UI` |
| 구독자 | `MC` |
| 발행 조건 | 주기 발생 |
| 발행 주기 | 10hz |
| QoS | `0` |
| Retain | `N` |
| 메시지 만료 시간 | `1 minute` |
| 중요도 | `중요` |

#### Payload 형식

| 항목 | 입력값 |
|---|---|
| 데이터 형식 | `JSON` |
| 문자 인코딩 | `UTF-8` |
| 스키마 버전 | `[명세 없음]` |
| Timestamp 기준 | `UTC Unix Epoch 밀리초` |

주어진 Payload 예시:

```json
{
    "timestamp": "1784727720000",
    "mode" : "auto",
    "amr" : "stop",
    "cobot" : "stop"
}
```

#### Payload 필드

| 필드 경로 | 자료형 | 필수 | 단위 | 허용값/범위 | 설명 | 값 예시 |
|---|---|---|---|---|---|---|
| `timestamp` | `string` | `Y` | `ms` | UTC Unix Epoch 밀리초로 추정 | 명령 생성 시각 | `"1784727720000"` |
| `mode` | `string` | `Y` | - | `auto`,`manual` | 자동/수동 모드 | `"auto"` |
| `amr` | `string` | `Y` | - | `stop`, `run`, `error`, `idle`, `hold` | amr state | `"stop"` |
| `cobot` | `string` | `Y` | - | `stop`, `run`, `error`, `idle`, `hold` | cobot state | `"stop"` |

#### 명령값 정리

| 대상 | 명령값 | 의미 |
|---|---|---|
| mode | `auto` | 자동/수동 모드 |
| amr | `stop` | amr state |
| cobot | `stop` |  cobot state |


#### 유효성 및 예외 처리

| 상황 | 처리 방법 |
|---|---|
| 필수 필드 누락 | `명령 거부` |
| 허용되지 않은 명령값 | `명령 거부 및 오류 응답` |
| 중복 메시지 수신 | `timestamp 확인하여 중복 메세지 확인 및 최신 메세지 우선` |
| 오래된 메시지 수신 | `timestamp 확인하여 중복 메세지 확인 및 최신 메세지 우선` |
| 처리 성공/실패 확인 | 응답 Topic이 정의되지 않아 별도 정의 필요 |



### T-007: 로봇 error 상태

주어진 명세를 기준으로 작성한 첫 번째 Topic이다. `[확인 필요]`와 `[권장]` 표시는 아직 확정되지 않은 내용이다.

#### 기본 정보

| 항목 | 입력값 |
|---|---|
| Topic | `doosan/robot/error` |
| 목적 | 새로운 job 명령 |
| 분류 | `state` |
| 발행자 | `Operatior UI` |
| 구독자 | `MC` |
| 발행 조건 | event, 구독자 요청시 |
| 발행 주기 | 10hz |
| QoS | `1` |
| Retain | `Y` |
| 메시지 만료 시간 | `10 minute` |
| 중요도 | `중요` |

#### Payload 형식

| 항목 | 입력값 |
|---|---|
| 데이터 형식 | `JSON` |
| 문자 인코딩 | `UTF-8` |
| 스키마 버전 | `[명세 없음]` |
| Timestamp 기준 | `UTC Unix Epoch 밀리초` |

주어진 Payload 예시:

```json
{
    "timestamp": "1784727779111",
    "error_count" : "2",
    "error_list" : ["emergency","safety error"],
    "error_time" : "1784727779110"
}
```

#### Payload 필드

| 필드 경로 | 자료형 | 필수 | 단위 | 허용값/범위 | 설명 | 값 예시 |
|---|---|---|---|---|---|---|
| `timestamp` | `string` | `Y` | `ms` | UTC Unix Epoch 밀리초로 추정 | 명령 생성 시각 | `"1784727720000"` |
| `error_count` | `string` | `Y` | - | `0~10` | 발생한 에러 카운트 | `"2"` |
| `error_list` | `string` | `Y` | - | `emergency`, `safety error`... | 발생한 에러 리스트 | `"emergency"` |
| `error_time` | `string` | `Y` | - | UTC Unix Epoch | 마지막 에러 발생시간| `"1784727779110"` |

#### 명령값 정리

| 대상 | 명령값 | 의미 |
|---|---|---|
| error_count | `2` | 자동/수동 모드 |
| error_list | `emergency` | 발생한 에러 리스트 |
| error_time | `1784727779110` |  마지막 에러 발생시간 |


#### 유효성 및 예외 처리

| 상황 | 처리 방법 |
|---|---|
| 필수 필드 누락 | `명령 거부` |
| 허용되지 않은 명령값 | `명령 거부 및 오류 응답` |
| 중복 메시지 수신 | `timestamp 확인하여 중복 메세지 확인 및 최신 메세지 우선` |
| 오래된 메시지 수신 | `timestamp 확인하여 중복 메세지 확인 및 최신 메세지 우선` |
| 처리 성공/실패 확인 | 응답 Topic이 정의되지 않아 별도 정의 필요 |




### T-008: 로봇 error 상태

주어진 명세를 기준으로 작성한 첫 번째 Topic이다. `[확인 필요]`와 `[권장]` 표시는 아직 확정되지 않은 내용이다.

#### 기본 정보

| 항목 | 입력값 |
|---|---|
| Topic | `doosan/robot/tcp` |
| 목적 | 새로운 job 명령 |
| 분류 | `state` |
| 발행자 | `Operatior UI` |
| 구독자 | `MC` |
| 발행 조건 | event, 구독자 요청시 |
| 발행 주기 | 10hz |
| QoS | `1` |
| Retain | `Y` |
| 메시지 만료 시간 | `10 minute` |
| 중요도 | `중요` |

#### Payload 형식

| 항목 | 입력값 |
|---|---|
| 데이터 형식 | `JSON` |
| 문자 인코딩 | `UTF-8` |
| 스키마 버전 | `[명세 없음]` |
| Timestamp 기준 | `UTC Unix Epoch 밀리초` |

주어진 Payload 예시:

```json
{
    "timestamp": "1784727779111",
    "cell" : "1A",
    "x" : "1205",
    "y" : "852",
    "z" : "1208",
    "yaw" : "-1.214"
}
```

#### Payload 필드

| 필드 경로 | 자료형 | 필수 | 단위 | 허용값/범위 | 설명 | 값 예시 |
|---|---|---|---|---|---|---|
| `timestamp` | `string` | `Y` | `ms` | UTC Unix Epoch 밀리초로 추정 | 명령 생성 시각 | `"1784727720000"` |
| `cell` | `string` | `Y` | - | `1A` ~ `12F` | **이 좌표를 잰 격자 이름.** 열(AMR 정차 구역) + 행(리프트 높이). 작업 중이 아니면 빈 문자열 | `"1A"` |
| `x` | `string` | `Y` | mm | - | **제로점 기준** x 좌표 | `"1205"` |
| `y` | `string` | `Y` | mm | - | **제로점 기준** y 좌표 | `"852"` |
| `z` | `string` | `Y` | mm | - | **제로점 기준** z 좌표 | `"1208"` |
| `yaw` | `string` | `Y` | radian | +,- 3.14 | yaw theta. 로봇은 mrad으로 주므로 1000으로 나눠 보낸다 | `"-1.214"` |

> **⚠️ 여기 실리는 좌표는 제로점 기준이다.** 이 값이 스캐너 관리 시스템으로 나가는 실제 데이터다(Modbus 280~285). 로봇의 베이스 프레임 좌표(384~389)는 운영 화면 모니터링용이라 여기 오지 않는다.
>
> 좌표만으로는 원통 어디를 잰 값인지 알 수 없으므로 `cell`을 함께 싣는다. 지금 어느 격자인지 아는 것은 `JobSequencer` 뿐이라, 이 결합은 Operator UI에서만 할 수 있다.

#### 명령값 정리

| 대상 | 명령값 | 의미 |
|---|---|---|
| x | `1000` | 자동/수동 모드 |
| y | `1000` | 발생한 에러 리스트 |
| z | `1000` |  마지막 에러 발생시간 |
| yaw | `1.257` |  yaw theta |


#### 유효성 및 예외 처리

| 상황 | 처리 방법 |
|---|---|
| 필수 필드 누락 | `명령 거부` |
| 허용되지 않은 명령값 | `명령 거부 및 오류 응답` |
| 중복 메시지 수신 | `timestamp 확인하여 중복 메세지 확인 및 최신 메세지 우선` |
| 오래된 메시지 수신 | `timestamp 확인하여 중복 메세지 확인 및 최신 메세지 우선` |
| 처리 성공/실패 확인 | 응답 Topic이 정의되지 않아 별도 정의 필요 |
---

### T-009: 격자별 작업 상태

격자(셀) 하나가 대기 → 작업중 → 완료를 거칠 때마다 외부(MC)에 알린다. 어느 영역이 끝났는지 외부가 추적할 수 있게 하기 위한 Topic이다.

#### 기본 정보

| 항목 | 입력값 |
|---|---|
| Topic | `doosan/robot/job_state` |
| 목적 | 격자별 작업 진행 상태 통지 |
| 분류 | `status` |
| 발행자 | `Operator UI` |
| 구독자 | `MC` |
| 발행 조건 | 셀 상태가 바뀔 때 (셀마다 3회) |
| 발행 주기 | 상태 변화 시 1회 |
| QoS | `1` |
| Retain | `N` |
| 중요도 | `중요` |

#### Payload 형식

| 항목 | 입력값 |
|---|---|
| 데이터 형식 | `JSON` |
| 문자 인코딩 | `UTF-8` |
| Timestamp 기준 | `UTC Unix Epoch 밀리초` |

```json
{
    "timestamp": "1784727779111",
    "job_id" : "1A",
    "state" : "executing"
}
```

#### Payload 필드

| 필드 경로 | 자료형 | 필수 | 단위 | 허용값/범위 | 설명 | 값 예시 |
|---|---|---|---|---|---|---|
| `timestamp` | `string` | `Y` | `ms` | UTC Unix Epoch 밀리초 | 상태 발생 시각 | `"1784727720000"` |
| `job_id` | `string` | `Y` | - | `1A` ~ `12F` | **격자 이름.** 열(AMR 정차 구역) + 행(리프트 높이) | `"1A"` |
| `state` | `string` | `Y` | - | `waiting`, `executing`, `completed` | 이 격자의 진행 상태 | `"executing"` |

#### 상태값 정리

| 상태 | 의미 | 발행 시점 |
|---|---|---|
| `waiting` | 작업 대기 | 이 셀로 진입해 리프트/AMR 이동을 시작할 때 |
| `executing` | 작업 중 | 이동이 끝나 로봇을 제로점에서 play한 직후 |
| `completed` | 작업 완료 | 로봇이 ㄹ자 스캔을 마쳤을 때 (레지스터 `290==5 && 295==1`) |

`1A → 1B → … → 1F → 2A → … → 12F` 순으로 진행하며, 셀마다 위 세 상태가 순서대로 나간다. 순회는 Operator UI의 `JobSequencer`가 자동으로 진행한다 — 자세한 내용은 [`grid_sequencer_design.md`](grid_sequencer_design.md) 참고.

### T-010: 로봇 동작 속도 비율

로봇 전체 동작 속도를 실시간으로 조절한다. 작업 중에도 바로 반영된다.

#### 기본 정보

| 항목 | 입력값 |
|---|---|
| Topic | `doosan/robot/req/speed` |
| 목적 | 로봇 동작 속도 비율 조절 |
| 분류 | `command` |
| 발행자 | `MC` |
| 구독자 | `Operator UI` |
| 발행 조건 | 속도 변경이 필요할 때 |
| QoS | `1` |
| Retain | `N` |
| 중요도 | `중요` |

```json
{
    "timestamp": "1784727779111",
    "speed" : "40"
}
```

#### Payload 필드

| 필드 경로 | 자료형 | 필수 | 단위 | 허용값/범위 | 설명 | 값 예시 |
|---|---|---|---|---|---|---|
| `timestamp` | `string` | `Y` | `ms` | UTC Unix Epoch 밀리초 | 명령 생성 시각 | `"1784727720000"` |
| `speed` | `string` | `Y` | `%` | `2` ~ `100` | 로봇 전체 동작 속도 비율 | `"40"` |

**동작:** Operator UI 가 받아 `robot/command/speed_ratio` 로 넘기면, 노드가 29999 `speed -set <N>` 으로 로봇에 실시간 전달한다. 로봇이 실제로 쓰고 있는 값은 Modbus **레지스터 17**(읽기)로 확인하며 `robot/status/speed_scale` 토픽으로 나온다. 펜던트에서 직접 바꿔도 이 토픽으로 들어온다.

> **속도 상한:** TCP 직선 속도는 안전 기준상 **150 mm/s** 를 넘지 않는다. 로봇 태스크(`dus_init.script`)가 비율을 곱하기 전에 `v_move`/`v_scan`/`v_seek` 를 0.150 m/s 로 자르므로, 비율 100 % 가 곧 150 mm/s 다. 여기의 `speed` 는 그 위에 곱해지는 비율이라 상한을 넘길 수 없다.

---

## 5. Command Topic 정의

명령 Topic마다 실행 결과를 확인할 수 있도록 요청과 응답의 관계를 작성한다.

| 명령 ID | Command Topic | 발행자 | 실행 장비 | Response/Ack Topic | Timeout | 재시도 | 중복 실행 방지 |
|---|---|---|---|---|---|---|---|
| `C-001` | `doosan/robot/req/mc_cmd` | `Operator UI` | `AMR, Cobot` | `doosan/robot/resp/mc_cmd` | `10sec` | `10sec` | `timestamp 피드백 값 확인` |
| `C-002` | `doosan/robot/req/reset` | `Operator UI` | `AMR, Cobot` | `doosan/robot/resp/reset` | `10sec` | `10sec` | `timestamp 피드백 값 확인` |
| `C-003` | `doosan/robot/req/emc` | `Operator UI` | `AMR, Cobot` | `doosan/robot/resp/emc` | `10sec` | `10sec` | `timestamp 피드백 값 확인` |
| `C-004` | `doosan/robot/req/job_clear` | `Operator UI` | `AMR, Cobot` | `doosan/robot/resp/job_clear` | `10sec` | `10sec` | `timestamp 피드백 값 확인` |
| `C-005` | `doosan/robot/req/job_cmd` | `Operator UI` | `AMR, Cobot` | `doosan/robot/resp/job_cmd` | `10sec` | `10sec` | `timestamp 피드백 값 확인` |



### 5.1 Command Payload 권장 항목

```json
{
  "timestamp": "1784727720000",
  "amr": "stop",
  "cobot": "stop"
}
```

### 5.2 Command 응답 Payload 권장 항목

```json
{
    "timestamp": "1784727720100",
    "t_id" : "mc_cmd",
    "t_stamp" : "1784727720000"
    "result" : "accepted"
} 
```

명령 결과값 후보:

| 결과 | 의미 |
|---|---|
| `accepted` | 명령을 검증하고 실행 대기열에 등록함 |
| `executing` | 명령을 실행 중임 |
| `completed` | 명령 실행을 완료함 |
| `rejected` | 현재 상태 또는 권한 문제로 명령을 거부함 |
| `failed` | 명령 실행 중 오류가 발생함 |

---

## 6. 연결 및 생존 상태

| 항목 | 입력값 |
|---|---|
| Heartbeat Topic | `Heartbeat/robot` |
| Heartbeat 주기 | `1초` |
| Offline 판정 시간 | `10초` |
| Last Will Topic | `Dead/robot` |
| Last Will Payload | `{"status": "OFFLINE", "reason": "unexpected_disconnect"}` |
| Last Will QoS | `2` |
| Last Will Retain | `Y` |



## 7. 확정 전 확인 목록

- [ ] 모든 Topic에 발행자와 구독자가 지정되어 있다.
- [ ] Topic 이름의 대소문자와 구분자 규칙이 통일되어 있다.
- [ ] 상태값과 이벤트, 명령, 명령 응답이 구분되어 있다.
- [ ] 모든 명령에 응답 또는 처리 결과 확인 방법이 있다.
- [ ] 명령 중복 수신 시 같은 작업이 반복 실행되지 않는다.
- [ ] QoS와 Retain 사용 이유가 정의되어 있다.
- [ ] 수치 데이터에 단위와 허용 범위가 기록되어 있다.
- [ ] 연결 끊김과 재연결 처리 방법이 정의되어 있다.
- [ ] Last Will과 Heartbeat 정책이 정의되어 있다.
- [ ] 비정상 Payload 처리 방법이 정의되어 있다.
- [ ] 각 Client의 Topic 접근 권한이 최소 범위로 제한되어 있다.
- [ ] 실제 장비 담당자와 Operator UI 담당자가 Topic 명세를 함께 검토했다.

---

## 8. 변경 이력

| 버전 | 날짜 | 작성자 | 변경 내용 |
|---|---|---|---|
| `0.1` | `[2026-07-27]` | `박태영` | 최초 작성 |
