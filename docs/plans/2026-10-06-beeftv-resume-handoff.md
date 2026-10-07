# BeefTV 无限画布迁移接续文档

**2026-10-07 AI 创作助手专项实施与分层验证已收尾：** 标准分集与画布已接入同一个项目级纯对话助手，公共面板、消息、输入和 CSS 直接抽取当前画布助手；不要再制作第二套画布助手。分集页面已去除创作模式切换，顶部保存状态左侧提供“AI 创作助手”，原生成表单、节点生成与手动编辑保留。新聊天不调用创作工具，附件、素材引用、个人 Skill、历史、排队、SSE、停止与恢复共用本人项目权限；旧会话只读查看，既有任务和候选仍按原许可处理。Python/API、来源保存屏障和数据库改造已实施，业务库 `short_drama` 已执行单个 ALTER，旧会话 **27→27**；同版本 API、scheduler、Agent Worker 已更新。后端 unit/API **1736**、新旧 Agent 隔离 MySQL **101**、新助手专项 **25**、独立助手集成 **15** 及追加集成 **1** 已实际通过。最新前端 Node 为宿主 **218**、画布 **223**，源码检查 **832 文件 / 178 适配 / 91 删除**；标准素材关闭恢复复测 **19 passed**、助手导航 **10 passed**，画布宿主/助手完整专项 **4 passed**。组合生产产物上标准助手 **14 passed**、画布助手 **2 passed**；之后仅补齐历史名称120字上限，再执行最终统一build及生产历史专项 **1 passed**，未冒称14/2在微修后整批重跑。标准/画布1440与390四图已实际查看，各批次范围存在交集不相加。第 6.5 节记录证据与未验范围。完整迁移未完成，此前完整迁移 goal 为 `paused`，本轮按用户“继续”完成助手专项；没有真实模型或供应商验收。

**2026-10-06 用户最新范围纠正：只迁移无限画布编辑器，模型配置、项目和资产统一由当前工作台管理，不再显示 BeefTV 首页或整站管理页面。** 本轮已物理删除 77 个整站/导航/品牌文件，所有旧整站深链转宿主，退出画布回 `/projects`；误撤权根因是本机 API 来源仍为 8080，已对齐用户实际 8081，并修正前端 CSRF 403 分类。先读[整站清理与故障记录](2026-10-06-canvas-only-cleanup.md)和[实施记录](2026-10-05-beeftv-implementation-log.md)，再读[管理功能对齐审计](2026-10-06-canvas-host-management-alignment-audit.md)。页面收敛不代表宿主项目/资产数据语义、画布级导入/恢复入口全部迁完；下文此前暂停快照中保留源管理页的内容不能作为后续目标。

模型统一和整站清理已按用户要求提交并推送，当前实施起点为 `e1b72e832fda9ba121c57a519974aed91ff1d3c3`，助手修改前工作树干净。下文更早的 HEAD 和“大量未提交改动”属于历史快照。本次助手专项尚未提交或推送。

早期暂停快照时间：2026-10-06 16:58（Asia/Shanghai）；2026-10-06 已追加用户单独授权的助手专项实施进度，2026-10-07 继续收尾。下文历史切片保留原证据，当前助手状态以顶部与第 6.5 节为准，供下次读取后继续。

**完整迁移尚未完成；此前完整迁移 goal 的系统状态仍为 `paused`。** 后续模型统一是用户单独授权的定向修改；不能因这一切片完成而将整个迁移目标标为完成。下次继续时先读最新实施记录，再按管理功能对齐审计和第 8 节安排待办，不重复导入已迁移的画布包。

**最新整站清理验证已完成：** 画布 Node 218、统一生产 E2E 2、真实 Python/随机 MySQL 浏览器 1 项通过，无集成跳过；真实 CSRF 403 不冻结、恢复保存及 14 旧深链已验。统一构建通过，独立/合并画布 874 文件一致；来源清单固定 832 个，168 适配中包含 77 条物理删除记录。`source:check` 现在支持显式删除，原文件重新出现会失败，不能重导入恢复首页。前端仍 8081/8082，本机 PUBLIC_ORIGIN 已对齐 8081；六类 Worker 已恢复，测试库已清理。当前切片没有新增提交推送。原整站库、资产分类、文件夹和回收站浏览器 harness 仅为历史资料，需改为宿主/图内入口后才能恢复验收。

**本轮模型统一已实施并完成专项验证。** `/ai_config` 为唯一模型管理入口，运行 profile、第二密钥、headers、模型测试与 BeefAPI 连接已统一；真实业务库三列已于 19:15 执行，原模型和旧选择保留。最终后端单元/API 1711、宿主 Node 205、画布 Node 210、宿主配置 E2E 18、真实 Python/MySQL 配置与企业浏览器 4 项通过；媒体 MinIO 和 Agent/标准 MySQL 回归、统一构建通过，服务已恢复。主动探测授权已收到并实现。完整事实、资源故障和清理证据见实施记录最新章节；本轮没有真实供应商验收，也未提交或推送。

## 1. 接续入口与信息优先级

下次先读取本文件，再读取涉及下一切片的代码、测试与接口说明。入口如下：

| 文档 | 用途 |
| --- | --- |
| [仓库协作约定](../../AGENTS.md) | 修改范围、验证、数据库迁移及无限画布的专用边界 |
| [完整一比一迁移方案](2026-10-05-beeftv-infinite-canvas-migration.md) | M0–M5 的目标、功能矩阵、数据模型、源行为与最终门禁 |
| [实施记录](2026-10-05-beeftv-implementation-log.md) | 已实施切片、失败原因、修复与历史验证证据 |
| [画布 API](../api/canvases.md) | 当前 Python HTTP 合同、权限、幂等、任务及支持范围 |
| [画布子包说明](../../frontend/canvas/README.md) | 实际目录、依赖兼容、统一脚本和验证入口 |
| [媒体工具审计](2026-10-06-beeftv-media-tool-audit.md) | 图片工具、RunningHub、媒体服务与原 UI 验证边界 |
| [文字多模态审计](2026-10-06-beeftv-text-multimodal-thinking-audit.md) | 普通节点、composer、Agent 分支、引用与 thinking 的源码事实 |
| [绘图视觉诊断](2026-10-06-beeftv-drawing-visual-diagnosis.md) | 尚未关闭的 4px 差异、独立尺寸问题及实验边界 |
| [画布数据库迁移记录](../数据库模型/migrations/2026-10-05-infinite-canvas/README.md) | 新库/旧库升级与实际业务库执行记录 |

