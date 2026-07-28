"""SMR 운영자 UI의 명령행 진입점.

이 모듈은 ``python -m smr_operator_ui`` 실행과 IDE에서 파일 절대 경로로
직접 실행하는 방식을 모두 지원한다.
"""

from __future__ import annotations

import sys
from pathlib import Path


if __package__ in (None, ""):
    # 파일을 직접 실행하면 ``src``가 아니라 패키지 폴더가 sys.path에
    # 들어가므로, src 레이아웃 패키지를 가져올 수 있게 상위 경로를 추가한다.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smr_operator_ui.app import main

raise SystemExit(main())
