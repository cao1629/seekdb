# iOS 构建与验证

## 2026-10-07：动态 SeekDB.framework 与桌面 C ABI

`codex/ios-dynamic-framework` 已实现进程内动态 framework。App 嵌入并加载
`SeekDB.framework`，在专用 8 MiB 栈线程运行本地引擎；Connector/C 通过实际 Unix
socket 发送 SQL。数据库文件位于 App 私有沙箱。未启用 App Groups 或 TCP。

公开头文件与既有 macOS `seekdb.h` 逐字相同，包含全部 25 个 C 函数；另外导出
7 个 iOS 生命周期/诊断函数。结果遍历、NULL/空 UTF-8、错误、值分配和事务沿用
桌面 driver。引擎/Rust/Connector 与第三方库静态封装到动态 framework，运行时仅依赖
iOS 系统库。build-manifest.json 记录源码提交、dirty 状态、二进制和头文件摘要、
静态链接输入摘要、锁定依赖来源及导出符号；Licenses 保留本地许可文件。

### 支持范围和生命周期

- `seekdb_open` 接受绝对目录和以 NULL 结束的 key/value 参数对。新数据库默认
  memory_budget=1G、vector_memory_limit=128M、log_disk_size=2G；可在首次初始化
  覆盖。已有数据库保留持久配置。CPU/sql 线程固定为 2，mysql_port_mode 固定 disabled。
- 非零 `port` 或非 disabled mysql_port_mode 返回 INVALID_ARGUMENT。
- 同目录再次 open 共享运行中引擎；不同目录返回 INTERNAL_ERROR。后续 handle 的
  参数不重新配置已运行引擎。连接应先 disconnect，再 close 所属 handle。
- 最后一个 close 请求停止并 join 引擎，恢复进程工作目录。每个进程只能启动一次；
  最后 close 后再次 open 返回 INTERNAL_ERROR。重新启动 App 进程才能重开数据库。
- socket endpoint 是 handle 持有的借用字符串，close 后无效。长沙箱路径使用可写
  tmp 中的短 symlink alias；模拟器必要时使用宿主 /tmp。不能硬编码桌面 /tmp 路径。
- 旧 seekdb_ios_run/request_stop 入口仍保留，不能与桌面 driver 生命周期混用。
- 引擎运行期间使用进程工作目录，App 应使用绝对文件路径。framework 应保持加载
  至进程结束；未验证主动 dlclose 或 App 后台长期运行。

### 构建

需要完整 Xcode、CMake、固定 Rust 工具链和依赖。所有输出留在本仓库。

```bash
export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
./build.iphone.sh release --target seekdb_ios_framework --jobs 2
./build.iphone.sh release --simulator --target seekdb_ios_framework --jobs 2
```

framework 目标自动设置 SEEKDB_IOS_FRAMEWORK=ON、SEEKDB_IOS_TEST_HOOKS=OFF、
OB_ENABLE_STANDBY=OFF。后者禁用物理主备/gRPC 路径；当前本地 iOS 依赖不提供
其所需完整归档。Rust 使用仓库内 Cargo/rustup 目录和现有 rustc_lldb_wrapper.py
处理本机 AMFI 对宿主 build scripts 的限制。直接调用 CMake 时也需这些环境：

```bash
export CARGO_HOME="$PWD/deps/ios/cargo"
export RUSTUP_HOME="$PWD/deps/ios/rustup"
export RUSTC_WRAPPER="$PWD/unittest/ios_build/rustc_lldb_wrapper.py"
export CARGO_NET_OFFLINE=true
cmake -S . -B build_ios_arm64 -DSEEKDB_IOS_FRAMEWORK=ON \
  -DSEEKDB_IOS_TEST_HOOKS=OFF -DOB_ENABLE_STANDBY=OFF
cmake --build build_ios_arm64 --target seekdb_ios_framework -j2
```

常规输出分别为 `build_ios_arm64/framework/SeekDB.framework` 和
`build_ios_sim_arm64/framework/SeekDB.framework`。交付时保留按源码 revision 命名的
独立快照，避免覆盖正在集成读取的产物。两种 arm64 平台不能互换。

### 独立动态探针

探针仅链接 UIKit，运行时 dlopen/dlsym framework，使用与桌面相同的头文件。
真机使用既有本地 profile/identity 离线签名，不访问或修改 Xcode 账户：

```bash
python3 unittest/ios_build/build_framework_probe.py \
  --framework build_ios_arm64/framework/SeekDB.framework \
  --build build_ios_arm64/framework-probe \
  --profile /path/to/existing.mobileprovision --identity YOUR_SIGNING_IDENTITY
xcrun devicectl device install app --device YOUR_DEVICE_UDID \
  build_ios_arm64/framework-probe/Release-iphoneos/SeekDBFrameworkProbe.app
xcrun devicectl device process launch --device YOUR_DEVICE_UDID YOUR_PROFILE_BUNDLE_ID
```

保持 App 前台。下载 Documents/framework-probe.json，必须 complete=true、passed=true、
worker_exit=true、hook_mode=disabled，且 build_id 对应 framework 提交。停止完成后
退出并重新启动该探针进程，第二份报告 previous_runs 必须增加 1。模拟器使用相同
脚本加 --simulator，无需开发证书。两份报告可以用
`unittest/ios_build/validate_framework_probe.py --revision FULL_SHA REPORT1 REPORT2` 验证。

最终实现提交为 `6f902fdab24ccefdde97c991b50faf551928b604`。对应独立产物：

- 真机：`build_ios_arm64/framework-6f902fdab24c/SeekDB.framework`。
- 模拟器：`build_ios_sim_arm64/framework-6f902fdab24c/SeekDB.framework`。

两份 manifest 均 source_dirty=false，标记与提交匹配；精确导出 32 个符号、无
LC_RPATH、仅系统动态依赖。双平台各两次独立进程启动，均通过 77 项真实 socket
SQL/生命周期检查及 worker_exit=true；真机 previous_runs 从 3 到 4，模拟器从 2 到 3。
测试进程在线程退出后仍存活，确认停机证据后才终止探针。聚焦宿主测试共31项通过。
完整报告、二进制摘要和回归诊断见
[tracked evidence](../../../unittest/ios_build/framework_evidence/README.md)。
后续证据/文档提交不改变上述实现；交付源码身份取 manifest 的实现提交而非旧审计标记。
真机来源记录当前验证 zlib/OpenSSL，其他既有 native archive 以精确链接摘要标识；
模拟器有14项当前来源验证记录。未执行完整仓库测试或 QuickLang 集成、App Store、
后台运行验收。最初输入范围和后续授权记录见 [framework 审计](ios-framework-audit.md)。

## 2026-10-06：自动准备依赖与构建模式

```bash
./build.iphone.sh          # 默认 Debug，自动准备依赖并完成最终链接检查
./build.iphone.sh debug    # 与默认入口相同
./build.iphone.sh release  # RelWithDebInfo，保留现有优化构建配置
```

默认流程检查完整 Xcode SDK、CMake、已有 Cargo/rustup，自动安装缺少的锁定 Rust
工具链和对应 iOS target。缺少公共头文件时调用 `./build.sh init`；只有 parser tools
缺少时复用脚本原有的 bison/flex 初始化。随后下载、校验并编译缺少的 iOS 依赖，
配置引擎并构建 `seekdb_ios_link_check`。它依赖引擎静态库、iOS runtime、SQL probe
和 Rust 库，最终链接能发现仅生成静态库时遗漏的符号；不在此步骤签名或安装 App。

`--deps-only` 仍用于只构建 iOS 第三方依赖；`--init` 作为兼容参数保留，常规调用
无需再填写。Mac 依赖包中的库不能链接到 iOS，Mac 初始化与 iOS 依赖构建均已纳入
默认流程。脚本不会自动安装完整 Xcode、CMake 或初始 rustup，也不会处理 Apple
账户授权；缺少这些基础工具时给出错误。`--configure-only` 也会先准备依赖。

依赖驱动的 `--reuse` 核对 package version/source SHA256、SDK 路径、最低系统版本、
平台、arm64、构建脚本/适配器摘要及已安装库和关键头文件摘要。缺失、空文件、变更
或旧格式 `verified.json` 会触发重建；某包重建后，其后的依赖包也重建，避免使用旧
链接输入。旧版本缓存首次迁移需重新构建一次。显式 `--deps-prefix` 由调用者管理，
不会自动下载或修改，CMake 会继续校验目标平台；`--headers-prefix` 仍可单独指定。

Debug 使用 CMake Debug 和 Rust `cmake-debug` profile；第三方依赖仍为 Release。
`release` 使用 RelWithDebInfo。默认仍使用 `build_ios_arm64`（模拟器为
`build_ios_sim_arm64`）；在同一目录切换模式会重新配置并触发必要重编译，也可用
`--build-dir` 隔离。真机测试 runner 显式选择 release，保持原有测试构建模式。

