#!/usr/bin/env bash
# TPAC 엔코더 시뮬레이터 — python3 mqtt_test/tpac_encoder_sim.py --host 127.0.0.1 --port 502 --connect
# shellcheck source=common.sh
source "$(dirname "$(readlink -f "$0")")/common.sh"
trap hold_window EXIT
need_ws || exit 1
ros_env
cd "$WS"
echo "== TPAC 엔코더 시뮬레이터 실행 (127.0.0.1:502 에 연결)"
python3 mqtt_test/tpac_encoder_sim.py --host 127.0.0.1 --port 502 --connect
