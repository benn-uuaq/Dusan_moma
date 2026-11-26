# steer_agv_controller_py

ROS 2 Humble의 QUAD_STEER_AGV_CONTROLLER가 10 ms 주기로 제어한다. PLC 접속을 켜면 TCP 워커 한 개가 읽기와 쓰기를 직렬 처리한다. ROS 좌표는 X 전방, Y 좌측이고 거리·속도·각도는 m, m/s, rad 단위다.

## 빌드와 실행

```bash
cd ~/ros2_ws
source /opt/ros/humble/setup.bash
colcon build --packages-up-to steer_agv_bringup --symlink-install
source install/setup.bash
ros2 launch steer_agv_bringup controller.launch.py
```

기본 실행은 PLC 접속을 끈 상태로 제어기와 robot_state_publisher를 시작한다. 피드백만 읽을 때는 launch 인자 `plc_enabled:=true`를 사용한다. PLC 명령과 PC LIFE까지 쓸 때는 `plc_enabled:=true plc_writes_enabled:=true`를 함께 지정한다. 기본 대상은 192.168.11.10:8501이고 공통 설정은 steer_agv_bringup/config/controller.yaml에 있다.

RViz2에서 Fixed Frame을 odom으로 설정하고 RobotModel, TF, Path(/vehicle/path)를 추가한다. 휠·리프트 조인트, odom과 경로는 실제 PLC 피드백이 들어올 때 갱신된다.

## RCS 동작

- `/vehicle/set_job`은 Auto에서 작업을 등록한다. `/vehicle/robot_control`의 RUNNING이 출발 또는 재개를 요청한다. 주행 중 PAUSE는 감속 후 정지 피드백을 확인하고 DM103=4를 보낸다. STOP·PAUSE는 작업을 보존하며 job_cancel=1은 폐기한다. 도달은 차량 중심의 odom 이동 거리와 정지 유지시간으로 판정한다. HOLD 상태는 PLC 피드백으로 보고한다.
- `/vehicle/manual_command`의 주행 출력은 DRIVE Manual에서만 허용하며 기본 0.4초 동안 갱신이 없으면 해제한다. 감속 비트가 켜지면 수동 조그 출력을 해제한다. Auto 주행의 감속 목표는 obstacle_slow_speed로 설정한다.
- `/vehicle/lift_control`은 HOLD(DM002=2)·아웃트리거 고정 피드백을 확인하고 높이를 보낸다. Auto에서는 속도를 먼저 쓴다. 수동 높이 요청은 PLC 속도를 사용한다. 협동로봇 safety reset 확인은 호출 측 RCS가 담당한다.
- `/vehicle/reset_odometry`는 정지 상태에서 x, y, yaw를 받아 pose와 엔코더 기준을 재설정한다. `/vehicle/robot_status`, `/vehicle/start_request`, `/vehicle/robot_estop`, `/odom`, `/joint_states`, `/vehicle/path`, TF, `/diagnostics`를 발행한다.
- RESET은 유효 요청마다 DM104=1을 한 번 보내고 PLC OK를 기다린다. 응답 대기 중에도 제어 타이머가 동작한다. 응답을 잃은 RESET은 자동 재전송하지 않는다. PLC 오류 코드가 남아 있으면 원인 제거 후 요청해야 한다.

## 시운전 기준

차체 왼쪽(+Y)의 레이저 가이드 센서 방향에 원 중심을 두므로 `turn_sign` 기본값은 +1이다. 전진 시 ROS 기준 반시계 방향이고, 후진 시 같은 원을 시계 방향으로 따라간다. 각 휠의 PLC 조향 피드백은 전방 0, 반시계 방향 양수로 가정한다. 정지 허용 오차, 수동 만료시간, 장애물 감속 속도 및 리프트 높이·속도 상한은 시운전 설정값이다. 실제 기계 한계, 조향 부호, 정지·리프트 취소 반응, PLC 측 PC LIFE timeout과 10 ms 주기 달성 여부는 장비에서 확인해야 한다.

QR 입력 계약과 ALIGN 완료 기준이 확정되기 전에는 `/vehicle/measure_lap`이 사유를 담아 거절한다. cmd_init도 DM105 트리거·해제 규약이 확정될 때까지 거절한다. PLC STATE=16 진입은 내부 작업과 휠 기준을 초기화하고 외부에는 INITIALIZE로 보고한다. C++ 실행 파일은 후속 버전이다.

## 모의 PLC 검증

```bash
source ~/ros2_ws/install/setup.bash
cd ~/ros2_ws
colcon test --packages-select steer_agv_controller_py
colcon test-result --verbose --test-result-base build/steer_agv_controller_py
```

통합 테스트는 localhost TCP 모의 PLC와 별도 ROS 클라이언트로 작업 등록, 후진, HOLD, 리프트, 반복 RESET, 장애물 정지, 수동 만료와 ACK 실패를 검증한다.

작업 결과 토픽은 시작 시 `job_feedback_topic`으로 설정한다. 기본값은 `/vehicle/last_job_request`이며 독립 UI에도 같은 파라미터를 전달한다.
