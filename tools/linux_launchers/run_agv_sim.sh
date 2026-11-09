#!/usr/bin/env bash
# AGV 시뮬레이터 — ros2 launch vehicle_sim vehicle_sim.launch.py
# shellcheck source=common.sh
source "$(dirname "$(readlink -f "$0")")/common.sh"
trap hold_window EXIT
need_ws || exit 1
ros_env
cd "$WS"
echo "== AGV 시뮬레이터 실행"
ros2 launch vehicle_sim vehicle_sim.launch.py
