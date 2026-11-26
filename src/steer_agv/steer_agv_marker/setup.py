from setuptools import setup

name = "steer_agv_marker"
setup(name=name, version="0.1.0", packages=[name],
      data_files=[("share/ament_index/resource_index/packages", ["resource/" + name]),
                  ("share/" + name, ["package.xml", "README.md", "marker_0_140mm.pdf"])],
      tests_require=["pytest"], entry_points={"console_scripts": [
          "marker_localizer = steer_agv_marker.node:main",
          "generate_marker = steer_agv_marker.generate:main",
      ]})
