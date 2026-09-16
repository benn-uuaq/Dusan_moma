from glob import glob

from setuptools import setup

package_name = "vehicle_sim"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
        (f"share/{package_name}/launch", glob("launch/*.launch.py")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="3S Robotics",
    description="차량 제어 노드 모의기",
    license="Proprietary",
    entry_points={
        "console_scripts": [
            "vehicle_sim_node = vehicle_sim.vehicle_sim_node:main",
            "vehicle_sim_ui = vehicle_sim.vehicle_sim_ui:main",
        ],
    },
)
