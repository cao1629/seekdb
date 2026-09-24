# iPhone 交叉编译（实验阶段）

最新状态（2026-09-24）：原生 seekdb 引擎已在 iPhone 17 Pro / iOS 27.0 完成 36 步通用 SQL 套件和五轮干净停止。五轮均为 `Stopped/result=0`、`sql_verified=true`，同一数据目录的 `previous_runs` 依次为 0、1、2、3、4；验证后没有新增崩溃报告。日志确认 1 GiB 逻辑预算。测试 App 运行期间保持亮屏，进入 Stopped / Failed 后恢复自动锁屏，不修改系统设置。

分层测试方案已开始实施。Phase 1 已完成物理真机验收：确定性初始化失败在 iPhone 上保留主错误 `-4016`，同时完成 server、curl 和工作目录三项清理，`cleanup_error=0`、`cleanup_status=7`，并确认运行 ID、源码 build ID 和 hook mode 与本次 App 一致。随后关闭 `SEEKDB_IOS_TEST_HOOKS` 全量重建，普通 App 在同一数据目录连续完成两轮 36 步通用 SQL、干净停止和持久化恢复，`previous_runs` 为 0、1；三轮执行均未产生新的 seekdb 崩溃或 Jetsam 报告。英文设计见 [iOS Layered Test Strategy Design](../../ios-layered-test-strategy-design.md)，清单格式见 [iOS Test Inventory Schema](../../ios-test-inventory-schema.md)。

测试 App 现提供显式 device registry 模式。主机启动时同时设置 `SEEKDB_IOS_TEST_SUITE`、`SEEKDB_IOS_TEST_FILTER` 和唯一 `SEEKDB_IOS_TEST_RUN_ID`；App 在引擎 Running 后执行匹配 case，并把 `run_start`、`case_start`、逐项 assertion、`case_end`、`run_complete` 依次追加到 run-scoped JSONL，每条写入后立即 flush。每个 case 的 `timeout_seconds` 是实际 deadline，而不只是元数据：由于任意 C++ callback 无法安全强制取消，watchdog 到期后串行写入失败 assertion、`case_end/result=124` 和 `run_complete/result=124`，flush 后以 124 终止 App 进程，禁止阻塞 callback 随后产生通过结论。host 因而能在 case deadline 加轮询开销内取得有界失败；超时进程不声称完成正常 engine cleanup，后续启动必须使用独立测试目录或走数据库恢复语义。

`run_device_suite.py` 只复制 allowlist JSONL 和固定生命周期状态，并校验 run/build identity、suite/filter、独立预期 case 覆盖、重复或缺失完成事件、device origin、非零结果及最终干净停止。只有尚无 `run_complete` 的合法前缀或最后一条未写完的 JSON 行会继续轮询；已经包含终态但校验失败的证据立即保留原始 `ValueError`，不会被改写为超时。JSON writer 保留合法多字节 UTF-8，按 JSON 规则转义引号和控制字符，并把每个非法输入字节确定性编码为 U+FFFD，因此任意诊断字节都不会破坏 JSONL。原始 `devicectl` 元数据不写入仓库证据。runner 显式选择最多 64 字符的独立数据目录，避免继承默认旧目录。没有 suite 环境变量的普通启动仍执行原 36 步 SQL。

2026-09-24 真机验收已完成：`ios.registry.smoke` 在独立数据目录产生完整的 5 条事件序列，依次为 `run_start`、`case_start`、1 条 assertion、`case_end`、`run_complete`，case/result 均为 0；随后 App 达到 `Stopped/result=0`、`suite_result=0`、`cleanup_status=7`、`cleanup_error=0` 且工作目录恢复。最终复验使用同一个已签名的 follow-up HEAD，严格按 registry smoke、普通模式首轮、同一全新数据目录普通模式重启轮的顺序执行。两轮普通模式各完成 36 步 SQL 后干净停止，持久计数为 0、1；两轮 JSONL 均为 36 个成功 step 加最终 `complete/result=0`。主机 45 项 iOS Python 契约、iphoneos compile-only、未签名和签名 App 完整链接、codesign、安装均通过；完整顺序前后的相关 crash/Jetsam 增量为空。脱敏 JSONL、生命周期状态和运行摘要只保存在忽略目录 `build_ios_arm64/device-evidence/task2-final-head/`。自动签名因 Xcode 当前没有登录账号而不可用，本次仅复用本机已存在并经 bundle、设备范围、有效期、证书私钥校验的开发描述文件手工签名；账号、Team ID、证书和设备唯一标识均未写入仓库证据。

