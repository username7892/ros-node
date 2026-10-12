cmake_minimum_required(VERSION 3.0.2)
project(m20_server)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(catkin REQUIRED COMPONENTS
    roscpp
    std_msgs
    m20_msgs
)

catkin_package(
    CATKIN_DEPENDS roscpp std_msgs m20_msgs
)

include_directories(${catkin_INCLUDE_DIRS})

add_executable(server_node src/server_node.cpp)
add_dependencies(server_node ${${PROJECT_NAME}_EXPORTED_TARGETS} ${catkin_EXPORTED_TARGETS})
target_link_libraries(server_node ${catkin_LIBRARIES})
