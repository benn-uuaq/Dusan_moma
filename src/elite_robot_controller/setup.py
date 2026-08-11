import os
from glob import glob
from setuptools import find_packages, setup

package_name = 'elite_robot_controller'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.launch.py')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='dusan_ws',
    maintainer_email='jing20c@gmail.com',
    description='Elite CS612 협동로봇 제어 ROS 2 노드 (SMR 비파괴 검사 프로젝트)',
    license='Proprietary',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            'robot_control_node = elite_robot_controller.robot.robot_control_node:main',
        ],
    },
)