验证：脚本路由 15 项、缓存失效 5 项、真机 phase 回归 45 项通过；本机完整 Xcode 下独立目录 Debug / Release CMake
配置与生成成功，Rust 输出指向 `aarch64-apple-ios/cmake-debug/libsql_nio.a`。
本次未执行完整 Debug 引擎编译、全新机器端到端构建或真机测试。

# iPhone 交叉编译（实验阶段）

## 2026-10-06：默认脚本修复与真机操作指南

直接运行 `./build.iphone.sh` 曾在编译阶段报 `grpcpp/grpcpp.h` 和 `rapidjson/error/en.h` 缺失：默认 iOS 依赖前缀尚未包含完整公共头文件。现在优先选择包含 gRPC、RapidJSON 和 Boost 头文件的 iOS 前缀，否则使用仓库已有的 `deps/3rd/usr/local/oceanbase/deps/devel` 公共头文件。显式 `--headers-prefix` 优先；缺少上述关键头文件时在 CMake 前给出具体诊断。此检查不是完整依赖验证，其他缺失仍可能由编译报告。`DEP_DIR` 始终为 iOS 库目录，公共头文件回退不会添加 macOS 库路径。

首次环境需安装完整 Xcode、CMake、rustup，完成 Xcode 许可和组件初始化。公共头文件目录不存在时，需先按仓库依赖初始化流程准备头文件，或通过 `--headers-prefix PATH` 指定完整头文件前缀；`--init` 只安装宿主 parser 工具及固定 Rust target，不准备完整公共头文件。所有下载和产物应留在仓库内。

```bash
export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
./build.iphone.sh --init --configure-only
./build.iphone.sh --deps-only --jobs 4
./build.iphone.sh --jobs 4 --target seekdb_ios_link_check
```

默认 `./build.iphone.sh` 只生成引擎静态库；安装前需构建上面的完整链接目标。安装形式是包含 seekdb 的 UIKit 测试 App，数据库运行于 App 沙箱。Xcode Settings → Accounts 登录开发账号，使用自己的 10 位 Team ID 和唯一 Bundle ID；将 iPhone 连接、信任 Mac，启用「设置 → 隐私与安全性 → 开发者模式」并按提示重启，安装和启动时保持解锁。

```bash
xcrun devicectl list devices
python3 deps/ios-build/build_app.py --team YOURTEAMID \
  --device YOUR_DEVICE_UDID --bundle-id org.seekdb.iosprobe.yourname --install
```

如提示开发者未信任，在「设置 → 通用 → VPN 与设备管理」信任相应开发者。签名过期后重新构建并安装；证书和私钥由 Apple 工具管理，不能提交仓库。

真机 SQL 测试使用本分支已存在的通用 SQL probe，不依赖其他 worktree 的测试脚本：

```bash
xcrun devicectl device process launch --device YOUR_DEVICE_UDID \
  --environment-variables '{"SEEKDB_PROBE_DATA_NAME":"ios-test-001","SEEKDB_PROBE_AUTO_STOP":"1"}' \
  org.seekdb.iosprobe.yourname
```

保持 App 前台，等待界面显示 `Stopped`。自动停止可能需要数十秒。然后下载结果：

```bash
mkdir -p build_ios_arm64/device-test
xcrun devicectl device copy from --device YOUR_DEVICE_UDID \
  --domain-type appDataContainer --domain-identifier org.seekdb.iosprobe.yourname \
  --source Documents/probe-status.json \
  --destination build_ios_arm64/device-test/probe-status.json
xcrun devicectl device copy from --device YOUR_DEVICE_UDID \
  --domain-type appDataContainer --domain-identifier org.seekdb.iosprobe.yourname \
  --source Documents/sql-probe-results.jsonl \
  --destination build_ios_arm64/device-test/sql-probe-results.jsonl
```

验收必须同时满足：本次新状态为 `Stopped/result=0`、`sql_verified=true`、`sql_result=0`，本次 JSONL 最终记录为 `complete=true/result=0`。检查状态时间、`data_name` 和本次 JSONL，避免把上一次结果当作本次成功。此套件覆盖通用表达式、DDL/DML、JSON/BLOB/数组、事务提交回滚等；不代表全量 SQL、向量或所有生命周期场景已验证。

持久化测试：正常停止后关闭旧 App 进程，再用完全相同的 `SEEKDB_PROBE_DATA_NAME` 启动；确认 `previous_runs` 连续递增，且每次 SQL 和停止均通过。同一进程不支持再次启动引擎。不要强制终止正在运行的引擎并算作干净停止，也不要每次换数据目录并算作恢复测试。测试仅使用专用数据目录和 `ios_probe` 表，不放用户业务数据。

当前 `codex/ios-layered-validation` 分支根目录已包含 `run.iphone.test.sh`，入口依赖 `unittest/ios_build/run_all_iphone_tests.py`。本次已把 master 工作区的未提交修复移植到该分支，并将当前 checkout 切换到该分支；原 worktree 保留在原提交的 detached HEAD。可在当前仓库根目录执行 `./run.iphone.test.sh --help` 查看分层测试用法，正式测试的设备、签名配置与范围见本页后续章节。旧 worktree 的构建和测试证据仍对应其原源码，不能作为本次修改的真机验收。

宿主验证命令为 `python3 -m unittest discover -s unittest/ios_build -v`，只验证脚本和链接参数，不代替真机执行。本轮最终版本 `./build.iphone.sh` 已实际构建 `oceanbase_static` 成功，退出码为 0；14 项宿主测试通过。本轮没有重新签名安装或执行真机 SQL。当前修复没有更改手机系统设置或签名配置。

## 2026-10-04 真机执行结果

已验证源码提交 `6deac1205bd0f2c1c1b7d7e5902e23c5a4bc3401`（已 rebase 到上游 `76ce86fad`），运行 ID `04e24c32-b9a4-4a6d-9083-7d27105b04be`。完整入口 `./run.iphone.test.sh --restart` 成功退出（exit=0）；设置 `SEEKDB_IPHONE_HOST_JOBS=4`，设备和签名配置只在本机进程中提供。

- 八阶段全部通过：inventory、registry-smoke、cpp-device-equivalents、rust-device-runtime、mysqltest、vector、lifecycle-memory、final-matrix。24/24 检查节点通过，failed/blocked/excluded/incomplete 均为 0。
- macOS 的 272 项 CI-selected mysqltest 按四个隔离分片执行，精确覆盖 272/272；本机 Python/native 回归 314/314。这些结果不是 272 项真机测试。
- 真机 registry 包含 14 项注册用例；向量两个进程验证真实 ANN 索引计划、持久化、事务 rollback/commit、更新删除和重复查询；生命周期验证同一进程前后台、正常停机及 SIGTERM 后新进程恢复。
- 六个阶段各完成两轮 36-step 普通 SQL，持久计数严格为 0、1，终结记录 complete=true/result=0。生命周期额外验证计数 0/1/2，中间的有意 SIGTERM 不属于正常停机证据。
- hooks-off production 的 Rust 符号隔离和普通 SQL 重启通过；final-matrix 校验前七阶段全部用例状态、证据存在性及 SHA256，包括生产符号隔离的运行身份和归档摘要。
- 已修复实际出现的 in-process 停机等待链：关闭 schema/SQL/runtime 前先取消系统包 DDL launcher 并 stop/wait loader。完整轮次中的停机门禁通过。

脱敏报告位于 `iphone_test/2026-10-04/summary.md`、summary.json、checkpoint.json 及 evidence-* 文件。重要结论保存在本页和变更记录；后续文档提交不改变以上已验证源码和产物身份。

锁屏/解锁仍需人工验收；设备内存测试实际触碰 8/32 MiB 内存页并执行 SQL/ANN，不证明 OOM、Jetsam 极限或 allocator 立即归还所有页。完整矩阵是该 runner 注册范围的验收，不代表所有仓库测试均已移植到真机。


2026-10-04：真机执行发现 Rust continuation 的进程状态依赖，以及 production 隔离目录默认 standby 导致缺失 iOS gRPC 链接。现使 continuation 在同次调用中准备 panic，并统一显式 `OB_ENABLE_STANDBY=OFF`；29 项 registry 和 44 项构建调度回归通过，修复后完整矩阵待重跑。

2026-10-03：已 rebase 到上游 `76ce86fad`；修复前新 HEAD 的 272 项 host mysqltest、305 项契约回归通过。真机 smoke 断言通过，但测试 UUID 脱敏导致主机证据校验失败，现已修复，完整矩阵待重跑。

可设置 `SEEKDB_IPHONE_HOST_JOBS=4 ./run.iphone.test.sh` 并行运行 host gate；每个 slice 使用独立数据库和端口，最终仍严格核对全部 272 项覆盖。需为同时运行的数据库实例预留内存。默认 1，支持 1 至 4。


## 单命令 stages 1–4（2026-09-25，尚未执行新一轮真机）

