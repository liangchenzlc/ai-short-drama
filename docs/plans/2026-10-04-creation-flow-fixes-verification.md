# 创作流程修复实施与验收记录

日期：2026-10-04。状态：20 项实施、对应验收及最终生产构建已完成。

对应[保持布局的修复方案](2026-10-04-creation-flow-fixes-without-layout-changes.md)。本轮保持现有分栏、卡片排列、面板位置、时间轴、主题及 CSS，反馈和新增动作复用原有菜单、候选预览、提示区与弹窗。开始时已有改动完整保留，未执行 commit。

## 1. 20 项实施结果

| 编号 | 优先级 | 问题 | 已实施方案 | 验证与边界 |
| --- | --- | --- | --- | --- |
| F01 | P1 | 带附件 Agent 请求丢响应后刷新，原请求可能被重建 | 待核对请求与输入草稿分开保存；冻结原键、附件、Skill 版本和音轨选择；存储失败阻止 POST | 刷新后核对原请求，保持单消息/Run；账号、会话隔离 |
| F02 | P1 | 素材修改文字后，当前图缺少重新确认入口 | 原位置调用既有采用流程，必要时建立本人的候选 | 本集/项目/全局、生成/上传/共享图路径验证；来源、版本和共享确认保留 |
| F03 | P1 | 素材缓存详情及 409 缺少恢复出口 | 打开读最新详情，迟到响应不重开；冲突保稿、完整下载、对照、明确保稿或弃稿 | 冲突后不会自动推进版本或重存；返回 Agent 前也必须明确核对 |
| F04 | P1/P2 | 首屏外镜头不能直接编辑，替换后遗留旧对象会话 | 独立详情读取，分页与详情分离；新增按返回 ID 定位；替换/归档清对象范围 | 第 21/50/80 镜、80 镜后新增、迟到列表、替换与归档回归 |
| F05 | P1 | 普通入口和旧编辑指针绕过 Agent 采用 | 列表过滤未采用稿；选择/保存/确认都拦截；来源布尔检查不受作者过滤影响 | 作者/协作者、ready/rejected/archived、已采用共享稿、跨项目边界覆盖；真实 MySQL 回归纳入最终验收 |
| F06 | P2 | 活动提取任务被历史首页挤出后不更新 | 对已知活动 task 独立补读 detail | 21+ 历史记录仍读取终态，不重新提交生成 |
| F07 | P2 | 两类参考图合计超过 16 才被后端拒绝 | 按 media ID 合并去重，提交前保存并重新读取引用后检查 | 17 张零生成 POST，16 张可发；去重不丢参考图 |
| F08 | P2 | 未选无效提取项阻塞合法项采用 | 单项错误反馈与明确撤回该项修改；保留其他草稿和服务端校验 | 无效内容不会误入库，明确处理后可继续采用 |
| F09 | P2 | 项目添加列表只排除本集当前页素材 | 读取完整本集成员分页，再排除已添加素材 | 跨页成员不会显示为新增成功，后端 link 幂等保持 |
| F10 | P2 | 历史三态不清楚，素材读取失败阻断镜头 | 候选/历史区显示 loading、empty、error；镜头和素材独立读取 | 首次慢读、503、刷新失败、shots 成功而 assets 失败均覆盖 |
| F11 | P2 | 导出/预览/重试原请求刷新丢失 | 按账号与对象保留完整 body/key/job_id；核对原请求后清记录 | 三类丢响应回归；明确创建前拒绝允许修正；unknown 不换键。损坏记录可下载、取消或明确清除，确认期间记录变化则拒绝清除 |
| F12 | P2 | 轮询失败无提示，声音入口或过期音频难恢复 | 就近显示连接状态和重连；能力读取三态；音频只合并媒体地址 | 断网保稿、迟到 poll、入口重试、字幕/配乐未保存时地址刷新覆盖 |
| F13 | P2 | 取消时已有候选或随后完成候选缺会话入口 | 取消事务补未展示引用；延迟归档共用去重，持锁当前读避免旧快照重复 | 保持 cancelled，消息/事件及候选引用严格唯一；不调用模型、自动继续或采用 |
| F14 | P2 | 原候选正文随采用后编辑变化 | 在现有 source_content 冻结原稿，详情优先读快照 | 采用后编辑不改原稿；旧记录 fallback 明确标为当前剧本，不能作为原生成稿证据 |
| O01 | P1 | 分镜缺少排序/归档入口 | 复用原镜头菜单和既有 API | 跨页移动、归档当前对象及范围清理通过 |
| O02 | P1 | 分镜候选审核信息不完整 | 原预览补 title、对白、时长、素材、来源摘录、剧情节点、整批总时长；同步 schema/DTO/文档 | 素材名称来自冻结快照；缺失/不可用状态有标识 |
| O03 | P1 | Agent 模式不能就近审核原生对白 | 在原镜头信息弹窗复用对白审核 | 保存失败保留草稿，成功后明确审核；修复重复挂载的编辑区 |
| O04 | P2 | 崩溃或刷新丢失正文/剪辑/声音草稿 | 持久化草稿与基准版本；恢复前重读，显式恢复/弃稿，完整下载损坏记录 | GET 失败后重试仍提供恢复；恢复核对失败不静默清稿；账号来源用可信内存身份，拒绝旧 sessionStorage 槽串用；不持久化临时 URL |
| O05 | P2 | Agent/声音数据库版本以 number 传递，存在精度风险 | Pydantic、Service、DTO、调用及回执统一十进制字符串 | 超过 JS 安全整数的版本保持精度；声音初始允许 0；拒 bool/float/负数/溢出，seq/cursor/时长保持数值 |
| O06 | P2 | 长会话重复回放，成片持续高频轮询 | state → messages → SSE，恢复游标保留在途回复；无重叠轮询，活跃/空闲/隐藏分别调整 | 万条历史替身测量及 300 片段 SQLite 测量见下；不宣称真实供应商或生产性能验收 |

