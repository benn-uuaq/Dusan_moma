#!/usr/bin/env bash
# 바탕화면 아이콘(두 번 터치로 실행)을 만든다 — 터치만 되는 리눅스 PC 용.
#
#   bash install_launchers.sh [dusan_ws 경로]      # 기본 ~/dusan_ws
#
# 이 폴더의 스크립트를 ~/.local/share/dusan_launchers 로 복사하고, 바탕화면과 프로그램
# 목록(~/.local/share/applications)에 .desktop 아이콘을 둔다. 다시 돌려도 된다(덮어쓴다).
set -euo pipefail

SRC="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
WS="$(readlink -f "${1:-$HOME/dusan_ws}")"
DEST="$HOME/.local/share/dusan_launchers"
APPS="$HOME/.local/share/applications"
DESKTOP="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
if [ -z "$DESKTOP" ] || [ "$DESKTOP" = "$HOME" ] || [ ! -d "$DESKTOP" ]; then
    for d in "$HOME/바탕화면" "$HOME/Desktop"; do
        [ -d "$d" ] && DESKTOP="$d" && break
    done
fi

mkdir -p "$DEST" "$APPS"
cp "$SRC"/common.sh "$SRC"/run_*.sh "$SRC"/update_from_usb.sh "$DEST"/
chmod +x "$DEST"/*.sh
echo "$WS" > "$DEST/ws_path"

# (파일 이름, 아이콘 이름, 터미널 창, 스크립트, 아이콘)
make_entry() {
    local file="$1" name="$2" terminal="$3" script="$4" icon="$5" comment="$6"
    local exec_line="bash -i \"$DEST/$script\""
    [ "$terminal" = "false" ] && exec_line="bash \"$DEST/$script\""
    cat > "$APPS/$file" <<DESKTOP_ENTRY
[Desktop Entry]
Type=Application
Version=1.0
Name=$name
Comment=$comment
Exec=$exec_line
Icon=$icon
Terminal=$terminal
Categories=Utility;
StartupNotify=true
DESKTOP_ENTRY
    chmod +x "$APPS/$file"
    if [ -n "$DESKTOP" ] && [ -d "$DESKTOP" ]; then
        cp "$APPS/$file" "$DESKTOP/$file"
        chmod +x "$DESKTOP/$file"
        # 우분투 바탕화면은 「실행 허용」된(trusted) 아이콘만 두 번 터치로 실행한다.
        gio set "$DESKTOP/$file" metadata::trusted true 2>/dev/null || true
    fi
    echo "   $name"
}

echo "== 아이콘 만들기 (dusan_ws: $WS)"
make_entry dusan-rcs.desktop "RCS" true run_rcs.sh \
    preferences-system "RCS 실행 — cd operator-ui && authbind --deep python3 -m smr_operator_ui"
make_entry dusan-agv-sim.desktop "AGV 시뮬레이터" true run_agv_sim.sh \
    applications-system "ros2 launch vehicle_sim vehicle_sim.launch.py"
make_entry dusan-tpac-encoder-sim.desktop "TPAC 엔코더 시뮬레이터" true run_tpac_encoder_sim.sh \
    network-transmit-receive "python3 mqtt_test/tpac_encoder_sim.py --host 127.0.0.1 --port 502 --connect"
make_entry dusan-mqtt-explorer.desktop "MQTT Explorer" false run_mqtt_explorer.sh \
    network-workgroup "~/mqtt-explorer.AppImage --no-sandbox"
make_entry dusan-update-usb.desktop "dusan_ws 업데이트(USB)" true update_from_usb.sh \
    system-software-update "USB 의 dusan_ws_update 로 최신으로 맞춘다"
update-desktop-database "$APPS" >/dev/null 2>&1 || true
if [ -n "$DESKTOP" ]; then
    echo "   → 바탕화면: $DESKTOP"
fi
echo "아이콘이 「실행 허용 안 됨」으로 보이면 아이콘을 길게 눌러 「실행 허용」을 고르세요(처음 한 번)."
