# SPDX-License-Identifier: GPL-3.0-or-later
cmake_minimum_required(VERSION 3.20)
project(quicksdf_core LANGUAGES CXX)

add_library(quicksdf_core SHARED quicksdf_core.cpp)
target_compile_features(quicksdf_core PRIVATE cxx_std_17)
if(MSVC)
  target_compile_options(quicksdf_core PRIVATE /O2 /W4 /permissive- /EHsc /Brepro)
  target_link_options(quicksdf_core PRIVATE /Brepro)
endif()
if(APPLE)
  # macOS publishes libquicksdf_core.dylib next to the Windows DLL; the
  # extension picks the file it finds in bin/ at import time.
  target_compile_options(quicksdf_core PRIVATE -O2 -fvisibility=hidden)
  set_target_properties(quicksdf_core PROPERTIES
    MACOSX_DEPLOYMENT_TARGET "11.2"
  )
endif()
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()
find_package(Threads REQUIRED)
target_link_libraries(quicksdf_core PRIVATE Threads::Threads)
set_target_properties(quicksdf_core PROPERTIES
  RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
  RUNTIME_OUTPUT_DIRECTORY_RELEASE "${CMAKE_BINARY_DIR}/bin"
  LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
  LIBRARY_OUTPUT_DIRECTORY_RELEASE "${CMAKE_BINARY_DIR}/bin"
  ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/lib"
)