## 2. 最终验证

| 范围 | 实际命令/结果 | 限制 |
| --- | --- | --- |
| 后端快速验证 | `.venv/Scripts/python.exe -m pytest tests/unit tests/api -q -p no:cacheprovider --basetemp=.runtime/creation-final-unit-06`：852 passed | 不调用付费模型；1 项第三方 anyio 弃用警告 |
| 前端单元 | `npm.cmd test`：189 passed，0 skipped | Node 测试，不等于真实供应商验收 |
| 前端生产构建 | `npm.cmd run build`：最后提示框调整后再次成功，包含 TypeScript 检查；最终日志 `.runtime/creation-final-build-08.log` | 不重复运行 typecheck |
| 浏览器 | 首轮全套 225 项：218 passed、4 failed、3 skipped；4 处已定位并修复或同步新流程。随后 57 项相关复验中 56 通过，最后 1 项及正文新增场景定向 4 项通过；render 定向 6 项通过 | API 测试替身；这些轮次包含重复场景，不相加为唯一测试数量。没有声称重新跑过全量且全部通过 |
| Python 静态检查 | `.venv/Scripts/python.exe -m ruff check src tests scripts` 与 `ruff format --check src tests scripts`：通过，382 files already formatted | 仅规范化原有未格式化测试文件，无行为改动 |
| MySQL | 首轮：318 passed、8 skipped、4 failed；修正 JSON 筛选并新增 F05 回归后，最终全量 330 passed、1 skipped；唯一跳过的 Broker 用例随后显式启用，独立执行 1 passed | 所有 331 个集成场景实际执行通过；全量和单项在各自新建的随机测试库中执行，不将两轮说成一个全量零跳过结果 |
| 基础设施 | 显式启用真实浏览器 → API → Publisher → Agent Worker、RabbitMQ、MinIO，以及四类生成任务的 Broker 归档验收：通过 | 随机队列/测试对象并清理；模型为本地 HTTP/内存替身，视频容器为协议样本，不等于付费供应商或真实视频质量验收 |
| 改动与布局 | diff-check、文档链接、新增生产源码调试/乱码扫描通过；无依赖变更；原布局在 1440/1024/390/320 视口检查 | 新损坏 render 提示在 390/320 的 2 项回归及截图检查通过；只将按钮放到同一提示框的说明下方，未改本轮 CSS/主题 |

