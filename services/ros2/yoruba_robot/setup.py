import os
from glob import glob

from setuptools import find_packages, setup

PKG = "yoruba_robot"


def share(sub):
    """Install every file in a subdir under share/<pkg>/<sub>/ so launch files
    can find them via get_package_share_directory."""
    return (os.path.join("share", PKG, sub), glob(os.path.join(sub, "*")))


setup(
    name=PKG,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages",
         [os.path.join("resource", PKG)]),
        (os.path.join("share", PKG), ["package.xml"]),
        share("launch"),
        share("description"),
        share("worlds"),
        share("config"),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Olayemi",
    maintainer_email="yemokanlawon@gmail.com",
    description="Yoruba voice-controlled 2-wheel robot: ROS2 digital twin "
                "(real ESP32 + Gazebo + RViz).",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "robot_bridge = yoruba_robot.robot_bridge:main",
            "voice_relay = yoruba_robot.voice_relay:main",
            "environment_publisher = yoruba_robot.environment_publisher:main",
        ],
    },
)
