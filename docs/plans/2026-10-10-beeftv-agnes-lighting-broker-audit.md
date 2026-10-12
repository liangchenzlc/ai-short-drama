# 原独立打光的 Agnes 真实供应商验收

接续[正式生成审计](2026-10-10-beeftv-agnes-generation-broker-audit.md)，按用户已授权的
[Agnes 指南](https://wiki.agnes-ai.cn/zh-Hans/docs/overview)使用免费的
`agnes-image-2.5-flash`。最终 live-d **2 passed / 0 skipped，256.72s，exit0**，
覆盖1440与390两种屏宽；全部 Python 后置与实际图片内容检查通过。
这是明确配置的蓝色逆光礼盒示例，不能据此保证所有打光预设或每次模型输出。
live-b 技术后置通过但真实图片新增人物，内容验收失败，不能计入最终成功。
live-c 空503的 `accepted_unknown=true` 仍未解决，没有自动重提交该任务。

## 实施与调用链

新增[独立 Python 验收](../../backend/tests/integration/test_canvas_agnes_lighting_broker_live.py)
与[原 UI 浏览器驱动](../../frontend/canvas/scripts/verify-agnes-lighting-python.mjs)。
旧打光驱动、旧真实模型验收及正式栈观测模块保持冻结；没有生产代码、HTTP、DDL、
依赖、标准模式或源适配账本变化。共用助手仍为纯对话，本片没有执行助手工具。

复用正常 API lifespan、独立 Scheduler 和六类 Celery Worker，以980–987八个不同
节点号启动同版正式栈；fixture 准备进程为1022。真实 RabbitMQ、原任务执行器、
Publisher、Recovery、随机 MySQL 和随机 MinIO 桶实际运行。凭据沿现有加密信封
在服务端内存解密，再通过保存模型 API 加密存储，未传入浏览器或写入文档。

参考为此前已验收的匿名蓝盒原图，1024×1024，SHA-256：
`a47ed24be0e913236bf292aba8b23d03cbe2feb7b33550eacf20fd04808f79b6`。
本片先按原文件 input 上传，再由原节点菜单打开打光。不是通过 API 种生成结果。
供应商调用按已读图生图合同走 `POST /v1/images/generations`、1K、1:1，原图通过
`extra_body.image` Data URI 传递，图片返回合同为 `b64_json`。观测记录实际引用摘要，
与上传原字节严格相等；真实供应商原返回字节与 MinIO、数据库、浏览器摘要一致。

## 实际批次与失败证据

| 批次 | 实际结果 | 实际范围 |
| --- | --- | --- |
| live-a | **1 failed / 1 deselected，135.35s，exit1** | 桌面原 UI 完成，一次真实200；新 Python 将客户端模型选择与供应商模型名混为一谈，最后媒体/权限后置未全部执行 |
| live-b | **1 passed，pytest.exit / exit1，207.02s** | 桌面技术后置全部通过，但原产物新增人物、改变构图，实际内容失败；390被任务面板遮挡，供应商POST为0 |
| live-c | **pytest.exit / exit1，85.64s** | 桌面一次空503、`upstream_unavailable`、`accepted_unknown=true`，零结果；成功媒体后置未执行，390未启动 |
| live-d | **2 passed / 0 skipped，256.72s，exit0** | 蓝色逆光＋礼盒约束，1440/390各一次真实200、唯一永久产物、全部技术后置与实际内容检查通过 |

a 的关键栈是 `test_canvas_agnes_lighting_broker_live.py:265` 的模型名断言。
实际 `model` 为 `host-{id}::agnes-image-2.5-flash`，`logicalModelId` 为保存模型ID。
核对[宿主目录适配](../../frontend/canvas/src/services/host-model-config.ts)与
[生成 Service](../../backend/src/short_drama/service/canvas_generation_service.py)后，只修
新测试为严格比较保存的 `selection`；供应商请求与冻结快照的原模型名断言保留。
上传原图初始化未发布，进入共享画布 commit 时由
[画布 Service](../../backend/src/short_drama/service/canvas_service.py)执行 `publish`，
所以源图与结果的项目、发布、原字节和成员可读检查没有删掉。

b 桌面原图只有蓝盒，实际生成图片出现人物。已查看原1024图片和fresh界面，
保存 `agnes-lighting-broker-red-evidence.json`；即使有技术 `acceptance.json`，
也不能作为打光内容成功证据。原“伦勃朗光”包含 `classical portraiture`，
本片没有改变固定源预设。后续通过原描述、模板和风格控件增加礼盒/构图/无人
约束，并改选蓝色逆光。成功只对应该新内容组合，源预设增人案例保持失败记录。

b390 的关键栈为旧新驱动 `fit()` 第134行、running恢复第274行：
`locator.click: Timeout 20000ms exceeded`，任务面板拦截“适应画布”按钮。
实际失败图已查看，参考图仍可见；首次、idle恢复共两次原图检查，尚未释放真实
供应商调用，POST为0。两浮层组件与固定源字节相同，调用均传专注模式小间距，
这只是源码核对，没有冒称源浏览器成对复现或遮挡已修。新驱动沿现有
[Ctrl+0 键盘合同](../../frontend/canvas/src/pages/canvas/use-canvas-keyboard.ts)适应画布，
没有强制点击、隐藏浮层、修改CSS或降低屏宽；指针遮挡仍作为未关闭布局问题保留。

c 的关键栈为新驱动第283行 `terminal.status == "succeeded"`：实际为
`failed / upstream_unavailable`。HTTP503响应字节为0，供应商受理不明，无可核对ID。
原 Worker 标为 `accepted_unknown=true`，浏览器只有一次提交，验收立即退出，
没有自动重放。d 是用户已授权的新验收任务，提示词/风格有变化、独立画布与新键；
不是 c 的重试或成功恢复。c 失败截图属于刚读终态的瞬时图，不宣称最终错误UI已验。

## 最终成功范围

两宽度均实际执行六光位、八风格选择/取消、亮度Home/End、颜色、轮廓光、智能
描述禁用、球面鼠标拖拽、重置、取消/重开默认值，模板实时修改/恢复/保存与真实
剪贴板。八风格在控件与提示词层检查，只有最终蓝色逆光进行了真实生成。
个人份数保存为3，工具仍只创建一份idle节点；此时零提交。关闭旧窗口后仅cookies
恢复同一idle节点，再按原“生成”按钮保存/提交唯一任务，running再次仅cookies恢复
后才释放真实请求；终态永久结果直接回填，再fresh恢复同一图与媒体。

最终两次真实200 POST、四个正常AMQP动作与四个终态原版本/过期版本探针，共八条
完成SUCCESS回执；每场始终只有一次供应商提交。动作版本精确连续，正确exchange/
image路由、实际Worker PID/父PID/节点号均已核对，额外终态探针不增加供应商调用。
四阶段参考解码、1024×1024、原SHA与完整源节点不变；唯一调用/绑定/attached结果、
归档原字节、稳定ID、MinIO定位、作者私人任务/模型、成员共享原作品、外人404及
共享节点无私人任务/模型参数全部后置通过。日志、浏览器、安全报告及冻结请求/
响应快照检查无明文凭据；页面错误和HTTP错误均为0。

| 宽度 | 最终原产物SHA-256 |
| --- | --- |
| 1440 | `ba2014d640b8290574f86be6bc3fd33f9ad04391a512c54e4614742a06a19196` |
| 390 | `9f7e912a4f340cde09344267fc2e5188735af21d4b564d43bdff632e4ce0f809` |

两张原始输出、参考原图、源/结果合成图及两宽度fresh合成图已实际查看：可见单个
带盖蓝盒，主体/位置/视角和构图保持，蓝色光、轮廓与阴影改变；未见新增人物、
其他物件、文字或标识。`content-review.json` 明确记录人工实际查看方法及仅本示例
的范围，不将其描述为自动内容分类、逐像素一致或所有预设质量保证。

## 命令、清理与接续

在 `backend/` 显式设置 `RUN_AGNES_LIVE=1`、`RUN_CANVAS_RESOURCE_MINIO=1`、
`RUN_CANVAS_BROWSER_INTEGRATION=1`、`RUN_CANVAS_BROKER_INTEGRATION=1`、
`SNOWFLAKE_WORKER_ID=1022`，清除助手生命周期观测模式，沿现有 Chrome 路径。
`AGNES_LIGHTING_REFERENCE_IMAGE` 指向上述已验收原图，每批使用新的log/basetemp：

```powershell
uv run python scripts/run_integration.py `
  tests/integration/test_canvas_agnes_lighting_broker_live.py -q --tb=short `
  -p no:cacheprovider --basetemp=.runtime/agnes-lighting-broker-new-temp
```

本机实际用既有 `.venv/Scripts/python.exe ../.runtime/beeftv-resume-run.py` 创建
独占MySQL再调用该入口。四批日志 `agnes-lighting-broker-live-{a,b,c,d}.log`、
各自basetemp、红证据、d原图片/acceptance及 `agnes-lighting-broker-final-review/`
保存于忽略的 `backend/.runtime/`，不提交。b另有pytest缓存写权限warning，
c/d关闭缓存插件；没有改缓存权限或系统策略，既有Starlette弃用warning单列保留。

全仓 Ruff check、新Python format及新JS语法检查实际通过；最终两场全部后置
实跑，没有重复旧2463快测、Node整组、统一build或固定源像素门禁。六隔离栈的
66队列、12交换机被动核验不存在；60角色/执行PID无存活，4199与实际API端口无
listener、任务容器/测试桶0，MinIO原3桶、用户原服务启动时间及两源树保持。
独立2838文件基线核对仅两源码＋六文档变化，23份前片冻结源码保持；最终新增
两源码单独冻结，合计25份。未重启用户服务、重导、commit或push。

本片只关闭蓝色逆光示例的两宽度真实打光成功门禁。伦勃朗增人、c受理不明、
专注模式指针遮挡仍分别保留；mask/透明背景不属于该供应商已支持合同。
接着复验正式视频390；Responses图片成功、用户同版八角色部署、其他独立工具、
H.265/动态政策/其他离线并发、音频/企业、32ms、R23候选及完整M0–M5继续，goal active。