本文件描述暂停时的状态；后续继续实施必须核对实际代码与新验证结果。历史审计是分析证据，不等于其列出的功能已经实施。
实施记录早期的 `802` 个源文件、`78` 张表、旧目录编辑拒绝、旧视觉全零、尚未启动 scheduler 等描述属于当时的阶段基线，后续已有变化。不要依据这些旧段落重复开发或宣称当前功能缺失。
早期暂停基线的 source 检查为 **832 个源文件 / 91 项显式适配**，业务库记录为 **93 张表 / 41 张画布表**。后续整站清理与助手替换已有新增适配/删除记录，不能将该旧计数当本轮最终检查结果；助手复用现有表，不新增业务表。

## 2. 用户已确认的目标与约束

- 只读源项目：`D:\code\BeefTV`，固定 commit `4ca2a65a7780a8dfcaaa86c33679f84fb04e055c`。不得擅自切换到其他版本作为对照基准。
- 目标项目：`D:\code\ai-short-drama`；本次核对 HEAD 为 `4e24eefdce3e5aab2f124e9ae3ad66a153a2657e`。已有大量未提交的迁移、Agent 与标准创作流程改动，全部保留，不 reset、不覆盖、不主动 commit。
- 创建项目提供“标准模式”和“无限画布模式”；默认标准模式，标准模式保持既有功能。
- 目标是完整创作画布，分阶段交付；布局、交互、操作、默认值、功能及源实际启用的运行分支都要一比一对照。
- 数据模型允许一个项目有多个画布，首版主要提供主画布；不要改回一个项目只存一个画布。
- 首版成员共享与保存冲突保护已纳入范围；实时多人拖拽/编辑协同后续加入。
- 历史 BeefTV 项目和媒体的批量搬运工具后续交付；源本来已有的画布归档、ZIP/JSON 导入导出仍属于本次迁移范围。
- 前端使用 React，后端接当前 Python/FastAPI，业务数据以 MySQL、MinIO 为准。
- **`frontend/canvas/` 已完成独立子包调整**：保留独立 `package.json`、锁文件、依赖目录、HTML、React root、Provider、CSS、字体和媒体依赖。宿主统一安装、启动、构建、预览。
- 共同声明且版本不一致的直接依赖采用宿主版本，源独有依赖独立保留；不为画布升级标准前端依赖，不把两包合为单一依赖或入口。
- 无限画布普通节点生成保留源直接回填流程，标准已有生成继续原候选采用规则。用户本次明确将两端助手替换为共用纯对话：不执行图操作、生成提议或回合撤销，不补原助手工具运行合同。私人参数、凭据、任务、生成历史与对话按本人隔离，明确进入共享图的作品按项目权限共享。
- 源禁用的创建入口保持同样状态，不能为凑齐功能表擅自启用。

## 3. 阶段进度与完成口径

“前端代码迁入”“服务适配已实现”“专项运行通过”“阶段完整验收”是不同状态。没有可靠的逐项完成分母，本次不记录估计百分比，也不按测试数量换算完成率。

| 阶段 | 暂停时状态 | 主要剩余 |
| --- | --- | --- |
| M0 源基准与兼容 | 固定源、依赖兼容、独立构建和多组对照已建立 | 完整启用功能与动作基准、逐帧动效/触控/媒体对照；正式绘图视觉仍有差异 |
| M1 双模式与持久化 | 主体大量落地，已执行相关真实 DDL 与专项联调 | 完整草稿/会话恢复、删除与资源生命周期尾项、生产静态部署、伴随入口与大图性能 |
| M2 生成与回填 | 多个切片已实现并验证 | 剩余协议、引用类型、批次、结构化产物、完整日志/错误行为与真实供应商验收 |
| M3 创作工具 | 绘图、裁切、宫格等多项切片已落地 | 标注与其余工具、批量创作、文档类操作及完整异常/恢复矩阵 |
| M4 媒体与导演 | 前端闭包和部分存储基础已经迁入 | 浏览器媒体工具、ASR、Python 时间线编译/渲染、全景调色、导演及真实媒体验收 |
| M5 助手与完整对照 | 部分归档基础已实施；项目级共用纯对话助手专项实施与分层验证已收尾；工作流未整体交付 | 真实模型与产品反馈、工作流/插件、剩余导入导出、全量标准与一比一回归；源助手执行工具目标已按用户新需求替代 |

**M4、M5 是主要剩余大块，M2、M3 仍有实质功能开发；没有任何一个 M0–M5 阶段可以据本文件宣称已完成全部门禁。** 完整目标继续以迁移方案为准。

## 4. 已落地内容与不能重做的基础

### 4.1 项目、构建与持久化

- 双模式创建、稳定创建键、独立画布入口、主画布和项目/画布关系、可信账号及项目范围检查。
- 节点/连线存储，共享作品和作者私人投影；串行保存、版本 CAS、幂等回执、409 保稿、未知请求恢复。
- 不可变历史、个人视口/外观的独立保存，旧图保存或恢复不会覆盖新的个人设置。
- 工作区分页与批量读取、项目文件夹与私人归属、原素材库和分类、回收站目录/受限预览/显式恢复及永久处置。
- 上传、分片恢复、稳定资源、跨项目独立副本及来源链、原素材匹配与归一化、资源引用保护及多项真实 MinIO/权限验证。
- 绘图持久化、绘图历史冻结与原子恢复、多项复制/粘贴/归档恢复；混合媒体 ZIP 与文件夹/自定义封面的部分归档组合已验。
- 原设置页、渠道/profile、目录保存、默认模型、独立模型测试、任务历史与 BeefAPI 连接/托管目录已接 Python。正式企业登录和收费供应商未验。
- 完整独立媒体构建、宿主统一产物和依赖兼容已经实现；**不要重跑 importer 覆盖适配代码**。

