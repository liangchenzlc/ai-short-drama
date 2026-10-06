# BeefTV 媒体工具与 RunningHub 源契约审计

日期：2026-10-06。源仓库：`D:\code\BeefTV`，固定 HEAD：
`4ca2a65a7780a8dfcaaa86c33679f84fb04e055c`。本次核对源工作区无未提交改动。
目标画布位于 `frontend/canvas/`；独立依赖、HTML 入口和统一启动/构建脚本沿用已完成调整。

本记录区分静态代码事实、已经证实的契约缺口和待运行验证。
阅读代码、包目录和构建成功不能替代真实 UI、MySQL、MinIO 或真实供应商验收。
本轮不修改源工具闭包、产品源码、CSS、源清单、视觉 mask 或 0px 门槛。

## 1. RunningHub 是两套不同执行合同

| 项目 | 独立应用工作流 A | 官方声明式视频包 B |
| --- | --- | --- |
| 插件身份 | `runninghub-workflow-provider` | `runninghub-workflow` v2.0.0 |
| 能力 | image / video / audio 工作流 | video |
| 上游创建 | Workflow：POST `/task/openapi/create`；App：POST `/task/openapi/ai-app/run` | POST `/task/openapi/create` |
| 上游查询 | POST `/task/openapi/outputs`，JSON 含 `apiKey`、`taskId` | GET `/task/openapi/status`；固定 manifest 未传 taskId |
| 凭据 | 工作流积分 key；上传另用企业 uploadKey | 普通渠道 Bearer key |
| 新用户管理入口 | 当前固定源未挂载 | 正式包加载并启用后走普通渠道协议入口 |
| 目标执行状态 | 尚未迁入独立配置/执行服务 | 元数据已保留，执行不支持且明确禁用 |

不能把 A 的 `/outputs`、状态数字、凭据或参数映射替换进 B，然后称为复刻。

### 1.1 A：现有配置执行分支存在，正常设置入口不可达

证据：源 `web/src/pages/settings/index.tsx` 的 `ConfigSectionKey` 只含
`channels | models`，panes 也只有这两项；`ChannelSettingsPane` 没传
`onOpenRunningHub`；`RunningHubSettingsPane` 没有生产 import/call。
插件页链接 `/settings?section=runninghub` 实际退回渠道页。
源 `backend/internal/handler/api.go` 调用 `RegisterRunningHubRoutes(api, svc, false)`，
`handler/runninghub.go` 在 false 时返回，管理 API 不注册。

插件管理并非全部禁用：catalog/status/list/activation 仍有注册。
bundled application 默认 enabled=false，旧 registry 可保留 enabled，另受 platformAvailable 约束。
Canvas Config 节点仍有 selector/workflow 分支；已有有效配置和启用状态可进入后台执行。
所以只能说当前正常管理入口不可达，不能说源没有任何工作流代码，也不能擅自恢复设置页。

源的管理路由定义包含 POST `/runninghub/workflow-info`、`/runninghub/app-info`。
上游 schema 接口分别为 `/api/openapi/getJsonApiFormat`、`/api/webapp/apiCallDemo`。
`FetchSettingsWorkflow` 不将 payload code 统一解释成失败，空 prompt 也可合法；
执行路径 `FetchWorkflowJSON` 校验 code 且要求 prompt，二者不能混用。

`resolveGenerationWorkflowExecution` 验证插件启用、RH enabled/baseURL/apiKey、selectedID、
`kind + ':' + workflowId` 和 capability。工作流不依赖普通 logicalModelId，也不继承渠道 key。
积分 key 用于管理/create/poll，企业 uploadKey 只上传，wallet 旧字段忽略。
源 LocalWorkspace 对任意参考媒体或 mask 直接拒绝（`provider/workflow/client.go`）。

执行顺序为：已知 resumed taskId 仅 poll；新任务先 paid-create/receipt 检查，必要时取 schema，
验证映射、上传，再 create。transport 错误/缺 taskId 属于 `CreateUncertain`；
明确非零业务 code 为确定失败。受理后先 `RecordAccepted`；记账失败保留 taskId，
由 `fenceWorkflowSubmission` 进入 unknown，不能自动再次 create。

参数映射还保留这些源规则：