同日质量修复复验增加阻塞 callback、终态错误分类和非法 UTF-8 的可执行契约，focused 14 项及完整 host 49 项通过。修复版重新完成 iphoneos runtime/App 链接、既有本机描述文件手工签名校验和安装，并按 smoke、全新目录普通首轮、同目录 restart 的顺序执行；结果仍为 smoke 5 个事件、两轮各 36 个成功 SQL step 加完成记录、`previous_runs=0/1`，相关 crash/Jetsam 增量为 0。脱敏证据保存在忽略目录 `build_ios_arm64/device-evidence/task2-quality/`。

`build.iphone.sh` 为 iPhone ARM64 和 Apple Silicon iOS 模拟器配置 CMake、Rust 和 Apple SDK。默认目标是 `oceanbase_static`。目前不是已完成的 iOS 产品构建流程；脚本不生成、签名或安装 App。

完整环境设置、逐文件修改原因和失败尝试见 [iOS 移植变更记录](ios-change-log.md)。后续环境、源码和 CMake 变更必须同步追加该记录，并区分已验证与待验证。

## 使用

需要 macOS、完整 Xcode、CMake 和可执行的 rustup/cargo。默认使用 `/Applications/Xcode.app/Contents/Developer`，可设置 `DEVELOPER_DIR`，无需修改系统 xcode-select。`--init` 安装源码固定版本的 Rust、目标标准库和宿主 Bison/Flex；宿主工具自动安装目前仅支持 Apple Silicon。

```bash
# 在 seekdb 根目录执行：首次准备工具并生成真机构建规则。
./build.iphone.sh --init --configure-only

# 真机：依赖目录必须包含为 iOS 真机编译的第三方库和头文件。
./build.iphone.sh --deps-prefix "$PWD/deps/ios/iphoneos/devel" --jobs 4

# 模拟器使用独立的 SDK、Rust target、依赖和输出目录。
./build.iphone.sh --simulator --init --configure-only
./build.iphone.sh --simulator --deps-prefix "$PWD/deps/ios/iphonesimulator/devel"

# 可指定目标和附加 CMake 选项。
./build.iphone.sh --target ob_sql_server_parser_objects --configure-only -- -DOB_ENABLE_UNITY=OFF
```

Rust 可执行文件由 PATH 或 `CARGO`、`RUSTUP` 提供；脚本也查找仓库内 `deps/ios/cargo/bin`。默认 Rust 缓存在 `deps/ios/cargo` 和 `deps/ios/rustup`；已有缓存可通过 `CARGO_HOME`、`RUSTUP_HOME` 指定。`--init` 不下载 rustup 本身。`./build.iphone.sh --deps-only` 从固定校验和的上游源码构建 zlib 1.2.13、OpenSSL 1.1.1u、curl 8.12.1、Abseil 20211102.0、S2 0.10.0、CRoaring 3.0.0、liblzma 5.4.7、libxml2 2.10.4、protobuf-c 1.4.1 和 SQLite 3.38.1，并验证静态库的 ARM64 架构与 iOS 平台；这还不是完整的第三方依赖集合。依赖源码、下载缓存和安装前缀均位于 `deps/ios/`。

真机输出位于 `build_ios_arm64`，模拟器位于 `build_ios_sim_arm64`，每个目录的 `logs` 保留配置及编译日志。默认最低系统版本 18.0，可通过 `--deployment-target` 调整。编译前至少要求 10 GiB 空闲空间，这只是保护阈值，不是全量构建空间估算。仅对已评估过大小的小目标，可显式设置 `SEEKDB_IOS_MIN_FREE_GIB` 调整阈值。

## 已验证范围（2026-09-24）

在 Xcode 27 / iOS SDK 27 环境下：

