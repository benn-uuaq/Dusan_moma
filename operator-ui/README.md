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
python -m smr_operator_ui
```

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
권한입니다. 환경변수가 없거나 연결에 실패하면 설정 화면에 저장소 오류가 표시되며,
입력값을 저장 완료로 처리하지 않습니다.
