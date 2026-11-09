#!/usr/bin/env bash
# MQTT Explorer — ~/mqtt-explorer.AppImage --no-sandbox
# shellcheck source=common.sh
source "$(dirname "$(readlink -f "$0")")/common.sh"
APP="$HOME/mqtt-explorer.AppImage"
if [ ! -f "$APP" ]; then
    trap hold_window EXIT
    echo "[중단] $APP 가 없습니다."
    exit 1
fi
[ -x "$APP" ] || chmod +x "$APP"
exec "$APP" --no-sandbox