- 真机和模拟器 CMake 配置成功。
- 真机 `oceanbase_static` 完整目标编译成功，生成 observer、SQL、storage、share、oblib、malloc 和 parser 共 7 个引擎静态库，逐个通过 iOS ARM64 平台检查。静态库之间仍有链接依赖，不能仅复制 `liboceanbase_static.a` 就运行引擎。
- `sql-nio` Rust 静态库已按 `aarch64-apple-ios` 编译成功。
- jemalloc 5.3.1 和上述 10 项依赖已构建为 iOS ARM64 静态库；ICU 69.1、OpenMP 21.1.8 和 LAPACKE 子集随后也已构建并通过 iOS 平台检查；VSAG 及其依赖的 8 个静态库也已编译并通过平台检查。默认依赖构建现包含上述 14 项。
- `seekdb_ios_runtime` 进程内生命周期静态库编译通过；真机证据覆盖运行、SQL、正常停止及确定性初始化失败后的完整清理。失败注入 App 报告 `Failed/result=-4016`、`cleanup_error=0`、`cleanup_status=7` 且工作目录恢复成功；随后关闭测试 hook 的普通 App 重新通过两轮 36 步 SQL 和干净停止。
- `seekdb_ios_link_check` 完整链接通过，产物约 216 MiB；vtool 显示 IOS/minos 18.0/sdk 27.0，otool -L 仅列 Apple 系统库。此目标是链接探针，没有 UIKit 界面，不能作为 App 运行验收。此次复用已成功构建的 rust-probe 目录；新 Rust 构建目录仍遇到宿主 build-script SIGKILL。
- 修复 zstd 部分链接误用 macOS 平台的问题，验证合并对象中的 ZSTD 内部符号已局部化。
- 为 Boost 1.74 回移上游 1.85 的 NumericConversion 枚举包装修复，只生成 iOS 构建目录中的头文件覆盖层；iOS 编译检查和 macOS 数值转换/溢出测试通过。
- `ob_parser.cpp.o` 经 `file` 验证为 Mach-O ARM64；`xcrun vtool -show-build` 显示平台 IOS、minos 18.0、sdk 27.0。
- 脚本参数路由、App 链接参数、模拟器配置、Cargo 多行参数/失败传播、非法参数、产品中立性和启动清理契约共 23 项测试通过：`python3 -m unittest discover -s unittest/ios_build -v`。
- 分层测试清单由 `unittest/ios_build/generate_test_inventory.py` 生成。它只读取 `git ls-files -z` 返回的已跟踪文件，复用现有 mysqltest runner 的 psmall 选择逻辑，并将分类清单合并后写入 `build_ios_arm64/generated/ios-test-inventory.jsonl`。active mysqltest 与 orphan GTest 必须逐 ID 审核，legacy obtest 的 500 个 ID 必须全部物化；正式生成还会拒绝相关受跟踪文件偏离 HEAD 及未跟踪 active mysqltest。focused 命令为 `python3 unittest/ios_build/test_inventory.py -v`。分类规则及字段见 `docs/ios-test-inventory-schema.md`；生成结果不等同于任何用例已经执行。
- 全新 Rust target 目录的 `aarch64-apple-ios` `libsql_nio.a` 构建通过；主机目标的 3 项 Rust 单元测试、doc-test 及 `cargo clippy --all-targets -- -D warnings` 通过。本机系统会终止由当前 Codex 进程直接生成并启动的宿主 Mach-O，因此验证使用忽略目录中的 LLDB runner；该 runner 不属于项目构建接口。
- 通用 SQL 套件已在真机完成五轮。每轮 36 个 step 全部成功，最终 JSONL 记录为 `complete=true/result=0`；同一数据目录的持久计数连续递增，五轮均完成自动停止。

引擎静态库编译暂用 `deps/3rd/usr/local/oceanbase/deps/devel` 的公共头文件，没有链接其中的 macOS 库。编译引擎静态库的复现命令（不执行最终 App 链接）：

```bash
./build.iphone.sh --jobs 4 \
  --headers-prefix "$PWD/deps/3rd/usr/local/oceanbase/deps/devel" \
  -- -DCMAKE_C_FLAGS_RELWITHDEBINFO=-O2 -DCMAKE_CXX_FLAGS_RELWITHDEBINFO=-O2
```

此命令需要该头文件目录已存在；库文件仍从默认 `deps/ios/iphoneos/devel` 获取。不要把 macOS 库目录传给最终链接的 `--deps-prefix`。新 Apple Clang 的部分既有代码诊断保留为 warning，后续仍需独立审查。

## 尚未完成

