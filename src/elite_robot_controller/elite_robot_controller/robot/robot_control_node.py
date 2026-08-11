import rclpy
from rclpy.node import Node
from std_msgs.msg import String, Int32, Float32MultiArray
from std_srvs.srv import Trigger

# 방금 만든 robot.py 내부 모듈 정상 참조 조치
from elite_robot_controller.robot.robot_driver import Robot_30001, Robot_29999, Robot_modbus, AlarmManager

class RobotControlNode(Node):
    def __init__(self):
        super().__init__('robot_control_node')
        
        self.declare_parameter('robot_ip', '192.168.227.134')
        robot_ip = self.get_parameter('robot_ip').get_parameter_value().string_value
        
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
        # 주소 66, 71, 72 정밀 수집 및 파싱
        robot_mode = self.robot_modbus.get_register(66)
        control_method = self.robot_modbus.get_register(71)
        operation_mode = self.robot_modbus.get_register(72)
        
        if robot_mode is not None: self.pub_robot_mode.publish(Int32(data=robot_mode))
        if control_method is not None: self.pub_control_method.publish(Int32(data=control_method))
        if operation_mode is not None: self.pub_op_mode.publish(Int32(data=operation_mode))

        # TCP 공간 좌표 정보 트래킹 (384 ~ 389번 레지스터)
        tcp_regs = self.robot_modbus.get_all_registers(384, 6)
        if tcp_regs and len(tcp_regs) == 6:
            pose_msg = Float32MultiArray()
            pose_msg.data = [
                tcp_regs[0] * 0.1, tcp_regs[1] * 0.1, tcp_regs[2] * 0.1,  # XYZ (mm 단위 스케일 보정)
                float(tcp_regs[3]), float(tcp_regs[4]), float(tcp_regs[5]) # Rx, Ry, Rz (mRad)
            ]
            self.pub_tcp_pose.publish(pose_msg)

        # 30001 포트 비동기 백그라운드 실시간 알람 스트림 처리
        self.robot_primary.get_data()
        while not self.robot_primary.alarm_queue.empty():
            alarm = self.robot_primary.alarm_queue.get()
            if self.alarm_mgr.process(alarm):
                alarm_msg = String()
                alarm_msg.data = f"[ALARM] {alarm.msg}" if alarm.msg else f"[ALARM CODE] E{alarm.code} S{alarm.sub}"
                self.pub_alarm.publish(alarm_msg)

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