# screens

운영자가 실제로 이동하며 사용하는 페이지 단위 `QWidget`을 관리하는 폴더다. 각 화면은 레이아웃과 표시 상태를 담당하고 장비 제어 및 DB 작업은 시그널을 통해 상위 애플리케이션이나 서비스 계층에 요청한다.

## 포함 파일

- `main_screen.py`
  - 검사 진행 상황, 원주 검사 화면, 사각형 작업 모델, 주요 제어 버튼을 제공한다.
  - 검사 시작, 일시정지, 수동 제어, 설정 이동, 검사 대상 변경, 작업 영역 변경을 시그널로 요청한다.
  - 작업 공간(workspace) 행에 두 시각화를 나란히 둔다. 왼쪽 `OrbitView`는 원통 검사 대상(지름/높이)을, 오른쪽 `RectWorkView`는 벽면 ㄹ자 스캔 작업 영역(너비/높이/스캐너 높이/겹침)을 보여준다. 둘 다 클릭하면 값 입력 대화상자가 뜬다.
  - `apply_wall_position(가로_mm, 세로_mm)`으로 사각형 작업 모델의 현재 위치 점을 갱신한다. 값은 `robot/status/tcp_pose_zero`(레지스터 280~285, 원점 기준 상대좌표)에서 온다. 로봇 태스크가 베이스 좌표계로 `cur - zero`를 내므로, 가로 = `-rel_y`(오른쪽 +), 세로 = `rel_z`(위 +)로 변환한다.
- `all_screens.py`
  - 수동 제어, Cobot 수동 제어, 검사 실행, 설정 메뉴, I/O 상태, 연결 설정, 시스템·UT·Cobot 설정, 오류 로그, 로그 파일, 운전 모드 화면을 정의한다.
  - `BaseScreen`에서 제목과 이전 버튼을 공통으로 제공한다.
  - `FormScreen`에서 설정값 수집, 적용, 취소, 저장 요청을 공통 처리한다.
  - `ConnectionSettingsScreen`은 협동로봇, 차량용 PLC, MQTT Broker의 유선 연결 정보를 한 화면에서 관리한다.
  - `CobotJogScreen`은 관절·TCP 조그와 기준 위치 저장을 담당한다. 조그는 누르는 동안 움직이므로 `jog_pressed`와 `jog_released`를 모두 보낸다.
    - 현재 관절값과 TCP값은 별도 카드가 아니라 **각 축 이름 옆, 조그 버튼 사이**에 둔다. 움직이는 축의 값을 바로 볼 수 있어야 하기 때문이다. `apply_joint_position()`과 `apply_position()`으로 갱신한다.
    - 홈 위치와 시작 포즈 카드에는 저장되어 있는 값을 함께 보여준다(`set_saved_pose()`). 값은 `robot_task/config/reference_poses.json`에서 읽으며, 로봇에 연결될 때마다 `OperatorWindow`가 레지스터에 다시 쓴다.
    - **조그 버튼은 누르고 있는 동안 `jog_pressed`를 되풀이해 보낸다**(`JOG_REPEAT_MS`). 로봇이 조그 명령을 받은 뒤 정해진 시간(`jog_hold_time`)만 움직이고 스스로 서기 때문이다. 화면이 멈추거나 통신이 끊기면 되풀이가 끊겨 로봇도 곧 선다.
    - 조그는 로봇을 실제로 움직이므로 레지스터 주소가 없으면 `set_enabled_commands()`로 잠근다. 위치 저장은 로봇에 쓰지 못해도 화면 기록은 남길 수 있어 잠그지 않는다.
  - `IOStatusScreen`은 왼쪽에 조회용 목록, 오른쪽에 `출력 제어` 버튼 모음을 둔다. 버튼을 표 칸 안에 넣으면 행 높이가 커져 화면이 답답해진다.
    - **신호 목록은 `config/plc_io.json`에서 읽는다.** 신호가 늘어나도 파일만 고치면 되고 화면 코드는 그대로 둔다. `group`이 있으면 머리글로 묶고, 개수가 늘어날 것에 대비해 버튼 모음은 스크롤된다.
    - 출력(OUT) 신호에만 버튼을 만든다. 입력은 PLC가 정하는 값이라 조작 대상이 아니다.
    - `set_value()`는 목록과 출력 제어의 현재 값을 함께 갱신한다.
  - `CobotManualScreen`은 엘리트 협동로봇의 연결, 전원·브레이크, 프로그램 제어, 홈 이동과 상태 표시를 담당한다.
    - 버튼은 `command_requested`로 명령 키만 보내고, `OperatorWindow`가 `robot/dashboard/*` 서비스로 전달한다. 명령 키를 서비스 이름과 같게 맞춰 두었다.
    - 연결 상태는 직접 판단하지 않고 `robot/status/connected` 값을 `set_connected()`로 받아 표시한다.
    - 화면 아래쪽 영역은 TCP 자세 표시에 사용한다. `TCP 현재값`과 `제로점 기준`을 나누어 각각 6개 성분을 개별 행으로 보여준다. 위치 성분 X, Y, Z는 mm, 회전 성분 RX, RY, RZ는 mrad 단위이며, 성분과 단위의 대응은 `_POSE_AXES` 한 곳에서만 정의한다.
    - 값 갱신은 `apply_tcp()`와 `apply_zero_point()`를 사용하며, 키는 `x`, `y`, `z`, `rx`, `ry`, `rz`이다. 전달하지 않은 성분은 이전 값을 유지한다. 값에는 단위를 붙이지 않는다. 단위는 항목명에 이미 표시된다.
    - 두 자세 값은 `services/ros_status_client.py`가 `robot/status/tcp_pose`와 `robot/status/tcp_pose_zero` 토픽에서 받아 `OperatorWindow`를 거쳐 전달한다. 원본은 `robot_control_node`가 읽는 Modbus 레지스터 384~389와 280~285이다.
    - 로봇 모드·제어 방식·운전 모드는 `apply_status()`로, 알람은 `add_alarm()`으로 받는다. 알람은 최신 항목이 위에 오도록 쌓이며 `MAX_ALARMS`개를 넘으면 오래된 것부터 지운다. 전체를 교체할 때는 `set_alarms()`를 쓴다.
    - 상태 값은 `MetricRow`가 아니라 `StatusBlock`으로 표시한다. `MetricRow`는 항목명과 값을 한 줄에 두어 값이 길면 항목명을 덮기 때문이다.
