# iOS 移植：环境与变更记录

本记录覆盖截至 2026-09-24 的本轮移植。当前验证分支 `codex/ios-generic-validation` 已 rebase 到 `upstream/master` 的 `834bbee1e`；原始 iOS 分支仍保留。入口说明见 [ios-build.md](ios-build.md)。

## 记录规则

后续每次环境设置、源码、构建脚本或 CMake 变更，同步记录日期、文件/设置、原因、具体参数、影响范围、复现命令、验证结果和剩余问题。失败尝试及撤销原因也保留。按独立功能提交时补充 commit ID，不把机器缓存、证书或密钥提交到仓库。日志与生成产物保存在仓库内；本记录及构建脚本纳入版本控制。

## 2026-09-25：Task 5 mysqltest standalone adapter（host contract only）

- 新增 active mysqltest parser 与独立 phase planner。静态发现继续复用现有 host runner，固定覆盖 283 个 active case、其中 272 个 CI-selected case；递归 `--source` 闭包为 316 个文件、302 条 source 指令、最大深度 2。循环、越出 `mysql_test` 根目录及缺失 include 均 fail-closed；当前 selected `fork_table.fork_table_merge` 经 `wait_condition.inc` 引用缺失的 `show_rpl_debug_info.inc`，因此明确记录为 host-only/source-unavailable。
- parser 保留 `--error` 的数字、symbolic、逗号列表和 `0` 形态以及每条 SQL 的文件、行号与 include stack provenance。connection、process/shell、topology、parser/control、result rewrite 与 error-policy 等不能无损映射的语义均记录稳定 host-only 原因。inventory 的 relevant-input、dirty gate、untracked gate 与 corpus digest 扩展到 `.inc`/`.sql`，并继续覆盖 `.test`/`.result`。
- 只有实际消费 tracked `.result` 且设备 C++ callback 同时断言 SQL status、affected rows、result column count/rows 的实现才能标记 device-native。当前仅 `empty_table` 达到该边界并注册为 `ios.mysqltest.empty_table`；此前静态 parser 得到的其余候选全部保守降级为 `device-transcript-unavailable`，不把“能拆出 SQL”冒充 lossless。host 272-case gate 单独读取完整 host result，不能提升为 device pass。
- stage 5 adapter 将 host gate、逐 source device case 和强制 SQL/same-directory restart gate 注册为独立 case。checkpoint resume 只选择 failed/pending，passed 不重跑；每个 source ID 仍由既有 phase engine 生成独立 failure artifact。后续安全复审已取消外部 host-result 导入，当前流程见 2026-09-25 记录。
- 本轮仅运行 host parser/inventory/adapter contracts、Python compile 和完整 Python tests；没有执行 iOS build、签名、安装或真机 case。因此 `empty_table` 目前是已实现的设备契约，不是已取得真机 pass；真实执行及 interrupt/resume acceptance 留给审查通过后的 standalone 试运行。
- 第五轮安全复审将 tracked host runner 的 result、failure、diagnostic、tmp 与 log
  路径统一改为 descriptor-anchored 写入。目录逐段 `O_NOFOLLOW`；固定输出只通过
  随机 `O_CREAT|O_EXCL` 临时 regular file、file/directory `fsync` 和同目录
  `renameat` 发布，预置 symlink、FIFO、特殊文件或不安全父链均 fail-closed，不能
  改写外部 victim。result comparison 与 diagnostic copy 同样通过 bounded regular
  nofollow fd 读取。
- host workspace 不再预建空 instance；真实 tracked `sdb.py destroy` 已验证对准备后
  尚不存在的 instance 返回成功。受控 host runner 即使 leader 先退出、后代关闭
  pipe 或忽略 TERM，也会在 grace 后无条件向原 PGID 发送 KILL并有界确认消失。
  runner 异常、timeout、SIGINT 和正常 run command 返回后，外层另起受控进程组调用
  tracked `sdb.py destroy`，只作用于本 run 的 anchored instance 与 marker/binary
  identity；真实编译的 detached fake seekdb 回归验证 daemon、pid 和 instance 均被
  清理。本轮未执行真实 272 case、iOS build、签名或真机。
- 随后的真实 macOS 27 ARM64 host release 构建在 Rust build.rs 已由 tracked LLDB
  wrapper 正常执行后，暴露 jemalloc configure conftest 被 SIGKILL 9 的独立故障。
  `deps/external/cmake/Jemalloc.cmake` 仅在 `OB_MACOS27` 且目标架构列表恰为单一
  ARM64 时向 `JEMALLOC_SYS_CONFIGURE_ARGS` 追加
  `--host=aarch64-apple-darwin`；普通 macOS、macOS 27 x86_64、既有 iOS
  `--host=aarch64-apple-ios` 及 Android 保持不变。新增 focused 测试用真实 CMake
  生成并执行 Cargo custom command，检查 fake Cargo 最终收到的环境。该 host
  参数让 autoconf 在 initial runtime probe 失败后切换 cross mode并跳过后续 runtime
  probes，并非完全不运行 initial probe。本轮没有
  触碰正在生成的 `build_release`，也未运行完整 host build、真实 272 case、签名或
  真机测试。
- mysqltest host binary resolver 现在逐项优先显式
  `SEEKDB_IPHONE_HOST_SEEKDB`、`SEEKDB_IPHONE_HOST_OBCLIENT`、
  `SEEKDB_IPHONE_HOST_MYSQLTEST`；缺失项只尝试当前 repository root 的固定
  `build_release/src/observer/seekdb` 与
  `deps/3rd/u01/obclient/bin/{obclient,mysqltest}`。它不搜索 `PATH` 或相邻 checkout，
  显式无效值不回退，所有路径继续要求 executable regular non-symlink 并由 host
  evidence 绑定实际 bytes。当前机器 canonical 产物齐全，所以常规入口只需
  `./run.iphone.test.sh`；缺失时仍在任何设备/build/sign/install 前失败，首次准备命令
  为 `./build.sh release --init --make`，不会由 standalone runner 隐式执行。

## 2026-09-25：standalone runner stages 1–4 adapter

- 新增 `iphone_test_phases.py`，把 inventory、registry smoke、4 个现有 C++ case、5 个现有 Rust case 和 production Rust symbol isolation 注册为稳定 adapter contract；每项显式记录 execution class、host timeout、命令、evidence validator 和 SQL/restart follow-up 要求。device runner 继续只保存 allowlist JSONL，不保存原始 CoreDevice/devicectl metadata。
- test-hook engine build、App package/sign/install 在 checkpoint 前、runner-lifetime 独占锁内完成；实际 CMake cache、runtime archive 和由 Info.plist 指定的 App executable bytes 随后进入 fingerprint。production hook-off/symbol gate 使用新增 `--build-dir` 的独立目录，避免破坏可恢复 checkpoint 绑定的 test App。
- 无显式 bundle/team 时，仅从唯一一致且未过期的现有 App/profile 推导；设备范围、单一 certificate 和本机 private-key identity 必须全部匹配。原始值只留在进程内并在外部命令前加入动态脱敏集合。签名 identity 不是 `build_app.py` 的硬参数。
- 依赖路径优先取 `SEEKDB_IPHONE_DEPS_PREFIX`、`SEEKDB_IPHONE_HEADERS_PREFIX`、`CARGO`、`RUSTUP`、`CARGO_HOME`、`RUSTUP_HOME`、`RUST_TARGET_DIR`；否则从现有 cache 与 cargo 相邻目录推导。所有路径在调用前验证，随后显式传给 build；输出只记录非敏感来源标签。当前本机只读审计显示 profile/key 唯一匹配，cache-backed dependencies 可用，但部分输入来自另一 checkout 的 cache，故实际命令会显式传值而不依赖隐式默认。
- 本提交只运行 host focused/full contract、shell syntax、Python compile 和 diff checks；按任务要求未执行 build、签名、安装或真机 stages 1–4。真正试运行仍以在线唯一物理 iPhone、profile device scope、有效 private key、iphoneos ARM64 dependencies、干净 checkout 和可用磁盘为前置；主机契约通过不等于真机 ready 或真机 pass。

### 质量复审修复

