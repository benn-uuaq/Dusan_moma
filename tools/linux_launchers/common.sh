#!/usr/bin/env bash
# 바탕화면 아이콘(두 번 터치)으로 띄우는 실행 스크립트들이 같이 쓰는 부분.
# install_launchers.sh 가 이 폴더를 ~/.local/share/dusan_launchers 로 복사하고,
# dusan_ws 위치를 ws_path 에 적어 둔다. 아이콘은 `bash -i` 로 띄우므로 ~/.bashrc
# (터미널에서 쓰던 환경)도 그대로 읽힌다.

LAUNCHER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS="$(cat "$LAUNCHER_DIR/ws_path" 2>/dev/null || true)"
[ -n "$WS" ] || WS="$HOME/dusan_ws"

# ROS 2 · 워크스페이스 · venv 환경 (이미 불러왔어도 다시 해도 된다).
ros_env() {
    # shellcheck disable=SC1091
    [ -f /opt/ros/humble/setup.bash ] && source /opt/ros/humble/setup.bash
    # shellcheck disable=SC1091
    [ -f "$WS/install/setup.bash" ] && source "$WS/install/setup.bash"
    # shellcheck disable=SC1091
    [ -f "$WS/.venv/bin/activate" ] && source "$WS/.venv/bin/activate"
    return 0
}

# 프로그램이 끝나거나 바로 죽어도 창이 닫히지 않게 — 무슨 일이 있었는지 보이도록.
# 키보드가 없으니 창 오른쪽 위 X 로 닫는다.
hold_window() {
    local rc=$?
    echo
    if [ "$rc" -eq 0 ]; then
        echo "== 끝났습니다. 창을 닫으세요(오른쪽 위 X)."
    else
        echo "== 멈췄습니다(코드 $rc). 위 메시지를 확인하고 창을 닫으세요(오른쪽 위 X)."
    fi
    read -r _ || true
}

need_ws() {
    if [ ! -d "$WS" ]; then
        echo "[중단] dusan_ws 를 찾을 수 없습니다: $WS"
        return 1
    fi
}
