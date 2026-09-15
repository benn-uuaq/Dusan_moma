"""각 .task 의 <cachedFileContents> 를 디스크의 .script 로 다시 채운다.

펜던트는 .task 안에 스크립트 본문 사본(캐시)을 들고 있다. .script 파일만
고치고 이걸 안 돌리면 **펜던트는 옛 본문으로 돈다.** 스크립트를 고친 뒤
반드시 실행한다:

    cd robot_task/dusan_task_v5
    python3 tools/sync_task_cache.py          # 갱신
    python3 tools/sync_task_cache.py --check  # 어긋난 곳만 보고(종료 코드 1)

두 가지 함정이 있다.
  * XML 구조가 <cachedFileContents>…</cachedFileContents><scriptFile path=…/>
    라서 파일 이름이 캐시 **뒤**에 나온다. path 에서 앞으로 찾으면(find)
    다음 노드의 캐시를 집어 전부 한 칸씩 밀린다 — 반드시 뒤로(rfind).
  * 한 태스크가 여러 폴더를 섞어 쓴다(nosensor_seq 는 goto_zero 만 다른
    폴더다). 폴더는 태스크별로 정하지 말고 노드의 path 에서 읽는다.
"""
import pathlib
import sys
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
OPEN, CLOSE = "<cachedFileContents>", "</cachedFileContents>"
PREFIX = 'path="Dusan/dusan_v5/'


def esc(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def sync(task: pathlib.Path, check_only: bool) -> tuple[int, int]:
    s = task.read_text(encoding="utf-8")
    pos = changed = checked = 0
    while True:
        marker = s.find(PREFIX, pos)
        if marker < 0:
            break
        end_q = s.index('"', marker + len('path="'))
        rel = s[marker + len(PREFIX):end_q]
        src = ROOT / rel
        idx = s.rfind(OPEN, 0, marker)
        close = s.rfind(CLOSE, 0, marker)
        if idx < 0 or close < idx or not src.exists():
            pos = end_q
            continue
        old = s[idx + len(OPEN):close]
        new = esc(src.read_text(encoding="utf-8"))
        checked += 1
        if old != new:
            changed += 1
            if check_only:
                print(f"  어긋남: {task.name} -> {rel}")
            else:
                s = s[:idx + len(OPEN)] + new + s[close:]
                end_q += len(new) - len(old)
        pos = end_q
    if not check_only and changed:
        ET.fromstring(s)                      # XML 을 깨뜨리지 않았는지
        task.write_text(s, encoding="utf-8")
    return checked, changed


def main() -> int:
    check_only = "--check" in sys.argv
    bad = 0
    for task in sorted(ROOT.glob("*.task")):
        checked, changed = sync(task, check_only)
        verb = "어긋남" if check_only else "갱신"
        print(f"{task.name}: 캐시 {checked}개 확인, {changed}개 {verb}")
        bad += changed if check_only else 0
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