- artifact identity 可复用不再等于跳过准备：每个 device run 在 checkpoint 前都验证 selected device 属于实际 embedded profile、certificate/private-key 唯一可用、App codesign 有效并执行本次安装；fresh build 后重新读取实际签名产物并复验。resume 执行相同的安全复验，selected device 以 SHA-256 隐私摘要进入 fingerprint，A/B 设备不能共享 checkpoint。
- registry、C++、Rust 三个 device phase 各新增稳定 SQL/restart gate case。每个 gate 必须在 run 专属目录保存两轮各 36 个成功 step，且 `previous_runs` 精确为 0、1；只写 details 不执行 follow-up 的旧行为不再能产生 phase pass。首轮证据已持久化时可在中断后只续跑 restart 轮。
- device launch、evidence poll 与 clean-stop poll 改为共用单一绝对 deadline；adapter 外层 timeout 比内部预算多 120 秒。resume 对每个已通过 case 持久化 skip count/time，供真实 interrupt/resume 试运行审计。
- `--suite inventory` 走纯 host-only 路径，不发现设备且不 build/sign/install。显式 fresh/empty `RUST_TARGET_DIR` 允许由 Cargo 创建；simulator/x86-only target 和非 iphoneos ARM64 cache 继续严格拒绝。
- 本轮仍只运行 host contract，未启动真机；真实 interrupt/resume acceptance 留给审查通过后的独立试运行，不能用 mock 结果声称完成。
- same-day `--restart` 不再可能因目录内残留的固定 SQL 文件零 launch 假通过：gate JSONL/metadata 使用 runner run ID scoped 文件名，并校验 run ID、build ID、selected-device 隐私哈希、data directory、hook mode、`previous_runs` 与内容摘要。只有同一 run 的完整首轮可在 resume 时跳过；新 run 即使看见旧文件也必须实际 launch。
- SQL round 增加 durable intent 协议：`prepared` 证明尚未进入外部 launch，`launch-uncertain` 表示只能按同一 round ID 从设备恢复。SIGINT 或 metadata fsync/OSError 后 intent 保留；resume 先重新复制并验证当前设备终态再补 completion metadata，证据缺失或状态不确定时停止而不重复推进 `previous_runs`。新 `--restart` 使用独立 scope，不受旧 intent 影响。
- CoreDevice 设备身份拆为两种进程内值：顶层 `identifier` 专供 devicectl 命令和显式 `--device` 选择，`properties.hardware.udid`（legacy 为 `hardwareProperties.udid`）专供 embedded profile 的 `ProvisionedDevices` 校验。hardware UDID 缺失或两个 schema 值冲突时安全拒绝；两种标识都进入动态脱敏集合，并以组合 SHA-256 隐私身份绑定 checkpoint fingerprint，任何原值都不进入 checkpoint/report/Git。本修复增加 current/legacy/缺失/冲突、profile scope、参数分流和原值扫描回归，只运行 host 测试，未运行真机。
- cache-backed build input 不再把可执行的 rustup 误当 Cargo：runner 对 `cargo --version` 与 `rustup --version` 做捕获式角色校验；cache `CARGO` 角色错误时仅接受验证通过的同目录 cargo sibling，并把正确路径显式传入 `-DCARGO=`，没有 sibling 或 cargo/rustup 混用时在 build 前阻断。setup 可诊断性改为 allowlisted 固定错误码，只输出 build/sign/install/profile 等阶段类别；未知返回、token-bearing exception、stdout/stderr 与任意异常文本仍统一脱敏为 generic failure。本修复仅运行 host 测试，未启动真机。
- rustup-managed shim 的角色 probe 顺序修正为先解析 candidate paths、`CARGO_HOME` 与 `RUSTUP_HOME`，再以 homes 和工具 `bin` 优先 `PATH` 组成的受控环境分别执行 `cargo --version`/`rustup --version`。正确 managed context 可通过，错误 home 继续安全阻断；所有 probe stdout/stderr 均捕获丢弃，回归中的敏感输出不进入终端、异常、checkpoint 或 report。本修复只运行 host 测试，未启动真机。
- 将此前 ignored build cache 内的 LLDB rustc workaround 正规化为受跟踪的 macOS-only `unittest/ios_build/rustc_lldb_wrapper.py`，standalone build 固定注入其绝对路径并在启动前验证 `xcrun`/LLDB；缺失时返回固定 build-input 类别。wrapper 先传播真实 rustc 的 exit/signal status，只原子替换本次新生成、严格 `build_script_build-<hex>`、macOS 64-bit host Mach-O 输出，保留 `.real`，并由 quote-safe launcher 传播 LLDB 内 build-script exit；symlink、路径异常、候选歧义及 stale `.real` 均拒绝。fake rustc、Mach-O、LLDB/build-script status 与 shell quoting 均有 host contract 覆盖。本修复未运行真实 iOS build、签名、安装或真机测试。
- LLDB wrapper 复审修复两项 fail-open：rustc current output 现在绑定 `extra-filename` 预期名，并比较 pre/post device、inode、mtime_ns、size、SHA-256；连续第二次覆盖同名 raw Mach-O 且 `.real` 已存在时固定失败，不再因 basename 预先存在而跳过。LLDB 为 SIGTERM、SIGKILL 和常见 fatal signal 配置 pass/no-stop，只有 process state 为 exited 才读取 status；exit description 中的 signal 映射为 `128+signal`，xcrun/LLDB 自身负 returncode 同样规范化。本机用 `xcrun clang -arch arm64` 生成的真实 macOS Mach-O 和真实 LLDB host 测试验证普通非零 7、SIGTERM 143、SIGKILL 137；未运行 iOS build 或真机。
- LLDB wrapper 不再依赖易遗漏的固定 fatal-signal 名单。launcher 先 `stop-at-entry`，再通过 live process 的 `SBUnixSignals` 动态设置 pass/no-stop/no-notify，最后 continue；SIGSTOP、SIGTSTP、SIGTTIN、SIGTTOU 保持默认策略，避免不可传递或作业控制 stop 造成挂起。带 20 秒边界的真实 arm64 Mach-O/LLDB 测试除普通非零、SIGTERM、SIGKILL 外，新增验证此前会被 LLDB 截停并误报 0 的 SIGTRAP=133 与 SIGQUIT=131。本修复未运行 iOS build、签名、安装或真机测试。
- LLDB job-control stop 不再沿 batch rc=0 fail-open：正常 command 和 `--one-line-on-crash` 路径现在复用同一 status mapper，只有 `eStateExited` 才接受 exit status/signal，任何 stopped 或其他终态固定退出 125。带超时的真实 arm64 Mach-O/LLDB 回归验证 SIGTSTP、SIGTTIN、SIGTTOU 均为 125，同时保留 normal、fatal signal 与 stale `.real` 行为；未运行 iOS build 或真机。
- standalone preparation 的 legacy Cargo cache 迁移提升为 durable build-unit transaction：发现 raw final 后，同时隔离 matching hashed rustc output、stale `.real` 和精确 unit fingerprint，避免 Cargo Fresh 从 hashed raw 恢复而跳过 wrapper。事务在 move 前 fsync target-relative manifest，partial move 后下次运行先完成恢复；both/neither、类型变化和 collision 均 fail-closed。所有将扫描的 direct profile symlink 现在拒绝，不再跳过。完整 tracked final/hashed launcher + matching `.real`、源码、device binaries 和未知文件保持不动，重复执行幂等，CLI 仍只记录 `migrated-N`。除 synthetic raw/stale/pair/symlink/collision/recovery contract 外，本机 Cargo 1.98 最小 build.rs integration test 证明迁移后 verbose command 重新调用 tracked rustc wrapper，最终 final/hashed launcher 指向 matching `.real`；未运行 iOS build 或真机。
- wrapped Cargo unit 完整性继续绑定 crate unit hash：final 与 hashed launcher 必须同时指向当前 `<crate-hash>` 对应的精确 `build_script_build-<hash>.real`，且 `.real` 仍为 regular、non-symlink host Mach-O。两个 launcher 共同误指另一 hash 的 valid `.real` 不再误判为 healthy，而是迁移当前 unit 后强制重建；新增 mismatch 回归。未运行 iOS build 或真机。
- phase command environment 统一绑定完整 Xcode：`_default_executor` 现在复制当前环境，保留显式有效 `DEVELOPER_DIR`，仅在缺失时选择默认完整 Xcode，并在命令前验证 devicectl、LLDB、iphoneos SDK。该冻结环境传给所有 phase subprocess，尤其 reuse-build 的 codesign/devicectl install，不再隐式回退 CommandLineTools；无效选择固定返回 `build-inputs`，probe 输出不外泄。新增 executor env、显式选择、reuse install 和固定错误码回归；未运行 iOS build 或真机。
- phase engine 不再在 dispatch 前批量 terminalize 所有 missing adapter。case spec 仍可预登记，但 engine 严格按 `PHASE_IDS` 执行：先完成已注册 stages 1–4，到第一个 missing stage 5 时只落一个 infrastructure failure 并停止，stages 6–8 保持 pending；resume 跳过已通过的前四阶段并只重试 stage 5。新增顺序、状态、单 failure artifact 与 resume skip/attempt 集成回归；未运行 iOS build 或真机。
- LLDB rustc wrapper 现在区分 stale raw + `.real` 与可正常重编译的 healthy exact launcher + matching `.real`。后者在 rustc 前先 durable 记录 pair identity/digest 的 prepared intent，成功刷新 raw 后推进为 ready manifest：旧 `.real` 保留到 identity-scoped backup，新 raw 转为 `.real` 后重新安装 launcher，所有 rename 都 fsync；rustc 普通失败恢复旧 launcher，wrapper 在 compiler 后或 partial rename 窗口中断则按 old/new digest 完成恢复，未知、损坏或 collision 状态继续 fail-closed。synthetic stale 回归仍返回 125；故障注入覆盖 post-compiler 与 partial rename 恢复；本机真实 arm64 Mach-O 连续两次不同 binary 编译均成功，第二个 launcher 经 LLDB 实际执行新 binary。未运行 iOS build 或真机。
- prepared rollback 的 durable launcher temporary 不再造成永久 125：恢复时必须验证它是指向 manifest old `.real` 的 exact、regular、non-symlink tracked launcher，随后完成 replace、directory fsync 与 manifest cleanup；错误内容或 symlink 仍 fail-closed。故障注入覆盖 temporary fsync 后、replace 前中断，并验证恢复后二次调用幂等；未运行 iOS build 或真机。
- device launch 边界新增纯内存 allowlist 分类：locked、disconnected、not-installed、trust、developer-mode、other。Locked 向调用终端打印固定解锁/常亮提示，在既有 shared deadline 内每 5 秒有界重试；非交互同样超时失败，其他类别立即以固定安全诊断退出。phase adapter 仅从 sanitized stderr/stdout 提取 allowlist 行、再次脱敏/限长后持久化，并将 launch 类失败标为 infrastructure；raw CoreDevice metadata、argv、相邻 token 均不落盘。production isolation exit 2 固定记录 build failure，不暴露 log。新增 locked→success、locked timeout、其他类别单次失败、checkpoint/failure token scan 和 production diagnostic 回归；未运行 iOS build 或真机。
- launch failure 改由结构化 `LaunchFailureCategory` 跨边界传递。SQL intent 仍在调用前进入 `launch-uncertain`；仅当每次尝试都明确 locked 且最终 deadline 耗尽，caller 才耐久地把同一 round ID 回写为 `prepared`，解锁 resume 可重新 launch。unknown/other 不 reset，保持 uncertain；prepared reset 写入崩溃时也继续保留 uncertain。新增 locked timeout→prepared→同 round resume success、unknown no-reset 与 reset fsync failure 回归；未运行 iOS build 或真机。
- standalone phase executor 从 `capture_output`/reader thread 改为独立进程组、binary pipes 和单线程 `selectors` 非阻塞双流 drain。只有以换行结束且 UTF-8 bytes 完整逐字匹配的固定解锁提示可经 runner 显式 stderr 在子进程完成前实时回显，每个命令最多一次；near-match、partial line、非法 UTF-8、注册 token 和其他原始内容均不回显，最终 bytes replacement-decode 后仍在边界脱敏和限长。leader wait 与 pipe drain 共用一个 monotonic deadline；leader 已 rc0 但后代继续持有 pipe 时也固定返回 timeout 124，不再无限 join 或假通过。timeout、SIGINT 和其他中断均以有界 TERM/KILL、drain 与 reap 清理整个进程组；stream 由 selector unregister 后的唯一 owner 正常 close，不再 raw-close 仍被 `TextIOWrapper` 持有的 fd，避免 descriptor 复用后误关 checkpoint。新增提前可见、incremental/partial/invalid UTF-8、重复提示、大双流、spoof/token、普通 timeout、leader-first inherited-pipe、真实 SIGINT descendant 和 fd reuse 回归；host focused 测试通过，未运行 iOS build 或真机。

## 2026-09-24：Rust runtime tests 的真机入口