`./run.iphone.test.sh` 已注册 inventory、registry smoke、4 个 C++ device equivalent、5 个 Rust device case，以及独立 production Rust symbol isolation。每个 device phase 末尾都有稳定、不可选跳的普通模式 gate：在该 run 专属数据目录完成 36-step SQL 首轮和同目录 restart，并严格要求 `previous_runs=0/1`；gate 未通过时 phase/run 都不能通过。可用四个 `--suite` 只运行当前已注册阶段，runner 仍按全局顺序执行。

phase engine 可以在 checkpoint 初始化时登记全部 case metadata，但只会按 `PHASE_IDS` 到达当前阶段时产生 terminal result。若后序 adapter 尚未实现，已注册的前序阶段仍先执行；到达第一个 missing adapter 时只写一个 infrastructure failure 并停止，更后阶段保持 pending。`--resume` 会记录并跳过此前已通过的 case，然后重试这个最早的 missing gate，不会预先为所有后序缺口生成失败文件。

在创建 checkpoint 前，runner 会持有同一 start-date 目录的独占锁，完成物理设备选择、current-HEAD hook-on `seekdb_ios_link_check`、签名/验签及本次安装、archive/App/CMake identity 哈希，再把锁交给 phase engine。CoreDevice 顶层 `identifier` 是 devicectl 命令标识；描述文件的 `ProvisionedDevices` 则只与 current schema 的 `properties.hardware.udid` 或 legacy schema 的 `hardwareProperties.udid` 比较，二者不能混用。hardware UDID 缺失，或 current/legacy 同时存在但不一致时，设备不具备资格。已有合法 artifact 只能复用 engine/App bytes，不能跳过当前 selected device 的 profile scope、certificate/private-key、codesign 和安装检查；新构建 App 在写 checkpoint 前再次读取实际 embedded profile 并完成同样检查。resume 会安全复验/安装，fingerprint 以 command identifier 与 hardware UDID 的组合隐私哈希绑定设备，不能把 A 设备证据续跑到 B；两个原值都只作为进程内动态脱敏 token，checkpoint/report 不保存原值。显式 `--device`/`SEEKDB_IPHONE_DEVICE` 仍匹配顶层 command identifier。Rust production isolation 使用独立的 `build_ios_arm64_production` 类目录，不覆盖 checkpoint 绑定的 hook-on 产物。该提交只完成主机契约与准备链，**没有启动真机阶段，也没有新增 pass 结果**。

无显式 bundle/team 时，只在现有 probe App 的 Info.plist 与 embedded profile 唯一一致、profile 为未过期 iOS profile、只有一个 developer certificate 且包含设备范围时进行进程内推导；证书摘要还必须在本机 codesigning identities 中唯一匹配私钥。任何歧义或缺失都停止，并要求设置 `SEEKDB_IPHONE_BUNDLE_ID`、`SEEKDB_IPHONE_TEAM`；`SEEKDB_IPHONE_SIGNING_IDENTITY` 不是硬要求，当前 `build_app.py` 不消费它。所有推导出的 bundle、team、设备、profile/certificate token 都在首个外部命令前注册为内存脱敏 token，不写入 checkpoint、report 或 Git。

构建依赖优先使用显式环境；否则读取现有 `CMakeCache.txt`，逐项验证目录、可执行工具和关键 iphoneos 静态库，再把所有路径明确传给 build 命令，不依赖当前 worktree 缺失的默认目录，也不静默沿用缓存配置。runner 先解析 Cargo/rustup candidate 与 `CARGO_HOME`/`RUSTUP_HOME`，然后构造只含解析后 homes、工具 `bin` 优先 `PATH` 的受控 probe 环境；Cargo 与 rustup 再分别通过捕获输出的 `--version` 验证角色，不能仅凭文件可执行就互换。这样 rustup-managed cargo shim 可在其完整上下文中验证；错误 home 仍在 build 前阻断。若 cache 的 `CARGO` 实际指向 rustup，只在同一 `bin` 目录存在且验证通过的 cargo sibling 时修正，并通过 `-DCARGO=` 写回 CMake。probe stdout/stderr 不进入诊断或证据。显式 `RUST_TARGET_DIR` 可以是尚未创建或空的 fresh target；已有 simulator/x86-only target 且没有 device target 时会被拒绝，cache-backed CMake 仍必须明确为 iphoneos ARM64。输出只打印 `environment`、`cmake-cache`、`cargo-sibling` 等非敏感来源标签，不打印路径：

所有 phase subprocess（包括 reuse-build 的 codesign/devicectl install）共享 runner 创建时冻结的受控 command environment。用户已显式设置且有效的 `DEVELOPER_DIR` 原样保留；未设置时才默认 `/Applications/Xcode.app/Contents/Developer`。启动任何 phase/device command 前，runner 用该环境分别验证 `xcrun --find devicectl`、`xcrun --find lldb` 与 `xcrun --sdk iphoneos --show-sdk-path` 的实际 executable/SDK directory；因此不会在 discovery/build 使用完整 Xcode、随后 install 又回退到 CommandLineTools。显式值无效、工具或 SDK 缺失时固定归类为 `build-inputs`，不透传 probe 输出或本机路径。

当前 macOS 执行环境会对 Cargo 新生成的 host build-script 直接触发 SIGKILL。standalone runner 因此固定设置 `RUSTC_WRAPPER=unittest/ios_build/rustc_lldb_wrapper.py`，并在启动 build 前验证 `/usr/bin/xcrun` 可解析可执行 LLDB；缺失时作为固定 build-input 错误阻断。该 tracked wrapper 始终先运行真实 rustc，仅对本次新生成或被本次重写、basename 严格为 `build_script_build-<hex>`、带 macOS platform load command 的 64-bit host Mach-O executable 做替换：`-C extra-filename=-<hex>` 提供预期名称，pre/post device、inode、mtime_ns、size 与 SHA-256 共同识别当前产物。首次真实文件原子保留为 `.real`，quote-safe launcher 通过 LLDB 执行；已有 exact launcher + matching host `.real` 是可重编译的 healthy pair，wrapper 在调用 rustc 前先持久化含旧 pair identity/digest 的 unit-local prepared intent。rustc 正常刷新 launcher 路径后，再把 intent 原子推进为含 new digest 的 ready manifest，把旧 `.real` 移入 identity-scoped recoverable backup，将新 raw 转为 `.real`、安装 launcher并 fsync。wrapper 在 rustc 返回失败时恢复旧 launcher；若恢复 launcher temporary 已 fsync、但在 replace 前中断，下次仅在它仍是指向 old `.real` 的 exact regular non-symlink tracked launcher 时完成 replace/fsync/manifest cleanup，内容或类型不匹配则 fail-closed。若在 rustc 完成后或其他 rename 窗口中断，下次调用按 intent 和 old/new digest 恢复事务，旧 binary 不会丢失。prestate 已是 raw + `.real`、损坏 launcher/pair、冲突 backup 或不确定中间态仍固定失败。LLDB 在 build-script 执行前以 stop-at-entry 建立进程，再通过 `SBUnixSignals` 动态枚举信号并设置 pass/no-stop/no-notify；为避免不可传递或作业控制 stop 造成挂起，SIGSTOP、SIGTSTP、SIGTTIN、SIGTTOU 保持 LLDB 默认策略。普通 command 路径和 batch crash/stop hook 都执行同一 status mapper：仅 `eStateExited` 可按 exit description 将终止信号映射为 `128+signal` 或返回正常退出码，任何 stopped/其他终态固定返回 125，不能落回 LLDB 自身的 0。compiler signal 及 xcrun/LLDB 自身 signal 同样传播为 shell status。本机真实 Mach-O/LLDB contract 覆盖普通退出、SIGTERM、SIGKILL、SIGTRAP、SIGQUIT，以及固定失败的 SIGTSTP、SIGTTIN、SIGTTOU；每次调用有超时边界。已有未变化文件、非 Mach-O、非 macOS、非严格名称不会替换；symlink、相对/dot-segment 路径、多个变化候选或 stale `.real` 会安全失败。它现在是 one-command 构建链的受跟踪组成，不再依赖 `build_ios_arm64/` 下的 ignored 本机脚本。

真实 macOS 27 ARM64 host release 构建进一步发现：Rust build-script 已经由上述
wrapper 正常执行后，`seekdb-jemalloc-sys` 的 native configure 仍会在运行 conftest
时被系统以 SIGKILL 9 终止。`deps/external/cmake/Jemalloc.cmake` 现在只对
`OB_MACOS27` 的单一 ARM64 host 生成
`JEMALLOC_SYS_CONFIGURE_ARGS=...\n--host=aarch64-apple-darwin`，让 vendored
autoconf 在 initial runtime probe 失败后切到 cross mode，并跳过后续 runtime
probes；它不是完全不生成或不运行 initial probe。普通 macOS、macOS 27 x86_64、iOS 的
`aarch64-apple-ios` 配置和 Android 均不继承该参数。focused contract 通过真实
CMake custom command 与 recording Cargo 验证最终环境；本次没有重启或修改正在
生成的 `build_release`，完整 host release 重试结果仍需另行记录。