统一命令仍在 `frontend/`：`npm run install:all`、`npm run dev`、`npm run build`、`npm run preview`。
单包启动保留 `npm run dev:standard`、`npm run dev:canvas`；统一开发默认标准端口 8080、画布端口 8082，构建产物组合至 `frontend/dist/canvas-app/`。
不要仅因缺少某个后端接口，就切回 IndexedDB 本地媒体存储来让界面看似可用。

### 4.2 已有生成与视频切片

已接专用 `/canvas-runtime/tasks`、可信来源保存、本人任务/结果、源结果消费者绑定、冻结能力/凭据、未知提交保护、重试/查询许可及部分真实 broker 场景。
新增三个视频适配器已稳定：`canvas_openai_videos.v1`、`canvas_newapi_video_generations.v1`、`canvas_beefapi_seedance_video.v1`。
包括 multipart/JSON、Seedance 预上传、可信媒体元信息、参数默认值/能力校验、Channel 2 HTTPS 替换、unknown 不重发、原 ID 查询和下载耗尽保护。

最终视频阶段 unit/API **1580 passed**，专项三视频单元 **138 passed**；native 分波次 **46 个唯一场景**全部通过，下载恢复 guard **4 passed**。
46 是失败批次修正后分波次闭合的唯一用例数量，不能写成某一失败命令一次全绿；详见实施记录原始日志。
该片已部署重启同版八角色，但不能覆盖后续新增的文字/缓存生产代码。

## 5. 本轮最新切片的实现与证据

### 5.1 原 UI 裁切：专项通过

验证文件：

- [浏览器脚本](../../frontend/canvas/scripts/verify-image-tools-python.mjs)
- [Python 联调测试](../../backend/tests/integration/test_canvas_image_tools_browser.py)

正常流程使用原上传、inline 裁切、拖动取消、派生节点/连线与原素材保留。覆盖全新 context、commit 两次 503 后同键保稿/同节点恢复且不重传、upload 503 仅本人本地草稿、成员作品权限及第三方 404。
最后 Python 逐像素、MySQL 归属与三份 MinIO 对象字节/hash 断言全部执行。
37×19 PNG 默认选区导出 **30×16**，矩形 `(3,1,33,17)`；源标签 round 显示 30×15，真实导出 ceil，必须保留源码事实。

最终 `.runtime/canvas-crop-browser-native-d.log`：**1 passed / 0 skipped，29.57s**；`.runtime/canvas-crop-native-d-report.json` 九项 true，`page_errors=[]`。
a/b/c 红证据保留，分别涉及 driver 对选择态、隐藏 toast 和反馈入口的理解。源全局 toast 被 CSS 隐藏，真实持续反馈在“云端未保存”的 Popover；没有改 UI/CSS/裁切算法或删除错误反馈断言。

### 5.2 原 UI 宫格切图：正常矩阵通过

验证文件：

- [浏览器脚本](../../frontend/canvas/scripts/verify-image-grid-python.mjs)
- [Python 联调测试](../../backend/tests/integration/test_canvas_image_grid_browser.py)

4/9/16/25 预设、3×2 自定义、无效 1×1、Escape 取消、row-major 标题、严格锚点/同行列/相邻最大尺寸+48px、五组 fresh context 与权限通过。
最终 Python **60 份派生图逐像素及奇数尾部重组、65 份 MinIO 字节/hash、65 节点/60 连线**及归属/私人素材 404 全执行。

最终 `.runtime/canvas-grid-browser-native-b.log`：**1 passed / 0 skipped，100.53s**；报告 `.runtime/canvas-grid-native-b-report.json` 为 `step=complete`、`page_errors=[]`。
a 的失败是测试重排 IEEE754 累加运算产生 1 ULP 差异；按源累加关系修正 driver，仍严格等值，未加 epsilon 或改门槛。
**部分上传/commit 失败、409、导航/换账号、删除中途结果尚未验；标注仍只完成静态审计。**

### 5.3 Chat 文字图片引用：后端联调通过，原 UI 待执行

主要新增文件：

- [文字参数信封](../../backend/src/short_drama/schemas/canvas_text_parameters.py)
- [Chat 多模态 recipe](../../backend/src/short_drama/ai/canvas_text_adapters.py)
- [文字引用准入](../../backend/src/short_drama/service/canvas_text_admission.py)
- [引用单元测试](../../backend/tests/unit/test_canvas_text_references.py)
- [真实 HTTP/MySQL/MinIO 联调](../../backend/tests/integration/test_canvas_text_image_runtime.py)

调用链接入 `canvas_generation_inputs.py`、`ai/adapters.py`、`ai_generation_service.py`、`canvas_generation_service.py`。
普通标准 TextInput 仍为字符串；仅画布 Chat 闭合引用信封构造最后一条 user 的 text→image_url 顺序，system/history 保持字符串。
准入基于保存的图片能力、实际项目范围和永久 MinIO 资源，检查 MIME/字节并剔除客户端伪造元信息；未声明图片能力默认 `maxImages=0`。
Worker 使用既有 `_input` 执行期签发 URL，签名 URL 不持久化。保存 `streaming=false` 沿源关闭流，Chat 流含 `stream_options.include_usage=true`。
当前非视频最多 16 图、PNG/JPEG/WebP/GIF 是本片边界，不能宣称覆盖源所有多模态配置；Responses 等未在本片解锁。
UINT64 overflow 在数据库前拒绝。

`.runtime/canvas-text-image-runtime-native-a.log`：**14 passed / 0 skipped，35.82s**。包括 stream/nonstream/保存关闭流、供应商真实读取签名 MinIO 原字节、system/history/顺序、排队后配置变化仍用冻结快照、同键交换顺序 409、本人 delta/SSE 游标和 Last-Event-ID、重放零新 POST、401 单次失败、真实 chunk 断流后 unknown 保留 FIRST 且不自动 retry/resume、八项原子边界零新任务/记录/绑定/POST，以及私人未发布图片/无秘密泄露。
相关五模块曾 **209 passed**；后续文字/缓存/runtime 合同最终专项 `.runtime/canvas-text-refresh-last-unit-g.log` 为 **111 passed**。这些计数存在范围交集，不能相加为唯一用例总数。

