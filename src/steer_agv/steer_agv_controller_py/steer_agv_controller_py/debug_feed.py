"""제어·PLC 로그를 파일에 기록하고 UI가 켜져 있으면 화면에도 전달한다."""
import csv
import json
import sys
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime
from threading import Lock

from .daily_text_logs import DailyTextLogs


class DebugFeed:
    def __init__(self, params):
        self.lock = Lock()
        self.params = dict(params)
        self.lines = self.empty_batches() if params["debug_ui"] else None
        self.files = DailyTextLogs(params["debug_log_dir"])

    @staticmethod
    def empty_batches():
        return {key: deque(maxlen=2000) for key in ("rcs", "plc", "drive")}

    def record(self, key, message, when=None):
        when = when or datetime.now()
        line = f'{when.isoformat(timespec="milliseconds", sep=" ")}  {message}'
        with self.lock:
            self.files.append(key, when.date(), line)
            if self.lines is not None:
                self.lines[key].append(line)

    def close(self):
        self.files.close()

    def set_params(self, params):
        with self.lock:
            self.params = dict(params)

    def take(self):
        with self.lock:
            batches = self.lines
            self.lines = self.empty_batches()
            return self.params, batches


class DriveFaultLog:
    """오류 시점의 데이터를 별도 파일에 추가하고 제어 주기 밖에서 디스크에 쓴다."""
    def __init__(self, directory, debug):
        self.path = Path(directory).expanduser() / "drive_faults.jsonl"
        self.profile_path = self.path.with_name("drive_profile.csv")
        self.profile_header_checked = False
        self.profile_rows = []
        self.debug = debug
        self.writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="drive-fault-log")

    def record(self, fields):
        when = datetime.now().astimezone()
        fields["time"] = when.isoformat(timespec="milliseconds")
        self.writer.submit(self._append, fields, when)

    def _append(self, fields, when):
        line = json.dumps(fields, ensure_ascii=False)
        if self.debug:
            self.debug.record("drive", line, when)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
        except OSError as exc:
            message = f"주행 오류 로그 저장 실패 ({self.path}): {exc}"
            print(message, file=sys.stderr)
            if self.debug:
                self.debug.record("drive", message)

    def profile(self, fields, flush=False):
        """제어 스레드는 샘플을 모으고 파일 입출력은 기존 기록 스레드에 맡긴다."""
        fields["time"] = datetime.now().astimezone().isoformat(timespec="milliseconds")
        self.profile_rows.append(fields)
        if flush or len(self.profile_rows) >= 50:
            self._flush_profile()

    def _flush_profile(self):
        if self.profile_rows:
            rows, self.profile_rows = self.profile_rows, []
            self.writer.submit(self._append_profile, rows)

    def _append_profile(self, rows):
        try:
            self.profile_path.parent.mkdir(parents=True, exist_ok=True)
            if not self.profile_header_checked:
                if self.profile_path.exists():
                    with self.profile_path.open(encoding="utf-8", newline="") as previous:
                        header = next(csv.reader(previous), [])
                    if header and header != list(rows[0]):
                        # 이전 형식으로 기록한 작업은 기존 열 순서 그대로 보존한다.
                        self.profile_path = self.profile_path.with_name("drive_profile_dm032.csv")
                self.profile_header_checked = True
            with self.profile_path.open("a", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=rows[0])
                if stream.tell() == 0:
                    writer.writeheader()
                writer.writerows(rows)
        except OSError as exc:
            message = f"주행 프로파일 저장 실패 ({self.profile_path}): {exc}"
            print(message, file=sys.stderr)
            if self.debug:
                self.debug.record("drive", message)

    def close(self):
        self._flush_profile()
        self.writer.shutdown(wait=True)
