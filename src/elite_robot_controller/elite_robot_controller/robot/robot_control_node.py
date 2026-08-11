import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, String, Int32, Float32MultiArray
from std_srvs.srv import Trigger

from elite_robot_controller import register_map
# 방금 만든 robot.py 내부 모듈 정상 참조 조치
from elite_robot_controller.robot.robot_driver import Robot_30001, Robot_29999, Robot_modbus, AlarmManager

class RobotControlNode(Node):
    # 레지스터 주소는 config/modbus_registers.json에서만 관리한다.
    # 주소가 아직 없는 항목은 요청을 거부해 엉뚱한 레지스터에 쓰지 않는다.

    def __init__(self):
        super().__init__('robot_control_node')

        self.declare_parameter('robot_ip', '192.168.227.134')
        self.declare_parameter('register_map', '')
        # 조그 속도는 레지스터 307(속도 비율 %)을 그대로 쓴다. 단위가 달라서
        # 아래 값을 100 % 기준으로 두고 비율만큼 줄인다.
        #   speedj  qd [rad/s], a [rad/s^2]   (스크립트 매뉴얼 3.1.26)
        #   speedl  xd [m/s],   a [m/s^2]     (스크립트 매뉴얼 3.1.27)
        self.declare_parameter('jog_joint_speed_max', 0.50)    # rad/s
        self.declare_parameter('jog_tcp_speed_max', 0.10)      # m/s
        self.declare_parameter('jog_tcp_rot_speed_max', 0.50)  # rad/s
        self.declare_parameter('jog_accel_max', 1.00)
        # speedj/speedl 의 t. 이 시간이 지나면 로봇이 스스로 멈춘다.
        # UI 가 누르는 동안 명령을 되풀이하므로, 통신이 끊기면 여기서 선다.
        self.declare_parameter('jog_hold_time', 0.5)
        # 홈 이동 시 movej 전에 movel로 올릴 높이 [m]
        self.declare_parameter('home_lift_z', 0.32)
        robot_ip = self.get_parameter('robot_ip').get_parameter_value().string_value
        map_path = self.get_parameter('register_map').get_parameter_value().string_value
        self.registers = register_map.load(map_path or None)

        self.robot_ip = robot_ip
        self.robot_dash = Robot_29999(robot_ip, 29999)
        self.robot_primary = Robot_30001(robot_ip, 30001)
        self.robot_modbus = Robot_modbus(robot_ip, 502)
        self.alarm_mgr = AlarmManager()
        self.connected = False
        # 로봇 자체 속도 비율 [%]. 조그 속도도 이 값을 따른다.
        self.speed_ratio = 100

        # 발행할 토픽 정의
        self.pub_robot_mode = self.create_publisher(Int32, 'robot/status/robot_mode', 10)
        self.pub_control_method = self.create_publisher(Int32, 'robot/status/control_method', 10)
        self.pub_op_mode = self.create_publisher(Int32, 'robot/status/operation_mode', 10)
        self.pub_tcp_pose = self.create_publisher(Float32MultiArray, 'robot/status/tcp_pose', 10)
        self.pub_tcp_pose_zero = self.create_publisher(Float32MultiArray, 'robot/status/tcp_pose_zero', 10)
        self.pub_joint_position = self.create_publisher(Float32MultiArray, 'robot/status/joint_position', 10)
        self.pub_alarm = self.create_publisher(String, 'robot/status/alarms', 10)
        self.pub_connected = self.create_publisher(Bool, 'robot/status/connected', 10)

        # 연결과 해제도 서비스로 노출해 운영 UI에서 다룰 수 있게 한다.
        self.create_service(Trigger, 'robot/dashboard/connect', self.cb_connect)
        self.create_service(Trigger, 'robot/dashboard/disconnect', self.cb_disconnect)

        # 대시보드 명령 서비스 매핑
        self.create_service(Trigger, 'robot/dashboard/robot_mode', self.cb_dash_mode)
        self.create_service(Trigger, 'robot/dashboard/status', self.cb_dash_status)
        self.create_service(Trigger, 'robot/dashboard/power_on', self.cb_dash_power_on)
        self.create_service(Trigger, 'robot/dashboard/power_off', self.cb_dash_power_off)
        self.create_service(Trigger, 'robot/dashboard/brake_release', self.cb_dash_brake)
        self.create_service(Trigger, 'robot/dashboard/play', self.cb_dash_play)
        self.create_service(Trigger, 'robot/dashboard/pause', self.cb_dash_pause)
        self.create_service(Trigger, 'robot/dashboard/stop', self.cb_dash_stop)

        # 홈 이동은 값이 없는 한 번짜리 명령이므로 Trigger를 쓴다. 기준 위치
        # 저장은 UI가 값을 보내므로 아래 토픽으로 받는다.
        self.create_service(Trigger, 'robot/command/move_home', self.cb_move_home)

        # 값이 있는 명령은 토픽으로 받는다.
        # 작업 속도는 mm/s, 속도 비율은 2~100 [%]이며 레지스터에 쓴다.
        self.create_subscription(Int32, 'robot/command/linear_speed', self.cb_linear_speed, 10)
        self.create_subscription(Int32, 'robot/command/speed_ratio', self.cb_speed_ratio, 10)
        # 조그는 레지스터가 아니라 30001 스크립트로 처리한다.
        # 값의 부호가 방향, 절댓값이 축 번호(1~6)이며 0은 정지다.
        self.create_subscription(Int32, 'robot/command/jog_joint', self.cb_jog_joint, 10)
        self.create_subscription(Int32, 'robot/command/jog_tcp', self.cb_jog_tcp, 10)

        # 기준 위치는 6개 레지스터에 한 번에 쓴다.
        self.create_subscription(Float32MultiArray, 'robot/command/home_joint', self.cb_home_joint, 10)
        self.create_subscription(Float32MultiArray, 'robot/command/start_pose', self.cb_start_pose, 10)

        # 10Hz 주기로 모드버스 데이터 갱신 및 30001 알람 수집
        self.timer = self.create_timer(0.1, self.update_robot_loop)
        self.get_logger().info("[DEBUG]] ELITE Robot 제어 ROS2 노드가 활성화되었습니다.")

        # 기동 시 한 번 붙어 본다. 실패해도 노드는 살아 있어야 운영 UI에서
        # 연결 버튼을 쓸 수 있다.
        if not self.connect_all_servers(robot_ip):
            self.get_logger().warn(
                "[WARN] 로봇에 연결하지 못했습니다. robot/dashboard/connect 로 다시 시도하십시오."
            )

    def connect_all_servers(self, robot_ip=None):
        """세 채널을 모두 연결한다. 하나라도 실패하면 연결로 보지 않는다."""
        try:
            d_ok = self.robot_dash.connect_29999()
            p_ok = self.robot_primary.connect_30001()
            m_ok = self.robot_modbus.connect()
            self.connected = bool(d_ok and p_ok and m_ok)
        except Exception as e:
            self.get_logger().error(f"[ERROR] 소켓 연결 중 예외 발생: {e}")
            self.connected = False
        self.publish_connected()
        return self.connected

    def disconnect_all_servers(self):
        """세 채널을 정리한다. 개별 실패는 남은 채널 정리를 막지 않는다."""
        for close in (self.robot_dash.disconnect_29999,
                      self.robot_primary.disconnect_30001,
                      self.robot_modbus.disconnect):
            try:
                close()
            except Exception as e:
                self.get_logger().warn(f"[WARN] 연결 해제 중 예외: {e}")
        self.connected = False
        self.publish_connected()
        return True

    def publish_connected(self):
        """세 채널 연결 여부를 알린다. 운영 UI가 이 값으로 상태를 표시한다."""
        self.pub_connected.publish(Bool(data=self.connected))

    def cb_connect(self, req, res):
        ok = self.connect_all_servers()
        res.success = ok
        res.message = (
            f"[connect] {self.robot_ip} 연결됨" if ok
            else f"[connect] {self.robot_ip} 연결 실패"
        )
        if not ok:
            self.get_logger().warn(res.message)
        return res

    def cb_disconnect(self, req, res):
        self.disconnect_all_servers()
        res.success = True
        res.message = "[disconnect] 연결을 해제했습니다."
        return res

    def update_robot_loop(self):
        # 연결 전에 소켓을 건드리면 예외가 난다. 상태만 알리고 넘어간다.
        if not self.connected:
            self.publish_connected()
            return

        self.publish_code('robot_mode', self.pub_robot_mode)
        self.publish_code('control_method', self.pub_control_method)
        self.publish_code('operation_mode', self.pub_op_mode)
        self.publish_pose('tcp_absolute', self.pub_tcp_pose)
        self.publish_pose('tcp_zero_relative', self.pub_tcp_pose_zero)
        self.publish_pose('joint_position', self.pub_joint_position)

        # 30001 포트 비동기 백그라운드 실시간 알람 스트림 처리
        self.robot_primary.get_data()
        while not self.robot_primary.alarm_queue.empty():
            alarm = self.robot_primary.alarm_queue.get()
            if self.alarm_mgr.process(alarm):
                alarm_msg = String()
                alarm_msg.data = f"[ALARM] {alarm.msg}" if alarm.msg else f"[ALARM CODE] E{alarm.code} S{alarm.sub}"
                self.pub_alarm.publish(alarm_msg)

    def publish_code(self, name, publisher):
        """레지스터 한 개를 읽어 그대로 발행한다."""
        entry = self.registers.read_entry(name)
        if not entry.available:
            return
        value = self.robot_modbus.get_register(entry.address)
        if value is not None:
            publisher.publish(Int32(data=value))

    def publish_pose(self, name, publisher):
        """자세 레지스터 6개를 읽어 [X, Y, Z, Rx, Ry, Rz]로 발행한다.

        get_all_registers가 부호 있는 16비트로 변환해 주므로 여기서는
        단위 환산만 한다. X, Y, Z는 mm, Rx, Ry, Rz는 mrad이다.
        """
        entry = self.registers.read_entry(name)
        if not entry.available:
            return

        regs = self.robot_modbus.get_all_registers(entry.address, entry.count)
        if not regs or len(regs) != entry.count:
            return

        scales = self.registers.scales_for(entry)
        pose_msg = Float32MultiArray()
        pose_msg.data = [value * scale for value, scale in zip(regs, scales)]
        publisher.publish(pose_msg)

    def write_register(self, name, value):
        """쓰기 레지스터에 값을 넣는다. (성공여부, 안내문구)를 돌려준다.

        주소가 정해지지 않은 항목은 시도하지 않는다. 엉뚱한 레지스터에
        쓰면 로봇이 예기치 않게 움직일 수 있다.
        """
        entry = self.registers.write_entry(name)
        if not entry.available:
            return False, f"[{name}] Modbus 주소가 설정되지 않았습니다."
        try:
            ok = self.robot_modbus.set_register(entry.address, value)
        except Exception as exc:
            return False, f"[{name}] 레지스터 쓰기 실패: {exc}"
        if not ok:
            return False, f"[{name}] 레지스터 {entry.address} 쓰기를 확인하지 못했습니다."
        return True, f"[{name}] 레지스터 {entry.address} <- {value}"

    def _write_service(self, name, value, response):
        """Trigger 서비스 응답에 쓰기 결과를 채운다."""
        success, message = self.write_register(name, value)
        if not success:
            self.get_logger().warn(message)
        response.success = success
        response.message = message
        return response

    def _write_topic(self, name, msg):
        """토픽으로 받은 값을 레지스터에 쓰고 실패만 기록한다."""
        success, message = self.write_register(name, int(msg.data))
        if not success:
            self.get_logger().warn(message)

    def cb_linear_speed(self, msg):
        self._write_topic('linear_speed', msg)

    def cb_speed_ratio(self, msg):
        # 태스크는 레지스터로, 조그는 이 값을 그대로 써서 속도를 줄인다.
        self.speed_ratio = max(2, min(100, int(msg.data)))
        self._write_topic('speed_ratio', Int32(data=self.speed_ratio))

    def _jog_scale(self):
        """속도 비율(2~100 %)을 배율로 바꾼다."""
        return self.speed_ratio / 100.0

    @staticmethod
    def _round6(values):
        """부동소수 잡음을 없앤다. 스크립트 문자열로 나가기 때문이다."""
        return [round(float(v), 6) for v in values]

    def write_pose(self, name, values):
        """자세 6개를 연속 레지스터에 쓴다. 단위 환산은 읽기와 반대로 한다."""
        entry = self.registers.write_entry(name)
        if not entry.available:
            return False, f"[{name}] Modbus 주소가 설정되지 않았습니다."
        if len(values) != entry.count:
            return False, f"[{name}] 값이 {entry.count}개가 아닙니다: {len(values)}개"

        scales = self.registers.scales_for(entry)
        for offset, (value, scale) in enumerate(zip(values, scales)):
            raw = int(round(float(value) / scale))
            ok = self.robot_modbus.set_register(entry.address + offset, raw)
            if not ok:
                return False, f"[{name}] 레지스터 {entry.address + offset} 쓰기 실패"
        return True, f"[{name}] 레지스터 {entry.address}~{entry.address + entry.count - 1} 갱신"

    def _write_pose_topic(self, name, msg):
        success, message = self.write_pose(name, list(msg.data))
        if success:
            # 태스크가 레지스터 값을 쓰도록 알린다.
            self.robot_modbus.set_register(
                self.registers.write_entry('pose_src').address or 308, 1
            )
        else:
            self.get_logger().warn(message)

    def cb_home_joint(self, msg):
        self._write_pose_topic('home_joint', msg)

    def cb_start_pose(self, msg):
        self._write_pose_topic('start_pose', msg)

    # ---------------------------------------------------------------- 조그
    def _jog_vector(self, code, count=6):
        """부호는 방향, 절댓값은 축 번호(1~6)인 코드를 속도 벡터로 바꾼다."""
        axis = abs(int(code)) - 1
        if axis < 0 or axis >= count:
            return None
        vector = [0.0] * count
        vector[axis] = 1.0 if code > 0 else -1.0
        return vector

    def _jog_stop(self):
        """29999 stop으로 즉시 멈춘다. 조그는 눌린 동안만 움직여야 한다."""
        self.robot_dash.robot_stop()

    def cb_jog_joint(self, msg):
        if not self.connected:
            return
        if int(msg.data) == 0:
            self._jog_stop()
            return
        unit = self._jog_vector(msg.data)
        if unit is None:
            self.get_logger().warn(f"[jog_joint] 축 번호가 범위를 벗어났습니다: {msg.data}")
            return
        scale = self._jog_scale()
        speed = self.get_parameter('jog_joint_speed_max').value * scale
        accel = self.get_parameter('jog_accel_max').value * scale
        qd = self._round6([value * speed for value in unit])
        hold = self.get_parameter('jog_hold_time').value
        self.robot_primary.send_script(f"speedj({qd}, {round(accel, 6)}, {hold})")

    def cb_jog_tcp(self, msg):
        if not self.connected:
            return
        if int(msg.data) == 0:
            self._jog_stop()
            return
        unit = self._jog_vector(msg.data)
        if unit is None:
            self.get_logger().warn(f"[jog_tcp] 축 번호가 범위를 벗어났습니다: {msg.data}")
            return
        # 앞 3개는 직선 속도, 뒤 3개는 회전 속도라 단위가 다르다.
        scale = self._jog_scale()
        linear = self.get_parameter('jog_tcp_speed_max').value * scale
        angular = self.get_parameter('jog_tcp_rot_speed_max').value * scale
        accel = self.get_parameter('jog_accel_max').value * scale
        xd = self._round6([
            value * (linear if index < 3 else angular)
            for index, value in enumerate(unit)
        ])
        hold = self.get_parameter('jog_hold_time').value
        self.robot_primary.send_script(f"speedl({xd}, {round(accel, 6)}, {hold})")

    def cb_move_home(self, req, res):
        """안전 높이까지 movel로 올린 뒤 movej로 홈 관절값에 간다."""
        if not self.connected:
            res.success = False
            res.message = "[move_home] 로봇에 연결되어 있지 않습니다."
            return res

        entry = self.registers.write_entry('home_joint')
        regs = self.robot_modbus.get_all_registers(entry.address, entry.count) \
            if entry.available else []
        if len(regs) != 6:
            res.success = False
            res.message = "[move_home] 홈 관절값이 저장되어 있지 않습니다."
            self.get_logger().warn(res.message)
            return res

        scale = self.registers.rotation_scale
        joints = [value * scale / 1000.0 for value in regs]   # mrad -> rad
        lift_z = self.get_parameter('home_lift_z').value
        script = (
            "p = get_actual_tcp_pose()\n"
            f"if (p[2] < {lift_z}):\n"
            f"  p[2] = {lift_z}\n"
            "  movel(p, a=1.2, v=0.25)\n"
            "end\n"
            f"movej({joints}, a=1.4, v=0.5)"
        )
        ok = self.robot_primary.send_script(script)
        res.success = bool(ok)
        res.message = "[move_home] 홈 이동을 요청했습니다." if ok else "[move_home] 스크립트 전송 실패"
        return res

    def _execute_dash_cmd(self, func, name, response):
        res_str = func()
        response.success = True if res_str is not None else False
        response.message = f"[{name}] Result: {res_str}"
        return response

    def cb_dash_mode(self, req, res): return self._execute_dash_cmd(self.robot_dash.robot_mode, "robotMode", res)
    def cb_dash_status(self, req, res): return self._execute_dash_cmd(self.robot_dash.robot_status, "status", res)
    def cb_dash_power_on(self, req, res): return self._execute_dash_cmd(self.robot_dash.robot_power_on, "robotControl -on", res)
    def cb_dash_power_off(self, req, res): return self._execute_dash_cmd(self.robot_dash.robot_power_off, "robotControl -off", res)
    def cb_dash_brake(self, req, res): return self._execute_dash_cmd(self.robot_dash.robot_brakeRelease, "brakeRelease", res)
    def cb_dash_play(self, req, res): return self._execute_dash_cmd(self.robot_dash.robot_play, "play", res)
    def cb_dash_pause(self, req, res): return self._execute_dash_cmd(self.robot_dash.robot_pause, "pause", res)
    def cb_dash_stop(self, req, res): return self._execute_dash_cmd(self.robot_dash.robot_stop, "stop", res)

    def destroy_node(self):
        self.robot_dash.disconnect_29999()
        self.robot_primary.disconnect_30001()
        self.robot_modbus.disconnect()
        super().destroy_node()

def main(args=None):
    rclpy.init(args=args)
    
    try:
        node = RobotControlNode()
        rclpy.spin(node)
    except SystemExit: 
        pass
    except KeyboardInterrupt: pass
    finally:
        if 'node' in locals():
            node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()