首轮浏览器失败中，两个按钮的加载图标离场造成精确名称匹配错误，改为稳定名称匹配并保留 enabled/disabled 断言；一个 URL 断言未保留新对象范围参数；另一个 Agent 返回流程使用旧 scope 替身，并在 409 后直接再次保存。更新后仍严格核对保稿、明确版本审核、零提前发送及保存后发送。

首轮 MySQL 的 `JSON != []` 筛选将空数组消息纳入结果；最终用 `JSON_LENGTH > 0` 明确筛选，增加 SQL COUNT、候选事件和引用精确断言，不放宽消息唯一性。纯 SQL 对照探针纳入最终报告。

最终真实 MySQL 为 **8.4.11**。对照探针实测 `JSON_ARRAY() != '[]' = 1`，而 `JSON_LENGTH(JSON_ARRAY()) = 0`，确认首轮误计数来自筛选语义。F05 跨作者旧公开稿、F13 取消已生成候选及旧快照去重全部通过真实数据库验证。

最终数据库命令通过被忽略的 `.runtime/creation-isolated-integration.py` 启动、等待并清理全新 MySQL 容器，再向标准 `scripts/run_integration.py` 传入 `TEST_DATABASE_URL`：

```powershell
# 首轮全量最终验收；三个开关对应隔离队列和测试对象
$env:RUN_AGENT_INFRA_INTEGRATION='1'
$env:RUN_STORAGE_INTEGRATION='1'
$env:TEST_PRODUCTION_INFRA='1'
$env:PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH='C:\Program Files\Google\Chrome\Application\chrome.exe'
.venv/Scripts/python.exe .runtime/creation-isolated-integration.py

# 补测全量中的唯一显式跳过项，另起一次性容器
$env:RUN_GENERATION_BROKER_TESTS='1'
.venv/Scripts/python.exe .runtime/creation-isolated-integration.py tests/integration/test_generation_broker.py -q -rs
```

全量日志：`backend/.runtime/creation-final-mysql-full.log`；补测日志：`backend/.runtime/creation-final-isolated-mysql.log`。两轮报告的 `test_exit_code=0`、`container_removed=true`，Docker 检查没有残留本轮 MySQL 容器。

最后提示框调整后，原 render 6 项与 390/320 两项再次全部通过，随后重新执行生产构建。窄屏说明和三个操作按钮均可读、可达，没有 document 横向溢出；下载、取消、确认清除及新任务的完整流程仍通过。

## 3. 性能与恢复测量

- **长会话，API 替身**：10,000 条完成历史；初次恢复 cursor=10000，首次仅送达 1 条当前事件；断线重连后累计 2 条，当前回复全文恢复，无历史全量回放。证据：`frontend/.runtime/agent-lane-long-report.json`。
- **300 剪辑片段，SQLite 单元夹具**：响应 153,302 bytes，20 次读取均值 15.93 ms，每次 12 条 SELECT。证据：`backend/.runtime/assembly-300-measurement.json`。这是测量基线，不能外推真实 MySQL 延迟或生产吞吐。
- **轮询**：首屏/有活动任务 4 秒，空闲 15 秒，页面隐藏 30 秒；递归定时器避免并行重叠，失败有限退避，显式重连保留草稿。浏览器假时钟验证活跃/空闲节奏和迟到响应。

## 4. 数据与交付边界

本轮无业务表结构变更，原稿快照使用已有列。真实数据库测试在新建的一次性 MySQL 8.4 容器内创建、执行完整 SQL 并清理随机 `_test` 库，不使用现有业务库或既有 root 凭据；测试新密码只在进程环境生成，不写入文件或对话。每次容器结束后移除，本轮没有为现有账号提权。此前 Agent 范围迁移的实际执行记录见[Agent 完善记录](2026-10-04-agent-creation-refinement.md)，不以历史验收冒充本轮验收。

保留候选本人可见、明确采用、来源先保存、版本保护、409 保稿、授权范围及 unknown 不自动重新提交。有效不确定请求不能用“清除损坏记录”入口绕过原请求核对。

未验收外部付费供应商的真实模型能力、生成质量、计费及生产环境长期负载。不保证软件永远没有缺陷；本文结论限定于列出的场景、实际测试及保持布局的修复范围。

测试日志、截图、测量和一次性编排脚本均在被忽略的 `.runtime`，没有作为交付源码或凭据提交；依赖与锁文件未更改。