**源普通文字节点没有传 `onTextDelta/useTextEvents`，保持 loading/空正文直到终态后回填。服务端私人 SSE 可恢复，不等于普通节点逐字展示；不得擅自新增逐字输出。**

原 UI 待运行文件已准备：

- [原界面浏览器脚本](../../frontend/canvas/scripts/verify-text-images-python.mjs)
- [原界面 Python 联调](../../backend/tests/integration/test_canvas_text_images_browser.py)

仅 Node 语法、Ruff/check format 和 collection **1 条**通过，尚未启动 native；14 条后端联调不能代替此原 UI 验收。

### 5.4 旧目录执行缓存升级与并发：专项通过

主要涉及 [目录服务](../../backend/src/short_drama/service/canvas_model_catalog_service.py)、[画布生成服务](../../backend/src/short_drama/service/canvas_generation_service.py)、[执行服务](../../backend/src/short_drama/service/generation_execution_service.py) 和 [缓存刷新单元测试](../../backend/tests/unit/test_canvas_model_runtime_refresh.py)。

- 新任务在构造 `_config` 前按已保存的本人活动目录/profile 刷新派生 cache，深拷贝文字/视频能力；不同才推进配置版本，相同完全 no-op。
- GET 保持只读，不修改目录、偏好或凭据；unbound 标准配置不动，同键 replay 在刷新前返回原任务/快照。
- 统一 **catalog→model** 锁顺序；refresh 后在原事务内显式 `session.flush()`，避免 `autoflush=False` 下 `_config(populate_existing=True)` 覆回旧 cache。
- protocol/credentials 使用已锁目录的 current read，防 MySQL RR 与 SQLAlchemy identity 弱引用回收后重新读到旧 header；只读 discovery 不增加写锁。
- 画布旧任务仅冻结 row_version 与当前相同才回写 cache；标准仍沿原 fingerprint 策略。

27 新单测红 **23 failed / 4 passed** → **27 passed**，相关五模块 **150 passed**。
native-a **12 failed / 14 passed / 23 deselected** 实际暴露 flush/旧 header 问题；修正后 native-b **26 passed / 23 deselected / 0 skipped，41.30s**，含真实旧目录升级、禁用 replay、确定性并发锁/current-read/新 header/版本验证。
日志 `.runtime/canvas-catalog-upgrade-lock-native-{a,b}.log`；未宣称被 deselect 的 23 条也在 b 中执行。

### 5.5 最终同版快速验证与检查

| 验证 | 实际结果与范围 | 本机日志 |
| --- | --- | --- |
| 后端 unit/API 最终 h | **1637 passed，1 warning，290.53s，exit 0**；已收回运行 session | `.runtime/canvas-text-final-quick-h.log` |
| 全仓 Ruff check | All checks passed | `.runtime/canvas-text-final-ruff-check-h.log` |
| 全仓 Ruff format | **571 files already formatted**；此后新增原 UI Python 文件另有静态检查 | `.runtime/canvas-text-final-ruff-format-h.log` |
| 画布 Node | **199 passed / 0 skipped** | `.runtime/canvas-text-node-final.log` |
| source ledger | **832 files / 91 adaptations**，固定 commit 未变 | `.runtime/canvas-text-source-final.log` |
| 统一生产 build | 既有最终稳定点通过；本轮没有生产 React/依赖/构建配置变更，未重跑 | `.runtime/canvas-beefapi-unified-build-final-stable.log` |

e 曾为 **1613 passed / 23 setup errors**，原因是系统 Temp 下 `pytest-of-coderedma` 的 WinError 5，业务断言未执行；切换工作区独立 basetemp 后 f **1636 passed**。f 后仍修改缓存与新增 overflow，最终同版结果以 h 为准。warning 来自既有 Starlette/AnyIO TestClient 弃用提示。
本次文字/缓存/图片工具切片没有新增 ORM、DDL、依赖或生产 React 组件。
本机日志/截图/报告在忽略的 `.runtime/`，不保证换机后存在；应保留本文件与维护文档中的实际结论，不提交测试桶、下载工具、缓存、构建产物或 runtime 证据目录。

## 6. 剩余工作明细

### 6.1 M0/M1：基准、生命周期与部署尾项

- 全部可达布局/控件/快捷键/操作序列/默认值、深浅主题、动效与触控成对对照；基础截图不足以覆盖完整功能。
- 外部 URL/本地键归档为可授权永久资源、播放代理、离线草稿重传；按源实际本地/托管分支处理，不把字段校验等同完整导入。
- 未保存草稿回收、绘图子文档 unknown 删除回执、完整并发删除/恢复、其余归档组合、全部历史/媒体物理清理。
- 嵌套共享字段、全部源版本十进制字符串、快照/回执不可变保护及完整子文档恢复专项审计。
- 伴随页面、宿主成员/设置入口、同源生产静态部署、跨窗口恢复和 100/1000/5000 节点的大图浏览器性能。
- 新代码的同版 API/Worker/scheduler 部署，见第 7、8 节；既有构建通过不等于生产部署验收。

### 6.2 M2：协议、引用、批次和运行行为

- Claude、Gemini 及尚未闭合的文字协议；Responses 图片、文字视频引用、源 composer 的 `@图片N`、绘图参考、文本份数及对应原 UI。
- Agent requests/审美分析/工具回复、本人提示词优化 relay；普通 canvas、独立创作页与助手各自可达能力须分开核对。
- 四类生成完整能力、结构化产物、批次调度/逐项重试、版本/历史/取消/unknown/恢复、完整源事件与错误日志对应。
- RunningHub 两套执行合同、其余模型协议兼容；真实企业授权、选定收费供应商逐类型验收。
- 已支持的供应商目录不代表所有协议可执行；不得根据模型名猜测视觉/thinking 能力。

下一片纯文字只读审计已完成，尚未实施，建议先 **Claude 普通纯文字任务**，再单独核 Gemini custom/hosted 差异：

