cmake_minimum_required(VERSION 3.24)
project(SeekDBProbe LANGUAGES CXX OBJCXX)
if(NOT CMAKE_SYSTEM_NAME STREQUAL "iOS")
  message(FATAL_ERROR "This probe requires the iOS SDK")
endif()
if(NOT EXISTS "${ENGINE_LINK_RESPONSE}" OR NOT DEVELOPMENT_TEAM)
  message(FATAL_ERROR "Provide ENGINE_LINK_RESPONSE and DEVELOPMENT_TEAM")
endif()
add_executable(SeekDBProbe MACOSX_BUNDLE
  main.mm
  ../cpp_device_tests.cpp
  ../device_evidence.cpp
  ../device_test_registry.cpp)
if(RUST_DEVICE_TESTS)
  target_sources(SeekDBProbe PRIVATE ../rust_device_tests.cpp)
  target_compile_definitions(SeekDBProbe PRIVATE SQL_NIO_IOS_DEVICE_TESTS=1)
endif()
target_include_directories(SeekDBProbe PRIVATE
  "${CMAKE_CURRENT_LIST_DIR}/.."
  "${CMAKE_CURRENT_LIST_DIR}/../../../src"
  "${CMAKE_CURRENT_LIST_DIR}/../../../src/oblib"
  "${CMAKE_CURRENT_LIST_DIR}/../../../src/observer/ios"
  "${CMAKE_CURRENT_LIST_DIR}/../../../rust/sql-nio/include")
target_compile_definitions(SeekDBProbe PRIVATE OB_HAVE_BUNDLED_JEMALLOC=1)
target_compile_options(SeekDBProbe PRIVATE $<$<COMPILE_LANGUAGE:OBJCXX>:-fobjc-arc>)
target_link_options(SeekDBProbe PRIVATE "@${ENGINE_LINK_RESPONSE}")
target_link_libraries(SeekDBProbe PRIVATE "-framework UIKit")
set_target_properties(SeekDBProbe PROPERTIES
  CXX_STANDARD 17
  CXX_STANDARD_REQUIRED YES
  CXX_EXTENSIONS NO
  MACOSX_BUNDLE_INFO_PLIST "${CMAKE_CURRENT_LIST_DIR}/Info.plist.in"
  MACOSX_BUNDLE_GUI_IDENTIFIER "${PROBE_BUNDLE_ID}"
  XCODE_ATTRIBUTE_PRODUCT_BUNDLE_IDENTIFIER "${PROBE_BUNDLE_ID}"
  XCODE_ATTRIBUTE_DEVELOPMENT_TEAM "${DEVELOPMENT_TEAM}"
  XCODE_ATTRIBUTE_CODE_SIGN_STYLE Automatic
  XCODE_ATTRIBUTE_CODE_SIGN_IDENTITY "Apple Development"
  XCODE_ATTRIBUTE_TARGETED_DEVICE_FAMILY "1"
  XCODE_ATTRIBUTE_ENABLE_USER_SCRIPT_SANDBOXING YES
  LINK_DEPENDS "${ENGINE_LINK_RESPONSE}")