- enabled slots 按 sourceIndex/ImageOrder 校验容量；超额参考图或无 mask 槽拒绝。
- nodeInfo 的 fieldValue 转字符串；required、min/max/step、BOOL、options 服务端校验。
- 不覆盖拓扑链接；SafeToOverride=false、Int.value、部分 ImageResize+ 字段跳过。
- 未改 widget 默认值和自动尺寸绑定不重复发送；random seed 为 uint32。
- 图片/音频默认 2.5 秒查询，视频默认 30 秒并有 transient/notfound/malformed/download 策略。
- poll code 0 必须有输出；805/806 为失败、813 queued、804 running，其余 pending。
- 图片全部保留；视频/音频循环取最后一个，最终音频优先，不改成全部视频/音频输出。
- 固定旧 COS 域名重写为 `rh-images.xiaoyaoyou.com`，相对 URL 解析为 root，CDN 下载不携带 key。

### 1.2 B：真实执行链与两项已证实的源码限制

源包：`plugin-packages/runninghub-workflow/manifest.json`。
正常打包/启用链是：官方 archive bootstrap → `protocol.LoadInstalledProviders`
→ registry/catalog → 前端 enabled provider 过滤 → 普通渠道模型协议编辑器
→ Canvas video → DeclarativeProtocolAdapter → create/poll → protected transport → 媒体归档。
本机源未找到 `.beeftv-plugin` archive，本轮没有构建包或启动 Go；这条代码链不等于 runtime 已通过。

create body 使用 namespace.model 或 request.model，input 使用 namespace.input 或
`{prompt, images, videos, audios}`，prompt 可为 namespace.workflow 对象。
version/client_id/webhook/webhook_events_filter 可选。
metadata 声明的 duration/ratio/resolution/generateAudio/watermark 未由此 create 模板直接映射。

**已证实缺口 1**：poll 只有 GET `/task/openapi/status`，无 query/body/pathTemplate/taskId 插值。
通用 `BuildPoll/buildManifestOperation` 不自动补 taskId，因此实际 wire 无 taskId。
不能臆造 `?taskId=...` 或换成 A 的 POST outputs。同步 create 返回媒体仍可能成功，
不能由此宣称包在所有情况下必然失败；异步查询/恢复合同确有缺口。

**已证实缺口 2**：manifest ParseCreate 可读 `prompt_id`，但
`app/provider_call_log.go` 的持久恢复 ID 只读 task_id/id/request_id/name，未读 prompt_id。
B 的生成协议链没有 A 那样显式 `RecordAccepted`。仅 prompt_id 响应不能被描述为重启恢复完整。

response.taskId coalesce id/request_id/prompt_id/context taskId；
status coalesce status/state/data.status/pending；videos=response.data，errorPaths=[error.code]，
resultEphemeral=true。通用文字状态不等于 A 的 804/805/813。
pending 但有媒体可推为成功；无媒体 succeeded 最终失败。没有 cancel operation。
默认视频 poll 30 秒；恢复/人工 query 仅 poll，不 create。
FinishProtocolResult 下载全部引用，但最终 video/audio 取第一个、图片全部保留，和 A 不同。
没有找到本包专属源测试，A 的测试不能充当 B 的验收。

### 1.3 目标边界与后续接法

A 在目标缺独立 RH settings、可信 plugin status/activation、Python adapter。
`host-model-config` 偏好不含 runningHub，目标 admission 禁止客户端 key 并要求 logicalModelId，
runtime DTO/config 和 generation options 不接受 A 的嵌套 execution/workflow 字段。
不能绕过凭据防线或把 A 当普通渠道来补齐。

B 的包元数据已保留，但不在 `PROTOCOL_ADAPTERS`；catalog 返回
enabled=false / executionSupported=false / unavailableReason，UI 因而不可选。
保持明确不可执行，先锁定实际供应商 wire 合同和固定源缺口的处理依据。
单纯加 catalog 字符串、虚构 poll 接口不能满足一比一要求。

可复用目标本人 `canvas_model_catalogs` 加密凭据、稳定 `canvas_channel_models` logical ID，
async_tasks initiated_by、generation record 参数/凭据快照及 prepared/sent/unknown，
Celery leases/recovery、protected HTTP、媒体探测与 MinIO 归档，task bindings/references/results
和 write receipts。新增 adapter 需贯通 Gateway/参数冻结/poll/query 白名单/归档；
sent/unknown 禁止 retry，已知 provider ID 才能 resume，人工 query 不 create/不换 ID。

