from pathlib import Path

import xacro
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def start(context):
    impl = LaunchConfiguration("implementation").perform(context)
    package = {"python": "steer_agv_controller_py", "cpp": "steer_agv_controller_cpp"}[impl]
    description = Path(get_package_share_directory("steer_agv_description")) / "urdf/robot_3.xacro"
    params = Path(get_package_share_directory("steer_agv_bringup")) / "config/controller.yaml"
    plc = {key: ParameterValue(LaunchConfiguration(key), value_type=bool)
           for key in ("plc_enabled", "plc_writes_enabled")}
    if impl == "python":
        for key, kind in (("plc_host", str), ("plc_port", int), ("job_feedback_topic", str)):
            if LaunchConfiguration(key).perform(context):
                plc[key] = ParameterValue(LaunchConfiguration(key), value_type=kind)
        plc["debug_ui"] = ParameterValue(LaunchConfiguration("debug_ui"), value_type=bool)
        plc["rcs_log_topics"] = ParameterValue(LaunchConfiguration("rcs_log_topics"), value_type=str)
    return [
        Node(package="robot_state_publisher", executable="robot_state_publisher",
             parameters=[{"robot_description": xacro.process_file(str(description), mappings={
                 "cam_mount_rpy": LaunchConfiguration("cam_mount_rpy").perform(context)}).toxml()}]),
        Node(package=package, executable="agv_controller", parameters=[str(params), plc]),
        Node(package="steer_agv_marker", executable="marker_localizer",
             condition=IfCondition(LaunchConfiguration("marker_camera")),
             parameters=[{k: ParameterValue(LaunchConfiguration(k), value_type=t)
                          for k, t in (("device", str), ("baudrate", int), ("calibration_file", str),
                                       ("marker_id", int))}]),
    ]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("implementation", default_value="python", choices=["python", "cpp"]),
        DeclareLaunchArgument("plc_enabled", default_value="false"),
        DeclareLaunchArgument("plc_host", default_value="", description="Override controller.yaml PLC host"),
        DeclareLaunchArgument("plc_port", default_value="", description="Override controller.yaml PLC port"),
        DeclareLaunchArgument("job_feedback_topic", default_value=""),
        DeclareLaunchArgument("debug_ui", default_value="false"),
        DeclareLaunchArgument("rcs_log_topics", default_value=""),
        DeclareLaunchArgument("plc_writes_enabled", default_value="false"),
        DeclareLaunchArgument("marker_camera", default_value="false"),
        DeclareLaunchArgument("device", default_value="/dev/ttyUSB0"),
        DeclareLaunchArgument("baudrate", default_value="921600"),
        DeclareLaunchArgument("marker_id", default_value="0"),
        DeclareLaunchArgument("calibration_file", default_value=""),
        DeclareLaunchArgument("cam_mount_rpy", default_value="0 0 0"),
        OpaqueFunction(function=start),
    ])
