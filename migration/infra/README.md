# 基础设施阶段（1d）

> 依据：设计文档 `migration/RULEBOOK.md` v0.2（2026-10-11 签字）§0.2 的 1d 和 D16。基线 `7d907abfa`。
> 1d 在第 2 步之前写出并测试第 3 步要用的全部基础设施。顺序：先写 API 文档（本目录 `api/`），审查之后冻结，再实现、配测试。

## 1. API 文档

每个模块组一份：`migration/infra/api/<编号>-<名字>.md`。API 文档是第 2、3 步实现者的只读上下文，也是 1d 实现者的规格。

**原则**（RULEBOOK §0.2）：被替换的 C++ 基础设施保留 C++ 的类型名、方法名和错误码，只把签名按所有权改写；改写后的签名写进 API 文档。翻译者照 C++ 的调用写，不自己挑 crate。

**每份文档的结构**：

1. **范围**：本模块取代、重写或包装的 C++（文件、类、宏），逐个列出对应的单元（代表文件路径）和处置（`replace:<模块>` / `rewrite:<模块>` / `keep-c` / `keep-cxx:<shim>` / `generate:<生成器>` / `data:<转换>`）。只读 C++ 源码得出，给 file:line。
2. **Rust API**：模块路径、类型、trait、函数、宏的签名（Rust 代码块，写到能编译的程度，函数体可以省略），每一项注明对应的 C++ 名字；错误码沿用 C++ 的 `OB_*`。按 RULEBOOK 的改名表（§4）取新名字，表外的一律保留 C++ 名。
3. **语义**：必须与 C++ 一致的行为点，每点给 C++ 的 file:line 证据（返回值、错误码、边界条件、并发语义、内存序、策略常数）；与 C++ 不同的地方只能是 RULEBOOK 已经接受的偏差（A.4）或 `FIXED(port)`，要点名。
4. **unsafe**：哪些地方需要 unsafe（只能在 RULEBOOK §3 的白名单路径里），每处的安全论证要点；对外暴露的 API 必须是安全的。
5. **测试**：要写的单测、loom 测试、Miri 测试、以 C++ 为准的金标准测试（golden 文件怎么从 C++ 产出），列成清单。
6. **翻译者怎么用**：5–15 个"C++ 写法 → Rust 写法"的对照例子，取自真实代码（给 file:line）。
7. **依赖**：用到的白名单 crate（RULEBOOK §1）和其他 1d 模块。
8. **开放问题**：RULEBOOK 没有决定、需要人决定的问题，每个给推荐项和备选。没有就写"无"。

**规则**：

- 只读源码和 RULEBOOK；只写自己那一份 API 文档（以及 `migration/infra/api/` 下自己的附件，如需要）。不改 RULEBOOK，不改别人的文档，不做 git 的写操作，不构建项目。
- 与 RULEBOOK 冲突时以 RULEBOOK 为准；发现 RULEBOOK 自身有错，写进文档第 8 节，并在 `migration/infra/amendments.tsv` 追加一行（`doc	section	problem	proposal`），排队给人。
- 用中文写，术语与 RULEBOOK 一致；代码、标识符、路径照原样。
- 不确定的计数写"约"，并说明怎么数的。

## 2. 模块组

| 编号 | 名字 | 范围（RULEBOOK 章节） |
|---|---|---|
| 01 | `mem` | `oblib::mem`：arena、`MemCtx`、`MemBudget`、全局分配器、分配失败（0.7、§2.4） |
| 02 | `sync` | `oblib::sync`、`oblib::atomic`：ObLatch 的移植、各种锁、原子工具（0.8、§2.7） |
| 03 | `thread-signal` | `oblib::thread`（含栈）、`oblib::signal`、`oblib::arch`、`main` 的开头（0.5、0.8） |
| 04 | `error-log-fmt` | `oblib::error`、`ob_macros`、`oblib::log`、`oblib::fmt`、警告缓冲区、`ObToString`（§1、§2.1–2.3、§2.11） |
| 05 | `containers` | `oblib::container`、`oblib::hash`、`oblib::queue`（§2.5、§2.7） |
| 06 | `string-num-sort-serial` | `oblib::string`、`num`、`ctype`、`sort`、`serialization`、`pod`、`slot`、`prelude`（0.10、0.11、§2.5、§2.11、§2.12） |
| 07 | `sql-ir-frame-datum` | datum、object、帧模块、`query::ir_id`、`QueryArena`、`RtExpr`、`ExprOp`/`OpState`（0.6、§2.6、§2.12） |
| 08 | `storage-core-kvcache` | `storage_core`、`share::cache` 的 KV cache（0.8、0.9、§2.8） |
| 09 | `parser-sys-generators` | `parser` crate、各 -sys 和 shim、生成器（0.12–0.14） |
