from dataclasses import replace
from math import atan2, hypot
from types import SimpleNamespace

import pytest

from steer_agv_controller_py import agv_math as calc
from steer_agv_controller_py.job_manager import JobManager
from steer_agv_controller_py.motion_controller import MotionController
from steer_agv_controller_py.odometry_estimator import OdometryEstimator
from steer_agv_controller_py.plc_client import PlcCodec, PlcSnapshot, WheelSample
from steer_agv_controller_py.safety_monitor import SafetyMonitor
from steer_agv_controller_py.status_publisher import plc_status


def snapshot(stamp=1.0):
    fast = PlcCodec.decode_fast_data(tuple([0]*40))
    slow = PlcCodec.decode_slow_data(tuple([0]*50))
    return PlcSnapshot(1, True, fast, slow, stamp, stamp, stamp)


@pytest.mark.parametrize("turn_sign, word", [(1, 2), (-1, 65534)])
def test_dm109_encodes_one_scan_in_hundredths_of_a_degree(turn_sign, word):
    motion = MotionController(dict(distance_tolerance=0.005, body_offset=0.6, turn_sign=turn_sign))
    job = SimpleNamespace(radius=1.25, standoff=1.0, set_dist=1.0, set_speed=0.1)
    odom = SimpleNamespace(twist=(0.1, 0, 0), pose=(0, 0, 0))
    words = motion.drive(job, 0, odom, 0.01, 0, slow=False, running=False)
    assert words[109] == word


def test_four_wheel_rotation_duplicate_and_encoder_reset():
    estimator = OdometryEstimator()
    sample = snapshot()
    rotation = 0.01
    vectors = [(-rotation*y, rotation*x) for x, y in calc.WHEEL_POSITIONS]
    wheels = tuple(WheelSample(0.0, 0.0, atan2(y, x)) for x, y in vectors)
    start = replace(sample, fast=replace(sample.fast, wheels=wheels))
    assert estimator.update(start) == 0
    end = replace(start, fast_at=1.01, fast=replace(start.fast, wheels=tuple(
        replace(w, distance_m=hypot(*v)) for w, v in zip(wheels, vectors))))
    estimator.update(end)
    assert estimator.pose == pytest.approx((0, 0, rotation))
    assert estimator.update(end) == 0
    jump = replace(start, fast_at=1.02, fast=replace(start.fast, wheels=tuple(
        replace(w, distance_m=1.0) for w in wheels)))
    with pytest.raises(ValueError):
        estimator.update(jump)
    assert estimator.pose == pytest.approx((0, 0, rotation))


def test_life_expires_at_three_seconds_even_with_fresh_reads():
    monitor = SafetyMonitor(0.5, 3.0)
    sample = replace(snapshot(4.0), life_changed_at=1.0)
    assert monitor.current_fault(sample, 4.0) == 701
    assert monitor.current_fault(replace(sample, life_changed_at=4.0), 4.0) == 0


def test_all_ones_is_minus_one_for_dint_feedback():
    words = [0] * 40
    words[0] = 0xffff
    for address in (5, 13, 26):
        words[address:address + 2] = [0xffff, 0xffff]
    fast = PlcCodec.decode_fast_data(tuple(words))
    assert PlcCodec.decode_dint(0xffff, 0xffff) == -1.0
    assert fast.wheels[0].distance_m == fast.wheels[0].speed_mps == fast.lift_height_m == -0.001
    assert fast.life == 65535
    assert fast.measurement_fault == 0


@pytest.mark.parametrize("address,field,code", [(5, "distance_m", 102), (13, "speed_mps", 102), (26, "lift_height_m", 203)])
def test_nonfinite_measurement_preserves_connection_and_watchdog(address, field, code):
    sample = snapshot()
    fast = replace(sample.fast, life=123)
    if field == "lift_height_m":
        fast = replace(fast, lift_height_m=float("nan"))
    else:
        wheel = replace(fast.wheels[0], **{field: float("nan")})
        fast = replace(fast, wheels=(wheel, *fast.wheels[1:]))
    sample = replace(sample, fast=fast)
    monitor = SafetyMonitor(0.5, 3.0)
    assert monitor.current_fault(sample, 1.1) == code
    assert monitor.current_fault(sample, 1.6) == 701
    report = plc_status(sample, 1.1, 0.5, 3.0)
    assert f"DM{address:03d}~DM{address + 1:03d}" in report.message
    assert dict((v.key, v.value) for v in report.values)["life"] == "123"


def test_invalid_encoder_gap_restarts_odometry_baseline():
    odom = OdometryEstimator()
    odom.update(snapshot())
    sample = snapshot(1.01)
    wheel = replace(sample.fast.wheels[0], speed_mps=float("nan"))
    fast = replace(sample.fast, wheels=(wheel, *sample.fast.wheels[1:]))
    assert odom.update(replace(sample, fast=fast)) == 0
    recovered = snapshot(1.02)
    wheels = tuple(replace(w, distance_m=5.0) for w in recovered.fast.wheels)
    assert odom.update(replace(recovered, fast=replace(recovered.fast, wheels=wheels))) == 0
    assert odom.pose == (0.0, 0.0, 0.0)


def test_register_at_hold_keeps_clamp_and_does_not_start():
    sample = snapshot()
    held = replace(sample, fast=replace(sample.fast, mode=2, state=2, outrig=True))
    jobs = JobManager({})
    jobs.register(SimpleNamespace(job_id="next", set_dist=0.1, set_speed=0.1,
                                  standoff=1.0, radius=1.25), held)
    outputs = jobs.outputs(held, OdometryEstimator(), 1.0, 0.01)
    assert (outputs[103], outputs[106]) == (2, 0)
    with pytest.raises(ValueError):
        jobs.run(held, 0.0, 1.0)