为处理 wrapper 纳入前遗留、Cargo 会以 Fresh 从 rustc hashed output 恢复 final launcher 而不重新调用 rustc 的 raw cache，runner 在同一个 preparation/run lock 内、checkpoint 与 build 前按完整 build unit 迁移。它只检查已验证 `RUST_TARGET_DIR` 直接 profile 下的严格 `<profile>/build/<crate-hash>/build-script-build`；若 unit 不是完整 tracked launcher 状态，则同时隔离 final、同 unit hash 的 `build_script_build-<hash>`、匹配的 stale `.real` 和 `<profile>/.fingerprint/<crate-hash>`。每个事务先持久化只含 target-relative path/type 的 manifest，再把条目通过同文件系统 `os.replace` 移入 target 内按 run ID 哈希隔离的 `.seekdb-ios-runner-quarantine/.../payload/`，并 fsync manifest、源和目标目录；进程在部分 move 后中断时，下次准备必须先完成该 manifest，both/neither、类型变化或碰撞均 fail-closed。完整状态必须由 final launcher 和 matching hashed launcher 同时、精确指向当前 crate unit hash 对应的 `build_script_build-<hash>.real`，且该 `.real` 是 regular、non-symlink host Mach-O；两个 launcher 即使共同指向另一 unit 的 `.real` 也会触发整 unit 迁移。完整 pair、target device binaries、源码、未知路径和未知文件不移动；任何将被扫描的 direct profile symlink、非规范 Cargo 目录、损坏 pair 或 quarantine collision 会在 build 前阻断。重复执行幂等，终端仅记录固定非敏感 `legacy_build_script_cache=migrated-N`，不输出缓存路径或文件名。本机 Cargo 1.98 最小 build.rs 集成回归确认 fingerprint 失效后实际再次调用 rustc wrapper，final/hashed 输出均成为 tracked launcher 并指向 matching `.real`。

```bash
export SEEKDB_IPHONE_DEPS_PREFIX="<iphoneos dependency prefix>"
export SEEKDB_IPHONE_HEADERS_PREFIX="<iphoneos header prefix>"
export CARGO="<cargo executable>"
export RUSTUP="<rustup executable>"
export CARGO_HOME="<cargo home>"
export RUSTUP_HOME="<rustup home>"
export RUST_TARGET_DIR="<repository-local Rust target directory>"

./run.iphone.test.sh \
  --suite inventory \
  --suite registry-smoke \
  --suite cpp-device-equivalents \
  --suite rust-device-runtime
```

试运行前仍必须确认：恰好一个 booted/paired/visible physical iPhone（或显式 `SEEKDB_IPHONE_DEVICE`）；profile 覆盖该设备；对应 certificate/private key 可用；依赖为 iphoneos ARM64；当前 checkout 干净；磁盘空间满足构建阈值。若没有可唯一复用的本机 profile，则显式提供 bundle/team 仍要求 Xcode 能在本机完成 provisioning；本轮未用真机构建验证该路径，因此不能仅凭主机测试声称 one-command 真机 ready。

`--suite inventory` 是纯 host-only 路径，不发现设备，也不要求 bundle/team、build、签名或安装。device runner 的 launch、evidence 和 clean-stop 共用一个绝对 deadline，外层 subprocess timeout 另留 120 秒收尾余量，不再把两个独立完整 timeout 串接到较短的外层限制。中断后 resume 会在 checkpoint 记录已通过 case 的 `resume_skip_count` 与 `last_resume_skipped_at`；SQL gate 若已保存首轮完整证据，只续跑 restart 轮。每轮 launch 前先原子、耐久地写入包含唯一 round ID 的 intent，再把 intent 切换为 `launch-uncertain` 后才允许调用设备；完成后 JSONL 的相邻 metadata 严格绑定 runner run ID、round ID、source build ID、selected-device SHA-256、data directory、hook mode、`previous_runs` 和 JSONL digest。若在设备完成后、metadata 写入前中断，resume 必须用原 round ID 从设备重新复制并验证终态后补 metadata，绝不盲目重跑 first；无法确认时安全失败。文件名包含由 runner run ID 派生的 scope，因此同 run resume 可恢复，same-day `--restart` 的新 run 绝不接受旧 gate 文件或 intent。

devicectl launch 的 stdout/stderr 只在当前进程内分类为 locked、disconnected、not-installed、trust、developer-mode 或 other，并通过结构化 `LaunchFailureCategory` 交给 caller，不写入 evidence/checkpoint。Locked 会向当前调用终端打印固定提示 `Unlock the iPhone and keep the screen awake; retrying…`，并在同一 shared deadline 内每 5 秒重试；即使非交互运行也会在 deadline 到期后固定失败。phase executor 使用独立进程组、binary pipes 和单线程 `selectors` 非阻塞复用 stdout/stderr，避免大量双流输出互相阻塞；只有 stderr 中以换行结束、完整且 UTF-8 bytes 逐字等于该 allowlist 提示的一行可在子进程结束前实时回显，而且每个命令最多一次。近似行、partial line、非法 UTF-8、任意 token 或其他原始输出都不回显；最终 bytes 以 replacement decoding 转为文本，再统一脱敏、限长。leader wait 和两个 pipe drain 共用同一个 monotonic deadline；即使 leader 已返回 0，只要继承 pipe 的后代在 deadline 仍未关闭输出，也会作为 timeout 返回 124，并以有界 TERM/KILL、drain 和 reap 清理整个进程组。selector 先 unregister，再由唯一 owner 正常关闭 stream，不跨线程 raw close fd；因此后续 checkpoint 即使复用 descriptor 也不会被旧 wrapper 析构误关。runner 中断使用同一有界清理协议。SQL round 在 launch 前仍先写 `launch-uncertain`；只有所有尝试都被 CoreDevice 明确以 locked 拒绝、deadline 耗尽时，caller 才把同一 round ID 的 intent 耐久回写为 `prepared`，解锁后的 resume 可以安全重新 launch。unknown/other 保持 uncertain 以维持 at-most-once；若回写 prepared 前崩溃或 fsync 失败，磁盘也仍是 uncertain。其他类别不重试，只输出对应固定安全诊断。phase adapter 只接受这些 allowlist 行，二次脱敏并限长后写入 failure/checkpoint；相邻 raw metadata、argv 和 token 不持久化。launch 类失败按 infrastructure 停止后序 case，避免同一设备状态重复污染所有 case；production isolation 非零退出固定记录 `production isolation build failed`，不复制 build log。

checkpoint 前的 setup 边界只接受固定错误码，并把它们映射为 build、build-input、signing、signature、install、profile 或 App-output 阶段诊断。任意未知返回、异常类型、异常文本及捕获的 stdout/stderr 一律折叠为 generic setup failure；阶段诊断不拼接外部命令输出或本机 token。

最新状态（2026-09-24）：原生 seekdb 引擎已在 iPhone 17 Pro / iOS 27.0 完成 36 步通用 SQL 套件和五轮干净停止。五轮均为 `Stopped/result=0`、`sql_verified=true`，同一数据目录的 `previous_runs` 依次为 0、1、2、3、4；验证后没有新增崩溃报告。日志确认 1 GiB 逻辑预算。测试 App 运行期间保持亮屏，进入 Stopped / Failed 后恢复自动锁屏，不修改系统设置。

分层测试方案已开始实施。Phase 1 已完成物理真机验收：确定性初始化失败在 iPhone 上保留主错误 `-4016`，同时完成 server、curl 和工作目录三项清理，`cleanup_error=0`、`cleanup_status=7`，并确认运行 ID、源码 build ID 和 hook mode 与本次 App 一致。随后关闭 `SEEKDB_IOS_TEST_HOOKS` 全量重建，普通 App 在同一数据目录连续完成两轮 36 步通用 SQL、干净停止和持久化恢复，`previous_runs` 为 0、1；三轮执行均未产生新的 seekdb 崩溃或 Jetsam 报告。英文设计见 [iOS Layered Test Strategy Design](../../ios-layered-test-strategy-design.md)，清单格式见 [iOS Test Inventory Schema](../../ios-test-inventory-schema.md)。

测试 App 现提供显式 device registry 模式。主机启动时同时设置 `SEEKDB_IOS_TEST_SUITE`、`SEEKDB_IOS_TEST_FILTER` 和唯一 `SEEKDB_IOS_TEST_RUN_ID`；App 在引擎 Running 后执行匹配 case，并把 `run_start`、`case_start`、逐项 assertion、`case_end`、`run_complete` 依次追加到 run-scoped JSONL，每条写入后立即 flush。每个 case 的 `timeout_seconds` 是实际 deadline，而不只是元数据：由于任意 C++ callback 无法安全强制取消，watchdog 到期后串行写入失败 assertion、`case_end/result=124` 和 `run_complete/result=124`，flush 后以 124 终止 App 进程，禁止阻塞 callback 随后产生通过结论。host 因而能在 case deadline 加轮询开销内取得有界失败；超时进程不声称完成正常 engine cleanup，后续启动必须使用独立测试目录或走数据库恢复语义。

