# 마커 기반 AGV 위치 측정

`marker_localizer`는 20 Hz로 새 프레임을 확인하고 지정한 ArUco 4x4_50 ID가 보일 때 AGV의 평면 위치를 `/vehicle/marker_pose`에 발행한다. 메시지는 `geometry_msgs/msg/PoseStamped`이며 x, y는 m, theta는 Z축 회전(rad)을 담은 quaternion이다. 기본 frame_id는 marker_ground로, 원점은 마커 중앙의 바닥 투영점이고 +X는 인쇄면에서 바깥쪽, +Y는 인쇄면의 오른쪽, +Z는 위쪽이다. PDF의 TOP이 위를 향하도록 수직면에 부착한다. 지도 좌표가 필요하면 노드의 frame_id와 marker_x, marker_y, marker_yaw를 마커의 실측 지도 좌표로 지정한다.

## 실행

```bash
cd ~/ros2_ws
source /opt/ros/humble/setup.bash
rosdep install --from-paths src/new_codex_projects/steer_agv/steer_agv_marker --ignore-src -y
colcon build --packages-up-to steer_agv_bringup --symlink-install
source install/setup.bash
ros2 launch steer_agv_bringup controller.launch.py marker_camera:=true calibration_file:=/absolute/path/to/camera.yaml
```

camera.yaml에는 해당 카메라·렌즈·해상도에서 측정한 ROS 보정값(image_width, image_height, camera_matrix, distortion_model, distortion_coefficients)이 필요하다. 입력 영상은 보정 전 원본으로 받는다. V4L2 USB 웹캠이면 launch 인자 `device:=/dev/video0` 또는 `/dev/v4l/by-id/...`를 지정한다. 기본 `/dev/ttyUSB0`는 카메라가 연속 JPEG 바이트를 보내는 시리얼 방식이며 기본 921600 baud, 8N1이다. 실제 전송 속도와 펌웨어 프로토콜을 확인한다. 처리 주기는 20 Hz이고 발행률은 새 영상 수와 검출 상태에 따른다.

cam_joint의 위치는 프로젝트 도면 좌표 (우측 0, 전방 0.45, 상방 0.30 m)를 base_link 기준 ROS 좌표 (0.45, 0, 0.30)로 옮겼다. 기본 광축은 전방이다. 실제 장착 각도는 `cam_mount_rpy:='0 0 0'`에 ROS 단위 rad로 지정한다. 노드는 URDF에서 발행한 base_link와 cam_optical_frame 사이의 고정 TF를 사용한다.

## 샘플과 발행

검출된 네 모서리로 optical_from_tag를 구하고, world_from_tag × inverse(optical_from_tag) × optical_from_base로 AGV 위치를 계산한다. 동일 ID가 중복되거나 재투영 오차가 큰 해는 제외한다. 일정 속도 Kalman 모델로 x, y, yaw를 추정하며 각도차는 ±π 경계에서 감싼다. 예측과의 마할라노비스 거리가 임계치를 넘으면 해당 프레임을 버린다. 최근 유효 샘플 10개가 모이면 현재 프레임부터 움직이는 창으로 발행한다. 마커를 잃으면 즉시 창을 비우고, 유효 샘플 간격이 0.5초를 넘으면 다시 수집한다. 카메라 입력이 없거나 현재 관측이 거부된 주기에는 발행하지 않는다.

## 마커 인쇄

```bash
ros2 run steer_agv_marker generate_marker --id 0 --output marker_0_140mm.pdf
```

동봉한 marker_0_140mm.pdf도 같은 생성기가 만든 A4 파일이다. 인쇄 배율을 100%(실제 크기)로 두고 검은 테두리 바깥 한 변이 140 mm인지 확인한다. ID를 바꾸면 launch의 marker_id도 맞춘다.
