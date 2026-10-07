"""ERUT 브로커 점검 — RCS 가 쓸 연결 설정으로 실제 MQTT 접속을 해 본다.

    python3 -m smr_operator_ui.erut_check

`run_rcs.sh` 가 RCS 를 켜기 전에 부른다. 포트가 열렸는지(TCP)만 보면 계정이
틀렸거나 엉뚱한 브로커에 붙는 경우를 못 잡는다 — 그래서 RCS 와 같은 판(MQTT
3.1.1)·계정으로 CONNECT 를 보내고 브로커의 답(CONNACK)을 그대로 보여 준다.

- 접속 ID 는 RCS 와 겹치지 않게 `3s-<장치 ID>-check` 를 쓴다(같은 ID 로 붙으면
  돌고 있는 RCS 를 끊는다). 아무것도 발행하지 않고 바로 끊는다.
- 비밀번호는 찍지 않는다(있는지만 알린다).
- 종료 코드: 0 붙음 · 1 브로커가 거절 · 2 브로커까지 닿지 않음 · 3 점검 불가.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import warnings
from typing import Any

from smr_operator_ui.services.erut_client import connack_hint

#: 연결 설정 화면의 기본값(ConnectionSettingsScreen)과 같다.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 1883
DEFAULT_DEVICE = "robot1"


def load_connection() -> tuple[dict[str, Any], str]:
    """RCS 가 읽는 것과 같은 저장소에서 연결 설정을 읽는다. (값, 출처)."""
    if os.getenv("SMR_DATABASE_URL", ""):
        from smr_operator_ui.services.settings_repository import (
            PostgreSQLSettingsRepository,
        )
        return PostgreSQLSettingsRepository().load("connection"), "PostgreSQL(SMR_DATABASE_URL)"
    from smr_operator_ui.services.settings_repository_file import JsonFileSettingsRepository
    repo = JsonFileSettingsRepository()
    return repo.load("connection"), str(repo.path)


def endpoint(values: dict[str, Any]) -> dict[str, Any]:
    """ConnectionSettingsScreen.erut_endpoint() 와 같은 규칙으로 접속 값을 만든다."""
    return {
        "host": str(values.get("ERUT Broker 주소") or DEFAULT_HOST).strip() or DEFAULT_HOST,
        "port": int(values.get("ERUT 포트") or DEFAULT_PORT),
        "device_id": str(values.get("ERUT 장치 ID") or "").strip() or DEFAULT_DEVICE,
        "username": str(values.get("ERUT 계정") or "").strip(),
        "password": str(values.get("ERUT 비밀번호") or ""),
    }


def tcp_reachable(host: str, port: int, timeout: float = 2.0) -> str:
    """닿으면 빈 문자열, 아니면 사유."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return ""
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"


