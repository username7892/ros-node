cmake_minimum_required(VERSION 3.0.2)
project(m20_yolo)

find_package(catkin REQUIRED COMPONENTS
    roscpp
    std_msgs
    sensor_msgs
    m20_msgs
)

catkin_package(
    CATKIN_DEPENDS roscpp std_msgs sensor_msgs m20_msgs
)

# 安装 launch / config / scripts，保证 devel 与 install 空间都能
# 通过 $(find m20_yolo)/config 找到 params.yaml
install(DIRECTORY launch config
        DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION})
install(PROGRAMS scripts/yolo_node.py scripts/stream_node.py
        DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION})
