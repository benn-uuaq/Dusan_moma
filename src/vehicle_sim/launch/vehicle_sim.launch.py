"""차량 모의기 실행.

    ros2 launch vehicle_sim vehicle_sim.launch.py            # 화면 있는 모의기
    ros2 launch vehicle_sim vehicle_sim.launch.py ui:=false  # 화면 없이(헤드리스)
"""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition, UnlessCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    ui = LaunchConfiguration("ui")
    return LaunchDescription([
        DeclareLaunchArgument("ui", default_value="true",
                              description="Tk 화면으로 차량 움직임을 본다"),
        Node(package="vehicle_sim", executable="vehicle_sim_ui", name="vehicle_sim",
             output="screen", condition=IfCondition(ui)),
        Node(package="vehicle_sim", executable="vehicle_sim_node", name="vehicle_sim",
             output="screen", condition=UnlessCondition(ui)),
    ])
