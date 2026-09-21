# iOS 移植：环境与变更记录

本记录覆盖截至 2026-09-21 的本轮移植，基线为 `6aa8d1548a3136d37017121401f5fab4ecd9bea0`。修改在 `codex/iphone-arm64-port` 分支按功能提交；编译成功不表示已在 iPhone 运行。入口说明见 [ios-build.md](ios-build.md)。

## 记录规则

后续每次环境设置、源码、构建脚本或 CMake 变更，同步记录日期、文件/设置、原因、具体参数、影响范围、复现命令、验证结果和剩余问题。失败尝试及撤销原因也保留。按独立功能提交时补充 commit ID，不把机器缓存、证书或密钥提交到仓库。日志与生成产物保存在仓库内；本记录及构建脚本纳入版本控制。

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
| 真机与签名 | 用户表示可以连接 iPhone 17 Pro；尚无成功安装/运行记录。既有本地 `QuickLang Local Development` 身份不等同于可用的 iOS 开发签名团队。 |

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

新日志位于 `build_ios_arm64/logs/`：openmp-build.log、lapacke-build.log、lapacke-host-test.log、vsag-build.log。真机检查仍只发现名为 QuickLang iPhone 17 Pro 的模拟设备，尚未识别到物理手机。

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
- 新增 unittest/ios_build/app 的 main.mm、Info.plist.in 和独立 CMakeLists.txt：UIKit 界面、后台专用线程、停止按钮、Documents/probe-status.json 状态记录。仅测试包装，不替代 QuickLang App，不声称 SQL 已验证。
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
