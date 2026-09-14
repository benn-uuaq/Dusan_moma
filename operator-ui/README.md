# SMR Operator UI

SMR 비파괴 검사 자동화 시스템의 운영자 UI 프로젝트입니다.

현재 단계는 UI 기준과 화면 구조를 확정하는 설계 단계입니다. UI는 Python 3.10 이상과 PyQt6를 사용하는 데스크톱 애플리케이션으로 구현합니다.

## 실행

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[test]"
.venv\Scripts\python -m smr_operator_ui
```

현재 구현은 실제 장비 대신 비차단 더미 서비스를 사용합니다. `검사 시작`을 누르면 12개 원주 구간에서 안전 순서가 반복되며, `일시정지`와 `재개`가 동작합니다.

## ROS 2 연동 (선택)

`Cobot 수동 제어` 화면의 TCP 자세 표시는 ROS 2 토픽에서 값을 받습니다.

| 토픽 | 화면 표시 |
| --- | --- |
| `robot/status/tcp_pose` | Cobot 수동 제어의 TCP 현재값 |
| `robot/status/tcp_pose_zero` | Cobot 수동 제어의 제로점 기준 값 + 메인 화면 사각형 작업 모델의 현재 위치 |

값은 `dusan_ws`의 `elite_robot_controller` 패키지가 Modbus에서 읽어 발행합니다. UI를 실행하기 전에 해당 노드를 띄우고 UI도 ROS 2가 소싱된 셸에서 실행해야 합니다.

```bash
ros2 launch elite_robot_controller elite_cs612.launch.py robot_ip:=192.168.227.134
authbind --deep python -m smr_operator_ui
```

**`authbind --deep`가 필요한 이유:** "TPAC 설정 / TCP 인코딩" 화면이 여는 외부 제공 서버는 기본 포트가 502인데, 1024 미만 포트는 리눅스에서 일반 사용자가 열 수 없습니다(`[Errno 13] Permission denied`). `sudo setcap cap_net_bind_service=+ep`를 `python3`에 직접 걸면 이 문제는 해결되지만, 리눅스 동적 로더가 capability 있는 실행 파일에서는 `LD_LIBRARY_PATH`를 무시해 버려서 **ROS 2가 라이브러리를 못 찾고 깨집니다**(`ImportError: librcl_action.so`). `python3`는 ROS도 같이 쓰는 공유 바이너리라 이 방법은 쓰면 안 됩니다.

대신 `authbind`로 이 프로세스 하나에만 502 바인드 권한을 줍니다(최초 1회 설정, `python3` 자체는 건드리지 않습니다):

```bash
sudo apt-get install -y authbind
sudo touch /etc/authbind/byport/502
sudo chmod 500 /etc/authbind/byport/502
sudo chown "$(whoami)" /etc/authbind/byport/502
```

이후 UI를 authbind 없이(`python -m smr_operator_ui`) 실행해도 나머지는 다 되지만, TPAC 서버만 `[Errno 13]`으로 시작에 실패합니다 — 화면의 통신 로그에 같은 안내가 뜹니다.

**rclpy는 선택 의존성입니다.** ROS 2가 없는 환경에서는 자세 값이 `-`로 남고 나머지 화면은 그대로 동작합니다. Windows 배포본을 ROS 없이 실행할 수 있도록 하기 위한 구조이므로 `pyproject.toml`의 의존성에 `rclpy`를 넣지 않습니다.

ROS 2를 소싱한 셸에서 테스트를 실행하면 ROS의 pytest 플러그인이 함께 로드되어 이 프로젝트가 요구하는 pytest 8 이상과 충돌합니다. `pyproject.toml`에서 해당 플러그인을 비활성화해 두었으므로, **`operator-ui` 폴더 안에서** 테스트를 실행하면 됩니다. 이 설정은 `pyproject.toml`이 있는 위치를 기준으로 적용되므로 워크스페이스 루트에서 실행하면 충돌이 다시 발생합니다.

```bash
cd operator-ui && python -m pytest
```

이 작업공간에서는 의존성이 `operator-ui/.deps`에 준비되어 있으므로 다음 명령으로도 실행할 수 있습니다.

```powershell
.\run.ps1
```

## 기준 문서

- `design.md`: 화면 구조, 디자인 토큰, 상태 표현, 안전 UX 원칙
- `agent.md`: PyQt6 구현 및 검증 작업 지침
- `../docs/erounsolution_requests/pptx/UI Design.pptx`: 최신 UI 참고안
- `../docs/erounsolution_requests/pptx/UI Design_draft0.pptx`: 변경 비교용 초기안

## 예정 구조

```text
operator-ui/
├── README.md
├── design.md
├── pyproject.toml
├── src/
│   └── smr_operator_ui/
│       ├── __main__.py
│       ├── app.py
│       ├── components/
│       ├── screens/
│       ├── services/
│       ├── state/
│       ├── styles/
│       └── resources/
└── tests/
```

구현을 시작할 때 `design.md`의 장비 통신 및 배포 관련 미확정 항목을 먼저 확인합니다.
## PostgreSQL 설정 저장소

시스템, UT, Cobot 및 검사 대상 설정은 PostgreSQL의 `operator_settings` 테이블에 저장됩니다.
테이블은 애플리케이션이 최초 연결할 때 자동으로 생성합니다. 접속 문자열은 소스 코드에
저장하지 않고 `SMR_DATABASE_URL` 환경변수로 전달합니다.

```powershell
$env:SMR_DATABASE_URL = "postgresql://사용자:비밀번호@서버:5432/데이터베이스"
.\run.ps1
```

필요한 PostgreSQL 권한은 대상 데이터베이스의 접속 권한과 테이블 생성·조회·입력·수정
권한입니다. `SMR_DATABASE_URL` 이 없으면 설정은 로컬 JSON 파일
(`~/.config/smr-operator-ui/settings.json`, `SMR_SETTINGS_FILE` 로 변경)에 저장됩니다.

## 운영 기록 (데이터 저장 위치)

시스템 설정의 **데이터 저장 위치** 아래에 네 가지 기록을 남깁니다
(`services/data_recorder.py`). 항목별 폴더 → 연도/월 폴더 → 날짜로 시작하는 파일입니다.

```text
<데이터 저장 위치>/
  작업기록/2026/09/20260914_작업기록.xlsx               구간 하나당 한 줄 (하루 파일 하나)
  스캔좌표/2026/09/20260914_153012_jb00000001_1A.txt   구간 하나당 파일 하나
  알람이벤트/2026/09/20260914_알람이벤트.txt            하루 파일 하나
  통신기록/2026/09/20260914_통신기록.txt                하루 파일 하나