## 2. 保存、提交、恢复、回填不能混为同一条路径

源主 executor 先建立占位节点/children/connections，updateProject，随后提交。
普通首次生成 clientOperationId 可选；createId 虽声明但该调用未使用。
proposal 固定 `proposal:${proposalId}:${node.id}`；retry 为稳定 SHA256 context。
image count 1–15，子节点各一个任务，count=1，有 base ID 时加 index。
源 runCanvasGenerationSubmissionOnce 按 nodeID 合并 promise、拒重复 fingerprint；
runCanvasGenerationTaskToConsumer 按可选 operation ID 上锁，task-created 先绑 metadata。
源并没有目标现在全部首次提交的持久 journal/强制来源 flush，不能把新增保护写成源既有实现。

源服务端先查询本人 operation ID，hash 一致复用，再验证权限/媒体、加密并持久 queued。
submission_unknown/uncertain 禁 retry。恢复仅观察旧 task；远程缺 task ID 可按 project/node
查前 100 个任务，local 缺 ID 不盲目复活。
主 consumer hydrate 输出 → 持久当前图 → POST `/ops/canvas.task.bind`，
effect key 含 taskId/nodeId/outputIndex，采用 canonical receipt 并检查 scope/project。
源 document commit 为 POST `/ops/canvas.document.commit`。

mask/angle/emotion 等 AI 工具另走旧 runBackendCanvasGenerationTask → 手动 upload/persist，
不是主 executor durable consumer。目标 adapter 已补 operation ID，不能说完全不能提交；
canonical URL 再 upload 是否产生重复资源、恢复和绑定仍须独立验收。

## 3. M3 图片三工具：真实可达的 source → target 链

源 `canvas-node-toolbar.tsx` 正常宽度图片 process 只暴露 crop/split/annotation。
源保留 resize/upscale/portraitTexture 定义不意味着正常入口可用；superResolve 未有生产定义。
窄宽度的 compact 分支另聚合工具，需单独按源实测；不要扩展默认入口来补功能。

| 工具 | 原交互/处理 | 派生图与保存 |
| --- | --- | --- |
| crop | 节点 inline 选区，8 个手柄、拖拽、Escape/取消/确认；默认 x/y=.1、宽高=.8 | resolve → browser cropDataUrl PNG → uploadImage → 新 child/connection → 选择 child/打开节点编辑 → persistMediaNodes |
| grid | 图片工具菜单内宫格 picker | browser splitDataUrl → 全部 pieces 并行上传成功 → 全部 children/连接 → row-major 布局/全选 → persistMediaNodes |
| annotation | 原节点 inline editor；画笔/矩形/文字、颜色/粗细、撤销/重做、关闭/保存 | 导出 PNG → uploadImage → 新 child/connection → 选择/关闭标注 → persistMediaNodes |

迁入同路径文件：`src/pages/canvas/use-canvas-media-tools.ts`，
`src/lib/canvas/canvas-image-data.ts`、`canvas-grid-split.ts`、`canvas-media-persist.ts`，
crop inline editor、grid picker、annotation inline editor/model。核心 UI/算法保持源实现。

crop x/y floor，宽高 ceil，canvas 最小 1px；无 crop 参数时是中心方形。
默认 37×19 图会输出 30×16，inline 尺寸标签用 round 显示 30×15；验证保留此源行为。
child 在源右侧 96px，同 y；title 为 `源标题 · 裁剪`，resultOrigin=derived，
generatedFromNodeId 指源，保留作者 prompt；原图不替换。
grid floor 分界保留奇数末行/列；manualSize=true，title 含 row-column。
Promise.all 全部 resolve 后一次性建立全部 children；上传抛错时不建半组，
但 uploadImage 对暂时错误可返回本人 IndexedDB fallback，不能把 resolve 都叫远端上传成功。
因此失败切片必须区分拒绝/抛错与 503 fallback；已成功上传的私人中间资源可能保留，
不能叫共享成品，也不能让未完整确认的组成为远端半组图。

