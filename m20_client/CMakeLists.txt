cmake_minimum_required(VERSION 3.0.2)
project(m20_client)

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

add_executable(client_node src/client_node.cpp)
add_dependencies(client_node ${${PROJECT_NAME}_EXPORTED_TARGETS} ${catkin_EXPORTED_TARGETS})
target_link_libraries(client_node ${catkin_LIBRARIES})
