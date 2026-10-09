# Build the locally vendored Connector/C without inheriting engine include paths.
set(WITH_UNIT_TESTS OFF CACHE BOOL "" FORCE)
set(WITH_CURL OFF CACHE BOOL "" FORCE)
set(WITH_DOCS OFF CACHE BOOL "" FORCE)
set(WITH_SSL OPENSSL CACHE STRING "" FORCE)
set(WITH_EXTERNAL_ZLIB ON CACHE BOOL "" FORCE)
set(ZLIB_LIBRARY "${DEP_DIR}/lib/libz.a" CACHE FILEPATH "" FORCE)
set(ZLIB_INCLUDE_DIR "${DEP_DIR}/include" CACHE PATH "" FORCE)
set(OPENSSL_ROOT_DIR "${DEP_DIR}" CACHE PATH "" FORCE)
set(OPENSSL_INCLUDE_DIR "${DEP_DIR}/include" CACHE PATH "" FORCE)
set(OPENSSL_SSL_LIBRARY "${DEP_DIR}/lib/libssl.a" CACHE FILEPATH "" FORCE)
set(OPENSSL_CRYPTO_LIBRARY "${DEP_DIR}/lib/libcrypto.a" CACHE FILEPATH "" FORCE)
# The pinned dependency is OpenSSL, not LibreSSL; do not execute iOS binaries on the host.
set(LIBRESSL_RESULT 1 CACHE STRING "" FORCE)
set(LIBRESSL_RESULT__TRYRUN_OUTPUT "" CACHE STRING "" FORCE)
set(HAVE_LIBRESSL FALSE CACHE BOOL "" FORCE)
set(INSTALL_PLUGINDIR lib/mariadb/plugin)
foreach(plugin DIALOG CLIENT_ED25519 CACHING_SHA2_PASSWORD SHA256_PASSWORD PARSEC
    MYSQL_CLEAR_PASSWORD AUTH_GSSAPI_CLIENT REMOTE_IO)
  set(PLUGIN_${plugin} OFF CACHE STRING "Disable unneeded dynamically loaded client plugins" FORCE)
endforeach()
add_subdirectory("${CMAKE_SOURCE_DIR}/deps/ios-driver/mariadb-connector-c"
  "${CMAKE_BINARY_DIR}/ios-connector" EXCLUDE_FROM_ALL)

add_library(seekdb_ios_framework SHARED EXCLUDE_FROM_ALL
  seekdb_client.c seekdb_lifecycle.cpp ../seekdb_ios.cpp seekdb.h)
target_include_directories(seekdb_ios_framework PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
set_source_files_properties(seekdb_client.c seekdb_lifecycle.cpp PROPERTIES
  INCLUDE_DIRECTORIES
  "${CMAKE_SOURCE_DIR}/deps/ios-driver/mariadb-connector-c/include;${CMAKE_BINARY_DIR}/ios-connector/include")
target_compile_definitions(seekdb_ios_framework PRIVATE
  SEEKDB_IOS_BUILD_ID="${SEEKDB_IOS_BUILD_ID}"
  SEEKDB_IOS_HOOK_MODE="disabled")
target_link_libraries(seekdb_ios_framework PRIVATE oceanbase_static sql_nio mariadbclient)
target_link_options(seekdb_ios_framework PRIVATE
  "-Wl,-exported_symbols_list,${CMAKE_CURRENT_SOURCE_DIR}/exports.txt")
set(SEEKDB_IOS_FRAMEWORK_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/framework" CACHE PATH
  "Directory for independently staged framework artifacts")
set_target_properties(seekdb_ios_framework PROPERTIES
  FRAMEWORK TRUE
  FRAMEWORK_VERSION A
  OUTPUT_NAME SeekDB
  MACOSX_FRAMEWORK_IDENTIFIER org.oceanbase.seekdb
  MACOSX_FRAMEWORK_INFO_PLIST "${CMAKE_CURRENT_SOURCE_DIR}/Info.plist.in"
  PUBLIC_HEADER "${CMAKE_CURRENT_SOURCE_DIR}/seekdb.h"
  LIBRARY_OUTPUT_DIRECTORY "${SEEKDB_IOS_FRAMEWORK_OUTPUT_DIRECTORY}"
  INSTALL_NAME_DIR "@rpath"
  BUILD_WITH_INSTALL_NAME_DIR TRUE
  SKIP_BUILD_RPATH TRUE
  XCODE_ATTRIBUTE_CODE_SIGNING_ALLOWED NO
  LINK_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/exports.txt;${CMAKE_SOURCE_DIR}/unittest/ios_build/verify_framework.py")
find_package(Python3 COMPONENTS Interpreter REQUIRED)
add_custom_command(TARGET seekdb_ios_framework POST_BUILD
  COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/unittest/ios_build/verify_framework.py"
    --binary "$<TARGET_FILE:seekdb_ios_framework>"
    --source "${CMAKE_SOURCE_DIR}" --build "${CMAKE_BINARY_DIR}"
    --sdk "${CMAKE_OSX_SYSROOT}" --deployment "${CMAKE_OSX_DEPLOYMENT_TARGET}"
  VERBATIM)
