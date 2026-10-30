"""SMR RCS 단독판(Windows) 실행 파일 진입점 — PyInstaller 가 이 파일로 묶는다.

ERUT · 차량(AGV) · PLC 없이 로봇 + TPAC. 자세한 것은 smr_operator_ui/standalone/launcher.py.
"""

from smr_operator_ui.standalone.launcher import main

raise SystemExit(main())
