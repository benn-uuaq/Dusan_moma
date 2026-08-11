"""기준 위치(홈 관절값, 시작 포즈)를 로봇 쪽 설정 파일에 저장한다.

로봇 태스크는 Modbus 레지스터로 값을 받지만, 레지스터는 전원을 내리면
남지 않는다. 그래서 같은 값을 파일에도 남겨 다음에 다시 올릴 수 있게 한다.

저장 위치는 `SMR_ROBOT_CONFIG_DIR` 환경 변수로 바꿀 수 있다. 지정하지
않으면 워크스페이스의 `robot_task/config`를 쓴다.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

FILENAME = "reference_poses.json"
ENV_VAR = "SMR_ROBOT_CONFIG_DIR"


def config_dir() -> str:
    """기준 위치 파일을 둘 폴더 경로를 돌려준다."""
    override = os.environ.get(ENV_VAR)
    if override:
        return override
    # operator-ui/src/smr_operator_ui/services -> 워크스페이스 루트
    here = os.path.abspath(__file__)
    workspace = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(here)))))
    return os.path.join(workspace, "robot_task", "config")


def config_path() -> str:
    return os.path.join(config_dir(), FILENAME)


def load_reference_poses() -> dict:
    """저장된 기준 위치를 읽는다. 파일이 없으면 빈 값을 돌려준다."""
    try:
        with open(config_path(), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_reference_poses(values: dict) -> str | None:
    """전달한 항목만 갱신해 저장한다. 저장한 경로를 돌려준다.

    쓰기에 실패해도 화면 동작을 막지 않도록 None을 돌려준다. 로봇에는
    이미 레지스터로 값이 전달되므로 파일은 다음 기동을 위한 기록이다.
    """
    data = load_reference_poses()
    stamp = datetime.now().isoformat(timespec="seconds")
    for name, pose in values.items():
        data[name] = {"values": [float(v) for v in pose], "saved_at": stamp}

    path = config_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except OSError:
        return None
    return path