| 源合同 | 下次实现须保留的事实 |
| --- | --- |
| Claude | `/v1/messages`，`x-api-key`、默认 `anthropic-version:2023-06-01`；不要套 Bearer。max_tokens 优先请求 extra、再保存 options、再 4096；普通纯文字不自动启用 thinking |
| Claude JSON/流 | 多 text part 按源 TrimSpace 后无分隔拼接；reasoning 独立。流需正确处理跨 UTF-8/chunk、CRLF、多行 data、start/delta/error 与 JSON fallback；源有效非空 text 在 EOF 可成功，不强加 message_stop 条件 |
| Gemini | 普通 manifest 为 `/v1beta/models/{escaped model}:generateContent`，鉴权 `x-goog-api-key`；没有普通 Gemini SSE 路径，不自动改成 streamGenerateContent |
| Gemini gate | custom 的 resolved APIFormat=gemini 普通任务被源 gate 拒绝；hosted 映射可能为 openai 而到达非流 manifest。宿主绑定 catalog 不自动等同源 hosted，不能笼统全禁或全部解锁 |
| URL | 保留 proxy path 和显式版本优先/去重，例如 Claude `/proxy/v1/messages`、Gemini `/proxy/v1beta/models/...:generateContent`，不能截成 origin 后简单拼接 |

建议新增画布专用 provider recipe、Claude stream decoder，并在目录/adapter/auth/Gateway 仅对画布任务与模型测试显式分发，标准与 Agent 原路径不随之改写。
先纯文字闭合请求/认证/解码及边界，再单独处理图片、视频、thinking、tools。上述仅静态源码/manifest 审计，未运行 Claude/Gemini 插件或真实供应商。

### 6.3 M3：工具与异常矩阵

- 标注正常路径、媒体持久化与刷新；grid 部分上传/commit 失败、409、导航/换账号、删除中途结果，以及其它图片工具的相应矩阵。
- 源 grid/annotation 的 `void` async callback 与无 catch 是已读事实；可能出现未处理 Promise 是待实测风险，未复现前不得写成已确认 bug 或擅改源闭包。
- 局部重绘、超分、多角度、光照、表情、肤质、角色参考、Skill/风格/提示词与所需模型服务的完整操作。
- 批量创作表、宫格任务、逐项恢复；Markdown/SVG/HTML/图表/对比、故事/短剧路径与源启用状态。
- 绘图参考、版本保留及与完整媒体/生成/导入导出的组合操作；不能由绘图存储通过推断全工具通过。

### 6.4 M4：媒体、时间线与导演

- 视频取帧/分析/裁切/拆分/变速/合并/音轨提取，音频裁剪、配乐、字幕、SRT、ASR、全景与调色。
- 源浏览器 FFmpeg 及独占 lease/进度/取消须保持；不要换成服务端 FFmpeg 后宣称操作等价。
- depth/lineart/pose 的 status/run、转换中间 IndexedDB Blob 的永久归档；未支持转换与源禁用入口保持原状态。
- 实际 `/timeline/transcriptions`、`/timeline/renders`、`/timeline/render-plan`、`/depth-captures` 及 depth-video stable client ID/unknown/session/观察流程，不能只返回 metadata。
- Go canonical timeline compiler 移植 Python：视频/图片单一视觉序列、跨视觉轨重叠错误、空隙 gap、混音、隐藏/静音、裁剪、淡入淡出、字幕和帧率；源可见 text clip 导出不支持的行为不得擅自扩展。
- 多轨时间线预览和真实输出、3D 导演资产/布景/机位/动作/深度/法线/拍摄。GLB 字节存取通过不代表 WebGL 导演验收。

### 6.5 M5：画布助手、工作流与最终验收

#### 共用 AI 创作助手专项

最新授权目标为“每个项目一组本人对话、纯问答与建议、两种模式共用界面和 Python 运行机制”。本助手专项实施与分层验证已收尾：标准页面顶部保存状态左侧按钮控制显隐，素材/镜头只作为编辑对象和消息来源，不建立阶段、素材或镜头会话。原编辑和生成设置保留，个人 Skill 仅指导回答，不获得执行能力。下表是助手切片证据，不代表完整 M5 或收费模型验收。

原 `pi/assistant` 兼容合同、直接改图、回合撤销、节点定位和付费生成提议属于**需求已替代**，不是已经实现的旧功能。原画布专属助手组件、运行 Hook、提议模块与 `/canvas-runtime/assistant/*` 前端请求链已移除，两端统一使用 `/api/v1/assistant`。不要再补原运行合同，也不要重复制作另一套助手；普通节点生成、手动编辑、撤销和画布工作流继续各自原范围。

| 切片 | 当前状态 | 实施与验证记录 |
| --- | --- | --- |
| 画布前端抽取及共用组件 | 已实施，最终构建及生产组合专项通过 | `frontend/src/features/ai-assistant/` 为唯一面板/消息/输入/CSS；画布只保留停靠、宽度调整、窄屏抽屉与上下文薄适配层。标题、消息和输入几何沿用画布助手；附件/Skill 弹窗使用共用主题变量 |
| 项目 V2 会话与纯对话运行 | 已实施并通过后端专项 | 每个本人项目可有多段对话，V2 不绑定分集、阶段、对象或任务；复用私人 Agent 表、Worker、排队、持久游标与 SSE，工具为空且预算为零；旧写入口拒绝 V2，项目成员不能读取另一人的历史、附件、Skill 或运行 |
| 数据库迁移 | 已在业务库执行并核验 | `short_drama` 已执行 `001-project-assistant.sql` 的单个 ALTER；`episode_id` 可空、V2 CHECK 与项目历史索引生效，旧对话 **27→27**，`inspect_agent_schema` 返回 `ready / gaps=[]`。复用原九表，无新增表，不重复执行 DDL |
| 标准分集入口与原编辑行为 | 已实施，分批及生产组合专项通过 | 去除模式切换与两种模式文案，顶部“AI 创作助手”按钮打开共用面板；小说/剧本、素材、分镜和成片原编辑与生成路径保留 |
| 画布助手接入与上下文屏障 | 已实施，完整专项及生产组合专项通过 | 发送先等待模型偏好与画布远端保存回执，来源版本为十进制字符串；保存失败、409、未知回执或等待中内容/权限变化均停止发送并保留草稿。明确 `@` 节点可在移除自动作品后继续引用，`include_document=false` 不读取整图 |
| 附件、素材引用与 Skill | 已实施，后端合同已验 | 上传/引用沿用本人项目检查、媒体真实信息和 Skill 版本冻结；来源截取明确提示；新助手只有回答能力。画布明确媒体节点由服务端冻结稳定 `MediaFile`，Worker 校验存储字节及 hash，不持久化签名 URL |
| 旧对话、任务及候选 | 已实施，兼容与导航专项通过 | 旧链接转历史只读入口，不向旧会话继续发新聊天、不合并范围；既有运行、计划、候选与明确采用保留原许可和版本契约。旧历史晚响应在关闭助手或切阶段后不能重开/拉回页面 |
| 草稿、未知发送与恢复 | 已实施，合同已验 | 草稿按账号和会话隔离；未知发送冻结原 body 与幂等键，刷新后使用原请求核对，后续编辑不会覆盖原待核对记录；断线通过游标恢复，不以新发送替代恢复 |
| 服务部署 | 已更新本机同版本服务 | API、scheduler、Agent Worker 已更新到本次实现；仅此三角色更新，不把其他历史重启记录当成本次八角色部署验收 |

