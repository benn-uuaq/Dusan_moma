#!/usr/bin/env bash
# Tk 시뮬레이터를 **Windows 파이썬**으로 띄운다.
#
#   mqtt_test/win_sim.sh mqtt_job_sim     (erut_sim, alarm_sim, tpac_encoder_sim 도 같다)
#
# 왜: WSLg(WSL 2.7 / WSLg 1.0.73)에서는 X11 창이 커서를 지정하면 Windows 쪽
# 포인터가 숨겨진다 — Tk 는 X11 로만 뜨므로 시뮬레이터 위에서 마우스 포인터가
# 사라진다(Qt/Wayland 인 RCS 는 괜찮다). Windows 파이썬으로 띄우면 보통
# Windows 창이라 포인터가 보인다.
#
# 브로커: Windows 쪽 localhost:1883 은 Windows mosquitto 가 쓰고 있어서, WSL
# mosquitto 에 1884 리스너를 더했다(/etc/mosquitto/conf.d/windows_sims.conf).
# Windows localhost:1884 -> WSL 브로커(RCS 가 쓰는 것과 같은 브로커)로 간다.
# 필요: Windows 파이썬 + paho-mqtt (py -m pip install paho-mqtt)
set -euo pipefail

name="${1:?시뮬레이터 이름: mqtt_job_sim / erut_sim / alarm_sim}"
shift
here="$(cd "$(dirname "$0")" && pwd)"
script="$here/${name%.py}.py"
[ -f "$script" ] || { echo "없는 시뮬레이터: $script" >&2; exit 1; }

py="${WIN_PYTHON:-}"
if [ -z "$py" ]; then
    py="$(ls -1d /mnt/c/Users/*/AppData/Local/Programs/Python/Python3*/python.exe 2>/dev/null \
          | sort -V | tail -1 || true)"
fi
[ -n "$py" ] && [ -x "$py" ] || {
    echo "Windows 파이썬을 못 찾았습니다. WIN_PYTHON=/mnt/c/.../python.exe 로 지정하세요." >&2
    exit 1
}

export SIM_MQTT_PORT="${SIM_MQTT_PORT:-1884}"
export WSLENV="${WSLENV:+$WSLENV:}SIM_MQTT_PORT"
exec "$py" "$(wslpath -w "$script")" "$@"
