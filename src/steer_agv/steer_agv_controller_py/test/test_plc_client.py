import socket
import struct
from math import degrees
from time import monotonic, sleep
from unittest.mock import MagicMock, Mock

import pytest

from steer_agv_controller_py.cpu_temperature import UNAVAILABLE, read_cpu_temperature_word

from steer_agv_controller_py.plc_client import PlcClient, PlcCodec, stopped_words


def test_fast_feedback_uses_low_word_first_and_signed_steering():
    words = [0] * 40
    words[5:7] = struct.unpack("<HH", struct.pack("<i", 1250))
    words[13:15] = struct.unpack("<HH", struct.pack("<i", -250))
    words[21:25] = [65526, 250, 0, 65535]
    words[29] = 65536 - 1234
    words[32] = 65536 - 50
    sample = PlcCodec.decode_fast_data(tuple(words))
    wheel = sample.wheels[0]
    assert wheel.distance_m == 1.25
    assert wheel.speed_mps == -0.25
    assert [degrees(w.angle_rad) for w in sample.wheels] == pytest.approx([-0.10, 2.50, 0, -0.01])
    assert degrees(sample.guide_error_rad) == pytest.approx(-12.34)
    assert sample.feedback_speed_mmps == -50
    assert not sample.auto_ready


@pytest.mark.parametrize("response", [b"E0", b"1", b"1 65536", b"000000 1"])
def test_invalid_word_response_is_rejected(response):
    with pytest.raises(ValueError):
        PlcCodec.parse_words(response, 2)


def test_command_finish_and_state_only_error():
    assert PlcCodec.build_write_request(103, 32) == b"WR DM103 32\r"
    with pytest.raises(ValueError):
        PlcCodec.build_write_request(103, 8)


def test_cpu_temperature_sensor_selection_and_missing_data(tmp_path):
    for i, (name, label, value) in enumerate([
            ("nvme", "Composite", "90000"), ("k10temp", "Tctl", "48125"),
            ("coretemp", "Package id 0", "52750")]):
        device = tmp_path / "hwmon" / f"hwmon{i}"
        device.mkdir(parents=True)
        (device / "name").write_text(name)
        (device / "temp1_label").write_text(label)
        (device / "temp1_input").write_text(value)
    assert read_cpu_temperature_word(tmp_path) == 528
    (tmp_path / "hwmon/hwmon2/temp1_input").unlink()
    assert read_cpu_temperature_word(tmp_path) == 481
    (tmp_path / "hwmon/hwmon1/temp1_input").write_text("bad")
    assert read_cpu_temperature_word(tmp_path) == UNAVAILABLE
    zone = tmp_path / "thermal/thermal_zone0"
    zone.mkdir(parents=True)
    (zone / "type").write_text("cpu-thermal")
    (zone / "temp").write_text("53000")
    assert read_cpu_temperature_word(tmp_path) == 530


def test_heartbeat_wrap_and_stale_temperature_share_one_ack():
    client = PlcClient("mock", 8501, 0.2, 1.0, writes_enabled=True)
    client._life = 65535
    client._cpu_sample = (10.0, 523)
    connection = Mock()
    connection.recv.side_effect = [b"OK\r\n", b"E1\r\n"]
    client._write_life(connection, 11.0)
    connection.sendall.assert_called_with(b"WRS DM100.U 2 0 523\r")
    assert client.register_feedback()[1] == {100: (0, "OK"), 101: (523, "OK")}
    with pytest.raises(ValueError, match="PLC rejected write"):
        client._write_life(connection, 13.1)
    connection.sendall.assert_called_with(b"WRS DM100.U 2 1 65535\r")
    assert client.register_feedback()[1] == {100: (1, "E1"), 101: (UNAVAILABLE, "E1")}


def test_running_speed_is_written_only_on_change(monkeypatch):
    # 같은 속도를 여러 주기 제출해도 정속·감속·정지 값은 각각 한 번만 쓴다.
    now = 0.0
    client = PlcClient("mock", 8501, 0.2, 1.0, fast_period=0.003, writes_enabled=True)
    writes = []
    run = {**stopped_words(), 103: 1, 106: 200, 109: 5}
    publish = client._publish

    def read(connection, address, count):
        words = [0] * count
        if address == 0:
            words[1] = 2
            words[2] = int(0.005 < now < 0.054)
        return tuple(words)

    def publish_and_submit(sample):
        publish(sample)
        words = {**run, 106: 150} if now >= 0.030 else run
        client.submit(client.take_latest_snapshot(), words if now < 0.050 else stopped_words())

    def wait(delay):
        nonlocal now
        assert delay > 0
        now += delay
        if now >= 0.080:
            client._stop.set()

    monkeypatch.setattr("steer_agv_controller_py.plc_client.monotonic", lambda: now)
    monkeypatch.setattr(client, "_read_words", read)
    monkeypatch.setattr(client, "_publish", publish_and_submit)
    monkeypatch.setattr(client, "_write", lambda conn, addr, word: writes.append((now, addr, word)))
    monkeypatch.setattr(client._stop, "wait", wait)
    client._poll(None, 1)

    speeds = [(t, w) for t, a, w in writes if a == 106]
    assert [w for t, w in speeds] == [200, 150, 0]
    stop_at, = [t for t, w in speeds if w == 0]
    assert .050 <= stop_at < .056
    assert [a for t, a, w in writes if t == stop_at][:2] == [103, 106]
    assert [w for t, a, w in writes if a == 109] == [5, 0]


def test_worker_reconnects_after_timeout_and_accepts_fragmented_response(monkeypatch):
    first = MagicMock()
    first.__enter__.return_value = first
    first.recv.side_effect = socket.timeout("PLC timed out")
    second = MagicMock()
    second.__enter__.return_value = second
    fast = b" ".join([b"0"] * 40)
    slow = b" ".join([b"2", b"0"] + [b"0"] * 48)
    second.recv.side_effect = [fast + b"\r", b"\n", slow + b"\r\n"]
    connect = Mock(side_effect=[first, second])
    monkeypatch.setattr(socket, "create_connection", connect)

    client = PlcClient("mock", 8501, 0.05, 0.005, fast_period=10.0, slow_period=10.0)
    client.start()
    try:
        deadline = monotonic() + 1.0
        while monotonic() < deadline:
            sample = client.take_latest_snapshot()
            if sample.slow is not None:
                break
            sleep(0.005)
        else:
            pytest.fail(f"no feedback received: {sample}")
        assert sample.generation == 2
        assert sample.connected
        assert sample.slow.obstacle_slow
        assert not sample.slow.obstacle_stop
        assert [call.args[0] for call in second.sendall.call_args_list] == [
            b"RDS DM0.U 40\r", b"RDS DM50.U 50\r",
        ]
    finally:
        client.close()
