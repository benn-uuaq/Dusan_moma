# SMR 비파괴 검사 자동화 프로젝트

소형 원자로(SMR) 설비의 초음파 비파괴 검사(UT)를 자동화하기 위한 프로젝트 문서 및 개발 작업 공간입니다.

## 시스템 구성

- 리프트 및 아웃트리거 장착 AMR: 리모컨 수동 이동, 목적지 도착 후 자동 원주 이동, 본체 고정, IMU 기반 수평 보정
- 협동로봇: 초음파 프로브의 위치 제어, 접촉 유지, 스캔 경로 수행
- 초음파 검사기: UT 신호 취득, 저장, 결함 판정 지원
- IMU 센서: AMR 본체의 roll, pitch 확인
- 아웃트리거: 협동로봇 검사 중 AMR 본체 고정 및 수평 보정

## 기본 검사 흐름

1. 작업자가 리모컨으로 AMR을 검사 목적지까지 수동 이동한다.
2. AMR이 소형원자로 또는 기준 구조물을 기준으로 검사 시작 위치를 확인한다.
3. 아웃트리거를 전개하고 IMU 센서로 본체 수평을 확인한다.
4. 아웃트리거 높이를 조정하여 AMR 본체 수평을 보정한다.
5. 수평 상태가 허용 범위에 들어오면 협동로봇 검사를 시작한다.
6. 협동로봇이 초음파 프로브를 접촉시키고 검사 데이터를 취득한다.
7. 검사 구간 완료 후 협동로봇을 안전 위치로 후퇴시킨다.
8. AMR이 소형원자로를 기준으로 다음 검사 위치까지 자동 원주 이동한다.
9. 수평 보정과 검사를 반복한다.
10. 데이터 저장, 분석, 리포트 작성 후 장비를 복귀시킨다.

## 문서 구조

- [프로젝트 개요](docs/01_project_overview.md)
- [요구사항 정의](docs/02_requirements.md)
- [시스템 아키텍처](docs/03_system_architecture.md)
- [검사 운영 절차](docs/04_inspection_workflow.md)
- [소프트웨어 개발 계획](docs/05_software_plan.md)
- [안전 및 리스크 관리](docs/06_safety_risk.md)
- [데이터 관리 계획](docs/07_data_management.md)
- [AMR 수동 이동 및 아웃트리거 수평 보정](docs/08_amr_leveling_and_outrigger.md)
- [로봇 시스템 구매사양서 반영 정리](docs/09_robot_system_purchase_spec.md)
- [이로운 솔루션 요청사항 관리](docs/erounsolution_requests/README.md)
- [회의록](docs/meeting_notes.md)
- [작업 로그](docs/work_log.md)
- [할 일 목록](docs/todo.md)
- [엘리트 협동로봇 제어 패키지](src/elite_robot_controller/README.md)

## 저장소 구성

| 경로 | 내용 |
| --- | --- |
| `docs/` | 프로젝트 문서 및 발주처 요청자료 |
| `operator-ui/` | 운영자 UI (PyQt6 데스크톱 앱). 독립 실행되며 colcon 빌드 대상이 아니다 |
| `src/` | ROS 2 패키지 |
| `tools/`, `work/` | 문서 생성 스크립트 및 산출물 |

## 개발 환경 준비

Ubuntu 22.04 + ROS 2 Humble 환경을 전제로 한다. 이 저장소는 colcon 워크스페이스 루트이므로, 클론한 디렉터리가 곧 워크스페이스가 된다.

### 1. 가상환경 생성

**`--system-site-packages`가 반드시 필요하다.** 이 옵션이 없으면 가상환경 안에서 `rclpy`와 `colcon`이 보이지 않아 빌드와 실행이 모두 실패한다.

```bash
python3 -m venv --system-site-packages .venv
```

### 2. Python 의존성 설치

```bash
.venv/bin/pip install pyModbusTCP
```

### 3. 환경 자동 활성화 (direnv)

저장소에 `.envrc`가 포함되어 있다. ROS 소싱 → 워크스페이스 소싱 → 가상환경 활성화를 디렉터리 진입 시 자동으로 수행한다.

```bash
sudo apt install direnv           # 최초 1회
echo 'eval "$(direnv hook bash)"' >> ~/.bashrc
direnv allow
```

direnv를 쓰지 않는다면 매번 아래를 수동으로 실행한다.

```bash
source /opt/ros/humble/setup.bash && source install/setup.bash && source .venv/bin/activate
```

### 4. 빌드

```bash
colcon build --symlink-install
```

빌드 후 `direnv reload`(또는 `source install/setup.bash`)로 워크스페이스를 다시 소싱한다.

### 5. 동작 확인

```bash
ros2 launch elite_robot_controller elite_cs612.launch.py robot_ip:=192.168.227.134
```

로봇이 연결되어 있지 않아도 노드는 계속 실행된다. 경고만 남기고 대기하며, 운영 UI의 `연결` 버튼이나 `robot/dashboard/connect` 서비스로 다시 시도할 수 있다.

### 운영자 UI 실행

`operator-ui/`는 ROS 2와 별개로 동작하는 PyQt6 앱이다. 실행 방법은 [operator-ui/README.md](operator-ui/README.md)를 참고한다.

### 실제 로봇 없이 전체 흐름 시험

로봇이 물리적으로 연결되어 있지 않으면 `robot_ip`(기본 `192.168.227.134`)로는 당연히 연결되지 않는다. 로봇 없이 UI까지 함께 시험하려면 [`src/elite_robot_controller/tools/elite_robot_simulator.py`](src/elite_robot_controller/tools/elite_robot_simulator.py)를 쓴다. 절차는 [패키지 README](src/elite_robot_controller/README.md#로봇-없이-시험-시뮬레이터)를 참고한다.

## 진행 원칙

- 검사 신뢰도와 재현성을 우선한다.
- 장비 동작, 검사 조건, 데이터 파일의 추적성을 확보한다.
- 수동 이동 구간과 자동 검사 구간을 명확히 구분한다.
- 협동로봇 검사 중 AMR 주행, 아웃트리거 회수, 수평 이탈을 인터락으로 차단한다.
- 안전 정지, 충돌 방지, 작업자 보호를 설계 초기부터 반영한다.
