# Copyright (c) 2026 OceanBase.
# SPDX-License-Identifier: Apache-2.0
if(NOT EXISTS "${SEEKDB_WASM_DEPS}/lib/libsqlite3.a"
    OR NOT EXISTS "${SEEKDB_WASM_DEPS}/include/sqlite/sqlite3.h")
  message(FATAL_ERROR "Build the pinned SQLite target with tools/wasm/build-sqlite.sh")
endif()
add_library(seekdb_wasm_sqlite STATIC IMPORTED GLOBAL)
set_target_properties(seekdb_wasm_sqlite PROPERTIES
  IMPORTED_LOCATION "${SEEKDB_WASM_DEPS}/lib/libsqlite3.a"
  INTERFACE_INCLUDE_DIRECTORIES "${SEEKDB_WASM_DEPS}/include")
target_link_libraries(seekdb_wasm_share PRIVATE seekdb_wasm_sqlite)
add_executable(test_wasm_sqlite_metadata
  "${SEEKDB_ROOT}/unittest/wasm/test_wasm_sqlite_metadata.cpp"
  "${SEEKDB_ROOT}/src/share/storage/ob_sqlite_connection.cpp")
target_link_libraries(test_wasm_sqlite_metadata PRIVATE seekdb_wasm_engine_options
  seekdb_wasm_sqlite seekdb_wasm_memory seekdb_wasm_runtime_support)
target_compile_options(test_wasm_sqlite_metadata PRIVATE -UNDEBUG)
target_link_options(test_wasm_sqlite_metadata PRIVATE -pthread -sUSE_ZLIB=1
  -sINITIAL_MEMORY=134217728 -sSTACK_SIZE=1048576 -sDEFAULT_PTHREAD_STACK_SIZE=1048576
  -sPTHREAD_POOL_SIZE=3 -sPTHREAD_POOL_SIZE_STRICT=2 -sASSERTIONS=2
  -sEXIT_RUNTIME=1 -sABORTING_MALLOC=0)
add_test(NAME wasm_sqlite_metadata COMMAND ${CMAKE_CROSSCOMPILING_EMULATOR}
  "$<TARGET_FILE:test_wasm_sqlite_metadata>")
set_tests_properties(wasm_sqlite_metadata PROPERTIES TIMEOUT 30)

add_executable(test_wasm_sqlite_wal_mapping
  "${SEEKDB_ROOT}/unittest/wasm/test_wasm_sqlite_wal_mapping.cpp")
target_link_libraries(test_wasm_sqlite_wal_mapping PRIVATE seekdb_wasm_sqlite)
target_include_directories(test_wasm_sqlite_wal_mapping PRIVATE "${SEEKDB_WASM_DEPS}/include/sqlite")
target_compile_features(test_wasm_sqlite_wal_mapping PRIVATE cxx_std_20)
target_compile_options(test_wasm_sqlite_wal_mapping PRIVATE -O2 -fno-builtin -pthread -UNDEBUG)
target_link_options(test_wasm_sqlite_wal_mapping PRIVATE -O2 -pthread -sWASMFS
  -sPTHREAD_POOL_SIZE=3 -sMALLOC=dlmalloc -sABORTING_MALLOC=0
  -sINITIAL_MEMORY=67108864 -sALLOW_MEMORY_GROWTH=0 -sSTACK_SIZE=1048576
  -sDEFAULT_PTHREAD_STACK_SIZE=1048576 -sENVIRONMENT=node -sEXIT_RUNTIME=1 -sASSERTIONS=2)
add_test(NAME wasm_sqlite_wal_mapping COMMAND ${CMAKE_CROSSCOMPILING_EMULATOR}
  "$<TARGET_FILE:test_wasm_sqlite_wal_mapping>")
set_tests_properties(wasm_sqlite_wal_mapping PROPERTIES TIMEOUT 30)