- TDD 首轮 focused RED 证明共享 `device_tests.rs`、test-only feature/profile、固定 C ABI、C++ adapter 和单一 archive marker 均不存在。实现后，`cert.rs`/`tls.rs` 的三项测试断言抽为 `Result<(), String>` case，host `#[test]` 只负责调用并 unwrap；inventory 仍发现精确三项 Rust host test，并分别映射到三个稳定的 `ios.rust.*` device equivalent。
- `ios-device-tests` feature 才导出 count/case-info/run C ABI；结构只含固定宽度整数和固定容量数组。非法 index、空/过小 output、诊断截断均在 Rust 边界处理。`ios-device-test` profile 是唯一 `panic="unwind"` 的链接 profile；release 与 cmake-debug 均保持 abort。intentional-panic case 在最外层 `catch_unwind` 转成 `NIO_DEVICE_TEST_PANIC`，设置进程内 containment marker；下一 continuation case 必须读到 marker 并成功，防止只验证错误码而未验证后续执行。
- `cmake/Rust.cmake` 在 iOS test hooks 开启时选择该 profile/feature/output directory，否则走原 production 路径。`seekdb_ios_runtime` 传播 test-only compile definition；App CMake 只在 `RUST_DEVICE_TESTS=ON` 编译 adapter。`build_app.py` 要求最终 link response 中恰好一份 Rust archive，并使用 `/usr/bin/strings -a` 的精确 symbol 行检查模式。没有使用 Apple/LLVM 21 `nm` 作为门禁，因为它无法读取 Rust 1.98.1 所带 LLVM 22 产生的 bitcode attribute；真实 feature archive 已验证包含三项 C ABI symbol，production release archive已验证不含。
- 主机验证：Rust 三项 unit test 和 doc-test 通过，`cargo clippy --locked -p sql-nio --all-targets -- -D warnings` 通过，`cargo fmt --all -- --check` 通过；`test_rust_device_tests.py` 6 项、inventory 12 项和完整 iOS Python 64 项通过。cbindgen 由 crate build 重新生成 `include/nio.h`，feature declarations 受 `SQL_NIO_IOS_DEVICE_TESTS` 保护。当前主机仍需忽略目录中的 LLDB rustc wrapper 执行 Cargo build-script；该 wrapper 不是仓库接口。为避免磁盘累积 stall，只删除了本轮新建且可再生的 host release/ios-device-test Cargo 输出，未删除 App container、设备证据或用户数据。
- 真机验收顺序固定为：test archive build/sign/install；`rust` suite 三个真实 case 加 panic/continuation；保存证据并确认干净停止；hook-off production rebuild 并证明 test symbol absent；最后在同一 native SHA 的全新目录执行 36-step SQL 首轮和同目录 restart，并比较相关 crash/Jetsam 增量。验收前不得预填 pass；最终 evidence 只写脱敏摘要，不记录 device、Team、证书、profile 或账号标识。
- preliminary source build 完成 test-hook iphoneos 全量链接、签名、安装和五项 `rust` 真机 case，全部 case 与 run 结果为 0。将结果状态写入 inventory/docs 后 amend 到最终 source build ID，并在最终 SHA 上重新完成 test-hook 链接与五项 case；再关闭 hooks 重建 production archive/App，确认 test symbols 缺失、链接闭包只有一份 production Rust archive。普通模式在全新目录完成 36-step SQL 首轮及同目录 restart，`previous_runs=0/1`，两轮均干净停止，序列前后相关 crash/Jetsam 增量为 0。最终脱敏证据位于忽略目录 `build_ios_arm64/device-evidence/task4-rust/final/`。

## 2026-09-24：C++ 孤立测试的真机覆盖

- 当前 revision 没有普通 C++ unittest 构建定义，普通目标数为 0；源码清单仍发现 9 个 GTest 注册。新增 `test_cpp_device_tests.py` 后先取得缺少 `cpp_device_tests.cpp` 的 RED，规格复核又以缺失的精确 parse/backend/memalign 断言、错误等价分类和 evidence metadata 取得第二轮 RED；再实现 4 个独立 `cpp` suite case，并将 App registry 从 smoke-only 合并为 smoke 加 C++。focused 8 项、inventory 12 项通过，完整 host suite 当前为 58 项；新增源以 iphoneos SDK/ARM64 compile-only 通过，仅出现既有引擎头文件 warning。
- 9 个注册逐项分类为 3 个 device equivalent 和 6 个 exact exclusion。malloc backend 的 `parse`/`detect_once`/`direct_jemalloc` 映射到设备上的 allocator backend、allocate/reallocate/usable-size/free 和 alignment 行为；`direct_jemalloc` 的单一映射 case 同时断言已选 jemalloc 且执行 `jemalloc_memalign`。`CrossApiAllocationDomain` 因 Linux `<malloc.h>` 与进程级 libc hook replacement 排除；`ReallocAndAlignment` 同样依赖 libc malloc/realloc/memalign 通过 malloc-zone hook 进入 jemalloc，显式 `ob_`/jemalloc API 不是该 hook invariant 的等价执行。`restore_after_fork` 因 `fork()`/`waitpid`/jemalloc child background-thread 状态排除；`test_adder` 源码在 Apple 上明确 `GTEST_SKIP`，且其 `ObErrorInfoMgr` 生成器不在 App 链接闭包内，`test_mgr` 与 `test_parser` 同样属于未链接的 CLI manager/getopt 路径，均不伪装成 embedded runtime case。
- `cpp_device_tests.cpp` 只调用 iOS 链接产物中真实存在的 allocator 和 error metadata API，不复制 GTest 源到 `tools/`，也不调用 fork、Linux malloc API、mallctl 或 Darwin malloc-zone hook；`OB_INVALID_ARGUMENT` 现在精确断言 MySQL `ER_WRONG_ARGUMENTS`，不只检查正数。inventory 新增 4 个稳定 registry ID，当前总计 812 行：9 个 GTest 中 3 个有 device equivalent、6 个 excluded、0 个 blocked。四个设备 ID 和三个 mapped required GTest 都记录 `latest_result=pass` 与同一脱敏摘要路径；严格生成仍要求 manifest/generator/发现输入与 HEAD 一致，忽略证据不作为 source discovery 输入。
- 最终提交的同一 native source SHA 已完成 iphoneos runtime/App 链接、现有本机描述文件签名校验和安装。真机 `cpp` suite 选中 4 个 registry case，每个 case 均产生至少一个成功 assertion，case 和 run 终态均为 0，engine cleanup 为 `cleanup_status=7`/`cleanup_error=0`且恢复工作目录。随后使用全新数据目录执行普通 36-step SQL 首轮和同目录 restart 轮，两轮均为 36 个成功 step 加成功 complete，`previous_runs` 依次为 0、1，清理终态均完整，probe 相关 crash/Jetsam 增量为 0。证据保存在忽略目录 `build_ios_arm64/device-evidence/task3-cpp/final/`，不含设备、Team、证书、profile 或账号标识。
- 失败尝试与边界：最终序列前连续两次 C++ suite assertion 和 `suite_result=0` 都已完成，但 status 持续为 `Stopping`、`result`/cleanup 为 null，host 分别在 180 秒和 300 秒超时。第二次已使用 `--terminate-existing` 和全新 data name，因此单纯重启进程/换目录不能解释恢复。当时专用测试 App 的 `Documents` 顶层已累积 43 项数据，日志未越过 `ObServer::set_stop()` 的首个 `sql_nio_stop()` 停机边界。卸载并重装该专用测试 App 后，同一 native SHA 的 C++ suite 和后续 SQL 0→1 才完成干净停机。卸载会删除整个 App container，包括数据库目录和未复制证据；只能在专用测试 App 上、保存必要 host 证据后执行。累积 container 状态下 `sql_nio_stop()` stall 的精确根因、阈值和非破坏恢复方案仍未解决、未验证；本次最终证据只证明 clean container 下 C++ 和同目录 SQL `previous_runs=0/1`。可复现的占位参数 reset 命令见 `ios-build.md`，不记录真实 bundle、device 或 Team 标识。
- 本边界说明为验收后 docs-only follow-up；真机产物与证据仍绑定 native source SHA `669875476d83857ec4382e57834b5fef1cdcb3d3`，不将文档提交 SHA 冒充为重建或重跑结果。

## 2026-09-24：device registry 与结构化证据协议

