# AI 短剧工作台／BeefTV 无限画布功能进度交接清单

> 整理日期：2026-10-12。功能与验收终态快照：北京时间 16:56 左右。
> 用途：接手人读完本文件即可确定现状、找到代码、了解调用授权并开始下一步；链接用于追溯证据。
> 整体状态：**仍在实施，尚未达到完整一比一迁移的完成条件。**
> 本次整理仅新增本文件，没有执行生成请求、扩额、部署、DDL、测试或修改产品代码。

## 目录

1. [接手先读：目标、边界与当前结论](#overview)
2. [已编写功能清单：逐功能及子功能](#implemented)
3. [未完成清单：逐项关闭条件](#unfinished)
4. [最新验收检查点与失败复现](#checkpoints)
5. [新增、修改及删除文件范围](#files)
6. [接下来应完成的工作顺序](#next)
7. [模型调用指令、授权与额度规则](#models)
8. [环境、启动与验证命令](#commands)
9. [用户规则指令汇总](#rules)
10. [交接完成检查表与证据索引](#evidence)

<a id="overview"></a>

## 1. 接手先读：目标、边界与当前结论

### 1.1 项目与固定来源

| 项目 | 当前约定 |
| --- | --- |
| 工作区 | `C:\Users\snow\code\ai-short-drama` |
| BeefTV 原项目 | `C:\Users\snow\code\githubProject\BeefTV` |
| 固定来源 commit | `4ca2a65a7780a8dfcaaa86c33679f84fb04e055c` |
| 另一个固定视觉来源 | 工作区 `.runtime/beeftv-visual-source`，验证时保留原 HEAD 与 clean 状态 |
| 标准前端 | `frontend/`，React 19、TypeScript strict、Vite 7、Ant Design |
| 无限画布前端 | `frontend/canvas/`，独立子包、HTML、React root、Provider、CSS、字体、锁文件 |
| 后端与业务事实 | Python 3.12+、FastAPI、Pydantic 2、SQLAlchemy 2；MySQL 与 MinIO 为业务事实来源 |
| 异步执行 | RabbitMQ、Celery、Publisher、Recovery、各角色 Worker；仅启动 API 不执行生成 |
| 迁移总目标 | 固定 BeefTV 版本中仍在范围内的布局、操作、媒体、生成及交互逐项一比一对照通过 |

### 1.2 用户已确认的需求调整

1. 标准模式保留小说／剧本、素材、分镜、成片等原编辑与生成路径。
2. 项目创建支持标准模式与无限画布模式；不创建伪分集来接入画布。
3. 只保留 BeefTV 画布编辑器。源首页、项目库、整站资产／设置／任务管理、独立创作页、外部 Agent 等整站页面已按授权移除；对应能力进入宿主工作台，不应重新迁回这些页面。
4. 两包共同声明、版本不同的直接依赖使用宿主版本；源独有依赖仍独立锁定。不得为画布升级标准前端依赖；依赖对齐后的视觉与行为需要重新证明。
5. **共用 AI 创作助手已改为本人项目下的纯问答与建议。** 标准与画布共用一套面板及 Python 运行机制，工具为空、执行预算为零。旧画布执行型助手、直接改图与付费生成提议需求已被替代，不能列为继续补做的功能。
6. 普通画布节点生成与工具回填仍沿固定源直接应用／提议确认流程；不插入标准模式的“候选采用”步骤。标准模式继续保留候选与明确采用规则。
7. 源已隐藏、禁用或不可达的入口保持原状态；不能为测试方便解锁。
8. 历史数据批量搬运、新插件生态、多人实时协同、后续原生媒体优化属于范围外扩展，不混入本次必交清单。

### 1.3 状态如何阅读

| 标记 | 含义 |
| --- | --- |
| 已编写 | 当前产品代码已有实现，不代表全部环境和异常已验收 |
| 专项通过 | 明确列出的测试／真实运行场景通过；不外推其他参数、协议、宽度或版本 |
| 真实部分通过 | 已实际调用供应商并达到列出的结果，但还有完整后置或内容门槛未通过 |
| 未完成 | 尚未实现，或实现已有但缺少完成条件所需证据 |
| 候选未应用 | 仅隔离候选、实验或测试包；不能当作当前产品行为 |
| 暂缓／替代／范围外 | 分别表示暂不验收、用户改变需求、当前无需交付；不得写成已完成 |

**当前重点：** ModelHub 新凭据有效，两视频已经生成；分镜四媒体整体验收仍未过。R19 新图片上传不可用恢复单场通过。R22 两版完整性能矩阵均未达到 32ms；viewport 候选未应用。R23 可见行为候选仍未经批准，完整源视觉门禁未关闭。

### 1.4 M0–M5 总阶段

| 阶段 | 已有范围 | 尚未闭合 |
| --- | --- | --- |
| M0 基线与兼容 | 固定来源、来源哈希、独立构建、基础成对对照、部分可达性审计 | 完整主题／默认值／键鼠触控／动效／媒体对照，严格视觉差异与全量基线 |
| M1 模式与持久化 | 双模式、图／个人投影、CAS、回执、资源、历史、回收切片 | 生命周期全矩阵、全部子文档／并发、完整部署与性能 |
| M2 生成 | 模型配置、协议适配、私人任务、四类生成合同与多种恢复切片 | 全协议／引用／批次／取消矩阵，未完成真实供应商范围 |
| M3 创作工具 | 图片、绘图、Skill、文档、分镜、角色相关适配 | 图片内容失败项、完整角色接线／回填、四媒体完整验收及成对异常矩阵 |
| M4 媒体与导演 | 浏览器 FFmpeg、时间线、深度、导演／全景／调色多个运行切片 | 真实 ASR、完整质量／长视频／平台矩阵、异常及完整源对照 |
| M5 助手与最终验收 | 共用纯问答助手、工作流、归档等切片 | 同版用户部署、Responses 图片成功、完整导入导出与 R24 总验收 |

<a id="implemented"></a>

## 2. 已编写功能清单：逐功能及子功能

本节按功能组织，R01–R24 是既有台账的索引。下列“已编写”与“通过范围”必须一起阅读。

### F01. 项目模式、入口与独立画布基础（M0／M1）

- **F01.1 项目模式：** `workspace_mode` 区分 `standard`／`infinite_canvas`，旧请求默认标准模式，项目创建后模式固定。
- **F01.2 无限画布首次创建：** 同事务创建项目、空主画布、主画布设置及本人幂等回执，失败回滚，原键恢复避免重复创建。
- **F01.3 画布路由：** `/canvas-app/canvas/:source_key`；项目、素材、模型配置、任务管理进入宿主入口，旧深链兼容转向。
- **F01.4 独立前端：** 原 React root、Provider、字体、主题和媒体依赖保留；宿主统一安装、开发、构建、预览并组合 `dist/canvas-app/`。
- **F01.5 来源跟踪：** 固定来源清单、适配理由、删除记录及第三方图标清单；重导入不得覆盖现有适配或复活移除页面。
- **F01.6 基础操作：** 节点、分组、连线、选择、右键、拖拽、撤销／重做、视口与外观保留源交互。基础几何／像素切片已验，完整可达清单未验完。

### F02. 文档保存、个人状态与历史（R20／R21）

- **F02.1 关系持久化：** 画布节点／边、类型化子文档及资源引用进入 Python／MySQL。
- **F02.2 共享与私人投影：** 共享作品与本人提示词、参数、模型选择、任务、生成历史分离；嵌套条目以稳定 ID 匹配，拒绝歧义映射。
- **F02.3 串行保存：** 来源保存屏障、版本 CAS、幂等写回执；409 保留本机草稿并等待明确处理。
- **F02.4 个人视口：** 独立版本与字段比较，位置更新不推进共享图版本；旧图提交不覆盖新位置。
- **F02.5 外观偏好：** 深浅／自定义主题、点／线／空白背景、图片信息独立保存与刷新恢复。
- **F02.6 历史：** 不可变快照、作者私人历史扩展、恢复、引用保护；历史／回执不被后续修改覆盖。
- **F02.7 多窗口与撤权：** 首次个人状态写入竞争、SSE 重认证、成员撤权停止继续写入、旧响应不重新打开旧编辑面板已有修复与专项。
- **F02.8 保存反馈：** 原顶栏状态与弹层显示保存进度、失败与已保存；源隐藏 Toast 的全部可见反馈仍需逐项对照。
- **通过范围：** 嵌套投影三集合×重排／删除六场，以及新增来源409／未 ACK 草稿单场等切片通过；不代表所有子文档和并发组合。

### F03. 资源上传、素材库、复制与回收（R19／R20）

- **F03.1 上传：** multipart、8MiB 分片、预留稳定身份、SHA-256、完成回执、实际尺寸／时长探测。
- **F03.2 文件读取：** 本人／项目范围鉴权、GET／HEAD／Range、永久资源身份；临时 URL 只作展示。
- **F03.3 托管存储：** 资源固定写入 Python／MySQL／MinIO，IndexedDB 仅作缓存和草稿。
- **F03.4 复制归一化：** 同项目直用、本人或跨项目文件按稳定目标身份复制，保存前归一化，原来源链本人可见。
- **F03.5 素材库：** 登记、分页／批量读取、收藏、生成筛选、分类创建／移动／改名／删除；删除分类保留素材。
- **F03.6 素材绑定：** 节点／时间线／导演等资源引用、`assetId` 私人边界、实际副本保护；使用中删除拒绝。
- **F03.7 六类素材合同：** 源解析器和 Python 共用有效／无效样本，尺寸、时长、MIME、字节数、地址与模型元数据严格验证。
- **F03.8 回收站：** 归档、受限预览、显式恢复、永久处置、删除回执与对象存储重试；旧上传键不能复活资源。
- **F03.9 生命周期：** 创建暂存、本人执行载荷、无上传身份生成媒体、历史创建资源、整图未知删除与私人草稿已有回收切片。
- **F03.10 故障恢复：** 原 File／原请求／原键保留，刷新后显式保存重传；音视频16场分批及新图片1440单场通过。
- **F03.11 普通 HEVC：** 本机可信 H.265 样片原上传、两宽度播放、cookies-only 恢复及字节／权限后置通过；不外推其他编码和浏览器。

### F04. 模型目录、凭据与协议（R03／R04／R07／R08）

- **F04.1 统一模型配置：** 宿主 `/ai_config` 管理本人模型，画布只读目录和运行 profile；偏好 CAS、旧模型别名恢复、失效反馈。
- **F04.2 服务端凭据：** 加密信封与扩展鉴权；浏览器不获得密钥，普通 CRUD 不伪造托管凭据。
- **F04.3 独立模型测试：** `/canvas-runtime/model-tests`，无需伪节点；稳定操作键、私人结果与未知回执重放。
- **F04.4 Chat／Responses：** 文本及声明能力下的多模态适配；图片／视频引用分组保序，系统提示词和 reasoning 私人隔离。
- **F04.5 Claude 普通文本：** `claude_messages.v1`，`/v1/messages`、`x-api-key`、版本头、流／非流解码与 proxy 路径合同；宿主与原 UI 恢复切片已验。
- **F04.6 Gemini 普通文本：** `gemini_generate_content.v1`、非流 `generateContent`、`x-goog-api-key`、模型与路径编码；源 custom gate 保持，hosted 等效路径切片已验。
- **F04.7 Agnes：** 免费文本、图片、视频正式网关与执行观测已接入，并有各类真实切片。
- **F04.8 ModelHub：** `modelhub_video.v1`／`minimax-h3` 适配、5–15整秒／480p／比例／模式参数准入与轮询；60项单测通过，新凭据真实鉴权已验证。
- **F04.9 视频默认值修复：** 未声明视频契约时保留工作区480；只发送模型支持的声音／水印开关，Ark素材开关仅随声明的 Ark 协议发送；四文件已应用并完成画布524项、source检查及两包构建。
- **F04.10 企业连接：** BeefAPI 连接／状态／钱包等宿主入口及后台授权机制已有实现；真实企业账号验收暂缓。

### F05. 生成任务、引用与异步恢复（R01／R05／R07）

- **F05.1 四类任务合同：** 文字、图片、视频、音频准入，可信模型／来源版本冻结、私人的 task binding／result。
- **F05.2 幂等：** 稳定 `clientOperationId`、`Idempotency-Key`、请求摘要，未知状态不自动重新提交。
- **F05.3 正式异步栈：** 原 Publisher／Recovery、真实 RabbitMQ、独立 Worker、租约、取消／恢复许可、归档与幂等结果回填。
- **F05.4 输入引用：** 自动连线引用、显式 `@` 图片／视频、反序／去重／缺失拒绝、已保存绘图 render 引用；Chat／Responses双协议切片已验。
- **F05.5 多份／批次：** 文本1–15份独立任务、批次索引与独立操作键、逐项结果恢复；完整跨协议／批次矩阵仍未闭合。
- **F05.6 展示语义：** 普通文字节点保持源 loading 至终态再回填；私人 SSE／增量恢复实现不等于普通节点逐字展示。
- **F05.7 真实正常链：** Agnes文字、文生图、图生图×1440／390六场正式队列、字节／权限／恢复后置通过。
- **F05.8 真实视频切片：** Agnes桌面 j 与390无印刷输入 r 各通过技术后置及有限内容查看，输入不同；旧内容失败和队列拒绝保持。
- **F05.9 消息重复保护：** 多个专项已验 duplicate／stale AMQP 不产生第二次供应商调用；AN 分镜四媒体整场的这项尚未到达。

### F06. 图片本地工具与 AI 编辑工具（R02／R06／R09）

- **F06.1 裁切：** 原选区、取消、派生图片、永久保存、刷新与两处503恢复；真实尺寸沿源导出算法。
- **F06.2 宫格切图：** 原网格、像素派生、逐资源归档与恢复；正常路径及六类异常矩阵已有专项。
- **F06.3 标注：** 原笔画／标注、成图持久化、刷新；upload／commit失败、409、导航、换账号、来源删除六场异常通过。
- **F06.4 本地插值放大：** 原入口、失败草稿、源像素切片对照。
- **F06.5 局部重绘：** 已保存蒙版与参考冻结、独立 Worker恢复、永久回填与防重复；完整真实协议／视觉矩阵尚未闭合。
- **F06.6 多角度：** 原设置、单项生成与恢复；真实Agnes两宽度观察到视点变化、色材保持，精确数值相机参数未证明。
- **F06.7 打光：** 原idle节点、参数、恢复；蓝礼盒逆光两宽度真实内容及技术后置通过，旧增人失败保留。
- **F06.8 表情：** 原手工表情、局部合成与恢复代码已写；E／F／H技术通过，朝向／抬眼改变导致内容失败，见未完成项。
- **F06.9 肤质／纹理：** 默认五参数、源隐藏入口与参数状态、单张／批次结构；默认单张两宽度真实通过，桌面场经600入口创建后返回，不等于纯桌面入口通过。
- **F06.10 提示词与审美：** 固定编辑器工具、持久阶段批准／租约、九阶段审美UI、复制提示词与终态恢复切片；源隐藏栏保持。
- **F06.11 Skill：** 私人正文／版本快照、模板引用、编译提示词、空模板拒绝、同任务恢复与分享成品。真实snapshot／Chat／1440文本小场通过；未覆盖完整Skill对象冻结及其他入口。

### F07. 文档节点、批量表、绘图与分镜（R10／R11）

- **F07.1 文档节点：** Markdown、SVG、HTML、图表、对比等五类已有节点编辑／保存切片；源脚本菜单禁用状态保留。
- **F07.2 批量表：** 原表编辑、选择行、配置、批次提交及逐项恢复已有实现。
- **F07.3 分镜编辑：** 原镜头行字段、稳定行ID、选中／未选中保护、保存／共享／恢复。
- **F07.4 分镜文字生成：** 私人批准、可信来源／角色snapshot、结构化输出校验、同任务恢复；真实角色文本v5小场通过。
- **F07.5 分镜媒体：** 分镜图、动作板、直接／首帧视频，批次、预览、稳定回填与恢复；受控供应商8场两宽度通过，真实四媒体整体验收未通过。
- **F07.6 来源项目读取：** 正式后端／前端读取已应用，404反馈、409刷新、已保存草稿、空图与literal插入保护；1440／600正常可见入口两场真实浏览器通过，零模型调用。
- **F07.7 角色版本基础：** ORM、完整SQL与019增量结构已应用／执行，当前版本及媒体引用模型已有实现。
- **F07.8 角色目录／图片：** 基于可信 `source_project_id`、双项目权限、实际角色关联、current版本／媒体范围的catalog与stable resolver已应用；对象GET重新鉴权。
- **F07.9 RR当前读取：** 七文件锁内当前读取修复已应用，RR25案及正常七案回归有记录；后续完整既有媒体S→X并发等不能由这批推断。
- **F07.10 绘图：** Excalidraw笔画、版本化子文档、永久preview／render、空引用拒绝、双协议文字引用及cookies-only恢复；更多组合未闭合。

### F08. 音视频本地加工与转换（R12／R13）

- **F08.1 视频工具：** 原浏览器取帧／裁切／拆分／变速等四工具运行专项；保存预览竞争已有复现与修复。
- **F08.2 媒体组合：** 合并与SRT×两宽度的实际字节、探测、权限及恢复后置通过。
- **F08.3 音频合同：** 格式／音色／命名空间、异步轮询预算、原WAV/HTTP codec样本、私人任务与独立 Worker／AMQP恢复切片。
- **F08.4 ASR接入：** 私人本地转写接口与执行合同已有；实际ASR模型效果／字幕完整链未验。
- **F08.5 转换作品：** 灰度／Canny永久结果、稳定任务／资源身份、取消和迟到结果保护已有实现与专项。
- **F08.6 真实深度：** 固定SHA的VDA Small CPU、2帧／400ms样片，direct／cookies×1440／600四场真实推理与完整后置通过；不是长视频／质量或CUDA／MPS验收。
- **F08.7 中间结果：** 浏览器转换Blob归档及任务取消边界已有切片，未支持转换和源禁用入口保持原样。

### F09. 多轨时间线、编译与渲染（R14）

- **F09.1 多轨文档：** 稳定片段UUID、毫秒时基、共享时间线、预览与版本化资源引用。
- **F09.2 canonical编译：** Go源规则移植Python；视频／图片单一视觉序列，跨视觉轨重叠报错、间隙gap、混音、隐藏／静音、裁剪／淡入淡出／字幕等按源处理。
- **F09.3 固定源限制：** 可见text clip不支持导出，不擅自扩展成任意视觉叠加引擎。
- **F09.4 浏览器导出：** 源FFmpeg wasm／Worker、进度／lease／输出；两宽度×开发／组合生产预览四场实际导出及新片段归档通过。
- **F09.5 后端渲染：** 持久任务准入、独立AMQP、后台执行与固定归档；原生与Go／Python六组实际产物对照切片。
- **F09.6 恢复：** cookies-only重开、原字节与真实探测、共享作品／私人运行隔离；完整取消／异常未关闭。

### F10. 导演、3D、全景与调色（R15）

- **F10.1 导演场景：** 原工作台、人物／几何体／机位三轨、GLB／glTF受控二进制与保存。
- **F10.2 WebGL构图：** 截图、永久作品、镜头／布景与恢复；深度／法线查看专项。
- **F10.3 姿势／运动：** 演员轨迹、跟拍、骨骼重放幂等；内置GLB无动画Clip，不能称动态步态通过。
- **F10.4 预演／录制：** 原键盘、缩放、播放、移除／撤销、真实WebGL帧与10秒白膜视频录制、重开无重传。
- **F10.5 全景：** 原环视、四方向实际像素、永久作品与恢复。
- **F10.6 调色：** 原UI、私人永久参考、同任务恢复；两宽度技术后置切片。
- **通过范围：** 多轨预演、截图、白膜、全景与调色均有专项；未等于全部资产、平台、质量和源视觉通过。

### F11. RunningHub、工作流与插件（R08／R16）

- **F11.1 内置Workflow／App：** 选择、模型身份、服务端描述符、标量字段、执行、图片／视频／音频归档与恢复。
- **F11.2 声明式RunningHub：** 独立Bearer／GET视频合同，按源入口执行；本地参考内联，同源下载保留鉴权、跨源去除鉴权。
- **F11.3 参数字段：** 六类字段、键盘、空值／步长校验、生成前保存与仅cookies／fresh恢复。
- **F11.4 队列与并发：** 六独立AMQP及归档／任务列表确定性并发切片。
- **F11.5 可达性：** 六模块已审计；Eagle本地分支、隐藏优化栏、禁用转换菜单、未挂载剪辑插槽保持源状态。
- **通过范围：** Workflow／App×三媒体、字段恢复与unknown等受控供应商专项；不能当作真实付费RunningHub或企业授权验收。

### F12. 归档、导入与导出（R17／R19）

- **F12.1 ZIP导出：** 共享历史／本人草稿／成员历史冻结、五种媒体原字节与本人投影隔离。
- **F12.2 导出恢复：** 原UI、读取失败／重试、cookies-only云恢复，六场专项通过。
- **F12.3 有界外部导入：** URL／资源导入边界与永久身份归一化部分接入；完整重新导入和跨账号传递未验完。
- **F12.4 复制保护：** 捕获同一绘图版本的笔画／预览／成品，不组合不同当前版本；节点／素材副本仍受引用保护。

### F13. 标准／画布共用 AI 创作助手（R18）

- **F13.1 唯一公共UI：** `frontend/src/features/ai-assistant/`；画布仅停靠、宽度调整、窄屏抽屉和上下文薄适配。
- **F13.2 本人项目V2会话：** 可有多段对话，不绑定分集／阶段／对象／任务；复用Agent表、正式Worker、队列与游标SSE。
- **F13.3 纯回答边界：** 工具为空、预算零；Skill指导回答，不赋予操作能力。
- **F13.4 来源保存屏障：** 模型偏好及作品保存ACK后发送；409／unknown／权限或内容变化时保留草稿并停止发送。
- **F13.5 附件与引用：** 本人媒体、真实信息、稳定MediaFile／hash、Skill版本冻结；`include_document=false`不读取整图。
- **F13.6 草稿／恢复：** 账号和会话隔离、冻结未知发送原body／键、断线游标恢复；不以新消息替代恢复。
- **F13.7 旧历史：** 只读入口与旧许可保留，晚响应不能重开关闭的面板或拉回阶段；重命名120字上限已对齐。
- **F13.8 真实正常矩阵：** 标准／画布×Chat／Responses×1440／390八唯一组合分批通过正式AMQP链。
- **F13.9 真实生命周期：** 三模式×两端×两协议×两宽度24组合分批通过；响应后租约／离线重放不产生额外POST。
- **F13.10 真实图片Skill：** Chat四组合通过；Responses四组合最终拒绝UI／fresh恢复通过，**Responses图片成功尚未验到**。
- **F13.11 部署切片：** 助手专项曾更新API／scheduler／Agent三角色；不能当作当前全部八角色同版部署。

### F14. 性能、权限与回归基础（R21／R22／R23）

- **F14.1 数据审计：** Actor、所属项目／来源项目双权限、私人Task／Asset隔离、成员作品共享、撤权重检。
- **F14.2 数据库并发：** 上传／素材锁序、首次个人状态竞争、当前读取、保存／归档／引用竞争的确定性回归。
- **F14.3 大图数据优化：** 列表批量读取、引用差异写、未变边跳写、快照编译缓存及私人历史guard批量读取。
- **F14.4 私人历史读取实测：** 三档快照SQL各25，p95约117.6／215.8／1113.3ms；1000节点到期保存约2156.8ms，仅该数据与版本范围。
- **F14.5 已应用前端优化：** floating-dock有限重复写入减少，Zoom同值去重及单Text.data更新；组件行为和源登记已验。
- **F14.6 未应用性能实验：** viewport候选与panel-width实验；前者完整矩阵仍失败，后者mutation无收益。
- **F14.7 回归工具：** Node、source-check、两包build、原UI驱动、真实MySQL／MinIO／AMQP与固定源成对基准。
- **边界：** 前端组件／数据查询改善不代表GPU呈现、全页视觉、泄漏或正式32ms达标。

<a id="unfinished"></a>

## 3. 未完成清单：逐项关闭条件

### U01. 分镜真实四媒体完整验收（最高优先级，R10）

- [ ] 核对ModelHub `minimax-h3` 最新官方比例／480p／模式合同，查明两视频1038×576来源；原16:9容差不变。
- [ ] 恢复图片→视频顺序，或使用源正常升层交互，使成员可以真实点击视频播放；不force click、不改共享坐标。
- [ ] 新独占轮完成两图＋两视频的原Table操作、来源保存、原／fresh cookies恢复、重复不新增与稳定绑定。
- [ ] 成员实际播放两视频并解码两图片，同时维持私人Task／Asset不可见、外人资源拒绝。
- [ ] 实际执行Python `check_graph`／`check_stage`、MySQL记录／结果／绑定、MinIO与供应商原字节SHA对应、精确尺寸／时长与输出元数据。
- [ ] 完成HTTP／cache／HEAD／Range后置、duplicate／stale AMQP无第二POST、最终图与媒体不变性及acceptance。
- [ ] 对原视频连续观看和必要帧／音频复核，验证固定镜头及缓慢转动；现有九点静帧不能证明全部内容。
- [ ] 记录自然终态、原失败／警告／JUnit实际数值和精确资源清理范围；不重跑冻结AN包、不修改旧失败结果。
- [ ] 补其余两宽度、动作板／首帧／角色版本／异常及完整成对对照。单个新轮通过也不等于R10整体完成。

### U02. 新增付费视频次数的预算落地（调用前置）

- [ ] 审查已准备的扩额候选，确认旧授权、原policy及四条reservation完整保留。
- [ ] 在唯一真实账本中以事务记录新增五次授权；只扩次数，暂不扩累计声明秒数。
- [ ] 核对扩额实际收据后绑定新预算观察器，不能改冻结AN或直接绕过门禁。
- [ ] 首轮最多两次各5秒，已有20秒＋本轮10秒，累计声明≤30秒；任何失败／unknown都继续计数。
- [ ] 每次调用前重新读取最新账本和实际授权，不依据本文的静态剩余额度直接调用。

### U03. 角色、来源与版本发布剩余项（R10）

- [ ] 完成尚未正式应用的角色菜单／UI接线与共享发布后续方案；候选通过不自动成为产品实现。
- [ ] 当前目录／图片resolver只支持current；pinned、声音、角色媒体生成引用及所需冻结规则分别补验。
- [ ] 完成完整既有Media共享锁→排他锁、cached identity刷新、dirty publication及真实调用链并发；不由RR25案或正常七案推断全覆盖。
- [ ] 旧角色backfill／可信来源映射须走维护流程：停旧写者、核无active／unknown、显式迁移、数据verify、同版部署；不补造项目关联。
- [ ] 019已做结构、未做数据回填；不能重复执行已存在DDL或写成角色完整升级结束。
- [ ] 验证有质量的真实角色图片。37×19颜色夹具只能证明身份与字节链。
- [ ] 可信公网HTTPS首帧实际供应商取图与完整首帧生成待验；不得自行建公网隧道。

### U04. 图片工具内容与异常矩阵（R06／R09／R11）

- [ ] 表情：保持原侧向偏下脸和视线，解决欣喜若狂结果转正面／抬眼的内容失败，再独立验证。
- [ ] 表情快速反馈：源／目标均复现异常；UUID候选有状态增长风险，未应用，不把隔离终态准确写成产品修复。
- [ ] 多角度：精确相机参数效果、更多设置与角色参考、批次／取消／异常继续；390裁切属源／目标共同现象。
- [ ] 肤质／纹理：更多参数／批次、纯桌面入口与完整视觉；源隐藏状态不解锁。
- [ ] 蒙版与局部重绘：真实供应商各协议、真实参考／mask字节、取消／来源变化／账号变化等关键异常与成对对照。
- [ ] 提示词优化／审美：动态资源政策／配额及其他异常；原直接审美`/tasks`仍409的边界按实际合同核对。
- [ ] Skill：完整对象与自然首段、未保存UI／并发、原模板入口、其他协议／宽度／异常及wire观察；不重跑已绿的小场。
- [ ] 绘图：其他媒体生成、跨图粘贴、归档／导入导出组合、版本／子文档恢复与完整异常。
- [ ] 文档／批量节点：故事／短剧、角色版本相关流程、完整批次异常与所有源启用节点成对回放。

### U05. 协议与完整生成矩阵（R03–R08）

- [ ] Claude／Gemini纯文字已完成有限切片；多模态、thinking、tools及其他引用分别验，不继续按旧文档“纯文字未实施”重做。
- [ ] Responses图片真实成功仍待验；最终拒绝UI已通过，不降低失败判断或按返回400宣称成功。
- [ ] 全部模型协议的输入引用、结构化产物、批次调度／逐项重试、取消、版本／历史、unknown与恢复完整对照。
- [ ] 真实音频供应商、其他付费供应商、企业登录授权暂缓，需新增明确授权后执行。
- [ ] 免费Agnes队列拒绝、ModelHub fallback及实际参数能力边界均按实际结果记录；可用一次不代表持续可用。

### U06. 媒体、时间线、导演质量与异常（R12–R15）

- [ ] 实际ASR／字幕模型与完整转写输出；codec／替身／接口测试不作模型效果证明。
- [ ] 深度长视频、质量、更多输入、CUDA／MPS，以及独立Framefield三类模型实际运行。
- [ ] 视频分析／音轨提取／音频裁剪／配乐／字幕等未逐项闭合的完整原UI路径、异常与成对视觉。
- [ ] 时间线取消、下载／归档失败、重开／并发、全部源启用轨道与导出规格对照。
- [ ] 导演完整资产／机位／动作／拍摄、会话异常、媒体下载与WebGL平台矩阵；动态步态不可由无动画GLB推断。
- [ ] 全景／调色更多参数、异常、恢复与固定源成对视觉。

### U07. 导入导出、离线媒体与生命周期（R17／R19／R20）

- [ ] ZIP重新导入、外部媒体归一化、更多节点／子文档组合、跨账号传递／并发与回执恢复。
- [ ] 源隐藏Toast相关可见反馈缺口，保留源边界与用户已授权改变，不掩盖失败。
- [ ] HEVC固定源成对与兼容副本、其他编码／浏览器／平台／异常。
- [ ] 新图片上传恢复只关闭1440／PNG／immediate／upload-unavailable单场；其余立即图片、更多媒体／政策／并发继续。
- [ ] 全部当前图／历史／本人草稿／任务／渲染／创建暂存引用释放、并发删除／恢复、物理对象清理及同版scheduler闭环。
- [ ] 单项绘图unknown删除的原界面成对运行、其他可信来源媒体及真实来源组合。

### U08. 数据权限、部署与性能（R21／R22）

- [ ] 所有子文档、全部版本字段、快照／回执不可变、成员／外人／撤权与任务／媒体恢复并发矩阵。
- [ ] 宿主成员／设置入口与伴随页面的完整接线和恢复。
- [ ] 当前API／scheduler／text／image／video／audio／render／agent同版用户部署；既有三角色助手部署或测试栈不代替。
- [ ] 100／1000／5000节点，真实不同媒体、首次创建、分页、节点打开／播放／时间线／导演重型交互。
- [ ] Worker／ObjectURL／缓存／进程等完整泄漏与生命周期检查。
- [ ] **1000节点拖拽／缩放p95≤32ms**。最新zoom全部超标，viewport候选不应用、不降门槛。
- [ ] 继续定位真实渲染／样式／媒体解码成本；源参考同样高不能证明唯一目标根因。

### U09. 视觉差异与未批准候选（R23）

- [ ] 全屏／range0中心、主题／字体／默认值、键鼠／触控、正常与减少动效的完整成对对照。
- [ ] 当前17场几何相同、16场严格全屏像素失败：顶部y=0–132品牌／共用助手差异与授权分开记录，门槛仍0、无mask。
- [ ] 历史绘图阴影4px、源自身波动及当前内部canvas尺寸错误，分别闭合，不互相解释。
- [ ] 绘图终态后挂载等隔离候选涉及源固有缺陷和可见行为边界；未经明确批准不应用。
- [ ] 多角度窄屏、源隐藏入口和菜单定位共同问题不算已修，也不因复现源问题自动通过视觉验收。

### U10. 共用助手与最终完整交付（R16／R18／R24）

- [ ] 纯问答助手同版用户部署及完整源／用户授权边界对照；保持工具零预算。
- [ ] 外部工作流／内置插件全部可达正常／关键异常与成对视觉；真实企业／付费项继续按授权。
- [ ] 所有范围内源启用项逐项通过，标准模式回归、共享／私人回归、源码／依赖／配置同步。
- [ ] 逐条关闭R01–R24剩余条目；不能以构建、哈希、测试替身或局部技术通过宣布M0–M5完成。

<a id="checkpoints"></a>

## 4. 最新验收检查点与失败复现

### 4.1 AN：新ModelHub凭据有效，完整四媒体仍未通过

| 项目 | 实际结果 |
| --- | --- |
| 冻结包 | `.runtime/modelhub-storyboard-media-an-v8-preparation-v1`，禁止重跑 |
| manifest SHA | `1d89a2a467cde3d4bdb2e859824882ac5799dafb4aabb5fe2143cf43583da187` |
| 自然终态 | controller OS1；1warning／624.46s；JUnit tests／failures／errors／skipped均0，不能写成1failed |
| 两视频 | `minimax-h3`，两POST200，各声明5s／480p／16:9，轮询completed、下载成功 |
| 两图片 | `agnes-image-2.5-flash`，两POST200，两原PNG解码成功 |
| 应用状态 | 四task／record／result自然succeeded、API READY，无active／unknown／read_errors，无retry |
| 作者播放／恢复 | 完成页与cookies-only重开四次视频播放均实际推进；两图解码、稳定身份、保存及重复不新增已到 |
| 权限 | 28次私人请求：owner8×200、member8×404、outside12×404 |
| 保存ACK | 55commit／54成功ACK／最高revision60，第17项未ACK，不写全部保存成功 |
| 成员失败 | 后生成图片位于上层，盖住同坐标视频中央播放按钮；成员媒体views均0 |
| 视频参数实测 | 两段均1038×576，format时长5.167s，24fps／124frames，完整本地解码OS0 |
| 严格比例 | `abs(width / height - 16 / 9) < 0.02`；实际差0.0243056，不满足。原Python断言未执行，只能记实测算定不符 |
| 内容 | 对象／颜色／无文字人物logo仅有限符合；红片背景／桌边变化，固定镜头未证明；对称蓝圆柱转动未证明 |
| 未到后置 | 成员播放、完整Python图／媒体、HTTP／Range、duplicate／stale AMQP、最终不变性与acceptance |
| 清理证据 | collector OS0，仅37已捕身份、精确MySQL／端口／AMQP／三桶的snapshot范围；不覆盖未来或脱离子树进程 |

关键复现步骤：新key→原Table先video再image→两阶段自然完成→成员新session打开共享图→点击视频播放按钮被图片拦截。

```text
step=成员共享四作品及私人任务隔离
locator.click: Timeout 30000ms exceeded.
verifyMedia:698:70 → driver:1376:61
```

源本身image／direct-video同坐标、后节点push到尾，成员新session `nodeStackOrder=[]`，按数组顺序绘制。当前证据不足以认定这是迁移布局bug。最窄后续是恢复image→video或源正常升层，不做force click、不改坐标、不降断言；比例问题也必须先查。

| 产物 | Task ID | 字节／SHA-256 |
| --- | --- | --- |
| 红视频 | `367940960639598592` | 741158／`fd4936be44e8c944dbee1570f7631557bccacd4e65490e540ec44fcfb86c12ff` |
| 蓝视频 | `367940939273814016` | 520833／`0f86630b1367024c9fe551c666d81b30d23d1bd94ba53277cdf31bbec331d36a` |
| 红图片 | `367942316305760256` | 773857／`3da8cd5b5e011678bc40d72f1a1eba57510def3b1360f2e02a1459ae5ed8dbc7` |
| 蓝图片 | `367942335566004224` | 842683／`3c9bb0c36f8f4a5b826501fe79764256c8c939cfdbd552fc82d6c94f56e89953` |

### 4.2 R22 v3：完整矩阵已测，性能候选仍不达标

- 每版6case／54手势组均完成；before与candidate各自然OS1，guard／benchmark／after=0／1／0，`frame_budget_failed`。
- 复用v2各874文件的owned成功构建；本轮新build退出码为null，不能写成重新构建。
- compare实际OS0仅证明受控夹具的行为／几何一致。
- viewport候选未应用；pan有所减少也不能替代原drag／zoom门槛。

| 1000节点三轮p95（ms） | before | candidate |
| --- | --- | --- |
| drag | 16.1／16.7／16.7 | 9.0／16.7／16.7 |
| zoom | 91.8／116.7／108.4 | 91.7／166.8／100.2 |

### 4.3 R19：新增图片上传不可用恢复单场通过

- 范围：1440／PNG／immediate／upload-unavailable，首次POST未转发API即503，与丢失上传回执场不同。
- 首次父目录不存在：OS1／1error／13.51s，`Path.mkdir → FileNotFoundError [WinError3]`，浏览器未启动，保留失败原件。
- 新独占父目录第二次：OS0／1passed／0skip／1warning／48.75s。
- 首次和刷新upstream0；原Ctrl+S之后同键恰一次multipart，原File／SHA／像素／永久身份／cookies-only零重传、MySQL／MinIO／HEAD／Range／三方权限后置通过。
- task／model POST均0，不是供应商验收。原20guard、15服务、两源保持；仅4397absence和原MySQL cleaned日志，未补完整存活树／全资源absence。
- 该成功场不用无理由重复；继续缺失的相邻矩阵。

### 4.4 新增五次授权正在准备，不能预记为已执行

截至本文件取证时，主线程已有 `.runtime/modelhub-budget-extension-root-v1/` 扩额候选、原账本baseline与10／30计划，并出现AO新准备目录。这里没有取得真实扩额apply或AO真实运行终态收据，**只能记为准备中**。

本文件不启动、审批、应用或接管这些主线程任务。接手人先核对最新实际收据，再决定下一步；不存在终态收据时不得补造“已扩额／已生成／已通过”。

<a id="files"></a>

## 5. 新增、修改及删除文件范围

### 5.1 统计口径

整理前实际读取 `git status --short`：**1858条状态记录，其中1492条已跟踪变更、366条未跟踪记录**。默认Git会把部分未跟踪目录汇总，因此这不是1858个唯一新增／修改文件，也不能据此断言全部变更由本次任务产生。

工作区原有大量未提交内容必须保留。以下是当前迁移主线的改动范围与实现入口；vendor／来源文件的换行或哈希变化不能按数量当成功能数量。本次交接整理仅新增本Markdown。

### 5.2 按文件夹汇总当前范围

| 目录／文件 | 已跟踪变更／未跟踪记录 | 对应职责 |
| --- | --- | --- |
| `backend/src/short_drama/ai/` | 8／13 | 协议、Agnes／ModelHub、Claude／Gemini、RunningHub、编辑器工具、引用及凭据 |
| `backend/src/short_drama/api/` | 4／6 | 画布模型、资源、任务、工具、角色／来源、路由 |
| `backend/src/short_drama/service/` | 32／24 | 保存／归一化、生成、来源／角色、回收、ASR／深度、时间线／渲染 |
| `backend/src/short_drama/dao/` | 5／9 | 图、资源、创建／删除、角色版本／目录、来源与工具／渲染数据访问 |
| `backend/src/short_drama/domain/` | 4／5 | 画布／资源／工具／渲染、角色版本等ORM |
| `backend/src/short_drama/schemas/` | 8／12 | API／任务／模型能力、角色／来源、时间线／深度／ASR契约 |
| `backend/src/short_drama/db/` | 2／4 | 可信Actor访问、画布任务／工具／渲染范围 |
| `backend/src/short_drama/tasks/` | 2／2 | 正式Worker／Runtime、工具／渲染任务 |
| `backend/src/short_drama/agent/` | 1／0 | 共用助手正式运行事件等调整 |
| `backend/src/short_drama/core/`、`storage/` | 各1／0 | Settings、MinIO实际归档与读取 |
| `backend/tests/integration/` | 11／123 | MySQL／MinIO／AMQP／真实浏览器／供应商各专项 |
| `backend/tests/unit/` | 13／35 | 协议／请求／权限／状态／版本与异常单测 |
| `backend/tests/api/` | 0／4 | 角色、来源及专用HTTP合同 |
| `backend/scripts/` | 2／3 | 迁移、schema导出与运维工具 |
| `backend/.env.example`、`pyproject.toml`、`uv.lock` | 各1／0 | 配置示例、pytest路径／依赖声明与锁 |
| `frontend/canvas/src/` | 693／6 | 固定源组件、页面、store、lib／services与宿主适配；含授权移除项 |
| `frontend/canvas/scripts/` | 4／51 | 来源校验／对照、性能测量、原UI真实Python驱动 |
| `frontend/canvas/tests/` | 8／23 | 生成／引用／恢复／媒体／工具合同 |
| `frontend/canvas/e2e/` | 2／1 | 宿主／画布、依赖兼容、角色current浏览器夹具 |
| `frontend/canvas/public/` | 6／0 | GLB／字体／MediaPipe／FFmpeg／three等资源范围 |
| `frontend/canvas/vendor/` | 653／0 | 第三方图标等来源资源，不视作653项功能改写 |
| 画布README／HTML／Vite／适配账本 | 各有已跟踪变更 | 运行入口、资源构建与来源登记 |
| `frontend/src/api/` | 4／4 | 宿主画布DTO、角色／来源／编辑器工具请求 |
| `frontend/src/features/` | 5／2 | 共用助手、模型配置／RunningHub设置及相关界面 |
| `frontend/src/pages/`、`frontend/tests/` | 各1／0 | 项目入口及回归 |
| `docs/api/` | 3／0 | assistant、canvases、model-runtime说明 |
| `docs/plans/` | 3／24 | 迁移、交接、剩余工作及各专项审计 |
| `docs/数据库模型/` | 6／9 | 完整schema、增量001–019中的变更与执行说明 |
| `.gitattributes`、产品／开发说明等 | 另有变更 | 换行规则、产品边界和维护说明 |

### 5.3 接手时优先定位的产品文件

| 功能 | 关键入口 |
| --- | --- |
| 固定来源与适配 | [source-manifest.json](../frontend/canvas/source-manifest.json)、[source-adaptations.json](../frontend/canvas/source-adaptations.json)、[画布README](../frontend/canvas/README.md) |
| 画布页面与保存 | `frontend/canvas/src/pages/canvas/`、`src/stores/canvas/`、`src/lib/canvas/`；后端 `canvas_service.py`／`canvas_document.py`／`canvas_workspace_service.py` |
| 正式模型请求／轮询 | [adapters.py](../backend/src/short_drama/ai/adapters.py)、[gateway.py](../backend/src/short_drama/ai/gateway.py)、[transport.py](../backend/src/short_drama/ai/transport.py)；ModelHub不在独立的`modelhub_minimax_h3.py`文件 |
| 画布生成准入／结果 | `backend/src/short_drama/service/canvas_generation_*.py`、`canvas_text_admission.py`／`canvas_video_admission.py`、`canvas_task_*.py`／`canvas_task_outputs.py` |
| 视频参数四文件修复 | [model-selection.ts](../frontend/canvas/src/lib/model-selection.ts)、[generation-task.ts](../frontend/canvas/src/services/api/generation-task.ts)、[参数测试](../frontend/canvas/tests/canvas-generation-source-input.test.mjs)、[适配账本](../frontend/canvas/source-adaptations.json) |
| 资源／复制／删除 | `canvas_resources.py`、`canvas_resource_dao.py`、`canvas_resource_deletion_dao.py`、`canvas_generation_inputs.py`、`canvas_media_locators.py`、`canvas_resource_cleanup.py`、`canvas_creation_cleanup.py`、`resource_import_service.py` |
| 来源／角色 | `api/v1/canvas_source_project.py`／`canvas_character_catalog.py`／`canvas_character_images.py`；对应DAO／Schema／Service；`asset_versions.py`等版本实现以实际目录检索 |
| 前端来源／角色 | [host-source-project.ts](../frontend/canvas/src/services/host-source-project.ts)、[host-character-catalog.ts](../frontend/canvas/src/services/host-character-catalog.ts)、[host-character-images.ts](../frontend/canvas/src/services/host-character-images.ts)、`use-canvas-source-project.ts`／`use-canvas-character-catalog.ts`、宿主对应DTO |
| 图片与绘图工具 | `frontend/canvas/src/components/canvas/`，`canvas_tool_*`、`canvas_drawing_*`及各工具Service；`frontend/canvas/scripts/verify-*-python.mjs`对应原UI驱动 |
| 时间线／渲染／深度 | `canvas_timeline_compile.py`／`canvas_timeline_native.py`／`canvas_timeline_renderer.py`、`canvas_render_service.py`／`canvas_render_archive.py`、`canvas_depth_*`、`canvas_transcription*.py` |
| RunningHub | `ai/canvas_runninghub_*.py`、`service/canvas_runninghub_*`；宿主`CanvasEditorTools.tsx`／`RunningHubConfigFields.tsx` |
| 共用助手 | [CreativeAssistantPanel.tsx](../frontend/src/features/ai-assistant/CreativeAssistantPanel.tsx)、[画布薄适配](../frontend/canvas/src/pages/canvas/creative-assistant-sidebar.tsx)、[保存屏障](../frontend/canvas/src/pages/canvas/canvas-assistant-context.ts)、`agent/runtime.py`与`/api/v1/assistant` |
| 已应用前端性能 | `floating-dock.tsx`与Zoom控件实现及来源账本；viewport／panel-width隔离候选不作产品入口 |
| 后端总路由 | [router.py](../backend/src/short_drama/api/v1/router.py)，核对实际include_router，禁止根据旧文档猜路径 |

### 5.4 数据库结构与实际执行边界

- 业务库记录为 `short_drama`／MySQL 8.4.11；后端不会启动时自动建表／迁移。
- 早期26张画布相关新表、后续资源／素材／任务／工具／渲染等均有增量；旧文档78／93表是历史快照，019之后完整SQL记录为101表。
- 共用助手V2使用既有Agent九表，已执行对话`episode_id`可空、范围CHECK、项目历史索引的ALTER，旧27段对话保持；不重复DDL。
- 018可信来源绑定结构已有实际执行记录；业务来源／角色关联不能凭空补造。
- 019已在业务库执行七条结构DDL：两个版本表、版本媒体索引、`assets.current_version_id`、current索引、角色版本CHECK、复合RESTRICT外键；独立verify为ready。原四角色、零project_assets、source全NULL、当前指针／版本／版本媒体零行是当时事实。
- 019**尚未完成角色backfill及完整停旧写者／同版部署流程**。本文件没有重新查业务库，不将这些历史行数当作当前实时值。
- 结构变化仍须ORM、当前使用库真实DDL、完整SQL／旧库增量／说明三步齐全。

### 5.5 本机运行证据与凭据文件

- `.runtime/`、`backend/.runtime/`、`frontend/.runtime/`等为忽略的日志／截图／候选／收据，不能作为源码提交。
- 唯一付费预算账本：`backend/.runtime/modelhub-minimax-h3-budget-20261012.sqlite3`。不得复制为“新预算”、初始化、重置或退款。
- ModelHub凭据：`backend/.runtime/modelhub-provider-credential.encrypted`，服务端加密信封；本文件不包含明文或密文。
- 扩额准备目录：`.runtime/modelhub-budget-extension-root-v1/`；新轮准备目录：`.runtime/modelhub-storyboard-media-ao-v8-preparation-v1/`。目录存在不代表manifest、freeze、apply或live已成功。
- 原AN包、原预算模块、旧失败收据／图片、34条历史unknown都保留，不覆写。

<a id="next"></a>

## 6. 接下来应完成的工作顺序

| 顺序 | 工作与输入 | 完成后可进入下一步的条件 |
| --- | --- | --- |
| 0 | 核对当前Git、主线程新收据、15原服务、两源HEAD／clean、真实账本；确认是否已有人继续AO | 不覆盖已有修改、不双开同轮、不复用旧PID／端口／基线；授权与实时额度明确 |
| 1 | 读取正式adapters／ModelHub单测及官方合同，定位1038×576比例问题；核固定镜头／转动内容 | 参数使用有文档依据；不能预判通过，不能降比例阈值或转码掩盖 |
| 2 | 审查并落地新增五次预算扩额，保留旧4次／20秒及授权审计 | 唯一账本事务完成且回读正确，次数上限按新授权、声明秒仍≤30 |
| 3 | 完成AO或其他新独占验收包；恢复image→video或源正常升层，全部原断言与未知重投门禁保留 | 新UUID／端口／日志目录／manifest／产品基线；本地／静态检查通过；旧AN不修改 |
| 4 | 首轮两次各5秒／480p真实视频，通过正式API→Worker→供应商；图片使用授权免费模型 | 按实际供应商及应用自然终态记录，所有POST含失败计预算；unknown立即停止重提 |
| 5 | 完成成员播放、连续内容查看、MySQL／MinIO／原字节／HTTP／Range／AMQP／不变性全部后置 | 仅实际执行通过的范围结案；精确资源清理收据与旧证据保持 |
| 6 | 收角色UI／发布／完整RR并发、backfill与可信首帧余项 | 先闭合代码／数据依赖门禁；维护／部署不凭测试夹具或结构DDL自动跳过 |
| 7 | 定位R22真实缩放成本并验证最小性能修改；保留source／target同机100／1000／5000矩阵 | 正式1000drag／zoom≤32ms、行为／几何／媒体／保存无退化，候选实际通过后再按权限应用 |
| 8 | 收R09表情内容失败及其他独立工具、R17／R19／R20／R21剩余矩阵 | 已绿场不无理由重跑；逐项输入／协议／宽度／异常与可见结果有证据 |
| 9 | 在同版本用户环境核全部角色、配置、broker、媒体、可信HTTPS来源 | 不擅自启停原用户服务；测试栈与用户部署分开记 |
| 10 | 闭合R23可见差异边界，完成R24全量M0–M5及标准／共享私人回归 | 所有范围内启用项通过、无未经接受的界面／流程偏差，才能声明完整迁移 |

步骤6–8中互不依赖的本地工作可并行安排；所有付费POST共用一份预算。遇到供应商不可用可继续授权范围内独立事项，不能反复制造或重投unknown。

每个真实轮结束后更新四份主维护文件：实施记录、接续交接、剩余工作、统一分镜媒体审计；同时按实际新状态更新本文件。旧失败保持原结论，新结果单列，不改写历史。

<a id="models"></a>

## 7. 模型调用指令、授权与额度规则

### 7.1 用户授权的时间顺序

| 顺序 | 用户指令／事实 | 当前如何执行 |
| --- | --- | --- |
| 1 | “先记录起来，暂不用真实供应商验收” | 后来的明确真实验收授权已覆盖对应免费模型与限定fallback；未授权种类继续暂缓 |
| 2 | Agnes官方指南及三个免费模型；“免费模型没有次数限制”“没达到验收标准可以调整代码后多次验收调用” | 允许必要真实文本／图片／视频验收与针对失败的代码调整，不受次数限制；仍须完整验收和unknown规则 |
| 3 | Agnes视频不可用时，ModelHub `minimax-h3`／480p，最多五次，五次总生成时长≤30s，可分配每次时长 | 只授权该备用视频模型；原总次数5、累计声明秒30，所有轮次共用账本 |
| 4 | 两次401后用户提供新的API Key并说“重新试试看” | 服务端加密凭据已更新，主密钥保持；两旧401仍计2次／10秒，不退款 |
| 5 | “我允许你再调用五次视频生成模型” | 新增五次调用授权，准备把次数上限5→10；**未明确增加秒数，仍保留累计声明≤30s** |

主代理已经向用户承诺：新增授权首轮先两次各5秒，480p，总声明时长保持原30秒以内。五次新增授权不要求全部耗尽；模型最短5秒且当前仅剩10声明秒，不能据新增次数自行扩至60秒。

### 7.2 允许使用的模型与官方入口

| 类型 | 供应商／模型 | 官方说明／调用范围 |
| --- | --- | --- |
| 文本 | Agnes `agnes-3.0-flash` | [Agnes指南](https://wiki.agnes-ai.cn/zh-Hans/docs/overview)，免费、必要真实验收不限次数 |
| 图片 | Agnes `agnes-image-2.5-flash` | 同上，免费、必要真实验收不限次数 |
| 视频首选 | Agnes `agnes-video-2.5-flash` | 同上，免费；队列拒绝／限流／unknown按真实分类 |
| 视频备用 | ModelHub `minimax-h3`／480p | [ModelHub quick-start](https://modelhub.cc/quick-start)；只用`https://api.modelhub.cc/v1`正式入口 |
| 真实音频 | 尚无本次明确授权供应商 | 暂缓独立真实音频验收，不用已存在音频适配推断已授权 |
| 企业授权／其他收费 | 尚未明确账号／预算 | 暂缓，不扩展付费模型、分辨率或供应商 |

已核对的ModelHub产品请求合同：`POST /v1/videos/generations`；`model=minimax-h3`、`duration=5`、`resolution=480p`、`ratio=16:9`、直接文字场`functionMode=omni_reference`；随后`GET /v1/videos/tasks/{provider_task_id}`。有first／last图使用另一模式，但不能擅自把当前直接文字验收改成首帧场并当原场通过。

官方合同仍需核对最新比例语义；不得臆造width／height等字段。先前双域GET鉴权诊断被自动审批拒绝且取消，不再重复；继续已核准的单一正式入口。

### 7.3 预算实时值与安全扩额

本文件依据已存在的AN终态与扩额baseline，未直接查询真实SQLite：

| 项目 | 最后可追溯数值 |
| --- | --- |
| 已计POST | 4次：AH两次401＋AN两次200，均各声明5s |
| 已计声明时长 | 20s |
| 原policy | max_posts=5、max_seconds=30 |
| 原policy剩余 | 1次／10s；不能据此直接开展需要两次的新轮 |
| 新授权候选policy | max_posts=10、max_seconds=30，apply尚无终态证据 |
| 扩额后按该快照 | 次数账面可剩6，秒数仍只剩10；本轮5s任务最多再做两次 |

预算规则：

1. 使用唯一账本`backend/.runtime/modelhub-minimax-h3-budget-20261012.sqlite3`，跨所有轮次累计。
2. 发送前事务reserve，发出前claim_dispatch；失败、401、429、timeout和unknown都不退款。
3. 同key禁止二次供应商发送；不确定结果不得换key重试。
4. 扩额必须追加原授权＋新增授权证据，保留原policy字节／hash、原四条reservation、endpoint／model／resolution／路径／user_version校验。
5. 不直接修改常量绕门禁，不复制／初始化／清零账本，不覆写冻结AN预算模块。
6. 只统计声明秒作预算；实际媒体时长另据ffprobe记录，不能拿实际5.167秒改写声明5秒或倒扣额度。
7. ModelHub没有次数无限授权；免费Agnes不限次数也不代表允许自动重投unknown。

### 7.4 凭据使用与真实验收判定

- 用户已提供Agnes与新ModelHub凭据，**本文件不重复任何API Key**。Agnes沿现有服务端配置读取；ModelHub沿上述encrypted文件或已存加密配置读取，不向浏览器传递。
- 密钥只放环境／服务端加密存储；不写日志、请求证据、测试快照、浏览器存储、`VITE_*`、文档或Git。
- 不读取或输出`.env`实际内容、不覆盖加密主密钥；测试master必须与持久信封cipher契约一致。
- 正式API、保存来源、独立Worker、真实供应商受理／轮询／下载、MySQL／MinIO归档、稳定回填、作者与成员实际播放／权限、恢复、重复消息及人工内容检查都应按场景完成。
- 真实调用200／READY、技术pytest通过、原帧采样内容通过是不同结论，分别记录。
- 内容变化或明确新任务才可用新key；原34条历史unknown不自动判未受理、不重投。
- 原UI只在服务端`can_retry`／`can_resume`许可下显式执行；已知未受理429的许可重试也计预算，不能泛化为503或unknown自动重发。

<a id="commands"></a>

## 8. 环境、启动与验证命令

### 8.1 环境交接

- Windows PowerShell；历史环境实测Node22.23.2、Python3.12.14，接手时重新确认。
- 推荐Node22.18+的22.x或24.x LTS；画布使用类型剥离，nanoid6不支持Node20。
- 后端进程／脚本在`backend/`运行，Settings从当前目录读取`.env`。
- 标准8080、画布8082、API8000是维护约定；接手先盘点实际监听，不直接杀占用进程。
- 现有`backend/.tools/uv/uv.exe`历史可用；PATH无uv时先确认本机工具，不重新下载或改系统配置。
- 现有`backend/.venv/Scripts/python.exe`可用于等价Python命令；Chrome沿`PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH`，本机路径`C:\Program Files\Google\Chrome\Application\chrome.exe`。
- FFmpeg／ffprobe须实际可执行；历史ffprobe在忽略的`backend/.tools/ffmpeg/probe-5.1/bin/ffprobe.exe`，换机不能假设存在。
- 每个并行后端使用不同`SNOWFLAKE_WORKER_ID`；同一轮API／scheduler／Worker的`GENERATION_QUEUE_NAMESPACE`一致，各测试轮互相隔离。
- 认证与Agent默认开启，升级未完成需按部署说明显式配置；不改默认值绕检查。
- 主线程已有15原服务和两源身份保护；本文件没有结束任何进程或更改权限。

### 8.2 常用启动入口

以下是命令说明，**本文件编写期间没有执行**。在现有用户环境先检查已运行服务，按需要使用。

| 工作目录 | 命令 | 用途 |
| --- | --- | --- |
| 仓库根 | `docker compose up -d` | MySQL／RabbitMQ／MinIO；保留已有业务基础设施 |
| `backend/` | `uv sync --locked` | 按锁准备环境，不覆盖现有`.env` |
| `backend/` | `uv run uvicorn short_drama.main:app --host 127.0.0.1 --port 8000` | API；多进程worker_id按启动脚本／现有配置安排 |
| `backend/` | `.\scripts\start_generation.ps1 -Role scheduler` | Publisher／Recovery |
| `backend/` | `.\scripts\start_generation.ps1 -Role text` | 对应text／image／video／audio／render／agent角色分别启动，先可用`-DryRun`检查 |
| `frontend/` | `npm run install:all` | 两包各锁安装 |
| `frontend/` | `npm run dev` | 单进程管理8080／8082两Vite |
| `frontend/` | `npm run dev:standard`／`npm run dev:canvas` | 独立运行其中一包 |
| `frontend/` | `npm run build`／`npm run preview` | 两包检查与组合产物、同源预览 |

### 8.3 按变更范围实际执行验证

| 工作目录 | 命令 | 结果应该如何记录 |
| --- | --- | --- |
| `backend/` | `uv run pytest tests/unit tests/api -q` | 快速测试，不依赖业务库／真实模型 |
| `backend/` | `uv run ruff check src tests scripts` | 静态检查 |
| `backend/` | `uv run ruff format --check src tests scripts` | 全格式；旧范围外失败也须明示 |
| `backend/` | `uv run python scripts/run_integration.py` | 随机临时MySQL库，需CREATE／DROP权限 |
| `frontend/` | `npm test` | 宿主Node |
| `frontend/` | `npm run typecheck` | 快速类型；build成功已含类型无需重复 |
| `frontend/` | `npm run build` | 两包类型＋生产构建＋画布产物组合 |
| `frontend/canvas/` | `npm test`／`npm run source:check`／`npm run test:e2e` | 画布合同／来源／浏览器，API夹具与真实链区分 |
| `frontend/` | `npm run test:e2e` | 标准浏览器；测试端口4175，具体文件按变更选 |

真实基础设施／模型专项必须显式开相应`RUN_*`，使用随机`_test`库、专属桶／队列／端口与各自finally清理。当前业务账号历史缺CREATE DATABASE权限；既有`.runtime/beeftv-resume-run.py`使用独占临时MySQL容器，不能改成业务库或提权业务账户来绕过。

最新可追溯验证摘要：

| 验证 | 已有实际结果与限制 |
| --- | --- |
| 完整后端unit/API复验 | 2939passed／0skip／2warnings／635.39s；首次Temp权限错误2849pass／90setup errors／OS1保留，不改判 |
| 后端Ruff | check已有通过；全format旧`apply_assembly_migration.py`混合换行缺口保留，相关范围format通过 |
| 视频默认参数应用后 | canvas524passed／0skip、source832／220适配／91删除、两包build实际OS0 |
| 宿主Node | 最近维护阶段219项通过是其当时版本范围，不将画布524加成当前唯一总量 |
| R19新增图片恢复 | 1passed／48.75s，真实库／存储／浏览器，供应商0 |
| R22 v3 | 两版完整54组均frame_budget_failed；compare0只为受控行为／几何 |
| AN真实四媒体 | 供应商受理／归档／作者恢复已到，但成员播放与完整后置未过；不写真实整场通过 |

本次交接文档只做内容、路径／链接与敏感信息检查，没有重复上述业务验证。

<a id="rules"></a>

## 9. 用户规则指令汇总

本节把仓库AGENTS与本次会话实际影响接续的用户要求集中列出。与旧说明冲突时，后来的明确用户授权及实际已应用代码优先；局部或准备结果不能改写成完整验收。

### 9.1 工作方式与修改范围

1. 默认中文回复与维护文档，标识符、字段和命令沿既有命名。
2. 先理解再修改，先读相关模块、调用链、测试；不确定API／库用法查文档，禁止凭记忆编造。
3. 开始前`git status --short`，保留全部已有未提交内容；最小修改，不顺手重构，需要重构先说明并取得同意。
4. 超过三个文件或跨层修改先说明改哪些、为何改，再动手；当前仅新增交接文档。
5. 维护说明与实际路由／配置／测试不一致时核对并更新受影响说明；旧计划仅作背景。
6. 不留TODO、注释死代码、`console.log`／`print`调试残留，使用项目logger。
7. 报错给关键完整stack与复现，不只写“报错”；失败原件与旧结果保留。
8. 不主动commit／push；用户明确要求提交时沿Conventional Commits，一个commit一件事。
9. 不启停原用户服务、不复用旧PID猜进程归属；仅清理确实本轮创建的资源。
10. 不降低验收断言、像素mask／阈值、32ms、时长／比例门槛；不靠force click、裁剪／转码或延迟掩盖问题。
11. 不应用未批准R23可见行为候选；不因它属于源缺陷自动接受偏差。
12. 不建公网隧道，不扩大供应商／分辨率／付费范围；模型授权按第7节。

### 9.2 后端、数据库与权限

1. API→Service→DAO→ORM；路由处理HTTP／依赖，Service事务／业务，DAO不提交；独立Service传入尚未开启事务的Session。
2. 保留可信Actor、归属项目与资源范围；system Session只用于内部明确场景，不能由HTTP输入开启。
3. HTTP输入／输出与AI结构化输出通过Pydantic验证，提示词不能替代校验。
4. 网络／数据库／队列／存储等I/O错误包含操作和失败原因；异步异常不得吞掉或静默失败。
5. 对外ID／版本为十进制字符串，前端不转Number；片段稳定UUID，时间UTC。
6. 表结构必须ORM＋当前使用库实际DDL＋完整SQL／增量／说明齐全，执行后注明真实库和语句；应用不自动迁移。
7. HTTP契约变化同步Pydantic、前端DTO及接口说明；Python类型注解、snake_case、Ruff100字符行宽。
8. 不关闭FK、不使用业务库做集成测试，不把跳过、collection、替身、资源清理或build当真实供应商证明。

### 9.3 前端、保存与交互

1. 通过`frontend/src/api/http.ts`和modules请求，DTO保留snake_case；组件不另建Axios。
2. 保留串行自动保存、版本验证、导航保护；生成前先来源保存。
3. 旧响应不覆盖新输入；409保留草稿并等待明确处理，不自动覆盖服务端。
4. 沿相邻TS／React、Hook和公共组件写法；标准工作台复用冷黑紧凑主题。
5. 前端界面改动前阅读PRODUCT／DESIGN；画布按固定源视觉与用户已批准例外处理。
6. 异步操作明确loading；数据区域loading／空／错误三态，就近错误、键盘操作、窄屏行为齐全。
7. 候选、已保存、已确认、当前采用要明确区分。
8. 源隐藏／禁用入口保留，标准功能与画布独立root／CSS／Provider不得混合。

### 9.4 生成、助手与媒体

1. 标准生成保存候选，明确采用后才改作品，保留版本／上下文／共享影响检查。
2. 画布直接回填是用户一比一授权的限定例外；本人参数／任务／历史／凭据／对话仍私人隔离，进入共享图的作品按项目权限。
3. 不确定请求复用原Idempotency-Key；内容变化／明确新任务才换键。恢复／重试遵循can_resume／can_retry，unknown不自动重提。
4. 原34条历史unknown不重投；新真实unknown也不能据无结果判未受理。
5. 原一般Agent的计划批准／候选作者／来源版本规则继续适用于仍保留的旧范围；**新共用纯问答助手不恢复执行工具**。
6. 项目权限不能替代私人候选作者／任务作者验证；本人会话及创作历史不能对成员泄露。
7. 稳定媒体定位值＋元数据持久化；签名／Blob URL不作身份，过期后重读。
8. API Key和master保护按第7节，不输出实际`.env`。

### 9.5 验证、文档和完成定义

1. 代码能实际跑并通过相应验证才算完成。修bug优先有意义的复现测试红→绿，新功能关键正常／边界覆盖。
2. build包含typecheck，不无理由重复；新修改或失败／不确定范围才扩大验证。
3. MySQL随机临时库须CREATE／DROP权限、最终清理；未配置的skip不是集成通过。
4. 浏览器API夹具、controlled provider、真实MySQL／MinIO／AMQP、真实供应商和人工内容分别标记。
5. 保留依赖锁和`.env.example`；依赖变化同步锁；不提交凭据、依赖目录、工具、缓存、产物、`.runtime`日志／截图。
6. 完成说明包括改了什么、实际跑什么、未验什么；不能用局部成功掩盖内容失败或未执行后置。
7. 完整迁移只能在所有范围内布局、操作、媒体、生成、助手与真实运行对照通过后声明；阶段／hash／build／夹具通过不等于完成。
8. 本次交接属于独立文档整理：不接管或继续执行主线程旧计划，不调度子代理，不改代码／配置／权限／预算账本；这里只提供接续清单。

<a id="evidence"></a>

## 10. 交接完成检查表与证据索引

### 10.1 接手第一轮核验

- [ ] 确认仓库根、固定源commit、实际git状态；不要覆盖或提交已有未归因改动。
- [ ] 核对主线程在本文快照之后的新apply／manifest／receipt，确认AO／扩额是否已有结果。
- [ ] 确认实时视频budget及授权，第7节4／20是最后记录值，非实时查询。
- [ ] 不重跑AN，不重投历史unknown，不应用未批准R23／失败viewport候选。
- [ ] 用第6节顺序推进，每个新轮独立目录／UUID／端口／基线，保留全部旧证据。
- [ ] 最终结案只按实际功能与场景，不累计重叠测试为唯一总数，不误报JUnit0为1failed。

### 10.2 必要维护与原件索引

| 用途 | 文件 |
| --- | --- |
| 仓库协作约定 | [AGENTS.md](../AGENTS.md)；本文件第9节已汇总 |
| M0–M5完整迁移依据 | [迁移方案](plans/2026-10-05-beeftv-infinite-canvas-migration.md) |
| 主实施记录 | [实施记录](plans/2026-10-05-beeftv-implementation-log.md) |
| 主接续文档 | [原交接](plans/2026-10-06-beeftv-resume-handoff.md) |
| R01–R24台账 | [剩余工作](plans/2026-10-08-beeftv-remaining-work.md) |
| 当前统一媒体／角色／来源审计 | [分镜媒体审计](plans/2026-10-11-beeftv-agnes-storyboard-media-broker-audit.md)，最新锚点`modelhub-an-r22v3-r19unavailable-current` |
| 角色目录／图片产品验证 | [角色读取审计](plans/2026-10-11-beeftv-character-reads-audit.md)，其更晚应用／RR状态以统一媒体审计为准 |
| 数据库完整结构／迁移 | [schema.mysql8.sql](数据库模型/schema.mysql8.sql)、[画布增量说明](数据库模型/migrations/2026-10-05-infinite-canvas/README.md) |
| 当前接口说明 | [画布API](api/canvases.md)、[助手API](api/assistant.md)、[模型runtime](api/model-runtime.md) |
| 开发、部署 | [开发指南](development.md)、[Agent部署](agent-deployment.md)、[账号协作部署](collaboration-deployment.md) |
| AN原自然终态与内容边界 | [Root终态v2](../.runtime/modelhub-an-root-terminal-review-v2.json)，SHA`7c3768448e5023a7d2602a636d8f1781860b28615625597cff802577633f285f` |
| AN精确资源回查 | [collector收据](../.runtime/modelhub-storyboard-media-an-v8-root-final-resource-review-v1/receipt.json)，SHA`3b6c0cff367a9c22a722aec5a8e598d859bf431f0ccda5c806c04f5bf921383f` |
| 视频四文件修复验证 | [验证收据](../.runtime/modelhub-canvas-video-defaults-verification-root-v2/receipt.json)，SHA`70e24bcd46e45d4d0813dfe807dc84782ff53fa655e0221f481ab3a24f9b82bf` |
| R22 v3实际对比 | [比较收据](../.runtime/r22-live-viewport-production-matrix-preparation-p-v3/comparison-root-v3-a.json) |
| R19新图片恢复 | [Root终态](../.runtime/r19-pending-image-unavailable-live-root-v2/root-terminal.json)，SHA`c10ff4c43109761a414ba10b02f97a91bfbbf2fbf4b4e6827d468a137afc7a29` |
| 新增五次预算准备 | [授权证据](../.runtime/modelhub-budget-extension-root-v1/authorization-evidence.json)、[扩额计划](../.runtime/modelhub-budget-extension-root-v1/real-ledger-extension-plan.json)，不代表apply成功 |

`.runtime`证据仅在当前机器留存，换机或清理后可能不存在；本文件已写明结论、关键输入、失败位置、额度与下一步，不要求先读所有历史审计才能接手。需要复验时保留缺失证据事实、重新建立独立基线，不能伪造原日志或旧通过。
