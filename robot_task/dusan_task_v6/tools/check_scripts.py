"""Elite 스크립트의 if/while/def ↔ end 균형을 훑는다.

펜던트는 태스크를 통째로 한 스크립트로 이어 붙여 돌리므로, 한 파일에서
end 가 하나 모자라면 **전혀 다른 파일 줄 번호**로 오류가 난다. 올리기 전에
여기서 먼저 잡는다.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OPENERS = re.compile(r"^\s*(def\s+\w+|if\s*\(|elif\s*\(|else\s*:|while\s*\(|for\s+|thread\s+)")
END = re.compile(r"^\s*end\s*$")


def main() -> int:
    bad = 0
    for p in sorted(ROOT.glob("scripts*/*.script")):
        depth = 0
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.split("#")[0].rstrip()
            if not line.strip():
                continue
            if END.match(line):
                depth -= 1
                continue
            if OPENERS.match(line) and not line.lstrip().startswith(("elif", "else")):
                depth += 1
        if depth:
            print(f"블록 균형 이상: {p.relative_to(ROOT)} (남은 깊이 {depth})")
            bad += 1
    print(f"스크립트 {len(list(ROOT.glob('scripts*/*.script')))}개, 이상 {bad}개")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
