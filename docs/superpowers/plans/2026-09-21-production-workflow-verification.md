# 五阶段工作流实施验收记录

日期：2026-09-21。状态：五阶段实现、正式库迁移和核心页面验收完成；测试数据独立核对与清理未完成，真实供应商样本未执行。

用户通过“开始实施”批准五阶段方案。继续使用 `feat/episode-writing` 工作分支，保留此前未提交的小说/剧本持久化实现。本轮未调用付费模型，也未推送仓库。

## 已验证

- 原有基线：后端 unit/API 291 passed，前端 Node 31 passed。
- 当前完整后端 `pytest tests/unit tests/api -q --tb=short -p no:cacheprovider`：333 passed；`ruff check src tests` 全部通过。
- 新生成业务与图片采用本地测试：`pytest tests/unit/test_generation_business.py tests/unit/test_media_asset.py -q`，18 passed；覆盖幂等、不可偷偷切换剧本、严格分镜结构、截断结果禁止错误恢复、候选待采用和旧来源确认。
- 隔离 MySQL：`test_episode_writing.py + test_generation_execution.py`，15 passed。
- 隔离 MySQL：`test_production_generation.py + test_ai_generations.py + test_episode_storyboard.py`，4 passed、1 条真实基础设施用例因未设置 opt-in 而 skipped；随后已单独启用并执行该用例，见下一条。
- `TEST_PRODUCTION_INFRA=1` 运行 `test_production_generation.py`：2 passed。固定测试上游返回剧本、JSON 分镜和小 PNG；真实 RabbitMQ 专用随机队列投递/确认/消费，真实 MySQL 随机测试库保存，真实 MinIO 入库。小说生成剧本→候选切换确认→生成分镜→显式追加→分镜图片生成→显式采用贯通；重复投递/采用不重复调用上游或创建业务结果。测试队列、交换机、对象及随机数据库均在测试后清理。
- 迁移测试 `test_episode_storyboard.py + test_production_workflow_migration.py`：3 passed。将隔离库退回旧表结构，再执行 000–005，验证旧行不变、候选回填、重复执行不重置版本，以及迁移后 information_schema 与 canonical SQL 的字段、索引、约束一致。
- 并发采用追加验证：两个独立 MySQL Session 同时采用分镜结果，只写入一批记录，另一请求收到幂等重放；同图并发采用只推进一次分镜和集合版本。加入断言后再次运行真实基础设施用例，2 passed。
- 上述云测试中出现的 pytest cache 写权限 warning 不影响断言；后续使用 `-p no:cacheprovider`。

## 实现文件归并

- `test_generation_business.py` 合并了结构化文本结果和本地保存恢复的单元用例；真实串联验收在 `test_production_generation.py`。
- `NovelScriptGeneration.tsx` 负责小说生成及剧本候选，不再额外拆分一个仅传递状态的候选包装组件。
- 分镜 Axios 模块为 `api/modules/storyboard.ts`；`GET shots/{id}` 与写接口都返回 `{shot, storyboard_version}`。
- 素材公开 `AssetRead` 与内部 `AssetRecordRead` 分开，公开响应不暴露幂等键/指纹或内部审计字段。

## 正式库迁移与页面验收进度

- 用户明确批准“允许继续迁移及验收”后，云数据库访问和浏览器命令成功执行，之前的权限审核超时已不再阻塞迁移。
- 正式库检查活动任务为 0，暂停本地写入进程，完整逻辑备份后执行 000–005 迁移并核验，原有各表行数不变；回填处理 0 条旧素材。结果文件为 `backend/.runtime/production-migration-result.json`，`verified=true`。
- 完整备份为 `backend/.runtime/production-backup-20260921-150915.sql.gz`（已忽略，不提交 Git）。迁移后 API、scheduler 和三类 worker 成功启动；浏览器受控生成验收期间再次暂停普通 scheduler/worker，由限定测试项目及测试配置的受控执行器处理，收尾时恢复。
- 素材独立 MySQL 集成 `test_asset_library.py` 已实际运行，2 passed。
- 前端最终 Node 45/45、TypeScript、diff-check 和生产 build 通过（1598 modules，既有 chunk 大小提示）；浏览器发现的分镜生图模型选择入口已补齐，选择测试配置后生成及刷新恢复均成功。新增任务行统一使用中文状态展示。
- 浏览器已完成：小说保存并刷新恢复、手写剧本保存、小说生成剧本候选不覆盖当前编辑、预览后显式切换及确认；项目角色新增、图片上传与明确确认、从项目库添加至分集；剧本生成分镜候选预览及明确追加、分镜文字修改、关联角色与排序。
- 浏览器已完成：归档空分镜、刷新后查看归档历史；图片生成成功后只显示候选、明确采用后刷新显示“当前采用”；编辑镜头后立即切页再返回，正文保持且版本推进到 v6，显示“创作内容已变化，请重新核对当前图片”；直接打开不含阶段的分集 URL 正常跳转 source，没有误报未保存确认。控制台检查为 0 errors / 0 warnings。

### 浏览器生成证据

测试项目 `360329016541974528`，分集 `360330420966920192`，角色 `360333803853451264`。只用无供应商凭据的测试配置 `360338550761242624` / `360338550761242625`。

| 操作 | 任务ID | 页面结果 |
| --- | --- | --- |
| 小说生成剧本 | 360351128581312512 | 完成后新增候选，原手写稿仍是当前编辑；预览并明确切换后才成为当前剧本 |
| 剧本生成分镜 | 360354968437264384 | 候选预览期间原分镜不变；明确追加后出现新镜头 |
| 分镜生成图片 | 360357443756101632 | 成功后出现候选且无当前图片；确认采用后刷新仍为当前图片，候选保留 |

页面发起真实 API 请求，由受控执行器执行现有任务与业务保存服务，图片使用真实 MinIO；受控执行器不调用供应商。真实 RabbitMQ 投递/消费另由隔离基础设施集成测试覆盖。

截图：`frontend/output/playwright/production-image-candidate.png`、`production-image-adopted.png`；完整页面结构、操作与请求证据保留在忽略目录 `frontend/.playwright-cli/`。

## 运行恢复与剩余事项

- 受控执行器 PID 21104 已停止。普通 scheduler/text/image/video 已恢复，分别为 PID 38456/29980/34352/40356；启动脚本完成进程命令归属检查，主代理通过 `Get-Process` 再次确认四个进程存活。API 在页面验收期间保持运行。
- 最后一次独立 DB/MinIO 断言命令两次遇到自动权限审核 deadline timeout，实际未执行；没有返回明确风险拒绝理由。为避免长期暂停正常生成，已优先恢复运行。
- **未完成：**本轮正式库验收数据的独立直接查询核对与精确清理。测试项目、两条测试配置、三个成功任务及测试图片暂时保留；测试配置无供应商凭据且非默认。清理清单为 `backend/.runtime/production-ui-smoke-manifest.json`，还需覆盖本轮手工新增的角色及上传图片，不可直接按名称批量删除或清空业务表。隔离集成测试的随机数据库/队列/对象已在其测试结束后清理，与这批页面验收数据分开。
- 真实供应商 N/G/I 样本未执行；需单独确认调用配置、数量与预算，不沿用此前一次诊断授权。

浏览器核心流程已通过；53 条验收标准没有全部逐条进行浏览器故障注入。断网、双窗口竞争、上传异常、重放与恢复等边界，以对应自动化测试的实际覆盖为准，不能把它们描述为全部手工验收通过。全局库三种素材分别创建、含图分镜替换、两张图切换采用等完整页面组合未逐项另跑。
