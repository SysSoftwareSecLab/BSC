cmake_minimum_required(VERSION 3.16)
project(bisafecode_native_pair_probes LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(ament_cmake REQUIRED)
find_package(moveit_core REQUIRED)
find_package(geometric_shapes REQUIRED)
find_package(srdfdom REQUIRED)
find_package(urdf REQUIRED)

add_executable(openarm_fcl_pair_probe openarm_fcl_pair_probe.cpp)
ament_target_dependencies(
  openarm_fcl_pair_probe
  moveit_core
  geometric_shapes
  srdfdom
  urdf
)
target_link_libraries(
  openarm_fcl_pair_probe
  moveit_core::moveit_collision_detection_fcl
)

add_executable(openarm_fcl_pair_probe_server openarm_fcl_pair_probe_server.cpp)
ament_target_dependencies(
  openarm_fcl_pair_probe_server
  moveit_core
  geometric_shapes
  srdfdom
  urdf
)
target_link_libraries(
  openarm_fcl_pair_probe_server
  moveit_core::moveit_collision_detection_fcl
)