def mqtt_connack(ep: dict[str, Any], timeout: float = 6.0) -> tuple[int | None, str]:
    """RCS 와 같은 판·계정으로 붙어 본다. (CONNACK 코드, 브로커 판)."""
    import paho.mqtt.client as mqtt

    # RCS 와 같은 VERSION1 콜백을 쓴다 — paho 2.x 의 사용 중단 경고는 점검 출력에 섞지 않는다.
    warnings.filterwarnings("ignore", category=DeprecationWarning, module="paho")
    warnings.filterwarnings("ignore", category=DeprecationWarning, module=__name__)
    kwargs: dict[str, Any] = {"client_id": f"3s-{ep['device_id']}-check",
                              "clean_session": True, "protocol": mqtt.MQTTv311}
    if hasattr(mqtt, "CallbackAPIVersion"):
        kwargs["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION1
    client = mqtt.Client(**kwargs)
    if ep["username"]:
        client.username_pw_set(ep["username"], ep["password"] or None)
    result: dict[str, Any] = {}

    def on_connect(cli, _userdata, _flags, rc) -> None:  # noqa: ANN001
        result["rc"] = rc
        if rc == 0:
            # mosquitto 면 판을 알려 준다(ERUT 브로커인지 가늠). 막혀 있으면 없다.
            cli.subscribe("$SYS/broker/version", qos=0)

    def on_message(_cli, _userdata, msg) -> None:  # noqa: ANN001
        result["version"] = msg.payload.decode("utf-8", "replace")

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(ep["host"], ep["port"], 15)
    client.loop_start()
    try:
        end = time.time() + timeout
        while "rc" not in result and time.time() < end:
            time.sleep(0.05)
        if result.get("rc") == 0:
            end = time.time() + 1.5
            while "version" not in result and time.time() < end:
                time.sleep(0.05)
    finally:
        client.disconnect()
        client.loop_stop()
    return result.get("rc"), result.get("version", "")


def local_listener(port: int) -> str:
    """이 PC 에서 그 포트를 듣는 프로그램(ss). 모르면 빈 문자열."""
    try:
        out = subprocess.run(["ss", "-ltnpH", f"sport = :{port}"], capture_output=True,
                             text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.splitlines()[0] if out else ""


def main() -> int:
    try:
        values, source = load_connection()
    except Exception as exc:  # noqa: BLE001
        print(f"[경고] 연결 설정을 읽지 못했습니다: {exc}")
        values, source = {}, "(읽기 실패 — 기본값)"
    ep = endpoint(values)
    where = f"{ep['host']}:{ep['port']}"
    print(f"[ERUT] 설정 {source}")
    print(f"       브로커 {where} · 장치 ID {ep['device_id']} · 계정 "
          f"{ep['username'] or '(없음 — 익명)'} · 비밀번호 "
          f"{'있음' if ep['password'] else '없음'}")
    if "ERUT Broker 주소" not in values:
        print("[경고] 저장된 ERUT 브로커 주소가 없어 기본값으로 붙습니다 — RCS 연결 설정에서 "
              "「ERUT Broker 주소」·「ERUT 포트」·「ERUT 계정」·「ERUT 비밀번호」를 넣고 저장하세요.")
    local = ep["host"] in ("127.0.0.1", "localhost", "::1")
    if local:
        listener = local_listener(ep["port"])
        if listener:
            print(f"       이 PC 의 {ep['port']} 포트: {listener}")

    reason = tcp_reachable(ep["host"], ep["port"])
    if reason:
        print(f"[경고] ERUT 브로커 {where} 에 닿지 않습니다 ({reason}).")
        if local:
            print("        로컬 시험용이면 이 PC 의 mosquitto 를 켜세요: "
                  "sudo systemctl enable --now mosquitto")
        else:
            print("        네트워크(랜선·인터넷)와, 주소·포트가 ERUT 가 알려 준 값인지 확인하세요.")
        return 2

    try:
        rc, version = mqtt_connack(ep)
    except ImportError:
        print("[경고] paho-mqtt 가 없어 MQTT 접속은 못 봤습니다(포트는 열려 있음) — "
              ".venv 를 켠 뒤 다시 보세요.")
        return 3
    except Exception as exc:  # noqa: BLE001
        print(f"[경고] MQTT 접속 중 오류: {exc}")
        return 2
    if rc is None:
        print(f"[경고] {where} 가 MQTT 답(CONNACK)을 주지 않았습니다 — MQTT 브로커가 아니거나 "
              "접속 수 한도에 걸렸을 수 있습니다.")
        return 2
    if rc != 0:
        print(f"[경고] ERUT 브로커 {where} 가 접속을 거부했습니다 (rc={rc}) — {connack_hint(rc)}.")
        return 1
    tag = f" · {version}" if version else ""
    print(f"[확인] ERUT 브로커 {where} 에 붙었습니다{tag}.")
    if local:
        print("       (이 PC 의 로컬 시험용 브로커입니다 — 현장 ERUT 브로커가 아닙니다.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
