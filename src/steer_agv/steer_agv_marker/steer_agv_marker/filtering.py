"""이상치를 제외하며 최근 10개 측정에 Kalman 필터를 적용한다."""

from collections import deque
from math import pi

import numpy as np


def wrap(angle):
    """yaw를 [-pi, pi) 범위로 정규화한다."""
    return (angle + pi) % (2*pi) - pi


class SampleFilter:
    """최근 수락된 10개 프레임에 등속도 Kalman 모델을 적용한다."""

    def __init__(self, position_sigma=0.025, angle_sigma=0.035, gate=16.27):
        self.noise = np.diag(np.square([position_sigma, position_sigma, angle_sigma]))
        # x, y, yaw 3차원 측정의 제곱 Mahalanobis 거리로 이상치를 판정한다.
        self.gate = gate
        self.samples = deque(maxlen=10)
        self.seed = deque(maxlen=3)

    def reset(self):
        """마커 추적 연속성이 끊겼을 때 모든 필터 상태를 지운다."""
        self.samples.clear()
        self.seed.clear()

    def _initial(self, z):
        """첫 자세 측정으로 [x,y,yaw,vx,vy,vyaw] 상태를 초기화한다."""
        return np.r_[z, np.zeros(3)], np.diag([*self.noise.diagonal(), 1., 1., 2.])

    def _step(self, state, covariance, dt, z):
        """등속도 예측과 측정 보정을 한 번 수행하고 이상치 점수를 반환한다."""
        transition = np.eye(6)
        transition[:3, 3:] = dt*np.eye(3)
        accel = np.vstack((dt**2/2*np.eye(3), dt*np.eye(3)))
        prediction = transition @ state
        prior = transition @ covariance @ transition.T + accel @ np.diag([1., 1., 4.]) @ accel.T
        innovation = z - prediction[:3]
        innovation[2] = wrap(innovation[2])
        s = prior[:3, :3] + self.noise
        gain = np.linalg.solve(s, prior[:3]).T
        state = prediction + gain @ innovation
        state[2] = wrap(state[2])
        return state, prior - gain @ prior[:3], innovation @ np.linalg.solve(s, innovation)

    def observe(self, stamp, z):
        """측정을 선별해 추가하고 10개가 모인 경우에만 필터 자세를 반환한다."""
        history = self.samples or self.seed
        if history and stamp <= history[-1][0]:
            return None
        if history and stamp - history[-1][0] > 0.5:
            self.reset()
        if z is None:
            self.reset()
            return None
        if not np.isfinite(z).all():
            return None
        if not self.samples:
            # 서로 일치하는 두 측정으로 시작해 첫 프레임 이상치가 추적을 고정하지 못하게 한다.
            for t, other in reversed(self.seed):
                _, _, score = self._step(*self._initial(other), stamp-t, z)
                if score <= self.gate:
                    self.samples.extend(((t, other), (stamp, z)))
                    self.state, self.cov, _ = self._step(*self._initial(other), stamp-t, z)
                    self.seed.clear()
                    return None
            self.seed.append((stamp, z))
            return None
        state, cov, score = self._step(self.state, self.cov, stamp-self.samples[-1][0], z)
        if score > self.gate:
            return None
        sliding = len(self.samples) == self.samples.maxlen
        self.samples.append((stamp, z))
        if sliding:
            # 창이 이동하면 가장 오래된 프레임의 영향을 제거한 뒤 10개만 다시 계산한다.
            t, first = self.samples[0]
            state, cov = self._initial(first)
            for next_t, measurement in list(self.samples)[1:]:
                state, cov, _ = self._step(state, cov, next_t-t, measurement)
                t = next_t
        self.state, self.cov = state, cov
        return state[:3] if len(self.samples) == self.samples.maxlen else None