- 质量复核补齐三项协议边界。`timeout_seconds` 现由逐 case watchdog 强制执行；callback 未在 deadline 内返回时，writer 在互斥保护下追加并 flush `case_timeout` 失败 assertion、`case_end/result=124`、`run_complete/result=124`，随后 `_Exit(124)`。这是无法安全取消任意 C++ callback 时的进程级终态失败：它提供有界 host 结果，但不把被终止进程表述为正常 cleanup。
- host runner 新增未完成证据类型，只重试可继续增长的合法前缀；结构完整但 stale、重复、非零或其他终态无效证据立即抛出原始校验错误。writer 新增严格 UTF-8 扫描：合法 2/3/4 字节序列原样保留，引号与控制字符正常转义，overlong、surrogate、越界、孤立或截断字节逐字节替换为 U+FFFD。focused contract 使用真实阻塞 callback 和可执行 C++ writer 覆盖以上行为。
- 质量修复的 focused 14 项与完整 iOS host suite 49 项通过；重新配置并链接 iphoneos runtime 与 App，完成手工签名校验和安装。真机严格按 registry smoke、全新目录普通首轮、同目录 restart 执行：smoke 为 5 个成功事件，两轮 SQL 均为 36 个成功 step 加最终完成记录，`previous_runs` 为 0、1，相关 crash/Jetsam 增量为 0。只保存脱敏 JSONL、状态和摘要，不保存原始设备或签名元数据。
- 二次质量复核发现已 flush 的失败 assertion 仍会在 `run_complete` 前触发语义错误，使 host 提前退出。新增两阶段 polling RED：第一次复制得到 `case_start` 加失败 assertion，第二次才得到完整失败 run。runner 现以 `run_complete` 是否出现作为语义校验门槛；此前一律重试，出现后立即传播原始失败。focused 15 项与完整 host 50 项通过。该修复只涉及 host runner、测试和文档；既有真机证据继续绑定 `a11ef0d6208f`，不冒充更新后 HEAD 的设备结果。
- 新增 `device_test_registry.{h,cpp}`：case 使用稳定字符串 ID、suite 和逐 case timeout metadata 注册；注册表拒绝无效或重复 ID，按 ID 排序，并支持 suite 加 glob filter 的确定性选择。内置 `ios.registry.smoke` 仅验证设备回调和 assertion 记录，不替代后续真实 C++、Rust、SQL、vector 或 lifecycle case。
- 新增 `device_evidence.{h,cpp}`：设备端 JSONL writer 以 append 模式写入 run-scoped 文件，每条记录均包含 device origin、run ID 和链接引擎 build ID，并在继续执行前 flush。事件顺序为 `run_start`、`case_start`、零个或多个执行中的 assertion、`case_end`、最终 `run_complete`；当前 smoke 至少产生一个 assertion。
- UIKit App 仅在显式提供 `SEEKDB_IOS_TEST_SUITE` 时进入 registry；同时读取 `SEEKDB_IOS_TEST_FILTER` 和 `SEEKDB_IOS_TEST_RUN_ID`，对 run ID 做字符和长度约束，证据文件固定为 `Documents/device-test-<run-id>.jsonl`。普通 launch 不设置 suite，仍执行既有 36 步 SQL 路径。CMake 仅增加两个 registry/evidence C++17 源文件，并把 ARC 编译选项限制到 Objective-C++。
- 新增主机 runner `run_device_suite.py`：启动已安装 App，只复制 allowlist JSONL；校验 run/build identity、suite/filter、独立预期 registry 覆盖、case timeout metadata、完整且无重复的事件序列、device-authored assertions 及零结果，并检查本机同步目录中新增的相关 crash/Jetsam 报告。`devicectl` 原始输出只保留在进程内存，不写入仓库证据目录。
- TDD 初始 RED 由缺失 registry、runner 与 App/CMake 接线触发。第一次真机启动又暴露 runner 继承默认旧数据目录，导致引擎在 Running 前返回 `-4109`；cleanup 仍为完整的 `status=7/error=0`，且没有新 crash/Jetsam。新增数据目录名称 RED 契约后，runner 默认选择独立 `ios-device-tests`，只接受 1 至 64 个字母、数字、下划线或连字符。随后又增加终态 RED 契约，要求 JSONL 完成后继续等待当前 run/build 达到 `Stopped/result=0`、`suite_result=0`、完整 cleanup 和工作目录恢复。
- 最终 focused 10 项和完整 iOS host suite 45 项通过。使用 iphoneos SDK 对新 C++ 与 Objective-C++ 源做 compile-only 检查通过，CMake/Xcode 未签名 Release App 完整链接通过。当前提交身份的 `seekdb_ios_link_check` 增量构建通过；Rust host build-script 仍需使用本地忽略目录的 LLDB wrapper 绕过既有 macOS SIGKILL 限制，该 wrapper 不是项目接口或提交内容。
- Xcode 当前没有登录账号，自动 provisioning 正确失败。随后只在内存中选择唯一 booted/wired 真机，并复用本机现有开发描述文件；脚本核验 bundle、物理设备 provisioning identity、有效期、证书及钥匙串私钥匹配后完成手工 codesign、严格验签和安装。任何 Team、证书、profile、账号或设备唯一标识均未写入仓库证据。
- `ios.registry.smoke` 真机执行产生 `run_start`、`case_start`、1 条通过 assertion、`case_end` 和 `run_complete`，case/run result 为 0；App 随后达到 `Stopped/result=0`、`suite_result=0`、`cleanup_status=7`、`cleanup_error=0`，工作目录恢复。普通模式使用另一新数据目录完成 36 步 SQL 并干净停止；重启同一目录再次完成 36 步，持久计数从首轮 0 读回为 1。两轮报告均为 36 个成功 step 与最终 `complete/result=0`，且未发现新增相关 crash/Jetsam 报告。
- Spec review follow-up 把 smoke 记录明确修正为 5 个事件，并在同一个已签名 follow-up HEAD 上严格按 smoke、普通首轮、同一全新目录普通重启轮重新执行。脱敏 JSONL、状态与摘要保存在忽略目录 `build_ios_arm64/device-evidence/task2-final-head/`；顺序执行前后扫描相关 crash/Jetsam 增量，不保存原始 `devicectl`、签名或设备元数据。

## 2026-09-24：分层测试 Phase 1 启动失败清理

- 新增受 Git 跟踪的英文设计 `docs/ios-layered-test-strategy-design.md` 和清单格式 `docs/ios-test-inventory-schema.md`。三类执行位置为 device-native、host-driven-device 和 host-only；交叉编译或主机脚本通过不计作真机通过。
- `src/observer/ios/seekdb_ios.cpp/.h` 在失败路径分别记录 server、curl 和工作目录清理位，并通过 `seekdb_ios_get_cleanup_error()` 暴露第一个清理错误。server 清理位仅在 stop/wait/destroy 成功后设置，原始引擎错误不被后续清理错误覆盖。
- `src/observer/ob_server.cpp` 对进程内初始化失败先请求停止，由 iOS 包装统一完成 wait/destroy，避免重复销毁；普通命令行模式保留原清理行为。
- `SEEKDB_IOS_TEST_HOOKS` 默认关闭。开启时，仅测试目标在 timer service 初始化后响应 `SEEKDB_IOS_TEST_FAIL_DURING_INIT`；iOS runtime archive 编译进 source revision 和 hook mode marker。`deps/ios-build/build_app.py` 同时校验 cache 和静态库 marker，checkout 前移后复用旧库或只切换 cache 未重编译都会被拒绝，避免 fault hook 混入普通 App。
- UIKit 状态从链接的 runtime API 读取源码 build ID 和 hook mode，并增加唯一 run ID、cleanup status、独立 cleanup error 和工作目录恢复结果。`run_device_cleanup_test.py` 同时校验 run ID、artifact build ID 和 hook mode；它不再把原始 `devicectl` JSON 写入仓库内证据目录，避免保存 Team ID 或设备唯一标识。
- 使用固定 Rust 1.98.1、`aarch64-apple-ios` target、iphoneos SDK 27.0、最低 iOS 18.0、`OB_ENABLE_STANDBY=OFF`、`SEEKDB_IOS_TEST_HOOKS=ON` 及 RelWithDebInfo `-O2` 完成 `seekdb_ios_link_check` 全量链接。当前 macOS 会终止直接启动的 Cargo 宿主 build-script；本次沿用忽略目录中的 LLDB wrapper 驱动宿主程序，该 wrapper 不属于项目接口。
- 主机契约测试覆盖失败 cleanup、partial-init `OB_NOT_INIT`、stale run ID、stale engine build、artifact hook mode、cleanup error、device SDK 强制选择和原始设备元数据禁写；`python3 -m unittest discover -s unittest/ios_build -v` 共 23 项通过，`bash -n`、Python 编译检查和 `git diff --check` 通过。
- 真机打包尝试未进入设备执行：Xcode 当时没有 compatible physical destination，已配对的 iPhone 记录均为 offline、boot shutdown、DDI unavailable。第一次目标选择退化为 simulator，设备静态库与 simulator 链接被正确拒绝；随后确认不存在在线真机 destination 后停止，不把该结果记作 App、真机或 Phase 1 通过。忽略目录中的失败日志已脱敏。
- `build_app.py` 现在显式传入 `-sdk iphoneos`，防止离线设备标识被 Xcode 回退为 simulator；未签名的 `generic/platform=iOS` wrapper 编译和完整设备链接通过。当前 Xcode 同时报告没有已登录账号及匹配 provisioning profile，因此签名、安装和真机执行仍未完成。
- 真机恢复在线后，确认设备为 wired、paired、Developer Mode enabled 且 DDI 可用。由于 Xcode 未登录账号，自动签名仍不可用；本次仅复用本机已有且有效的开发描述文件和对应钥匙串证书，在本地验证 bundle、设备范围、有效期及证书匹配后手工签名、验证并安装。账号、签名标识、设备唯一标识、描述文件和私钥均未写入仓库或证据文件。
- 首次 hook-enabled 真机执行正确触发主错误 `-4016`，但报告 `cleanup_error=-4006`、`cleanup_status=6`。`-4006` 为 `OB_NOT_INIT`：失败点位于 `ObServer::init()` 的部分初始化阶段，统一 `wait()` 会经过尚未初始化组件的 stop 路径。修复仅在 server 初始化尚未完成且清理返回 `OB_NOT_INIT` 时将其视为已完成；完整初始化后的 stop 错误仍然保留为清理失败。该修复提交为 `f25621b67`，并增加对应回归契约测试。
- 修复后的 hook-enabled App 在 iPhone 17 Pro / iOS 27.0 通过负向验收：状态为 `Failed`，主错误 `-4016`，`cleanup_error=0`，`cleanup_status=7`，工作目录恢复成功，run ID、源码 build ID 和 hook mode 均与本次产物匹配；执行后没有新增 seekdb 崩溃或 Jetsam 报告。
- 随后关闭 `SEEKDB_IOS_TEST_HOOKS`，重新配置并完成 `seekdb_ios_link_check` 全量链接，产物 marker 确认为 hook disabled。普通 App 在同一新数据目录连续执行两轮，每轮 36 个 SQL step 全部成功，最终状态均为 `Stopped/result=0`、`sql_verified=true`、`cleanup_error=0`，持久计数 `previous_runs` 依次为 0、1；两轮后仍无新增 seekdb 崩溃或 Jetsam 报告。
- 脱敏后的本地原始证据保存在忽略目录 `build_ios_arm64/device-evidence/`；扫描未发现未屏蔽的设备唯一标识。受 Git 跟踪的本文记录可复现结果与限制，原始设备和签名元数据不进入版本控制。

## GitHub 追踪

远端仓库：`git@github.com:longdafeng/seekdb`；分支：`codex/iphone-arm64-port`。

- `1d600e19c`：ARM64 交叉编译脚本、CMake 适配、固定依赖构建和脚本测试。
- `0c34a7b6a`：实验性进程内生命周期接口及链接探针。
- 环境与变更文档、仓库记录规则由后续独立文档提交维护，提交号可用 `git log -- docs/developer-guide/zh/ios-change-log.md` 查询。
- 2026-09-21 提交前再次运行 6 项 Python 测试，全部通过；`bash -n build.iphone.sh` 和 `git diff --check` 通过。未重复宣称完整链接或真机测试通过。
- `AGENTS.md` 新增 iOS Change Traceability 规范，要求环境、代码及 CMake 变更和关键验证结果写入受 Git 追踪的文档。原始缓存日志不作为唯一记录；工具链和二进制构建缓存仍不提交。

## 环境设置与当前状态

