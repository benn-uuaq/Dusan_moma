#!/usr/bin/env python3
"""워크스페이스를 팀원 인계용 zip으로 재패키징한다.

- `.git/`을 포함하여 커밋 히스토리를 함께 넘긴다.
- 빌드 산출물, 가상환경, 이전 인계 아카이브는 제외한다.
- 파일명은 UTF-8 플래그를 세워 기록한다. 인계받은 원본 zip이 CP437 mojibake로
  저장돼 있어 한글 파일명이 깨졌던 문제를 되돌려주지 않기 위함이다.
- 산출물은 워크스페이스 밖에 생성하여 자기 자신을 포함하지 않게 한다.

사용법:
    python3 tools/package_for_handoff.py [출력경로]
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import zipfile

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EXCLUDE_DIRS = {".venv", "build", "install", "log", "__pycache__", ".pytest_cache", "dist"}
EXCLUDE_SUFFIX = (".pyc", ".pyo", ".exe", ".spec")
EXCLUDE_NAMES = {"erounsolution_project.zip"}


def should_skip_file(name: str) -> bool:
    if name in EXCLUDE_NAMES:
        return True
    if ":Zone.Identifier" in name:
        return True
    return name.endswith(EXCLUDE_SUFFIX)


def main() -> int:
    if len(sys.argv) > 1:
        out = os.path.abspath(sys.argv[1])
    else:
        stamp = dt.date.today().strftime("%Y%m%d")
        out = os.path.join(os.path.dirname(WS), f"dusan_ws_handoff_{stamp}.zip")

    out_real = os.path.realpath(out)
    if out_real.startswith(os.path.realpath(WS) + os.sep):
        print("[중단] 출력 경로가 워크스페이스 안입니다. 밖으로 지정하세요.", file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(out_real), exist_ok=True)
    count = 0
    with zipfile.ZipFile(out_real, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(WS):
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIRS)

            # 빈 디렉터리도 항목으로 기록한다. gc 이후 `.git/refs/heads`처럼
            # 비어 있는 디렉터리가 빠지면 푼 저장소를 git이 인식하지 못한다.
            if not dirs and not files and root != WS:
                rel = os.path.relpath(root, WS) + "/"
                info = zipfile.ZipInfo(rel)
                info.external_attr = (0o40755 << 16) | 0x10
                info.flag_bits |= 0x800
                z.writestr(info, b"")

            for fname in sorted(files):
                if should_skip_file(fname):
                    continue
                full = os.path.join(root, fname)
                if os.path.islink(full) or not os.path.isfile(full):
                    continue
                rel = os.path.relpath(full, WS)
                info = zipfile.ZipInfo.from_file(full, rel)
                info.flag_bits |= 0x800  # 파일명 UTF-8 명시
                info.compress_type = zipfile.ZIP_DEFLATED
                with open(full, "rb") as fh:
                    z.writestr(info, fh.read())
                count += 1

    size_mb = os.path.getsize(out_real) / 1024 / 1024
    print(f"{out_real}\n파일 {count}개, {size_mb:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
