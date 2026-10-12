# 共用助手正式 Worker、AMQP 与 Agnes 验收

本片接续[助手真实运行审计](2026-10-10-beeftv-assistant-agnes-live-audit.md)和
[剩余台账](2026-10-08-beeftv-remaining-work.md)。用户已授权按
[Agnes 官方指南](https://wiki.agnes-ai.cn/zh-Hans/docs/overview)重复调用免费模型。
本片使用 `agnes-3.0-flash`，不新增音频或企业授权范围。

## 实施范围

新增 `backend/tests/integration/assistant_broker_worker.py` 和
`test_project_assistant_agnes_broker_live.py`。实际矩阵发现认领状态没有通知页面，
新增确定性测试先红，再在 `agent/runtime.py` 增加四行私人开始事件写入。
同步现有助手接口文档；事件 DTO 已允许字符串事件名，无新字段、表结构、依赖或
前端生产代码变化。
原助手浏览器驱动及 R18/R19 冻结源码保持原摘要，没有重跑 importer。

API 使用原 `short_drama.main:app` 和正常 lifespan；Scheduler 使用原
`short_drama.tasks.runtime`。六个独立 Celery Worker 订阅同一随机 namespace 的
text/image/video/audio/render/agent 六条队列，沿原启动脚本的线程池和并发数。
八角色节点号分别为 980–987，测试准备进程为 1022。
数据库来自 fixture 的随机 `short_drama_<uuid>_test`，三桶使用随机
`canvas-test-<uuid>-{image,video,audio}`。数据库连接、fixture 加密键和其他 Settings
只通过子进程内存环境传递，不写配置文件。SMTP 主机和发件地址显式置空，
Scheduler 原 `deliver_one` 因此直接返回，不向真实邮箱发送测试注册或邀请邮件。

Agent Worker 启动器只给原 `AgentModelGateway` 构造器安装观测子类，并在首次真实
POST 前等待浏览器 running 恢复。真实请求、响应和 SSE 委托原 Gateway；
`short_drama.execute_agent`、`runtime()`、`execute_one`、Publisher、Recovery 和
Celery 消费实现保留；认领事务仅增加下述事件。Celery task_prerun/task_postrun 仅记录任务 ID、run ID、
消息版本、routing key、exchange、完成状态和执行进程身份。

正常两条消息只能由独立 Scheduler 发布；测试进程没有直接调用 Runtime 或
`AgentPublisher.tick()`。正常对话后另外投递两条明确标注的重复/过期消息，核对
原 Worker 消费后供应商 POST 和作品状态均不增加。这两条探针不算用户消息。

## 实际批次与失败证据

| 批次 | 实际结果 | 范围 |
| --- | --- | --- |
| live-a | **1 failed / 7 deselected，69.36s** | 画布 Chat 1440；两个真实200回答及浏览器完整流程已执行，失败在后置 Worker PID 预期，后续重复消息和Python权限后置未执行 |
| live-b | **1 passed / 7 deselected，96.99s** | 修正预期后同一画布场景；两真实POST、四AMQP回执及全部Python后置通过 |
| live-c | **exit1 / 1 deselected，79.97s** | 首个画布 Chat 390 因原状态事件缺口而中止，七场未全部执行；仅一条AMQP消息已认领、供应商提交0次 |
| started-red-a | **1 failed / 15 deselected，5.86s** | 隔离MySQL确定性复现：认领后数据库running，但cursor之后没有开始事件；未调用模型 |
| started-green-a | **33 passed，35.22s** | 修复后的项目助手16项及旧Runtime17项，同一随机MySQL，非真实供应商测试 |
| live-d | **7 passed / exit1，616.01s** | 修复后完整八场矩阵，前七场全部后置通过；标准Responses390第二条真实请求超时，不能称整批八场通过 |
| live-e | **1 passed / 7 deselected，97.45s** | 新独立标准Responses390会话，两次真实200、四AMQP回执及全部后置通过；未重发live-d原请求 |
| fast-a | **2463 passed，477.42s** | 修复后完整unit/API，无真实供应商；一个既有Starlette弃用warning |

live-a 关键栈为
`test_project_assistant_agnes_broker_live.py:384: assert deliveries["pid"] == workers["agent"].pid`。
Windows 虚拟环境启动器的 Popen PID 为启动父进程，Celery 实际执行另一个 Python
子进程；首次安全报告 PID 为 22204，不能要求它等于父进程 PID。
修正后核对实际 PID 或直接父 PID 与自建 Popen 句柄一致，并以同一实际 PID
核对模型网关。清理沿已有 `stop_browser` 停止自己创建的整个进程树。

同时核对原 `agent/state.py:finish_locked`：终态把 `message_status` 设为 idle，
并把 `message_version` 加一。因此正常消费版本必须等于终态版本减一；
重复消息使用原消费版本，过期探针再减一。没有改生产版本推进或放宽围栏。

live-c 原栈为冻结浏览器驱动第260行的 `locator.waitFor: Timeout 30000ms exceeded`。
原 GET 已读到 running，页面仍是“消息已排队”，原Worker回执显示认领但未完成，
观测网关 `submissions=0`。浏览器停在首次POST释放前，真实供应商没有受理问题。
`useAssistantRuntime` 沿事件重读状态，而原Worker到模型 `before_send` 才写
`decision.started`；认领与准备期间缺少事件，先前桌面成功受初次读取时序影响。
修复在queued首次认领时同事务写 `run.started`，payload只含run ID，原权限、
SSE、租约和版本检查不变。确定性测试第320行 `assert len(events) == 1` 先红；
实际准备与重复/过期认领、cursor、事件正文及相邻33项随后通过。

live-d 第八场第一条真实回答、复制和仅cookies running恢复通过，第二条请求
`timeout / accepted_unknown=true`，原运行终态failed，原冻结驱动第150行
`assert.equal(result.status, "succeeded", JSON.stringify(result.error))` 失败，
调用栈第294行。保留二条用户POST/二条供应商提交/二条已完成AMQP回执、一份完整
响应、零页面/HTTP错误和全部隔离清理记录。原运行没有retry/resume或自动重发。
`assistant-broker-d-reconciliation.json` 记录这些终态观察；不把失败说成已确认未受理。
用户授权重复验收后的下一场使用新的独立测试会话，不能称恢复了这个不确定请求。

最终同版证据为 live-d 七场加 live-e 一场，覆盖 canvas/standard × Chat/Responses ×
1440/390 的八个唯一组合，**分批全部通过**。成功验收集合内共16次真实200 POST、
32条AMQP任务回执（16条正常发布、16条重复/过期探针），不包含其他红批或live-b。
`backend/.runtime/assistant-broker-final-review/matrix.json` 列出实际批次与目录；
`eight-fresh.png` 八场合成图已实际查看：回复、引用与底部输入/发送均可见，
窄屏没有本片新增的横向越界。不是固定源全幅像素对照或其他媒体验收。

本片全仓 Ruff check、四份受影响Python format及限定 diff 检查通过。
新增生产改动只有上述四行；前端源码和源适配账本没有变动，不重复借用构建、
Node或source hash宣称本片运行通过。此前全仓format的未改迁移脚本换行问题保留。

实际清理盘点：12套隔离八角色已停止，132个测试队列名称被动核验均不存在；
测试API端口和4197无监听，标签MySQL容器0，MinIO原3桶/测试桶0。
原API28840/前端28808及各自启动时间保持；两源树干净，HEAD分别保持
`ef65732bff5ee99019664ca8689608b1c7831cbb` 和
`4ca2a65a7780a8dfcaaa86c33679f84fb04e055c`。没有重启用户服务、提交或推送。

独立 `.runtime/assistant-broker-artifacts.py` 的 baseline/freeze 已实际通过：
本片10文件白名单、232个本地链接/锚点，四份非文档源码冻结，前片13份冻结源码
摘要保持。后续新片须另建范围基线，不把本片冻结校验当未来全仓变化的证明。

批次日志位于忽略的 `backend/.runtime/assistant-broker-live-{a,b}.log`，
对应 `*-temp/` 保存原 `provider-report.json`、`worker-report.json`、
`stack-report.json`、浏览器报告和截图。只有全部Python后置通过才写
`acceptance.json`；live-a 不存在成功验收文件。失败报告不覆盖。

## 已通过场景的后置断言

每场两条消息均来自共用原助手界面；首条引用已保存作品，第二条移除作品后只延续
实际历史。原 running 状态关闭旧 context，再仅凭 cookies 恢复，释放首次真实
请求；终态再用 fresh context 验历史、可见回答、复制、引用和作品状态。

真实 Agnes 请求必须为对应 `/v1/chat/completions` 或 `/v1/responses`，均为200 SSE，
不混换协议。两条正常 AMQP 消息必须携带相同 run ID、原版本、随机 exchange 和
agent routing key；终态重复/过期消息仍由正式 Worker 消费，四条 task 回执为
SUCCESS，供应商调用数固定为二。

MySQL 验私人对话、实际请求消息/响应、流式事件、来源快照和历史；问答不能创建
工具调用、候选或生成任务。本人可读，项目成员和外人读对话、消息、状态、事件、
附件、run 及模型均404。画布 revision/nodes/connections 或标准小说写作内容
保持原值，已保存作品仍按项目权限共享。

## 复现入口

工作目录为 `backend/`，使用可创建随机测试数据库的服务器，设置四个显式 opt-in：
`RUN_AGNES_LIVE=1`、`RUN_CANVAS_RESOURCE_MINIO=1`、
`RUN_CANVAS_BROWSER_INTEGRATION=1`、`RUN_CANVAS_BROKER_INTEGRATION=1`；
准备进程 `SNOWFLAKE_WORKER_ID=1022`。
本机浏览器端口为4197，可用 `AGNES_ASSISTANT_BROKER_BROWSER_PORT` 改为独占空闲端口。
Chrome 使用既有 `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH`。

```powershell
# 使用新的日志和 basetemp；不要复用已经完成的批次名。
uv run python scripts/run_integration.py `
  tests/integration/test_project_assistant_agnes_broker_live.py -q `
  --tb=short --assert=plain -p no:cacheprovider `
  --basetemp=.runtime/assistant-broker-new-temp
```

本机实际沿忽略的 `../.runtime/beeftv-resume-run.py` 创建独占 MySQL 容器，
再调用同一正式 integration 入口；不修改业务 MySQL 权限或把业务库当测试库。
真实凭据沿既有加密读取器只在内存使用，不在命令、报告或前端配置中提供。

## 验证边界

八角色隔离启动与六队列订阅，不能替代用户业务环境的同版部署。
本片业务执行为共用助手纯对话；没有执行 text/image/video/audio/render Worker
的业务任务，不将其启动状态写成三类生成、音频或成片渲染验收。
之前的24个生命周期场使用测试独占执行器，不能据本片正常消息通过改称全部经过
正式 AMQP。Responses 图片400、桌面视频503、H.265/动态政策、其他离线媒体与
并发、32ms、未批准 R23 候选和完整 M0–M5 门禁继续保留。
