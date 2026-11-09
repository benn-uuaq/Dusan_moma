#!/usr/bin/env bash
# USB 로 업데이트 — 꽂힌 USB 에서 dusan_ws_update/update_dusan_ws.sh 를 찾아 돌린다.
# (그 스크립트가 dusan_ws 를 최신으로 맞추고, 빌드하고, 이 아이콘들도 새로 깐다.)
# shellcheck source=common.sh
source "$(dirname "$(readlink -f "$0")")/common.sh"
trap hold_window EXIT
need_ws || exit 1

found=""
for script in /media/"$USER"/*/dusan_ws_update/update_dusan_ws.sh \
              /run/media/"$USER"/*/dusan_ws_update/update_dusan_ws.sh \
              /media/*/dusan_ws_update/update_dusan_ws.sh; do
    [ -f "$script" ] || continue
    # 여러 개면 번들이 가장 새것인 쪽.
    if [ -z "$found" ] || [ "$(dirname "$script")/dusan_ws.bundle" -nt "$(dirname "$found")/dusan_ws.bundle" ]; then
        found="$script"
    fi
done
if [ -z "$found" ]; then
    echo "[중단] USB 에서 dusan_ws_update 폴더를 찾지 못했습니다."
    echo "       노트북에서 만든 dusan_ws_update 폴더를 USB 맨 위에 통째로 복사해 꽂으세요."
    exit 1
fi
echo "== $found"
bash "$found" "$WS"
