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