- 全新目录的完整依赖流水线及 Rust 宿主 build-script SIGKILL 问题；增量完整链接已通过。磁盘空间约 9.4 GiB，继续构建时仍需关注剩余空间。
- 已新增 `seekdb_ios_run`、`seekdb_ios_request_stop`、`seekdb_ios_get_state`、`seekdb_ios_get_cleanup_status` 和 `seekdb_ios_get_cleanup_error`；`in_process_` 模式跳过服务信号线程，等待结束走 `stop()`，不走原命令行路径的 `_Exit(0)`。该路径已取得 36 步 SQL、多轮正常停止和连续持久化恢复证据。启动失败执行 stop/wait/destroy、curl cleanup 和工作目录恢复，主错误与清理错误分别记录；真机负向验收已通过。接口每进程仅允许调用一次，不可在 UI 线程调用。`BUILD_EMBED_MODE` 仍不能恢复旧 C API。
- iOS ARM64 链接已验证 S2/Abseil ABI、OpenMP 运行库版本及 Rust sql_nio 链接修复；数学和向量功能仍需真机运行验证。
- App 沙箱数据目录、线程和内存限制已完成基础适配；重复停止已验证，前后台切换、锁屏恢复和内存压力仍待验证。
- App 包装、签名、安装、36 步通用 SQL 及五轮正常停止后的持久化恢复已有真机证据；现有模拟器环境不能替代这些真机证据。
- 当前分支没有常规 C++ 全量测试入口所需的 `unittest/CMakeLists.txt` 与 `all_tests_main.cpp`，本机也没有可用的 Linux 容器运行时；因此本页不宣称完整 C++/Linux 测试通过。

所有 seekdb 移植修改、缓存和构建产物保持在本仓库目录内。

## 日志和产物

- `build_ios_arm64/logs/engine-build.log`：引擎静态库构建。
- `build_ios_arm64/logs/sql-nio-build.log`：Rust 构建；保留了宿主构建工具首次运行被 SIGKILL 的失败及后续成功记录。
- `build_ios_arm64/logs/jemalloc-build.log`：jemalloc 交叉编译。
- `deps/ios/iphoneos/build/*/verified.json`：基础依赖的源码版本、校验和、SDK 和目标信息。
- `deps/ios/iphoneos/devel`：已安装并满足当前链接探针的 iOS 第三方库。

## 真机状态及链接复现

2026-09-21 通过 devicectl 确认 iPhone 17 Pro / iOS 27.0 为 wired、connected、paired，Developer Mode Status 为 Enabled (1)。随后通过 Personal Team 自动签名生成 Apple Development 证书，SeekDB Probe 构建、签名验证和安装成功；首次启动被设备信任检查拦截，尚未取得引擎运行或 SQL 证据。账号、私钥不进入 Git。

本次增量链接命令（rust-probe 是此前成功的 Cargo 输出目录，全新环境不能假设它存在）：

```bash
SEEKDB_IOS_MIN_FREE_GIB=6 ./build.iphone.sh --jobs 4 \
  --target seekdb_ios_link_check \
  --headers-prefix "$PWD/deps/3rd/usr/local/oceanbase/deps/devel" \
  -- -DRUST_TARGET_DIR="$PWD/build_ios_arm64/rust-probe" \
  -DOB_ENABLE_STANDBY=OFF \
  -DCMAKE_C_FLAGS_RELWITHDEBINFO=-O2 \
  -DCMAKE_CXX_FLAGS_RELWITHDEBINFO=-O2
```

6 GiB 阈值仅用于已评估的增量链接，默认仍为 10 GiB；不可据此估计全量构建空间。

## UIKit 真机探针

完成上述引擎链接后，在 Xcode Settings / Accounts 登录并选择开发团队。下面命令使用现有引擎产物生成独立 Xcode 项目，由 Xcode 自动申请签名证书和 provisioning profile，并安装到指定设备：

```bash
python3 deps/ios-build/build_app.py --team YOURTEAMID \
  --device YOUR_DEVICE_UDID --bundle-id org.seekdb.iosprobe.yourname --install
```

团队 ID 必须是 Apple 的 10 位标识；设备 ID 用 `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcrun devicectl list devices` 查询。去掉 `--install` 则只生成并签名，不安装。此工具不自动启动应用。

源码位于 `unittest/ios_build/app`，生成项目及产物位于 `build_ios_arm64/app`。构建脚本从 CMake 的 link_check 链接命令提取真实静态依赖，拒绝未知参数，避免手动维护第二份库清单。启动探针后自动以独立 8 MiB 栈线程运行引擎，数据保存在 Documents/seekdb；主线程每秒显示状态并写 Documents/probe-status.json。Running 仅代表生命周期状态，SQL 验证另行记录。Stop engine 按钮请求停止，同一进程不支持再次启动。

