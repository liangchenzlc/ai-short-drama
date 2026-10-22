# BeefTV 私人历史引用批量权限读取审计

日期：2026-10-11。R22 的到期快照重复查询已修复；R22 和完整 M0–M5 仍未完成。
本片零供应商调用，不作为新的 Agnes 或用户同版部署验收。

## 问题与限定实现

旧 1000 节点快照为 4021 条 SQL、p95 16197.8130ms。剖析显示每条私人历史路径
重复读取 Project、ProjectCanvas、项目资格和资源当前行；普通资源已批量为两条。
本片仅修改 `db/access.py` 的画布历史分支并新增
`tests/integration/test_canvas_snapshot_reference_guard.py`。历史计划曾限定不修改
全局访问层文件；本片先说明实测原因和最小范围，再修改其中的限定分支。
没有 ORM、DDL、HTTP、Service、DAO、前端、依赖、固定源或 R23 候选变化。

优化仅适用于本次 `session.new` 中本人新增、`owner_kind="revision"` 且
`revision_id` 非空的私人 media/binary 引用。先按项目 ID 调用原 `require_project`，
保留项目/成员锁，再分模型、升序 ID、每批 500 条执行 scoped `FOR SHARE`。
每条作者、工作台类型、画布归属、资源范围和原 `allowed_scopes` 检查仍执行。
每个路径及 slot 独立保存；maps 只存在于当前 flush，不跨事务或 Actor 复用。
pending Project 或实质 dirty/deleted 父表、成员、资源整批回退原路径；pending
canvas/resource 保留原对象。普通引用及修改、删除继续原检查。

## 红绿与边界

红批 **2 failed / 18 passed / 0 skipped，86.06s**。两类 100 路径场先完成字节、
slot、历史与权限后置，最终在新测试第 310 行的 SQL 门禁失败：

```text
test_due_snapshot_keeps_each_private_path_without_per_path_guard_queries[100-media/binary]
AssertionError: 同一项目/画布/两资源的快照 guard 不得逐私人路径重复读取父表或资源
projects=206, project_canvases=103, media_files/canvas_binary_resources=101
assert 410 <= 24
```

绿批 **32 passed / 0 skipped，112.24s**。2/100 路径 × media/binary 四场均为
14 条 guard SELECT（项目8、画布4、资源2），两条资源当前读实际执行。
额外覆盖错误作者、跨项目/画布、他人私人资源、整批回滚、后续 flush 删除、
换 Actor、撤权后新事务、pending 与 mixed dirty/deleted 兼容。四个真实 RR 场
在另一事务提交新资源后普通读取仍为空，guard 当前读成功；501 不同身份正常/
缺失尾项四场完整校验 500+1 两批，缺失由 guard 拒绝且无部分引用。

RR、501 身份与混合回退资源仅为 guard-only ORM 夹具，未归档实际文件，不计
独立媒体性能。正常四场实际使用 HTTP、账号/CSRF、隔离 MySQL、MinIO，核对
原字节、独立路径、作者私人可见及共享作品三方权限。

## 原三档性能复验

原 `test_canvas_reference_performance.py` 未改，独占运行，各档两轮预热、20轮测量，
复用两个实际 PNG，保留20份历史及40/2000/20000条私人路径。连同精确撤权补验，
perf-a **5 passed / 0 skipped，292.46s**。14条 guard SELECT 与下表全部 SQL 不混用。

| 图片节点 | 快照 p95：cache-c → 本片（ms） | 快照全部 SQL | 到期保存 p95：cache-c → 本片（ms） |
| --- | --- | --- | --- |
| 2 | 118.0544 → 117.5850 | 29 → 25 | 429.8664 → 419.4967 |
| 100 | 1955.8509 → 215.7828 | 421 → 25 | 2262.2857 → 677.0897 |
| 1000 | 16197.8130 → 1113.2542 | 4021 → 25 | 17290.8302 → 2156.7638 |