关键入口：[公共助手](../../frontend/src/features/ai-assistant/CreativeAssistantPanel.tsx)、[画布薄适配](../../frontend/canvas/src/pages/canvas/creative-assistant-sidebar.tsx)、[画布保存屏障](../../frontend/canvas/src/pages/canvas/canvas-assistant-context.ts)、[助手 API](../api/assistant.md)、[旧库增量与实际执行记录](../数据库模型/migrations/2026-10-06-project-assistant/README.md)。

助手专项实际证据如下；分批结果按范围单列，不能累计交集或扩大为完整迁移验收：

| 验证批次 | 当前实际结果 | 范围与证据说明 |
| --- | --- | --- |
| 后端 unit/API | **1736 passed** | 本轮实际执行结果，未调用真实模型；不代表供应商验收 |
| 新旧 Agent 隔离 MySQL | **101 passed** | 随机隔离 MySQL，覆盖 V2/旧范围及隐私与写入口边界 |
| 新助手专项 | **25 passed** | 单独专项批次，与整组 MySQL/单元范围可能重叠，不相加为唯一总数 |
| 已落盘助手集成批次 | **15 passed** | `.runtime/project-assistant-integration-final.log`；前轮为 1 failed / 14 passed，不能把红批次写成通过或与 101 相加 |
| 追加隔离集成 | **1 passed** | 本轮补充边界已实际执行；不能据此扩大为所有真实浏览器验收 |
| 最新前端 Node | 宿主 **218 passed**、画布 **223 passed**，均无跳过 | 两包分别 `npm test`；`frontend/.runtime/project-assistant-node-final.log`、`frontend/canvas/.runtime/project-assistant-node-final.log`。214 为此前阶段计数，不再用作当前结果 |
| source/静态检查 | **832 文件 / 178 适配 / 91 删除**；Ruff check 与 format **588 文件**通过 | 最新 `npm run source:check`：`frontend/.runtime/project-assistant-source-final.log`，固定源 commit 未变，删除文件重新出现会失败；Python 检查覆盖 `src tests scripts`，不是运行/视觉验收 |
| 最终统一前端构建 | **通过，覆盖最后历史名称限制修复** | `frontend/.runtime/project-assistant-build-verified.log`：`npm run build` 完成两包 TypeScript 检查与生产构建，画布 built **44.69s**，产物组合为 `dist/`、`dist/canvas-app/` 并保留 `canvas/dist/`；构建成功不等于真实模型或完整源对照 |
| 宿主浏览器回归 | 53 项批次 **50 passed / 3 failed**；后续关闭恢复复测 **19 passed**、分镜布局专项 **1 passed**、助手导航 **10 passed** | `.runtime/project-assistant-e2e-green.log` 保留红批次；`.runtime/project-assistant-close-recovery-green.log` 覆盖素材保存冲突、显式关闭及刷新后焦点恢复，`.runtime/project-assistant-shot-layout-green.log` 为分镜专项；`.runtime/project-assistant-navigation-final.log` **28.2s**，含旧历史晚响应在关闭助手或切阶段后不能拉回页面的两个新增用例。路径均相对 `frontend/`，各批交集不相加，API 为测试夹具 |
| 画布浏览器回归 | 最新宿主/助手完整专项 **4 passed / 0 skipped，67.18s** | `frontend/canvas/.runtime/project-assistant-host-canvas-final.log` 覆盖拖拽保存/撤权、保存导航/409、助手首次加载中关闭再开、保存后来源引用、草稿、1440/390 及旧只读。加载重开先在 `project-assistant-reopen-red.log` 复现失败，再修复通过；较早助手单项 **1 passed** 与本批重叠不相加。私人偏好409为开发store状态注入＋API夹具，不称产品模型选择UI或供应商验收 |
| 生产组合产物专项 | 标准 **14 passed，27.9s**；画布 **2 passed，8.63s** | 组合 `dist/`，preview `127.0.0.1:4175` 与 API 夹具；标准运行 `creative-assistant.spec.ts`、`agent-workspace.spec.ts`，画布按“助手首次加载/共用创作助手”名称筛选，覆盖新增入口与来源/恢复。日志为 `frontend/.runtime/project-assistant-production-standard.log`、`frontend/canvas/.runtime/project-assistant-production-canvas.log`。此批之后历史名称长度单点修复，14/2没有再整批重跑，不将夹具写为真实供应商验收 |
| 最后历史名称修复生产专项 | **1 passed，4.1s** | `frontend/.runtime/project-assistant-production-history.log`：实际逐键输入121字只保留120，与服务端上限一致；继续验证重命名、归档后立即只读与新建对话。使用最后重新构建的组合产物与API夹具，单列本项，不冒称其他生产批次重新执行 |
| 桌面/窄屏视觉核验 | **标准/画布1440与390四图已实际查看** | 正文、消息、引用标签及底部输入/发送可见，未见抽屉横向越界、浅底亮字或遮挡；标准安全Markdown夹具中的脚本作为普通文本显示且断言未执行。四图备份于 `.runtime/project-assistant-production-screenshots/{shared-assistant,shared-canvas-assistant}-{1440,390}.png`。来源为14/2生产批次，最后120字微修不影响布局；这不是固定源全页面像素门禁 |

