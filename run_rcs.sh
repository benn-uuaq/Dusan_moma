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
#   2026-10-07 에 뺐다. 저장된 연결 설정(「ERUT Broker 주소」·「ERUT 포트」·계정)으로
#   켜기 전에 실제로 붙어 보고, 안 되면 무엇을 볼지 알려 준 뒤 그대로 켠다(RCS 가
#   켜진 뒤에도 계속 다시 붙어 본다). 따로 볼 때: python3 -m smr_operator_ui.erut_check
# * 실행 권한이 없다고(Permission denied) 나오면: bash run_rcs.sh 로 켜거나
#   chmod +x run_rcs.sh
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
# 포트만 보지 않고 RCS 와 같은 계정·판으로 실제 MQTT 접속을 해 본다
# (operator-ui/src/smr_operator_ui/erut_check.py). 계정이 틀렸거나 엉뚱한 브로커면
# 브로커의 거절 코드(rc)와 뜻을 알려 준다. 결과와 상관없이 RCS 는 켠다.
if ! timeout 20 python3 -m smr_operator_ui.erut_check; then
    echo "        RCS 는 그대로 켭니다. 문제 해결: docs/rcs_update_guide.md 「ERUT 브로커에 안 붙을 때」"
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
