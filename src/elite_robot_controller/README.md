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
| `robot/status/tcp_pose` | `std_msgs/Float32MultiArray` | Modbus 384~389 (현재 TCP, 기본 프레임) |
| `robot/status/tcp_pose_zero` | `std_msgs/Float32MultiArray` | Modbus 280~285 (원점 기준 상대 pose) |
| `robot/status/joint_position` | `std_msgs/Float32MultiArray` | Modbus 73~78 (관절 각도) |
| `robot/status/alarms` | `std_msgs/String` | 30001 알람 |
| `robot/status/connected` | `std_msgs/Bool` | 세 채널 연결 여부 |

## Modbus 레지스터 맵

**주소는 코드에 두지 않고 [`config/modbus_registers.json`](config/modbus_registers.json) 한 곳에서만 관리한다.** 주소가 바뀌거나 새로 확인되면 이 파일만 고치면 되고 코드는 건드리지 않는다. 운영 UI도 같은 파일을 읽으므로 정의가 갈라지지 않는다.

`register_map` 파라미터로 다른 경로를 지정할 수 있다.

```bash
ros2 run elite_robot_controller robot_control_node --ros-args -p register_map:=/경로/my_registers.json
```

### 현재 내용

| 항목 | 주소 | 내용 |
| --- | --- | --- |
| `read.robot_mode` | 66 | 로봇 모드 |
| `read.control_method` | 71 | 제어 방식 |
| `read.operation_mode` | 72 | 운전 모드 |
| `read.joint_position` | 73~78 | 관절 각도 (베이스, 어깨, 엘보, 손목 1~3) |
| `read.tcp_absolute` | 384~389 | 현재 TCP (기본 프레임) |
| `read.tcp_zero_relative` | 280~285 | 원점 기준 상대 pose |
| `write.linear_speed` | 306 | 작업 속도 [mm/s]. 태스크의 `movel` 속도 |
| `write.speed_ratio` | 307 | 로봇 자체 속도 비율 [%] 2~100 |
| `write.pose_src` | 308 | 1이면 태스크가 310~321의 기준 위치를 쓴다 |
| `write.home_joint` | 310~315 | 홈 관절값 [mrad] |
| `write.start_pose` | 316~321 | 시작 포즈 (길이 0.1 mm, 회전 mrad) |

쓰기 주소는 로봇 태스크가 읽는 범용 레지스터 대역(256~383)에 맞췄습니다. 태스크가 이미 쓰는 256~305는 피했습니다.

`address`가 `null`인 항목은 주소 미확정을 뜻한다. **노드는 해당 요청을 거부하고 Modbus 접근 자체를 하지 않는다.** 엉뚱한 레지스터에 쓰면 로봇이 예기치 않게 움직이기 때문이다. 운영 UI도 같은 파일을 읽어 해당 버튼을 비활성화한다.

### 자세 값 환산

자세는 축마다 레지스터 1개씩 `[X, Y, Z, Rx, Ry, Rz]` 순서로 6개가 연속 배치된다. `Robot_modbus.get_all_registers()`가 부호 있는 16비트로 변환해 주므로 노드에서는 단위 환산만 한다.

항목의 `kind`가 환산 방법을 정한다. `pose`는 앞 3개를 위치, 뒤 3개를 회전으로 보고, `angle`은 6개 모두 회전으로 본다. 관절 각도는 6축 전부 mrad이므로 `angle`이다.

| 성분 | 설정 항목 | 환산 | 발행 단위 |
| --- | --- | --- | --- |
| X, Y, Z (`pose`) | `scale.position_per_count` | 레지스터 × 0.1 | mm |
| Rx, Ry, Rz (`pose`), 관절 6축 (`angle`) | `scale.rotation_per_count` | 레지스터 × 1.0 | mrad |

레지스터를 6개 모두 읽지 못하면 잘못된 자세를 내보내지 않도록 발행을 건너뛴다.

> **확인 필요:** 위치 환산 계수 0.1은 이식 전 코드의 값을 그대로 이어받은 것으로, 실장비에서 검증하지 않았다. 레지스터가 0.1 mm가 아니라 1 mm 단위라면 설정 파일의 `position_per_count`만 1.0으로 바꾸면 된다.

## 명령 인터페이스