`run_device_suite.py` 只复制 allowlist JSONL 和固定生命周期状态，并校验 run/build identity、suite/filter、独立预期 case 覆盖、重复或缺失完成事件、device origin、非零结果及最终干净停止。只要流中尚无 `run_complete`，即使已 flush 的前缀包含失败 assertion，也继续轮询；最后一条未写完的 JSON 行同样按未完成处理。`run_complete` 一旦出现，完整语义校验立即运行，终态失败保留原始 `ValueError`，不会被改写为超时。JSON writer 保留合法多字节 UTF-8，按 JSON 规则转义引号和控制字符，并把每个非法输入字节确定性编码为 U+FFFD，因此任意诊断字节都不会破坏 JSONL。原始 `devicectl` 元数据不写入仓库证据。runner 显式选择最多 64 字符的独立数据目录，避免继承默认旧目录。没有 suite 环境变量的普通启动仍执行原 36 步 SQL。

2026-09-24 真机验收已完成：`ios.registry.smoke` 在独立数据目录产生完整的 5 条事件序列，依次为 `run_start`、`case_start`、1 条 assertion、`case_end`、`run_complete`，case/result 均为 0；随后 App 达到 `Stopped/result=0`、`suite_result=0`、`cleanup_status=7`、`cleanup_error=0` 且工作目录恢复。最终复验使用同一个已签名的 follow-up HEAD，严格按 registry smoke、普通模式首轮、同一全新数据目录普通模式重启轮的顺序执行。两轮普通模式各完成 36 步 SQL 后干净停止，持久计数为 0、1；两轮 JSONL 均为 36 个成功 step 加最终 `complete/result=0`。主机 45 项 iOS Python 契约、iphoneos compile-only、未签名和签名 App 完整链接、codesign、安装均通过；完整顺序前后的相关 crash/Jetsam 增量为空。脱敏 JSONL、生命周期状态和运行摘要只保存在忽略目录 `build_ios_arm64/device-evidence/task2-final-head/`。自动签名因 Xcode 当前没有登录账号而不可用，本次仅复用本机已存在并经 bundle、设备范围、有效期、证书私钥校验的开发描述文件手工签名；账号、Team ID、证书和设备唯一标识均未写入仓库证据。

同日质量修复复验增加阻塞 callback、终态错误分类和非法 UTF-8 的可执行契约，focused 14 项及完整 host 49 项通过。修复版重新完成 iphoneos runtime/App 链接、既有本机描述文件手工签名校验和安装，并按 smoke、全新目录普通首轮、同目录 restart 的顺序执行；结果仍为 smoke 5 个事件、两轮各 36 个成功 SQL step 加完成记录、`previous_runs=0/1`，相关 crash/Jetsam 增量为 0。脱敏证据保存在忽略目录 `build_ios_arm64/device-evidence/task2-quality/`。

后续 host-only 复核补充“失败 assertion 前缀后继续出现终态失败”的轮询契约，focused 15 项及完整 host 50 项通过；该修改不改变 App/native/CMake，未把上一 App 产物重新标记为新提交的设备证据。上述真机结果仍严格绑定 `a11ef0d6208f`；runner 的 build identity 校验保持不变，后续若要对更新后的 HEAD 再取真机证据，必须先重建并安装同一 HEAD 的 App。

Rust device suite 使用 `ios-device-tests` Cargo feature 和 `ios-device-test` profile。该 profile 仅供签名测试 App 使用，继承 release 优化但把 panic 设为 unwind，使最外层 C ABI 能把 panic 转成有界失败结果；production release 与 CMake debug 继续 `panic="abort"`。三项原 cert/TLS 测试现在由 host `#[test]` 和设备 ABI 共同调用同一组 `Result<(), String>` case，避免两套断言漂移。C ABI 固定提供 count、case info 和 run，并拒绝非法 index、空/过小 output；ID 与诊断使用固定容量，panic 在 `catch_unwind` 外边界内转换。设备 registry 另有 intentional-panic 和紧随其后的 continuation case，后者证明同一进程能在捕获 panic 后继续执行。

`SEEKDB_IOS_TEST_HOOKS=ON` 时 CMake 只选择 `ios-device-test/libsql_nio.a` 并传入 feature；关闭时只选择 production archive。`build_app.py` 从最终 link response 中要求恰好一份 `libsql_nio.a`，再按 archive 的精确 symbol 字符串验证 test/production mode，禁止把两份 Rust archive 并链。测试 App 仅在 test mode 编译 `rust_device_tests.cpp`。主机门禁已通过三项 unit test、doc-test、Clippy `-D warnings`、fmt、focused 6 项和完整 iOS Python 64 项；feature archive 含三项 `nio_device_test_*` symbol，production release archive不含。

最终 source build ID 已重新完成 test-hook iphoneos runtime/App 链接、自动签名、安装及 `rust` suite。三个共享真实 case、intentional panic 和紧随其后的 continuation 共五项均为 `result=0`，run 终态为 0；这证明 panic 没有越过 C ABI，且同一进程继续执行。随后在同一 SHA 关闭 hooks 重建 production archive/App，确认测试 symbol 不存在且链接闭包仍只有一份 Rust archive；再用全新数据目录执行普通 36-step SQL 首轮和同目录 restart 轮，两轮均为 36 个成功 step 加成功 complete，`previous_runs=0/1`，相关 crash/Jetsam 增量为 0。脱敏证据保存在忽略目录 `build_ios_arm64/device-evidence/task4-rust/final/`。签名、设备和账号标识不写入证据。若容器累积导致 stall，只能在保存已有证据后卸载专用、可丢弃的测试 App，并记录该边界；不得删除其他 App 或用户数据。

签名安装 test-hook App 后，Rust suite 的五个独立 case 必须全部列为 host 预期覆盖：

```bash
python3 unittest/ios_build/run_device_suite.py \
  --device "$DEVICE_ID" --bundle-id "$BUNDLE_ID" \
  --suite rust --filter 'ios.rust.*' --data-name ios-rust-tests \
  --expected-case ios.rust.cert.formats_display_name_for_sql_account \
  --expected-case ios.rust.cert.rejects_truncated_certificate \
  --expected-case ios.rust.device.intentional_panic \
  --expected-case ios.rust.device.panic_continuation \
  --expected-case ios.rust.tls.exposes_sql_cipher_names
```

上述顺序是 registry 的字典序。suite 完成后必须先保存 JSONL/status，再重建 hook-off production archive 并证明 test symbol 缺失，最后才在同一 native SHA 的全新目录执行 36-step SQL 首轮和同目录 restart。若累积 container 再触发已知停机 stall，只能在已保存证据且确认是专用可丢弃测试 App 后卸载；卸载行为及数据删除边界必须记录，不得删除用户 App 数据。

C++ 分层覆盖已接入 App：当前源码 revision 没有普通 C++ unittest target，inventory 中的 9 个孤立 GTest 已逐项解析为 3 个 device equivalent、6 个 exact exclusion、0 个 blocked。设备 `cpp` suite 包含 `ios.cpp.allocator.backend`、`ios.cpp.allocator.lifecycle`、`ios.cpp.allocator.realloc_alignment` 和 `ios.cpp.ob_error.mapping`；它们覆盖 iOS 上真实存在的 backend parse/detect-once、普通 allocate/reallocate/usable-size/free、alignment，以及生产错误名、精确 `ER_WRONG_ARGUMENTS` 和 SQLSTATE 映射。Linux malloc hook cross-API 及 libc malloc/realloc/memalign malloc-zone hook 不可用显式 `ob_`/jemalloc API 伪装，fork child、未链接的 `ob_error` CLI manager/getopt parser 也保留精确排除；`test_adder` 源码本身在 Apple 平台通过 `GTEST_SKIP` 排除，且其 `ObErrorInfoMgr` 生成器逻辑未链接进 App，因此生产 error metadata case 只作独立设备覆盖，不伪装为 `test_adder` 等价项。四个设备 ID 和三个映射后的 required GTest 均在 inventory 记录 `latest_result=pass` 与脱敏摘要路径。

签名安装后可用同一 runner 选择全部 C++ case；四个 `--expected-case` 必须独立提供，host 才会接受完整覆盖：

```bash
python3 unittest/ios_build/run_device_suite.py \
  --device "$DEVICE_ID" --bundle-id "$BUNDLE_ID" \
  --suite cpp --filter 'ios.cpp.*' --data-name ios-cpp-tests \
  --expected-case ios.cpp.allocator.backend \
  --expected-case ios.cpp.allocator.lifecycle \
  --expected-case ios.cpp.allocator.realloc_alignment \
  --expected-case ios.cpp.ob_error.mapping
```

