import struct
import socket
import select
import time
import queue
from pyModbusTCP.client import ModbusClient

DEFAULT_TIMEOUT = 10.0
MESSAGE_TYPE_ROBOT_STATE = 16
MESSAGE_TYPE_ROBOT_MESSAGE = 20

FMT_HEADER = 'IB'
FMT_ROBOT_MODE = 'IBQ???????BBdddB??I'
FMT_JOINT_HEADER = 'IB'     
FMT_JOINT_DATA = 'dddiiiffffBI'
FMT_CARTESIAN = 'IBdddddddddddd'
FMT_CONFIG = 'IB'+'dd'*6+'dd'*6+'ddddd'+'d'*6+'d'*6+'d'*6+'d'*6+'IIIBBBB'
FMT_MASTERBOARD = 'IBIIBBBdddBBBdddffffB???B'
FMT_ADDITIONAL = 'IB????B'
FMT_TOOL = 'IBBBddfBffB'
FMT_SAFETY = 'IBIbBdddd'
FMT_TOOL_COMM = 'IB?III?Bff'

class RobotDataConfig():
    def __init__(self):
        self.names_pre = [
            'total_message_len', 'total_message_type',
            'mode_sub_len', 'mode_sub_type', 'timestamp', 'reserved_1', 'reserved_2',
            'is_robot_power_on', 'is_emergency_stopped', 'is_robot_protective_stopped',
            'is_task_running', 'is_task_paused', 'robot_mode', 'robot_control_mode',
            'target_speed_fraction', 'speed_scaling', 'target_speed_fraction_limit',
            'get_robot_speed_mode', 'reserved_3', 'is_in_package_mode', 'reserved_4',
            'joint_sub_len', 'joint_sub_type'
        ]
        self.names_joint = [
            'actual_joint', 'target_joint', 'actual_velocity', 
            'joint_reserved_1', 'joint_reserved_2', 'joint_reserved_3',
            'current', 'voltage', 'temperature', 'torques', 'mode', 'joint_reserved_4'
        ]
        self.names_post = [
            'cartesial_sub_len', 'cartesial_sub_type',
            'tcp_x', 'tcp_y', 'tcp_z', 'rot_x', 'rot_y', 'rot_z',
            'offset_px', 'offset_py', 'offset_pz', 'offset_rotx', 'offset_roty', 'offset_rotz',
            'configuration_sub_len', 'configuration_sub_type',
            'limit_min_joint_0', 'limit_max_joint_0', 'limit_min_joint_1', 'limit_max_joint_1',
            'limit_min_joint_2', 'limit_max_joint_2', 'limit_min_joint_3', 'limit_max_joint_3',
            'limit_min_joint_4', 'limit_max_joint_4', 'limit_min_joint_5', 'limit_max_joint_5',
            'max_velocity_joint_0', 'max_acc_joint_0', 'max_velocity_joint_1', 'max_acc_joint_1',
            'max_velocity_joint_2', 'max_acc_joint_2', 'max_velocity_joint_3', 'max_acc_joint_3',
            'max_velocity_joint_4', 'max_acc_joint_4', 'max_velocity_joint_5', 'max_acc_joint_5',
            'default_velocity_joint', 'default_acc_joint', 'default_tool_velocity', 'default_tool_acc', 'internal_use',
            'dh_a_joint_0', 'dh_a_joint_1', 'dh_a_joint_2', 'dh_a_joint_3', 'dh_a_joint_4', 'dh_a_joint_5',
            'dh_d_joint_0', 'dh_d_joint_1', 'dh_d_joint_2', 'dh_d_joint_3', 'dh_d_joint_4', 'dh_d_joint_5',
            'dh_alpha_joint_0', 'dh_alpha_joint_1', 'dh_alpha_joint_2', 'dh_alpha_joint_3', 'dh_alpha_joint_4', 'dh_alpha_joint_5',
            'dh_theta_joint_0', 'dh_theta_joint_1', 'dh_theta_joint_2', 'dh_theta_joint_3', 'dh_theta_joint_4', 'dh_theta_joint_5',
            'masterboard_version', 'control_box_type', 'robot_type', 'robot_structure', 'tool_io_type', 'reserved_cfg2', 'reserved_cfg3',
            'masterboard_sub_len', 'masterboard_sub_type',
            'digital_input_bits', 'digital_output_bits',
            'standard_analog_input_domain0', 'standard_analog_input_domain1', 'tool_analog_input_domain',
            'standard_analog_input_value0', 'standard_analog_input_value1', 'tool_analog_input_value',
            'standard_analog_output_domain0', 'standard_analog_output_domain1', 'tool_analog_output_domain',
            'standard_analog_output_value0', 'standard_analog_output_value1', 'tool_analog_output_value',
            'masterrbord_temperature', 'robot_voltage', 'robot_current', 'io_current',
            'safety_mode', 'is_robot_in_reduced_mode', 'operational_mode_selector_input',
            'threeposition_enabling_device_input', 'internal_use_mb',
            'additional_sub_len', 'additional_sub_type',
            'is_freedrive_button_pressed', 'reserved_add', 'is_freedrive_io_enabled', 'is_dynamic_collision_detect_enabled', 'reserved_add2',
            'tool_sub_len', 'tool_sub_type',
            'tool_analog_output_domain', 'tool_analog_input_domain', 'tool_analog_output_value', 'tool_analog_input_value',
            'tool_voltage', 'tool_output_voltage', 'tool_current', 'tool_temperature', 'tool_mode',
            'safe_sub_len', 'safe_sub_type',
            'safety_crc_num', 'safety_operational_mode', 'reserved_safe',
            'current_elbow_position_x', 'current_elbow_position_y', 'current_elbow_position_z', 'elbow_radius',
            'tool_comm_sub_len', 'tool_comm_sub_type',
            'is_enable', 'baudrate', 'parity', 'stopbits', 'tci_modbus_status', 'tci_usage', 'reserved_tc1', 'reserved_tc2'
        ]
        self.fmt = (
            '>' +
            FMT_HEADER + FMT_ROBOT_MODE +
            FMT_JOINT_HEADER + (FMT_JOINT_DATA * 6) +
            FMT_CARTESIAN + FMT_CONFIG + FMT_MASTERBOARD +
            FMT_ADDITIONAL + FMT_TOOL + FMT_SAFETY + FMT_TOOL_COMM
        )

