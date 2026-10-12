cmake_minimum_required(VERSION 3.0.2)
project(m20_camera)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_INCLUDE_CURRENT_DIR ON)

find_package(catkin REQUIRED COMPONENTS
    roscpp
    sensor_msgs
)

find_package(OpenCV 4 REQUIRED)
find_package(PkgConfig REQUIRED)
pkg_check_modules(GST REQUIRED gstreamer-1.0 gstreamer-app-1.0)

catkin_package(
    CATKIN_DEPENDS roscpp sensor_msgs
)

include_directories(
    include
    ${catkin_INCLUDE_DIRS}
    ${OpenCV_INCLUDE_DIRS}
    ${GST_INCLUDE_DIRS}
)

add_executable(video_node src/video_node.cpp)

target_link_libraries(video_node
    ${catkin_LIBRARIES}
    ${OpenCV_LIBS}
    ${GST_LIBRARIES}
)

target_link_directories(video_node PRIVATE ${GST_LIBRARY_DIRS})
