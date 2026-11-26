# four_steer_amr_plugin 주행 시험

ROS 2 Humble 및 Gazebo Classic 환경에서 실행한다.

```bash
cd ~/ros2_ws
source /opt/ros/humble/setup.bash
colcon build --packages-select steer_agv_description steer_agv_sim_test
source install/setup.bash
ros2 run steer_agv_sim_test cmd_vel_test
```

기본 실행은 별도 ROS 도메인과 Gazebo master에서 화면 없이 평지와 steer_agv 모델을 띄운다. 시뮬레이션에서는 아웃트리거를 올리고 설치된 libfour_steer_amr_plugin.so에 8개 조인트를 연결한다. /cmd_vel(Twist)의 linear.x와 angular.z에 20 Hz로 명령하며, 플러그인의 /odom으로 전진·후진·제자리 좌회전·정지를 확인한다. 각 단계의 이동량과 요 변화는 시작 시점 차체 기준이다.

기존 Gazebo 모델에서 시험하려면 해당 ROS 도메인에서 `ros2 run steer_agv_sim_test cmd_vel_test --existing`을 실행한다. 이 모드는 현재 /cmd_vel 구독자와 /odom 발행자가 각각 four_steer_amr_plugin 하나인지 확인한 뒤 실제로 명령을 발행한다. 시험 중 다른 속도 발행기를 함께 실행하지 않는다. 성공 시 종료 코드 0, 검증 실패 시 1이다.