- `info_screen.py`
  - 제목과 설명만 필요한 간단한 보조 화면을 제공한다.
- `__init__.py`
  - `OperatorWindow`에서 사용할 화면 클래스를 공개한다.

## 화면 전환

모든 화면은 애플리케이션 시작 시 한 번 생성되어 `QStackedWidget`에 추가된다. 화면을 이동해도 위젯은 삭제되지 않으므로 입력값과 선택 상태가 유지된다. 메인 화면에 도착하면 이전 화면 탐색 기록은 초기화된다.

## 작성 규칙

- 화면은 서비스나 장비 드라이버를 직접 호출하지 않는다.
- 사용자 동작은 `pyqtSignal`로 상위 계층에 전달한다.
- 공통 UI는 `components` 폴더의 위젯을 재사용한다.
- 숫자와 텍스트 입력에는 `keypad.py`의 터치 입력 위젯을 사용한다.
- 화면별 인라인 스타일을 반복하지 않고 `styles/app.qss`를 사용한다.
- 메인 이외의 화면에는 공통 `이전` 동작을 제공한다.
- 화면 높이는 720 px로 고정되어 있다. 항목이 많으면 세로로 쌓지 말고 열로 나눈다. `FormScreen`은 `columns` 인자로, 그 밖의 화면은 `QHBoxLayout`으로 처리한다.
- **QSS가 `QPushButton`의 `min-height`를 64 px로 잡는다.** 여러 줄 문구를 넣은 버튼은 이 높이에 갇혀 글자가 잘리므로 세로 크기 정책을 `Expanding`으로 바꾸거나 담는 행의 높이를 직접 키운다.

## 설정 항목명 규칙

`FormScreen`은 **항목명을 그대로 PostgreSQL의 항목 키로 사용한다.** 한 화면 안에서 항목명이 겹치면 나중 위젯이 앞선 위젯을 덮어써 값이 사라진다. 여러 장비를 한 화면에 모을 때는 `PLC IP`, `MQTT 포트`처럼 장비 이름을 접두어로 붙여 항목명을 구분한다.

