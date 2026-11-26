"""RCS·PLC·주행 오류를 발생일별 TXT 파일에 모아 저장한다."""
import re
import sys
from calendar import monthrange
from collections import defaultdict
from datetime import date
from pathlib import Path
from queue import Empty, SimpleQueue
from threading import Thread
from time import monotonic


class DailyTextLogs:
    filenames = {"rcs": "rcs", "plc": "plc", "drive": "drive_fault"}
    owned_file = re.compile(r"(?:rcs|plc|drive_fault)_([0-9]{4}-[0-9]{2}-[0-9]{2})\.txt")

    def __init__(self, directory):
        self.directory = Path(directory).expanduser()
        self.queue = SimpleQueue()
        self.worker = Thread(target=self._run, name="daily-text-logs", daemon=True)
        self.worker.start()

    def append(self, key, day, line):
        self.queue.put((f"{self.filenames[key]}_{day.isoformat()}.txt", line))

    def close(self):
        # 종료 표식 앞에 들어온 마지막 PLC 응답과 오류까지 모두 기록한다.
        self.queue.put(None)
        self.worker.join()

    def _prune(self, today):
        if not self.directory.is_dir():
            return
        for path in self.directory.iterdir():
            match = self.owned_file.fullmatch(path.name)
            if not match:
                continue
            try:
                day = date.fromisoformat(match[1])
            except ValueError:
                continue
            if day >= today:
                continue
            year = day.year + (day.month == 12)
            month = day.month % 12 + 1
            # 다음 달에 같은 일자가 없으면 그 달의 말일에 만료된다.
            expires = date(year, month, min(day.day, monthrange(year, month)[1]))
            if expires <= today and path.is_file():
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    print(f"만료 로그 삭제 실패 ({path}): {exc}", file=sys.stderr)

    def _write(self, batches):
        for filename, lines in batches.items():
            path = self.directory / filename
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as stream:
                    stream.writelines(lines)
            except OSError as exc:
                print(f"로그 저장 실패 ({path}): {exc}", file=sys.stderr)

    def _run(self):
        checked_day = None
        while True:
            today = date.today()
            if today != checked_day:
                try:
                    self._prune(today)
                    checked_day = today
                except OSError as exc:
                    print(f"로그 폴더 확인 실패 ({self.directory}): {exc}", file=sys.stderr)
            try:
                item = self.queue.get(timeout=60)
            except Empty:
                # 수신이 없는 날도 날짜가 바뀌면 보관 기한을 확인한다.
                continue
            batches = defaultdict(list)
            deadline = monotonic() + 0.25
            count = 0
            while item is not None:
                filename, line = item
                batches[filename].append(line + "\n")
                count += 1
                remaining = deadline - monotonic()
                if remaining <= 0 or count >= 1024:
                    break
                try:
                    item = self.queue.get(timeout=remaining)
                except Empty:
                    break
            self._write(batches)
            if item is None:
                return
