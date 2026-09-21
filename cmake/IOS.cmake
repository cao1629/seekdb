# Copyright (c) 2026 OceanBase.
# SPDX-License-Identifier: Apache-2.0

# Configure the Apple target before Env.cmake selects host compiler defaults.
# Select iphoneos for devices or iphonesimulator for Apple Silicon simulators.
set(APPLE TRUE)
set(CMAKE_SYSTEM_PROCESSOR arm64)
set(CMAKE_OSX_ARCHITECTURES arm64 CACHE STRING "iOS target architecture")
set(CMAKE_OSX_SYSROOT iphoneos CACHE STRING "iOS SDK")
set(CMAKE_OSX_DEPLOYMENT_TARGET 18.0 CACHE STRING "Minimum iOS version")
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)
if(NOT CMAKE_OSX_ARCHITECTURES STREQUAL "arm64")
  message(FATAL_ERROR "The experimental iOS build currently supports arm64 only")
endif()
if(NOT OB_CC)
  execute_process(COMMAND xcrun --sdk "${CMAKE_OSX_SYSROOT}" --find clang
    OUTPUT_VARIABLE OB_CC OUTPUT_STRIP_TRAILING_WHITESPACE
    COMMAND_ERROR_IS_FATAL ANY)
endif()
if(NOT OB_CXX)
  execute_process(COMMAND xcrun --sdk "${CMAKE_OSX_SYSROOT}" --find clang++
    OUTPUT_VARIABLE OB_CXX OUTPUT_STRIP_TRAILING_WHITESPACE
    COMMAND_ERROR_IS_FATAL ANY)
endif()
execute_process(COMMAND xcrun --sdk "${CMAKE_OSX_SYSROOT}" --show-sdk-path
  OUTPUT_VARIABLE SEEKDB_IOS_SDK_PATH OUTPUT_STRIP_TRAILING_WHITESPACE
  COMMAND_ERROR_IS_FATAL ANY)
if(CMAKE_OSX_SYSROOT MATCHES "[Ss]imulator")
  set(SEEKDB_IOS_RUST_TARGET aarch64-apple-ios-sim)
  set(SEEKDB_IOS_CLANG_TARGET "arm64-apple-ios${CMAKE_OSX_DEPLOYMENT_TARGET}-simulator")
else()
  set(SEEKDB_IOS_RUST_TARGET aarch64-apple-ios)
  set(SEEKDB_IOS_CLANG_TARGET "arm64-apple-ios${CMAKE_OSX_DEPLOYMENT_TARGET}")
endif()

execute_process(COMMAND xcrun --sdk "${CMAKE_OSX_SYSROOT}" --show-sdk-version
  OUTPUT_VARIABLE SEEKDB_IOS_SDK_VERSION OUTPUT_STRIP_TRAILING_WHITESPACE
  COMMAND_ERROR_IS_FATAL ANY)
execute_process(COMMAND xcrun --sdk "${CMAKE_OSX_SYSROOT}" --find ld
  OUTPUT_VARIABLE SEEKDB_IOS_LINKER OUTPUT_STRIP_TRAILING_WHITESPACE
  COMMAND_ERROR_IS_FATAL ANY)
