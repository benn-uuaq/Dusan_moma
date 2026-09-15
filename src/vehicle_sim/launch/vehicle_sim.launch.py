"""차량 모의기 실행: ros2 launch vehicle_sim vehicle_sim.launch.py"""
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(package="vehicle_sim", executable="vehicle_sim_node", name="vehicle_sim",
             output="screen"),
    ])
