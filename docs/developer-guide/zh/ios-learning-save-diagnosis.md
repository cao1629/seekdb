# 2026-10-07 iPhone 学习进度保存失败分析

## 结论与证据边界

现有证据优先指向QuickLang背诵错题提交的版本竞态，未找到与失败时刻对应的seekdb SQL错误、磁盘满或内存不足证据。尚未从手机获得具体LONGTERM_VERSION_CONFLICT等应用错误码，因此这是高优先级原因，不声明截图根因已完全确认。此次只读收集，不附加调试器、不重启/终止App、不修改数据库或QuickLang源码。20G配置操作仍未完成，与此次分析无关。

## 手机日志

原日志仅保存在ignored `build_ios_arm64/quicklang-save-diagnosis/seekdb-log/seekdb.log`，154424718 bytes。可见时间范围2026-10-07 10:40:49至12:01:48。截图11:57落在该范围，18:07不在范围，截图日期未经确认。

应用连接session_id=84有15次rollback：11:56共8次、11:57共1次、12:01共6次。11:57:18.558420的rollback_tx ret=0、has_write_state=false、op_sn=3；15次均无写状态，事务持续11.010至33.079ms。abort_cause=-6002表示事务已回滚，不单独证明内核异常。形态与应用读取后校验拒绝相符，不能据此识别所有应用异常。

11:57:18附近redo统计容量2048MiB、使用90MiB，并非日志盘用尽。更早11:34后台freeze有OB_TIMEOUT、11:46后台向量管理有OB_TRANS_TIMEOUT；未建立与11:57应用失败的因果关系。启动阶段的计划缓存未收录、缓存miss等负返回码也不能全部当作SQL失败。

App容器Library和Documents列表未发现独立QuickLang应用日志文件，已有本地构建/调试日志也未找到此次具体应用错误码。native.rs只把MySQL数字错误码输出到stderr，不持久化原始消息；Spelling错误捕获对用户显示通用保存失败。未收集新控制台流以避免重新启动或附加。

## 代码链

QuickLang源码基线a3e4f21c，文件仅只读。

- `src/ui/src/shared/features/spelling/Spelling.tsx:249-265`：test阶段计时器每秒调用setSaved并持久化elapsed。
- `src/crates/storage-seekdb/src/app_storage.rs:217-240`、`user_documents.rs:329-341`：app_state_write更新用户文档，revision加1。
- `Spelling.tsx:348-389`：错题首次提交固定operationId、expectedVersion；同一单词/轮次/答案失败重试保留pendingError，沿用旧expectedVersion。
- `src/ui/src/shared/native/appState.ts:29-44`：invokeAppStateCommand不等待writes队列；普通写入另由writeAppState串行。
- `src/crates/storage-seekdb/src/longterm_v2/ordinary.rs:153-165`：读到当前用户文档后校验version，不一致直接返回VERSION_CONFLICT；domain/src/longterm.rs:29定义为LONGTERM_VERSION_CONFLICT。
- 上述错误发生在prepare_card及写入前，会由事务包装正常rollback，与手机无写状态回滚形态吻合；文档校验错误也可能产生相似形态，仍需具体应用错误码区分。

## 隔离复现与建议

通过本地esbuild打包实际appState.ts，在本仓库ignored目录运行createAppStateRepository，替换IPC为模拟后端：计时写入开始→错题捕获缓存版本0→写入提交版本1→错题校验失败。该复现验证队列与版本竞态，不等同真机或真实seekdb端到端复现。

建议QuickLang把计时保存与错题提交放入同一排序机制，在真正执行时获取版本；计时写入应在提交期间协调暂停/合并。收到版本冲突后，应先核对幂等回执及当前题目，再安全刷新/重建请求，不能盲目复用过期expectedVersion或换operationId造成重复记账。补充隐私安全的命令名、错误码、关联ID日志，并覆盖计时并发、失败重试和断点恢复。不要先调整seekdb内存/磁盘参数来掩盖应用状态冲突。