class RobotHeader():
    __slots__ = ['type', 'size',]
    @staticmethod
    def unpack(buf):
        rmd = RobotHeader()
        (rmd.size, rmd.type) = struct.unpack_from('>iB', buf)
        return rmd

class RobotData():
    @staticmethod
    def unpack(buf, config):
        data = RobotData()
        try:
            unpacked = struct.unpack(config.fmt, buf)
            it = iter(unpacked)
            for name in config.names_pre: setattr(data, name, next(it))
            for name in config.names_joint: setattr(data, name, [])
            for _ in range(6):
                for name in config.names_joint:
                    getattr(data, name).append(next(it))
            for name in config.names_post: setattr(data, name, next(it))
            return data
        except (struct.error, StopIteration):
            return None
    
class AlarmData:
    def __init__(self, code=None, sub=None, level=None, msg=None):
        self.code = code
        self.sub = sub
        self.level = level
        self.msg = msg
        self.timestamp = time.time()
        self.active = True
    
class ReadAlarm():
    @staticmethod
    def unpack(buf):
        data_length = struct.unpack(">i", buf[0:4])[0]
        data = buf[0:data_length]
        msg_type = data[14]
        if msg_type == 10:
            msg = bytearray(data[23:data_length-1]).decode()
            return AlarmData(msg=msg)
        if msg_type == 6:
            error_code = struct.unpack(">i", data[15:19])[0]
            sub_error_code = struct.unpack(">i", data[19:23])[0]
            level = struct.unpack(">i", data[23:27])[0]
            return AlarmData(code=error_code, sub=sub_error_code, level=level)
        return None
        
class Robot_30001():
    def __init__(self, ip, port2) -> None:
        self.__data_config = RobotDataConfig()
        self.ip = ip
        self.port2 = port2
        self.alarm_queue = queue.Queue()
        self.__sock = None
        self.__buf = b""

    def connect_30001(self):
        try:
            self.__sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.__sock.settimeout(0.5)
            self.__sock.connect((self.ip, self.port2))
            self.__sock.settimeout(10.0) 
            print(f"[DEBUG][Robot Primary] Connected to {self.ip} on port {self.port2}")
            self.__buf = b""
            return self.__sock
        except Exception as e:
            print(f"[ERROR][Robot Primary] Connecting to {self.ip} on port {self.port2}: {e}")
            self.__sock = None 
            return None 
        
    def disconnect_30001(self):
        if self.__sock:
            self.__sock.close()
            self.__sock = None
            print("[Robot Primary] Disconnect")

    def get_data(self):
        return self.__recv()

    def __recv(self):
        try:
            self.__read_socket_no_wait()
        except Exception:
            return None
        last_valid_data = None
        while len(self.__buf) >= 5:
            try:
                head = RobotHeader.unpack(self.__buf)
            except:
                self.__buf = b""
                break
            if len(self.__buf) < head.size:
                break
            payload = self.__buf[:head.size]
            self.__buf = self.__buf[head.size:]
            if head.type == MESSAGE_TYPE_ROBOT_MESSAGE:
                try:
                    alarm = ReadAlarm.unpack(payload)
                    if alarm: self.alarm_queue.put(alarm)
                except: pass
                continue
            if head.type == MESSAGE_TYPE_ROBOT_STATE:
                try:
                    last_valid_data = RobotData.unpack(payload, self.__data_config)
                except: pass
        return last_valid_data

    def __read_socket_no_wait(self):
        while True:
            readable, _, _ = select.select([self.__sock], [], [], 0)
            if not readable: break
            try:
                more = self.__sock.recv(4096)
                if not more: raise ConnectionError("Socket closed")
                self.__buf += more
            except BlockingIOError:
                break
            except Exception:
                break
            
