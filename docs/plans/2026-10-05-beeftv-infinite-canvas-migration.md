# BeefTV 无限画布一比一迁移方案

编制日期：2026-10-05。本文是基于本地源码的实施设计，不代表功能已经迁移、数据库已经升级或真实模型已经验收。

实施进展以[实施记录](2026-10-05-beeftv-implementation-log.md)为准。2026-10-06 已追加项目文件夹服务及本人归属设计：复用原版 UI，通过 Python 两张独立表实现文件夹与画布归属，当前数据库为 86 表。原界面删除文件夹先回收其中项目，底层文件夹 API 只清除归属；二者不能合并成不同的新流程。这是一个已落地模块，不代表 M0–M5 的完整一比一验收完成。

同日补齐回收站显式恢复：依据本人删除回执恢复原画布和必要的项目归属，保留原媒体/绘图/历史，未知恢复回执同键重试。随后接通已保存画布的未知删除回执恢复：删除前持久化本人请求，刷新只读核对并找回回收条目，旧删除被后续恢复取代时保留当前作品。服务端回收目录、受限媒体预览与不可恢复处置已通过真实 MySQL/MinIO 和浏览器专项，复用既有回执，无新增表；具体证据以实施记录为准。物理清理全部历史/媒体字节和未保存子文档的完整恢复仍未完成；源“已删除文件夹中的恢复画布被失效归属隐藏”已运行复现，缺陷修正仍待用户选择，不混入一致性声明。

修订依据：用户进一步明确“布局、交互、操作、功能都要一样”。原方案属于功能迁移与目标产品适配，不能保证一比一；本版改为以本地 BeefTV 的固定版本作为画布行为基准，保留其 React 页面、组件、视觉依赖、交互算法和操作流程，仅在服务边界适配 Python / MySQL / MinIO。不得以“能力相似”代替实际对照验收。

推荐实施方式：现有项目创建弹窗增加“标准模式 / 无限画布模式”。标准模式保留现有功能、主题和规则；无限画布使用 `frontend/canvas/` 独立子包、HTML、React root 和样式，隔离宿主页面布局和全局 CSS。按用户 2026-10-06 的后续调整，两包共同声明但版本不同的直接依赖采用宿主依赖版本，源独有依赖继续单独锁定，并处理其兼容依赖；不为画布升级标准前端。Python 重建源接口的行为合同，并复用当前账号、权限、模型配置、存储和生成调度。依赖对齐后必须重新验证源布局与行为，不能用此前不同依赖版本的截图结果证明本次一致。只有完成全部视觉、操作、功能与运行验收后，才可宣称该基线的画布一比一迁移完成；方案本身不能证明已经实现一致。

## 1. 已确认的范围与源码基线

用户已经确认：

- 无限画布的布局、交互、操作和功能以 BeefTV 为准，一比一复刻；不是重新设计一套相似画布。
- 完整创作画布是最终目标，分阶段交付。
- 一个项目允许多个画布，首版创建一个主画布。
- 首版支持成员共享作品与保存冲突保护；多人实时协同后续加入。
- 预留 BeefTV 历史数据导入工具，首版先迁移功能。
- 画布放在 `frontend/canvas/` 作为独立子包，保留独立依赖、锁文件和 HTML；在 `frontend/` 统一安装、启动、构建与预览，共同声明但版本不同的直接依赖采用宿主版本，其他功能保持原目标。

本次阅读的源仓库为 D:/code/BeefTV，Git HEAD 为 4ca2a65a7780，工作区无未提交改动。目标仓库为 D:/code/ai-short-drama，Git HEAD 为 4e24eefdce3e，存在大量未提交改动；分析依据包括这些工作区内容，实施时必须重新确认基线并保留它们。

“完整创作画布”包含编辑工作区的顶栏、侧栏、底部工具栏、节点、菜单、编辑面板、浮层、生成与引用、素材组织、绘图、媒体加工、批量创作、版本历史、画布时间线、导演台和画布助手，以及从这些入口实际可达的功能。账号、支付、积分、桌面安装器及画布以外的 BeefTV 整站页面不属于复刻范围；使用当前项目对应基础设施。画布中实际启用的内置插件、外部工作流及导入导出入口必须纳入最终功能清单，不能只复制按钮后将执行部分排除。

### 1.1 一比一的基准与边界

基准固定为 BeefTV commit 4ca2a65a7780，同时记录源锁文件摘要、实际安装版本、构建开关、浏览器与操作系统、字体、视口、DPR、账号偏好和测试数据。M0 必须实际运行源画布并采集基准；本次静态阅读没有完成这项运行验证。

默认对照源正常画布入口，不将 libtvChrome、DEV 实验室或特定 fixture 的特殊布局当作默认界面。完整媒体构建与精简构建的开关需分别记录，完整目标采用源完整媒体配置；simple / professional 工具模式、深浅主题、背景模式及已有布局偏好分别建场景。源仓库后续更新不自动成为本轮验收基线。

| 维度 | 必须保持的内容 | 判定依据 |
| --- | --- | --- |
| 布局与视觉 | 工作区结构、控件位置与尺寸、间距、字体、图标、颜色、圆角、阴影、层级、遮挡、弹窗及动效 | 同环境、同数据、同状态的源 / 目标截图与 DOM 几何对照 |
| 交互 | 点击、双击、悬停、指针捕获、拖拽、滚轮、触控、框选、吸附、输入焦点、快捷键、浮层事件边界 | 相同操作轨迹回放，比较选区、位置、视口和焦点等状态 |
| 操作流程 | 入口、步骤、默认值、菜单顺序、确认与取消、生成结果落位、版本选择、重试、保存和恢复 | 逐步状态与操作次数一致，不新增强制采用或审批步骤 |
| 功能 | 该基线所有实际启用的节点、工具、媒体加工、时间线、导演、助手与工作流能力 | 功能清单逐项通过，不以占位、隐藏入口或测试替身替代真实执行 |
| 当前不可用状态 | 源已禁用、隐藏或标记“正在开发”的入口 | 保留源名称、排序、徽标和可用性；另行开发不计入本轮复刻 |

项目模式选择、当前账号认证、成员共享和服务器冲突保护属于接入当前项目的边界。源是本地工作区，成员权限与服务器故障不存在可直接逐项比对的同等场景；这些扩展必须单独验收并明确记录。存储实现、数据库记录 ID 和请求信封允许变化，但不得无声改变画布正常创作操作。

### 1.2 当前规范的适用范围

用户最新的一比一要求优先于原方案中的主题适配和流程改造。无限画布不套用标准工作台的主题与面板布局，也不把 BeefTV 的直接结果回填改成“候选架 → 再采用”。源助手的图编辑及付费生成提议分别保留其原有执行与确认方式。

这意味着当前 AGENTS.md 中“AI 输出先保存为候选，由用户明确采用”的规则，在无限画布的源行为兼容范围内需要明确例外；标准模式仍遵守现有规则。实施时同步记录该模式例外及对应接口、权限与产品说明，不能一边要求严格复刻，一边在实现中重新插入采用步骤。本次仅修订方案，没有修改 AGENTS.md 或业务规则。

任务、模型凭据、供应商诊断、生成历史和助手对话仍按本人隔离。源自动写入的结果作为画布作品按项目权限共享；作者看到的任务参数通过私人投影补齐，不能将整个源 metadata 原样公开。共享扩展不能成为泄露密钥或其他成员私人创作过程的理由。

## 2. 源码核对后的关键结论