该命令中的环境变量只作本机参数示例；设备、Team、证书、profile 和账号标识不得写入仓库证据。本节最终提交已用同一 native source SHA 完成 iphoneos runtime/App 链接、现有本机描述文件签名校验与安装；真机 C++ JSONL 的 4 个 case 均有 assertion 且 `result=0`、`run_complete/result=0`。随后在同一 SHA 下以全新数据目录执行普通模式 36-step SQL 首轮及同目录 restart 轮，两轮均为 36 个成功 step 加 1 个成功 complete，`previous_runs=0/1`，清理状态完整，整个序列的 probe 相关 crash/Jetsam 增量为 0。脱敏证据保存在忽略目录 `build_ios_arm64/device-evidence/task3-cpp/final/`。

上述最终序列严格只证明 **clean App container** 下的 C++ suite，以及随后同一新目录中 SQL `previous_runs` 从 0 到 1 的重启恢复。最终序列前，两次 C++ suite 都已产生成功 assertion 和 `suite_result=0`，但引擎持续停在 `Stopping`，`result`/cleanup 字段一直为 null；180 秒和 300 秒的 host 终态等待先后超时。当时专用测试 App 的 `Documents` 顶层已累积 43 项多轮测试数据；日志在 stop request 后未出现 `ObServer::set_stop()` 中首个 `sql_nio_stop()` 之后的停机记录。单纯 `--terminate-existing` 后换新目录重试仍可复现；卸载并重装专用测试 App、清空整个 container 后，同一 native 二进制才完成干净停机。这只说明 stall 与累积 container 状态相关；精确的 `sql_nio_stop()` 阻塞根因、触发阈值及非清空恢复方案仍未解决、未验证，不得将本次 clean-container 通过外推为累积多轮数据下的停机可靠性。

仅对可丢弃的专用测试 App，且已将需要的 JSON/JSONL 证据复制到 host 后，可用下列占位参数安全重置。**uninstall 会删除该 App 的整个 data container，包括全部数据库目录和尚未复制的证据**；不得对含需保留数据的 App 执行。

```bash
DEVICE_ID="<connected-test-device-id>"
BUNDLE_ID="<dedicated-test-app-bundle-id>"
APP_PATH="build_ios_arm64/app/Release-iphoneos/SeekDBProbe.app"

xcrun devicectl device uninstall app --device "$DEVICE_ID" "$BUNDLE_ID"
xcrun devicectl device install app --device "$DEVICE_ID" "$APP_PATH"
```

本文档边界说明是 native 验收后的 docs-only follow-up；上述真机证据继续绑定 native source SHA `669875476d83857ec4382e57834b5fef1cdcb3d3`，不把后续文档提交标记为新的设备二进制。

`build.iphone.sh` 为 iPhone ARM64 和 Apple Silicon iOS 模拟器配置 CMake、Rust 和 Apple SDK。默认目标是 `oceanbase_static`。目前不是已完成的 iOS 产品构建流程；脚本不生成、签名或安装 App。

完整环境设置、逐文件修改原因和失败尝试见 [iOS 移植变更记录](ios-change-log.md)。后续环境、源码和 CMake 变更必须同步追加该记录，并区分已验证与待验证。

## 使用

### Standalone mysqltest 阶段

`./run.iphone.test.sh --suite mysqltest` 现在注册三个有序边界：独立 host mysqltest gate、每个已审查 lossless source 的设备 case，以及不可省略的通用 SQL/同目录 restart gate。入口不接受预先准备的 JSON 作为执行证明；同一次受锁 runner 流程会调用受跟踪的 host runner，先执行精确 272 个 CI-selected case，再 merge 并独立复核本机三项 executable bytes。

每项 host executable 都先读取对应的显式环境变量；未设置的项只回退到当前
repository root 内以下固定路径，不扫描其他 checkout，也不搜索 `PATH`：

```text
build_release/src/observer/seekdb
deps/3rd/u01/obclient/bin/obclient
deps/3rd/u01/obclient/bin/mysqltest
```

当前机器的三个 canonical 产物均已存在，因此常规入口不再需要重复填写三项 host
环境变量：

```bash
./run.iphone.test.sh
# 只选择本阶段时：
./run.iphone.test.sh --suite mysqltest
```

如需覆盖其中一项或多项，仍可显式提供：

```bash
SEEKDB_IPHONE_HOST_SEEKDB=/absolute/path/to/seekdb \
SEEKDB_IPHONE_HOST_OBCLIENT=/absolute/path/to/obclient \
SEEKDB_IPHONE_HOST_MYSQLTEST=/absolute/path/to/mysqltest \
  ./run.iphone.test.sh --suite mysqltest
```

显式变量一旦出现（包括空字符串或仅空白字符）就必须给出可执行的 regular
non-symlink file；无效显式值不会静默回退。canonical 路径从当前 repository root
的真实目录 fd 开始逐段以 `openat`/`O_NOFOLLOW` 验证，`build_release`、`deps/3rd`
或任一父组件是 symlink、特殊节点或发生逃逸时均拒绝；binary identity 也通过
anchored parent fd 对稳定 regular file bytes 取 hash，避免重新按可替换父路径读取。
canonical 产物缺失或无效会在设备发现、build、签名和安装之前 preflight 失败。
首次准备三个产物的受支持命令为：

```bash
./build.sh release --init --make
```

依赖已初始化后的增量命令为 `./build.sh release --make`。`--init` 可能联网下载固定
依赖并写入仓库构建目录，不由 standalone runner 隐式执行。

host gate 在同一个 run lock 内把三项已验证 source executable 复制到
`mysqltest-host/binaries/` 的 run-local snapshot。复制使用随机 `O_EXCL` 临时文件、
`fsync`、`renameat` 与目录 `fsync`；文件和 snapshot 目录随后设为 owner-only
read/execute。tracked mysqltest runner、`sdb.py` 及其启动的 seekdb/obclient/mysqltest
只接收 snapshot 绝对路径，不再执行原始 build/dependency 路径。每个执行边界和
outer 返回前都会从 nofollow parent fd 重算 source 与 snapshot identity、核对权限和
bytes；同 owner 可重新 `chmod` 并不被当作安全边界，检测依赖 run lock、anchored fd
及前后 identity 一致性。checkpoint/case evidence 同时保存 source 与 snapshot 的
digest/size，二者必须相等；passed resume 只重载同一 run snapshot，pending/failed
在尚未物化时创建或严格复用它。

该 snapshot 部署只支持不依赖 checkout-local 相对动态库的 Mach-O。当前三个 canonical
产物经 `otool -L` 只包含 `/usr/lib/` 和 `/System/Library/` 依赖；runner 对 Mach-O
snapshot 重做同一 allowlist preflight，`@rpath`、`@loader_path`、`@executable_path`
或其他非系统依赖均 fail closed。此规则保证移动后的 executable 不会悄悄回到原始
checkout 加载运行时依赖。

三项 source/snapshot 必须全部是 thin 64-bit Mach-O；shebang script、ELF、随机 bytes、
32-bit Mach-O 与未解析的 fat Mach-O 均在执行前 fail closed。安全边界针对路径注入、
symlink/特殊节点、stale artifact，以及 accidental 或不协作的并发修改；它依靠同一
run lock、descriptor-anchored I/O 和执行前后 identity 比对。不声称抵抗同 UID 的主动
攻击者在两次校验间替换后恢复文件：同 UID 也可修改 runner、使用调试/ptrace 能力或
清除文件 flags，而 macOS/Python 没有可用的 `fexecve` 将已验证 fd 直接交给进程执行。
本机调研确认 Python `os` 没有 `fexecve`；对 `O_EXEC` fd 尝试
`execve("/dev/fd/<fd>")` 也以 `EBADF` 拒绝。抵抗该级别攻击需要 privileged isolation
或不同 UID 的可信 launcher，超出 standalone runner 范围。

