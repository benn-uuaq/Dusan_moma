#!/usr/bin/env bash
# ERUT 시뮬레이터 — ERUT 쪽 MQTT 브릿지 대역(모재 · 격자 값 입력, 명령 버튼, 자동 진행).
# shellcheck source=common.sh
source "$(dirname "$(readlink -f "$0")")/common.sh"
trap hold_window EXIT
need_ws || exit 1
cd "$WS"
echo "== ERUT 시뮬레이터 실행 (브로커 주소 · 장치 ID 는 RCS 연결 설정과 같게)"
python3 mqtt_test/erut_sim.py