def test_acceleration_ramps_to_limit_in_one_second():
    speed = acceleration = 0.0
    for _ in range(100):
        speed, acceleration = calc.profile_step(speed, acceleration, 0.5, 10.0, 0.1, 0.1, 0.01)
    assert acceleration == pytest.approx(0.1)
    assert speed == pytest.approx(0.0505)


def test_path_speed_uses_fresh_encoder_samples_and_clears_on_reconnect():
    odom = OdometryEstimator()
    for i in range(21):
        sample = snapshot(1.0 + i * 0.01)
        wheels = (WheelSample(round(-0.271 * i * 0.01, 3), 0., 0.),) * 4
        sample = replace(sample, fast=replace(sample.fast, wheels=wheels))
        odom.update(sample)
        assert odom.update(sample) == 0
    assert odom.path_speed_mps == pytest.approx(-0.271, abs=0.01)
    odom.update(replace(sample, generation=2))
    assert odom.path_speed_mps == 0


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("job_speed", [0.2, 0.5])
def test_encoder_based_approach_reaches_hold(direction, job_speed):
    # 지령/엔코더 비율과 100 ms 응답 지연을 반영해 마지막 10 cm 소요 시간도 검증한다.
    motion = MotionController(dict(distance_tolerance=0.005, stop_speed_tolerance=0.005,
                                   arrival_hold_time=0.2, jerk_ratio=1., body_offset=0.6, turn_sign=1))
    odom = OdometryEstimator()
    job = SimpleNamespace(set_dist=direction * 3., set_speed=job_speed, radius=1.25, standoff=0.)
    position = speed = progress = command = 0.
    approach_at = None
    dt = 0.01
    for i in range(3000):
        speed += (1.355 * command - speed) * dt / 0.1
        position += speed * dt
        sample = snapshot(i * dt)
        wheel = WheelSample(round(position, 3), speed, 0.)
        progress += odom.update(replace(sample, fast=replace(sample.fast, wheels=(wheel,) * 4)))
        if approach_at is None and abs(job.set_dist - progress) <= 0.1000001:
            approach_at = i * dt
        words = motion.drive(job, progress, odom, dt, i * dt, slow=False, running=True)
        command = PlcCodec.decode_int16(words[106]) / 1000
        assert 0 <= direction * command <= job.set_speed
        if words[103] == 2:
            assert command == 0
            assert abs(progress - job.set_dist) <= motion.config["distance_tolerance"]
            assert approach_at is not None and i * dt - approach_at < 4.0
            return
    pytest.fail("엔코더 기반 감속이 HOLD에 도달하지 않음")


@pytest.mark.parametrize("direction", [-1, 1])
def test_pause_brakes_before_command_and_preserves_job(direction):
    config = dict(distance_tolerance=0.005, stop_speed_tolerance=0.005, deceleration=0.1,
                  jerk_ratio=1., body_offset=0.6, turn_sign=1, drive_progress_timeout=5.)
    jobs = JobManager(config)
    s = snapshot()
    running = replace(s, fast=replace(s.fast, state=1, mode=2))
    stopped = replace(s, fast=replace(s.fast, state=0, mode=2))
    job = SimpleNamespace(job_id="pause", set_speed=0.2, set_dist=direction * 3., standoff=1., radius=1.25)
    jobs.register(job, stopped)
    jobs.run(stopped, 0., 0.)
    jobs.motion.speed = 0.2
    jobs.pause()
    odom = OdometryEstimator()
    for tick in range(1, 450):
        before = jobs.motion.accel
        speed = jobs.motion.speed
        odom.twist = (direction * speed, 0., 0.)
        jobs.progress += odom.twist[0] * 0.01
        words = jobs.outputs(running, odom, tick * 0.01, 0.01)
        if words[103] == 4:
            break
        assert words[103] == 1 and direction * PlcCodec.decode_int16(words[106]) >= 0
        assert abs(jobs.motion.accel - before) <= 0.00100001
        assert speed - 0.00100001 <= jobs.motion.speed <= speed
    else:
        pytest.fail("PAUSE was never sent")
    assert words[106] == 0 and abs(odom.twist[0]) <= config["stop_speed_tolerance"]
    assert jobs.job is job and not jobs.completed and direction * jobs.progress > 0
    with pytest.raises(ValueError, match="PAUSE feedback"):
        jobs.run(stopped, 0., 5.)
    jobs.run(replace(s, fast=replace(s.fast, state=4, mode=2)), 0., 5.)
    assert jobs.command == 1


def test_lift_requires_hold_throughout_motion():
    jobs = JobManager(dict(max_lift_speed=0.2, max_lift_height=1.5, lift_tolerance=0.003))
    s = snapshot()
    stopped = replace(s, fast=replace(s.fast, mode=2, outrig=True))
    with pytest.raises(ValueError, match="HOLD"):
        jobs.set_lift("lift", 0.1, 0.1, stopped)
    held = replace(s, fast=replace(stopped.fast, state=2))
    jobs.set_lift("lift", 0.1, 0.1, held)
    assert jobs.outputs(held, None, 0., 0.01)[108] == 100
    words = jobs.outputs(stopped, None, 0., 0.01)
    jobs.apply_lift_stop(words, stopped)
    assert jobs.lift is None and words[108] == 0
