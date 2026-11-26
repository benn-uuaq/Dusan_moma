#!/usr/bin/env bash
# AGV 테스트 PLC — AGV 담당이 만든 테스트 PLC(127.0.0.1:8501) + 리프트 보조(리프트 높이 · 수동 아웃트리거 흉내).
# AGV 제어 노드는 RCS 가 띄운다(연결 설정: 차량 제어 「ROS 차량 노드 고정」, AGV 제어 노드 「RCS 에서 실행」,
# PLC 주소 127.0.0.1 · 포트 8501). 실제 PLC(192.168.11.10:8501)를 쓸 때는 이것을 띄우지 않는다.
# 사용법: docs/agv_test_plc_guide.md
# shellcheck source=common.sh
source "$(dirname "$(readlink -f "$0")")/common.sh"
cleanup() {
    local rc=$?
    kill $(jobs -p) 2>/dev/null
    (exit "$rc")
    hold_window
}
trap cleanup EXIT
need_ws || exit 1
ros_env
cd "$WS"
echo "== 1/2 테스트 PLC (창: Steer AGV · 테스트 PLC, 127.0.0.1:8501)"
ros2 run steer_agv_test_plc test_plc_ui &
sleep 3
echo "== 2/2 리프트 보조 — 테스트 PLC 의 리프트 높이 · 수동 아웃트리거를 흉내 낸다"
echo "   RCS 연결 설정: PLC 주소 127.0.0.1 · PLC 포트 8501 · AGV 제어 노드 「RCS 에서 실행」"
python3 tools/steer_agv_test_plc_lift.py