| 项目 | 设置、作用及验证边界 |
| --- | --- |
| 工作目录 | `/Users/longda/work/repo/db/ob/github/seekdb.longda`；seekdb 源码修改、下载、缓存、构建输出均在此目录内。 |
| 主机 | Apple Silicon；本次读取为 macOS 27.0，build `26A428`。 |
| Xcode | 完整 Xcode 27.0，build `27A266a`，位于 `/Applications/Xcode.app`；iphoneos SDK 27.0。 |
| 工具选择 | 脚本设置 `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer`，支持调用者覆盖；未执行全局 `xcode-select --switch`。本次读取全局路径仍为 `/Library/Developer/CommandLineTools`。 |
| CMake | 本次读取版本 4.2.3。 |
| Rust | 固定工具链 1.98.1；已安装 `aarch64-apple-ios` 和 `aarch64-apple-ios-sim`。默认 `CARGO_HOME=deps/ios/cargo`、`RUSTUP_HOME=deps/ios/rustup`，支持显式覆盖；不修改全局 shell 配置。 |
| 宿主工具 | `--init` 从仓库固定的 macOS 依赖配置准备 Bison/Flex，不下载完整 LLVM 工具包；不会自行安装 rustup。 |
| 编译目标 | ARM64；默认 iphoneos、最低 iOS 18.0；模拟器为 iphonesimulator，独立 Rust target 和输出目录。 |
| 资源保护 | 默认 4 个构建任务；空闲空间保护阈值 10 GiB，可用 `SEEKDB_IOS_MIN_FREE_GIB` 覆盖。曾剩约 5 GiB，用户释放空间后继续；本次读取约 17 GiB。阈值不代表全量空间需求。 |
| 开发者工具权限 | 用户明确授权后，通过系统设置加入 `/Applications/ChatGPT.app`，界面验证 `ChatGPT` 开关为 on。此机器 Codex 集成于该应用，没有独立 `/Applications/Codex.app`；Terminal 开关未改变。尚未验证此设置是否解除 ICU 构建阻塞。撤销方式是在同一页面关闭 ChatGPT 开关。 |
| 真机与签名 | iPhone 17 Pro 已通过 Xcode 自动签名完成构建、安装和运行；证书及描述文件由系统管理且不进仓库。2026-09-24 已完成五轮 `Stopped/result=0` 的通用 SQL 与重复停止验证。 |

## 构建脚本与 CMake 逐文件记录

| 文件 | 变更及原因 |
| --- | --- |
| `build.iphone.sh`（新增） | 统一真机/模拟器配置、初始化、依赖构建、目标选择、最低版本、并行度和空间检查；支持 `--headers-prefix` 与 `--deps-prefix` 分离；使用 pipefail 保留失败，写入构建日志。 |
| `CMakeLists.txt` | 在 Env 选择宿主默认编译器之前加载 iOS 配置。 |
| `cmake/IOS.cmake`（新增） | 固定 ARM64，使用 xcrun 获取 Apple Clang、SDK 和 ld；try_compile 只生成静态库；映射设备/模拟器 Clang、Rust target。 |
| `cmake/Env.cmake` | iOS 显式使用目标架构 arm64。 |
| `cmake/Rust.cmake` | Cargo 增加 iOS `--target`，调整目标产物目录，传递绝对 SDKROOT 和 IPHONEOS_DEPLOYMENT_TARGET。 |
| `deps/external/cmake/CargoExternal.cmake` | 尊重显式 CARGO；外部 Rust 构建携带 iOS target；生成 CMake runner 保存多行环境值，避免 Makefile 被换行参数破坏，传播子进程失败。此通用 runner 也影响非 iOS 外部 Cargo 构建。 |
| `deps/external/cmake/Jemalloc.cmake` | iOS 显式 target/sysroot，清除继承的 CPPFLAGS/LDFLAGS；换行传递 `--with-jemalloc-prefix=je_`、`--host=aarch64-apple-ios`、`--with-lg-page=14`、`--disable-zone-allocator`。 |
| `cmake/IOSBoost.cmake`（新增） | 对 Boost 1.74 的 5 个 NumericConversion 头文件生成覆盖层，回移 1.85 的 integral_constant 枚举包装方式；不改原始依赖目录，避免新 Clang 拒绝越界枚举常量实例化。 |
| `src/oblib/CMakeLists.txt` | 分离公共头文件目录和库目录；iOS 使用 Boost 覆盖层；检测编译器支持的 warning 参数；对三项新诊断保留 warning 而非 error，调整 virtual-specifier 参数顺序；iOS 库目录使用 lib，链接完整 ICU data，扩展 Apple framework 平台选择。 |
| `src/oblib/lib/CMakeLists.txt` | iOS OpenMP 编译参数改为 Apple Clang 接受的 `-Xpreprocessor -fopenmp`；运行库仍待最终链接验证。 |
| `src/oblib/lib/compress/CMakeLists.txt` | 部分链接传递正确的 ios/ios-simulator、最低版本及 SDK 版本，避免误标 macOS。 |
| `src/oblib/lib/compress/zstd_1_3_8/CMakeLists.txt` | iOS 使用 Apple ld -r，并利用 private extern 局部化；避免调用 ELF objcopy。 |
| `src/observer/CMakeLists.txt` | Apple 链接选择器覆盖 iOS；新增 `seekdb_ios_runtime` 静态库与 `seekdb_ios_link_check` 链接探针，均 EXCLUDE_FROM_ALL。探针不是可交付 UIKit App。 |

## 源码、依赖与测试逐文件记录

| 文件 | 变更及边界 |
| --- | --- |
| `src/oblib/lib/allocator/ob_malloc.cpp` | iOS 不接管宿主 App malloc zone；配合禁用 jemalloc zone allocator，保留引擎显式分配路径。 |
| `src/share/ob_telemetry.cpp` | gethostuuid 限定 macOS，iOS 使用已有无机器 ID 路径，可显式提供 SEEKDB_TELEMETRY_INSTANCE_ID；未实现自动持久化 App UUID。 |
| `src/observer/ob_server_options.h` | 新增默认 false 的 `in_process_` 选项。 |
| `src/observer/ob_server.h`、`ob_server.cpp` | 保存 in_process 状态；该模式不启动信号处理线程、不启动无客户端退出监视；wait 停止后调用 stop，避免 `_Exit(0)`。普通命令行默认路径保留；运行验证仍待完成。 |
| `src/observer/ios/seekdb_ios.h`、`seekdb_ios.cpp`（新增） | 同步后台线程入口、原子停止请求和生命周期状态查询。仅支持每进程一次；使用沙箱绝对路径，创建 run/etc/log，改变 cwd，正常返回时恢复；1 GiB 内存、2 GiB redo、TCP 关闭、Unix socket `run/sql.sock`。启动失败的全局清理仍有风险；没有已验证的 SQL C API。 |
| `deps/ios-build/build.py`（新增） | 固定源码 URL/版本/SHA256，下载、构建、检查 ARM64 和 iOS 平台，输出 verified.json。源码修改仅落在仓库内下载解包目录；包括 zlib 旧 Mac 宏兼容修复。支持 10 项默认依赖，ICU 显式构建。 |
| `.gitignore` | 忽略 deps/ios 缓存与两个 Python 辅助目录的 __pycache__。 |
| `unittest/ios_build/test_build_iphone.py`（新增） | 参数路由、模拟器、依赖模式、失败传播和非法参数验证。 |
| `unittest/ios_build/test_cargo_external.py`（新增） | 实际 CMake runner 验证显式 Cargo、iOS target、多行环境及失败传播。 |
| `unittest/ios_build/boost_numeric_probe.cpp`（新增） | 数值转换及溢出探针，用于 iOS 编译检查和宿主执行验证。 |
| `unittest/ios_build/link_probe.cpp`（新增） | 链接进程内入口；使用 SEEKDB_IOS_TEST_DIRECTORY 指定路径，不能替代 App 验收。 |
| `docs/developer-guide/zh/ios-build.md`、`docs/developer-guide/zh/ios-change-log.md` | 使用说明、当前验证边界、逐项变更和后续记录入口。 |

默认依赖为 zlib 1.2.13、OpenSSL 1.1.1u、curl 8.12.1、Abseil 20211102.0、S2 0.10.0、CRoaring 3.0.0、liblzma 5.4.7、libxml2 2.10.4、protobuf-c 1.4.1、SQLite 3.38.1。精确来源及校验和以 build.py 的清单及 `deps/ios/iphoneos/build/*/verified.json` 为准。libxml2 已启用 LZMA；liblzma 从旧依赖版本 5.2.2 改为 5.4.7，旧源码下载不顺利，采用可获得且支持 CMake 的版本，仍需最终集成验证。

## 已执行验证与未完成项

- 此前引擎 7 个静态库、Rust sql-nio、jemalloc、上述 10 项依赖和进程内入口编译成功；平台检查为 iOS ARM64。多个静态库不能视为一个完整可运行引擎。
- 此前 6 项 Python 测试通过，Boost 探针通过 iOS 编译及宿主数值转换/溢出检查，zstd 合并对象内部符号局部化已检查。
- 纯 iOS 链接探针关闭 standby，仍缺 `libicui18n.a`；ICU、VSAG/OpenMP/BLAS 等剩余依赖、完整链接、App 签名、SQL、持久化和真机生命周期均未完成。
- `--headers-prefix` 目前复用宿主包公共头文件；库必须来自纯 iOS prefix。头文件复用仍须关注目标相关配置，不代表整个宿主依赖包适用于 iOS。

```bash
# 从 seekdb 根目录运行。初始化和构建详见 ios-build.md。
./build.iphone.sh --deps-only
python3 deps/ios-build/build.py icu
./build.iphone.sh --jobs 4 --target seekdb_ios_link_check \
  --headers-prefix "$PWD/deps/3rd/usr/local/oceanbase/deps/devel" \
  -- -DOB_ENABLE_STANDBY=OFF \
  -DCMAKE_C_FLAGS_RELWITHDEBINFO=-O2 \
  -DCMAKE_CXX_FLAGS_RELWITHDEBINFO=-O2
python3 -m unittest discover -s unittest/ios_build -v
bash -n build.iphone.sh
git diff --check
```

`-O2` 覆盖仅用于这些实际构建命令；standby OFF 是本地移动端链接探针选择，不是脚本默认值。以上记录命令不意味着每个命令已成功，尤其 ICU 和最终链接仍失败。

## 失败尝试与排查证据

1. 早期磁盘约 5 GiB 不足，停止大规模构建；用户释放空间后恢复。仅清理本轮失败的大型下载，不删除用户数据。
2. Rust 宿主 build-script 曾被 SIGKILL，重试后 sql-nio 编译成功；原始输出保存在 `build_ios_arm64/logs/sql-nio-build.log`。
3. ICU 69.1 需要先构建 macOS 宿主工具，再以 `--host=aarch64-apple-darwin --with-cross-build=...` 交叉编译。宿主 configure 的 conftest 被 SIGKILL；系统 amfid 日志显示 Code=-423，签名不被接受。临时 ad-hoc 与已有本地证书签名尝试未解决，未保留为正式方案；试验脚本留在 `build_ios_arm64/probes/host_compiler-signing-attempt.py`。未关闭全局 Gatekeeper，也未清除系统安全属性。现已设置开发者工具权限，效果待验证。
4. 宿主 VSAG 包二进制记录 `129b82c-dirty`；已取得 antgroup/vsag 的 129b82c 源码，公开 include/vsag 与依赖包头文件比较一致，但 dirty 实现差异未知，不能宣称源码完全相同。归档 `deps/ios/downloads/vsag-129b82c.tar.gz`，SHA256 `4cd2f2ba5f3fe894f9ebe0a943d2cb479234bfb5614a7cdc089981ce859365c6`。仍需移植上游 macOS brew、OpenMP、OpenBLAS/Fortran 构建逻辑。

