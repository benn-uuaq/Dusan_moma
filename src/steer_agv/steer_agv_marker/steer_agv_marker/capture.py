"""V4L2 카메라 또는 직렬 포트의 연속 JPEG 스트림에서 영상을 읽는다."""
import os
from threading import Event, Lock, Thread
from time import monotonic

import cv2
import numpy as np


def jpeg_frames(port, stop):
    """SOI/EOI 바이트를 기준으로 직렬 스트림에서 완전한 JPEG만 추출한다."""
    buf = bytearray()
    while not stop.is_set():
        buf.extend(port.read(min(65536, max(1, port.in_waiting))))
        while True:
            start = buf.find(b"\xff\xd8")
            if start < 0:
                del buf[:-1]
                break
            del buf[:start]
            end = buf.find(b"\xff\xd9", 2)
            if end < 0:
                # 손상된 입력이 계속 쌓여 메모리를 소모하지 않도록 상한을 둔다.
                if len(buf) > 4_000_000:
                    del buf[:-1]
                break
            payload = bytes(buf[:end+2])
            del buf[:end+2]
            frame = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            if frame is not None:
                yield frame


class Camera:
    """캡처 전용 스레드에서 최신 프레임 하나만 보관하는 카메라 래퍼."""
    def __init__(self, device, baud, size, clock, logger):
        self.device, self.baud, self.size = device, baud, size
        self.clock, self.logger = clock, logger
        self.stop = Event()
        self.lock = Lock()
        self.latest = None
        self.thread = Thread(target=self.run, daemon=True, name="marker-camera")

    def frames(self):
        """장치 종류에 따라 직렬 JPEG 또는 V4L2 프레임을 생성한다."""
        if os.path.realpath(self.device).startswith(("/dev/tty", "/dev/pts/")):
            import serial
            with serial.Serial(self.device, self.baud, timeout=0.05) as port:
                yield from jpeg_frames(port, self.stop)
        else:
            cam = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
            try:
                if not cam.isOpened():
                    raise OSError(f"cannot open {self.device}")
                cam.set(cv2.CAP_PROP_FRAME_WIDTH, self.size[0])
                cam.set(cv2.CAP_PROP_FRAME_HEIGHT, self.size[1])
                cam.set(cv2.CAP_PROP_FPS, 20)
                cam.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                # 오래된 프레임을 처리하지 않도록 드라이버 버퍼도 가능한 한 줄인다.
                while not self.stop.is_set():
                    ok, frame = cam.read()
                    if not ok:
                        raise OSError("camera read failed")
                    yield frame
            finally:
                cam.release()

    def run(self):
        """프레임을 계속 갱신하고 오류 시 잠시 기다린 뒤 장치를 재연결한다."""
        while not self.stop.is_set():
            try:
                for frame in self.frames():
                    if self.stop.is_set():
                        return
                    with self.lock:
                        self.latest = (monotonic(), self.clock.now().to_msg(), frame)
            except (OSError, ImportError, cv2.error) as exc:
                with self.lock:
                    self.latest = None
                self.logger.warning(str(exc), throttle_duration_sec=5)
                self.stop.wait(1)

    def take(self):
        """최신 프레임을 한 번 꺼낸다. 같은 영상은 두 번 처리하지 않는다."""
        with self.lock:
            latest, self.latest = self.latest, None
        return latest

    def close(self):
        """캡처 스레드에 종료를 알리고 제한 시간 동안 정리를 기다린다."""
        self.stop.set()
        self.thread.join(timeout=2)