1736/101/25/追加 1 的结果来自前轮已收回的实际工具输出，未找到对应同名落盘日志，不伪造日志名；现存 15 项独立集成日志按上表单列。DDL 证据为 `.runtime/project-assistant-ddl-result.json`；三角色更新沿 `.runtime/restart-project-assistant.ps1`，当前进程和输出记录位于 `backend/.runtime/{api,scheduler,agent}.{pid,out.log,err.log}`。这些本机运行文件不提交。

标准回归暴露并修复了四类问题：普通素材/分镜编辑深链不能随旧 Agent 范围一起删掉，现使用 `asset/shot` 保留刷新与返回恢复；来源查询变化不能重新注册关闭回调而意外收起助手；素材显式关闭后必须抑制同一深链立即重开，并在卡片重载后恢复焦点；宽屏分镜设置侧栏的按钮遮挡“下一镜头”，已在 `storyboard-layout.css` 对选择标题增加64px右侧空间，修复真实产品布局。此前按实际 `CreationSlot` 调整测试选择器属于排查过程，不能将此红项说成只有测试问题，没有降低编辑/布局断言。画布 commit 观察先于夹具等待队列入列的竞态也已通过明确开始信号修正，不使用固定 sleep 掩盖。

最终导航审计还修复了旧历史详情晚响应：助手已关闭或阶段已切换时，旧请求不能重新打开面板或重定向。画布则补齐首次加载未完成就关闭再开的恢复路径，加载取消不能让下一次打开一直停在等待态；先红后绿的完整四项浏览器批次覆盖此边界。

最后只读审计发现对话重命名前端200字与后端120字不一致，已将公共历史输入改为120字并补实际键盘边界测试。最终两包构建和生产历史单项通过后，只关闭本次创建的4175 preview进程；未停止用户现有8081/8082开发服务。助手专项代码尚未提交或推送。

后续先核对此表、当前代码与最终证据，再补真实运行和产品反馈；不要重新抽取 UI 或实现另一套助手。此前完整迁移 goal 为 `paused`，本轮用户继续的是助手专项；M5 的工作流、插件和完整对照仍未完成。没有真实模型、正式企业授权或收费供应商验收，不能宣称完整一比一迁移。

#### 其他 M5 待办

- 基线启用的外部工作流/插件、两个 RunningHub 合同和其余既有 ZIP/JSON 导入导出组合；源不可达管理入口不擅自恢复。
- 所有源启用项的正常及关键异常/恢复操作、完整截图/动效/媒体与标准模式回归、共享/私人边界验收。

实时协同、历史数据批量搬运、新插件生态和后续原生媒体优化不属于当前 M0–M5 必交范围；源已有启用功能不能因此省略。

## 7. 未关闭问题、环境与部署状态

### 7.1 严格视觉门禁

正式结果：**17 场景几何相同，16 场景 0px；`drawing-editor-empty` 阴影 4px 尚未关闭**，灰阶差异 17/18。固定源自身重复运行也观察到同点波动，不能直接认定是目标 CSS 错误。
另有独立内部 canvas 尺寸问题：弹窗缩放动画中首次测量可能保留错误尺寸；`afterOpenChange(true)` 后挂载是候选方向，尚未实施/验证，并不能解释原阴影 4px。
不能改 mask/阈值、源阴影/依赖或反复运行碰到一次 0px 就宣布解决；依赖对齐前的旧证据不自动覆盖当前版本。
源减少动画规则影响 AntD 菜单定位的问题两边已复现，正常动效分类通过不代表减少动画场景通过。

### 7.2 环境与服务

- 当前开发环境为 Windows PowerShell；Node 实际 22.22.1。推荐版本与入口以仓库 AGENTS/子包 README 为准。
- 本机 `uv` 不可用，Python 验证实际使用 `backend/.venv/Scripts/python.exe`；后端工作目录必须为 `backend/`。
- 本机浏览器为 `C:\Program Files\Google\Chrome\Application\chrome.exe`，经 `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` 指定；下次使用前重新确认存在和版本。
- 历史八业务角色重启覆盖视频稳定版；本次助手专项已更新同版本 API、scheduler 和 Agent Worker。该三角色更新不替代其他媒体/生成切片的完整部署核验，下次继续仍按当前进程和对应代码重新核对，不复用旧 PID。
- 业务库 `short_drama` 的助手专项 DDL 已实际执行并记录：`agent_conversations.episode_id` 可空、更换范围 CHECK、新增项目历史索引，旧会话 27→27、readiness ready。没有新增表；其他环境须按增量说明核验后补缺，不自动建表、不重复执行已完成的 ALTER。
- 最后 runner 核实测试桶、native 临时目录、4194/4199 listener 和相关测试进程均为 0；原 UI 文字图片测试未启动。最终 h 快测也已结束。下次必须重新盘点实际进程，不能直接复用旧 PID。
- 源/宿主原有服务可能占用 3000/8080；不得随意结束已有用户进程。诊断目标 4184 已关闭；4199 为本片原 UI 专项端口。
- `.runtime/run_native_canvas_mysql_tests.py` 为现有任务自有临时 MySQL launcher，结束 SHUTDOWN/清理；`.runtime/canvas-restart-services.ps1` 为既有八角色重启脚本。两者均是本机忽略文件，下次若不存在，按维护文档重新核对流程，不假设它们属于提交源码。
- 上次执行 shell 需环境的 `require_escalated` 才可读取工作区；这是当时 ACL 状态，下次按实际工具权限处理，不增加业务批准步骤。

### 7.3 验证边界

