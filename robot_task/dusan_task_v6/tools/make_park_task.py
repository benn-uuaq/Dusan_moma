"""받침대 태스크 두 개를 만든다 — 팔을 받침대에 올리기 / 받침대에서 홈으로 (2026-11-25).

    dusan_v6_park.task    dus6_park.script   홈 -> ① 회전 -> ② 들기 -> ③ 뻗기 -> ④ 내려놓기
    dusan_v6_unpark.task  dus6_unpark.script ④ -> ③ -> ② -> ① -> 홈 (= 로봇팔 ready)

머리(설치 · 로봇 정보)는 마킹 태스크에서 그대로 가져오고, 본문은 스크립트 노드 하나다.
한 번 돌고 끝나야 하므로 반복(isTaskAlwaysLoops)을 끈다.

    python3 tools/make_park_task.py        # 두 태스크 다시 만들기
    python3 tools/sync_task_cache.py       # 그다음 캐시 채우기
"""
import pathlib
import re
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
HEADER_FROM = "dusan_v6_mark.task"
BUILDS = (("dusan_v6_park", "dus6_park.script"), ("dusan_v6_unpark", "dus6_unpark.script"))


def build(name: str, script: str) -> str:
    src = (ROOT / HEADER_FROM).read_text(encoding="utf-8")
    head = src[:src.index(">", src.index("<MainTask")) + 1]
    head = re.sub(r'<EliTask name="[^"]*"', f'<EliTask name="{name}"', head, count=1)
    head = head.replace('isTaskAlwaysLoops="true"', 'isTaskAlwaysLoops="false"', 1)
    body = (f'\n    <FolderNode display="1 [스크립트파일] {script}" isHideSubtree="false" typeName="Folder">\n'
            f'      <ScriptNode scriptType="FILE_TYPE" typeName="Script">\n'
            f'        <cachedFileContents></cachedFileContents>\n'
            f'        <scriptFile pathRootType="TASK_PATH" path="Dusan/dusan_v6/scripts/{script}"/>\n'
            f'      </ScriptNode>\n'
            f'    </FolderNode>\n  </MainTask>\n</EliTask>\n')
    text = head + body
    ET.fromstring(text)
    return text


for name, script in BUILDS:
    (ROOT / f"{name}.task").write_text(build(name, script), encoding="utf-8")
    print(f"{name}.task  <- scripts/{script}")
