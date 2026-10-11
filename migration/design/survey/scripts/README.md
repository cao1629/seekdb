# 调查脚本

设计文档的 7 份调查备忘（`../*.md`）用到的计数和分析脚本。都是标准库 Python，只读，在仓库根目录运行，统一用 `python3 -I`。

| 目录 | 对应备忘 | 用法 |
|---|---|---|
| `ir-graphs/` | `ir-graphs.md` | 多数脚本直接扫 `src/`；附录给出了 grep 版的复现命令 |
| `memory/` | `memory.md` | 第一个参数是去掉注释的源码镜像（见下），附录逐个说明了脚本 |
| `concurrency/` | `concurrency.md` | 先 `python3 -I concurrency/mirror.py . <镜像目录>`，其余脚本以镜像目录为参数；§2.0 有说明 |
| `storage-txn/` | `storage-txn.md` | 第一个参数是镜像目录；§2.0 有说明 |
| `layering/` | `layering.md` | `python3 -I layering/<脚本> .`；用到 `build_release/` 里的目标文件和 `migration/depmap/` 的输出 |
| `parser-generators-thirdparty/` | `parser-generators-thirdparty.md` | §2.6 的 [C1]–[C8] 说明了每个脚本 |
| `conventions/` | `conventions.md` | 脚本里写死了 `/tmp/conv-survey-7d907abfa-38738/stripped`（镜像）和同一目录下的 `scripts`（`sys.path`）；复现时改成自己的镜像路径 |

**镜像**：去掉注释和字符串、保留行号的源码副本，由 `migration/feasibility/scripts/arch/strip.py` 生成：

```sh
python3 -I migration/feasibility/scripts/arch/strip.py "$PWD" migration/feasibility/results/arch/gen.ignore <镜像目录>
```

脚本是调查 agent 写的，按"只读、不入库"的规则写在临时目录里，之后原样复制进来，以便备忘里的数字可以复现。正则计数都是近似值，以备忘里的说明为准。