2026-09-25 的首次真实 host gate 在约 3.7 秒内失败：原始与 snapshot 的 seekdb、
obclient、mysqltest 直接执行 `--help`/`--version` 均以 137（SIGKILL）退出，slice
evidence 记录 `wait for seekdb exited with 1`，console 只有启动前言。tracked
`macos_lldb_launcher.py` 现在为该 macOS 27 execution-policy 路径提供通用 launcher；
较低版本 macOS 只在结构化 direct probe 明确返回 SIGKILL/137 时启用，Linux 与正常
macOS 继续直跑。launcher 再次校验绝对 anchored snapshot、thin 64-bit Mach-O、系统
dylib 和固定 `xcrun lldb`，以 argv list 传参，不经 shell；共享 timeout/中断会清理
完整 LLDB process group，目标正常退出与 signal 映射保持准确。LLDB 自身 stdout/stderr
丢弃，目标 stdout/stderr 通过独立继承 fd 转发，避免 debugger 文本污染 mysqltest
结果判断；LLDB 自身 stdin 固定为 `/dev/null`，调用进程的 stdin 则复制为独立 fd 并仅
通过 `process launch -i` 交给目标，保留 PIPE、文件、TTY、`DEVNULL` 和 EOF 语义。
launcher 顶层接管 SIGTERM、SIGINT、SIGHUP；spawn 临界区先屏蔽信号，记录 LLDB
process group 后再恢复信号，首次信号触发有界 TERM→KILL→wait，重复信号不重入清理，
最后以 `128+signal` 退出并恢复原 handler。默认 23 小时 timeout 小于 24 小时 host
deadline；单个 mysqltest case 显式使用 3570 秒，小于外层 3600 秒；wait-ready 的每次
launcher timeout 也为外层 client attempt 预留清理预算，避免外层先杀 launcher。
首次解除 mask 本身也位于 signal 捕获范围；cleanup 先确认真实 LLDB session leader，
冻结其 spawn，先只终止精确 target/target PGID 并保留 LLDB/debugserver 完成 wait/reap；
target PID（包括 zombie）从 `ps` 消失后才清 debugger descendants 与 LLDB group。
handler teardown 全程 block TERM/INT/HUP，恢复全部 handlers/pending state 后才恢复原 mask。

`sdb.py --launcher` 除结构化启动前缀外，还在 instance 的 `run` 目录中原子记录 detached
launcher identity；marker 通过 descriptor-anchored、`O_NOFOLLOW`、regular-file、大小
上限和 no-replace 写入约束，绑定 PID、process start identity、稳定完整 argv、launcher、
真实 seekdb snapshot 与 `--base-dir`。因此 seekdb 尚未写出 managed PID 的启动窗口也能
由 `stop`/`destroy` 精确向 launcher 发 TERM，触发其 LLDB/target 分层清理；managed PID 与
launcher marker 同时存在时两条生命周期都必须完成。PID 复用、marker symlink 或 identity
不匹配均 fail-closed，不发送信号也不删除 instance；marker 落盘失败则立即终止本次新
launcher 并等待其全链退出。wait-ready obclient、init SQL obclient 和
每个 mysqltest case 同样使用 launcher prefix。focused 只读验证已确认当前三个
run-local snapshot 的 `--help`/`--version` 经 launcher 均不再返回 137；真实 tiny
Mach-O 的 exit 7、SIGTERM 143、SIGKILL 137 也按契约传播。该验证没有启动 272 case
或真机阶段。

macOS launcher identity 的 argv 来自 `KERN_PROCARGS2`，按 native `argc`、exec path 与
NUL-delimited argv 有界解析，不再用会破坏引号、空格及空参数的 `ps` 文本与 shell parser；
权限错误、PID 消失竞态、截断或非法 payload 均 fail-closed。start 的 detached spawn、
identity 读取与 marker durable write 还是一个 signal-safe ownership transaction：父进程
在 `Popen` 返回/赋值临界区阻塞 TERM/INT/HUP，子进程 exec 前恢复原 mask；marker 持久化
前后的 signal 或任意 `BaseException` 都按当前内存/marker ownership 精确清理 launcher
全链，最后恢复 handler/mask，并将外部 TERM/INT/HUP 映射为 `128+signal`。
任何新 spawn 前都会 anchored preflight 现有 launcher marker：只有 marker 缺失或一个已
严格验证且进程确已退出的 stale marker 才能继续；live owner 视为 duplicate，malformed
JSON、symlink、special file、超限或 foreign marker 都原样保留并 fail-closed。spawn 后若
marker 写入与外部文件竞态，rollback 也固定先凭本次 `Popen` ownership 回收 launcher
全链，再尝试解析 durable marker；marker 解析失败不能跳过进程清理，未知 marker 不删除。
每个 canonical base-dir 还在其父目录使用基于绝对路径哈希的永久 lifecycle lock file；
lock 以 parent dir-fd、`O_CREAT|O_NOFOLLOW` 打开，严格校验 regular、当前 owner、0600 及
name/fd inode 一致后获取 `flock(LOCK_EX)`。因此 base-dir 被 destroy/recreate 后仍复用同一
lock inode。start 从首次 base/marker preflight 到 spawn+durable commit，stop/destroy 从
状态读取到 terminate、marker 删除和 rmtree 完成均持有该锁；destroy 直接调用 locked
stop helper，避免嵌套 flock。symlink、FIFO、权限篡改等 lock object 原样保留并 fail-closed，
所有异常与 `BaseException` 路径都关闭 fd 释放锁。
在计算 lock key 及任何 lifecycle mutation 前，runner 还会通过 canonical parent dir-fd
核对 final component：现存 entry 必须是 exact stored basename、同一 dev/ino 的真实目录，
final symlink、case-insensitive alias 与 NFC/NFD Unicode alias 均拒绝。尚不存在的名称以
parent dev/ino 加 `NFD+casefold` comparison key 共享锁，因此潜在 APFS alias 并发创建也
会串行；取得锁后再次复核 exact entry/dev/ino，stop 不会经 alias 删除真实 PID/marker。

LLDB launcher 模式下，tracked mysqltest runner 会额外向 `sdb start` 传入
`--nodaemon`。seekdb 必须在整个服务生命周期内保持为 LLDB 的原始 target；若沿用默认
daemon 模式，父 target 在 fork 后退出，实际服务不再受 launcher 托管，`wait-ready` 会在
任何 mysqltest case 执行前失败。该选项只作用于选中 launcher 的 macOS execution-policy
兼容路径，不改变 Linux 或可直接执行 Mach-O 的普通 macOS daemon 行为。真实单变量验证
已覆盖 start、SQL readiness 与 destroy 全链。

修复后的首轮 host gate 已完整执行 272 项，其中 269 项通过。两个日期 case 的
`real_sleep 1` 在当前 macOS ARM mysqltest 中只能保证到达下一整数秒边界，无法稳定让
两次 `DATETIME(0)` 默认值跨秒；两次 `REPLACE` 之间现使用 2 秒等待，result 不变。
稀疏向量 case 的三处差异来自 macOS VSAG 的累计权重质量剪枝语义；Linux ARM64 的锁定
VSAG 制品仍按元素数量剪枝，因此共享 result 保持 Linux 基线；host runner 仅在 Darwin
存在同名 `.darwin-patch.result` 时，以唯一旧片段校验并物化该平台 golden。macOS 输出已
连续独立复现，三个原失败 case 已逐项通过；在新 corpus digest 上的完整 272 项尚待
下一轮 standalone run 重新确认。

新 corpus 的 standalone run 随后实际完成 272/272，case evidence 为 `success=true`、
`failed_cases=[]`。首次收尾校验仍误报失败：classification 使用按名称排序，而 host runner
按 `mysqltest_config.yaml` 的配置顺序执行，二者集合完全一致但顺序不同。validator 现在
先要求两套选择的长度和集合严格一致，再以 runner 的真实顺序核验 evidence；缺失、额外或
乱序 case 仍会失败。

这是真实完整 host mysqltest，不是静态检查，耗时取决于 272 个 case 和重试；任一 host evidence/binary/corpus 问题都会在 iOS build、签名、安装或设备发现前停止。

静态 parser 审计全部 283 个 active source，并递归覆盖 `.inc`/`.sql` 输入；循环、path escape、缺失 include 或 connection/process/topology/result rewrite/error-policy 等语义均保持 host-only/not-applicable，不能静默删除。当前设备 registry 只包含 `ios.mysqltest.empty_table`：它消费 tracked `empty_table.result`，在内部 SQL proxy 上断言 statement status、affected rows、精确计数值，并从 result-set field metadata 精确断言有序字段名 `nr,b,str` 后要求 `OB_ITER_END`。其他 parser 候选在没有完整 transcript、affected-row、warning/error-domain adapter 前均保持 host-only。

此阶段的 host contracts 已通过，但尚未执行本轮真机 build/launch；不要把注册状态写成设备 pass。真机执行仍要求完整 Xcode、唯一物理设备、匹配的 profile/private key、当前 HEAD test-hook App build/sign/install，以及上述三个可执行的本机 host binary。

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

mysqltest standalone 阶段的 host gate 现在只接受受跟踪 host runner 生成并
封印的 merged evidence。证据绑定当前 commit、完整 `.test/.inc/.sql/.result`
语料、host runner/config/parser、三项本地 host binary identity 和精确有序的 272
项 CI case 列表；缺失、损坏、陈旧、增删 case 或 binary identity 不一致均按
evidence/infrastructure 失败处理，并阻止后续构建、安装和真机动作。host evidence
只能由同一次受锁 runner 调用 tracked runner 生成，不能通过 argv 或环境导入外部
JSON；slice/result 输入必须是有大小上限的 regular non-symlink 文件。
host runner 的 work、tmp、log、failure 和 result 写入均从逐段
`O_NOFOLLOW` 打开的目录描述符出发；输出使用随机 `O_EXCL` 临时文件、文件与目录
`fsync` 及同目录 `renameat`，预置 symlink、FIFO 或其他非 regular 目标会在写入前
失败。workspace 不再预建 sdb 无法识别的空 instance 目录；host runner 正常、失败、
超时或被中断后，外层都会以独立有界进程组调用 tracked `sdb.py destroy`，只清理
本 run 的 marker/binary identity 匹配实例。该步骤用于回收 sdb 另开 session 的
seekdb，不能由仅杀死 host runner process group 替代。

