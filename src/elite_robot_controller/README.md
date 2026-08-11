# elite_robot_controller

Elite CS612 협동로봇을 ROS 2에서 제어하기 위한 패키지입니다. AMR 위에 올라가는 매니퓰레이터의 제어부에 해당합니다.

`~/ws_elt`의 `robot_controller` 패키지에서 **드라이버, 제어 노드, launch 파일만** 이식했습니다. 원본의 `ui/`, `tasks/`(팔레타이징·언로딩·픽앤플레이스)는 ws_argo 프로젝트 잔재로 `argo_project_interfaces`에 의존하고 벽면 검사와 무관하여 가져오지 않았습니다.

## 통신 구조

로봇 컨트롤러와 세 개의 채널로 동시에 통신합니다. 세 채널이 모두 연결되어야 노드가 기동합니다.

| 포트 | 클래스 | 역할 |
| --- | --- | --- |
| 29999 | `Robot_29999` | Dashboard. 텍스트 명령으로 전원, 브레이크, 재생/일시정지/정지 제어 |
| 30001 | `Robot_30001` | Primary. 바이너리 패킷 스트림에서 로봇 상태와 알람 수집 |
| 502 | `Robot_modbus` | Modbus TCP. 레지스터 단위 상태 조회 및 설정 |

## 노드

### `robot_control_node`

10 Hz(`0.1초`) 주기로 Modbus 레지스터를 읽고 30001 알람을 수집하여 발행합니다.

**파라미터**

| 이름 | 기본값 | 설명 |
| --- | --- | --- |
| `robot_ip` | `192.168.227.134` | Elite 컨트롤러 IP |

**발행 토픽**

| 토픽 | 타입 | 출처 |
| --- | --- | --- |
| `robot/status/robot_mode` | `std_msgs/Int32` | Modbus 66 |
| `robot/status/control_method` | `std_msgs/Int32` | Modbus 71 |
| `robot/status/operation_mode` | `std_msgs/Int32` | Modbus 72 |
| `robot/status/tcp_pose` | `std_msgs/Float32MultiArray` | Modbus 260~265 (현재 절대 TCP) |
| `robot/status/tcp_pose_zero` | `std_msgs/Float32MultiArray` | Modbus 280~285 (원점 기준 상대 pose) |
| `robot/status/alarms` | `std_msgs/String` | 30001 알람 |

## Modbus 레지스터 맵

주소는 `RobotControlNode`의 클래스 상수로 한 곳에 모아 두었다.

| 상수 | 주소 | 내용 |
| --- | --- | --- |
| `REG_ROBOT_MODE` | 66 | 로봇 모드 |
| `REG_CONTROL_METHOD` | 71 | 제어 방식 |
| `REG_OPERATION_MODE` | 72 | 운전 모드 |
| `REG_TCP_ABSOLUTE` | 260~265 | 현재 절대 TCP |
| `REG_TCP_ZERO_RELATIVE` | 280~285 | 원점 기준 상대 pose |

자세는 축마다 레지스터 1개씩 `[X, Y, Z, Rx, Ry, Rz]` 순서로 6개가 연속 배치된다. `Robot_modbus.get_all_registers()`가 부호 있는 16비트로 변환해 주므로 노드에서는 단위 환산만 한다.

| 성분 | 상수 | 환산 | 발행 단위 |
| --- | --- | --- | --- |
| X, Y, Z | `POSITION_SCALE` | 레지스터 × 0.1 | mm |
| Rx, Ry, Rz | `ROTATION_SCALE` | 레지스터 × 1.0 | mrad |

레지스터를 6개 모두 읽지 못하면 잘못된 자세를 내보내지 않도록 발행을 건너뛴다.

> **확인 필요:** 위치 환산 계수 0.1은 이식 전 코드의 값을 그대로 이어받은 것으로, 실장비에서 검증하지 않았다. 레지스터가 0.1 mm가 아니라 1 mm 단위라면 `POSITION_SCALE`만 1.0으로 바꾸면 된다.

**서비스** (모두 `std_srvs/Trigger`, 29999 Dashboard 명령에 대응)

`robot/dashboard/` 아래: `robot_mode`, `status`, `power_on`, `power_off`, `brake_release`, `play`, `pause`, `stop`

## 실행

```bash
ros2 run elite_robot_controller robot_control_node --ros-args -p robot_ip:=192.168.227.134
```

```bash
ros2 launch elite_robot_controller elite_cs612.launch.py robot_ip:=192.168.227.134
```

**로봇이 연결되어 있지 않으면** 세 채널 연결에 실패하고 `[ERROR] 로봇 연결 실패` 로그를 남긴 뒤 `SystemExit`로 종료합니다. 이는 의도된 동작입니다.

## 의존성

`package.xml`의 `rclpy`, `std_msgs`, `std_srvs` 외에 **`pyModbusTCP`가 pip 의존**으로 필요합니다. rosdep 키가 없어 `package.xml`에 선언하지 않았습니다.

```bash
pip install pyModbusTCP
```

## 테스트

```bash
colcon test --packages-select elite_robot_controller && colcon test-result --verbose
```

`test_pose_publishing.py`는 Modbus 클라이언트를 대체해 로봇 없이 자세 레지스터 주소와 단위 환산을 검증한다.

**워크스페이스 가상환경의 `pytest`로 직접 실행하지 말 것.** ROS Humble의 `launch_testing` 플러그인은 pytest 6 API를 사용하는데 `operator-ui`가 pytest 8 이상을 요구해 같은 가상환경에서 충돌한다. `colcon test`는 정상 동작한다.

`flake8`과 `pep257` 검사는 현재 실패한다. `robot_driver.py`와 `robot_control_node.py`를 `ws_elt`에서 로직 수정 없이 이식했고 원본이 이미 스타일 규칙을 지키지 않았기 때문이다. 일괄 정리는 실장비 검증 이후로 미룬다.

## 주의

`robot_driver.py`는 Elite 컨트롤러의 바이너리 패킷 구조체 포맷(`FMT_*` 상수)에 강하게 결합되어 있습니다. 컨트롤러 펌웨어 버전이 바뀌면 파싱이 깨질 수 있으므로 포맷 문자열을 함부로 수정하지 마십시오.
