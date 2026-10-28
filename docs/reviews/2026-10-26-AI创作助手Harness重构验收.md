# AI 创作助手 Harness 重构验收

日期：2026-10-26。对应[实施记录](../plans/2026-10/2026-10-26-AI创作助手Harness重构实施.md)
及[运行机制](../instruction/AI创作助手Harness运行机制.md)。本记录区分协议替身、真实基础设施
和真实模型；旧引擎回归存在基线失败，不能表述为全库测试全部通过。

## 交付范围

- LangGraph 管模型和工具节点，LangChain 每段只做一次 function calling 决策。
- MySQL saver 保存轻状态和 pending writes，生产恢复继续复用 Run／Turn／Operation 账本。
- 图片、Skill 统一引用与 Resolver；媒体只在本次请求水合，历史和图状态不存媒体实体。
- Skill 正文首次保存、重复加载去重、独立保留配额；深层文本、脚本和示例图递进读取。
- 保留 L1–L5、水位、来源、遗忘及 CAS；五段系统提示与本轮续执行提示版本化；自主选工具。
- 新任务关闭旧中断控制，旧任务固定原引擎；不新增任意脚本执行能力。
- 真实业务库已执行两个图表的 DDL；完整 SQL 与 ORM 一致，共 118 张表。

## 真实模型长任务

使用用户授权的网关 `https://sub2.de5.net` 与 `gpt-6.1-sol`。明文密钥只存在测试进程环境
和内存中，隔离模型配置使用临时加密密钥；隔离 MySQL 与 MinIO 随机前缀在测试结束后
精确清理，不写入用户作品，也未调用真实生图、生视频或音频生成节点。

长任务依次加载两个手选 Skill，重复加载第一个核验去重，逐段读取六份资料及只读脚本，
按需查看一张本地绘制的蓝条示例图，再自主调用业务工具创建两个角色和三个分镜，
每个保存批次分别用 `read_task_results` 读取成果。最终一轮 21 次工具操作均成功；参数错误
纠正另有早期真实任务和协议／数据库回归证据。一次响应原始回执保存后模拟 Worker 丢失，
走租约过期、数据库恢复与离线重放。

| 指标 | 实测 |
| --- | --- |
| pytest | 1 passed，237.06 秒，含隔离库初始化与清理 |
| 任务执行时间 | 211.05 秒 |
| 决策段／工具调用 | 22／21 |
| 成功写入批次 | 4，合计两个角色和三个分镜，四个批次分别回读 |
| 一次压缩最多移出近期工具组 | 17 |
| 独立保留 Skill | 2／2 |
| 原始回执重放新增外部请求 | 0 |
| 持久状态媒体实体泄漏 | 0 |
| 示例图视觉断言 | 蓝色条带位于左侧，通过 |
| 实际外部请求 | 25，含 L4／记忆整理调用，排除原始回执重放 |

该用例将近期工具窗口设为 2600 估算 tokens、L4 软阈值设为 1000 字节、近期输入设为
600 字节，以短夹具实际触发压缩；生产阈值未因此降低，也未提高 32 次决策预算。
网关累计观测的输入／输出 usage 为 721413／9598 tokens，已排除离线重放的重复 usage；
它不是供应商账单，也不能据此拆分单次图片 token 成本。本轮自动断言六份资料的实际读取
及顺序、脚本／图片读取、每个保存批次的成果回读、角色与分镜正文及关联、蓝条左侧回答。
正文线索检查接受同段表达“同名”和“包裹”，不要求固定连续词串；早期两个用例曾因等价
中文表述被过严的词串断言拒绝，已修正测试，未取消内容或真实保存检查。

最终证据位于忽略目录 `.runtime/harness-live-final6.out`、`harness-live-progress.json`、
`harness-live-result.json` 与 `harness-live-decisions.json`，保留合成任务的统计、决策及引用
快照，不含凭据和媒体实体。早期验收发现并修复了压缩后进度丢失、遗漏手选方法、Skill
旁白导致 digest 不一致等问题；最终通过结果不代表此前每次尝试均成功。

## 自动化验证

| 范围 | 结果与证据 |
| --- | --- |
| Agent 单测，含三协议、媒体冻结、预算和能力缓存 | 244 passed；`harness-unit-continuation.out`，84.50 秒 |
| 续执行提示与视觉工具结果的三协议线格式 | 3 passed；`harness-continuation-protocols.out`，与上行有重叠 |
| Harness 数据库场景与旧图片归属 mock 修复 | 11 passed；`harness-final-verified.out` |
| 最终 Harness，增加换版／移除／来源遗忘、进度摘录及续执行 | 13 passed；`harness-continuation-contracts.out`，80.09 秒，包含前述 10 个 Harness 场景 |
| Harness 与旧 Skill 集成 | 27 passed；`harness-skills-final.out`，与上行有重叠 |
| Agent 与助手 API 契约 | 81 passed；`harness-api-final.out` |
| 前端 Agent／助手相关用例 | 73 passed；`harness-frontend-reviewed.out`，包含先前 26 项 |
| 宿主 TypeScript 检查 | 退出码 0；`harness-frontend-typecheck-verified.out` |
| 宿主及画布生产构建 | 退出码 0；`harness-frontend-build-final.out`，保留分包大小提示 |
| 完整 SQL 校验 | `export_schema.py --check` 通过，118 张表 |
| 锁文件与 Python 改动检查 | `uv lock --check`、本次 59 个 Python 文件 Ruff 检查与格式检查通过 |
| 结构门禁 | 887 个源文件、0 结构问题；规模信号只作复审提示 |