class AlarmManager:
    def __init__(self):
        self.active_alarms = {}

    def process(self, alarm):
        key = (alarm.code, alarm.sub, alarm.msg)
        if key in self.active_alarms:
            return False
        self.active_alarms[key] = alarm
        print("================================")
        print("[ALARM TRIGGERED]")
        if alarm.msg:
            print(f"[ALARM MSG] {alarm.msg}")
        else:
            print(f"[ALARM CODE] E{alarm.code} S{alarm.sub} (level={alarm.level})")
        print("================================")
        return True

class Robot_29999():
    def __init__(self, ip, port1):
        self.sock = None
        self.ip = ip
        self.port1 = port1

    def connect_29999(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(0.5)
            self.sock.connect((self.ip, self.port1))
            self.sock.settimeout(10.0)
            print(f"[DEBUG][Robot Dashboard] Connected to {self.ip} on port {self.port1}")
            try:
                self.sock.recv(4096) 
            except Exception: pass
            return self.sock
        except Exception as e:
            print(f"[ERROR][Robot Dashboard] Connecting to {self.ip} on port {self.port1}: {e}")
            return None

    def send_command_29999(self, command):
        try:
            if self.sock is None:
                if self.connect_29999() is None:
                    return None
            self.sock.sendall(f"{command}\n".encode("utf-8"))
            response = self.sock.recv(4096).decode("utf-8").strip()
            return response
        except Exception as e:
            print(f"[ERROR] Sending command: {e}")
            return None

    def disconnect_29999(self):
        if self.sock:
            self.sock.close()
            self.sock = None
            print("[Robot Dashboard] Disconnect")

    def robot_mode(self): return self.send_command_29999("robotMode")
    def robot_status(self): return self.send_command_29999("status")
    def robot_power_on(self): return self.send_command_29999("robotControl -on")
    def robot_power_off(self): return self.send_command_29999("robotControl -off")
    def robot_brakeRelease(self): return self.send_command_29999("brakeRelease")
    def robot_play(self): return self.send_command_29999("play")
    def robot_pause(self): return self.send_command_29999("pause")
    def robot_stop(self): return self.send_command_29999("stop")
    
class Robot_modbus():       
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.client = None
        self.is_running = False

    def connect(self):
        self.client = ModbusClient(host=self.host, port=self.port, unit_id=255, timeout=1.0)
        
        if self.client.is_open:
            self.client.close()
        
        is_open = self.client.open()
        if is_open:
            self.is_running = True
            print(f"[DEBUG][Robot Modbus] Connected to {self.host} on port {self.port}")
            return True
        else:
            print(f"[ERROR][Robot Modbus] Connecting to {self.host} on port {self.port}: timed out")
            return False

    def disconnect(self):
        if self.client:
            self.client.close()
        self.is_running = False
        print("[Robot Modbus] Disconnect")

    def set_register(self, address, value):
        try:
            write_data = int(float(value))
        except ValueError:
            write_data = 1 if str(value).strip().lower() in ['true', '1'] else 0
        modbus_write_val = write_data & 0xFFFF
        is_success = self.client.write_single_register(address, modbus_write_val)
        if not is_success:
            return False
        import time
        time.sleep(0.05) 
        
        signed_read_val = self.get_register(address)
        if signed_read_val is not None and write_data == signed_read_val:
            return True
        return False

    def get_all_registers(self, start_address, count) -> list:
        regs = self.client.read_holding_registers(start_address, count)
        if regs is None:
            regs = self.client.read_input_registers(start_address, count)
        if regs:
            return [val - 65536 if val > 32767 else val for val in regs]
        return []
    
    def get_register(self, address) -> int:
        result = self.client.read_holding_registers(address, 1)
        if result is None:
            result = self.client.read_input_registers(address, 1)
        if result and len(result) > 0:
            val = result[0]
            return val - 65536 if val > 32767 else val
        return None