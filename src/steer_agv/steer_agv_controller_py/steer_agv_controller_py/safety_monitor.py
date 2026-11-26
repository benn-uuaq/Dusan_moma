"""PLC 피드백을 바탕으로 주행 가능 여부를 한 곳에서 판정한다."""

class SafetyMonitor:
    """현재 고장과 한 번 발생하면 RESET까지 유지되는 고장을 관리한다."""
    def __init__(self, freshness, life_timeout):
        self.freshness = freshness
        self.life_timeout = life_timeout
        self.latched = 0

    def current_fault(self, sample, now):
        """현재 스냅샷에서 즉시 확인되는 오류 코드를 반환한다."""
        # 연결 여부뿐 아니라 수신 시각과 PLC LIFE 변화도 각각 감시한다.
        if not sample or not sample.connected or not sample.fast or not sample.slow:
            return 701
        if any(t is None or now-t >= self.freshness for t in (sample.fast_at, sample.slow_at)):
            return 701
        if sample.life_changed_at is None or now - sample.life_changed_at >= self.life_timeout:
            return 701
        if sample.fast.error_code:
            return sample.fast.error_code
        # STATE=8은 PLC ERROR이며 상세 코드가 없으면 기존 대체 오류 901을 사용한다.
        if sample.fast.state not in (0, 1, 2, 4, 16, 32):
            return 901
        return sample.fast.measurement_fault

    def check_motion(self, sample, now):
        """자동/수동 동작을 시작해도 되는지 검사하고 아니면 거부한다."""
        code = self.current_fault(sample, now) or self.latched
        if code:
            raise ValueError(f"vehicle fault {code}")
        if sample.fast.estop or sample.slow.obstacle_stop or sample.fast.state == 16:
            raise ValueError("motion blocked by E-STOP, obstacle or initialization")