受控 HTTP 供应商＋真实 Gateway/MySQL/MinIO/浏览器是本片实际联调范围，不能描述为收费供应商、正式企业授权或整片 AMQP 验收。
前阶段 broker 实测有独立日志；不将直接执行器片的结果拼成全部经过消息队列。API 夹具、source hash、构建、collection、跳过和 deselect 均不能替代真实运行。
读取配置通过 Settings，不在会话或文档输出 `.env`、实际密钥、数据库密码或签名 URL；保留已有配置与加密主密钥。

## 8. 下次继续的具体顺序

### 第一步：恢复上下文并核对工作区

1. 读取本文件、仓库 AGENTS、实施记录最新章节、下一切片涉及的 API/代码/测试。
2. 检查 `git status --short`，保留当前大量未提交改动；核对固定源 commit、独立子包和统一脚本，不重导。
3. 核对 goal 状态；本快照为 paused，只有用户明确继续后才开始迁移。不要因文档读取自行启动服务或测试。
4. 核对测试端口、随机数据库/桶、已有 Worker 与供应商 fixture 所有权。并行代理可以静态审计独立范围，native/browser/数据库/服务生命周期由一个 runner 统一串行管理。

### 第二步：读取已收尾助手证据，再闭合原 UI 文字图片验收

先读取第 6.5 节已收尾的助手证据与真实模型未验范围；不要重做公共面板、重建项目助手表或恢复旧工具链。下一迁移切片执行已准备的 `test_canvas_text_images_browser.py`，不要重新编写已经存在的后端14条联调。新增产品反馈或真实模型问题按实际范围另行验证，不因历史待收集文字重复实现本助手。
两图必须经原上传 input，原输出连接点创建空文字节点，第二图原 rail 拖入边，原生成按钮先保存后提交；不能通过 API 种生成结果或拦截成功生成请求代替。
首段真实 DB delta 已存在时原节点仍 loading/空正文，页面 `text-events` 请求数为 0；释放供应商终态后原消费者绑定同一节点。
继续核对 fresh 仅 cookies context、原图片/system 不变、MySQL nodes/edges/results/references、MinIO 原字节和本人/成员/第三方权限。

已核对的本机启动入口如下，**这是恢复后的执行说明，本次没有执行这些命令**。先确认 launcher、mysqld、MinIO 和浏览器可用；数据库由 launcher 自建，不能把应用库作为 TEST_DATABASE_URL。

```powershell
# 工作目录：D:\code\ai-short-drama\backend
$env:RUN_CANVAS_RESOURCE_MINIO = '1'
$env:RUN_CANVAS_BROWSER_INTEGRATION = '1'
$env:PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
.\.venv\Scripts\python.exe ..\.runtime\run_native_canvas_mysql_tests.py tests/integration/test_canvas_text_images_browser.py -q
```

为每轮保存新的日志/报告，不覆盖旧红证据；核实 `1 passed / 0 skipped`、最终所有 Python 断言执行、无 page errors，并核实 native/桶/测试进程退出与清理。若仅跳过或 collection，不标完成。

### 第三步：更新证据并部署本片同版服务

- 原 UI 通过后更新实施记录/本交接状态，按实际新增或修改范围做 Ruff/测试/source 检查。纯文档变化不重复运行产品测试。
- 部署前检查业务 AsyncTask/AgentRun/render/batch 活动为 0；核对八角色 PID 对应本仓库路径、命令、子进程与创建时间，备份旧日志。
- 可读取并复用现有 `.runtime/canvas-restart-services.ps1` 的检查流程；缺失或进程形态变化时重新核对，不能盲跑旧脚本/杀旧 PID。
- 重启同版八角色后验证 API health/OpenAPI、各角色运行与对应 RabbitMQ 消费者；默认 remote control 关闭，空 Celery ping 不能据此判断失败。
- 部署不修改 `.env`、队列 namespace 或加密主密钥；有活动任务先等其完成，不能为部署丢掉执行。

### 第四步及后续：按切片完成剩余功能

建议顺序：M3 标注正常＋grid 异常/409 → M2 Claude 纯文字及其原 UI → Gemini 源 gate 与非流协议 → 其余引用/工具/批次 → M4 媒体与时间线/导演 → M5 剩余工作流/插件及助手未验范围 → 最终全量对照。
视觉未闭合问题可以单独静态分析和窄场景验证；不要重复跑完整 17 场景碰绿。
每片先阅读调用链/源合同，超过三文件或跨层改动先说明文件计划；不顺手重构。源异常缺陷是否修复须与一比一目标区分，不能静默改变行为。

## 9. 后续验证与更新原则

- 后端改动先相关测试，再 unit/API、Ruff；使用工作区独立 basetemp，避免重用已经出现 WinError 5 的系统 Temp 根。
- MySQL 集成必须随机临时库，媒体必须任务专用桶；启用项实际运行并清理才记验收，不把应用库当测试库。
- 前端改动先相关 Node/浏览器测试，再需要的统一 build；build 已含两包类型检查。修改源文件须显式记录 adaptation，不能 importer 覆盖。
- 没有新变化或失败时不重复已通过的整组验证。收费供应商、正式企业登录、AMQP、浏览器媒体和视觉分别记录，不混合口径。
- 完成每个切片后同步“功能状态、实际命令、结果、失败证据、未验范围、部署版本、清理状态”，并更新接续入口。

最终只有所有源基线启用项实际可用、全部布局/交互/操作/功能对照通过、未解决差异关闭、源禁用状态正确、标准模式和权限回归通过，才可声明 M0–M5 完整完成并结束 goal。

## 10. 下次可以直接使用的指令

> 读取 `docs/plans/2026-10-06-beeftv-resume-handoff.md`、仓库 AGENTS 和实施记录最新章节，在现有未提交改动与 `frontend/canvas/` 独立子包基础上继续 BeefTV 一比一迁移。共用 AI 创作助手专项实施与分层验证已收尾，读取第6.5节证据与真实模型未验范围，不重复制作画布助手、不恢复旧工具链或重复DDL；接着完成已准备的原UI文字图片验收、更新证据并按范围部署同版服务，再推进剩余切片。不要重导源，保留标准已有编辑生成功能，不把受控供应商、跳过或静态检查写成完整验收。