目标上传边界：image-storage 从 `/canvas-app/canvas/<key>` 取 X-Canvas-Key；
host-resource-upload 提供稳定上传身份/X-Idempotency-Key，真实 Python/MySQL/MinIO 返回 resource:key。
ensureCanvasNodeAsset 绑定本人素材库（`/canvas-runtime/assets/<id>`）。
persistOwnedCanvasMediaNodes 合并 live/stored 图、更新 store、flush，再 sync/readback。
源 GET `/canvas-projects/<key>` 映射 my-document；源 document commit 映射
`projects/<id>/canvases/<id>/commits`，带 version/Idempotency-Key。
共享 metadata 允许稳定作品 locator/尺寸/派生来源；prompt/task/config 与本人资产库保持隔离。
三工具不需要新增表、DDL 或服务端 FFmpeg 任务。

## 4. crop 最小切片验证与报告边界

新增脚本 `frontend/canvas/scripts/verify-image-tools-python.mjs` 与集成测试
`backend/tests/integration/test_canvas_image_tools_browser.py`。
测试资料使用 37×19 奇数尺寸、逐像素可区分的 PNG。
账号/项目/成员邀请由真实认证 API 准备；原图片由真实 input 上传；
派生图必须通过原图片工具菜单和 inline 确认产生，不以 API 代建成功图。

步骤及必须证据：

1. 正常路径不拦截 API。确认源节点/原资源不变，新 child、connection、derived 元数据、
   右侧 96px 定位；实际 PNG 30×16 与预期 `(3,1)-(33,17)` 原像素逐点相同。
2. 开启选区，真实指针拖动、取消；重新打开恢复源默认选区，取消期间不上传/建 child。
3. 全新 browser context（只带 cookie、无 IndexedDB/Blob cache）读回 MinIO endpoint，
   页面 naturalWidth/naturalHeight 正确；Python 再直接读对象字节、MySQL node/reference/library。
4. **画布提交 503 注入**：上传和 asset registration 真实成功，仅提交被拦截失败；
   记录 wire，服务端没有新图/连接/共享作品，原浏览器草稿仍有派生 child；
   解除故障后用原保存入口恢复同 child，不重新裁切/上传。
5. **资源上传 503 注入**：仅上传失败，不能描述为真实断网；记录上传失败和是否发送 commit。
   源 fallback 可保留 IndexedDB 图，不得称远端已保存；草稿/失败反馈及刷新范围按实测报告。
6. 成员能读明确保存的共享作品字节；本人 asset 和私参隔离；第三方项目/document/media 返回 404。
   保存失败的私人中间媒体对成员不可读。

当前 crop 最小切片状态：**完整测试通过：1 passed、0 skipped、29.57 秒**。
第四轮 native-d 使用 task-owned 随机 MySQL、隔离 MinIO 和实际 Chromium；
全部正常/取消/刷新、commit/upload 两阶段仅 HTTP 503 故障注入、草稿/同 child 恢复/共享与权限
均执行通过，page_errors=[]。最终 Python 独立逐像素比较、3 份 MinIO 对象字节/hash、
canonical 3 nodes/2 edges、节点位置/来源/尺寸、本人 upload/asset binding 均执行通过。
上传失败实际未发成功 commit；报告记录两处真正可见的就近错误文本。
报告 `.runtime/canvas-crop-native-d-report.json`、日志 `.runtime/canvas-crop-browser-native-d.log`。
runner 已复核 MinIO test buckets=0、native directory=0、4199/4194 listener=0、测试进程=0。
这只证明 crop 最小切片，不证明整个 M3 或完整画布迁移。

