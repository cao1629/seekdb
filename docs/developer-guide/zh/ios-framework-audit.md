# iOS 动态 framework 与桌面 C ABI 输入审计（2026-10-07）

状态：本文主体保留初始输入审计的历史记录。随后获得使用既有本地 driver/Connector 缓存的授权，已在本仓库实现真机与模拟器动态 framework；当前用法、验证及限制见 [ios-build.md](ios-build.md)。旧静态 runtime 和内部 SQL proxy 不作为动态 C ABI 验收证据。

## 源码范围与工作区

- 允许的引擎源码目录：`/Users/longda/work/repo/db/ob/github/seekdb.longda`。
- 审计基线：`1288082d49326d105d22444cb3c6c8fd7ace62ca`。
- 独立分支：`codex/ios-dynamic-framework`，从 `ios/iphone13-17-iphoneOS2627-macOS27` 创建；创建前工作区干净。
- 未下载引擎或驱动源码，未修改 QuickLang，未清理构建缓存，未启动或终止其他构建，未改变签名或系统环境设置。

## 初始审计时的仓库实现

`src/observer/CMakeLists.txt` 定义静态 `seekdb_ios_runtime`，链接 `oceanbase_static` 和 `sql_nio`。`src/observer/ios/seekdb_ios.h` 只定义七个生命周期 ABI。运行实现同步阻塞，需要专用线程；仅支持一次启动/进程，使用 App 数据目录和 Unix socket。

`cmake/Env.cmake` 明确指出 `BUILD_EMBED_MODE` 已废弃且不起作用。对本仓库 `src`、`rust`、`cmake` 的搜索未找到 `seekdb_open`、`seekdb_connect` 或 `seekdb_result_get_str` 的 C driver 实现。`unittest/ios_build/sql_probe.cpp` 使用 `ObMySQLProxy`，不能证明独立 MySQL socket 客户端或桌面 C ABI 可用。

已有 `build_ios_arm64` 和 `build_ios_sim_arm64` 缓存。真机缓存为 arm64、iphoneos、最低 iOS 18.0、RelWithDebInfo、`SEEKDB_IOS_TEST_HOOKS=OFF`；这些只是配置值，不能证明最终 framework 的平台、依赖或 hook 状态。当前没有交付 `framework/SeekDB.framework`。

## 初始审计时的范围外输入

只读检查发现：

- 契约头文件：`/Users/longda/work/repo/myself/quicklang/deps/seekdb/include/seekdb.h`。
- 完整 driver 缓存：`/Users/longda/work/repo/myself/quicklang/deps/cache/seekdb-bindings-src/lib`。
- driver checkout revision：`086008a97d1ec1bd07ae09933a51069dad38dd08`。
- MariaDB Connector/C 缓存：该 checkout 的 `deps/mariadb-connector-c`，revision `46880b003653a000e9588bd73c8b1dd65088c686`。
- driver 的 `lib/include/seekdb.h` 与 QuickLang 的契约头文件通过 `cmp` 逐字相同。

没有复制或编译上述外部缓存。它们不是本任务指定目录内的输入，使用前需要明确扩大本地源码范围，或由用户将允许使用的源码放入本仓库。

driver 的 `lib/src/seekdb.c` 已实现连接、结果集、事务和值接口，但 `seekdb_open` 解析外置引擎路径并创建进程；`lib/src/port.c` 的 POSIX 路径调用 `posix_spawn`。不能原样用于 iOS。连接和结果实现依赖 `mysql.h`/MariaDB Connector/C，本仓库现有 iOS 依赖尚未提供这一 driver 配套实现。

## 完整接口与语义缺口

目标包括全部 25 个函数声明，而不只是相同名称：

- 生命周期：`seekdb_open`、`seekdb_close`、`seekdb_connection_options`。
- 连接与错误：`seekdb_connect`、`seekdb_disconnect`、`seekdb_last_error`。
- 查询和结果：`seekdb_query`、`seekdb_result_free`、`seekdb_result_column_count`、`seekdb_result_column_name`、`seekdb_result_column_type_id`、`seekdb_result_row_count`、`seekdb_result_next`、`seekdb_result_get_int64`、`seekdb_result_get_uint64`、`seekdb_result_get_float`、`seekdb_result_get_str`。
- 事务：`seekdb_trx_begin`、`seekdb_trx_commit`、`seekdb_trx_rollback`。
- 值与分配：`seekdb_value_free`、`seekdb_value_create_int64`、`seekdb_value_get_int64`、`seekdb_malloc`、`seekdb_free`。

