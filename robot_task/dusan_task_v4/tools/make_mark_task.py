"""스캔 태스크에서 마킹 태스크를 만든다.

프로브 3점(probe_c/l/r)까지는 스캔 태스크와 똑같다 — 그래야 이 구간의 호를
새로 잡는다. 그 뒤 goto_zero·ㄹ자 루프를 버리고 dus_mark_point -> dus_home
두 노드를 붙인다. 한 번 돌고 끝나야 하므로 반복(isTaskAlwaysLoops)을 끈다.

    python3 tools/make_mark_task.py        # 두 판 다시 만들기
    python3 tools/sync_task_cache.py       # 그다음 캐시 채우기
"""
import pathlib
import re
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
# (원본 태스크, 새 태스크, 마킹 스크립트 폴더)
BUILDS = (
    ("dusan_v4.task", "dusan_v4_mark", "scripts"),
    ("dusan_v4_nosensor.task", "dusan_v4_nosensor_mark", "scripts_nosensor"),
)
# 이 표시가 붙은 폴더까지 남긴다(프로브 3점이 끝나는 자리).
KEEP_THROUGH = "dus_probe_r.script"


def top_folders(s):
    body = s.index(">", s.index("<MainTask")) + 1
    end = s.index("</MainTask>")
    out, depth, start = [], 0, None
    for m in re.finditer(r"<(/?)FolderNode\b[^>]*?(/?)>", s[:end]):
        if m.start() < body:
            continue
        closing, selfclose = m.group(1), m.group(2)
        if closing:
            depth -= 1
            if depth == 0:
                out.append((start, m.end()))
        elif selfclose != "/":
            if depth == 0:
                start = m.start()
            depth += 1
    return out, end


def script_folder(display, rel):
    return (f'<FolderNode display="{display}" isHideSubtree="false" typeName="Folder">\n'
            f'      <ScriptNode scriptType="FILE_TYPE" typeName="Script">\n'
            f'        <cachedFileContents></cachedFileContents>\n'
            f'        <scriptFile pathRootType="TASK_PATH" path="Dusan/dusan_v4/{rel}"/>\n'
            f'      </ScriptNode>\n'
            f'    </FolderNode>')


for src_name, new_name, folder in BUILDS:
    s = (ROOT / src_name).read_text(encoding="utf-8")
    folders, main_end = top_folders(s)
    names = [re.search(r'display="([^"]*)"', s[a:a + 200]).group(1) for a, _ in folders]
    probe_r = next(i for i, n in enumerate(names) if KEEP_THROUGH in n)
    # probe_r 다음의 이동 노드(원점 쪽 대기 자세로 가는 move_start)까지 남긴다.
    keep = probe_r + 1 if probe_r + 1 < len(folders) and "move_start" in names[probe_r + 1] else probe_r
    cut = folders[keep][1]
    tail = ("\n    " + script_folder(f"{keep + 2} [스크립트파일] dus_mark_point.script",
                                    f"{folder}/dus_mark_point.script")
            + "\n    " + script_folder(f"{keep + 3} [스크립트파일] dus_home.script",
                                       "scripts/dus_home.script")
            + "\n  ")
    s = s[:cut] + tail + s[main_end:]
    s = re.sub(r'<EliTask name="[^"]*"', f'<EliTask name="{new_name}"', s, count=1)
    s = s.replace('isTaskAlwaysLoops="true"', 'isTaskAlwaysLoops="false"', 1)
    ET.fromstring(s)
    (ROOT / f"{new_name}.task").write_text(s, encoding="utf-8")
    kept = " | ".join(names[:keep + 1])
    print(f"{new_name}.task  <- {src_name}\n   남김: {kept}\n   붙임: dus_mark_point -> dus_home")
