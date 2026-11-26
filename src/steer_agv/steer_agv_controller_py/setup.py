from setuptools import setup

package = "steer_agv_controller_py"

setup(
    name=package,
    version="0.1.0",
    packages=[package],
    tests_require=["pytest"],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package]),
        ("share/" + package, ["package.xml"]),
    ],
    entry_points={"console_scripts": ["agv_controller = " + package + ".main:main"]},
)
