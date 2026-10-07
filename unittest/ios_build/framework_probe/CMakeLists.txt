cmake_minimum_required(VERSION 3.24)
project(SeekDBFrameworkProbe LANGUAGES CXX OBJCXX)
if(NOT CMAKE_SYSTEM_NAME STREQUAL "iOS" OR NOT EXISTS "${SEEKDB_FRAMEWORK}/SeekDB")
  message(FATAL_ERROR "Provide an iOS SDK and a verified SEEKDB_FRAMEWORK directory")
endif()
add_executable(SeekDBFrameworkProbe MACOSX_BUNDLE main.mm stability.mm)
target_include_directories(SeekDBFrameworkProbe PRIVATE
  "${SEEKDB_FRAMEWORK}/Headers" "${CMAKE_CURRENT_SOURCE_DIR}/../../../src/observer/ios")
target_compile_options(SeekDBFrameworkProbe PRIVATE -fobjc-arc)
target_compile_definitions(SeekDBFrameworkProbe PRIVATE PROBE_SOURCE_REVISION="${PROBE_SOURCE_REVISION}")
target_link_libraries(SeekDBFrameworkProbe PRIVATE "-framework UIKit")
set_target_properties(SeekDBFrameworkProbe PROPERTIES
  CXX_STANDARD 17
  MACOSX_BUNDLE_GUI_IDENTIFIER "${PROBE_BUNDLE_ID}"
  MACOSX_BUNDLE_INFO_PLIST "${CMAKE_CURRENT_SOURCE_DIR}/Info.plist.in"
  XCODE_ATTRIBUTE_PRODUCT_BUNDLE_IDENTIFIER "${PROBE_BUNDLE_ID}"
  XCODE_ATTRIBUTE_DEVELOPMENT_TEAM "${DEVELOPMENT_TEAM}"
  XCODE_ATTRIBUTE_CODE_SIGN_STYLE Automatic
  XCODE_ATTRIBUTE_CODE_SIGN_IDENTITY "Apple Development"
  XCODE_ATTRIBUTE_TARGETED_DEVICE_FAMILY "1"
  XCODE_ATTRIBUTE_ENABLE_USER_SCRIPT_SANDBOXING NO)
# The Python packager copies and signs Frameworks after Xcode resolves its platform product directory.
