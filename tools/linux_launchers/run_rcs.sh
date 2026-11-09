#!/usr/bin/env bash
# RCS — cd operator-ui && authbind --deep python3 -m smr_operator_ui
# shellcheck source=common.sh
source "$(dirname "$(readlink -f "$0")")/common.sh"
trap hold_window EXIT
need_ws || exit 1
ros_env
cd "$WS/operator-ui"
echo "== RCS 실행 ($WS/operator-ui)"
# 포트 502(TPAC 외부 제공 서버)는 authbind 로만 열 수 있다 — 없으면 그냥 켜고 알린다.
if command -v authbind >/dev/null 2>&1 && [ -x /etc/authbind/byport/502 ]; then
    authbind --deep python3 -m smr_operator_ui
else
    echo "[경고] authbind 가 준비되지 않아 TPAC 서버(포트 502)를 열 수 없습니다 — docs/rcs_update_guide.md 「처음 한 번」"
    python3 -m smr_operator_ui
fi