| 종류 | 이름 | 쓰기 항목 |
| --- | --- | --- |
| 서비스 (`std_srvs/Trigger`) | `robot/command/save_home_pose` | `save_home_pose` |
| 서비스 (`std_srvs/Trigger`) | `robot/command/save_start_pose` | `save_start_pose` |
| 서비스 (`std_srvs/Trigger`) | `robot/command/move_home` | `move_home` |
| 토픽 (`std_msgs/Int32`) | `robot/command/linear_speed` | `linear_speed` |
| 토픽 (`std_msgs/Int32`) | `robot/command/speed_ratio` | `speed_ratio` |
| 토픽 (`std_msgs/Float32MultiArray`) | `robot/command/home_joint` | `home_joint` |
| 토픽 (`std_msgs/Float32MultiArray`) | `robot/command/start_pose` | `start_pose` |
| 토픽 (`std_msgs/Int32`) | `robot/command/jog_joint` | 없음 (30001 스크립트) |
| 토픽 (`std_msgs/Int32`) | `robot/command/jog_tcp` | 없음 (30001 스크립트) |

값이 없는 한 번짜리 명령은 성공 여부를 돌려받아야 하므로 서비스로, 값이 있는 명령은 토픽으로 받습니다.

### 조그와 홈 이동

조그와 홈 이동은 레지스터가 아니라 **30001 포트로 스크립트를 보내** 처리합니다.

- 조그 값은 **부호가 방향, 절댓값이 축 번호(1~6)** 이며 `0`은 정지입니다.
- 움직일 때는 `speedj` / `speedl`을 보내고, **멈출 때는 29999의 `stop`** 을 씁니다. 감속 없이 즉시 서기 위해서입니다.
- **조그 속도는 레지스터 307(속도 비율 %)을 그대로 따릅니다.** 단위가 달라 아래 파라미터를 100 % 기준으로 두고 비율만큼 줄입니다.

| 파라미터 | 기본값 | 단위 | 대상 |
| --- | --- | --- | --- |
| `jog_joint_speed_max` | 0.50 | rad/s | `speedj` 의 `qd` |
| `jog_tcp_speed_max` | 0.10 | m/s | `speedl` 의 `xd` 앞 3개 |
| `jog_tcp_rot_speed_max` | 0.50 | rad/s | `speedl` 의 `xd` 뒤 3개 |
| `jog_accel_max` | 1.00 | rad/s² · m/s² | `a` |
| `jog_hold_time` | 0.5 | s | `t` |

예를 들어 비율이 20 %면 관절 조그는 0.1 rad/s, 가속도는 0.2가 됩니다.

`t`(`jog_hold_time`)는 **짧게 둡니다.** 매뉴얼상 로봇은 `t` 동안 계속 움직이므로, 길게 주면 정지 명령이 실패했을 때 그 시간만큼 멈추지 않습니다. 대신 운영 UI가 버튼을 누르고 있는 동안 명령을 되풀이해 보내고(150 ms 간격), 화면이 멈추거나 통신이 끊기면 되풀이가 끊겨 로봇도 `t` 안에 섭니다.
- `robot/command/move_home`은 `home_lift_z`까지 `movel`로 올린 뒤 310~315에 저장된 관절값으로 `movej` 합니다. 태스크의 `move_home` 노드와 같은 순서입니다.

> `speedj`/`speedl`은 컨트롤러의 속도 백분율 설정에 영향을 받습니다(스크립트 매뉴얼 3.1.26/3.1.27). 100 %가 아니면 실제 속도가 그만큼 줄어듭니다.

**서비스** (모두 `std_srvs/Trigger`)

`robot/dashboard/` 아래:

| 서비스 | 동작 |
| --- | --- |
| `connect` / `disconnect` | 세 채널을 연결하거나 해제한다. Modbus 레지스터를 쓰지 않는다 |
| `robot_mode`, `status` | 29999 Dashboard 조회 명령 |
| `power_on`, `power_off`, `brake_release` | 전원과 브레이크 |
| `play`, `pause`, `stop` | 프로그램 제어 |

Dashboard 명령은 29999 소켓으로 나가므로 레지스터 주소와 무관하게 바로 쓸 수 있습니다. 반면 `robot/command/*`는 Modbus 레지스터에 쓰므로 주소가 정해져야 동작합니다.

## 실행

```bash
ros2 run elite_robot_controller robot_control_node --ros-args -p robot_ip:=192.168.227.134
```

```bash
ros2 launch elite_robot_controller elite_cs612.launch.py robot_ip:=192.168.227.134
```

**로봇이 연결되어 있지 않아도 노드는 종료하지 않습니다.** 기동 시 한 번 연결을 시도하고, 실패하면 경고만 남긴 뒤 대기합니다. 운영 UI의 `연결` 버튼이나 `robot/dashboard/connect` 서비스로 다시 시도할 수 있습니다. 연결 전에는 Modbus 읽기를 건너뛰고 `robot/status/connected`로 상태만 알립니다.

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