需要核实 opaque handle 所有权、借用字符串有效期、NULL 与空 UTF-8 字符串、显式 SQL 长度、列类型、有符号/无符号数值转换、遍历结束错误码、MySQL 错误保存、autocommit 和事务行为。

桌面 `open` 支持多个 handle、进程共享和可选 TCP 参数；iOS runtime 只允许一次启动/进程且必须禁用 TCP。桌面头文件还规定 POSIX endpoint 是 `/tmp` 下的每 handle alias，iOS 必须使用允许的 App 沙箱路径。不能声称所有桌面语义完全一致：需要明确 iOS 支持的参数集合、重复 open/close 行为及 socket endpoint 的平台差异。现有 runtime 固定初始配置，也未提供桌面 parameters 的转发和重启持久化配置契约。

## 初始审计提出的实现与验收条件

明确可用 driver/connector 输入后，在本仓库内适配 `open/close` 为专用线程启动/停止本地 singleton，保留客户端结果语义，禁止进程创建。Connector/C 需独立构建 iOS 静态库并保留许可/来源信息，不能链接缓存中的 macOS 动态库。

正式 framework 名称与 CFBundleExecutable 为 `SeekDB`，二进制为 MH_DYLIB，install name 为 `@rpath/SeekDB.framework/SeekDB`，用 export list 隐藏引擎/Rust 全局符号。封装引擎、Rust 及第三方静态依赖；附公开头文件、Info.plist、revision、平台/部署版本、依赖清单及可验证的 disabled hook 标记。

计划路径为 `build_ios_arm64/framework/SeekDB.framework` 和 `build_ios_sim_arm64/framework/SeekDB.framework`；尚无可提供的 framework 构建命令或产物。现有 `./build.iphone.sh release --target seekdb_ios_link_check` 只构建旧静态链接检查，不能作为动态 framework 命令。

验收需由独立 App 探针 `dlopen/dlsym` 同一 C ABI，运行真实 socket SQL、事务提交/回滚、nullable UTF-8，检查关闭及新进程重启持久化。然后分别验证真机 arm64 与模拟器 arm64，真机需签名嵌入、安装和运行。上述项目本次全部未验证。

## 后续适配结果（2026-10-07）

在明确授权后，将上述本地 Connector/C 3.4.8 缓存复制到 `deps/ios-driver/`，客户端逻辑复制并适配到 `src/observer/ios/driver/`；来源 revision 与许可见 `SOURCE.json`。未下载引擎、bindings 或 Connector 源码，未修改 QuickLang。引擎始终来自本 checkout。

公开 `seekdb.h` 与桌面契约逐字相同，导出 25 个桌面函数及 7 个原有 iOS 生命周期/诊断函数。客户端连接、结果和值/事务逻辑沿用桌面 driver；open/close 改为进程内单例线程。socket alias 位于沙箱 tmp，拒绝 TCP，同目录多个 handle 共享引擎，最后一个 close 停止并 join，每个 App 进程只允许一次启动。接口 ABI 一致不等于所有桌面生命周期行为一致。

真机与模拟器二进制已通过 MH_DYLIB、arm64、对应 LC_BUILD_VERSION、最低 iOS 18、系统动态依赖、@rpath install name、精确导出及 disabled hook 验证。独立动态探针覆盖真实 Connector/C socket SQL、提交/回滚、NULL/空串/UTF-8、错误保存、停止和新进程持久化。最初真机报告虽已完成 SQL，却在加载线程退出时触发 jemalloc TSD 析构崩溃；根因是引擎默认 background_thread:true 与 iOS jemalloc 未编译后台线程支持不兼容，bootstrap 停在 recursible；iOS 改为 background_thread:false，并在加载线程检查 allocator bootstrap 完成，并加入显式 pthread_join 后的 worker_exit 证据要求。当前产物身份及最终设备证据以使用文档和 tracked evidence 为准。
