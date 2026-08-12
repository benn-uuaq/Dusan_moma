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
| `cobot` | `string` | `Y` | - | `run`, `stop`, `ems` | Cobot에 전달할 동작 명령 | `"stop"` |

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
        "thickness" : "500",
        "target_distance" : "8560"
    },
    "grid" :
    {
        "cell_id" : "A0",
        "segment_index" : "1",
        "segment_count" : "12",
        "grid_index" : "0",
        "grid_count" : "9",
        "width" : "600",
        "height" : "800",
        "scan_h" : "150",
        "overlap" : "20"
    }
}
```

#### Payload 필드

| 필드 경로 | 자료형 | 필수 | 단위 | 허용값/범위 | 설명 | 값 예시 |
|---|---|---|---|---|---|---|
| `timestamp` | `string` | `Y` | `ms` | UTC Unix Epoch 밀리초로 추정 | 명령 생성 시각 | `"1784727720000"` |
| `job_id` | `string` | `Y` | - | `id` | 새로운 job의 id | `"jb00000001"` |
| `job_info` | `dictionary` | `Y` | - | `key` | job information | `"diameter", "height","thickness","target_distance"등 값의 대분류 ` |
| `diameter` | `string` | `Y` | - | `mm` | 검사 대상체(원통) 지름 | `"2500"` |
| `height` | `string` | `Y` | - | `mm` | 검사 대상체(원통) 높이 | `"6000"` |
| `thickness` | `string` | `Y` | - | `mm` | 검사 대상체 두께 | `"500"` |
| `target_distance` | `string` | `Y` | - | `mm` | 이동할 거리 | `"300"` |
| `grid` | `dictionary` | `Y` | - | `key` | 원통이 커서 AMR 원주 구역과 Cobot 세로 격자로 나눠 스캔하기 위한 정보 | 아래 9개 필드 |
| `grid.cell_id` | `string` | `Y` | - | 예: `"A0"`, `"A1"`, `"B0"` | 구역 문자 + 격자 번호로 만든 격자 이름. 문자=구역(`segment_index`), 숫자=격자(`grid_index`, 0부터) | `"A0"` |
| `grid.segment_index` | `string` | `Y` | - | `1` ~ `segment_count` | AMR이 현재 위치한 원주 구역 번호(1부터) | `"1"` |
| `grid.segment_count` | `string` | `Y` | - | `1` 이상 | 원주를 나눈 전체 구역 수. 원통 크기에 따라 12보다 늘어날 수 있다 | `"12"` |
| `grid.grid_index` | `string` | `Y` | - | `0` ~ `grid_count - 1` | 이번에 Cobot이 스캔할 세로 격자 번호(0부터). 구역 안에서 리프트로 높이를 바꿔가며 0, 1, 2... 순서로 올라간다 | `"0"` |
| `grid.grid_count` | `string` | `Y` | - | `1` 이상 | 이 구역의 전체 세로 격자 수 | `"9"` |
| `grid.width` | `string` | `Y` | - | `mm` | 이번 격자의 가로 폭. 로봇 태스크의 `app_width`(레지스터 256)로 전달 | `"600"` |
| `grid.height` | `string` | `Y` | - | `mm` | 이번 격자의 세로 높이. 로봇 태스크의 `app_height`(레지스터 257)로 전달 | `"800"` |
| `grid.scan_h` | `string` | `Y` | - | `mm` | UT 스캐너가 한 자리에서 훑는 세로 유효높이. 로봇 태스크의 `scan_h`(레지스터 258)로 전달 | `"150"` |
| `grid.overlap` | `string` | `Y` | - | `mm`, `0` 이상 `scan_h` 미만 | 세로 겹침. 로봇 태스크의 `overlap`(레지스터 259)로 전달 | `"20"` |

**격자 개념:** 원통이 한 번에 스캔하기엔 너무 크므로 AMR이 원주를 `segment_count`개 구역(`segment_index` = 1, 2, 3...)으로 나눠 이동하고, 각 구역 안에서는 Cobot이 닿을 수 있는 높이만큼만 스캔하므로 세로로 다시 `grid_count`개 격자(`grid_index` = 0, 1, 2...)로 나눈다. 예: 구역 A(`segment_index=1`)의 격자는 `A0, A1, ..., A8`처럼 나열된다.

동작 순서는 다음과 같다: AMR이 구역에 도착 → Cobot이 격자 0을 ㄹ자로 스캔 → 완료 후 Cobot이 홈 위치로 복귀 → 리프트가 상승해 다음 격자 높이로 이동 → Cobot이 격자 1을 스캔 → (해당 구역의 마지막 격자까지 반복) → AMR이 다음 구역으로 이동 → 위 과정을 반복.
#### 명령값 정리

| 대상 | 명령값 | 의미 |
|---|---|---|
| job_id | `jb00000001` | 새로운 job의 id |
| job_info-diameter | `2500` | 검사 대상체 지름 |
| job_info-height | `6000` |  검사 대상체 높이 |
| job_info-thickness | `500` | 검사 대상체 두께 |
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
    {
    "timestamp": "1784727779111",
    "x" : "1205",
    "y" : "852",
    "z" : "1208",
    "angle" : "-1214"
}
}
```

#### Payload 필드

| 필드 경로 | 자료형 | 필수 | 단위 | 허용값/범위 | 설명 | 값 예시 |
|---|---|---|---|---|---|---|
| `timestamp` | `string` | `Y` | `ms` | UTC Unix Epoch 밀리초로 추정 | 명령 생성 시각 | `"1784727720000"` |
| `x` | `string` | `Y` | mm | `0~1000000000` | x 좌표 | `"1000"` |
| `y` | `string` | `Y` | mm | `0~1000000000` | y 좌표 | `"1000"` |
| `z` | `string` | `Y` | mm | `0~1000000000` | z 좌표 | `"1000"` |
| `yaw` | `string` | `Y` | radian | +,- 3.14 | yaw theta| `"1.257"` |

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