| 核对项 | 实际实现 | 迁移含义 |
| --- | --- | --- |
| 前端技术 | BeefTV 和目标项目都使用 React 19；源项目另有 Zustand、Leafer、Tailwind、Excalidraw、Three 等 | 迁入完整画布依赖闭包；保留源视觉系统和操作实现，只适配业务服务 |
| 依赖版本 | 源项目 Ant Design 6、Router 8、Vite 8、TypeScript 7；目标是 Ant Design 5、Router 7、Vite 7、TypeScript 5.9 | 按用户后续调整，共同声明但版本不同的直接依赖采用宿主版本，并完成兼容适配；画布独有的 `react-router` 8 等继续独立锁定。不升级标准前端，重新对照依赖变更后的视觉与行为 |
| 画布渲染 | React DOM 节点、SVG 连线与 Leafer 交互覆盖层；平移缩放采用 ref、直接预览和 requestAnimationFrame | 保留现有引擎分层，避免第一阶段重写交互内核 |
| 页面规模 | web/src/pages/canvas/project.tsx 为大型编排页，调用许多页面 Hook、Store 和媒体服务 | 按依赖链迁入模块，不能只复制 InfiniteCanvas 组件 |
| 数据来源 | workspace-mode.ts 的 isLocalWorkspaceMode() 当前直接返回 true；Go、SQLite、本地资源及浏览器缓存构成本地工作区 | 目标改为 Python / MySQL / MinIO 权威存储；IndexedDB 只承担草稿与恢复 |
| 项目组织 | CanvasProject.workspaceProjectId 与辅助函数已经支持项目到多个画布 | 复用组织语义；项目、画布、分集分别使用独立稳定标识 |
| 保存保护 | 源项目具有 revision、提交日记、冲突草稿、三方差异处理、显式强制覆盖入口 | 迁移串行保存、日记与草稿保护；409 后等待用户处理，提供基于最新版本的显式合并 |
| 生成回填 | 源项目会将生成媒体与任务信息写入节点 metadata，并自动物化结果节点 | 保留源新增、替换、版本选中与结果落位语义；共享作品与本人任务字段在存储层拆分、读取时投影 |
| 开发中入口 | MediaConversion、Frame、Script 的创建被功能可用性函数和测试标记为“正在开发”；添加菜单也禁用这些入口 | 一比一迁移保留源禁用状态；不擅自启用或把尚未发布的节点算成可用功能 |
| 节点与入口 | Frame 的注册定义称为背板，添加菜单中对应入口称为“逐帧拉片”；文件夹又有单独命令 | 保留实际文案、图标、顺序和各入口行为；不在迁移中重新命名或合并 |
| 画布助手 | 当前源码已接 /api/assistant/* 与 agent-host 中钉选的 pi SDK；专题文档仍有旧 Agent 退场描述 | 以实际源码为准；迁移工具与交互能力，接入当前 Python Agent，单独进行运行验收 |
| 版本历史 | 源项目普通历史快照最短间隔 5 分钟、保留最近 20 份，媒体由引用保护 | 版本号与历史快照编号不同；迁入恢复与删除保护，并补齐绘图、导演和时间线的冻结策略 |
| 目标 Agent | AgentConversation 和 AgentArtifact 的 episode_id 为必填，范围 CHECK、Service 和运行工具也依赖分集 | 需要显式增加画布作用域；不能创建伪分集来接入画布助手 |
| 目标成片 | EpisodeAssembly / Clip / RenderJob 绑定分集与镜头；当前编辑流程主要是单视频轨，声音单独编排 | 画布保留源多轨模型、毫秒精度与浏览器加工流程；Python 管理稳定资源、必要服务及持久化 |
| 目标生成 | async_tasks 已有幂等、发布、恢复与租约；记录有 prepared / sent / unknown 等执行状态 | 画布任务接入现有生成服务；不在前端再建供应商调用或重试状态机 |
| 目标媒体 | media_files 对格式的 CHECK 当前覆盖 image / video / audio | 导演台的 GLB / glTF 等二进制资源需要独立受控模型与存储接口 |

源码中的测试只能证明已经存在测试用例。本次没有执行源项目测试，也没有据此宣称大画布、助手或真实模型已经运行通过。

## 3. 创建项目与两种模式的行为

### 3.1 创建弹窗

沿用 [ProjectsPage.tsx](../../frontend/src/pages/projects/ProjectsPage.tsx) 中的现有弹窗、名称和默认画幅，在名称上方增加一个有标签的单选组：

| 选项 | 说明 | 创建成功后的入口 |
| --- | --- | --- |
| 标准模式，默认选中 | 按小说、素材、分镜、成片四步创作 | 现有项目详情，用户自行添加第一集 |
| 无限画布模式 | 在自由画布中组织节点、连接素材并创作 | 新项目的主画布 |

两种模式都使用项目名称与默认画幅。无限画布的工具默认值以源为准，项目画幅仅在源已有的项目上下文 / 默认选项位置映射，不能一律覆盖源生成尺寸、比例或导出选项；它不限定画布世界尺寸。用户切换单选项时保留已填写信息；提交中提供 loading、禁用重复提交和关闭，就近显示错误。

创建弹窗采用现有冷黑、紧凑的中文工作台主题与 Dialog。单选组可使用方向键切换，标签解释模式差异。进入无限画布后切换为 BeefTV 的完整画布界面；创建弹窗的主题不传递到画布内部。

### 3.2 项目模式字段

增加项目字段：

~~~text
workspace_mode: "standard" | "infinite_canvas"
~~~

数据库默认值为 standard。ProjectCreateRequest、内部 ProjectCreate、ProjectRead、ProjectSummary、前端 ProjectDto、RemoteProject 和项目列表映射同步修改。旧创建请求省略该字段时仍创建标准项目。

模式与分集已有的提示词 / Agent 创作选择是两个维度；不复用现有 URL 的 mode 查询参数。

源画布另有 simple / professional 工具展示模式，必须保留其入口、默认值、工具过滤和切换效果，映射为本人 canvas_tool_profile 偏好。它不能与项目 workspace_mode 混用，也不能据此绕过后端能力与权限检查。

首版将项目模式设为创建后固定。项目设置显示模式名称；普通 PATCH 不接受修改模式。后续如需要转换，做独立的预览、复制与映射操作，保留原项目和来源，不原地改字段后假定数据已经转换。

### 3.3 创建事务与失败恢复

标准分支保持现有创建行为和声音模式初始化。无限画布分支在一个 Service 事务中完成：

1. 由可信 Actor 创建项目。
2. 创建空主画布，初始化 schema_version、row_version 和画布设置。
3. 创建项目主画布设置记录。
4. 写入本人可见的创建幂等回执。
5. 事务提交后返回项目与 primary_canvas_id。

无限画布创建请求使用 Idempotency-Key。首次提交前保存键与请求摘要；结果不确定时复用同一键查回执或重发原请求，明确修改内容后才换键。旧标准创建接口继续兼容不带该请求头的调用。

如果主画布或回执插入失败，整个新项目事务回滚。并发使用同一键时由唯一索引保证最多一个结果，失败事务不能留下孤立项目。服务器不因浏览器刷新重复创建主画布。

### 3.4 路由与项目列表

建议保留项目根入口，增加：

~~~text
/projects/:projectId
/projects/:projectId/canvases/:canvasId
~~~

根入口读取服务端项目模式：标准项目仍渲染原详情；无限画布项目进入服务端记录的主画布。画布路由验证项目模式、画布归属和成员权限，不依据 URL 参数直接开放编辑。

项目卡片增加模式标签。标准项目继续显示分集数，无限画布项目显示画布数。列表接口批量聚合数量与主画布信息，避免每个项目分别查询画布。画布删除保留源菜单和反馈，主画布被删时服务端在同一事务按源列表顺序选择剩余画布作为主画布，不额外要求“先指定主画布”。删除当前画布按源跳到下一画布；最后一个被删除时归档该无限画布项目并返回项目列表，保留源回收语义、历史和任务出处。归档状态允许主画布关系为空，恢复时重新建立有效关系。

App.tsx 的详情匹配、预加载和路径函数都要识别画布入口。无限画布使用独立 HTML / React 构建入口，以同源路径提供；入口可以跳转到 /canvas-app/projects/:projectId/canvases/:canvasId。开发代理和生产路由必须支持直达、刷新、资源加载和返回主工作台。不同画布之间沿用源 SPA 切换，不能每次切换都重载编辑器。

画布文档内保留源工作区外壳与布局，不叠加当前项目的顶栏、侧栏、内容最大宽度或滚动容器，也不使用 iframe 承载整个编辑器，以免改变快捷键、焦点、剪贴板、指针捕获和全屏行为。返回项目列表、账号与权限异常映射到源既有入口；画布内可见的结构不得借接入之名重新排列。

## 4. 总体架构

~~~mermaid
flowchart TD
    Create[创建项目：标准 / 无限画布] --> Project[共享的项目、账号与权限]
    Project --> Standard[现有分集工作区]
    Project --> Canvas[React 画布工作区]
    Canvas --> Engine[DOM 节点 / SVG 连线 / Leafer 覆盖层]
    Canvas --> LocalMedia[源浏览器 FFmpeg / WebGL / 绘图与媒体预览]
    Canvas --> Adapter[源服务合同兼容层与统一 HTTP 客户端]
    Adapter --> API[FastAPI 路由与 Pydantic 契约]
    API --> Service[画布、结果物化、上下文、媒体与时间线 Service]
    Service --> DAO[DAO 与 SQLAlchemy ORM]
    DAO --> DB[(MySQL)]
    Service --> Storage[(MinIO)]
    Service --> Generation[现有生成准入与发布恢复]
    Generation --> Workers[Celery 文本 / 图片 / 视频 / 音频 Worker]
    Service --> MediaJobs[确需服务器的媒体与 ASR 任务]
    Service --> Agent[现有 Python Agent 与画布工具]
    Workers --> Results[本人任务与不可变结果记录]
    MediaJobs --> Results
    LocalMedia --> Results
    Results --> Apply[按源行为物化：授权、幂等与版本校验]
    Agent --> Apply
    Apply --> Shared[共享作品节点、媒体引用与版本历史]
~~~

边界约定：

- React 负责交互、编辑投影、浏览器草稿、媒体预览和任务展示。
- Python 是操作授权、结构验证、参考解析、任务准入、结果持久化和历史恢复的裁决方；正常结果回填不新增人工采用步骤。
- DAO 不自行提交事务；网络、MinIO、模型调用和 FFmpeg 不在长数据库事务内执行。
- MySQL 保存业务结构、稳定引用与任务状态；MinIO 保存文件字节；临时签名 URL 只进入展示层。
- 标准模式和画布模式可以明确导入或复制已保存作品，默认不双向同步；标准模式的候选采用规则保持原样。

## 5. 功能迁移矩阵

表中“迁入”指保留源界面、交互与功能，服务依赖仍需适配；“适配”只能改变内部实现，不得改变正常操作流程。“源禁用”不代表目标遗漏，必须以同样的禁用状态复刻；后续启用另立需求。

| 功能组 | 源实现位置，均相对 BeefTV | 迁移处理 | 阶段 |
| --- | --- | --- | --- |
| 平移、滚轮缩放、触控双指、抓手、框选 | components/canvas/infinite-canvas.tsx；lib/canvas/canvas-selection.ts | 迁入 Pointer Capture、坐标换算和浮层事件边界 | M1 |
| 拖拽、缩放、对齐、等距、层级、复制粘贴 | pages/canvas/use-canvas-selection-controller.ts；use-canvas-node-operations.ts；lib/canvas/canvas-layout.ts | 迁入纯算法与预览；结束动作提交一个操作批次 | M1 |
| 搜索、缩略导航、适应视图、快捷键 | components/canvas/canvas-mini-map.tsx；lib/canvas/canvas-shortcuts.ts；canvas-node-search.ts | 保留源入口、结果列表、定位、帮助文案与默认快捷键，不新增替代操作面板 | M1 |
| 基础文字、图片、视频、音频节点 | types/canvas.ts；lib/canvas/node-registry/ | 原样迁入节点定义与渲染；存储拆分 metadata 后，作者视图还原源对象 | M1–M2 |
| 文件上传、拖入、素材库与生成历史选取 | use-canvas-upload.ts；canvas-media-persist.ts；canvas-generation-history.ts | 接目标上传、个人资源导入、媒体库及本人生成历史；引用前确认持久化 | M1–M2 |
| 连线、端口、参考素材、角色卡、智能引用 | canvas-connection-policy.ts；canvas-resource-references.ts；canvas-text-mention.ts | 迁入交互与引用算法；服务端重做类型、数量、权限和稳定 ID 校验 | M2 |
| 文本、图片、视频、音频生成 | canvas-*-generation-executor.ts；services/api/generation-task.ts | 接 AIGenerationService、现有异步任务与本人模型配置 | M2 |
| 生成配置节点、Skill、风格与提示词编辑 | node-registry；canvas-style-system.ts；canvas-config-composer.tsx | 迁入；模型 ID、生成参数、私人 Skill 内容放本人设置，风格作品按明确保存分享 | M2–M3 |
| 批量创作表、宫格任务、批量重试 | canvas-batch-table.ts；canvas-generation-batch.ts | 保留表格、参数、确认、执行、结果落位与逐项重试流程；服务端适配批次调度 | M3 |
| Script 分镜脚本节点、故事输入、短剧工作流 | canvas-short-drama.ts；canvas-storyboard-operations.ts；canvas-storyboard-materializer.ts | 保留现有数据的渲染与已实现路径；创建菜单保持源禁用状态，不新增逐项采用流程 | M3 |
| 背板、文件夹、折叠、分组 | canvas-frame.ts；canvas-folder-storage.ts；canvas-frame-node.tsx | 保留各自容器、文案、样式、折叠和关联行为；“逐帧拉片”入口按源禁用 | M1–M3 |
| 绘图与白板、绘图参考图 | canvas-drawing-excalidraw-editor.tsx；canvas-drawing-storage.ts | 按需加载 Excalidraw；结构与渲染图分别持久化，引用冻结版本 | M3 |
| 裁切、标注、宫格切分、局部重绘、超分 | canvas-node-crop-dialog.tsx；canvas-node-mask-edit-dialog.tsx；canvas-grid-split.ts | 保留源编辑器、预览与确定性加工算法；Python 适配媒体持久化和模型调用 | M3 |
| 多角度、光照、表情、肤质、角色参考 | canvas-angle-scene.tsx；canvas-emotion.ts；canvas-portrait-texture.ts | 保留预览；面部识别等 Worker 懒加载，付费编辑通过 Python 协议适配 | M3–M4 |
| 视频取帧、分析、裁剪、拆分、变速、合并、音轨提取 | canvas-video-frame.ts；canvas-video-segment.ts；canvas-video-merge.ts | 迁入实际启用的工具、参数、浏览器 FFmpeg 与进度 / 取消流程；源禁用创建入口仍禁用 | M3–M4 |
| 音频播放、裁剪、字幕、SRT、配乐 | canvas-audio-player.tsx；canvas-subtitle-dialog.tsx；types/timeline.ts | 迁入编辑与预览；转写需单独受控的 ASR 服务，不默认为已有能力 | M4 |
| Markdown、SVG、HTML、图表、对比 | node-registry/definitions/builtin-nodes.tsx | 保留源渲染器、编辑动作和隔离预览能力，按类型校验内容，不擅自改为只读 | M3 |
| 全景、调色、媒体转换 | canvas-panorama-config-modal.tsx；canvas-color-grade.ts；media-conversion/ | 全景和调色迁入；保留转换类型的兼容读取及“智能剪辑”菜单禁用状态 | M4 |
| 画布保存、恢复草稿、撤销重做 | canvas-operation-journal.ts；use-canvas-project-lifecycle.ts；use-canvas-history.ts | 接串行命令、版本与幂等回执；撤销是受校验的逆操作，不能回滚别人已提交内容 | M1–M2 |
| 版本记录、只读预览、恢复、导出 | canvas-version-history.tsx；backend/internal/canvas/canvas_history.go | 接结构快照、冻结子文档和媒体引用保护；恢复产生新版本 | M1–M3 |
| 画布多轨时间线与成片导出 | components/canvas/canvas-timeline-dialog.tsx；lib/timeline/；types/timeline.ts | 保留源时间线、预览、浏览器渲染与导出流程；不复用分集单轨 UI 或强制改时间精度 | M4 |
| 3D 导演台、布景、机位、动作、深度与法线 | components/canvas/director/；lib/canvas/director/；types/director.ts | 迁入 Three 场景、资产、机位与拍摄流程；截图 / 视频按源动作直接添加，不插入候选采用 | M4 |
| 画布助手、计划、工具与运行反馈 | use-canvas-assistant.ts；services/api/agent-assistant.ts；backend/internal/canvas/capability/ | 保留面板、会话、事件、直接改图、撤销及付费提议；Python 实现兼容工具与事件合同 | M5 |
| 自定义外部工作流、插件节点 | canvas-workflow-builder.ts；generation-workflow-execution.ts；lib/plugins/ | 源基线实际启用的编辑及执行协议逐个实现 Python 适配器；未实现不能通过最终一比一验收 | M5 |
| 画布打包、现有导入导出、历史 BeefTV 数据搬运 | canvas-export.ts；canvas-archive-restore.ts | 保留源已提供的交互与文件合同；批量搬运已有 BeefTV 数据的工具后续交付 | M5 / 后续 |

源 enum 中的 19 类节点都进入最终兼容清单：image、text、drawing、script、skill、config、video、audio、frame、markdown、svg、html、panorama、compare、chart、colorgrade、media-conversion、batch-table、director。兼容数据类型不等于全部允许创建；逐项记录注册器、真实菜单名称、默认值、工具模式过滤、启用状态和可达动作。Frame / Script / MediaConversion 的源禁用入口必须保留。插件动态类型、注册副作用和编辑器插槽另行盘点。

连线与端口语义按源实现迁移，不因连线存在而新增自动执行。服务端重建源工作流的预览、确认和依赖执行规则；若源已有工作流启动流程，不能替换成目标额外的计划审批页。源允许的普通引用环按原解析语义处理，执行依赖按真实协议校验，解析必须去重并限制上下文大小。

## 6. React 迁移与前端结构

### 6.1 保留源结构，隔离构建与样式

继续使用 BeefTV 的 React DOM / SVG / Leafer 实现，不替换为 React Flow、Konva、Fabric 或新写的画布引擎。第一轮保留 project.tsx 编排页、页面 Hook、组件层级、节点注册器、工具注册器、Store 的交互职责和 CSS 类名。仅迁移 InfiniteCanvas 无法得到源页面。

画布独立构建包位于 `frontend/canvas/`，以完整 HTML 文档接入同源工作台。独立包只含画布依赖闭包；按用户后续调整，共同声明但版本不同的直接依赖采用宿主实际解析版本，包括 React / React DOM、Ant Design、Axios、TypeScript、Vite 及其同名插件和类型包。源独有的 Tailwind、Motion、Leafer、富文本、绘图、3D 与 `react-router` 8 等仍在子包独立锁定；AntD 关联的 X / 图标包需匹配宿主 AntD 5。两包都保留自己的 package.json、package-lock.json 和 node_modules，不合并成一个依赖安装目录，不升级标准前端。源清单保留原依赖解析信息，当前安装版本和兼容差异另行记录；不能把源带版本范围的 package.json 当作版本锁定依据。

~~~text
frontend/
  src/pages/canvas/CanvasProjectRoute.tsx     主工作台模式分流与入口
  src/api/http.ts                            两套前端复用的统一传输
  src/api/modules/canvases.ts
  src/api/types/canvases.ts
  scripts/                                  统一启动、构建与产物组合
  canvas/
    package.json / package-lock.json
    index.html / vite.config.ts / tsconfig.json
    src/application.tsx                      画布独立 Provider 与入口
    src/pages/canvas/                         源页面编排与 Hook
    src/components/canvas/                   原节点、面板、工具栏和对话框
    src/components/ui/                       实际依赖的源基础组件
    src/lib/canvas/ / src/lib/timeline/        原算法、注册器与媒体执行器
    src/stores/canvas/                        原交互状态与编辑投影
    src/types/                               源前端合同及内部 ID 映射
    src/styles/                              源 token、reset、工具类与覆盖样式
    src/services/                            保留源调用签名的兼容服务
    src/adapters/                            Python DTO、资源、任务和助手适配
    public/                                  画布必需字体、图标、wasm 与模型资源
    tests/ / e2e/                            源算法及源 / 目标成对回放
~~~

此方式保留独立 React root、HTML、Provider 和样式边界。开发与生产分别提供同源的工作台入口和画布入口；后端仍只有 Python 服务，不增加源 Go / Node 宿主作为必需运行组件。用户仍从当前项目列表进入画布。

统一脚本在 `frontend/` 运行：`install:all` 分别按两包锁文件安装；`dev` 在一个 Node 进程中通过各包 Vite API 创建宿主 8080 与画布 8082 两台独立服务器，退出时统一关闭；`dev:standard` / `dev:canvas` 保留单包运行入口。`build` 分别类型检查并构建两包，将画布 `canvas/dist/` 复制到宿主 `dist/canvas-app/`。`preview` 只运行宿主 Vite preview，对 `/canvas-app/*` 使用画布 HTML 回退、标准页面使用宿主回退，并保留 API 代理；正式静态部署仍须配置对应代理与双入口回退。子包自己的安装、构建、预览和测试脚本继续可独立运行。两包统一运行推荐 Node 22.18+ 的 22.x 或 24.x LTS。

迁入按源 import 闭包逐项登记，包含静态资产、注册副作用、运行时动态导入与 Worker。不要在首轮为了“更符合当前目录规范”拆解页面或合并 Store；必要的账号 / 画布会话隔离放在会话生命周期和服务边界，迁入后再以实际缺陷为依据调整。

### 6.2 视觉依赖闭包

必须核对并迁入以下源行为，而非仅复制主题色：

- application.tsx 中的 antd/dist/reset.css、globals.css、实际生效的 beeftv-local-overrides.css 及加载顺序。
- app-providers.tsx 中的 ConfigProvider、中文 locale、AntD App、message / notification 配置、主题与皮肤应用；账号和 bootstrap 的数据来源替换为目标服务。
- canvas-theme.ts、use-canvas-theme-store.ts、app-theme.ts、skin-themes.ts 中画布使用的 token、深浅主题、背景与本人偏好。
- Inter / JetBrains Mono 字体资源，以及源 application.tsx 设置的 body 字体。以实际 computed style 和字体加载结果为准，不能只看到 globals.css 就判断最终字体。
- 顶栏、侧栏、底部 Dock、缩略导航、节点工具栏、右键菜单、Popover、Tooltip、编辑面板、Modal、Drawer、版本覆盖层和助手层。
- Portal 挂载、z-index、浮层激活规则、焦点恢复、滚动锁、inert、遮挡关系和主题切换。独立 HTML 文档中的 body 浮层使用源主题，不泄露到标准模式。
- Motion 的时长、缓动、展开 / 收起、hover 反馈和 reduced-motion 行为；字体、图标与模型资产的许可和加载路径。

具体尺寸、颜色和动画数值从固定源码与实际页面采集，不在方案中猜测一套近似参数。迁移不得重新布局面板、替换图标、换字号、统一圆角、重写空状态或补充源没有的常驻控件。

### 6.3 四种状态

| 状态 | 保存位置 | 是否共享 | 示例 |
| --- | --- | --- | --- |
| 已保存作品图 | MySQL，由 Service 校验 | 当前项目成员共享 | 节点文字、布局、连线、按源动作回填的媒体 |
| 本人创作设置和过程 | 私人表、现有任务 / Agent 表 | 仅本人 | 提示词草稿、模型配置、任务、不可变结果和诊断 |
| 编辑草稿与提交日记 | 按账号 / 项目 / 画布隔离的 IndexedDB | 本机本人 | 未确认操作、在途请求、冲突副本 |
| 高频交互临时值 | ref、局部状态 | 不共享 | 指针位置、缩放预览、拖动轨迹、框选 |

保留源 Store 向页面和节点提供的调用签名，不把标准项目或其他账号的项目混入。私人字段与作品字段在适配边界分流，会话切换时中止旧读取、核对账号 / 画布 / 编辑 epoch，释放播放和图形资源。IndexedDB 仅存本人草稿、缓存与提交日记，不能覆盖 MySQL 确认基线。

源任务占位节点、生成批次容器、版本分支和结果卡按原样显示，不新建“候选架”。作者画布对象由共享作品加本人参数 / 任务状态构成。未完成或未被源动作附着的结果仍属于私人记录；源动作完成后只发布相应作品和媒体，不发布任务明细。

### 6.4 三个主要适配器

1. **数据适配器**：服务端 snake_case DTO 转为源画布对象；数据库 ID / row_version 保持十进制字符串。node.id / connection.id 保留源稳定键，数据库记录 id 单独保存。源 revision 如有数值计算，改在边界用不透明字符串版本或 BigInt，禁止将数据库 ID 或版本转为 Number。
2. **媒体适配器**：持久化 media_id / resource_id；展示时获取签名 URL，过期重新读取。引擎旧 metadata.content 中的媒体 URL 只作为临时投影，保存 DTO 明确剔除 URL、Blob、data URL 和供应商地址。
3. **生成适配器**：源 executor 的输入、输出与状态合同不变，由现有 frontend/src/api/http.ts 统一传输到 Python。适配排队、进度、结果、失败、取消和版本选择；源回填副作用映射为受控、幂等的物化事务，不要求用户额外点击采用。

`frontend/canvas/` 不另建 Axios 客户端；构建通过 `@host` 显式引用 `../src/` 的现有传输模块及其无 React 依赖的合同。源 HTTP 信封和错误类型由兼容层转换，不让目标通用错误提示替换源画布的正常提示与流程。认证失效和成员权限错误使用当前可信 Actor 规则。

公共节点保留作品正文和节点类型；每个成员的模型、配置节点具体参数、私人 Skill 绑定和生成历史独立保存。作者看到的配置面板、模型选择器和历史保持源布局与行为。其他成员使用自己的配置；这是源本地画布之外的共享扩展，须单独验收。

源 CanvasProject.workspaceProjectId 表示画布分组，而 CanvasProject.projectId 是可选的短剧业务项目绑定。目标所有画布都有宿主 project_id，不代表应把它写进源 projectId；否则会触发 isProjectLinked，改变侧栏、添加菜单和素材入口。兼容层必须区分宿主归属、源分组键、画布 ID 与真实业务绑定，仅在存在合法对应时启用源关联项目分支。不同 ID 的映射须稳定，不能靠字符串相同推断归属。

### 6.5 保存与渲染性能

- 平移缩放沿用 ref 与 requestAnimationFrame 的立即预览，交互停止后保存本人视口；视口不增加共享图版本。
- 一次拖拽、对齐、批量删除或连线调整形成一个命令批次；不逐帧发 HTTP。
- 节点按空间索引和可见窗口渲染；采用进入 / 保留范围减少反复卸载，选中对象保留必要的编辑实例。
- 媒体缩略图、播放代理、可见性预算和激活规则按源实现保留；不得擅自限制源允许的播放或时间线混合预览。
- 节点内容与布局变化分别识别，避免因拖动重算引用、重新挂载视频或重建语义上下文。
- 大图解码、面部识别、ZIP 和密集计算按需放 Worker；重型渲染器只在工具打开时加载。
- 标准模式入口不依赖画布节点注册器；构建产物中 Three、Excalidraw、MediaPipe 不进入标准模式首屏加载链。

源空间索引测试构造了 50,000 个节点，但它不是完整交互、媒体解码与保存的性能验收。目标先建立 100 / 1,000 / 5,000 节点的可重复浏览器基准；5,000 为容量基准，不作为首版无条件流畅承诺。

### 6.6 UI、键盘与状态

主工作台与创建弹窗继续遵循 [PRODUCT.md](../../frontend/PRODUCT.md) 和 [DESIGN.md](../../frontend/DESIGN.md)。无限画布以固定源页面为设计规范，保留各个面板的实际挂载、布局、展开和互斥关系，不把“属性 / 候选 / 版本 / Agent”重新合并为目标新侧栏。窄屏、触控和焦点行为也按源对应场景复刻；新增产品优化另行提出。

保留源 CanvasRefreshShell、加载错误、重试、空状态、保存状态、任务详情和就近提示的呈现方式。映射状态必须准确：本机草稿不能显示为服务器已保存，来源不确定不能显示成功。服务器新增的网络 / 权限 / 并发状态在源既有错误和冲突位置表达；没有对应源场景时列入共享扩展记录，不把源控件隐藏来规避对照。

保留源 Ctrl/Cmd+S、撤销重做、Delete、复制粘贴、Escape、空格临时抓手等全部快捷键和冲突优先级，同时保护输入框、文本编辑器、模态框和下拉的事件边界。源已有键盘帮助、搜索、定位和可访问行为原样迁入；不在本轮增加新的操作入口来替代源行为。

## 7. 数据库设计

### 7.1 存储策略

推荐“关系型结构 + 类型化 JSON 内容 + 不可变历史快照”：

- 画布、节点、连线、版本、任务关联和媒体引用拆为实体。
- 各类节点专有内容使用有 schema_version 的 JSON，由 Pydantic 按 kind 判别校验。
- 源文档到关系结构再回到源对象必须可往返：可选字段、默认值、版本分支、父子关系、轨道顺序、绘图和导演子文档不能在拆表时丢失。新增类型验证不能把源实际允许的数据静默改写。
- 当前图由节点与连线表构成，历史 JSON 是冻结版本；不同时维护一份可独立修改的 current_document JSON。
- 媒体引用建立可查询的关系表，删除检查不靠临时遍历大 JSON。

这比源项目整份 PayloadJSON 更适合服务器、多用户、来源版本和后台任务，但不会拆分当前标准模式的小说、素材或分镜表。

所有数据库记录 id 使用现有 Snowflake BIGINT UNSIGNED，接口为十进制字符串。node_key / edge_key 保留源 nanoid 等不透明稳定键，不强迫源引擎改用数据库 ID 或重新生成所有节点 ID。时间线的数据库 clip_key 遵循当前稳定 UUID 约定；源 clip.id 如不同则保存稳定、可逆的 source_clip_key 映射，不能每次读取或导出重新编号。几何值保留源精度和舍入规则，拒绝非有限数，不擅自吸附到整数坐标。

所有新表沿用 InnoDB、utf8mb4、UTC DATETIME(6)、现有审计字段和明确的外键。版本字段接口同样为字符串。

### 7.2 表清单与交付阶段

以下是完整目标的建议表清单，按阶段落地；最终 DDL 在实施阶段生成并严格核验。

| 表 | 核心字段与用途 | 权限 | 阶段 |
| --- | --- | --- | --- |
| projects，增列 | workspace_mode VARCHAR(16) NOT NULL DEFAULT 'standard'；CHECK 允许两种值 | 原项目权限 | M1 |
| project_canvases | id、project_id、title、position、schema_version、row_version、archived_at、审计字段 | 项目共享 | M1 |
| project_canvas_settings | project_id 唯一、primary_canvas_id、workspace_key、row_version；主画布与稳定源分组键，归档项目无活动画布时 primary_canvas_id 可空 | 项目共享，自动替换主画布与删除原子进行 | M1 |
| canvas_nodes | id、project_id、canvas_id、node_key、kind、parent_node_key、x / y / width / height、z_index、content_json、row_version、content_version、archived_at | 项目共享作品 | M1 |
| canvas_edges | id、project_id、canvas_id、edge_key、from_node_key、to_node_key、from_port、to_port、relation、context_json、archived_at | 项目共享作品 | M1 |
| canvas_write_receipts | id、actor_user_id、project_id、canvas_id、idempotency_key、operation_kind、request_hash、expected_row_version、committed_row_version、result_json、created_at | 仅发起者；支持创建、提交、结果物化和恢复的确定结果 | M1 |
| canvas_revisions | id、project_id、canvas_id、canvas_row_version、schema_version、snapshot_json、content_hash、reason、created_by、created_at | 共享作品历史 | M1 |
| canvas_revision_user_states | id、user_id、project_id、canvas_id、revision_id、schema_version、state_json；冻结源历史中需要保留的本人参数 / 节点扩展 | 仅本人，不能读取其他快照作者的私人字段 | M2 |
| canvas_media_references | id、project_id、canvas_id、owner_kind、owner_key、node_key 可空、slot、ordinal、media_id | 当前共享作品引用 | M1 |
| canvas_revision_media_references | id、project_id、canvas_id、revision_id、media_id | 历史媒体保留 | M1 |
| canvas_user_states | id、user_id、project_id、canvas_id、viewport_json、preferences_json、row_version | 仅本人 | M1 |
| canvas_node_user_states | id、user_id、project_id、canvas_id、node_key、draft_json、model_config_id 可空、row_version | 仅本人生成设置 | M2 |
| canvas_user_media_references | id、user_id、project_id、canvas_id、owner_kind、owner_key、slot、ordinal、media_id；本人节点设置和私人历史中的媒体引用 | 仅本人，媒体访问须匹配本人或合法项目范围 | M2 |
| canvas_task_bindings | id、project_id、canvas_id、node_id 可空、initiated_by、async_task_id 或 media_job_id、source_snapshot、context_hash、源结果动作及稳定 effect_key | 仅发起者 | M2，M3 扩展媒体任务关联 |
| canvas_task_media_references | id、project_id、canvas_id、task_binding_id、media_id、role、ordinal；冻结任务使用的输入 / 中间媒体，提供 FK 删除保护 | 沿 task_binding 仅发起者 | M2 |
| canvas_results | id、project_id、canvas_id、created_by、task_binding_id 可空、result_index、kind、content_json、media_id 可空、attachment_status、row_version、物化回执 | 仅作者的不可变执行结果；附着后仍不共享记录本身 | M2 |
| canvas_drawings | id、project_id、canvas_id、node_key、engine、schema_version、document_json、row_version、preview_media_id、render_media_id | 明确保存的绘图共享；未保存草稿私人 | M3 |
| canvas_timelines | id、project_id、canvas_id 唯一、schema_version、document_json、row_version、current_media_id | 共享剪辑作品；导出历史另按作者隔离 | M4 |
| canvas_media_jobs | id、project_id、canvas_id、initiated_by、kind、snapshot_json、idempotency_key、request_hash、status、stage、progress、lease_token、locked_until、message_version、取消与重试字段、output_media_id | 仅发起者 | M3，M4 扩展渲染任务 |
| canvas_director_scenes | id、project_id、canvas_id、scene_key、title、schema_version、scene_json、row_version | 明确保存的场景共享 | M4 |
| canvas_binary_resources | id、ResourceScope、created_by、published_at、resource_kind、mime_type、storage_locator、byte_size、checksum_sha256、审计字段 | 本人或明确发布的项目资源 | M4 |
| canvas_binary_references | id、project_id、canvas_id、revision_id 可空、owner_kind、owner_key、resource_id | 当前 / 历史 3D 等资源保留 | M4 |

采用 project_canvas_settings 保存主画布，避免在 projects 与 project_canvases 之间建立循环外键。项目模式从 standard 改为 infinite_canvas 的回填只用于经过审核的显式转换，不用于旧项目默认升级。

canvas_media_references 的 owner_kind 包含 node、drawing、timeline、director；node 引用具有同画布复合外键。非节点子文档的 owner_key 与存在性由类型化 Service 验证。canvas_binary_references 同理；revision_id 非空时指向冻结历史引用，不能被当前场景编辑改写。

task_bindings 的 AI 关联使用真实 async_task；非 AI 关联使用真实 canvas_media_job。不能为了满足 AI 表外键而伪造模型配置、供应商调用记录或分集。

### 7.3 索引、外键与约束

| 数据 | 必须建立的约束 / 索引 |
| --- | --- |
| 多画布 | project_canvases 的 (project_id, id) 唯一键，以及 (project_id, archived_at, position, id) 列表索引 |
| 主画布 | project_canvas_settings.project_id 唯一；workspace_key 稳定且唯一；(project_id, primary_canvas_id) 复合外键指向同项目画布，空值只允许无活动画布的归档项目 |
| 节点 | (canvas_id, node_key) 唯一；(project_id, canvas_id) 归属外键；父节点同画布外键；width / height > 0、版本 > 0；坐标必须是有限数 |
| 连线 | (canvas_id, edge_key) 唯一；两端分别引用同画布 node_key；from / to 索引；自连线与重复端口关系按类型合同校验 |
| 写回执 | (actor_user_id, idempotency_key) 唯一；请求摘要包含操作种类与目标，异内容同键返回冲突 |
| 历史 | (canvas_id, canvas_row_version) 唯一；(canvas_id, created_at, id) 时间索引；固定长度 SHA-256 摘要 |
| 当前媒体引用 | (canvas_id, owner_kind, owner_key, slot, ordinal) 唯一；media_id 反向索引及 RESTRICT 外键 |
| 历史媒体引用 | (revision_id, media_id) 唯一；media_id 反向索引及 RESTRICT 外键 |
| 用户状态 | (user_id, canvas_id) 唯一；节点私人设置为 (user_id, canvas_id, node_key) 唯一 |
| 私人历史与引用 | (user_id, revision_id) 唯一；私人引用 (user_id, canvas_id, owner_kind, owner_key, slot, ordinal) 唯一，media_id 反向索引与 RESTRICT 外键 |
| 任务关联 | async_task_id 唯一；媒体任务关联增加后 CHECK 恰好一个任务引用；(initiated_by, canvas_id, created_at, id) 索引 |
| 任务媒体引用 | (task_binding_id, role, ordinal) 唯一；task_binding_id 与 media_id 外键；media_id 反向索引与 RESTRICT；不能只把输入 ID 留在 source_snapshot JSON |
| 执行结果 | (task_binding_id, result_index) 唯一；(created_by, canvas_id, attachment_status, created_at, id) 索引；结构、文本、媒体结果按 kind 校验 |
| 独立文档 | drawing 的 (canvas_id, node_key)、director 的 (canvas_id, scene_key)、timeline 的 canvas_id 唯一；版本 > 0 |
| 非 AI 媒体任务 | 作者与幂等键唯一；待发布 / 租约恢复索引；任务状态、租约字段对与终态时间 CHECK |
| 二进制资源 | 稳定 locator 唯一；校验和、大小、受支持 MIME；引用 resource_id 使用 RESTRICT 外键 |

节点 row_version 表示整行修改；content_version 只在作品正文、当前媒体或影响生成的内容变化时增加。布局移动不使已有结果失效；输入连线、内容和参考媒体变化会改变服务端 context_hash。context_hash 用于输入溯源与并发保护，不能随意增加源没有的正常回填阻断条件。

节点删除先归档并清理活动连线和当前引用；任务出处保留，完成回调不会复活节点。历史保留期结束后的物理清理需检查任务、结果、子文档与历史引用。

### 7.4 必须处理的隐私边界

仅添加 project_id 会落入 access.py 的项目共享兜底。因此必须在该兜底之前显式定义：

- canvas_user_states / canvas_node_user_states / canvas_revision_user_states / canvas_user_media_references：user_id 等于 Actor，且仍有项目访问权。
- canvas_task_bindings / canvas_media_jobs：initiated_by 等于 Actor，且仍有项目访问权。
- canvas_task_media_references：沿 task_binding 作者检查，不能落入普通项目共享分支。
- canvas_results：created_by 等于 Actor，且仍有项目访问权。
- canvas_write_receipts：actor_user_id 等于 Actor；查询和重放时重新校验目标访问权。
- canvas_binary_resources：沿用本人资源 / 项目已发布资源规则。

同时扩展 scope_of、before_flush 写保护、身份映射校验、bulk write 防护和 audit_events 过滤。私人表的创建、错误与运行标识不得通过项目审计泄露；共享审计只记录实际作品变更。

共享节点、共享快照、封面和共享导出不得包含内部 result_id、task_id、config_id、原始生成参数、供应商错误、私人首帧或对话内容。结果物化只发布源动作写入的产物与作品数据，不递归发布全部输入资源。作者本人的节点面板、任务详情、生成历史和源格式导出通过本人投影提供其有权查看的源字段；不得误将共享 DTO 的删减视为作者功能也要删减。

个人素材作为源动作的共享输出或项目素材时，由服务端建立独立项目副本，界面仍保留源导入步骤。禁止将本人 scope 的媒体直接绑定为共享作品。成员退出后共享作品留在项目；其私人结果、任务和对话访问立即撤销。

## 8. Python Service、接口与操作合同

### 8.1 分层与职责

建议新增：

~~~text
backend/src/short_drama/
  api/v1/canvases.py
  schemas/canvas.py
  schemas/canvas_operations.py
  schemas/canvas_generation.py
  schemas/canvas_media.py
  domain/canvas.py
  dao/canvas_dao.py
  service/canvas_service.py
  service/canvas_context_service.py
  service/canvas_generation_service.py
  service/canvas_result_service.py
  service/canvas_history_service.py
  service/canvas_media_service.py
  service/canvas_timeline_service.py
  service/canvas_timeline_plan_service.py
  service/canvas_assistant_service.py
  tasks/canvas_media.py
~~~

文件名是建议；按阶段拆分真实职责。路由负责 HTTP 和依赖注入，Service 管理权限、规则和事务，DAO 执行查询，ORM 与 SQL 同步。外部协议继续放现有 ai 层。

### 8.2 API 清单

所有路径相对 /api/v1，保持当前项目的响应及错误约定。画布兼容服务把这些 DTO 与错误转换成源服务调用签名和前端预期；源组件不用感知 Python 信封。原 Go / SQLite 的存储实现不复制，源接口的成功、失败、重试与版本语义必须逐项移植。

| 方法与路径 | 行为 |
| --- | --- |
| POST /projects | 接收 workspace_mode；无限画布分支原子创建主画布并接受幂等键 |
| GET /canvas-write-receipts/{key} | 查询本人创建请求的回执；项目 / 画布 ID 尚未知时也可恢复确定结果 |
| GET /projects/{project_id}/canvases | 分页画布摘要 |
| POST /projects/{project_id}/canvases | 新建空画布或明确复制共享作品，带幂等键 |
| GET /projects/{project_id}/canvases/{canvas_id} | 返回版本化共享图，不混入私人任务和设置 |
| GET /projects/{project_id}/canvases/{canvas_id}/my-document | 返回本人编辑投影：共享图与明确区分的本人设置 / 任务字段，兼容源画布对象 |
| POST /projects/{project_id}/canvases/{canvas_id}/commits | 原子应用受支持的编辑操作，带 expected_row_version 与幂等键 |
| GET /projects/{project_id}/canvases/{canvas_id}/write-receipts/{key} | 查询本人不确定请求的确定回执 |
| DELETE /projects/{project_id}/canvases/{canvas_id} | 带版本的归档，自动维护主画布并返回源导航对应目标，不新增选择步骤 |
| GET /projects/{project_id}/canvases/{canvas_id}/user-state | 本人视口和界面偏好；节点设置按需另取 |
| PATCH /projects/{project_id}/canvases/{canvas_id}/user-state | 本人版本化偏好修改，不改共享图版本 |
| GET /projects/{project_id}/canvases/{canvas_id}/nodes/{node_key}/my-settings | 本人生成草稿与模型设置 |
| PATCH /projects/{project_id}/canvases/{canvas_id}/nodes/{node_key}/my-settings | 保存本人设置，验证本人配置 / Skill 引用 |
| POST /projects/{project_id}/canvases/{canvas_id}/generations | 解析已保存来源，接入现有生成准入 |
| GET /projects/{project_id}/canvases/{canvas_id}/results | 仅本人执行结果与生成历史，供源历史 / 版本 / 对比入口读取 |
| POST /projects/{project_id}/canvases/{canvas_id}/results/{result_id}/materialize | 源完成副作用触发的新增 / 替换 / 版本选择，自动调用且原子幂等，无额外采用按钮 |
| GET /projects/{project_id}/canvases/{canvas_id}/revisions | 历史列表与只读预览 |
| POST /projects/{project_id}/canvases/{canvas_id}/revisions/{revision_id}/restore | 备份当前作品，恢复为新版本 |
| GET /projects/{project_id}/canvases/{canvas_id}/drawings/{node_key} | 当前已保存绘图 |
| PUT /projects/{project_id}/canvases/{canvas_id}/drawings/{node_key} | 校验绘图版本、结构与稳定渲染引用 |
| GET /projects/{project_id}/canvases/{canvas_id}/timeline | 画布时间线 |
| PUT /projects/{project_id}/canvases/{canvas_id}/timeline | 版本化保存 / 命令提交，验证所有媒体与时间范围 |
| POST /projects/{project_id}/canvases/{canvas_id}/timeline/render-plan | 只读编译源 canonical plan，不排队、不计费、不修改画布，保留源校验与失败语义 |
| POST /projects/{project_id}/canvases/{canvas_id}/media-jobs | 提交裁切、取帧、合并、转写或导出等非 AI 任务 |
| GET /projects/{project_id}/canvases/{canvas_id}/director-scenes | 导演场景列表 |
| POST /projects/{project_id}/canvases/{canvas_id}/exports | 冻结共享图，异步生成可下载归档 |

具体取消、恢复、重试接口沿用现有任务动作约定；新媒体任务需要同样明确的许可字段。无限画布不支持的操作返回机器可读原因和就近提示，不回退为另一次付费生成。

主画布变更和画布重命名分别提供版本化 settings / metadata 接口；导演场景提供同项目范围的创建、读取、修改与归档接口；本人非 AI 任务提供详情和允许的动作接口。具体端点随相应阶段的 Pydantic 合同一起冻结，禁止直接开放 ORM 通用 CRUD。

### 8.3 图编辑批次

示例合同：

~~~json
{
  "expected_row_version": "42",
  "schema_version": 1,
  "operations": [
    {
      "type": "move_nodes",
      "items": [
        {
          "node_key": "455d9e41-0cba-4877-bc37-8b15fef80fb5",
          "x": 320.5,
          "y": -80
        }
      ]
    }
  ]
}
~~~

命令采用判别联合，包含 add_nodes、update_node_content、move_nodes、resize_nodes、delete_nodes、connect_nodes、delete_edges、set_parent、update_canvas_settings 等明确操作。所有操作白名单、字段白名单和类型上限由服务端校验，拒绝任意数据库字段 patch。

同一批次全成功或全失败。锁顺序从 Project 开始，再锁 Canvas，随后按稳定顺序锁相关对象；结果物化、Agent 工具和媒体任务入场遵循相同顺序。限制批次数量、文档大小、节点几何和递归层级，不允许循环父子、跨画布端点和越权媒体。源一次动作涉及节点、连线、容器或子文档的变化必须完整落在同一动作中，不能改变撤销粒度。

### 8.4 串行自动保存与不确定请求

编辑会话的状态机：

~~~text
编辑 → 本机草稿 / 提交日记 → 串行提交 → 服务端回执 → 确认基线
                                   ↓ 网络不确定
                            保留原键，查询或重放原请求
                                   ↓ 409
                            保存冲突副本，暂停提交
~~~

提交成功只确认该请求包含的操作，不清空请求发出后产生的新输入。旧响应先与编辑 epoch、操作键和确认版本核对，不能将新输入替换成旧快照。

幂等重放先核对本人权限、原请求摘要和已提交回执，再进行版本判断；否则已经成功的旧版本请求会被误判为新冲突。

409 后保留源冲突草稿、查看差异、加载最新与明确强制保存的入口和表现，将操作映射到基于最新版本的合法事务。三方差异不得静默覆盖本人草稿或其他成员作品。因服务器多人共享新增的冲突用例单独登记，不改造正常无冲突保存的界面与步骤。明确强制保存不能跳过 Service 的媒体和作品范围检查。

撤销重做以命令为单位。没有服务器变化时可撤销本人当前会话操作；服务器版本已变时先核对，不将别人的修改作为自己的撤销对象。生成中的取消与作品撤销分别处理。

## 9. 生成、批量与源结果回填

### 9.1 接入现有异步生成

新增 CanvasGenerationService 作为业务入口，复用 [AIGenerationService](../../backend/src/short_drama/service/ai_generation_service.py)。

其中 create_locked 已支持持有项目锁的授权调用者在同一事务中准入任务，可用于画布任务与 task_binding 的原子创建。实现时扩展类型化 CanvasGenerationSource、来源准备与恢复分支；不将 source 元数据当作绕过普通契约校验的任意 JSON。

建议来源字段：

~~~json
{
  "scene": "canvas_node",
  "project_id": "902001",
  "canvas_id": "902002",
  "node_key": "455d9e41-0cba-4877-bc37-8b15fef80fb5",
  "content_version": "7",
  "context_hash": "0000000000000000000000000000000000000000000000000000000000000000"
}
~~~

context_hash 由服务端对实际保存的内容、参考顺序、端口语义、媒体身份和必要版本计算；示例全零仅表示格式，不是可用摘要。生成前必须等待共享作品和本人参数保存完成，再冻结实际输入。

当前 image / video / audio / text 输入合同分别扩展，重点包括：

- 图片蒙版、图片编辑 / 超分等 operation；当前 ImageInput 没有 mask_media_id。
- 视频参考类型、延长、重绘、镜头运动等操作的协议能力。
- 音频实际输入、音色和配音约束；不把 source audio 节点误当图片参考。
- Script / Chart 等结构化文本结果的 Pydantic 校验。

调用选用本人已启用配置，密钥仍在服务端加密存储。模型选择不新增人工“能力验证”流程；操作所需能力根据真实适配器合同核对，未知或不支持的错误准确反馈。

source_snapshot 冻结可用作品及实际参数。生成阶段变化或节点删除不会把新输入写进旧任务；结果完成后记录为本人的不可变执行结果。兼容层继续运行源完成逻辑，把新增结果、更新节点、批次容器和版本主选中动作映射到服务端物化接口；不能因为换后端而停止回填或要求额外采用。已删除节点和被后续任务替换的关联不得被旧回调复活或覆盖。

### 9.2 保留源回填语义的物化事务

1. 按源提交动作冻结目标、输入、结果落位策略和稳定副作用键；源原本的确认保留，不额外插入批准。
2. 验证结果作者、当前成员资格、真实任务关联、节点 / 批次 / 版本分支及允许的结果动作。
3. 验证产物已经归档 MinIO 且元数据可用；保留源归档中 / 错误 / 重载状态，不能提前显示成功。
4. 执行源 buildGenerationTaskNodeResult、批次 materializer、版本主选中等对应变化，保留几何、顺序、父子关系、连线与当前结果选择。
5. 原子发布此次动作的作品媒体，更新当前引用、作品版本和幂等回执；私人任务、提示词和诊断不进入共享行。
6. 回执映射回源对象并确认本次副作用；重复事件、刷新恢复和消费者重放只应用一次。

具体回填条件以固定源码为准，不将所有工具都归并成“新增候选”。源的直接替换、另建版本、默认选中、展开 / 折叠和自动落位都需要独立对照。其他成员在此期间修改目标导致冲突时，保留结果和本人草稿，在源已有冲突 / 历史入口恢复；不能静默覆盖或另建一套强制候选审批流程。这一多人边界是当前项目扩展。

连续生成、节点版本、批次主结果和对比保留源选择规则。工作流后继使用源执行计划指定的输入与依赖产物，不要求用户逐步采用后才可继续。未附着图的中间结果和任务上下文仍为私人执行数据；共享引用边只能指向已持久化、具有项目权限的作品资源。

### 9.3 批量与工作流

扩展现有 generation_batches 的 scene、来源预检和 CHECK，新增 canvas_image、canvas_video 等经过确认的场景；保留原 asset / shot 分支。

每项冻结 node_key / row_key、来源版本、模型配置版本与参考清单。保留源表格列、设置项、默认并发、预览、确认、任务分组和重试入口；服务端按这些设置调度并施加实际运行限额。若限额使源设置无法执行，明确记录环境差异并修复配置或合同，不能悄悄删控件或改变默认值后声称一致。

子任务仍由 async_tasks 管理。部分失败可按 can_retry / can_resume 恢复，结果不确定的项禁止自动重新提交。源批准或启动时冻结实际参数；内容变化后的预览、确认与启动规则沿用源实现，不增加目标专用审批面板。

按依赖顺序执行的工作流需要兼容源的计划合同；简单连线图不自动成为执行计划。RunningHub 等基线已启用的外部工作流由 Python 适配器解析源协议包、节点端口与执行状态，不运行 Go 插件二进制。需要本地执行端的能力必须给出可运行的 Python 协议实现或明确外部依赖；缺少执行端时不能通过该项验收。

## 10. 绘图、媒体加工、时间线与导演台

### 10.1 绘图

Excalidraw 文档与最终参考图分别保存，持久化 drawing_id、document_version、render_media_id 和 checksum。生成引用必须对应当前保存版本的渲染图；缺失或版本不一致时明确阻止任务。

绘图正文保存失败保留草稿。上传渲染图期间若画板版本变化，不能把旧图标成新版本的参考图。历史快照冻结对应绘图文档及媒体引用，不能只指向会继续变动的 canvas_drawings 当前行。

节点复制与归档导出同样捕获一次完整绘图版本，再按该版本的稳定资源身份读取预览和成品；不能独立重复查询当前笔画、预览和成品后组合。读取保留账号及删除标记检查，来源同时保存时副本中的笔画和派生图必须仍属于同一版。对应实现和实际并发验证记录在[实施记录](2026-10-05-beeftv-implementation-log.md)，不以构建通过代替字节及状态验收。

### 10.2 非 AI 媒体任务

源在浏览器执行的图片加工、视频取帧、裁剪、切分、合并、音轨抽取和时间线导出继续在浏览器执行，保留对应 Canvas / FFmpeg wasm 算法及参数、Worker 生命周期、队列、进度阶段、取消与结果落位。canvas-ffmpeg-session.ts 的独占 lease 与时间线导出独立 Worker 的取消边界都要迁入，不能简单换成一个全局媒体队列。

Python 提供稳定媒体读取、受控远程取回、上传归档与元数据探测，源确需后端执行的 ASR / 协议能力进入 canvas_media_jobs。上传和结果持久化映射到源已有保存与错误流程，不新增人工确认。源浏览器工具依赖的 wasm、Worker、字体、模型资产、响应头、CORS 与加载路径必须完整交付。

确需原生服务时可复用 [video_render.py](../../backend/src/short_drama/service/video_render.py) 的受控进程、超时、临时空间、取消与探测能力，以及现有 render Worker 的租约思想。新任务使用独立 ORM 与 publisher / recovery 入口，不将 canvas_id 塞进 episode_id，也不新增虚假的 async_tasks.service_type='render'。服务端编译白名单操作，禁止客户端传任意本机路径或直接执行完整命令；失败、取消和重复投递均须处理。

浏览器加工后按源动作下载、添加、替换或回填，真实媒体归档后更新共享作品。未附着的中间结果仍为私人数据；缩略图与播放代理只沿作品权限展示。将源浏览器 FFmpeg 改为服务器原生 FFmpeg 属于后续优化提案，必须先证明界面、进度、取消、输出与正常流程一致，不能在本轮默认替换后声称一比一。

### 10.3 画布时间线

源 TimelineProject 的前端类型定义 video / audio / subtitle / text / image；这些类型和编辑 UI 要分别盘点。它与当前 EpisodeAssembly 的分集剪辑模型不同，但类型声明不代表每种内容已经能成功导出。

画布使用独立 TimelineDocument，保留源 TimelineProject version=2 的 tracks、clips、durationMs、startMs、sourceStartMs、sourceDurationMs、fadeInMs / fadeOutMs、directMedia、字幕文本与关联语义，以及各类轨道、顺序、锁定 / 可见 / 静音和编辑规则。源时间线主要使用毫秒，不擅自改为整帧存储或强制按 30 fps 取整。track / clip 的稳定键在服务边界映射，前端仍读取源对象。

源 timeline-export 先经 services/api/timeline-tasks.ts 调用 POST /api/timeline/render-plan，由 Go editing.Compile 生成 canonical plan，再由浏览器 lowerCanonicalPlan 与 FFmpeg wasm 执行。因此 Python 必须移植 backend/internal/editing/ 的语义编译、类型、默认值和校验，兼容只读规划接口；源接口失败时不能擅自回退为另一套前端编译规则。

实际编译规则也必须保留：当前 Go 编译器接收 video / image / audio / subtitle，可见 text clip 返回不支持类型；可见视频 / 图片按时间形成单一视觉序列，跨视觉轨重叠也返回错误，间隙补 gap，音频可混合，隐藏轨跳过，静音、裁剪与淡入淡出按源处理。这不是任意视觉轨叠加引擎，不能将补齐 text 导出或视觉合成当成原功能迁移。M0 对照源 UI 的转换 / 隐藏路径与实际运行结果。

默认输出在源未指定时为 1920×1080、30 fps、44100 Hz、烧录字幕；显式选项与源范围保持一致，不把默认 30 fps 误解为所有时间数据都必须量化到 30 fps。保留源 timeline-export、timeline-to-ffmpeg、canonical plan 和渲染服务算法，以及字幕画面、音频混合与缩放规则。导演输出的 24 / 25 / 30 fps 选项必须保留。源已启用的后端 timeline/renders 路径同样移植到 Python 媒体任务，使用相同语义计划，不能把它改接当前分集 renderer，或仅因同样使用 FFmpeg 就宣称输出相同。

预览、导出保留源交互与结果处理；冻结当前时间线和媒体来源，导出期间继续编辑不会改变正在处理的输入。不新增“成片候选 → 设为当前成片”步骤。需核对真实画面、字幕、音轨、时长、帧率、裁剪、淡入淡出和取消清理，不只比较任务返回成功。

自动字幕依赖 ASR 能力：采用目标现有可用供应商或单独部署的 Python ASR / whisper 服务，需要配置、超时与专项验收。源依赖 whisper.cpp 的配置不能直接迁到目标后视为可用。

### 10.4 3D 导演台

保留场景、角色、模型、材质、灯光、机位、轨迹、镜头和拍摄输出，Three / R3F / Drei 懒加载。

场景 JSON 只保存稳定资源 ID。GLB / glTF、纹理及引用资源按实际类型进入受控二进制资源 / 媒体表；glTF 的外部文件须打包或重绑为受控资源，不允许任意路径读取。MinIO 访问、CORS 与加载器凭据边界单独验收。

保存场景时冻结 schema_version 与引用；保留源截图、深度、法线、白模视频、机位组和拍摄输出的原有动作及添加方式，不改成统一候选面板。场景预览与录屏继续按源浏览器流程执行，最终文件须归档。WebGL 上下文丢失、加载失败、超大模型和取消录制要保留编辑稿，按源错误入口处理。GLB / glTF 与相关纹理、动作资源缺失时，不能使用示例模型替代后声称导演台迁移完成。

### 10.5 HTML / SVG 与插件

源 HTML 节点使用 SandboxedFrame，允许脚本但不同时授予 allow-same-origin，并支持自身内容、上游文本与 {{input}} 模板替换。必须保留这套隔离和功能；把所有脚本禁用会破坏源交互式 HTML 预览。此处 iframe 仅用于 HTML 节点，不是整个画布。SVG、Markdown、图表与导入内容沿用源渲染及隔离规则，并以专项样本核对实际支持的内容；后端不执行模型输出代码。

保留节点注册器、内置插件注册副作用、编辑器插槽、未知节点表现与实际启用的插件工具。运行时插件的版本化 schema、能力与执行协议移植到受控 Python 服务或明确的外部运行依赖。基线中已经可用的画布工作流不能归为“以后再做”后仍称完整复刻；新增插件生态、整站插件市场与支付不在本轮范围。

## 11. 画布助手与现有 Python Agent

### 11.1 需要的结构升级

现有 AgentConversation / AgentArtifact 的 episode_id 必填，运行、授权和附件检查也沿着分集查找。推荐保留既有分集合同，增加判别清晰的画布合同：

~~~text
domain: "episode" | "canvas"
episode domain: episode_id 必填，canvas_id 为空
canvas domain:  canvas_id 必填，episode_id 为空
~~~

已有记录回填 domain='episode'。新增 canvas_id 复合归属约束，调整 episode_id 可空与作用域 CHECK；画布 subject_type 为 canvas / canvas_node，subject_id 使用数据库画布或节点记录 ID，task_type 区分 planning、creation、image、video、audio、batch。

保留旧 scope_version=0 / 1 分支，画布作用域使用新版本，配套完整 DTO、Service、db/access、运行检查点、附件解析、工具授权和前端范围键。标题、节点坐标和分镜序号均不能决定会话身份。

AgentArtifact 增加画布工具结果与提议种类或稳定关联指针，但不得强制套用标准 Agent 的候选采用流程。源直接改图的工具通过同一图操作 Service 执行，源付费生成提议只登记并等待其原有确认。会话与执行细节私人，工具实际写入的画布作品按项目权限共享。

### 11.2 工具与交互迁移

工具范围：

- 读取当前已授权画布摘要、选中对象与限定参考内容。
- 读取本人历史、候选及可用模型 / Skill。
- 按源授权与工具合同创建或修改节点、连线、分镜表、布局与内容，保留直接执行及回合撤销。
- 保留源付费生成提议、数量、模型、来源版本和确认后的既有生成链路；源多步骤流程按原合同执行。
- 读取任务状态、请求允许的取消 / 恢复 / 重试。
- 保留源运行高亮、实际变更摘要、生成提议和历史，不引入标准 Agent 的新候选采用面板。

工具必须重读服务端来源版本与成员资格。选择模型不新增能力声明流程；缺少实际参数时在对话中补齐。Agent 对话和过程仅本人可见，运行反馈使用现有事件流恢复约定。

画布助手 UI 原样迁入，包含选中节点上下文、模型配置、可用 / 不可用状态、消息流、工具反馈、变更摘要、提议、会话历史、取消和撤销。Python 新增画布兼容运行策略，逐项实现源工具 schema、授权范围、事件顺序、状态转移和持久化合同；复用现有 Agent 的账户、任务和事件基础设施，不直接把标准 Agent 面板或决策流程换进去。

源 pi / Node 宿主与 Go 代理不成为目标新增必需服务。改用 Python / pydantic-ai 并不天然等价：必须用固定模型返回 / 工具脚本对照确定性行为，再用真实模型验证工具可达、执行与恢复。随机生成的回复文字和媒体内容不要求逐字逐像素相同，但不能因此放宽工具、步骤、权限、取消、撤销或结果处理的验收。

标准 Agent 的 scope、默认值、工具行为和历史记录都要做回归。此阶段触及现有未提交 Agent 改动，实施前必须在已确认基线上接续。

## 12. 版本历史、资源保留与导入导出

### 12.1 历史策略

保留源普通快照的“至少间隔 5 分钟、最近 20 份”及 before_restore 等真实触发条件、排序和恢复流程；版本号与快照数量保持不同语义。服务器可有不出现在用户历史列表的内部恢复记录，但不能擅自增加可见快照、重排列表或改变默认保留数量后声称一致。额外产品历史策略后续单独提出。

共享历史快照包含作品节点、连线、类型化子文档及对应不可变资源身份，不含私人任务、参数或聊天。源历史中作者可见的私人字段如需恢复，使用作者专属的不可变历史扩展，不写入共享 snapshot_json；M0 盘点源各类文档历史的实际覆盖。绘图、时间线、导演场景不能保存可变当前行的裸指针。

只读预览允许定位、缩放与下载。恢复核对当前 row_version、先备份当前版本，再校验全部资源并形成新版本；不把 row_version 改回历史数字，不修改所有权和项目模式。

### 12.2 资源生命周期

媒体删除检查覆盖当前节点、绘图、时间线、导演输出、私人结果、任务来源和历史；二进制资源检查对应全部当前 / 历史引用。面向用户的引用说明只返回其有权查看的来源，不能泄露另一成员的私人任务。

归档项目或画布不是立即物理删除文件。资源清理按既有回收策略及引用保留期执行，物理删除失败不能先删除记录。未完成上传 / 归档的孤儿对象需要可重试清理与审计，不能将 Blob URL 留为持久化身份。

### 12.3 源现有导入导出与后续数据搬运

源当前画布中已有的导入 / 导出 / 打包 / 恢复入口和文件格式属于功能复刻范围，按源布局、交互、默认项和资产处理逐项迁移。作者导出可以包含本人有权读取的源参数字段，但不能包含凭据或其他成员私人数据；需要新增目标扩展字段时保证源格式可识别部分与稳定 ID 映射可往返。

用户确认后续再做的是已有 BeefTV 项目、媒体和历史的批量搬运工具，不是延期源已有的画布导入导出功能。历史工具优先接 BeefTV 导出 ZIP / JSON，原 SQLite 仅只读抽取为受控中间格式；不得直接把源数据库表批量导入目标业务库。

处理流程：

1. 预检归档版本、文件完整性、大小、路径与校验和，防止路径穿越和异常解压。
2. 明确目标账号、新建目标项目与归属，不推断源用户与目标账号对应关系。
3. 生成 project / canvas / node / edge / asset / resource ID 映射。
4. 将源 workspaceProjectId 分组转换为一个目标项目的多画布。
5. 上传媒体并探测真实元数据，重绑 storageKey、节点、绘图、时间线与导演资源。
6. 源已保存作品经导入预览后明确成为目标作品；私人生成参数、聊天及原调用记录留在本人导入资料，绝不混入共享图。
7. 移除密钥、供应商凭据、临时 URL 和本机路径；不复制源模型渠道的秘密配置。
8. 对禁用或未知类型提供只读兼容 / 转换报告，保留原资料，不静默丢弃字段。
9. 无法取得文件的 Blob / 本机临时 URL 进入缺失清单，不声称自动恢复成功。
10. 将旧版本转换为带来源说明的目标历史；数量、绘图及媒体不完整时准确标记。
11. 记录可重入导入任务与映射；批次失败保留可恢复状态，不产生半绑定公开节点。

后续可增加 canvas_import_jobs / canvas_import_mappings，键为作者、来源摘要与源稳定 ID。导入模式转换、缺失媒体与不支持功能均提供可审阅报告，不强制转换源时间基准。源已有文件的功能性导入与后续批量迁移工具分别验收。

## 13. 分阶段交付与门禁

工作量为源码分析后的粗估“人日”，不等同于日历工期；M0 完成后用实际迁入和兼容性结果重估。阶段可以内部并行，但依赖与验收按顺序。

| 阶段 | 范围与主要交付 | 进入下一阶段的门禁 | 粗估 |
| --- | --- | --- | --- |
| M0：基线与兼容验证 | 实际运行固定源版本；生成视觉、交互、功能和服务合同清单；采集截图 / 操作轨迹 / 资产；验证独立构建与代表性 Python 适配 | 默认模式、构建开关、依赖锁、源基准可复现；共享扩展与例外记录明确 | 5–8 |
| M1：双模式与持久化画布 | 创建单选、模式字段、主画布、独立入口与原布局；基础节点 / 分组 / 连线 / 视口、上传、保存 / 日记 / 草稿、基础历史 | 标准回归、真实 MySQL / MinIO 链路通过；基础布局与操作成对验收 | 12–18 |
| M2：四类生成与原流程回填 | 类型化来源、私人投影、任务关联、文本 / 图片 / 视频 / 音频、直接回填、版本选择、历史与不确定恢复 | 源回填 / 取消 / 重试 / 版本行为对照通过；所选供应商逐类型真实验收 | 12–20 |
| M3：创作工具与批量 | 保留源可用性；表格、批量、Skill / 风格、绘图、图片工具、文档 / 图表 / 对比与必要媒体服务 | 对应节点和每个工具动作通过视觉 / 状态回放；真实绘图参考与蒙版协议验收 | 18–30 |
| M4：媒体与导演 | 原浏览器视频工具、字幕 / ASR、多轨时间线与导出、全景 / 调色、导演及原资产；禁用入口原样保留 | 实际 wasm / ASR / WebGL 和真实样片通过；进度、取消、输出及保存对照通过 | 18–30 |
| M5：助手、工作流与完整验收 | Python 画布助手合同；基线启用的工作流 / 内置插件；源导入导出；全量清单、截图与操作回归 | 所有基线启用功能通过，禁用状态正确；零未解决的一比一差异；标准功能和共享边界回归通过 | 14–24 |
| 后续扩展 | 已有 BeefTV 数据批量搬运、新增工作流 / 插件生态、多人实时协同、服务器加工优化 | 按各自独立验收合同 | 单独估算 |

严格复刻的 M0–M5 粗估合计 79–130 人日，用于说明范围扩大，不是执行承诺；原功能迁移的 60–99 人日估算不再适用于本版。M0 实际运行后按功能条目、可用供应商和插件执行端重估。范围不含历史批量搬运、新插件生态、实时协同和后续原生媒体优化；源已启用画布功能不能从估算中漏掉。

M1–M4 可作为明确标记的阶段预览，必须列出尚未迁入的源启用功能；它们不满足完整一比一完成定义。尚未可用的入口可在阶段预览限制执行，但不得把这种限制当成最终一致。源本来禁用的入口则从开始到最终版本保持同样状态。

M5 最终发布须同时满足：基线清单无遗漏、全部启用项真实可用、视觉与交互对照通过、标准模式回归通过、共享扩展通过、没有未经用户明确接受的界面或流程偏差。某功能因供应商、Python 协议或资产缺失未完成时，准确标为未完成，不能以“后端已换栈”作为通过理由。

## 14. 验证与验收

### 14.1 自动测试覆盖

| 范围 | 至少验证的正常与异常路径 |
| --- | --- |
| 双模式 | 省略模式默认标准；两种创建；非法模式；模式不可普通修改；画布创建失败整体回滚；重复 / 不确定创建重放 |
| 标准回归 | 项目详情、分集增删、小说保存、素材、分镜、候选采用、声音与成片；旧 URL 和旧 Agent 范围 |
| 图结构 | 跨画布端点、未知类型、重复键、循环父子、无效几何、操作批次中途失败整体回滚 |
| 保存 | 延迟旧响应、新输入保留、连续拖动合并、离开等待保存、刷新恢复、409 暂停、回执重放 |
| 权限与隐私 | 主人、协作者、第三方、离开成员；私人结果 / 任务 / 参数 / 对话 / 私人媒体不能通过共享 DTO、快照、导出或审计泄露；作者投影不丢源功能 |
| 生成 | 保存后提交、稳定幂等键、模型归属、参考容量、来源变更、节点删除、归档失败、unknown 不重发 |
| 回填 | 作者 / 任务关联、源新增或替换条件、多产物、版本主选中、落位与连线、重复副作用、归档失败和共享冲突；不得插入额外采用 |
| 批量 | 并发限制、部分失败、取消、恢复、未知项、参数变化重新批准、重复投递 |
| 绘图 / 导演 | 保存冲突、上传期间修改、渲染版本匹配、资源过期与缺失、撤销和上下文丢失 |
| 历史与资源 | 恢复为新版本、快照与当前图独立、历史引用阻止删除、资源清理失败保留记录 |
| 真实媒体 | 源浏览器 wasm 的取帧、裁剪、合并、视觉序列 / 间隙、音频混合、字幕及真实 ASR；保留重叠 / 不支持类型错误，校验输出与取消清理 |
| 导入导出 | ID 重映射、文件缺失、未知类型、私有内容隔离、路径穿越、校验和与重复导入 |
| UI | 鼠标 / 键盘 / 触控、输入和浮层事件、窄屏、loading / 空 / 错误、焦点、浏览器历史导航 |

从源测试迁入有价值的纯算法用例，转换 Bun 测试入口到独立包的 Node runner。源码字符串断言可辅助检查，但不能替代 Playwright 交互、真实 MySQL 事务或实际媒体结果验证。源 / 目标 API 合同对照使用同样输入与确定性返回，分别校验状态、错误、默认参数、重放和副作用；mock 结果不得计入真实供应商通过记录。

### 14.2 视觉对照验收

M0 保存源画布的可复现基准集，包含基线清单、账号偏好、数据 / 媒体校验和、浏览器精确版本、Windows 字体、DPR、视口和依赖锁摘要。推荐桌面至少覆盖 1440×900 与 1920×1080，窄屏至少覆盖 390×844 和 768×1024；支持范围以源实际运行情况记录。

| 基准场景 | 必须覆盖的状态 |
| --- | --- |
| 页面与主题 | 初次加载、空画布、正常 / focus / 只读 / 版本预览，深浅主题，背景与工具模式 |
| 工具与浮层 | 默认与自定义 Dock、添加菜单各分区、设置、搜索、缩略导航、右键菜单、Tooltip / Popover |
| 节点 | 19 类兼容数据的实际渲染；普通、hover、选中、多选、编辑、缩放、折叠、版本与关联高亮 |
| 创作过程 | 上传、队列 / 生成 / 归档 / 成功 / 失败 / 取消、批量部分失败、任务详情与结果落位 |
| 编辑面板 | 源实际可达的文字、提示词、蒙版、裁切、绘图、风格、素材与历史等面板 |
| 高级工作区 | 时间线轨道 / 预览 / 导出，导演各面板 / 机位 / 拍摄，助手消息 / 工具 / 提议 |
| 恢复与异常 | 保存冲突、加载失败、资源失效、历史恢复、窄屏与浮层遮挡 |

两端使用同一测试数据、媒体和模型响应，固定时间、随机输入、播放位置及最终稳态后采图；源与目标采用相同固定方式。动效另采关键帧或操作视频，不能全部关闭动画后声称动效一致。字体加载完成、WebGL 场景相机 / 光照 / 资源相同后再比较。

复用源 web/scripts/compare-canvas-screenshots.mjs 的指标与算法，并补充逐区域差异图、DOM 几何 / computed style 断言和失败门禁。该源脚本只输出整体相似度等指标，本身不会判定复刻通过；仅报告“整体相似度 99%”不足以排除丢按钮或局部改版。

验收规则：

- 稳定的控件、布局、图标和文本区域以无可解释视觉差异为目标；位置 / 尺寸允许的量化误差先用源对源重复采样校准，不能用宽松阈值掩盖真实偏移。
- 字体抗锯齿、WebGL 等平台噪声只能按源自重复测量登记有限容差；同一环境仍有真实外观差异时必须修复。每个工具区、面板和节点类型分别通过，不能用全图平均值抵消局部失败。
- 项目名称、时间戳、媒体结果等动态内容优先固定数据；确需排除时列明唯一对应区域及原因。禁止遮掉整个节点、工具栏、右栏或失败功能。
- 菜单文字、顺序、默认值、可用性、字段、图标与交互入口逐项精确核对。测试基准不能用目标截图反过来替换源截图。

### 14.3 交互与功能成对回放

同一测试轨迹分别作用于源和目标，在每个关键步骤记录画布文档、选区、视口、焦点、打开面板、撤销栈动作及生成结果选择。仅可标准化内部数据库 ID、存储定位值、授权 token 和非确定性时间；位置、节点内容、顺序、边、当前媒体、模式与结果处理不能被过滤。

| 操作轨迹 | 必须对比的结果 |
| --- | --- |
| 平移 / 滚轮 / 触控 / 空格抓手 | 缩放锚点、速度、范围、视口结果和输入边界 |
| 单选 / 多选 / 框选 / 拖拽 / resize | 命中、阈值、增减选区、吸附、父子和最终几何 |
| 添加 / 连线 / 断线 / 复制 / 删除 / 分组 | 创建位置、端口、参考顺序、容器与层级、粘贴偏移 |
| 撤销 / 重做 / 快捷键 / 浮层 | 一次动作的逆操作、焦点恢复、输入框保护、菜单与模态优先级 |
| 保存 / 刷新 / 历史 / 导入导出 | 文档往返、媒体有效、版本语义、源格式与恢复结果 |
| 生成 / 批量 / 版本 / 重试 | 同样的步骤数、参数与确认、落位、结果选择、部分失败和重复事件 |
| 媒体 / 绘图 / 时间线 / 导演 | 参数、预览、播放、加工结果、进度 / 取消、输出与持久化 |
| 助手 / 工作流 | 工具 schema、事件、直接改图、付费提议、确认、会话恢复与撤销 |

每个实际启用的菜单命令、节点工具、对话框动作和助手工具至少有正常路径，并对其关键取消 / 错误 / 恢复路径建用例；每个源禁用项验证其相同不可用状态。记录清单字段：功能 ID、源位置、入口 / 模式 / 前置条件、默认值、输入轨迹、期待变化、视觉基准、目标适配点、自动结果、真实运行结果及未解决差异。

确定性媒体工具使用真实小样本对比输出尺寸、时长、帧率、音轨、字幕、画面与关键帧；固定编码环境时进一步比较解码结果。生成模型和助手的随机内容不要求相同，但对应参数、调用能力、步骤、返回结构及结果应用必须一致。付费供应商实测独立记录，不能靠 mock 通过补齐功能清单。

### 14.4 实施时执行命令

代码实施时先执行相关测试，再按变更范围执行项目规定检查：

~~~powershell
# backend 工作目录
uv run pytest tests/unit tests/api -q
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run python scripts/run_integration.py

# frontend 工作目录
npm test
npm run build
npm run test:e2e

# frontend/canvas 工作目录
npm test
npm run build
npm run test:e2e
~~~

宿主统一 build 已含两包类型检查，成功后不重复 typecheck；子包 build 仍可独立执行。标准前端 Playwright 沿用隔离端口 4175；源画布、目标画布和对照测试使用单独配置、隔离端口及数据，避免串扰。API 测试替身通过表示浏览器合同通过。MySQL 使用随机临时测试库；跳过不算通过。实际已执行的验证以实施记录为准，本文命令清单不构成通过证明。

RabbitMQ、MinIO、重启恢复和 Worker 测试按项目现有分层在隔离环境显式启用。真实模型验收另行记录所选配置、调用种类、输入版本、请求 / 实际参数、任务状态、实际文件与归档结果，不能以测试替身代替供应商验收。

### 14.5 性能验收目标

先固定参考机器、浏览器版本、分辨率与媒体样本，再报告测量值。建议基准为桌面 Chromium、1440×900、100 / 1,000 / 5,000 节点，混合文字、缩略图、视频和分组。

首版目标：

- 1,000 节点场景中，拖拽与缩放持续流畅，记录 p95 帧耗时，目标不超过 32 ms。
- 页面只加载可见 / 激活媒体；节点数增加不能导致所有视频同时解码。
- 连续拖拽只产生有限的命令提交，不随 pointermove 数量线性增加网络请求。
- 两种模式首屏分别测量；标准模式不下载 Three、Excalidraw、MediaPipe。
- 重复进入 / 离开画布后验证事件、图形实例、视频、Object URL 和 Worker 已释放，无持续内存增长。
- 大画布保存、快照生成与批次列表分页分别测 p95、文档大小和数据库查询次数。
- 源与目标在同机同场景分别测量拖拽 / 缩放、节点打开、播放、时间线和导演交互；记录相对退化。不能以新增网络保存为由接受明显的指针响应或播放退化，具体预算在 M0 实测后冻结。

这些是建议验收目标，不是本次静态阅读测得的结果。5,000 节点场景用来发现容量问题，超限策略和服务器限额依据实测确定。

## 15. 数据库升级、发布与回退

当前完整 SQL 为 52 张表的工作区版本；新功能按阶段增量升级，不重建现有业务库。

每阶段数据库变更必须三步同时完成：

1. 修改 ORM、Pydantic 和关联数据 / 权限代码。
2. 在明确确认的当前使用数据库中实际执行经过预检的 DDL 与必要回填，记录库名和执行语句。
3. 更新 schema.mysql8.sql、旧库增量迁移 README、MySQL 数据说明和相关接口文档。

首批迁移至少包含 workspace_mode 默认与 CHECK、画布基础表、历史与引用、用户状态、写回执；之后各阶段增加其已定义实体。旧项目一律回填 standard，既有分集、素材、媒体和版本数据不转换。

按 [迁移执行边界](../数据库模型/migrations/README.md) 预检表定义、备份与数据量，暂停相关写入，执行 DDL、回填和严格核验，再部署同版 API / Worker。MySQL DDL 会隐式提交，迁移中断要核对已执行状态后安全重入，不能声称整体事务回滚。

导入 ORM 后，当前 readiness 会将新表纳入所需结构；每阶段先完成实际迁移再发布该阶段代码。不得为了继续启动而跳过缺表检查或修改 Agent 默认值。

可以增加独立的 CANVAS_ENABLED、CANVAS_ADVANCED_MEDIA_ENABLED、CANVAS_AGENT_ENABLED 等功能开关及版本化能力接口，但关闭开关不应删除数据。标准项目仍可正常访问；画布项目在暂时不可编辑时提供只读 / 导出及明确说明，不重解释为标准项目。

回退优先关闭新增执行入口、暂停相关 Worker，保留 schema 和数据。已经写入新格式时使用兼容补丁向前修复；旧二进制若无法理解新记录，不能仅回退代码继续写入。发布前明确可回退的版本范围。

主要风险与处置：

| 风险 | 处置与验收门禁 |
| --- | --- |
| 复制源 Store 导致私人参数或过程进入共享图 | 分离四种状态和作者投影；多人 MySQL 隐私测试覆盖读取、保存、历史、导出、封面和审计 |
| 全量保存或旧回调覆盖新输入 | 命令批次、串行队列、编辑 epoch、稳定回执和 409 草稿；节点删除后回调不复活 |
| 目标主题或旧依赖改变源布局 / 操作 | 保留源独立构建与依赖锁；不混用 React root、AntD 和全局 CSS，M0 验证同源入口与真实代表节点 |
| 开发中节点或供应商不支持的工具被误标为完成 | 节点逐项清单、运行能力合同、异常用例和所选供应商真实文件验收 |
| 时间线前端搬入但遗漏源 Go 计划编译 | Python 移植源 editing.Compile 与 canonical plan；浏览器执行源降低与渲染算法，真实样片核对帧、时长、声音与字幕 |
| 快照、草稿和媒体数量持续增长 | 明确大小 / 保留期 / 分页 / 配额，先保留引用再清理，容量用实测修订 |
| 新 Agent 域破坏现有分集授权 | 保留旧域与 scope_version 分支，单独迁移并覆盖既有分集工具与候选回归 |
| Python Agent 的默认流程取代源助手行为 | 独立画布兼容策略，逐项对照工具、事件、直接改图、提议、取消和撤销 |
| 本地工具 / 插件缺少运行依赖 | 源实际可达入口、协议、文件能力与环境前置条件逐项登记并落实 Python / 受控外部执行端；不能只保留 UI |
| 全图高相似度掩盖局部缺失 | 分区域截图、DOM 几何、控件清单与操作回放四者共同门禁；未解决差异不得发布为一比一 |

## 16. 依赖与许可证

画布独立包按源实际解析版本迁入依赖闭包，不用目标旧版组件替代。依赖存在与功能动态加载是两件事：M0 可先锁齐需要的版本，各阶段只加载当前工具，标准入口不加载画布包。

| 阶段 | 依赖范围 | 约束 |
| --- | --- | --- |
| M0–M1 | 源 React / React DOM、Router、Ant Design 与相关组件、Tailwind / Motion、图标、字体、Zustand、Leafer、IndexedDB 等 | 画布独立运行，保留源 Provider、CSS 与精确版本；统一复用目标 HTTP 客户端，Node / 构建工具要求实际核对 |
| M3 | 源 Excalidraw、Tiptap、Markdown、代码编辑、ZIP、图表与实际节点渲染依赖 | 保留源渲染器及插件注册，不另选近似库；按源动态加载边界迁入 |
| M3–M4 | 源 @ffmpeg/ffmpeg、@ffmpeg/core、@ffmpeg/util、Three / R3F / Drei、MediaPipe 等 | wasm / Worker / 模型 / 字体与 JS 版本配套；保留完整媒体构建及资源路径 |
| 后端 | 现有 Celery、MinIO、httpx、SQLAlchemy、必要的媒体探测 / 原生 FFmpeg | 移植源计划 / 服务逻辑；ASR、3D 或协议处理确需依赖时新增并同步 uv.lock，不引入源 Go / pi Node 必需宿主 |

源项目使用 MIT，迁入实质代码需要保留 LICENSE / NOTICE 的版权与许可声明，并为迁入文件记录来源与源 commit。第三方素材和依赖按照源 THIRD_PARTY_NOTICES.md 单独处理。

源 @ffmpeg/core 声明 GPL-2.0-or-later。本版为保持源浏览器媒体功能，需要保留其 wasm 执行并落实实际分发构建对应的通知、许可与源码提供义务；不能再通过默认切换服务器执行规避迁移设计。Python 使用的原生 FFmpeg 二进制及编码器同样按实际构建许可核对。

保留当前 package-lock.json / uv.lock；独立画布包生成可重复的 npm 锁文件，源 Bun 锁作为版本核对依据。不提交 node_modules、下载缓存和构建产物。仅迁入画布实际需要的字体、图标、皮肤、模型和静态资产；源工作区内可见元素以基准为准，画布以外的整站页面和安装器不搬入。

## 17. 主要改动文件与维护文档

| 范围 | 既有或新增文件 / 目录 | 修改目的 |
| --- | --- | --- |
| 创建与项目 DTO | frontend/src/pages/projects/ProjectsPage.tsx；src/api/modules/projects.ts；src/types/projects.ts | 模式选择、字段映射和创建恢复 |
| 入口与布局 | frontend/src/app/App.tsx；paths.ts；pages/projects/ProjectRoute.tsx；pages/canvas/CanvasProjectRoute.tsx | 服务端模式分流、同源画布独立入口与返回；标准布局不混入画布 |
| 项目列表 | frontend/src/features/projects/ProjectCard.tsx；ProjectList.tsx | 模式标签、画布数及正确入口 |
| 新画布模块 | frontend/canvas/；frontend/src/api/modules/canvases.ts 与 types | 源完整 React 画布、依赖锁、样式资产、编辑会话与服务兼容层 |
| 构建与路由部署 | frontend/package.json、scripts/、vite.config.ts；frontend/canvas/vite.config.ts；实际反向代理 / 部署配置 | 统一启动构建与预览、同源独立 HTML、静态资源、Worker / wasm、刷新 / 直达、API 与认证 |
| 项目创建契约 | backend/src/short_drama/schemas/project.py；project_creation.py；domain/project.py；service/project_service.py；dao/project_dao.py | 默认 standard、无限画布原子创建、摘要聚合 |
| 路由与模型注册 | backend/src/short_drama/api/dependencies.py；api/v1/router.py；domain/__init__.py | 注册真实 Service 与新实体 |
| 数据权限与就绪检查 | backend/src/short_drama/db/access.py；readiness.py | 共享 / 私有显式分支、写入防护与严格迁移核验 |
| 生成 | schemas/ai_generation.py；service/ai_generation_service.py；generation_context_service.py；generation_business_service.py；generation_batch_service.py；新增 canvas_result_service.py | canvas 来源、源结果物化、版本、批次场景与恢复；保留标准候选流程 |
| 媒体和清理 | service/media_file_service.py；media_recycle_bin_service.py；storage_service.py；tasks/render.py；service/video_render.py | 画布引用保护、受控加工与资源生命周期 |
| 时间线计划 | 新增 schemas/canvas_timeline.py；service/canvas_timeline_plan_service.py 与媒体任务相关模块 | 移植源 editing 类型、canonical plan、编译 / 校验与源服务能力 |
| Agent，M5 | domain/agent.py；schemas/agent*.py；service/agent_*；新增 canvas_assistant_service.py；agent/authorization.py、tools.py、runtime.py、state.py | 新画布域、兼容工具 / 事件 / 直接改图 / 提议、上下文与授权，保留标准 Agent |
| 数据库与 API 文档 | docs/数据库模型/schema.mysql8.sql；migrations/；MySQL8数据表设计.md；docs/api/；frontend/src/api/README.md | 完整 SQL、增量迁移、真实合同与运维说明 |
| 产品、设计与协作约定 | frontend/PRODUCT.md；DESIGN.md；AGENTS.md；frontend/canvas 的维护说明 | 双模式范围，画布源设计规范、源回填例外和共享语义；仅在实施相应功能时同步 |
| 验证 | backend/tests/unit、api、integration；frontend/tests、e2e；frontend/canvas/tests、e2e 与对照 / 性能配置 | 关键业务、源基准、成对回放、真实功能、恢复、隐私与标准回归 |

实施会超过 3 个文件且跨 API / Service / DAO / ORM / DTO，因此每阶段开始前应列出实际文件和修改理由，再动手。新增通用辅助能力仅在真实重复或明确协议边界出现时抽取。

## 18. 已确定的默认设计与后续决策

本方案以以下默认设计继续：

- 项目模式创建后固定；跨模式迁移通过显式复制作品。
- 主画布创建为空，不创建伪分集、示例媒体或示例任务。
- 首版采用共享作品 + 本人创作过程，409 冲突需要明确处理。
- 无限画布独立构建，保留源工作区布局、主题、字体、组件、动效、快捷键与工具可用性；标准模式保持当前工作台。
- 无限画布保留源直接回填、版本选择与助手执行 / 提议流程，不插入标准候选采用步骤。
- 保留源浏览器加工 / 渲染及对应 wasm、Worker、资源；Python 移植实际服务和 canonical plan，不默认改用原生 Worker 替代。
- 时间线保持源毫秒精度、导出选项及导演 24 / 25 / 30 fps，不统一为 30 fps。
- 源已启用画布工作流、内置插件与导入导出包含在最终验收；历史数据批量搬运、新生态与实时协同后续交付。
- M0–M4 为阶段交付，M5 全量对照通过后才能称完整一比一；容量限额和性能预算以源 / 目标实测确定。

M0 还需实际核对源运行依赖和环境：模型供应商及编辑协议、ASR / whisper.cpp 可用部署、启用的本地 / 外部工作流执行端、3D 模型资产和上限、源导出规格、历史资源覆盖与参考机器。需要本地文件或桌面能力的画布功能，须明确浏览器授权、Python 本机能力或受控运行端的对应实现，不能仅剔除安装器便连带删功能。

这些事实影响真实功能能否一比一运行；静态源码不能替代该验收。任何源启用功能无法复现时，记录原因、目标补齐方法和当前未完成状态；涉及可见行为差异的取舍必须由用户明确接受，不能默认视为例外。

## 19. 可追溯源码阅读清单

下列源路径均相对 D:/code/BeefTV；目标路径相对当前仓库。此清单记录本次重点阅读的调用链与测试，未声称逐行审阅仓库所有文件。

| 领域 | 重点依据 |
| --- | --- |
| 源基线与依赖 | AGENTS.md；LICENSE；NOTICE；THIRD_PARTY_NOTICES.md；web/package.json；backend/go.mod；agent-host/package.json |
| 入口与页面 | web/src/router.tsx；pages/canvas/project.tsx；canvas-project-world-layers.tsx；use-canvas-render-model.ts |
| 源视觉与浮层 | web/src/main.tsx、application.tsx；styles/globals.css、beeftv-local-overrides.css；components/layout/app-providers.tsx；lib/canvas-theme.ts；stores/canvas/use-canvas-theme-store.ts；components/canvas/canvas-toolbar.tsx、canvas-overlay-layer.tsx；pages/canvas/canvas-refresh-shell.tsx |
| 引擎与性能 | components/canvas/infinite-canvas.tsx；canvas-leafer-graphics-layer.tsx；lib/canvas/canvas-spatial-index.ts；canvas-selection.ts 相关调用；test/canvas-spatial-index.test.ts；canvas-media-performance.test.ts |
| 节点与创建 | web/src/types/canvas.ts；lib/canvas/node-registry/；canvas-feature-availability.ts；tool-registry/definitions/add-node-menu-tools.tsx；test/canvas-feature-availability.test.ts |
| 项目与保存 | stores/canvas/use-canvas-store.ts；lib/canvas/canvas-workspace-project.ts；services/workspace-project-repository.ts；workspace-mode.ts；canvas-operation-journal.ts；canvas-revision-conflict.ts；pages/canvas/use-canvas-project-lifecycle.ts |
| 保存差异与测试 | lib/canvas/canvas-patch-merge.ts；test/canvas-document-field-rule.test.ts；canvas-document-rebase.test.ts |
| 后端保存与历史 | backend/internal/canvas/user_data.go；document_commit.go；canvas_history.go；model/models_canvas_history.go；document_commit_test.go |
| 生成与媒体语义 | services/api/generation-task.ts；pages/canvas/canvas-generation-orchestration.ts；lib/canvas/canvas-generation-task-sync.ts；canvas-node-semantics.ts；canvas-connection-policy.ts；canvas-media-persist.ts |
| 绘图、视频与导出 | canvas-drawing-storage.ts；canvas-drawing-reference.ts；canvas-video-merge.ts；canvas-ffmpeg-session.ts；canvas-video-timeline-segments.ts；canvas-export.ts；types/timeline.ts |
| 时间线计划与执行 | web/src/services/api/timeline-tasks.ts；web/src/lib/timeline/timeline-export.ts；backend/internal/handler/routes.go、timeline_plan_test.go；backend/internal/app/task_timeline.go；backend/internal/editing/compile.go、plan.go、compile_test.go |
| HTML / SVG 隔离 | web/src/components/canvas/nodes/html-node.tsx、sandboxed-frame.tsx |
| 视觉对照工具 | web/scripts/compare-canvas-screenshots.mjs；源脚本只输出指标，未运行截图对照 |
| 导演与助手 | types/director.ts；pages/canvas/use-canvas-assistant.ts；services/api/agent-assistant.ts；backend/internal/canvas/capability/builtin.go |
| 源文档与代码差异 | docs/content/docs/overview/features.mdx；实际功能可用性与助手源码 |
| 目标项目链路 | frontend/src/pages/projects/ProjectsPage.tsx、ProjectRoute.tsx；app/App.tsx、paths.ts；api/modules/projects.ts；backend 的 project 契约、ORM、Service 与路由 |
| 目标权限与任务 | backend/src/short_drama/db/access.py、readiness.py；domain/async_task.py、ai_generation_record.py、media_file.py、generation_batch.py；service/ai_generation_service.py、generation_archive.py |
| 目标 Agent 与成片 | domain/agent.py、episode_assembly.py；schemas/agent.py、agent_context.py、episode_assembly.py；service/agent_conversation_service.py、episode_assembly_service.py；tasks/render.py；service/video_render.py |
| 目标测试与维护 | tests/unit/test_project_creation.py；integration/test_private_candidate_boundaries.py；frontend/e2e/project-redesign.spec.ts；PRODUCT.md；DESIGN.md；数据库迁移 README |

本次交付为方案文档，并根据用户的一比一要求修订其迁移边界、前端隔离、数据库、结果回填、媒体、助手及验收设计。未修改业务代码、AGENTS.md、其他已有未提交改动或任何数据库；未安装依赖、启动服务、运行模型请求，也未执行测试 / 构建 / DDL 或实际截图对照。文档核对包含本地链接、示例 JSON、格式、已引用关键源码路径、模式规则与一致性条款；运行一致性尚未验证。