```

| 기록 | 형식 | 내용 |
|---|---|---|
| 작업기록 | xlsx | 날짜·시작·종료·소요, 출처(ERUT/MC/RCS), job_id, 구간, 결과(완료/중단), 비고(그 구간의 알람·장애), 호 길이·격자 높이·격자간 겹침·반지름·두께, 프로브, 태스크 판, 좌표 수, 스캔 좌표 파일. 마킹은 `구간=마킹` 한 줄 |
| 스캔좌표 | txt (탭 구분) | ㄹ자 스캔 중(로봇 상태 290 = 6)인 원점 기준 좌표. 시각·경과·구간·X·Y·Z[mm]·Rx·Ry·Rz[deg] (Rz +180° 좌표계) |
| 알람이벤트 | txt (탭 구분) | 시각·구분(정보/알람/장애/해제/알림)·코드·수준·내용 |
| 통신기록 | txt (탭 구분) | 시각·방향(수신/발신)·채널(ERUT/MC)·토픽·원문. 좌표 스트림(`doosan/robot/tcp`)과 하트비트는 제외 |

- 텍스트 기록은 한 줄씩 바로 덧붙여, 프로그램이 도중에 꺼져도 그때까지는 남습니다.
  UTF-8(BOM) 이라 Windows 엑셀·메모장에서 한글이 깨지지 않습니다.
- 작업기록 엑셀을 열어 둔 채(Windows 잠금) 기록이 나면 버리지 않고 들고 있다가, 닫으면 1분 안에 저장합니다.
- **로그 보존 기간**(일)이 지난 파일은 시작할 때와 6시간마다 지웁니다. 파일 이름이 날짜로
  시작하는 우리 기록만 지우고, 비게 된 월·연도 폴더도 정리합니다.
- WSL 에서는 `D:/SMR/Data` 가 `/mnt/d/SMR/Data` 로 쓰입니다. WSL 이 아닌 리눅스에서 Windows
  경로가 들어 있으면 `~/SMR/Data` 를 씁니다. `SMR_DATA_DIR` 환경변수가 있으면 그것이 우선입니다.