以下保留此前验证驱动的红证据，三次均未修改产品源码或放宽业务断言：
runner 首轮使用 task-owned native MySQL、隔离 MinIO 和实际 Chromium：1 failed，47.85 秒。
原上传、指针拖拽/取消、默认 30×16 裁切上传与保存、派生位置/标题/连接、原图不变、
fresh context 的真实资源读取均已到达相应断言；此时还未执行 Python 的最终逐像素/对象复核。
刷新后测试只 hover 源节点，未单击选中，`图片工具` 按钮等待 20 秒超时，
因而没有进入 503 注入，不能声称失败草稿/恢复/权限已通过。
源码 `use-canvas-render-model.ts` 明确 toolbar 仅依单选节点显示；
验证 helper 已改为原 UI 单击源节点再 hover，不改产品源码。
首轮报告、截图与 DOM 归档在 `.runtime/canvas-crop-native-a-*`（不提交）。
第二轮 1 failed，46.50 秒：选中修正有效，进入 commit 503，两个请求复用同一幂等 key。
随后测试匹配了已隐藏的 toast 而超时；源持续显示 `画布保存状态：云端未保存`，
并自动打开带 `role=alert` 的“服务暂时不可用，请稍后重试。”反馈。
已依据 `canvas-sync-status.tsx` 改测该持续反馈；未放宽草稿、共享、字节断言。
第二轮尚未执行后续草稿/恢复/权限断言，红证据 `.runtime/canvas-crop-native-b-*` 保留。
第三轮 1 failed，51.08 秒：commit 503 的可见反馈、本人草稿、不共享、同 child 恢复、
不重传均到达断言；随后资源上传 503 已注入，但测试等待 toast 超时。
已核对源与目标 `styles/globals.css` 都明确 `.ant-message/.ant-notification { display: none !important; }`，
不能测试全局 toast 可见。真实持续反馈是保存 Popover 的
“画布媒体尚未持久化，原有草稿已保留，请完成上传后重试”。
验证改为该 `role=alert` 与云端未保存状态；未改 CSS、未删除反馈断言。
第三轮上传草稿/最终权限/Python 字节断言尚未执行，证据 `.runtime/canvas-crop-native-c-*` 保留。
已实际执行 `node --check scripts/verify-image-tools-python.mjs`，新增 Python 测试的
`ruff check`、`ruff format --check` 通过；`pytest --collect-only -q -p no:cacheprovider`
收集到 1 条测试。这些静态检查本身未启动 native、未执行实际裁切；
上述首轮真实运行由统一 runner 另外执行。
真实运行入口：在 backend 使用现有隔离测试环境，显式开启
`RUN_CANVAS_RESOURCE_MINIO=1`、`RUN_CANVAS_BROWSER_INTEGRATION=1`，执行
`python -m pytest tests/integration/test_canvas_image_tools_browser.py -q -p no:cacheprovider`。
浏览器脚本独占 Vite `127.0.0.1:4199`；API 由测试使用隔离 factory 自启，
需 runner 协调已有 native 所有权，不重复启动数据库、供应商 fixture 或 Worker。
grid 正常预设/自定义切片已通过下述 native-b；annotation 暂为静态审计，尚未通过真实运行。
保存冲突 409、导航/换账号中途结果、删除中途是否复活仍为后续矩阵，不冒称已通过。
静态发现三 callback 在首个 await 前没有统一捕获 scope/project lifetime，
这是一项待实测风险，尚未复现为 bug；不在本轮擅改 source 闭包。

grid 后续正常验证文件为 `verify-image-grid-python.mjs` 与
`test_canvas_image_grid_browser.py`，范围为 4/9/16/25 预设、3×2 自定义、
无效 1×1/关闭菜单无副作用、odd-size floor 分界/尾部、row-major 标题、48px 组内间距、
私人资产、全新 context、MySQL/MinIO 全部字节与共享权限。测试准备账号和独立画布，
派生节点仍须通过源菜单操作生成。部分上传/提交失败是后续独立矩阵，不能由正常路径推断通过。
grid 驱动 Node 语法、Python Ruff/check format 和 collection 1 条已通过。
首轮 native-a：1 failed、0 skipped、80.61 秒；4/9/16 的浏览器阶段走完，
25 宫格严格坐标断言失败，custom/最终 Python 65 对象复核未执行。
已核对源累计 `x += (columnWidth + gap)`，测试先 reduce 再加锚点重排了 IEEE754 运算：
原锚点 325.7894736842105，前四列宽
550.6666666666667 / 550.6666666666667 / 629.3333333333334 / 550.6666666666667，
源累计得到 2799.122807017544，原测试得到 2799.1228070175443，差 4.547473508864641e-13。
这是已证实的测试表达式差异，未认定为产品/持久化缺陷。
验证改测锚点、同列/同行对齐、相邻列/行的最大尺寸+48px；仍全部严格等值，
无 epsilon、无门槛变化、不改算法。红证据 `.runtime/canvas-grid-native-a-*` 保留。