## 日志索引与后续追加

原始日志优先查 `build_ios_arm64/logs/`：`engine-build.log`、`sql-nio-build.log`、`jemalloc-build.log`、`icu-build.log`、`link-check.log`。依赖构建证据位于 `deps/ios/iphoneos/build/`；ICU 宿主 configure 详情位于 `deps/ios/host/icu/config.log`。这些缓存日志可能被后续运行覆盖，关键结论应同步摘录到本文件，新的失败/修复应补充命令、日期及结果。

后续条目格式：`日期 → 文件/环境项 → 修改原因与具体参数 → 验证命令及结果 → 剩余问题 → commit（提交后补充）`。

## 2026-09-21：ICU、OpenMP 和数学库继续移植

- 开发者工具权限开启后，ICU 宿主 configure 和工具编译成功，不再遇到此前的 conftest SIGKILL。一次构建收到 SIGTERM 后增量重跑。随后目标工具 pkgdata.cpp 调用了 iOS 不可用的 system()；build.py 在目标 configure 增加 `--disable-tools`，宿主工具仍正常构建并通过 `--with-cross-build` 生成目标数据。ICU 69.1 的 icuuc/icui18n/icudata 均构建、平台检查、安装成功。
- ICU 的调用点：`src/sql/engine/expr/ob_expr_regexp_context.cpp` 用 uregex_open/find/appendReplacement 等实现 SQL 正则，设置时间和栈上限，并转换 Unicode 文本；对应 REGEXP、REGEXP_LIKE、REGEXP_INSTR、REGEXP_SUBSTR、REGEXP_REPLACE。本轮没有裁剪这些功能。
- `deps/ios-build/build.py` 新增显式 openmp 目标：LLVM OpenMP 17.0.6 与同版本 CMake 公共模块固定 URL/SHA256；识别 tar.xz，替换解包源码中的公共模块路径。关闭共享库、libomptarget、OMPT、hwloc，静态 libomp.a 已通过 iOS ARM64 平台检查并安装。[LLVM 构建说明](https://openmp.llvm.org/Building.html)。
- 新增 `deps/ios-build/lapacke/CMakeLists.txt` 与显式 lapack 目标：固定 LAPACK 3.12.0，仅编译 VSAG 使用的 sgeqrf/sorgqr/sgetrf/ssyev/sgesdd C 接口及工具函数；底层链接 Apple Accelerate，使用 LAPACK_F2C 匹配其旧接口。初次链接缺少 LAPACKE_get_nancheck，补入上游 lapacke_nancheck.c 后通过。没有编译或链接 macOS Fortran 库。[Apple 数学库说明](https://developer.apple.com/documentation/accelerate/blasparamerrorproc)。
- 新增 `unittest/ios_build/lapacke_probe.c`：macOS 宿主执行 QR 重构、LU、特征值和 SVD 检查通过；同一探针使用 iOS SDK 链接成功，vtool 显示 IOS/minos 18.0/sdk 27.0。尚未真机执行。
- 重跑完整引擎链接，已越过 ICU 缺失错误，当前首先缺少 cpuinfo（VSAG 依赖）。
- 新增 `deps/ios-build/vsag_packages.py`、`deps/ios-build/vsag/CMakeLists.txt`、`deps/ios-build/vsag/include/cblas.h`，并扩展 build.py 的显式 vsag 目标：固定 VSAG 及 fmt/spdlog/ANTLR/cpuinfo/json/thread-pool/tsl 源码；使用受 Git 追踪的 iOS CMake 适配层及 OpenMP/LAPACKE/Accelerate，保留上游 src 目标图。修改解包源码中的 OpenMP 参数。仍在验证；首次编译发现既有 Boost 头文件包缺少 dynamic_bitset.hpp，正在补全源码头文件，不能宣称完整 VSAG 或引擎链接成功。

新增依赖构建命令（从仓库根目录执行）：

```bash
python3 deps/ios-build/build.py icu
python3 deps/ios-build/build.py openmp lapack
python3 deps/ios-build/build.py --jobs 4 vsag
```

新日志位于 `build_ios_arm64/logs/`：openmp-build.log、lapacke-build.log、lapacke-host-test.log、vsag-build.log。真机检查当时仍只发现一个 iPhone 17 Pro 模拟设备，尚未识别到物理手机。

后续适配细节：

- 采用完整 Boost 1.74.0 固定校验和源码头文件，替代缺失 dynamic_bitset 的宿主精简头文件包；build.py 支持 tar.bz2。DiskANN 定义 BOOST_NO_CXX98_FUNCTION_BASE，使用 Boost 自带兼容分支处理 libc++ 已移除的 std::unary_function。
- VSAG 的 CBLAS 适配仅引入 vecLib/cblas.h，并增加 SDK 内的 Accelerate 子 framework 搜索路径，避免 Accelerate umbrella 与 LAPACKE 重复声明 Fortran LAPACK 函数的类型冲突。DiskANN 显式连接 fmt::fmt，获得 logger 所需头文件。
- `src/oblib/lib/CMakeLists.txt` 在 iOS ARM64 分支使用 libomp.a、liblapacke.a 和 Accelerate，移除该分支对 macOS Fortran/quadmath/gcc/OpenBLAS 库的依赖；macOS 原有库选择保留。仍待完整引擎链接验证。
- 新增 `unittest/ios_build/icu_regex_probe.cpp`，在宿主 ICU 上验证中文文本的 Unicode Han 属性正则匹配通过；同一程序 iOS 链接通过，平台 IOS/minos 18.0/sdk 27.0。探针首次启动未取得成功输出，显式再次运行后取得退出码 0 和成功信息；没有将首次调用算作成功。
- 6 项脚本测试再次全部通过，日志为 ios-script-tests.log；bash 语法和 git diff 格式检查通过。
- VSAG 适配补全 CRoaring C++ 头文件目录、ANTLR runtime/autogen 的目录布局；布局使用仅位于构建目录的符号链接，避免复制 pragma-once 头文件造成类型重复定义。fmt 头文件作为上游原有的公共 include 提供；Boost 兼容宏也传播给包含 DiskANN 头文件的 VSAG 对象目标。
- VSAG 自身生成的 version.h 与 ANTLR 的同名头文件冲突，给 vsag_static 优先指定其生成头文件目录，版本记录为 129b82c-ios。
- 用户确认已连接手机后，再查 devicectl、xctrace 和 USB 枚举仍未发现物理 iPhone；只看到模拟器。已提供手机端开发者模式开启步骤和 USB 直连排查建议，未把用户确认当成设备已被工具识别的证据。
- VSAG 随后编译成功，产出 libvsag_static、diskann、simd、io、cpuinfo、fmt、antlr4-runtime、antlr4-autogen 共 8 个静态库，逐个通过 iOS ARM64 检查并安装至 `deps/ios/iphoneos/devel/lib/vsag_lib`。verified.json 已生成。此处复用现有 CRoaring 3.0.0，而上游 VSAG 默认取 3.0.1；编译通过不表示完整向量检索回归已通过。
- `build.iphone.sh --deps-only` 的默认依赖顺序扩展为 14 项：原有 10 项之后依次构建 ICU、OpenMP、LAPACKE 子集和 VSAG；帮助文本同步更新。各项已分别验证，尚未在全新目录一次性执行完整 14 项流水线。

## 2026-09-21：真机接通与最终链接修复

- devicectl 已确认物理 iPhone 17 Pro、iOS 27.0、USB wired / connected / paired，开发者模式由 Disabled 变为 Enabled (1)。期间 USB 枚举一度丢失，重新连接后恢复；无需修改系统 xcode-select。钥匙串尚无 Apple Development 证书，已请用户在 Xcode Accounts 配置个人开发团队，未采集或提交凭证。
- VSAG 补齐后，完整链接暴露三类未解析符号：Abseil string_view、__kmpc_dispatch_deinit、nio_*。S2 上游 CMake 无条件强制 C++11，而 Abseil 使用 C++17，造成 string_view ABI 不一致。build.py 对固定版本 S2 的 CMake 标准进行受检查替换，统一为 C++17；S2 已重建、安装并通过 iOS 平台验证。
- build.py 将 OpenMP 和 LLVM CMake 公共模块固定到 21.1.8，记录下载 SHA256，并按版本隔离 OpenMP 构建目录。上游 21.1.8 的 kmp_dispatch.cpp 提供新 Clang 所需的 __kmpc_dispatch_deinit；旧 17.0.6 不提供。21.1.8 静态库已编译、安装并通过 iOS ARM64 平台检查，未自行添加替代运行库函数。
- src/observer/CMakeLists.txt 为 seekdb_ios_runtime 增加 PUBLIC sql_nio，沿用正式 Cargo 构建依赖和 Rust 系统库。首次正式目标构建中 thiserror 宿主 build-script 被 SIGKILL，保留 sql-nio-cmake.log，随后增量重试；不将首次尝试记为成功。
- 复现：python3 deps/ios-build/build.py --jobs 4 s2 openmp；随后运行 build.iphone.sh 的 seekdb_ios_link_check 目标，使用上述头文件前缀及 -DOB_ENABLE_STANDBY=OFF、两个 RelWithDebInfo=-O2 参数。最终链接结果将在本节追加。
- Rust 在新 rust-target 目录增量重试仍有多个宿主 build-script 被 SIGKILL。cmake/Rust.cmake 将 RUST_TARGET_DIR 暴露为 CACHE PATH，默认不变；本次使用 -DRUST_TARGET_DIR="$PWD/build_ios_arm64/rust-probe" 复用此前已成功编译的同一源码/目标产物，不将此视为全新构建通过。codesign 校验新宿主程序磁盘签名有效，但这不能证明系统运行策略允许执行。
- 完整链接前磁盘降至约 9.4 GiB，默认 10 GiB 保护正确终止。确认本次是增量链接、observer/SQL 库分别约 48/140 MiB 后，仅该次命令设置 SEEKDB_IOS_MIN_FREE_GIB=6，未修改脚本默认阈值。
- 最终 seekdb_ios_link_check 构建达到 100%，约 216 MiB；vtool 确认 IOS/minos 18.0/sdk 27.0，otool -L 仅包含 Accelerate、libSystem、Security、CoreFoundation、SystemConfiguration、libiconv、libc++ 等 Apple 系统库，三类未解析符号均已消除。日志 link-check.log；脚本测试 6 项通过，bash -n、py_compile、git diff --check 通过。没有将链接探针误记为 UIKit App、SQL 或真机运行成功。

## 2026-09-21：UIKit 真机测试 App

- 用户在 Xcode Accounts 完成登录后，读取到 Personal Team；起初钥匙串仍只有本地证书，随后通过 xcodebuild 的自动签名流程申请 Apple Development 签名。团队 ID 作为命令参数，不硬编码在源码中。
- 新增 unittest/ios_build/app 的 main.mm、Info.plist.in 和独立 CMakeLists.txt：UIKit 界面、后台专用线程、停止按钮、Documents/probe-status.json 状态记录。它只是测试包装，不声称 SQL 已验证。
- 新增 deps/ios-build/build_app.py：读取成功链接探针的依赖闭包、绝对化静态库路径、移除宿主 rpath、拒绝未知链接参数；生成仓库内 Xcode 工程，使用 Automatic 签名、允许 provisioning 更新和设备注册，codesign 验证后可按 --install 安装。证书私钥由 Xcode/钥匙串管理，不纳入 Git。
- 新增 unittest/ios_build/test_app_link.py，覆盖带空格路径、参数顺序、未知参数拒绝、缺少运行库拒绝。构建和安装日志位于 build_ios_arm64/logs/app-build.log；实测结果继续追加。
- 首次 Xcode 包装链接因其宿主库搜索路径选中了 Homebrew macOS libomp.dylib 而失败；提取器现将所有第三方 -l 参数解析为 iOS 前缀内的绝对静态库路径，仅白名单系统库保留 -l，并新增测试。避免仅依赖 -L 顺序。10 项脚本测试全部通过。
- Xcode 自动签名成功生成 Apple Development 证书；SeekDBProbe Release 真机构建成功，codesign --verify --deep --strict 通过，devicectl 确认 org.seekdb.iosprobe.longda 安装成功。描述文件匹配 App 和目标设备，get-task-allow 为 true，有效期至 2026-09-28。
- 首次启动返回 CoreDeviceError 10002 / FBSOpenApplicationErrorDomain Security，提示签名、entitlement 或尚未信任描述文件；本机签名检查通过且描述文件包含目标设备，已请用户完成手机端开发者信任，未将安装成功算成引擎运行成功。
- build_app.py 为后续 Xcode 构建指定仓库内 app/DerivedData，避免测试工程的派生数据使用默认位置；系统 Xcode/SDK 自身的共享缓存及系统凭证仍由 Apple 工具管理，不进入 Git。

## 2026-09-21：首次真机启动诊断

- 用户完成开发者信任后，devicectl 成功启动测试 App，但程序随即退出。尽管控制台报告 exit code 0，系统崩溃报告显示 EXC_BREAKPOINT / SIGTRAP，栈顶为 UIApplicationEvaluateRuntimeIssueForNoSceneLifecycleAdoption；没有把退出码 0 当作正常运行。
- main.mm 改用 UIWindowSceneDelegate 创建窗口和启动后台线程，新增轻量 UIApplicationDelegate；Info.plist 声明单 Scene 生命周期。此修复针对 SDK/iOS 27 的实际 UIKit 启动诊断；引擎尚未到达初始化，因此没有基于这次崩溃修改数据库逻辑。
- 原始报告和控制台位于忽略目录 build_ios_arm64/logs/device-crash.ips、device-console.log；报告中的设备和账号元数据不提交，关键错误和修复已在此受 Git 跟踪的记录中保留。
- Scene 修复版 Xcode Release 构建成功，但覆盖安装途中设备连接中断，返回 IXRemoteErrorDomain 6 / Connection interrupted；随后 devicectl 显示 unavailable，USB 枚举仍能看到 iPhone。此时不能确认修复版已安装或启动，正在恢复连接。日志 app-scene-build.log、device-console-scene.log。
- 设备恢复后 Scene 修复版重新安装、启动成功，Documents/probe-status.json 可读回，状态 Failed、result=-4024、sql_verified=false。界面生命周期问题已越过，数据库初始化仍失败。
- 通过 LLDB 连接真机进程，断点和返回寄存器确认 ObServer::init_opts_config 返回 -4024（OB_BUF_NOT_ENOUGH）；真机执行 sysconf(_SC_ARG_MAX) 返回 -1。ObCommonConfig::add_extra_config_unsafe 将该返回值直接作为最大长度，导致正常配置字符串被拒绝。
- src/share/config/ob_common_config.cpp 对非正的 sysconf 结果使用 256 KiB 有界备用上限，与 Windows 既有上限一致；正值平台保留系统上限。未禁用配置长度检查，未修改全局安全策略。增量构建日志 engine-argmax.log；修复后的真机结果待追加。
- ARG_MAX 修复版编译、签名、安装成功；真机不再返回 -4024，日志确认配置和引擎初始化完成，执行到首次 bootstrap 检查后返回 -4015。旧失败目录已包含数据版本标记，不是空库；不删除旧目录，后续使用独立目录继续验证。该结果证明配置限制修复生效，不代表建库或 SQL 成功。
- 读取真机日志发现内存配置自动取约 9.16 GiB；源码参数定义确认 memory_limit 已弃用且不影响内存预算。seekdb_ios.cpp 改为 memory_budget=1G，并显式设置 vector_memory_limit=128M，头文件说明改为逻辑预算而非 RSS 硬限制。
- main.mm 增加受限的 SEEKDB_PROBE_DATA_NAME 启动环境变量，只允许 Documents 下最多 64 字符的简单目录名，并在状态 JSON 中记录 data_name。用于保留旧失败目录的同时验证空库启动，不自动删除或重置用户数据；默认目录不变。
- 真机使用新目录 seekdb-budget-v1 后首次启动成功，状态文件为 Running / result=null / sql_verified=false；日志确认 server runtime ready，memory_size=1GB。这是首次原生 iOS 引擎启动证据，尚非 SQL 验收。
- 新增 unittest/ios_build/sql_probe.cpp/.h 与独立 seekdb_ios_sql_probe 测试静态库，通过现有内部 SQL proxy 验证 SELECT、DDL、DML 和读回，操作仅限 ios_probe.lifecycle 测试表。它不进入生产运行库；链接探针及 UIKit 测试 App 显式包含该测试库。
- main.mm 在 Running 后以第二个专用线程执行 SQL 测试，状态文件记录 sql_result、sql_verified、previous_runs；可用 SEEKDB_PROBE_AUTO_STOP=1 在测试返回后请求干净停止。持久化通过同一数据目录的计数读回验证，不能以新目录替代。
- SQL 测试静态库、完整链接和 UIKit Release 签名构建通过；10 项脚本测试通过。覆盖安装时设备虽然显示 connected，但安装无进展、文件和 details 接口超时，暂不能确认 SQL 测试版安装。旧进程控制台有 alloc_log_item -4013 和 signal 9；未取得对应 Jetsam 报告，不能断言是系统内存终止还是覆盖安装终止。后续需验证内存稳定性。
- build_app.py 为设备安装增加 120 秒超时，避免连接异常时无限等待；保留失败日志并由用户恢复连接后显式重试，不自动清除设备数据。
- 较慢的 SQL 测试版安装随后成功，设备 lockState 也恢复响应；启动时却明确返回 FBSOpenApplicationErrorDomain 7 / Locked。读回的 Running JSON 时间早于本次启动，属于旧进程证据，不能当作 SQL 测试版已运行。已请用户解锁手机。
- main.mm 在探针启动和运行时禁用本 App 的空闲自动锁屏，进入 Stopped / Failed 后恢复；只影响前台测试 App 的 idleTimerDisabled，不修改系统自动锁定设置，也不绕过手动锁屏。

## 2026-09-21：SQL、恢复与停止验证

- 解锁后 SQL 测试版在真机执行成功：SELECT 6*7、建库建表、计数写入和读回全部通过，sql_result=0、sql_verified=true、previous_runs=0。状态随后进入 Stopping。证据 probe-status-sql-current.json 和 device-console-sql-current.log。
- 停止阶段发生 SIGABRT；系统报告栈为 ObTabletMemtableMgrPool::destroy → obs_destroy_modules → ObServerRuntime::destroy → ObServer::destroy。断言要求池计数为零。第一次控制台命令也达到 20 秒观察超时，但实际崩溃报告明确为 SIGABRT，不能将它归因于控制台超时或宣称正常停止。
- 同一 seekdb-budget-v1 目录重新启动后，SQL 再次成功，previous_runs=1，验证已提交计数跨进程恢复；这是异常终止后的持久化恢复证据，尚不是干净停止后的重启验收。证据 probe-status-sql-restart.json。
- src/observer/omt/ob_server_runtime_controller.cpp 将 Memtable 管理池销毁移至 LS 和 storage meta memory manager 之后；这些对象持有池分配的 handle，必须先释放。保留原断言，不绕过销毁，也不强制清零计数。完整链接和真机停止结果待追加。
- 销毁顺序调整后完整 iOS 链接、UIKit Release 构建及签名通过，10 项脚本测试通过，git diff --check 通过；安装返回 CoreDeviceError 3002 / IXRemoteErrorDomain 6 / Connection interrupted，devicectl 随后显示 unavailable。已请用户恢复直连，尚未取得此调整后 Stopped/result=0 的真机结果。日志 engine-pool-order.log、app-pool-order.log；不将编译通过记为停止问题已解决。

- 恢复连接后，池顺序修复版安装运行成功，SQL 成功且 previous_runs=2。随后仍发生 SIGABRT，栈顶变为 ObLSService::destroy；源码断言要求 LS 已停止且不再持有 log stream。证据 stop-pool-order-crash.ips、probe-status-reconnect.json。
- ObServer::stop 在 in_process_ 模式下补充 server_runtime_controller_.wait()，位于 stop() 后、其他全局服务停止及资源销毁前。该等待复用现有 worker join 与 obs_stop_modules / obs_wait_modules 流程；命令行模式保留原行为，不禁用断言。完整 iOS 链接与 10 项脚本测试通过；真机验证结果待追加。未新增系统环境设置。

- stop/wait 修复版已安装并启动，SQL 成功读回 previous_runs=3；随后停止阶段仍出现 EXC_BAD_ACCESS / SIGBUS，触发线程为 TableGCTask，经 ObMemtable::safe_to_destroy 调用 ObLogHandler::get_max_decided_scn。说明仍有后台 GC 与日志资源生命周期问题，尚未正常停止。证据 runtime-wait-crash.ips；后续继续扩展通用 SQL 测试并诊断清理顺序。

## 2026-09-21：扩展 SQL 兼容性测试

- 真机测试曾发现 BIGINT UNSIGNED 错用 `get_int`，返回 -4001 (OB_OBJ_TYPE_ERROR)；显式 `u:` 类型现使用 `get_uint`。该经验保留在通用结果读取器中。
- 覆盖 JSON、MEDIUMBLOB、VARCHAR 数组、二进制排序规则、`SELECT FOR UPDATE`、显式事务提交/回滚、乐观版本条件、唯一键及 CHECK 约束。事务由 ObMySQLTransaction 固定同一连接；失败用例检查具体引擎错误。
- 设备不可用时，指定设备 ID 的 Xcode 构建返回 70；generic/platform=iOS 离线设备构建成功，但不能替代真机执行。

## 2026-09-21：GC 与日志流停止顺序

- 正常停止此前在 TableGCTask → ObMemtable::safe_to_destroy → ObLogHandler::get_max_decided_scn 发生 SIGBUS。源码确认 ObLSService::wait 直接 free_ls_，而 ObStorageMetaMemMgr::stop 不停止 GC，wait 才等待全部元数据释放并 join GC 定时器。
- obs_wait_modules 将 storage meta memory manager 的 wait 提前到 LS wait 之前，使延迟回收期间 LS/log handler 仍有效；保留元数据全部释放条件和 GC join，不屏蔽断言或丢弃待回收对象。该顺序仍需真机验证，包括检查是否存在等待依赖。
- 磁盘约2.3 GiB，清理本任务忽略目录中的可重建缓存：build_ios_arm64/rust-target（失败的旧 Rust 输出，当前使用 rust-probe）、deps/ios/iphoneos/build/icu/data、deps/ios/host/icu/data、deps/ios/iphoneos/build/vsag/CMakeFiles、deps/ios/downloads。保留源码、已安装 iOS 依赖、Rust 成功输出及日志，释放后约3 GiB。后续依赖全量重建需重新下载归档/生成这些缓存。
- 使用既有增量命令、SEEKDB_IOS_MIN_FREE_GIB=3、目标 seekdb_ios_sql_probe 构建通过，并用 generic/platform=iOS 构建签名 App。
- 修复版签名、安装、启动成功；首轮状态 `Stopped/result=0`、SQL 测试通过、previous_runs=6，系统没有新增 SeekDB 崩溃报告。这是一次正常停止证据，不足以证明重复稳定性。
- 同目录再次启动后 previous_runs=7、SQL 再次通过，证明首轮正常停止后的数据保留。第二轮状态停留 Stopping 且时间不刷新；LLDB 显示引擎线程位于 `prepare_stop` 等待阶段。不能据此断言 GC 仍失败，也不能宣称第二次正常停止完成。

## 2026-09-24：上游同步与产品中立测试

- 分支已 rebase 到 `upstream/master` 的 `834bbee1e`。冲突处理保留上游事务回调前释放外层 latch 的语义，同时重放 iOS 生命周期、停止顺序和测试变更；未用整文件覆盖掩盖上游修改。
- SQL probe 收敛为 36 步通用套件。`ios_probe.lifecycle` 保留跨进程计数；每轮只清理无外键的 `feature_matrix` 与 `feature_event`。UIKit 将逐步 JSONL 写入 `Documents/sql-probe-results.jsonl`，只有最终 `complete=true/result=0` 才设置 `sql_verified=true`。
- 删除旧专属 schema、runner 和历史设备结果，不把旧结果重命名为通用证据。CMake 的 `seekdb_ios_sql_probe` 仅编译 `sql_probe.cpp`。
- 主机脚本验证为 11 项 Python 测试、`bash -n build.sh build.iphone.sh` 与 `git diff --check`。产品中立性检查确认 Git 跟踪内容不包含已移除套件的名称或旧目标标识。
- `sql_probe.cpp` 的结果读取消除了整数下标重载歧义，并显式使用 `sqlclient::ObMySQLResult`。fixture 清理不再错误地要求 `DELETE` 影响零行，因此同一数据目录可重复运行；该缺陷曾使中间两轮分别在 `fixture.clear_events` 和 `fixture.clear_matrix` 返回 `-4016`，修复后未复现。
- 使用全新 Rust target 目录为 `aarch64-apple-ios` 构建 `libsql_nio.a` 成功。macOS 主机上的 Cargo build-script、测试和 Clippy 驱动在当前 Codex 进程责任链下会被系统以 137 终止；用户授权启用 Developer Tools 后，使用仓库忽略目录中的 LLDB runner 完成 Rust 3 项单元测试、doc-test 和 `cargo clippy --all-targets -- -D warnings`。runner 只是本机验证绕行，不是受 Git 跟踪的构建要求。
- `seekdb_ios_link_check` 增量完整链接通过；产物为 ARM64 Mach-O，平台 IOS、最低版本 18.0、SDK 27.0，仅依赖预期的 Apple 系统动态库及 framework。随后 UIKit App 完成签名、安装和真机启动。
- 设备数据目录 `ios-generic-20260924` 共取得五轮完整成功证据：轮次 1、4、5、6、7 的 `previous_runs` 依次为 0、1、2、3、4；每轮 36 个 step 全部 `result=0`，最终记录为 `complete=true/result=0`，状态均为 `Stopped/result=0` 且 `sql_verified=true`。重复停止约需 32 秒，短于该周期的轮询不能判为失败。
- 2026-09-24 验证后未产生新的 SeekDB Probe 崩溃报告；设备上可见的六份报告均为 2026-09-21 的历史文件。前后台、锁屏恢复、内存压力和完整向量功能仍未覆盖。
- 当前源码树没有常规 C++ 全量测试入口所需的 `unittest/CMakeLists.txt` 与 `all_tests_main.cpp`，且本机 Linux 容器运行时不可用，因此没有宣称完整 C++/Linux 测试通过。已执行的主机脚本、Rust、iOS 链接与真机测试边界如上。

## 2026-09-24：分层测试语料清单

- 新增 `unittest/ios_build/generate_test_inventory.py`、`test_inventory.py` 与受 Git 跟踪的 `ios-test-classification.json`。生成器以 `git ls-files -z` 为唯一文件来源，并直接导入现有 seekdb mysqltest runner 的 `discover_cases`，没有另写 psmall YAML 选择语义。
- 当前修订发现 283 个 active mysqltest，其中 272 个由 CI psmall 配置选择；`tools/obtest/t/**` 下 500 个 legacy case；3 个 Rust `#[test]`；9 个当前无常规 C++ target 的 `TEST`/`TEST_F`；5 个 iOS probe。主机 Python 用例数量随本次新增清单测试增加，始终按已跟踪文件动态核对。
- 每一行生成结果包含稳定 ID、源码路径、执行层、适用状态、来源 commit 与 corpus digest。排除项和阻塞项必须分别给出明确原因，设备等价映射必须存在且无环。生成结果写入忽略目录 `build_ios_arm64/generated/`，只证明语料发现与分类完整，不证明设备或主机执行通过。
- TDD RED 为生成器不存在；实现后 focused 清单测试通过。完整 host iOS Python suite、双次生成字节比对与 `git diff --check` 在提交前执行并记录最终结果。本次没有新增或记录设备、签名、团队或账户唯一标识。
- 规格复审后收紧清单契约：283 个 active mysqltest 与 9 个 orphan GTest 在 manifest 中逐 ID 记录 reviewed decision，500 个 legacy obtest ID 全部物化并做精确集合校验；corpus default 不再被视为上述语料的审核证据。
- 正式生成默认绑定 HEAD：相关受跟踪输入存在 staged/unstaged 改动或 active mysqltest 存在未跟踪文件时立即失败。字段类型、枚举、null、相对路径、reason 互斥、commit/digest 格式均增加负向测试。常规 C++ target 数量改为从受跟踪的非 iOS unittest CMake 定义派生，不再读取 manifest 字面量。
- 官方 focused 命令修正为 `python3 unittest/ios_build/test_inventory.py -v`；仓库目录 `unittest/` 与 Python 标准库包同名，不能使用 `python3 -m unittest unittest.ios_build...`。
- HEAD 绑定复审补充 staged deletion 回归：relevant 路径现在从 HEAD 与 index 的并集派生，因此已从 index 删除但仍属于 HEAD 的 Rust、iOS、host Python、mysqltest 等输入不会绕过 clean gate。

## 2026-09-25：mysqltest evidence 与真机契约收紧

- host mysqltest runner 直接生成带 canonical digest 的 slice/merged evidence，绑定
  source commit、完整 mysqltest source/result/include/SQL corpus、host runner/config/
  parser 以及 seekdb、obclient、mysqltest 三项 binary identity。iPhone host gate
  只接受精确有序的 272 项 CI executed case，拒绝手写最小 aggregate、缺项、额外项、
  tamper、旧 commit/corpus 和 build identity 不一致。
- corpus 与 host evidence 改用 bounded `O_NOFOLLOW` regular-file 读取，并在一次 fd
  生命周期内核对 identity；parser 在一次分类中缓存同一 source/result/include
  bytes，避免路径检查后换入其他内容。host result 路径只从环境读取，不进入 argv。
- 显式 mysqltest suite 在任何设备发现、签名、build/install 前验证 host evidence；
  缺失或损坏归类为 evidence/infrastructure 并停止。真机 `empty_table` 适配器在
  server modules 未 ready 时明确失败，空结果精确检查 ordered `nr,b,str` metadata
  与 `OB_ITER_END`，不再以仅列数或零执行作为通过。
- 本轮仅执行 host focused/full tests 和静态检查，没有运行真机、签名或安装。
- 第三轮复审删除手工准备 host JSON 的默认流程。同一次受锁 standalone run 现在以
  本地 seekdb/obclient/mysqltest executable 为输入，直接调用 tracked host runner
  完整执行 272 case 后 merge；gate 随后重新 hash 实际 executable 并核对 evidence。
  evidence/source/corpus/build/run/case-list digests 同时进入 artifact、checkpoint 和
  config fingerprint，resume 不能换证据后跳过已通过 gate。
- slice merge 和最终 evidence 均使用 bounded nonblocking `O_NOFOLLOW` regular-file
  stable read，拒绝 FIFO、symlink、oversize、identity change 及递归 JSON 资源异常。
  `empty_table` 空结果不再调用 row-dependent column count，只从 result-set field
  metadata 断言 ordered `nr,b,str`，再要求 `next()==OB_ITER_END`；row-null、错误标签
  和错误终态均有 host C++ contract 回归。
- 第四轮复审使 resume 按 host gate case 状态分流：passed 只从同一 locked run 目录
  重载 evidence，并重新核对 checkpoint identity、run UUID、当前 corpus/source 和
  当前本地 binary bytes；pending/failed 才重新执行 272 case。这样既不重复已通过
  的长耗时 host suite，也不能换 evidence 后跳过。
- `mysqltest-host`、slice、tmp、log、instance 等目录逐段通过 descriptor-anchored
  `O_NOFOLLOW` 创建/打开，预建 symlink 或非目录立即失败。tracked runner 的 run 与
  merge 子进程共用 absolute deadline，并各自在独立 process group 中运行；timeout、
  SIGINT 或异常会先 TERM 整组、有界 drain，再 KILL/reap，避免遗留 seekdb/mysqltest。
