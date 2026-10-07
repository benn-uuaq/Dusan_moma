#!/usr/bin/env bash
# 현장 RCS 실행 — 환경을 불러오고, ERUT 브로커가 닿는지 보고, authbind 로 RCS 를 띄운다.
#
#   ./run_rcs.sh
#
# * authbind: TPAC 설정 / TCP 인코딩 화면이 여는 서버의 기본 포트(502)는 1024 미만이라
#   일반 사용자는 열 수 없다. python3 에 setcap 을 걸면 ROS 2 가 깨지므로(operator-ui
#   README 「실행」) authbind 로 이 프로세스에만 권한을 준다. 그냥
#   `python3 -m smr_operator_ui` 로 켜면 TPAC 시뮬레이터·장비와 안 붙는다.
# * ERUT 브로커: RCS 의 외부 통신은 ERUT 브로커(MQTT) 하나다 — 사내 MC MQTT 는
#   2026-10-07 에 뺐다. 연결 설정의 「ERUT Broker 주소」·「ERUT 포트」(기본
#   127.0.0.1:1883)에 닿는지 켜기 전에 본다. 안 닿으면 무엇을 볼지 알려 주고 그대로
#   켠다(RCS 가 켜진 뒤에도 계속 다시 붙어 본다).
# 자세한 순서·문제 해결: docs/rcs_update_guide.md

cd "$(dirname "$(readlink -f "$0")")" || exit 1

# ---- 환경 ------------------------------------------------------------------
if [ -f /opt/ros/humble/setup.bash ]; then
    # shellcheck disable=SC1091
    source /opt/ros/humble/setup.bash
else
    echo "[경고] /opt/ros/humble/setup.bash 가 없습니다 — ROS 2 Humble 설치를 확인하세요."
fi
if [ -f install/setup.bash ]; then
    # shellcheck disable=SC1091
    source install/setup.bash
else
    echo "[경고] install/setup.bash 가 없습니다 — 먼저 빌드하세요:"
    echo "        colcon build --symlink-install"
fi
if [ -f .venv/bin/activate ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

# ---- ERUT 브로커 확인 ----------------------------------------------------------
settings="${SMR_SETTINGS_FILE:-$HOME/.config/smr-operator-ui/settings.json}"
read -r erut_host erut_port < <(python3 - "$settings" <<'EOF2'
import json, sys
host, port = "127.0.0.1", 1883
try:
    conn = json.load(open(sys.argv[1], encoding="utf-8")).get("connection", {})
    host = str(conn.get("ERUT Broker 주소") or host).strip() or host
    port = int(conn.get("ERUT 포트") or port)
except Exception:
    pass
print(host, port)
EOF2
)
if timeout 2 bash -c "exec 3<>/dev/tcp/${erut_host}/${erut_port}" 2>/dev/null; then
    echo "[확인] ERUT 브로커 ${erut_host}:${erut_port} 에 닿습니다."
else
    echo "[경고] ERUT 브로커 ${erut_host}:${erut_port} 에 닿지 않습니다. RCS 는 그대로 켭니다."
    if [ "$erut_host" = "127.0.0.1" ] || [ "$erut_host" = "localhost" ]; then
        echo "        이 PC 의 mosquitto(로컬 시험용 브로커)를 확인하세요:"
        echo "          systemctl status mosquitto"
        echo "          sudo systemctl enable --now mosquitto     (꺼져 있으면)"
        echo "          sudo apt install -y mosquitto mosquitto-clients   (없으면)"
    else
        echo "        브로커(${erut_host})까지 네트워크가 닿는지(랜선·인터넷), 주소·포트가"
        echo "        ERUT 가 알려 준 값과 같은지 확인하세요."
    fi
    echo "        주소가 틀렸으면 RCS 연결 설정의 「ERUT Broker 주소」·「ERUT 포트」를 고쳐 저장하세요."
fi

# ---- RCS 실행 ---------------------------------------------------------------
if command -v authbind >/dev/null 2>&1 && [ -x /etc/authbind/byport/502 ]; then
    exec authbind --deep python3 -m smr_operator_ui "$@"
fi
echo "[경고] authbind 가 준비되지 않아 TPAC 서버(포트 502)를 열 수 없습니다 — TPAC 와 안 붙습니다."
echo "        최초 1회 설정(docs/rcs_update_guide.md 「처음 한 번」):"
echo "          sudo apt-get install -y authbind"
echo "          sudo touch /etc/authbind/byport/502"
echo "          sudo chmod 500 /etc/authbind/byport/502"
echo "          sudo chown \"\$(whoami)\" /etc/authbind/byport/502"
exec python3 -m smr_operator_ui "$@"
