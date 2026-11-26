from datetime import date, datetime

from steer_agv_controller_py import daily_text_logs
from steer_agv_controller_py.debug_feed import DebugFeed, DriveFaultLog


def test_midnight_and_restart_append_to_event_date(tmp_path, monkeypatch):
    class LogDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 11, 25)

    monkeypatch.setattr(daily_text_logs, "date", LogDate)
    params = {"debug_ui": True, "debug_log_dir": tmp_path}
    before = datetime(2026, 11, 24, 23, 59, 59)
    after = datetime(2026, 11, 25)
    feed = DebugFeed(params)
    feed.record("rcs", "요청", before)
    feed.record("rcs", "응답", after)
    _, batches = feed.take()
    feed.close()
    feed = DebugFeed(params)
    feed.record("rcs", "재시작", after)
    feed.close()

    assert (tmp_path / "rcs_2026-11-24.txt").read_text().splitlines() == [batches["rcs"][0]]
    assert (tmp_path / "rcs_2026-11-25.txt").read_text().splitlines() == [
        batches["rcs"][1], "2026-11-25 00:00:00.000  재시작"]


def test_drive_fault_without_ui(tmp_path):
    feed = DebugFeed({"debug_ui": False, "debug_log_dir": tmp_path})
    faults = DriveFaultLog(tmp_path, feed)
    faults.record({"code": 103, "reason": "주행 오류"})
    faults.close()
    feed.close()
    detail = (tmp_path / "drive_faults.jsonl").read_text().strip()
    log, = tmp_path.glob("drive_fault_*.txt")
    assert log.read_text().strip().endswith(detail)


def test_idle_start_prunes_only_expired_log_files_at_month_end(tmp_path, monkeypatch):
    class LogDate(date):
        @classmethod
        def today(cls):
            return cls(2024, 2, 29)

    monkeypatch.setattr(daily_text_logs, "date", LogDate)
    expired = {"rcs_2024-01-29.txt", "plc_2024-01-31.txt", "drive_fault_2023-12-31.txt"}
    keep = {"plc_2024-02-01.txt", "notes_2024-01-01.txt", "rcs_2024-01-00.txt",
            "drive_faults.jsonl", "drive_profile.csv"}
    for name in keep | expired:
        (tmp_path / name).touch()
    logs = daily_text_logs.DailyTextLogs(tmp_path)
    logs.close()
    assert {p.name for p in tmp_path.iterdir()} == keep
