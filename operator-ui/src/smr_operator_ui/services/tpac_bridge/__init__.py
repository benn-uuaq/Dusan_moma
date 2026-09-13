"""TPAC(UT 장비) 등 외부 Modbus 장치와의 중계 계층.

`\\wsl.localhost\\...\\src\\tpac_bridge` 의 독립 실행형 프로그램에서
UI 와 무관한 세 파일(robot_map.py, modbus_io.py, bridge_core.py)을 그대로
가져왔다. 로직은 손대지 않았고, 같은 폴더 import(`from modbus_io import ...`)
를 패키지 상대 import(`from .modbus_io import ...`)로만 바꿨다.

원본이 바뀌면 이 세 파일도 함께 손으로 맞춰야 한다(자동 동기화 없음).
`screens/tpac_bridge_screen.py` 가 이 패키지의 `Bridge` 파사드를 그대로 쓴다.
"""

from .bridge_core import Bridge
from .robot_map import JOINT_NAMES, OUT_FORMATS, TCP_NAMES, WORD_ORDERS

__all__ = ["Bridge", "JOINT_NAMES", "TCP_NAMES", "OUT_FORMATS", "WORD_ORDERS"]
