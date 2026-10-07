# 2026-10-07 动态 framework 稳定性实测

引擎源码：`6f902fdab24ccefdde97c991b50faf551928b604`，测试源码：
`9d149628cd45c839b4ed2d8c822c75c440bc21ce`。使用原已验收 framework，
未修改生产引擎；实际 Connector/C Unix socket，禁用测试 hooks，无调试器。

| 平台 | 持续秒数 | 事务迭代 | 并发读取 | 连接建立次数 | 预热内存 MiB | 采样峰值 MiB | 最大迭代耗时 ms | 持久计数 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| iPhone 17 Pro / iOS 27.0.1 | 600.250 | 2933 | 5822 | 182 | 163.283 | 213.768 | 4.258 | 1467 |
| arm64 模拟器 / iOS 27.0 | 600.051 | 2901 | 5721 | 179 | 162.175 | 215.534 | 12.576 | 1451 |

每个平台共5轮进程启动：首轮持续600秒、其后4轮读取已提交counter，
分别始终为1467和1451。每轮77项基本接口/生命周期断言通过，complete、
passed、worker_exit均true；持续测试的reader线程也成功join。事务交替提交/回滚，
固定一行数据，所有期望计数检查通过。峰值相对60秒预热基线增加约50.5/53.4 MiB，
低于本次256 MiB验收限额。原始10秒采样保留在run-1.json；采样峰值并非瞬时峰值。
runner结束只终止自身Probe进程。记录包含全部10份原始小JSON和汇总，
未收录证书、profile、设备标识、App容器路径或大型日志。

## 验证范围与未通过项

- 14项framework宿主门禁通过（新增稳定性8项、原动态证据6项）。
- 扩跑iOS宿主suite：347项，338通过、1跳过、3失败、5错误，不能声明完整suite通过。
  5项错误和1项失败位于test_device_registry：模拟设备启动诊断为
  `device launch failed: iPhone remained locked`，包括锁屏重试、durable SQL轮次恢复和
  unknown launch错误文本检查。这些是host模拟测试结果，不是本次真机锁屏或崩溃证据。
  另2项失败为test_mysqltest_parser的FIFO/symlink/超大JSON拒绝检查、
  test_product_neutrality的上游来源和集成审计中产品名检查。本轮未修复这些独立问题。
- 本次仅证明固定小数据集、前台10分钟、有限并发和5轮进程启动。
  未证明24小时运行、后台/挂起恢复、高并发、大数据、QuickLang整个App稳定性，
  也不等同严格无内存泄漏证明。最终close后的同进程再次启动仍不支持。

复跑命令和阈值见`docs/developer-guide/zh/ios-build.md`的稳定性测试章节。
