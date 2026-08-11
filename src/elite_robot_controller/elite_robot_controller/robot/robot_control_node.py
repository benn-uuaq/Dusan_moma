import rclpy
from rclpy.node import Node
from std_msgs.msg import String, Int32, Float32MultiArray
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
        robot_ip = self.get_parameter('robot_ip').get_parameter_value().string_value
        map_path = self.get_parameter('register_map').get_parameter_value().string_value
        self.registers = register_map.load(map_path or None)

        self.robot_dash = Robot_29999(robot_ip, 29999)
        self.robot_primary = Robot_30001(robot_ip, 30001)
        self.robot_modbus = Robot_modbus(robot_ip, 502)
        self.alarm_mgr = AlarmManager()
        
        if not self.connect_all_servers(robot_ip):
            self.get_logger().error("[ERROR] 로봇 연결 실패")
            raise SystemExit()
        
        # self.connect_all_servers()

        # 발행할 토픽 정의
        self.pub_robot_mode = self.create_publisher(Int32, 'robot/status/robot_mode', 10)
        self.pub_control_method = self.create_publisher(Int32, 'robot/status/control_method', 10)
        self.pub_op_mode = self.create_publisher(Int32, 'robot/status/operation_mode', 10)
        self.pub_tcp_pose = self.create_publisher(Float32MultiArray, 'robot/status/tcp_pose', 10)
        self.pub_tcp_pose_zero = self.create_publisher(Float32MultiArray, 'robot/status/tcp_pose_zero', 10)
        self.pub_alarm = self.create_publisher(String, 'robot/status/alarms', 10)

        # 대시보드 명령 서비스 매핑
        self.create_service(Trigger, 'robot/dashboard/robot_mode', self.cb_dash_mode)
        self.create_service(Trigger, 'robot/dashboard/status', self.cb_dash_status)
        self.create_service(Trigger, 'robot/dashboard/power_on', self.cb_dash_power_on)
        self.create_service(Trigger, 'robot/dashboard/power_off', self.cb_dash_power_off)
        self.create_service(Trigger, 'robot/dashboard/brake_release', self.cb_dash_brake)
        self.create_service(Trigger, 'robot/dashboard/play', self.cb_dash_play)
        self.create_service(Trigger, 'robot/dashboard/pause', self.cb_dash_pause)
        self.create_service(Trigger, 'robot/dashboard/stop', self.cb_dash_stop)

        # 위치 저장과 홈 이동은 값이 없는 한 번짜리 명령이므로 Trigger를 쓴다.
        self.create_service(Trigger, 'robot/command/save_home_pose', self.cb_save_home_pose)
        self.create_service(Trigger, 'robot/command/save_start_pose', self.cb_save_start_pose)
        self.create_service(Trigger, 'robot/command/move_home', self.cb_move_home)

        # 값이 있는 명령은 토픽으로 받는다. 작업 속도는 mm/s, 조그는
        # 레지스터에 그대로 넣을 코드값이다.
        self.create_subscription(Int32, 'robot/command/linear_speed', self.cb_linear_speed, 10)
        self.create_subscription(Int32, 'robot/command/jog_joint', self.cb_jog_joint, 10)
        self.create_subscription(Int32, 'robot/command/jog_tcp', self.cb_jog_tcp, 10)

        # 10Hz 주기로 모드버스 데이터 갱신 및 30001 알람 수집
        self.timer = self.create_timer(0.1, self.update_robot_loop)
        self.get_logger().info("[DEBUG]] ELITE Robot 제어 ROS2 노드가 활성화되었습니다.")

    def connect_all_servers(self, robot_ip):
        try:
            d_ok = self.robot_dash.connect_29999()
            p_ok = self.robot_primary.connect_30001()
            m_ok = self.robot_modbus.connect()
            return d_ok and p_ok and m_ok
        except Exception as e:
            self.get_logger().error(f"[ERROR] 소켓 연결 중 예외 발생: {e}")
            return False

    def update_robot_loop(self):
        self.publish_code('robot_mode', self.pub_robot_mode)
        self.publish_code('control_method', self.pub_control_method)
        self.publish_code('operation_mode', self.pub_op_mode)
        self.publish_pose('tcp_absolute', self.pub_tcp_pose)
        self.publish_pose('tcp_zero_relative', self.pub_tcp_pose_zero)

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

        scale = self.registers
        pose_msg = Float32MultiArray()
        pose_msg.data = [
            regs[0] * scale.position_scale,
            regs[1] * scale.position_scale,
            regs[2] * scale.position_scale,
            regs[3] * scale.rotation_scale,
            regs[4] * scale.rotation_scale,
            regs[5] * scale.rotation_scale,
        ]
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

    def cb_save_home_pose(self, req, res):
        return self._write_service('save_home_pose', 1, res)

    def cb_save_start_pose(self, req, res):
        return self._write_service('save_start_pose', 1, res)

    def cb_move_home(self, req, res):
        return self._write_service('move_home', 1, res)

    def _write_topic(self, name, msg):
        """토픽으로 받은 값을 레지스터에 쓰고 실패만 기록한다."""
        success, message = self.write_register(name, int(msg.data))
        if not success:
            self.get_logger().warn(message)

    def cb_linear_speed(self, msg):
        self._write_topic('linear_speed', msg)

    def cb_jog_joint(self, msg):
        self._write_topic('jog_joint', msg)

    def cb_jog_tcp(self, msg):
        self._write_topic('jog_tcp', msg)

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