到期保存均71–75 SQL；普通保存仍46 SQL，普通快照资源查询两条，普通保存历史
引用载入零、未变引用零 UPDATE，独立 slot/列/ID 保持。首次建图未计入p95。
其他操作有波动：1000档普通保存1880.7512→1998.6069ms、当前读取
939.9644→945.6873ms、历史列表809.4428→367.3681ms、单份历史读取
277.9927→773.4596ms，SQL各保持46/13/7/8。保留全部样本，不称全部提速或
生产性能承诺；未测浏览器、Worker、部署或模型。

## 回归、版本与清理

- 完整unit/API：**2463 passed / 0 skipped，473.88s**，工作区独占临时目录。
- 相邻引用保存、媒体定位、绘图历史、工作区：**25 passed / 1 skipped，93.97s**。
  唯一跳过为精确撤权场缺少opt-in，已在perf-a补验通过。
- 全仓Ruff check通过；本片两文件format通过。全仓766份已格式化，旧
  `scripts/apply_assembly_migration.py` 第16/23行混合换行仍失败，SHA与起始基线相同。
- fast-a因本片错误导入中止，已将 `CanvasUserMediaReference` 改从实际
  `domain.canvas` 导入；fast-b因外部pytest Temp的WinError5中止，未删除外部目录。
  两批不计通过，最终fast-c完整通过。绿批后Ruff仅统一行尾；性能和相邻集成
  实际运行最终格式版本。
- 四任务MySQL容器日志均有cleaned，最终同前缀容器零；fixture清理随机库和任务桶。
  没有新正式八角色、浏览器服务器或持久进程。原PID28808/28840启动时间仍为
  2026-10-08T02:00:14.4573905Z / 2026-10-08T02:07:23.8222398Z，两固定源干净、HEAD保持。

最终SHA256：

```text
db/access.py: ef9a50b32fbfa4d3d183240f34c50bf2dda5d9d25e94334adf1f117080e35725
新回归: 41786f63f0157972748875696b575af2816316565d64f44d9a8fd28dc95c57fb
旧混合换行脚本: 621bfee6009e194ceaee8e30b8328f343ff05d150f16196e2f6cbda42d3122f4
```

Service、DAO及原性能测试SHA与cache-c一致；旧32份冻结源码保持，新增回归另冻。
日志 `backend/.runtime/snapshot-reference-guard-{red-a,green-a,fast-a,fast-b,fast-c,adjacent-a,perf-a}.log`
为PowerShell UTF-16 BOM；各pytest目录JSON为UTF-8。红批源码另存在
`.runtime/snapshot-reference-guard-red-source.py`。性能完整报告在
`.runtime/output/snapshot-reference-guard-perf-a/`，旧cache-c目录保留；均忽略、不提交。

在 `backend/` 复验，使用新临时目录和独占MySQL包装：

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:RUN_CANVAS_RESOURCE_MINIO='1'
$env:SNOWFLAKE_WORKER_ID='963'
.venv/Scripts/python.exe ../.runtime/beeftv-resume-run.py tests/integration/test_canvas_snapshot_reference_guard.py -q -p no:cacheprovider --tb=long --basetemp=.runtime/新回归目录
$env:SNOWFLAKE_WORKER_ID='964'
$env:CANVAS_SQL_PERFORMANCE_ENABLED='1'
$env:CANVAS_SNAPSHOT_PROFILE_ENABLED='1'
$env:CANVAS_REFERENCE_PERF_OUTPUT='C:/Users/snow/code/ai-short-drama/.runtime/output/新性能目录'
.venv/Scripts/python.exe ../.runtime/beeftv-resume-run.py tests/integration/test_canvas_reference_performance.py tests/integration/test_canvas_snapshot_profile.py::test_snapshot_cache_scope_preserves_member_privacy_revocation_and_later_connections -q -p no:cacheprovider --tb=long --basetemp=.runtime/新性能目录
```

继续独立工具真实验收、不同实际媒体、首次创建、生命周期和部署；1000缩放
50.0/50.0/66.5ms的32ms失败、R23未批准候选、完整视觉及M0–M5保留，goal active。
