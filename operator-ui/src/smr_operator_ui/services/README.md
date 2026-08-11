# services

이 폴더는 화면에서 사용하는 **검사 동작과 데이터 저장 기능**을 제공합니다.

화면 위젯은 사용자 입력과 결과 표시를 담당하고, 시간이 걸리는 처리나 외부 시스템 연동은 이 폴더의 서비스 계층에서 담당합니다. 이를 통해 UI 코드와 업무 로직을 분리하고 화면 멈춤을 방지합니다.

## 주요 파일

### `simulator.py`

- 실제 검사 장비 없이 UI 동작을 시험할 수 있는 검사 시뮬레이터입니다.
- `InspectionSimulator`가 검사 시작, 일시 정지, 재개, 정지 동작을 처리합니다.
- `QTimer`로 검사 단계를 순서대로 진행합니다.
- 상태가 변경되면 `snapshot_changed`, 작업 메시지가 발생하면 `activity` 시그널을 전송합니다.

### `settings_service.py`

- 설정 화면과 PostgreSQL 저장소 사이를 연결하는 Qt 서비스입니다.
- 데이터 조회와 저장을 `QThreadPool`에서 비동기로 실행하여 UI가 멈추지 않도록 합니다.
- 작업 결과를 `loaded`, `saved`, `failed` 시그널로 화면에 전달합니다.
- 작업 중인 워커를 보관하여 실행 도중 제거되지 않도록 관리합니다.

### `settings_repository.py`

- 운영 설정값을 PostgreSQL에 저장하고 불러오는 저장소입니다.
- `SMR_DATABASE_URL` 환경 변수에서 데이터베이스 연결 정보를 읽습니다.
- `operator_settings` 테이블이 없으면 자동으로 생성합니다.
- 설정 범위(`scope`)와 항목 키(`key`)를 기준으로 JSONB 값을 조회하거나 갱신합니다.

### `mqtt_server.py`

- `docs/mqtt_topic_form.md`에 정의된 로봇 MQTT Topic을 관리합니다.
- 외부 MQTT Broker에 비동기로 연결하고 명령, 상태, 응답, Heartbeat Topic을 구독합니다.
- AMR/Cobot 동작, 리셋, 비상정지, Job 초기화 및 신규 Job 명령 발행 API를 제공합니다.
- 수신 Command의 JSON 구조, 허용값, 유효시간과 중복 `timestamp`를 검사합니다.
- 연결 상태와 수신 결과를 Qt 시그널로 화면에 전달합니다.
- 기본 Broker는 `127.0.0.1:1883`이며 `SMR_MQTT_*` 환경 변수로 변경할 수 있습니다.
- 재접속 간격은 1초부터 최대 30초까지 증가하며 10회 실패하면 자동 재접속을 종료합니다.

### `ros_status_client.py`

- `elite_robot_controller`의 `robot_control_node`가 발행하는 자세 토픽을 구독합니다.
- `robot/status/tcp_pose`(현재 절대 TCP)와 `robot/status/tcp_pose_zero`(원점 기준 상대 pose)를 받습니다.
- 값은 `[X, Y, Z, Rx, Ry, Rz]` 순서이며 위치는 mm, 회전은 mrad 단위입니다.
- 구독 콜백은 ROS 실행기 스레드에서 실행되므로 값은 `tcp_pose_changed`, `tcp_pose_zero_changed` 시그널로만 전달합니다.
- 성분이 6개가 아니면 화면에 일부 값만 반영되지 않도록 버리고 `error_occurred`를 보냅니다.
- **rclpy는 선택 의존성입니다.** ROS가 없는 환경에서는 `available`이 `False`가 되고 화면은 그대로 동작합니다. Windows 배포본에서 ROS 없이 실행할 수 있어야 하므로 이 구조를 유지합니다.
- `rclpy.init()`을 이 서비스가 호출한 경우에만 `rclpy.shutdown()`을 수행합니다.

### `__init__.py`

- 다른 모듈에서 공통으로 사용할 서비스 클래스를 공개합니다.
- 현재 `InspectionSimulator`, `SettingsService`, `MqttServer`, `RosStatusClient` 관련 클래스를 패키지 외부에 제공합니다.

## 사용 규칙

- 화면 객체를 서비스 내부에서 직접 생성하거나 조작하지 않습니다. 결과는 Qt 시그널로 전달합니다.
- 데이터베이스 및 장비 통신처럼 시간이 걸릴 수 있는 작업은 GUI 스레드에서 직접 실행하지 않습니다.
- 화면 표시용 문구나 스타일은 `screens` 또는 `styles`에서 관리합니다.
- 애플리케이션 상태 구조는 `state` 폴더의 모델을 사용합니다.
- 데이터베이스 SQL과 연결 처리는 저장소 클래스에 두고, 화면은 `SettingsService`만 사용합니다.
- 실제 장비 연동이 추가되면 시뮬레이터와 동일한 신호 및 상태 전달 방식을 유지해 화면 의존성을 줄입니다.