Apple 管理的证书和设备描述文件保存在系统凭证目录，不复制进仓库；构建脚本、参数说明和验证结果由 Git 跟踪。

首次安装后，如系统提示未信任开发者，在 iPhone「设置 → 通用 → VPN 与设备管理 → 开发者 App」中信任签名账号，按系统提示完成。随后可点击 SeekDB Probe 图标，或使用 devicectl device process launch 启动。Personal Team 的当前描述文件实测有效期至 2026-09-28；过期后重新构建签名并安装。

真机首次启动已越过信任检查，但旧 UIKit 包装因未采用 Scene 生命周期而触发 SIGTRAP。现已改为 UIWindowSceneDelegate 并声明 Scene manifest，修复版编译通过；覆盖安装期间设备断连，运行验证仍待完成。devicectl 的旧次控制台虽显示退出码 0，实际系统崩溃报告为 SIGTRAP，应以崩溃报告和持久化状态共同判断。

连接恢复后，Scene 修复版已在真机正常显示界面、保存状态。首次引擎调用返回 -4024，真机 LLDB 确认 sysconf(_SC_ARG_MAX) 返回 -1；配置解析增加有界备用上限后，配置和引擎初始化已完成，当前在旧失败目录的 bootstrap 检查返回 -4015。SQL 尚未验证。

运行时当前使用 memory_budget=1G、vector_memory_limit=128M、log_disk_size=2G。memory_budget 是逻辑预算，不是进程 RSS 硬限制；旧 memory_limit 参数在本源码中已弃用，不能用于约束内存配置。

测试新空库可通过 devicectl 启动环境变量 `SEEKDB_PROBE_DATA_NAME` 选择 Documents 内的新子目录（最多 64 个英文字母、数字、下划线或连字符）。默认仍为 seekdb，不自动清除任何失败目录；状态 JSON 同时记录 data_name。验证重启持久化时必须复用同一名称，不能将每次换新目录算作重启验证。

新空库 seekdb-budget-v1 已在 iPhone 17 Pro 达到 Running，日志显示 1 GiB 逻辑预算。SQL 测试版在 Running 后使用内部 SQL proxy 执行通用 36 步套件；结果以 `sql_verified`、`sql_result`、`previous_runs` 及 `Documents/sql-probe-results.jsonl` 的最终 `complete/result` 为准。设置 `SEEKDB_PROBE_AUTO_STOP=1` 可在 SQL 检查返回后自动请求停止。该测试路径尚不能证明 MySQL Unix socket 客户端兼容。2026-09-24 使用数据目录 `ios-generic-20260924` 连续验证五轮，`previous_runs` 为 0 至 4，五轮均完成自动停止；重复停止在本机约需 32 秒。

最新真机进展：SQL 表达式、建库建表、计数写入读回已通过，同一 seekdb-budget-v1 目录跨进程恢复得到 previous_runs=0、1、2。停止曾在 Memtable 管理池及 LS 销毁断言处中止，目前补齐进程内运行时的 stop/wait 顺序后继续验证。尚不能将异常退出后的恢复等同于正常停止验收。

- stop/wait 修复版曾在停止阶段出现 TableGCTask 访问已释放日志流；随后将 storage meta memory manager 的等待提前到 LS 释放前，避免后台 memtable GC 越过日志流生命周期。

## 通用 SQL 兼容性套件

`unittest/ios_build/sql_probe.cpp` 覆盖表达式与持久计数、二进制键大小写及排序、JSON、BLOB、无符号读取、字符串数组、唯一键原子失败、事务回滚、`FOR UPDATE`、乐观版本更新以及三类 CHECK 约束。`ios_probe.lifecycle` 永不由套件清理；每轮只清空无外键的 `feature_matrix` 与 `feature_event` fixture。完整规则见 `unittest/ios_build/README.md`。

2026-09-24 的通用套件已重新编译、签名并在真机完整执行。成功轮次 1、4、5、6、7 的 JSONL 各含 36 个成功 step 和最终完成记录；状态文件均为 `Stopped/result=0`、`sql_verified=true`，持久计数依次为 0、1、2、3、4。中间两轮暴露 fixture 清理对 affected rows 的错误假设，修复后连续四轮通过。旧设备 JSONL 属于已删除的专属套件证据，不能重命名为本次结果。