第二轮 native-b：1 passed、0 skipped、100.53 秒、exit 0，`step=complete`、
`page_errors=[]`。4/9/16/25 预设及 3×2 自定义全部经原菜单创建，
5 个全新 context 读取真实派生图；60 份派生图逐像素核对、37×19 奇数尾部
重组一致，65 份 MinIO 对象原始字节/hash、MySQL nodes/edges/作者归属、
成员共享作品与私人资产/第三方 404 断言全部执行。
绿证据 `.runtime/canvas-grid-native-b-report.json` 与
`.runtime/canvas-grid-browser-native-b.log`；runner 核实测试 bucket/native 目录、
4199 listener 均为 0。只覆盖正常宫格矩阵，部分上传、commit 失败及保存冲突等未验。

annotation 没有需要新增的专用 HTTP 接口；上传/私人资产/document commit 仍接既有边界。
已证实源码事实：源与目标 editor.save 均只有 try/finally，无 catch，按钮 `void save()`；
父级 onConfirm 直接 await saveAnnotatedImageNode，后者 upload/persist 也无 catch。
grid 的 onSplit 同样 `void splitImageNode`，该 callback 无 catch。
HTTP 拒绝可能形成未处理 Promise，是待运行风险；没有把它写成已复现缺陷，
也不悄改 source 的错误处理或将异常传输描述为已验收。

## 5. M3/M4 媒体后续边界

- 转换节点正常内置注册；浏览器 grayscale/Canny 可达。video 只取首帧，advanced video 明确 unavailable。
- depth/lineart/pose 图片实际调用 `/depth-estimation`、`/lineart-estimation`、`/pose-estimation`
  status/run，本地 JSON schemaVersion=1、operation/dataUrl，返回 pngBase64/width/height/modelId/cpu；pose 有 personCount。
- transparency 背景去除等 unsupported 保持源 unavailable；不能由注册名推断支持。
- conversion Blob 进 IndexedDB `image:<scope>:media-conversion:<node>:<fingerprint>:<operation>`，
  metadata resultStorageKey。目标 normalizer 归档需独立证明，Blob URL 不能当数据库媒体身份。
- 视频 crop/trim/音轨提取/merge 保留浏览器 FFmpeg 独占 lease，不能改为服务器 FFmpeg。
- ASR/后台成片 render/render-plan/depth-captures 是真实后端路由，
  `/timeline/transcriptions`、`/timeline/renders`、`/timeline/render-plan`、`/depth-captures` 不能仅返回 metadata。
- depth video 有 stable client ID、unknown 重放、观察与 session；目标尚缺完整执行。
- Timeline canonical Go compiler 仍须移植 Python；其编译/输出合同按迁移方案独立验证。

完整一比一验收还包括上述媒体、生成、助手与源真实运行对照。
本次审计或 crop 切片通过均不能将整个迁移标记完成。

## 6. 图片到普通文字节点的原 UI 验证准备

新增独立 `frontend/canvas/scripts/verify-text-images-python.mjs` 与
`backend/tests/integration/test_canvas_text_images_browser.py`。
复用真实本机 HTTP Chat fixture、真实 Gateway/执行器及隔离 MySQL/MinIO，
两张红/蓝原图须经原上传 input 创建；原输出连接点菜单新建空文字节点，
第二张图经原 rail 拖拽入边，原生成按钮提交且先保存提示词。
引用顺序以源 connections 入边顺序为准，实际供应商获取临时签名 URL 并读取两份 MinIO 字节；
核 wire system/user prompt 与顺序，不调用付费模型，也不以 API 代建生成结果。

普通文字节点沿源终态回填合同：供应商 stream 的真实第一段已经进入本人 delta 表时，
节点仍为 loading/空正文，页面不请求 `text-events`；立即释放供应商终态后由原消费者
绑定同一节点，全新仅 cookies context 恢复正文及两张真实 MinIO 原图。
另核原图片/system 未变、MySQL nodes/edges/result/media reference、私人任务/增量与共享作品权限。
Node 语法、Ruff check/format check 与 collection 1 条已通过，真实浏览器运行待统一 runner。
未覆盖其他文字协议、配置 composer、绘图引用、文本份数、保存冲突或流异常；
任务 SSE 专项与源普通节点展示的差别见 [文本审计](2026-10-06-beeftv-text-multimodal-thinking-audit.md#61-普通文字节点与任务-sse-的调用者边界)。