- mysqltest 的 `empty_table` 真机适配器仅在 server modules ready 后执行，空结果
  还会独立读取并精确断言有序字段名 `nr,b,str` 和终止状态 `OB_ITER_END`；模块
  未初始化或零执行不能报告通过。

- 全新目录的完整依赖流水线及 Rust 宿主 build-script SIGKILL 问题；增量完整链接已通过。磁盘空间约 9.4 GiB，继续构建时仍需关注剩余空间。
- 已新增 `seekdb_ios_run`、`seekdb_ios_request_stop`、`seekdb_ios_get_state`、`seekdb_ios_get_cleanup_status` 和 `seekdb_ios_get_cleanup_error`；`in_process_` 模式跳过服务信号线程，等待结束走 `stop()`，不走原命令行路径的 `_Exit(0)`。该路径已取得 36 步 SQL、多轮正常停止和连续持久化恢复证据。启动失败执行 stop/wait/destroy、curl cleanup 和工作目录恢复，主错误与清理错误分别记录；真机负向验收已通过。接口每进程仅允许调用一次，不可在 UI 线程调用。`BUILD_EMBED_MODE` 仍不能恢复旧 C API。
- iOS ARM64 链接已验证 S2/Abseil ABI、OpenMP 运行库版本及 Rust sql_nio 链接修复；数学和向量功能仍需真机运行验证。
- App 沙箱数据目录、线程和内存限制已完成基础适配；重复停止、前后台切换、终止后恢复及有界内存压力已验证；锁屏恢复仍需人工验收。
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

## 2026-09-25 standalone 试运行状态

当前 runner 已在 macOS 完成 current-HEAD 的 272/272 host mysqltest，并通过按 runner
配置顺序核验的 merged evidence gate。随后 App 准备构建发现两个环境/构建问题：

1. Mac 数据卷剩余空间约 8.4 GiB，低于构建脚本默认 10 GiB 门禁。使用 CMake
   `clean` 仅清理可重建的 production iOS build 产物后恢复到约 12 GiB；该数值不是
   iPhone 剩余空间。
2. clean 将 Git 跟踪的 `rust/sql-nio/include/nio.h` 删除。原因是该源码树头文件被
   错误列入 Cargo custom command 的 `BYPRODUCTS`。include path 本身存在，但 C++
   object 与 Rust build 可并行，Cargo 缓存也不保证在头文件缺失时重跑 cbindgen。

现已移除该源码头的 CMake clean 所有权，并增加回归测试。重新 configure 后生成的
`sql_nio_build` clean 规则不再包含 `nio.h`；使用 runner 同一组仓库内 Cargo/Rustup、
LLDB wrapper、hook-on profile 和 iphoneos ARM64 参数，`seekdb_ios_link_check` 已完整
链接通过。完整 iOS Python suite 为 297/297 通过。当前 revision 尚需继续完成 App
签名/安装和后续真机 phase，不能把本节的链接通过解释为全部真机测试完成。

## 2026-09-26 App 重链修复与当前状态

runner 后续两次重新执行 host gate，均为 272/272 通过；这仍是 macOS 结果。App 准备
阶段发现旧 executable marker 与当前 HEAD/runtime archive 不一致。构建日志进一步证明
两次新 App 编译都因缺少引擎头文件搜索路径而在链接前失败，旧 bundle 只是残留产物，
没有 current-HEAD 真机 case 因此被执行。

`build_app.py` 现在复用 engine cache 中已验证的 header prefix，并把 engine build root
传给 App CMake。独立 App target 补齐与引擎头文件一致的 source/generated/dependency
搜索路径、GNU C++20 和必要宏。由于 CMake `LINK_DEPENDS` 对 Xcode generator 不建立
外部 archive 输入依赖，打包命令固定执行 `clean build`；完成链接后，还会在 codesign
和安装前校验 App 实际 executable 的 source build ID 与 hook mode。若编译失败或 marker
不一致，旧 App 不会被安装。

真实 generic iOS clean build 已重新执行 `Ld` 和 `CodeSign`；App executable 与 runtime
archive 均包含 `6a60099e79ad/enabled` marker，且严格签名验证通过。该结果只证明 App
构建闭环恢复；当前提交仍需安装到已连接 iPhone，并继续完成 registry、C++、Rust、
mysqltest 等价用例、向量、生命周期和内存压力阶段后，才能生成完整真机矩阵报告。
安装前 fail-closed 与 resume 行为测试及完整 iOS Python suite 已达到 305/305 通过。

首轮真机 run 已完成 272/272 host gate 以及 App 构建、签名和安装，但 iPhone 在
`registry-smoke` launch 窗口持续锁定，case 按 10 分钟超时停止，checkpoint 保留。
这次恢复同时发现：当 host preflight evidence 已成功、但 mysqltest phase 因更早的设备
phase 尚未执行时，`--resume` 会把 pending 误判为需要重跑。修复后，该场景会重新校验
同一 run 的 binary snapshot 与完整 evidence identity 后复用；没有成功 evidence、明确
failed 时执行完整 host gate；已有 success evidence 但 snapshot、binary、corpus、digest
或 `run_id` 重验不一致时，resume 会 fail-closed 终止，不覆盖该 run 的可疑证据。

## 2026-10-04 扩展阶段

`vector`、`lifecycle-memory`、`final-matrix` 已接入 standalone runner。向量由两个
独立 App 进程共享专属 fixture 目录验证索引恢复和事务；生命周期由真实系统 scene
回调及拥有的 App PID 验证前后台、正常停止、SIGTERM 后恢复；有界内存压力在设备
上实际触碰 8/32 MiB 内存页并查询 SQL/ANN。最终矩阵拒绝任何缺失、失败或没有证据
的前序阶段。新增实现已在上述源码版本完成真机验收，最终矩阵通过。

锁屏/解锁操作仍是人工验收项；有界内存测试不证明 OOM 或 Jetsam 极限。macOS
mysqltest 的 272 项覆盖在报告中继续明确归为 host-only。

完整复验曾在 mysqltest 普通 SQL 停机遇到后台系统包 DDL 的 schema retry 与 session
cleanup 等待链。现已在 in-process 停机入口提前取消 DDL launcher、stop/wait loader，
再关闭 schema/SQL/runtime；native 锁持有回归、当前源码真机全阶段与最终矩阵均已通过。
此前 `86c9aafecc9f` 的专项阶段已通过，但完整轮次仍 incomplete，不能改写为通过。

## 显式 OOM/Jetsam 极限专项

这是单独的自然终止测试，普通 `./run.iphone.test.sh` 不默认执行。保持专用 SeekDB Probe
前台与设备解锁；脚本准备当前源码和签名产物，数据库目录每轮独立。运行配置沿用
`SEEKDB_IPHONE_DEVICE`、`SEEKDB_IPHONE_TEAM`、`SEEKDB_IPHONE_BUNDLE_ID` 等本机环境。

```bash
python3 unittest/ios_build/run_iphone_memory_limit.py \
  --output-dir iphone_test/memory-limit/<unique-run-directory>
```

引擎启动并完成持久化 SQL 后，以 64 MiB 步长实际写入匿名内存，最高 8 GiB。自然
ENOMEM 返回、系统 Jetsam 和达到安全上限分别判定；上限或超时不计为极限通过。
系统事件必须为本轮新 Jetsam 报告、明确目标 PID/SeekDBProbe victim reason；恢复要求
同数据库新进程完整 SQL 和干净停止。报告只给本轮设备/OS/前台状态下的观测极限，
不宣称固定可用上限或 seekdb allocator 自身全部用量。极限专项当前结果：首轮最后观测 allocated=3.125 GiB、footprint 约 3.26 GiB，进程自然
退出但没有取得匹配新 Jetsam 报告，因此 OOM/Jetsam **未验证**；同目录 SQL 恢复及
previous_runs=1、正常停机已验证。第二轮被快速后台切换打断，不能作为极限结果。

`d4876815c` 增加离开前台即释放压力保护，以及失败轮次也保留恢复报告；当前 App
编译、签名和安装成功，完整本机回归 318/318。需要保持解锁设备在 SeekDB Probe
前台后继续专项。首轮脱敏报告为
`iphone_test/memory-limit/2026-10-04-first/evidence-memory-limit-first-result.json`。
这不改变此前完整矩阵所验证的 `6deac1205bd0` 范围及其有界内存结论。
