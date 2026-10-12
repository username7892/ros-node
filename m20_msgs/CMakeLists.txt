cmake_minimum_required(VERSION 3.0.2)
project(m20_msgs)

find_package(catkin REQUIRED COMPONENTS
    std_msgs
    message_generation
)

add_message_files(
    FILES
    TargetList.msg
)

add_service_files(
    FILES
    SetTargets.srv
)

generate_messages(
    DEPENDENCIES
    std_msgs
)

catkin_package(
    CATKIN_DEPENDS std_msgs message_runtime
)