数据库场景包括大 JSON 窄投影、普通语言保存、画布 CAS、工具失败纠正、重复投递、
Skill 分段／脚本／图片／压缩保留、原始响应零重发、纯讨论重答、会话清理与 L5 遗忘。
图 saver 的原生 pending writes 恢复、过滤、并发幂等和跨账号拒绝亦已执行。
音视频只有本地 FFmpeg 和协议替身验证，未进行真实供应商的音视频理解验收。

全库 Ruff 仍有 26 项存量 import 排序问题，另有六个未改文件不满足格式检查；
本次改动文件单独检查通过，未批量改写无关旧文件。证据为
`.runtime/harness-ruff-repository.out`、`harness-format-repository.out`。

全工作区 `node scripts/test.mjs --changed --dry-run` 被另一组剧本页的七个未映射新文件
阻塞，未改动其目录或添加忽略规则；本次 `--feature standard.agent,standard.assistant`
的 fast／mysql／infra 预览通过，测试清单已重新生成。

## 旧引擎基线对照

旧 Skill 与全局任务首次全量回归为 368 passed、57 failed。修复本次变化后重跑失败集，
曾为 30 passed、27 failed；与独立 Git HEAD 源码、独立 MySQL 的同一 57 项对照，
基线为 31 passed、26 failed。唯一新增失败是伪造 Task 缺少 `effective_state`，已补齐
测试 mock。最终将该 57 项与旧 L5 的 40 项合并重跑，得到 **66 passed、31 failed**，
319.53 秒；失败集合与两个独立 Git HEAD 基线的并集完全一致，新增失败为零。

最终证据：`.runtime/harness-legacy-reviewed.out`。基线证据为
`.runtime/harness-baseline-legacy.out`、`harness-baseline-memory2.out`。
这些存量失败包含旧图片请求／回放断言、旧 monkeypatch 路径、记忆作用域和 `save_delta`
返回值断言；本次不将它们改写为通过，也不以新引擎用例替代旧引擎验证。

## 审查与边界

依照 `code-review-skill` 完成范围、职责／依赖、关键行逻辑／并发／错误处理和结论四阶段。
分诊结果为 XL；新增代码按执行图、资源、上下文、工具、Skill 及持久层职责归档，
未通过放宽结构基线压过门禁。

- 🔴 已修：MySQL 等锁前快照造成 checkpoint 幂等误判；统一锁顺序并使用当前读。
- 🔴 已修：Skill pin 清空旁白后恢复 digest 不一致；冻结 include_content 并覆盖非空旁白。
- 🔴 已修：纯讨论重答读入未来上下文；按原位置重建并禁用工具。
- 🟡 已修：参数错误缺少可纠正字段信息；只返回公开契约校验详情，不返回原输入或堆栈。
- 🟡 已修：旧框架能力缓存被显示为新链路已验证；绑定当前 LangChain framework 版本。
- 🟡 已修：手选移除／换版后近期窗口残留旧正文；移动窗口下界、保留其他活动 pin，
  并拦截被排除方法的自动重载。先复现为两项失败、一项来源遗忘通过，修复后 13 项全通过。
- 🟡 已修：最终长任务曾误将 Skill 示例图交给附件工具，未保存即结束；明确三类资源
  读取入口，并在附件身份错误中返回 Skill 正确工具提示，覆盖提示到达模型的数据库回归。
- 🟡 已修：严格长任务曾在压缩后反复重读资料，耗尽 32 次决策预算；仅有路径游标不足以
  保留已读事实。新增有来源、总量有界的原文摘录与模型阶段旁白，明确截断及省略数量，
  不额外调用总结模型。先补两项失败单测，再实现投影；数据库回归同时确认失效来源不会
  通过进度旁白重新进入上下文。验收夹具最后一段不再指向不存在的 step-7.md。
- 🟡 已修：用户步骤在 L4 摘要中完整保留时，模型仍曾跳过第二个方法及去重核验。
  新增工具轨迹之后的本轮续执行提示，冻结为 `assistant-harness-v2` 输入，要求核对依赖、
  未完成项和可纠正的读取错误；不使用编排代码强制工具序列。旧输入缺字段时不追加消息。
- 🟢 保留边界：衍生对象上传后、引用落库前崩溃可能产生孤儿对象，目前没有自动 GC；
  删除会话会重试清理已登记衍生对象并保护业务原件。

Claude 与 Responses 已通过协议替身验证，真实模型长任务本次仅验证 OpenAI Chat 兼容链路。
未接入告警通知服务或指标看板，泄漏写入拒绝及 ERROR 日志须由部署日志系统接收。
未执行浏览器交互验收、真实媒体生成节点或生产 Worker 集群压测。

本次 Harness 改动未遗留阻塞或应当修复的审查项；旧引擎基线失败、全工作区选测阻塞和
以上未验边界保留在交付记录中，不计作已通过。
