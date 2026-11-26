"""네 바퀴의 누적거리와 조향각으로 차체 오도메트리를 추정한다."""

from collections import deque
from math import copysign, hypot, isfinite
from . import agv_math as math


class OdometryEstimator:
    """PLC의 새 고속 샘플마다 차체 자세와 속도를 갱신한다."""
    def __init__(self):
        self.pose = (0.0, 0.0, 0.0)
        self.twist = (0.0, 0.0, 0.0)
        self.previous = None
        self.generation = None
        self.stamp = None
        self.path_speed_mps = 0.0
        self._path_samples = deque()

    def _reset_path_speed(self):
        self._path_samples.clear()
        self.path_speed_mps = 0.0

    def update(self, sample):
        """새 휠 샘플을 적분하고 목표 거리 판정용 이동량을 반환한다."""
        wheels = sample.fast.wheels
        # 동일한 PLC 샘플이 제어 주기에서 여러 번 보이더라도 중복 적분하지 않는다.
        if self.generation == sample.generation and self.stamp == sample.fast_at:
            return 0.0
        if all(isfinite(w.speed_mps) for w in wheels):
            self.twist = math.body_delta([math.wheel_vector(w.speed_mps, w.angle_rad) for w in wheels])
        if sample.fast.invalid_wheel_dms:
            # 누락 구간은 적분하지 않고 다음 정상 샘플에서 기준점을 다시 잡는다.
            self.previous = None
            self._reset_path_speed()
            self.stamp, self.generation = sample.fast_at, sample.generation
            return 0.0
        previous, stamp = self.previous, self.stamp
        same = self.generation == sample.generation
        self.previous, self.stamp, self.generation = wheels, sample.fast_at, sample.generation
        if previous is None or not same:
            # 재연결 후 첫 샘플은 누적거리 기준점으로만 사용한다.
            self._reset_path_speed()
            self._path_samples.append((sample.fast_at, 0.0))
            return 0.0
        dt = sample.fast_at - stamp
        distances = [w.distance_m - p.distance_m for w, p in zip(wheels, previous)]
        if dt <= 0 or max(map(abs, distances)) > 2.0 * dt + 0.005:
            # 물리적으로 불가능한 엔코더 점프를 적분해 위치가 튀는 일을 막는다.
            self._reset_path_speed()
            raise ValueError("wheel encoder discontinuity")
        vectors = [math.wheel_vector(d, math.midpoint_angle(p.angle_rad, w.angle_rad))
                   for d, p, w in zip(distances, previous, wheels)]
        delta = math.body_delta(vectors)
        self.pose = math.integrate_pose(self.pose, delta)
        moved = copysign(hypot(*delta[:2]), delta[0])
        # 작업 진행 거리와 같은 부호·좌표계로 최근 약 100 ms의 평균 속도를 구한다.
        samples = self._path_samples
        if dt > 0.1 or not samples:
            self._reset_path_speed()
            samples.append((sample.fast_at, 0.0))
        else:
            position = samples[-1][1] + moved
            samples.append((sample.fast_at, position))
            while len(samples) > 2 and samples[1][0] <= sample.fast_at - 0.1:
                samples.popleft()
            duration = sample.fast_at - samples[0][0]
            if duration >= 0.05:
                self.path_speed_mps = (position - samples[0][1]) / duration
        return moved
