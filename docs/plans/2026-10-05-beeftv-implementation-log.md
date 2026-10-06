# BeefTV 画布迁移实施记录

完整目标以 [一比一迁移方案](2026-10-05-beeftv-infinite-canvas-migration.md) 为准。本记录用于跨阶段接续，不将某个阶段的完成视为完整迁移完成。

**2026-10-06 暂停接续入口：[迁移接续文档](2026-10-06-beeftv-resume-handoff.md)。** 当前 goal 为 `paused`；详细记录了最新证据、未完成功能、未部署代码和下次先执行的原 UI 文字图片验收。本记录早期数量及行为描述是当时的历史基线，以最新切片与接续快照为准，不据旧段落重复实施。

2026-10-06 按用户调整，独立画布包从 `canvas-frontend/` 移至 `frontend/canvas/`，保留独立依赖、锁文件和 HTML，并由宿主统一安装、启动、构建与预览。本记录此前出现的 `canvas-frontend/.runtime/...` 是当时实际执行的历史路径，保留原文；当前脚本和维护入口均使用 `frontend/canvas/`。共同声明依赖的版本按宿主对齐后，需要重新对照视觉与行为，此前旧依赖版本的验证不自动覆盖本次变化。

## 基线

- BeefTV：4ca2a65a7780a8dfcaaa86c33679f84fb04e055c，开始实施时工作区干净。
- 目标仓库：4e24eefdce3e，已有大量未提交的 Agent 与创作流程改动，全部保留。
- 已存在 Node 22.22.1、两套前端依赖、Python 虚拟环境和 MySQL 服务。
- 3000、8080 已有进程监听，启动任何联调服务前检查用途，避免替换用户进程。

## 阶段状态

| 阶段 | 状态 | 证据与剩余工作 |
| --- | --- | --- |
| M0 源基准和兼容构建 | 进行中 | 最新 832 个源文件 / 91 项适配，独立依赖兼容与统一媒体构建已通过；正式 17 编辑器场景几何一致、16 场景 0px，绘图阴影 4px 未关闭；完整功能、逐帧动效与媒体基准待补 |
| M1 双模式和持久化 | 主体落地，未完整验收 | 模式、主画布、权限、保存/CAS、历史、个人状态、资源/文件夹/回收及真实 DDL 已落地；业务库最新记录 93 表 / 41 画布表；完整恢复、生命周期尾项、伴随页面、大图与部署仍待验 |
| M2 生成与回填 | 多项切片通过，未完成 | 已有任务/结果、私人投影、幂等恢复、多项生成/视频协议和 Chat 图片后端联调；原 UI 文字图片待运行，剩余协议/批次/结构化结果/日志/真实供应商待闭合，最新 text/cache 未部署 |
| M3 创作工具 | 多项切片通过，未完成 | 绘图持久化/历史/复制、裁切真实原 UI 和宫格正常矩阵已验；标注、宫格异常/409、其余工具、批量/文档及完整媒体引用待验 |
| M4 媒体和导演 | 主要剩余阶段 | 前端闭包与部分存储基础已迁入；浏览器媒体工具、Python canonical plan、ASR、时间线、导演及真实媒体运行仍待完整交付 |
| M5 助手和完整对照 | 主要剩余阶段 | 部分 ZIP/归档组合已有专项；Python 画布助手、工作流/插件、剩余导入导出、完整截图/操作/功能及标准回归待交付 |

## 已实现的范围

本节保留初始阶段快照，其中“当前拒绝目录修改”等限制已被后续 M2 设置/目录切片覆盖；最新实现范围请结合顶部阶段表、后续日期章节和接续文档读取。

- 宿主创建弹窗提供两种模式，默认标准模式；无限画布创建使用稳定幂等键，未知响应保留原请求，成功进入 `/canvas-app/canvas/{source_key}`。标准项目不加载画布包；无限画布项目不能创建伪分集。
- 独立 `frontend/canvas/` 保留源 HTML、React root、Provider、CSS、字体、原版交互和完整媒体依赖；按用户后续调整，共同声明但版本不同的直接依赖采用宿主版本，源独有依赖独立锁定。源版本、文件哈希、明确适配原因与 HTTP 调用清单可追溯；不要重跑 importer 覆盖适配文件。
- 前端先读取真实账号再导入持久化 Store。HTTP 与 Blob 复用宿主传输；源 `/projects`、`/assets` 等进入专属运行时命名空间，避免误调标准接口。未接通接口仍为明确错误，不能视为已迁移。
- Python 提供画布关系存储、共享作品与本人私人投影、版本 CAS、幂等写回执、首次创建、删除/主画布选择、不可变历史读取与恢复。规范 ID/版本均为十进制字符串，源节点与连线保留稳定字符串键。
- 回收站目录、受限媒体预览、显式恢复和不可恢复处置已接入 Python；新设备可读取本人当前归档记录，永久处置阻止后续恢复。原回收卡片与确认流程保持；全部历史和媒体的物理清理尚未完成，不能等同于数据全部擦除。
- 工作区列表支持权限内分页，按页批量查询节点、边、时间线、导演与个人状态；启动时不再重复逐图读取。列表计数和内容都按本人权限限定。
- 视口串行独立保存，字段比较更新不推进图版本；旧图提交不会覆盖新位置，未知响应复用原请求，跨窗口冲突保留本机位置并停止自动覆盖。
- 外观（深浅/自定义主题、点/线/空白背景和图片信息）独立保存并在刷新后恢复；按本人外观字段比较，不推进图版本、不生成作品历史，旧图保存与历史恢复不能覆盖新外观。
- 真实浏览器联调发现源 CSS 隐藏 Toast、宿主 HTTP 丢弃未知错误正文。已按已知画布错误码映射安全提示，并用原顶栏保存状态/弹层展示图保存和个人设置的进度与失败；默认成功态图标保留原视觉。
- 嵌套私人投影改为稳定 ID 匹配；成员插入、重排、删除分镜不会错配私人提示词或复活已删除的对象。无 ID 的条目仅按唯一公共内容匹配，歧义拒绝保存；旧位置数组保留数据并拒绝猜测映射。当前业务库三类私人状态表实际均为 0 行，无需旧数据转换，本次无 DDL。
- SSE 每次轮询重新认证、检查成员权限。撤权后前端停止对应会话继续写入并保留本机草稿；账号变化进入登录恢复。外部图更新只通过源刷新合并入口投影，避免直接二次覆盖节点。
- 个人模型目录读取与偏好 CAS 已接通，不向浏览器返回凭据。原版渠道编辑和协议执行仍未完成，目录修改当前明确拒绝，不能作为最终一比一差异予以接受。
- 资源 multipart / 8 MiB 分片上传、SHA256、预留身份、完成回执、真实尺寸/时长探测、鉴权字节读取与 Range 已接通 Python/MySQL/MinIO。资源进入共享图时直接发布本人同项目资源；私人字段不发布。file 类资源独立存储，当前 GLB 只验证字节存取。
- 修复真实选图时只写 IndexedDB 的问题：托管画布固定使用 Python 资源服务，保留源本地 UI 分支，IndexedDB 只作草稿/缓存。图片、视频和封面在异步读取前捕获画布范围，导航不会改变进行中的上传归属。
- 私人素材基础登记/批量读取/分页/收藏及生成筛选、分类创建/重命名/移动/删除已接入关系存储。分类名称和批量规则对齐源，分类删除保留素材；分类归属不在 JSON 中重复维护。新增原版 `/assets` 伴随路由，完整素材操作仍在验收。
- 已有画布的资源保存归一化已接通：同项目直用，个人/跨项目文件按目标稳定身份复制；原版素材弹窗插入、等待期间继续编辑、撤销重做及提交失败刷新后的原请求重试已验证。首次整图创建、托盘与跨画布剪贴板专项操作仍未验收，不能以保存适配覆盖这些差异。
- 原版个人资产页已通过真实分类操作：创建、移动、刷新恢复、重命名、删除分类后回到未分类。浏览器检查同时要求 storageKey、assetId 和实际素材分页记录，不能仅凭图片可显示就认定登记成功。
- 素材登记、上传会话与自动保存的两处并发死锁均实际复现 MySQL 1213，回归从 409 失败转为通过。素材写入与上传身份预留复用按账号划分的 advisory lock；移除这两处 users 父记录行锁，保留幂等身份、上传配额与原有权限检查。
- 移植源 `asset/library_canvas_guard.go` 的节点/时间线资源优先级与绑定校验：可移除未使用封面，仍被该素材绑定的资源不能移除；素材引用关系随更新同步增删，文件本身不立即删除。持有项目锁并使用当前读取，已验证较早开启的请求也能识别后来提交的图引用。
- 引用索引补充源/宿主资源 URL 和指定 bare-ID 字段识别，不发起外部请求。嵌套 assetId 纳入本人投影，避免成员看见或借用他人的私人素材键；节点、时间线、导演与嵌套绑定均有投影测试。仅外部 URL 的素材导入及完整旧 ID 映射仍未完成。
- 永久删除与回收站状态条件已接入 Python。当前作品、成员私人投影、历史和已实现业务中的资源引用受到保护；其他素材复用同一资源时保留文件。素材、上传和资源记录的删除与持久删除回执原子提交；对象存储回收失败/响应未知可重试，旧上传键不会复活资源。
- scheduler 已接入每 10 秒的资源删除回执处理和每小时的上传暂存清理，另有默认只读的显式脚本。回收 helper 已用隔离真实 MySQL/MinIO 验证；当前没有启动新的业务 scheduler，也没有在业务库批量清理，生产调度闭环仍待验收。
- 素材恢复、彻底删除、删除被共享图引用的素材时拒绝且保留文件，已通过原版界面与真实 Python/MySQL/MinIO 联调；新建被测回收站素材由真实 API 准备，上传至画布及分类动作仍由原版 UI 执行。
- 双上下文首次视口写入发现 MySQL 1062，根因是等待项目锁后仍使用较早的 REPEATABLE READ 快照。画布写入对子表显式使用当前锁定读取，权限复核也读取当前私人状态；新增确定性回归覆盖首次视口与作品保存并发，保留外观与独立版本，读取接口仍不加写锁。
- 六类素材 `data` 已按未修改的源 `parseAssetRecord` 补齐 Python 校验：必填地址字段、尺寸、字节数、MIME、时长、音轨标记、模型文件名和实体定义；同时补齐可选来源/说明/外部 ID/肖像认证类型、非空 ID 与合法分类。保留视频零尺寸、有限小数和诚实未知 MIME；不接受数字字符串、布尔数字、显式 null 或伪造缺省元数据。非法更新不会覆盖已存素材和引用。源解析器与 Python 使用同一组 73 条有效/无效样本，并单独验证非有限数字。
- 资源副本可继续使用原素材 `assetId`，实际副本加入素材引用保护；本人来源链通过读取信封恢复，其他成员不可见。源素材修复、缺失绑定及保存前匹配识别副本，保持原素材的 storageKey。真实浏览器已验证副本图读取、节点/时间线绑定、原版拖拽保存及刷新；副本图由 API 准备，原版跨画布选择、拖入、粘贴的自动复制仍未接通。

## 实际数据库变更

本节为早期实际执行记录；后续增量与业务库最新 93 表 / 41 画布表见后文和数据库迁移记录，不以本节早期表数作为当前库状态。

已在当前业务库 `short_drama`（MySQL 8.4.11）实际执行 `projects.workspace_mode` 及 26 张画布表的 DDL；不是只生成 SQL。完整 schema 当前共 78 张表。

新增表：`project_canvases`、`project_canvas_settings`、`canvas_nodes`、`canvas_edges`、`canvas_write_receipts`、`canvas_revisions`、`canvas_user_states`、`canvas_node_user_states`、`canvas_revision_user_states`、`canvas_media_references`、`canvas_revision_media_references`、`canvas_user_media_references`、`canvas_timelines`、`canvas_director_scenes`、`canvas_workspace_user_states`。

资源和素材阶段另增加 9 表：`canvas_binary_resources`、`canvas_resource_uploads`、`canvas_resource_chunks`、`canvas_binary_references`、`canvas_user_binary_references`、`canvas_library_assets`、`canvas_library_asset_references`、`canvas_library_folders`、`canvas_library_folder_items`。

永久删除阶段增加 `canvas_resource_deletions`：在 `short_drama` 实际执行 `CREATE TABLE canvas_resource_deletions`、`CREATE INDEX idx_canvas_deletion_due`。执行前仅新表缺失且 `changed=[]`，执行后与最终只读复核均为 `ready`、`missing=[]`、`changed=[]`。新表保留私人上传身份、待删除定位值、重试进度与完成状态；没有改动既有上传 CHECK，也没有实际删除业务素材。

资源复制阶段增加 `canvas_resource_copy_sources`：实际执行 `CREATE TABLE canvas_resource_copy_sources`、`CREATE INDEX idx_canvas_copy_origin`，以及一条 `ALTER TABLE canvas_resource_uploads DROP CHECK ck_canvas_upload_mode, ADD CONSTRAINT ck_canvas_upload_mode CHECK (mode IN ('multipart','chunked','copy'))`。预检仅缺新表与已知 CHECK 升级，其他差异为空；执行后及本轮最终只读检查为 `ready`、`missing=[]`、`changed=[]`。完整 SQL 与 ORM 的 78 表一致性检查通过。没有复制或删除业务文件。

实际语句与重入步骤见[本阶段迁移](../数据库模型/migrations/2026-10-05-infinite-canvas/README.md)；ORM、完整 SQL、增量 SQL 和说明已同步。最新分类阶段应用实际执行两表及两个索引，返回 `status=ready`、`missing=[]`、`changed=[]`。资源阶段曾发现两个复合外键名称冲突，显式命名修正后安全重入完成；未删除已有表，单测新增全局外键名唯一检查。

锁、引用保护和私人投影修复本身没有新增表或 DDL；删除回执的新增另列于上文。修复嵌套 assetId 前只读检查 `short_drama`：canvas_timelines、canvas_director_scenes、canvas_nodes、canvas_revisions 均为 0 行，含 assetId 的行也均为 0，无需转换已有业务文档；没有读取或输出作品内容。其他已有部署若有旧共享 assetId，须先核对并显式迁移，不能把本机零行事实推广到其他库。

素材合同收紧前再次只读核对当前 `short_drama`：`canvas_library_assets` 为 0 行，无需转换不完整旧记录。本次仅修改校验与测试，没有 ORM、DDL 或业务数据变更。其他已有部署需先核验旧素材是否满足源合同，不能直接套用本机零行结论。

## 已实际执行的验证

本节为早期验证快照，保留当时的结果和边界；最新同版快速验证为 1637 passed，详见“暂停前最新证据收尾”。旧数量及截图不自动覆盖后续代码和依赖变化。

以下证据明确区分运行层次；后续改动应重跑受影响范围。

| 验证 | 结果与边界 |
| --- | --- |
| 后端 `tests/unit tests/api` | 副本来源响应后的最新 988 通过，206.77 秒；资源复制阶段为 988 通过，203.95 秒。新增来源表后补充父上传级联的明确断言，未放宽来源 FK。测试临时文件使用任务独立 basetemp，关闭 pytest 缓存写入，未修改系统权限 |
| 六类素材源合同对照 | 同一组 73 条样本分别运行 Python 模型与未修改的源解析器，覆盖接受/拒绝及规范化结果。Node 另验证非有限数字，共 74 测试通过；Python 加上非有限数字、uint64 资源身份与 URL 导入边界，共 81 测试通过，与相关引用/资源单测合计 119 通过。初始 65 样本阶段 Python 曾实际出现 53 失败，修复后通过；没有修改源解析器放宽合同 |
| 画布文档、资源与素材引用针对性单测 | 最新 50 通过；覆盖源/宿主资源定位、超大 ID、节点/时间线优先级、错误结构、稳定私人投影和嵌套素材键，未调用模型 |
| 真实 MySQL/真实认证画布测试 | 最新 15 通过；覆盖事务、回执、CAS、隐私、模式、分页批量查询、视口/外观独立保存、旧图保护和成员重排后的私人投影。使用任务自有临时 MySQL 与随机测试库，测试后清理 |
| 数据库 schema 针对回归 | 最新 105 通过；覆盖完整 SQL、约束与相关 DTO，包含新来源表级联关系；另有真实旧库 CHECK 升级、重入与未知漂移拒绝集成测试 |
| 后端 Ruff | 最新归一化阶段 `check src tests scripts` 与 `format --check src tests scripts` 全部通过，431 文件格式正确。此前完整 SQL 与 78 张 ORM 表核对一致；本轮未改 ORM/SQL |
| 宿主前端 | 归一化 DTO 后 `npm test` 191 通过，生产 build 通过，7.51 秒；本轮未修改标准模式功能 |
| 标准项目浏览器 | `project-redesign.spec.ts` 5 通过 |
| 模式创建浏览器 | `project-modes.spec.ts` 1 通过；验证默认模式、切换保留输入、503 后原键重试、独立入口。画布入口 HTML 是夹具 |
| 画布前端单测/类型检查 | 最新 `npm test` 117 通过；在已有来源/账号隔离合同上增加资源识别、冻结请求、分批失败重试、失效阻断及历史共享引用校验。source:check 为 802 文件、32 处明确适配；不等于完整原版操作验收 |
| 画布生产构建 | 归一化与日记恢复后的类型检查、完整 wasm/Worker 生产构建通过，Vite 13.20 秒；保留源较大 chunk 和构建插件耗时警告。来源阶段曾发现新代码 BigInt 字面量不兼容源编译目标，已改用十进制字符串范围比较，未升级目标或依赖 |
| 迁移版真实编辑器浏览器 | 1 通过：拖拽保存、刷新、独立视口/外观恢复、撤权阻断；HTTP 为夹具，不能替代 Python 联调或供应商验收 |
| 真实浏览器 + Python + MySQL | 新增 `test_canvas_browser.py` 实际 1 通过；临时账号真实登录，无 API 拦截，验证拖拽落库、刷新、外观恢复及两个窗口冲突保留/错误可见。使用随机测试库与临时 API，退出后全部清理。助手 status/ui-session 与 tasks 三个真实 404 被显式记录，未掩盖为完成 |
| 资源真实 HTTP / MySQL / MinIO | 6 通过，与原有画布测试合计 21 通过；覆盖上传、分片恢复、越权、发布、Range、真实 ffprobe、二进制历史引用、私人素材分页、暂存清理；丢失存储 ACK 为故障注入，不能称真实断网验收 |
| 分类、资源和原画布真实集成 | 最终代码下 39 项通过；另外两项标准素材 JSON 引用保护的测试准备路由修正后单独补跑 2 项通过，共 41 个场景。包含素材永久删除、恢复竞争、SQL 回滚、原上传身份墓碑、共享物理位置保护、并发清理、成员私人/历史引用、分片清理及首次个人状态竞争。标准 JSON 引用分别验证十进制字符串和历史数值。此前两处 1213 死锁、ONLY_FULL_GROUP_BY 与旧快照图引用回归仍通过，未放宽数据库模式 |
| 素材合同收紧后的真实集成 | 受影响的 `test_canvas_library.py`、`test_canvas_library_deletion.py`、`test_canvas_resources.py` 单次命令 25 通过，66.49 秒。新增真实 HTTP 拒绝非法新建/替换并保留原记录、时间戳、引用和 MinIO 文件；旧夹具补齐从真实上传返回的媒体字段。包含原版界面上传、分类、恢复、永久删除及删除占用拒绝的浏览器联调；测试使用随机库和任务专属 MinIO 桶，退出后清理 |
| 资源复制阶段完整受影响集成 | 复制、复制迁移、素材、永久删除、资源、画布 HTTP 和 workspace 共七文件，单次 55 通过，124.09 秒，包含正常动效下的原版资源浏览器。复制与迁移共 13 个场景：独立字节与发布、隐私、删除原文件、幂等并发与墓碑、响应丢失恢复、源/目标撤权、来源变化、完成 SQL 回滚、过期清理竞争、真实音视频，以及旧库升级和未知漂移。使用随机 MySQL 库及任务专属 MinIO 桶，退出后清理 |
| 副本身份与私人来源阶段集成 | 八文件整组中 65 个非浏览器场景通过，浏览器测试断言曾失败；修正测试的读取与比较边界后，该浏览器场景单独补跑 1 通过，37.25 秒。共覆盖 66 个场景，没有宣称一次命令全绿。新增 11 项验证节点/时间线、个人/项目来源、无关配对拒绝、ORM 越权拒绝、二次副本与中间撤权、绑定与替换/删除并发、历史保护，以及来源响应隐私与分页批量读取 |
| 保存归一化与恢复阶段集成 | 九文件单次命令 72 通过，170.50 秒，包含正常动效下的原版资源浏览器。新增六项后端场景覆盖同项目/个人/跨项目、稳定副本重试、撤权、删除墓碑、部分失败与并发；浏览器增加原版选择弹窗、继续拖拽、删除、撤销/重做、全新上下文恢复，以及未提交草稿刷新后原键原正文重试 |
| 资源真实浏览器 | 选图→真实存储→实际素材绑定→节点保存→全新上下文读取图片，分类创建/移动/刷新/重命名/删除，以及素材恢复、彻底删除、使用中删除拒绝全部通过，无页面异常。删除后在真实 MinIO 确认物理回收；使用中素材和字节仍存在。使用正常动效，报告明确记录助手/tasks 未实现 404 及预期的删除占用 409；减少动画下的源菜单缺陷另列，不算该场景通过 |
| 原版/目标成对对照 | `npm run test:parity` 已完成 10 个场景：桌面、选择、右键、拖拽、撤销、深色外观、浅色外观、线网格、自定义外观、窄屏均几何一致且像素差异 0。Chrome 154.0.8037.95、DPR 1、1440×900 / 390×844、减少动态效果，API 夹具固定。未覆盖正常动效、媒体/生成/助手实际执行，不是完整一比一验收 |

当前机器 `uv` 不可用，Python 检查实际使用 `backend/.venv/Scripts/python.exe`。浏览器实际使用 Chrome `C:/Program Files/Google/Chrome/Application/chrome.exe`，通过 `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` 传入。截图和临时日志位于忽略的 `.runtime/`，不作为产品源码提交。

永久删除阶段的实际日志：`.runtime/canvas-delete-fast-final.log`（907 通过）、`.runtime/canvas-delete-final.log`（39 通过，2 项测试准备路由 404）、`.runtime/canvas-delete-standard.log`（修正路由后单独补跑 2 通过）、`canvas-frontend/.runtime/resource-python-browser/report.json`（全部资源/分类/恢复/删除断言通过）、`canvas-frontend/.runtime/canvas-delete-build.log`。没有把失败的整组命令写成 41 项一次性全绿，也没有重新采集之前已通过的 10 个基础像素对照场景。

关键复现与修正：删除检查最初对关联表假定 `id` 列，复合键表触发 `canvas_resource_deletion_dao._reference → AttributeError: id`，已改用 `SELECT 1` 的存在性检查。确定性并发测试 `test_canvas_http.py::test_first_personal_writes_use_current_state_after_waiting_for_project_lock` 在旧代码返回 `1062 Duplicate entry … canvas_user_states.uk_canvas_user_state`，权限复核也曾读取旧快照而误报 404；修复后两种并发场景及原有 HTTP 测试共 6 项通过。浏览器恢复素材的最初夹具缺少源严格合同中的媒体字段，补齐真实资源元数据后通过；未放宽源解析器或改动原版 UI。

MinIO 回收失败和删除成功但响应未知均为故障注入，底层字节操作使用真实隔离桶；测试不等于真实网络断开或供应商验收。未调用付费模型，未运行新的业务 scheduler，标准宿主前端本轮没有修改，完整标准模式浏览器回归仍按后续阶段执行。

首轮对照实际发现托管启动预加载缓存、助手停靠宽度和远端恢复的时序差异，造成首次居中偏移。已在托管加载边界等待读取完成，且同画布后续读取保留实时视口；未修改源居中算法或 CSS。修复后上述六场景严格一致。对照脚本在存在像素差异、几何差异、页面异常或未实现测试响应时退出失败。

最新 10 场景对照已在资源存储适配后重跑通过。一次失败为测试夹具漏掉新增 GET /assets 请求，补齐明确空列表后整套通过，未放宽未实现请求断言。中间一次自定义外观有 18 像素差异，定位为外观浮层圆角边界的过渡；捕获稳定性检查补充浮层子控件的颜色、边框、阴影和变换后，10 场景再次均为 0 差异，没有放宽像素断言或修改源 CSS。

分类伴随路由加入后，10 场景成对对照再次全部通过。真实分类验收先暴露了素材登记 409，两条同步回归均确认底层 1213，已修复。之后的菜单定位问题分为：无障碍名称含箭头 `right`；原动画尚在调整几何时指针落到相邻项；减少动画下源 CSS 导致同步布局测量错误。前两者通过准确定位及等待实际命中区域稳定修正测试，没有改原控件或 CSS。

减少动画问题已同环境对照原版与目标：Chrome 154、1440×900 下，`styles/workspace-product.css:1235` 的 1ms 全后代 transition-duration 使资产菜单落到视口外，两边都复现；正常动效下两边定位正常。当前保留源 CSS，此已知源缺陷尚未修复，正常分类验收不能覆盖它。调试数据仅为临时测试账号/夹具，截图和菜单几何放在忽略的 `.runtime/`，不提交调试脚本或产物。

素材合同阶段日志：`.runtime/canvas-asset-contract-red.log`、`.runtime/canvas-asset-contract-fast.log`、`.runtime/canvas-asset-contract-integration.log`、`canvas-frontend/.runtime/asset-contract-source.log`、`canvas-frontend/.runtime/asset-contract-tests.log`。最新资源浏览器报告仍明确列出助手 `status/ui-session`、`tasks` 的 404 和预期的删除占用 409；页面异常为 0，不能由此宣称助手或任务已经迁移。本轮没有重跑构建、10 个像素对照场景或完整标准模式浏览器回归，没有调用模型供应商。

## 素材复用的继续接入依据

已重新阅读源 `use-canvas-upload.ts` 的素材选择、拖入与批量插入，以及素材修复、保存、资源服务调用链。源工作区跨画布共用同一 `assetId` 和 `resource:` 身份，没有独立的资源 copy API。目标的 `MediaFile/CanvasBinaryResource` 有项目范围，不能放开 `_replace_resource_refs` 的范围检查，也不能直接修改源素材的 `project_id`。

复制时需保留原素材身份与原版自动修复行为：仅新建资源并改 storageKey 会让 `canvas-asset-repair.ts` 判定素材与节点不匹配，产生新素材或错误重绑。后端独立副本与原素材的关联已实现；已有画布的保存自动映射和原版选择弹窗现已接通，详见后续“保存前资源归一化”记录。托盘、剪贴板、初次创建和整图复制仍需专项操作验收；不能以副本 API 或读取恢复验证代替这些原版操作验收。

继续核对到的额外接入点包括 `lib/canvas/canvas-node-asset.ts` 的素材查找与缺失绑定，以及 `project-asset-sync.ts::persistCanvasNodeAsset`；只适配 `canvas-asset-repair.ts` 不足以避免重复。后端素材替换检查现已覆盖原项目及已登记副本项目，永久删除沿这些实际资源关联检查当前/历史占用；来源 FK 在复制完成后释放不再导致漏查副本绑定。来源响应和前端身份匹配的验收另见下方记录。

保存接入必须在 `local-workspace-repository.ts` 的不可变提交日记建立前完成归一化，并保护异步期间的新输入；不能在原请求键下静默改写已排队 payload。已有画布现已遵循该顺序。初次创建/复制画布使用 `prepareInitialCanvasWrite` 固定首次请求，目标画布尚不存在时不能直接调用现有复制接口，也不能把先保存空画布当作原完整请求成功；这一分支仍是后续实施项，没有放宽校验来绕过。

### 独立资源副本基础

新增 `POST /api/v1/canvas-runtime/resources/copies` 和前端底层 `copyResourceToCanvas` 调用，详见[接口合同](../api/canvases.md#资源上传复制与读取)。来源可以是本人私人文件或可访问项目作品；目标必须是可访问的无限画布。复制始终分配独立文件与资源 ID，默认私人，进入共享图才发布。前端捕获目标画布和账号范围，保留调用方稳定键；本节记录的独立复制阶段尚无 UI 自动调用者，后续保存归一化通过同一复制服务完成接入。

后端短事务预留上传、目标身份和来源快照；复制使用正式的 `copy` mode，不伪造 multipart。持久来源 FK 保护尚在使用的原文件，MinIO I/O 不占用项目行事务；完成前重新核验源和目标权限、来源 metadata 以及目标位置/大小。完成时创建资源并释放来源 FK，来源删除或撤权不影响有目标权限的本人重放已完成请求。目标资源删除后保留原键墓碑。

复制与过期清理共用每上传独立的有界 MySQL advisory lock；到期清理可释放来源 FK，但保留原身份，来源未变化时同键可恢复。复制来源记录继承父上传的本人和目标项目范围，原始 ID/快照不可变，目标成员无法读取。新表来源外键同时接入既有删除保护，未放宽共享图的资源范围。

首轮 6 个复制场景通过，2 个迁移场景暴露 MySQL `4163 User-level lock name ... should not exceed 64 characters`。迁移锁对长数据库名改用固定长度 SHA256 摘要，短名保持兼容；随后 11 个场景通过。本轮补入复制过程中来源改变，以及最后一次 SQL 更新失败导致回滚的场景；最终与旧资源/素材/画布回归共 55 项一次性通过。SQL 和存储响应丢失均为故障注入，底层字节/事务使用真实隔离 MinIO/MySQL，不等于真实断网或供应商验收。

阶段日志：`.runtime/canvas-copy-first.log`、`.runtime/canvas-copy-second.log`、`.runtime/canvas-copy-integration-final.log`、`.runtime/canvas-copy-fast-final.log`、`canvas-frontend/.runtime/canvas-copy-tests-final.log`、`canvas-frontend/.runtime/canvas-copy-build.log`。最终 Ruff 检查 427 文件通过，`export_schema.py --check` 为 78 表一致，业务库仅只读复核。此阶段没有新增业务 scheduler、付费模型调用、完整标准模式浏览器回归或全画布像素对照；大于 5 GiB 的 SDK 复制路径只读过实际安装代码，未做真实大文件验收。

另外核对到源 `app/resource.go::ImportResourceURL` 在本地服务模式下明确返回“本地工作区不支持通过 URL 导入素材，请先下载到本机后上传”；浏览器本地图片链路另有 fetch 后上传路径。后续须按实际启用的运行分支对照，不能把源码中存在远端 import 方法等同于本地入口已经启用，也不能只新增 URL 下载接口就宣称这条原版操作一致。

### 副本身份、素材绑定与私人来源恢复

`CanvasResourceDAO.copy_ancestors` 按批次读取本人已完成且可见的复制来源。新复制把当时可见的完整来源链冻结进既有 `snapshot_json.resource_ancestors`；旧记录没有该字段时沿来源继续查询，防止重复与循环。中间项目撤权后，最终副本仍可匹配原个人素材；目标成员能读取已发布副本字节，但拿不到作者来源链。

画布提交在使用本人副本素材时先取得既有素材互斥锁，再进入画布事务。`bind_canvas_library_copies` 验证原素材声明资源与副本来源一致，增加实际副本引用；不增加第二条素材，也不改变原素材的 payload、项目或 storageKey。普通跨项目引用继续被拒绝。素材更新保留仍属于新声明来源的副本引用，按项目 ID 顺序锁定并检查当前图；已撤权的副本关联不借更新删除。关联增删最后统一 flush，保留旧 payload 用于校验合法的引用删除。

`my-document`、本人历史、完整 workspace 分页在信封中返回 `resource_aliases`；共享 GET 返回空映射。分页来源一次批量查询，再只分发每份文档实际引用的副本。前端在源逻辑接收文档前登记来源，以账号和登录 epoch 隔离，迭代合并和查询，整批验证后才修改缓存。来源只用于判断素材身份，不能授予访问权或写回共享图。节点素材查找、历史缺失绑定、源素材修复和强制重绑均使用这一对应关系。

真实浏览器追加场景通过认证 API 创建副本图，包含沿用同一 `assetId` 的图片节点和时间线 directMedia。全新浏览器上下文从真实 Python/MySQL/MinIO 恢复图片，再通过原版按需素材读取和实际匹配/修复/保存函数验证：没有新增素材，未改动原素材 storageKey，节点与时间线绑定保持。随后实际拖拽节点、等待原版保存、刷新并确认位置和绑定均保留。图的初始插入由 API 准备，此测试没有验收原版素材选择、跨画布拖入或粘贴的自动复制。

关键红绿验证：`.runtime/canvas-alias-source-red.log` 中缺失绑定调用新建回调 1 次，期望 0 次，暴露 `canvasNodeResourceId/assetResourceId` 仍直接按不同资源 ID 比较；接入来源身份后，新建次数为 0。测试本身先后修正了异步轮询、素材按需加载、摘要列表不含 data，以及把视口/同步字段计入异步正文比较的问题。同步修复仍严格比较整个对象不变；异步保存用源 `sameCanvasDocument` 判断正文不变，另对实际资源和 assetId 做明确断言，没有隐藏失败接口或放宽重复素材断言。

最新日志：`.runtime/canvas-alias-verified.log`（65 通过，浏览器比较断言失败）、`.runtime/canvas-alias-browser-final.log`（修正该测试后独立 1 通过）、`.runtime/canvas-alias-fast.log`（988 通过）、`canvas-frontend/.runtime/canvas-alias-node-final.log`（109 通过）、`canvas-frontend/.runtime/canvas-alias-build.log`、`.runtime/canvas-alias-host-tests.log` 和 `.runtime/canvas-alias-host-build.log`。资源浏览器报告仍显式记录助手 status/ui-session、tasks 的 404，页面异常为 0；未调用付费模型。

本阶段没有 ORM 或 DDL 变化。已只读复核当前业务库 `short_drama` 为 `ready`、无缺表或结构漂移，完整 SQL 与 78 张 ORM 表一致。未重跑 10 个基础像素对照、完整标准模式浏览器回归或真实模型供应商验收；这些后续工作仍不能省略。

### 保存前资源归一化与未提交草稿恢复（2026-10-06）

新增 `CanvasResourceNormalizationService`、`POST /api/v1/canvas-runtime/resources/normalize` 及前后端 DTO。同项目文件直用；个人文件和其他项目作品调用既有独立复制服务，稳定键由作者范围、目标画布和输入资源 ID 确定。分批重排、部分失败、刷新和响应未知不重复分配身份，目标撤权和删除墓碑仍拒绝；最后重新复核全部目标资源。已有六个真实 MySQL/MinIO 场景先因旧接口 405 失败，新增接口后通过。本阶段没有 ORM 或 DDL 变化，也没有执行业务库迁移。

已有画布保存先重放原 `inFlight`，再冻结下一次图请求并归一化，每批最多 200 个资源。所有映射及来源校验成功后才建立新的不可变提交；复制等待期间只映射实时状态中仍存在的相同定位值，不覆盖位置、文字、媒体替换，不复活删除。原素材 `assetId`、payload 和 storageKey 均保留。两批中的第二批失败不会交付半份图，重试使用同样的资源批次。

原版编辑器 React 状态与 Store 各有一份实时图，仅改 Store 不足以回填。新增资源映射通知把同一映射应用到当前编辑器、观察基线和历史补丁。按对象及字段语义复用同一次映射的结果，保留源历史依赖的共享引用；不把资源复制记成用户操作，也不清空重做栈。原版撤销会把外观规范化为内容相同的新对象，资源投影的额外渲染曾把它误记为外观编辑；仅在投影边界对等价外观对齐引用，保留实际外观修改与源撤销粒度。

浏览器真实操作首先发现“复制等待期间删除 → 撤销删除 → 返回复制响应”后重做不可用，`.runtime/canvas-normalize-redo-red.log` 中断言为 `resource normalization must preserve the source redo stack: false !== true`。仅替换历史内资源值仍会因为源引用比较失败，诊断确认上述外观空编辑后修复；`.runtime/canvas-normalize-redo-green-c.log` 独立场景通过，后续 72 项整组也通过。临时历史探针已移除，诊断产物留在忽略的 `.runtime/`。

另一个真实复现是：文件已复制，图提交被注入 503 拒绝，刷新保留了副本 ID，但服务端空图没有副本来源，源素材匹配返回 `undefined`。现将归一化返回的本人来源先校验并写入本机画布提交日记 `resourceAliases`，再投影副本定位；恢复同账号日记时在编辑器/素材修复前恢复身份关系。运行时仍按账号和登录 epoch 隔离，日记字段不会进入共享图、HTTP 正文或服务器回执，也不授予资源权限。浏览器从红到绿验证匹配原素材、未新增素材、Ctrl+S 使用完全相同的键和正文重试，日志为 `.runtime/canvas-normalize-recovery-red.log` 和 `canvas-normalize-recovery-green.log`。

原版弹窗测试只通过认证 API 建立空目标项目和主画布，图片节点全部由“从素材库插入”产生。验证独立文件及相同字节、原 assetId、复制期间拖拽与删除、撤销重做、自动保存和全新浏览器上下文恢复。延迟场景持有真实 HTTP 响应；恢复场景由 Playwright 在图提交送达前注入 503，成功重试与资源 I/O 使用真实 Python/MySQL/MinIO。二者属于故障注入，不是实际网络中断，也不是模型供应商验收。

最终验证记录：

- `.runtime/canvas-normalize-all.log`：九文件真实集成 72 通过，170.50 秒；随机 MySQL 库和任务专属 MinIO 桶已清理。
- `.runtime/canvas-normalize-fast-final.log`：后端 unit/api 988 通过，194.37 秒。初次默认临时目录报 `PermissionError: [WinError 5] .../Temp/pytest-of-coderedma`，23 项在 setup 阶段失败；改为工作区独立 `--basetemp` 后整组通过。残留的是 pytest 缓存目录权限警告和既有依赖弃用警告。
- `canvas-frontend/.runtime/canvas-normalize-node-final.log`：117 通过；`canvas-normalize-build-final.log`：完整构建通过，13.20 秒。Ruff 检查/格式 431 文件通过，source:check 为 802 文件 / 32 处登记适配。
- `.runtime/canvas-normalize-host-tests.log` 和 `canvas-normalize-host-build.log`：宿主 191 测试及构建通过，7.51 秒。
- `canvas-frontend/.runtime/canvas-normalize-parity.log`：本轮改动后重跑 10 个既有基础场景，几何一致、像素差异均为 0；API 仍是对照夹具，减少动效，不能外推为完整操作或供应商验收。

资源浏览器报告继续明确记录助手 `status/ui-session`、`tasks` 未实现的 404、删除占用 409，以及测试注入的 503；页面异常为 0。未更改标准模式流程、未新增依赖、未调用付费模型，也未执行完整标准模式浏览器回归。本阶段不覆盖初次 `revision=0` 整图创建/复制、托盘拖入、跨画布粘贴、外部 URL/本地键和特殊 URL 路径的完整归一化；这些仍是接续工作。

## 首次完整创建与原版复制操作（2026-10-06）

新增 `CanvasCreationService`，创建前固定原始请求、幂等键与全部来源身份。个人及跨项目资源在两张本人私有准备表中分配稳定目标 ID，短事务锁定来源和权限，事务外复制 MinIO 字节，再短事务确认副本与释放来源保护。全部文件准备好后，单一事务同时建立新项目（工作区创建）、画布、规范媒体、共享图、本人投影和回执；最终图失败时不留下可见空项目或半张图。现有项目内媒体直接复用，不更改私人资源的归属。

两个 POST 创建接口使用独立 `CanvasCreationRead` 返回摘要、`resource_map` 和本人 `resource_aliases`。回执保留原请求摘要，同键重试返回相同内容；后续 normalize 使用同一来源的稳定复制键，复用首次副本。前端保留原首次请求，先将来源持久化到日记，再映射确认图、实时图和历史，最后记录服务端版本与真实 workspaceProjectId。源多画布复制的后续画布仍以第一张源键组织，适配层解析其规范项目 ID，不让源键进入十进制项目路由。

过期准备清理接入 scheduler 和 `cleanup_canvas_uploads.py`。清理与完整创建共用准备记录的 advisory lock，删除字节前先把准备标记恢复成需重新复制，防止删除成功但确认丢失时把不存在的字节当作已完成文件发布；原请求与预留 ID 保留，重试重新核验来源。没有数据库行事务跨越创建或清理的 MinIO I/O。已完成创建不进入准备清理，规范文件按原引用及删除墓碑管理。

数据库三步已完成：ORM、完整 SQL 及 `003-canvas-creation.sql` 同步；在当前 `short_drama` 实际执行 `CREATE TABLE canvas_creation_attempts`、`CREATE INDEX idx_canvas_creation_expiry ON canvas_creation_attempts (status, expires_at)`、`CREATE TABLE canvas_creation_resources`。执行前只缺两表、`changed=[]`，执行后及只读复核均 `ready`、`missing=[]`、`changed=[]`。完整 schema 80 表，其中画布 28 表；本次未改写既有业务行或业务资源字节。

真实首测 `.runtime/canvas-create-red-b.log` 原先返回 `404 Canvas media is missing or outside the permitted scope`，现 `canvas-create-first-green.log` 通过。新增九项隔离 MySQL/MinIO 场景覆盖完整创建、同键重放、部分复制响应丢失、图事务回滚、清理响应丢失后恢复、同项目复用、私人权限、来源撤权前后不同状态、目标撤权、并发创建与清理互斥以及准备身份不可变；迁移用例另验旧结构补齐、重入和未知 CHECK 漂移拒绝。

浏览器验证发现并修复了两个真实接线缺口：`/canvas` 仍跳回宿主，现恢复原版画布列表页；顶栏“复制画布”原先只写本机缓存，现复用原菜单并等待 Python 创建成功后提示。只读“复制项目”同样先保存后导航，失败保留副本与就近错误。浏览器 `.runtime/canvas-create-browser-f.log` 曾因原菜单执行后没有 POST 超时；接线后 `canvas-create-browser-g.log` 通过，65.21 秒。脚本同时按源的悬停行为显露当前画布的操作按钮，不强制点击被勾选图标遮挡的按钮。

原版项目重命名还暴露了宿主项目 ID 与画布源键不同的问题：源代码用 workspaceProjectId 调用 Store.renameProject，找不到目标。现用源的分组顺序选出主画布源键，重命名后接入串行保存；验证范围加入原版标题编辑与服务端确认。

浏览器从已通过原上传操作建立的图片画布出发，实际点击“创建副本”“复制画布”，再复制含两画布的项目。验证新项目独立字节、位置、原 assetId、同项目媒体直用、多画布归属、作者来源回执及全新浏览器上下文恢复。没有用 API 写入目标图替代复制菜单，也未调用付费模型。源列表的分类/封面/回收及其它伴随入口仍需逐项验收，不能因为接通列表路由就宣称页面全部完成。

最终验证：`.runtime/canvas-create-all.log` 的十一文件真实集成 **83 通过**，200.27 秒，包含新增重命名和全部复制浏览器场景；随机 MySQL 测试库、任务专属 MinIO 桶与自建联调进程已清理。`.runtime/canvas-create-fast.log` 为后端 unit/api **989 通过**，196.29 秒；既有 Starlette 弃用和 pytest 缓存目录权限警告仍存在，无测试跳过。画布 Node **119 通过**、宿主 Node **191 通过**，双方生产构建均通过；最新画布代码构建记录为 `canvas-create-lifecycle-build.log`。

Ruff check/format 为 **438 文件**；source:check 为 **802 文件 / 32 处登记适配**。最后代码变更后的 `canvas-create-parity-final.log` 再次验证 10 个既有基础场景几何相同、像素差异 0，仍使用 API 对照夹具，不能代替完整功能与供应商验收。`git diff --check` 通过，ORM/SQL 总表数核对为 80。未新增依赖、未提交 git、未调用付费模型，也未执行完整标准模式浏览器回归。

首创失败后的完整浏览器恢复还不能宣称完成：本轮已证明服务器固定原请求并可重放，但首次 POST 的 ACK 丢失、尚未发布的副本刷新后重新打开、等待期间编辑/导航/账号切换，以及只读入口复制仍需专门验证。静态核对发现 `openLocalCanvasProjectFromBackend` 在读取服务端之前尚未重放持久化的首次创建请求，403/404 又会冻结访问；需要区分确有本人首次创建记录的草稿和被撤权的已发布图。原版列表复制回调的错误反馈也需核对，不能把刷新后恢复已有画布的验证外推到这一分支。完整目标保持进行中。

## 2026-10-06 首次创建与整组复制的刷新恢复

新增只读的首次请求读取边界；没有记录时不新建，无法解析、身份不一致或非零版本记录均保留并报错。首次发送前同时保存原请求与本机草稿。重新打开及工作区批量恢复先在画布锁内重放原请求，再读取、合并服务端图；只有媒体映射、本人来源和实时草稿已落盘才清除首次记录。服务端成功但 ACK 丢失时仍使用原 source_key、幂等键与正文，后续输入另行提交，避免把新旧资源 ID 误判为外部编辑。

原版列表“创建副本”先冻结全部画布草稿，再依次创建；刷新按第一张画布的源键依赖恢复后续画布。保留源菜单、排序、文案和成功后的入口，增加操作中的反馈、同入口重复点击保护及异常捕获；账号切换后不继续成功导航。多个 POST 仍是逐画布事务，不能称作整组数据库原子提交。首次创建的 403/404 可能来自复制来源，不据此写入目标撤权标记；已有的目标撤权标记仍阻止提交，服务端始终重新检查权限，GET 404 不能变成创建授权。

两项浏览器红测先复现：`canvas-create-recovery-red.log` 在原版菜单复制失败后刷新，没有原请求重试，`verify-creation-recovery-python.mjs:33` 等待响应超时；`canvas-create-group-red.log` 在两画布项目复制第一张 ACK 丢失后，断言待恢复草稿数为 2，实际仅 1。两者现均通过。

真实浏览器验证使用原版复制菜单，成功的请求与媒体全部经 Python、随机 MySQL 测试库和 MinIO；只由 Playwright 注入“POST 送达前 503”和“成功 POST 后把回执改为 503”。校验请求键与正文完全相同、回执和目标项目相同、规范媒体恢复、没有假冲突；多画布在第一张回执丢失后恢复完整原分组。另以应用 Store 注入回执之后的标题和节点位置编辑，验证刷新后另行持久化，不能把此项称为真实拖拽轨迹验收。真实 API 归档目标后刷新，确认 GET 404、无首次记录、不产生新创建请求。所有脚本页面异常为 0。

本轮最终验证：

- `canvas-create-recovery-final.log`：真实资源浏览器与原有跨窗口冲突浏览器 **2 通过**，96.33 秒，无跳过；隔离数据库、测试桶和自建进程已清理。较早的 `canvas-create-recovery-crosswindow.log` 因漏设显式浏览器开关跳过 1 项，不作为通过证据；最终命令补齐开关重跑。
- `canvas-create-recovery-node-final.log`：画布 Node **121 通过**；`canvas-create-recovery-e2e.log`：原版拖拽保存、视口、外观及撤权的 API 夹具浏览器 **1 通过**，不代表供应商验收。
- `canvas-create-recovery-build-fixed.log`：生产构建通过，13.78 秒。首次构建发现源 `updateProject` 不接受 `createdAt`，已改为新草稿建立时维护分组时间顺序，没有扩大源普通更新合同。
- `canvas-create-recovery-parity.log`：10 个既有基础场景几何一致、像素差异 0，使用固定 API 对照数据；source:check 为 **802 文件 / 33 处登记适配**。本轮新增的适配登记是原版列表复制存储边界。
- Ruff 对本轮唯一改动的 Python 测试文件检查与格式检查通过；本轮没有后端实现或表结构修改，无新 DDL。没有新增依赖、调用模型供应商或执行 git commit；没有重跑整套后端快速测试或标准模式完整浏览器回归。

首次创建恢复仍有专项场景需要继续核对：整组草稿准备期间浏览器存储失败/关闭、复制期间导航和账号切换的端到端操作、只读复制入口、跨窗口同时恢复同一初次请求。当前测试证明的是上述确定场景，不能外推为所有故障或完整一比一迁移已验收。

## 2026-10-06 整组准备的存储故障与页面会话边界

新增 `canvas-initial-group.ts`，以本人作用域的一份原子本机记录保存整组复制快照。在现有账号存储锁内建立逐画布首次请求，补齐尚未写入普通缓存的草稿，再清除整组记录。恢复时不覆盖已有草稿，不从缺失 GET 推断创建，不更换 source_key，也不增加服务端表或整组数据库事务。原有图缓存、提交日记与后端 MySQL/MinIO 仍各自负责原来的边界。

真实红测 `canvas-copy-prepare-red.log` 在第二张画布首次请求的 IndexedDB `put` 注入 `QuotaExceededError`；第一张可以恢复，第二张 POST 缺失，等待超时。修复后同场景通过；另外阻断普通画布缓存写入，证明即使复制草稿未写进通常的画布缓存，刷新也能根据整组记录恢复全部画布。测试在工作区启动完成后注入故障，避免把启动缓存失败误当复制准备失败；不是测试替身代替后端成功操作。

真实导航红测 `canvas-copy-session-red.log` 使用原版列表复制菜单，暂扣真实成功 ACK，再点击侧栏“资产”。旧代码在 ACK 到达后把 `/canvas-app/assets` 拉回 `/canvas-app/canvas/{副本}`。现列表维护挂载状态，进入画布的延迟回调也检查页面仍有效；账号变化后不显示原复制的成功或失败提示。原菜单与界面布局保持，已经发出的保存可以完成。

新增会话与只读验证：真实登录第二个测试账号，并显式调用画布已有的认证/本机会话恢复入口，再释放原账号的成功 ACK。新账号画布列表和本机草稿均为空，对原画布和副本媒体读取均为 404；没有旧回调跳转。切回原账号后重新加载，恢复原请求键、正文及同一回执。此项使用真实 Cookie/CSRF/账号权限，客户端会话切换由测试调用已有启动边界，不能称为宿主登录页面的完整 UI 回归。初次测试遗漏已有 Cookie 下登录所需的 CSRF 头，返回 `403 csrf_failed`；只修正测试请求，没有放宽应用鉴权。原版只读“复制项目”也实际点击通过，独立宿主项目、媒体映射和原 assetId 均在服务端确认后进入可编辑副本。

本轮验证记录：

- `.runtime/canvas-copy-session-green-c.log`：资源及完整复制/故障/会话浏览器 **1 通过**，98.95 秒；脚本各新断言为 true，页面异常为 0。使用随机隔离 MySQL 数据库和 MinIO，测试资源及自建进程已清理。
- `.runtime/canvas-copy-session-crosswindow.log`：原有真实 MySQL 跨窗口图保存与冲突浏览器 **1 通过**，20.99 秒；不等于两个窗口同时恢复同一首次请求已验收。
- `canvas-frontend/.runtime/canvas-copy-prepare-node.log`：**123 通过**；`canvas-copy-session-build.log`：完整生产构建通过，11.73 秒；`canvas-copy-session-e2e.log`：原有编辑器拖拽、独立偏好和撤权的 API 夹具浏览器 **1 通过**。
- `canvas-copy-session-parity.log`：10 个既有基础场景几何一致、像素差异 0；source:check 仍为 **802 文件 / 33 处登记适配**。对照使用固定 API 数据，不代表全部操作、动效和真实供应商通过。
- 本轮 Python 仅修改浏览器测试，Ruff 检查及格式通过；未修改业务 ORM、表结构或执行业务库 DDL，未新增依赖、未提交 git、未调用付费模型。既有 Ant Design/Starlette 弃用及大包提示仍存在；未重跑整套后端 unit/api 或标准模式完整浏览器回归。

后续仍需验证同时恢复同一首次请求的跨窗口竞争、编辑器只读复制过程中改变路由、托盘与跨画布剪贴板，以及完整创作功能和所有源/目标对照。以上阶段证据没有改变完整 M0–M5 的完成条件。

## 2026-10-06 素材托盘与跨画布图片剪贴板

新增独立浏览器入口 `verify-tray-clipboard-python.mjs`，避免把已有资源/复制恢复脚本继续扩成更长的一项。API 仅准备空项目和画布，来源图片通过原上传入口建立；目标图片由原托盘点击、HTML5 拖入产生，连线由原端口实际拖出，复制粘贴使用 Ctrl+A/C/V。没有构造目标节点或伪造系统剪贴板标记代替操作。复制库 4.0.2 异步写入原生剪贴板，测试等待真实标记落地后再切换画布，不修改原系统内容。

第一个真实缺口由 `canvas-tray-red-b.log` 复现：全新浏览器只有登录 Cookie，无素材缓存，服务端已有图片，但原托盘一直等不到“素材库 1”。新增 `host-canvas-tray-library.ts` 和 `use-canvas-asset-library.ts`，通过既有素材分页/投影恢复完整本人图片库。保留源组件、尺寸、搜索、点击与拖入，原内容区域增加加载、失败重试反馈。完整读取成功后才移除服务端已消失的旧缓存，不删除读取期间新上传或待提交的本机编辑；中途失败不交付虚假空库。账号变化或卸载取消旧请求。

第二个真实缺口由 `canvas-tray-clipboard-d.log` 复现：同一标签页真实登录另一个账号后，原全局 sessionStorage 节点槽把上个账号的图片节点插入新账号空图。后端拒绝原资源访问，但前端已出现旧内容。现以账号作用域保存节点槽并核对原系统标记；不读取旧无归属槽，内存副本捕获账号 epoch，账号变化清空副本与优先粘贴状态。保留源节点/连线重建和相对布局规则，没有放宽后端权限。

真实 Python/MySQL/MinIO 浏览器已验证：托盘点击与拖入自动保存、拖入位置换算、真实连线；同项目粘贴复用媒体，跨项目粘贴独立复制相同字节，原素材及 assetId 保留；新节点及连线 ID、相对位置、全新上下文图片解码与服务端图恢复；切换账号后不再恢复旧节点。分页场景通过 API 准备 101 项本人素材，原托盘读取第二页后能够搜索该页记录，删除一项再重开变为 100 项且不显示旧缓存。读取失败场景注入一次 503，其后的重试与保存使用真实服务和存储。

本轮最终证据：

- `.runtime/canvas-tray-pages-b.log`：上述独立真实浏览器 **1 通过**，46.17 秒；`canvas-frontend/.runtime/tray-clipboard-python-browser/report.json` 所列 9 个结果标记均为 true，页面异常为空。
- `.runtime/canvas-tray-regression.log`：原资源/整组复制恢复/导航/账号浏览器与原跨窗口冲突浏览器 **2 通过**，114.08 秒，无跳过；隔离 MySQL、测试桶与自建进程已清理。
- `canvas-frontend/.runtime/canvas-tray-node.log`：原有画布 Node **123 通过**；`canvas-tray-build-final.log`：生产构建通过，4.81 秒；`canvas-tray-e2e.log`：既有拖拽保存、本人偏好与撤权 API 夹具浏览器 **1 通过**。
- `canvas-tray-parity-final.log`：原 10 个基础场景加托盘展开、指针调高与“当前画布”标签，共 **13 场景**几何一致、像素差异 0。初次新增场景未等待托盘内高亮的 spring 动效，产生数千像素差异；补充内部稳定采样后还出现工具栏图标 1 像素差异，最终将工具栏内部样式也纳入同一稳定等待后通过。没有修改产品动效或放宽像素阈值。这些对照使用 API 固定数据、减少动效，不能代替完整运行或真实供应商验收。
- Ruff 对唯一改动的 Python 集成测试检查/格式通过，source:check 为 **802 文件 / 35 处登记适配**，`git diff --check` 通过。新登记的是托盘服务读取与节点剪贴板账号边界。

测试准备过程中还修正了三个测试自身问题：中文幂等请求头改为 ASCII、通过原“关闭助手”按钮腾出实际拖入区域、素材夹具使用真实输入字节数而非不存在的 `resource.bytes`。这些不计为应用故障。已有助手 status/ui-session、tasks 404 仍在报告中明确保留，未调用付费模型；Ant Design/Starlette 弃用与构建大包提示仍存在。

本轮没有后端业务实现、ORM 或 DDL 修改，无业务库迁移、无新依赖、无 git commit；未重跑整套后端 unit/api 或标准模式完整浏览器回归。当前新增的完整复制证据限于图片与连线，不能外推为绘图子文档、其它媒体和所有素材行为均已迁移。绘图持久化、媒体加工、四类生成、助手、时间线及 M0–M5 全部验收仍须继续。

收尾读取本轮真实提交记录还发现：托盘节点具有稳定 `resource:` storageKey，但展示字段 `metadata.content` 仍可能携带本页 Blob URL 进入提交正文；当前跨页及全新上下文恢复依靠稳定 storageKey 通过，不能据此宣称所有临时展示定位均已从持久化请求清除。后续媒体归一化需按字段语义处理该展示值，并同时核对首次整图创建、复制与服务端快照，不能通用替换正文中恰好出现的 URL。

## 2026-10-06 媒体展示地址与持久化身份

针对上一阶段发现的托盘 Blob URL 入库，新增前后端纯媒体规范化函数。普通保存、首次创建/复制、服务端图拆分和历史快照统一使用稳定文件地址；不修改实时编辑对象或提示词、文字正文。绘图预览、导演封面、裁剪来源和时间线 directMedia 按其字段语义处理。保留播放 variant、proxy 和片段，移除已识别资源地址的 origin/签名；未知字段不通用替换。主要 Blob/data 媒体没有稳定身份时拒绝新写入，临时派生预览没有身份则省略。

前端内容 hash、已确认基线和三方合并使用兼容规范快照，消除同一文件 Blob/URL 造成的假未保存和假冲突。旧图先叠加本人投影再规范读取，避免改变无 ID 数组的私人身份匹配；下次保存真实更新原图行。旧首次创建和在途提交按原键、原正文重放，不能改原幂等哈希。原始请求审计仍可能包含旧客户端展示值，但作品图和新请求已经按上述字段处理；这不等于任意外部地址、所有旧本地文件或未经审计的媒体字段都已迁移。

红测分别见 `.runtime/canvas-media-locator-unit-red.log` 和 `canvas-frontend/.runtime/canvas-media-locator-node-red.log`：原实现保留 `blob:page-preview`，与预期稳定文件地址不符。新增真实集成验证共享读取与底层图行、同一资源不同 Blob 不推进版本、旧行读取兼容及保存清理、历史恢复、孤立 Blob 拒绝、首次复制目标资源字节及 URL、原请求重放与同键换正文 409。原托盘浏览器额外检查所有新 commit 正文没有 Blob，保存确认后不存在假未保存。

最终验证：

- `.runtime/canvas-media-locator-fast.log`：后端 unit/api **992 通过**，198.11 秒；Ruff 全仓检查通过，格式检查 **440 文件**通过。存在 Starlette 弃用和 pytest 缓存目录权限警告，不影响测试结果。
- `.runtime/canvas-media-locator-integration-a.log`：新增媒体两项及扩展托盘浏览器 **3 通过**，48.99 秒；`.runtime/canvas-media-locator-all.log`：全部画布集成 **87 通过**，301.60 秒，无跳过。使用随机隔离 MySQL、MinIO 和真实 Python 服务，包含原完整复制恢复及跨窗口浏览器；隔离库、桶和自建进程已清理，不是模型供应商验收。
- `canvas-frontend/.runtime/canvas-media-locator-node-final.log`：Node **128 通过**；`canvas-media-locator-build-final.log`：最终生产构建通过，3.59 秒；`canvas-media-locator-e2e.log`：原编辑器 API 夹具浏览器 **1 通过**。既有 Ant Design 弃用及大包提示仍存在。
- `canvas-media-locator-parity.log`：**13 场景**几何一致、像素差异 0；source:check 为 **802 文件 / 37 处登记适配**。新增登记为内容比较与三方合并的媒体边界，没有修改源绘制、布局或操作算法；固定 API 数据对照仍不能替代完整功能与真实模型验收。

本阶段无 ORM/DDL 变化、无新依赖、无 git commit；未重跑标准模式完整浏览器。绘图持久化、媒体加工、生成、助手、时间线及完整 M0–M5 继续实施。

## 2026-10-06 原版绘图操作与子文档持久化

新增绘图 API、Service、DAO、Pydantic 契约及三张 ORM 表。沿用原 Excalidraw 窗口、工具与样式，绘图文档通过 Python/MySQL 保存不可变版本，预览及成品通过既有资源接口进入 MinIO。绘图版本对外为十进制字符串；保存按窗口持有的版本做 CAS，相同上次请求可重放；删除保留墓碑，不能以初始版本自动重建。每次访问复核当前画布及成员权限，保存作品共享，未保存草稿留在本人缓存。

真实红测 `.runtime/canvas-drawings-red.log` 首先暴露绘图 PUT 404。接入后，`canvas-drawings-browser-f.log` 发现 MySQL JSON 将笔画小数 `10.399993896484375` 变为 `10.399993896484377`，相同请求重试发生假冲突；现版本文档中只有一份精确 JSON 字符串快照，读取时解码，不通过近似比较掩盖坐标变化。`canvas-drawings-browser-g.log` 又复现两个同账号窗口共享缓存后，旧窗口借用新版本导致预期 409 实际 200；保存现使用调用者打开时持有的版本，保留旧窗口草稿及原串行队列。预览上传键增加画布和绘图身份，成品使用实际上传资源 ID。

数据库三步已完成：ORM、完整 SQL 及 `004-canvas-drawings.sql` 同步，在当前业务库 `short_drama` 实际执行 `CREATE TABLE canvas_drawings`、`CREATE INDEX idx_canvas_drawing_active ON canvas_drawings (canvas_id, archived_at, updated_at)`、`CREATE TABLE canvas_drawing_versions`、`CREATE TABLE canvas_drawing_media_references`。执行前只缺三表，执行后 `ready`、`missing=[]`、`changed=[]`，完整 schema 与 **83 表（其中画布 31 表）**一致。未改写旧业务行或删除业务文件，精确快照编码修复不涉及追加 DDL。

浏览器只通过认证 API 准备空项目，节点由原右键菜单创建，再双击打开绘图窗口，用真实鼠标绘制并点击“保存绘图”。校验预览 PNG 字节、成功回执丢失后的同文重试、全新浏览器上下文的精确笔画恢复，以及两个窗口的 409 和草稿保留。回执丢失使用一次 503 故障注入，成功请求使用真实 Python、隔离 MySQL 与 MinIO。菜单层级、未选画笔、radio 被图标覆盖是测试操作问题，不计为产品缺陷；`canvas-drawings-numeric-red.log` 实际通过，也不作为红测证据。

最终验证记录：

- `.runtime/canvas-drawings-browser-h.log`：绘图专项及迁移 **5 通过**，35.84 秒；`.runtime/canvas-drawings-all.log`：全部画布集成 **92 通过**，351.16 秒，无跳过，随机测试库、桶及自建进程已清理。
- `.runtime/canvas-drawings-fast-final.log`：后端 unit/api **996 通过**，204.75 秒；最终 Ruff 全仓检查通过，格式检查 **448 文件**通过，`export_schema.py --check` 为 83 表一致。
- `canvas-frontend/.runtime/canvas-drawings-node-final.log`：Node **129 通过**；`canvas-drawings-build-final.log`：生产构建通过，18.02 秒；`canvas-drawings-e2e.log`：既有编辑器 API 夹具浏览器 **1 通过**，17.95 秒。
- `canvas-drawings-parity-a.log`：原 13 场景加绘图创建菜单与空绘图窗口，共 **15 场景**几何相同、像素差异 0；source:check 为 **802 文件 / 44 处登记适配**。空窗口对照不能代替完整绘图工具及全部操作验收。

当前绘图删除保留全部版本及媒体引用；整图历史的冻结绘图绑定、原子恢复、节点/项目复制、跨画布粘贴和导入导出尚未接通，完整工具、图像导入、生成引用及版本回收仍需验收。浏览器报告继续列出助手 `status/ui-session`、`tasks` 的未迁移 404；页面异常为空不等于这些功能已实现。未新增依赖、未提交 git、未调用模型供应商、未执行标准模式完整浏览器回归；既有依赖弃用和大包提示仍存在。

## 2026-10-06 绘图历史冻结与原版恢复流程

新增 `canvas_revision_drawing_references`，将整图快照绑定到其声明的不可变绘图版本，并保存该版本的稳定预览定位。普通历史不读取最新笔画替代旧图；恢复前备份当前已保存作品，图、绘图新版本、媒体引用及回执在同一事务提交。前版历史仅按已有明确 drawingId/drawingRevision 兼容读取，缺少版本时拒绝完整恢复，不猜测回填。版本保留策略尚未启用，全部绘图版本继续保留；未来回收前必须完整核对旧历史绑定。

源码明确历史界面只预览图片、不开放历史笔画编辑；已保留该布局与只读操作，不增加历史编辑入口或更改历史下载的源行为。服务端冻结与恢复属于已定方案的存储一致性边界。原版列表捕获绘图版本和墓碑，恢复透传该前提，即使图版本未变也不能覆盖列表打开后另存的笔画。恢复后确认并重读，解除本次恢复涉及的本机删除标记；私人冲突草稿始终保留自己的 CAS 来源版本，不能从最新缓存静默取得覆盖资格。

`.runtime/canvas-drawing-history-red.log` 首先真实复现历史缺少绘图预览引用与恢复绘图前提。实现后验证了已删除绘图恢复、成员共享/非成员拒绝、恢复回执重放、列表后笔画改变的 409、回执写入失败时图与子文档全部回滚，以及 MySQL 的版本 RESTRICT 和历史绑定 CASCADE。普通 Actor 不能原地改写已保存版本或历史绑定。初次综合测试 `canvas-drawing-history-a.log` 运行在尚未重新导出完整 SQL 的测试基线上，缺新表导致后续清理失败；这是验证启动顺序错误，完整 SQL 同步后重跑，不能计为业务故障。

浏览器使用原鼠标笔画、版本记录列表、只读预览、恢复确认、Delete 及重新打开操作，验证旧图片不会被后续笔画改变、成功恢复回执被注入一次 503 后同键同正文重试、删除后恢复笔画、全新上下文恢复及原两窗口冲突。`canvas-drawing-history-browser-a.log` 的失败发生在所有历史操作已通过之后：原编辑器重新打开再关闭会把 Excalidraw 的 `lastCommittedPoint` 序列化为 null，测试却与更早的首次保存比较。现全新上下文严格比较最近实际保存的完整快照，并另行检查原不可变版本没有改变；没有修改源绘图算法或放宽坐标比较。

数据库已在当前 `short_drama` 实际执行一条 `CREATE TABLE canvas_revision_drawing_references`，见 `canvas-drawing-history-db-before/apply/after.log`。预检只缺该表，无结构漂移；执行后再次只读检查为 `ready`、`missing=[]`、`changed=[]`。ORM、完整 SQL、`005-canvas-drawing-history.sql` 及说明已同步，当前 **84 表，其中画布 32 表**。没有改写旧业务图、历史行或媒体字节。

这一阶段最终证据：

- `.runtime/canvas-drawing-history-c.log`：绘图、历史、约束及迁移 **11 通过**，80.95 秒；`canvas-drawing-history-all.log`：当时全部画布集成 **98 通过**，376.66 秒，无跳过。随机测试库、MinIO 测试桶和自建进程已清理。
- `canvas-drawing-history-fast.log`：后端 unit/api **996 通过**，192.70 秒；Ruff 全仓检查和 **451 文件**格式检查通过；完整 SQL 与 **84 表**一致。
- 画布 `canvas-drawing-history-node-b.log`：**130 通过**；`canvas-drawing-history-build-final.log`：生产构建通过，3.16 秒；`canvas-drawing-history-e2e.log`：既有编辑器 API 夹具浏览器 **1 通过**，16.93 秒。
- 宿主 `canvas-drawing-history-host-node.log`：**191 通过**；`canvas-drawing-history-host-build.log`：构建通过，7.53 秒。完整宿主浏览器回归另行运行，尚不能由这两项宣称浏览器已通过。
- `canvas-drawing-history-parity-b.log`：**17 场景**（新增版本列表与只读历史预览）几何一致、像素差异 0；source:check 仍为 **802 文件 / 44 处登记适配**。初次对照仅空绘图窗口阴影边缘 4 个像素差一级灰度；加入实际截图连续稳定检查后通过，没有改源 CSS 或放宽零差异阈值。

报告仍明确列出助手 status/ui-session、tasks 的未迁移 404 和预期的冲突/故障注入，页面异常为空；依赖弃用、大包及 Excalidraw 初始化提示仍存在。未调用付费模型、未引入依赖、未执行 git commit。随后继续补其它浏览器保留删除缓存时的恢复场景；上述 98 项不包含这一新增场景。

## 2026-10-06 跨浏览器绘图恢复与宿主生产回归

原浏览器删除绘图，另一个全新浏览器上下文仅沿用登录 Cookie 并通过原版历史界面恢复，然后原浏览器重载并打开绘图，真实复现 `AssertionError: server restore must supersede another browser's acknowledged deletion cache`、实际返回 null，见 `canvas-drawing-tombstone-red-b.log`。首次 red 日志实际是绘图关闭后自动保存尚未完成导致版本冲突，不能作为墓碑故障证据；历史测试现通过原 Ctrl+S 等待提交确认。

DELETE 现在返回确认删除的十进制版本，本机缓存将它与删除标记一起保存；只有严格较新的服务端活动绘图才能解除墓碑，并在本机锁内重新检查。私人草稿仍持有自己的旧 CAS 版本。旧缓存无删除版本、删除回执未知或并发删除/恢复的完整幂等语义尚未解决，不通过猜测放开缓存。没有修改原绘图布局、CSS 或工具算法，没有新增 DDL。

`canvas-drawing-tombstone-green.log` 为绘图、历史、迁移及扩展浏览器 **11 通过**，94.37 秒；之后 `canvas-drawing-tombstone-stale.log` 再验证删除后注入旧版本及相同版本 GET 均不能复活绘图、另一上下文真正恢复后可重新打开，绘图专项 **4 通过**，62.36 秒，无跳过。成功请求使用真实 Python/隔离 MySQL/MinIO，过期 GET 是明确的故障注入，临时库、桶与自建进程已清理。画布 Node **130 通过**、构建 **29.39 秒通过**；Ruff **451 文件**通过，受影响后端契约/模型测试 **9 通过**。此前 996/98 项和 17 场景对照没有在墓碑补改后重复运行，不将它们记为本补改的重跑结果。

宿主 `canvas-drawing-history-host-e2e.log` 为 **227 通过、3 跳过**，9.7 分钟；这 3 项显式要求生产构建。使用已构建的 dist 启动独立 4175 preview，`canvas-drawing-history-host-production-b.log` 补跑 **3 通过**，8.1 秒，无跳过，结束后停止自建 preview。首次生产调用以 `^production` 匹配完整测试名导致 `No tests found`，去除锚点后正确执行；不是产品失败。这些宿主测试使用 API 夹具，不能记为真实模型验收。PowerShell 将测试进程正常 stderr 警告包装为 NativeCommandError，外层退出码为 1；测试日志和 Playwright/pytest 汇总实际通过，无失败断言。

源登记为 **802 文件 / 44 处适配**，补充绘图删除版本和缓存恢复原因；没有新依赖、没有 git commit、没有调用付费模型。下一步继续可编辑绘图副本、跨画布粘贴及归档流程。

## 2026-10-06 绘图节点变体与跨画布粘贴

新增真实浏览器步骤：原 Ctrl+C/ Ctrl+V 从已有笔画粘贴到同项目另一画布及另一项目，打开全新浏览器上下文读取完整 snapshot，实际画第二笔保存并核对来源未变；原右键“创建参数变体”验证新子文档、原 36 像素位移，再 Delete 副本确认来源保留。预览与成品逐字节比较，目标有独立资源 ID，成品 storageKey 与自身资源 ID 一致。

`canvas-drawing-copy-red.log` 首先复现 `画布自动保存未完成：drawing paste in another canvas of same project`；失败页面显示副本 0 个图形，原因是 clone 用目标画布键查来源子文档。现在账号隔离的剪贴板记录来源画布键，clone 从来源读取笔画/预览/成品并按目标画布上传。回填检查账号、目标 drawingId 及版本，只更新仍对应的较旧节点，不覆盖新编辑；原节点菜单、快捷键、命名与相对位置算法保持不变。

`canvas-drawing-copy-green-a.log` 中新增粘贴与变体步骤已通过，后续原冷启动测试点击来源中心时被偏移 36 像素的副本挡住；这属于测试操作问题。增加上述副本删除验证后，`canvas-drawing-copy-green-b.log` 为绘图、历史、迁移及跨窗口浏览器 **12 通过**，118.01 秒，无跳过。使用真实 Python、随机隔离 MySQL 和 MinIO，任务专属基础设施已清理。报告中的新四个结果标记均为 true，页面异常为空，助手/tasks 未迁移 404 继续明确保留。

画布 `canvas-drawing-copy-node.log` **130 通过**、`canvas-drawing-copy-build.log` 构建 **16.98 秒通过**。`canvas-drawing-copy-parity.log` 重跑 **17 场景**几何一致、像素差异 0，仍为固定 API 数据对照。受影响 Python 测试的 Ruff 检查/格式通过，source:check 为 **802 文件 / 44 处适配**。本阶段没有后端业务代码、ORM/DDL 或依赖变更，未重复运行后端 996 项或宿主 230 个夹具场景。

`canvas-drawing-copy-resource-regression.log` 追加原资源复制/恢复浏览器及素材托盘/图片剪贴板浏览器 **2 通过**，136.67 秒，无跳过，隔离库、桶和自建进程已清理；账号切换后的剪贴板保护及图片原流程未因本次来源字段而回归。

以上覆盖节点复制，不能推断整画布/整项目复制、归档或源同时编辑期间的笔画与派生图一致性均已通过；源复制时读取最新笔画的并发边界还需核对。旧剪贴板没有来源键时维持原同画布规则，不猜测跨画布来源。继续接通首次整图创建中的绘图子文档。

## 2026-10-06 首次整图创建与完整绘图复制

两个创建接口增加可选 `drawing_documents`，复用绘图写入契约。新建笔画使用初始 revision `"0"`，图中引用目标版本 `"1"`；缺少非空笔画、重复/游离 ID、错误版本或不合法元数据均拒绝，完整图与笔画合计限制 32 MiB。空清单与省略保持旧无绘图请求的摘要输入，既有回执无需重新生成。新清单不进入共享图或普通图提交。

创建服务把绘图预览、成品及 `files.*.dataURL` 中稳定图片纳入原媒体准备、权限检查与复制映射。一次发布事务同时建立画布、绘图、精确快照、媒体引用、图和回执；绘图写入后故障也会全部回滚，原请求重试继续使用已准备的资源。仅按明确的资源字段映射身份，不通用替换文字或内嵌图片字节。没有新增 ORM 表或 DDL，完整 SQL 与现有 **84 表**仍一致；本阶段不需要业务库 DDL。

前端保留原项目库整组复制、顶栏复制画布及只读复制项目入口。复制前冻结节点，每份笔画及其派生图通过一次服务端读取捕获；目标绘图清单先存入本人首次请求/整组记录，再随首个创建请求交付。确认及实时图只保留正常文档，资源映射校验同时覆盖子文档。多画布项目依然逐张原子创建，不能宣称整个项目是一个数据库事务。

红测 `canvas-creation-drawings-red.log` 有三项失败：原接口拒绝绘图清单，缺少清单的非空绘图反而返回 201。接入后 `canvas-creation-drawings-green-a.log` 新旧首次创建 **12 通过**，27.79 秒，包含精确浮点、嵌入资源地址映射、独立预览/成品、作者权限、相同请求重放和写入中途回滚。单元测试另验证不合法 JSON 元数据返回 ValidationError，以及新增空字段不改变旧摘要。

真实浏览器 `canvas-creation-drawings-browser-a.log` **1 通过**，105.76 秒，增加原项目库复制两张带不同笔画的画布、原顶栏复制、只读页复制、资源字节检查和全新上下文打开。两张画布及其子文档保留分组，跨项目文件独立、同项目文件复用，节点与子文档可立即读取。此前绘图保存、历史恢复、删除缓存、节点粘贴、变体和两窗口冲突继续在同一次场景运行。页面异常为空，助手/tasks 未迁移 404 仍明确记录，未调用模型供应商。

最终验证：

- `canvas-creation-drawings-all.log` 全部画布集成 **99 通过、2 setup 错误**，413.75 秒；两项音视频临时文件测试因系统 `Temp/pytest-of-coderedma` 的 WinError 5 失败，尚未执行到业务。验证运行器现默认使用工作区内随机独立 basetemp，`canvas-creation-drawings-media-b.log` 补跑这 **2 项通过**，8.58 秒，无跳过。101 个集成场景均获得成功执行证据，但不是一次 101 全绿的运行。每次随机数据库、测试桶和自建进程已清理。
- `canvas-creation-drawings-fast.log` 后端 unit/api **999 通过**，199.91 秒；Ruff 全仓检查及 **454 文件**格式检查通过，`export_schema.py --check` 为 **84 表一致**。
- 画布 `canvas-creation-drawings-node.log` **132 通过**；`canvas-creation-drawings-build-a.log` 构建 **13.05 秒通过**；`canvas-creation-drawings-e2e.log` 原编辑器 API 夹具浏览器 **1 通过**，17.17 秒。宿主 `canvas-creation-drawings-host-build.log` 构建 **8.07 秒通过**。
- `canvas-creation-drawings-parity.log` **17 场景**几何一致、像素差异 0；source:check 为 **802 文件 / 44 处适配**。仍是固定 API 数据的视觉对照，不能外推为全功能或供应商验收。

未新增依赖、未 git commit、未重复执行宿主 230 个夹具浏览器场景。后续仍需归档导入导出、节点复制中并发来源编辑的派生图一致性、未知删除回执和全套工具验证；本阶段未将未接入的归档流程计为完成。

## 2026-10-06 ZIP 绘图归档与未知回执恢复

新增独立真实浏览器场景 `verify-archive-python.mjs` / `test_canvas_archive.py`，由原右键菜单创建绘图、真实鼠标画线、项目库勾选后导出 ZIP，再交给无来源项目权限的账号通过原文件选择器导入。逐字节核对预览/成品，全新浏览器重新编辑，检查来源保持独立。另覆盖发布已成功但 503 回执丢失、刷新重放原请求，以及缺少 ZIP 图片时零上传/零发布。

初次 `canvas-archive-red.log` 因测试误用宿主项目名定位画布卡片而超时；改用实际画布标题后，`canvas-archive-red-b.log` 明确复现创建接口 `422: canvas creation is missing a drawing document`。导入现在先上传派生图片，在首次创建中同时交付笔画；同步确认后使用经过完整资源映射校验的确认文档进行读回核对，不放宽图内容比较。

`canvas-archive-green-a.log` 又复现同字节预览/成品不同文件名导致上传键冲突 `409`；上传键已加入请求声明摘要，明确使用本人资源作用域。`green-b` 在创建与绘图保存成功后因测试等待短暂消息的可见态失败，改为确认消息生成并继续核对真实数据；`green-c` 发现测试在源编辑器关闭前捕获旧 revision，改为等原关闭保存完成再捕获基线。以上失败均保留日志，没有将未完成断言计为通过。

`canvas-archive-green-d.log` 独立场景 **1 通过**，29.38 秒，无跳过。跨账号导入、全新上下文编辑、丢失回执的同键同文恢复及损坏归档前置拒绝均通过。导入失败后不自动删除可能已发布的作品，首次请求由现有恢复机制持久保留。

导出增加同版笔画/预览/成品读取；本机草稿按同一代缓存捕获。绘图引用的托管图片在归档快照中嵌入，保留精确浮点与文字，不再依赖原账号的受权 URL。新增两个纯契约场景验证该转换与缺失资源拒绝。并发浏览器在导出读取笔画之后、获取派生图之前，让另一原版编辑器实际画第二笔并保存，ZIP 仍保持第一次捕获的笔画和两份派生图；不是只校验调用次数。

`canvas-archive-image-a.log` 在混合普通图片后复现素材 PUT `404`：目标画布尚未创建，不能建立指向它的素材记录。现在本人首次请求保存 `initialArchiveBindings` 标记，创建成功、资源映射完成后再建立目标画布的私人素材及节点绑定；原请求在绑定和草稿落盘前保留，避免刷新只恢复图而丢失素材关系。该恢复字段不进入普通图或服务端创建 DTO。绑定过程中出现新编辑则拒绝覆盖。图库绑定使用后续正常图提交，不夸大为整个导入一个事务。

`canvas-archive-image-b.log` 混合图片/绘图完整场景 **1 通过**，38.20 秒，含原图文件字节、独立目标 ID、素材库和节点一致、未知回执刷新后的素材绑定。此前 `canvas-archive-regression-a.log` 绘图归档、绘图保存/历史和创建 **13 通过**，156.63 秒；恢复标记接入后的 `canvas-archive-creation-regression.log` 新旧首次创建与原绘图复制浏览器再 **13 通过**，113.14 秒，无跳过。二者范围有重叠，不能累加成 26 个不同场景。随机测试数据库、桶及专用进程都已清理。

当前 `canvas-archive-node-b.log` Node **135 通过**，`canvas-archive-build-image.log` 构建 **3.35 秒通过**，`canvas-archive-e2e-final.log` 原编辑器 API 夹具浏览器 **1 通过**，16.67 秒。受影响 Python 测试 Ruff 检查和格式通过；source:check 为 **802 文件 / 45 处适配**。未重复执行宿主完整浏览器和后端 unit/api 全集，本阶段无对应产品代码变更。

继续扩展真实 WAV 和由本机 FFmpeg 生成的 H.264 MP4：`canvas-archive-media-a.log` 复现视频缩略图 `sourceKey` 仍指向原账号资源，跨账号发布返回 `404 Canvas creation source does not exist`。补齐与主视频一同映射后，`canvas-archive-media-b.log` 完整混合场景 **1 通过**，49.51 秒，无跳过；PNG/WAV/MP4 原始字节、时长/尺寸/MIME、视频缩略图原始字节和来源键、三个素材库记录及未知回执刷新后的绑定均通过。最终 `canvas-archive-build-media.log` 再次构建通过。音视频仅验收这些具体样本，不外推到所有编码、时间线或导演台。

视觉对照本轮**未通过严格零像素验收**。`canvas-archive-parity.log`、`canvas-archive-parity-b.log` 与诊断运行的 17 场景几何一致；三个素材托盘场景同在 `(549,859)` 差 1 像素，RGB 为源 `(253,253,253)`、目标 `(255,255,255)`。绘图编辑器在部分运行另有 4 个灰阶差 1 的阴影边缘像素，另一次为 0。诊断已核对底栏 SVG 路径、属性、32 个元素的位置和计算样式相同，但尚未确定微小像素差异根因；不能据此标为通过。保留原阈值、差异图及诊断记录，没有修改产品 CSS 或掩盖截图区域。

本阶段未改后端业务模型、ORM、DDL、依赖或标准模式界面。实际浏览器仍记录 `/canvas-runtime/canvas-folders`、助手和任务入口未迁移的 404；无文件夹绘图归档通过不代表完整归档、项目文件夹或助手已经实现。

## 2026-10-06 本人项目文件夹与完整项目 ZIP

复现并补齐 `/canvas-runtime/canvas-folders` 原 GET/PUT/DELETE 合同。新增 `canvas_project_folders` 与 `canvas_project_folder_items`，分别保存本人的不透明文件夹键、名称、封面和墓碑，以及每名成员独立的画布归属。相同名称允许重复；名称最多 80 Unicode 字符；更新保留首次创建时间；墓碑阻止旧 PUT 复活。原页面、组件、菜单、CSS 和文件选择步骤未重写。

`folderId` 从共享根移到规范私人归属表，只在本人文档、本人快照和工作区摘要投影；成员互相看不到或覆盖分类。源客户端分页摘要接回 `folder_id`。图提交仍用原版本 CAS 与幂等键，移动产生图版本与历史；项目库按原流程逐张移动项目内画布。接口删除文件夹保留作品，清除本人分类；原版 UI 则先逐张把其中作品归档并登记本机回收站，再删除文件夹，未将两层行为合并。回收站恢复尚未验收。

封面上传显式使用本人资源作用域，避免依赖当前激活画布。封面外键参与既有资源回收检查，替换和删除释放旧引用；跨账号不能使用他人的资源。分类的专用账号锁与图创建/提交/恢复协调，先于项目行锁；媒体写入先锁资源再锁文件夹，避免与回收循环等待。被撤权项目仅清除本人关系，不读写作品。

红测 `canvas-project-folders-red.log` 明确包含 GET/PUT 404；同轮还有一处测试 fixture 参数用错，修正后继续。`green-a` 在完整 SQL 尚未同步时暴露缺表及清理失败，不计通过。同步 86 表完整 SQL 后，`canvas-project-folders-green-b.log` **7 通过**，18.94 秒，包括五个文件夹业务场景、迁移重入和既有素材/自动保存并发回归。

`canvas-project-folders-browser-a.log` 的两个真实 MinIO 场景通过，浏览器在重命名按钮定位失败；原 Ant Design 两字按钮含自动空格，修正测试定位。`browser-b` 在子菜单项的精确可访问名称匹配失败，改为源实际文字入口。`browser-c` 已通过首次移动和全新上下文恢复，但第二次移动时菜单从不稳定转为不可见；测试现等待上次原成功消息生成后继续，修正后完整流程通过，没有修改产品交互、关闭动画或强制点击隐藏菜单。该定位失败不单独作为产品缺陷结论。

`canvas-project-folders-browser-d.log` **1 通过**，28.55 秒，无跳过：通过原 UI 创建/改名/封面、把同项目两张画布移入/移出、全新浏览器打开，原复选框导出文件夹与封面 ZIP，再由无来源访问权账号导入。逐字节核对封面，验证两个新画布仍属同一新项目并映射新文件夹；原账号删除文件夹归档其中两个画布，不影响导入账号的作品。测试没有声称整个多画布 ZIP 是一个事务。

进一步添加受控的成员并发保存场景：`canvas-folder-member-race-red.log` 复现删除文件夹预加载 ORM 实体后等待锁，另一成员先保存为 revision 3，删除仍写 revision 3 而未推进到 4。现先查询目标 ID，取得项目锁后才读取画布实体。`canvas-folder-member-race-green.log` **6 通过**，15.46 秒，含该回归和前述五个文件夹业务场景。

本阶段已在当前业务库 `short_drama` 实际执行两张 CREATE TABLE 与两个 CREATE INDEX，清单见[006 增量](../数据库模型/migrations/2026-10-05-infinite-canvas/006-canvas-project-folders.sql)。`canvas-project-folders-ddl.log` 与后续只读核验均为 `ready`、`missing=[]`、`changed=[]`；当前 **86 表 / 34 张画布表**。预检现有图与历史中的非空旧 folderId 均为 0，无需归属回填，未改写旧业务行或媒体字节。

快速验证 `canvas-project-folders-unit-api.log` 初次因系统临时目录 WinError 5 出现 23 个 setup 错误、976 通过；改用工作区随机独立 basetemp 后 `canvas-project-folders-unit-api-b.log` **999 通过**，198.92 秒，无跳过。最后的成员竞争修复随后由上面的真实 MySQL 回归验证。画布 Node **136 通过**、构建通过、source:check **802 文件 / 46 处适配**；宿主 Node **191 通过**、构建 **6.97 秒通过**。后端全仓 Ruff 检查与 466 文件格式检查通过；后续新增断言继续按影响范围核验。

最终 `canvas-folders-full-integration.log` 全部画布集成 **112 通过**，499.06 秒，无跳过；覆盖本轮文件夹、完整 SQL/增量升级、既有真实媒体、绘图、历史、创建恢复、归档与浏览器场景。运行使用随机测试库、测试 MinIO 桶和任务自建服务，结束后已清理。`export_schema.py --check` 确认完整 SQL 与 ORM 的 **86 表**一致；最终 Ruff 检查、466 文件格式检查、Git diff 空白检查和六份维护文档的本地链接核对通过。

补充同一新项目归属及导入封面账号隔离断言后，`canvas-project-folders-browser-final.log` 再次遇到第二次移动时子菜单不稳定/消失，说明单纯等待上次保存成功还不足以稳定自动操作。测试现等待菜单自身及祖先动画完成，再执行原鼠标悬停/点击，没有关掉动画或改变产品代码；`canvas-project-folders-browser-motion.log` **1 通过**，26.20 秒，两项新增断言也通过。此补验与 112 项回归包含重复场景，不累计为新增独立用例数。

随后针对菜单动画时序连续独立补验：`canvas-project-folders-browser-repeat-1.log` **1 通过**，25.68 秒；`canvas-project-folders-browser-repeat-2.log` **1 通过**，25.79 秒，均无跳过。两次均完成新增归属与账号隔离断言并清理临时资源；这是同一场景的稳定性补验，不累加独立用例数。

本轮未放宽视觉零像素要求；上一节未解释的像素差异仍未闭环，文件夹本轮的真实浏览器功能验证也不能代替源/目标视觉对照。未调用付费模型，未新增依赖，未提交 Git commit。标准前端未重复执行完整 Playwright 集合，标准 Node/构建与后端快速回归不能冒充真实供应商验收。

## 2026-10-06 回收站显式恢复与原作品保留

源 `recycle-bin-dialog.tsx` 的恢复操作只把删除快照放回浏览器 store，再移除回收卡片。Python 迁入版此前仍归档着原画布/项目，不能把本机列表变化当作真正恢复。现保留源弹窗结构、按钮、选区、多选和确认步骤；恢复按钮调用服务端，待规范文档和原提交日记/草稿写入确认后移除卡片，异常保留条目并通过原消息组件反馈。运行中有 loading 和重复点击保护。

新增 `CanvasRecycleService` 与 `POST /canvas-runtime/canvas-projects/{source_key}/recycle-restore`，请求是本人原删除回执的 `archive_key`，另带稳定写入幂等键。前端从回收快照中的删除前版本生成这两个键，未知回执的刷新重试不创建新请求。恢复只接受本人 `canvas.archive` 回执，并重新验证当前项目成员资格；其他成员不能借用删除者回执，撤权后也不能重放本人旧回执。

`canvas_recycle_access.py` 在内部事务中只为回执指向的确切项目、画布及成员检查开放归档读取；现有账号谓词仍生效，未启用系统 Session，未给普通 HTTP 查询开放归档数据。事务先锁项目再锁画布，锁后读取当前回执，使用删除回执的 committed_row_version 检查归档代次。成功时恢复原图/项目并递增版本，必要时重建主画布；已有主画布保留。节点、绘图、媒体和历史沿用原记录，私人字段仍按本人投影。恢复及回执失败一起回滚，旧恢复回执重放不重复其过去的副作用，也不能把后来再次归档的作品复活。

本阶段没有 ORM 或 DDL 变化，业务库仍为上阶段的 86 表；没有新增依赖或 Git commit。新增后端合同/权限、相邻 API 与宿主 DTO 同步；两处迁入源文件的适配已登记，原 CSS 未改变。

`canvas-recycle-red.log` 复现三个恢复入口 404；同轮有一处测试误用 join fixture 参数，已修正，未算作产品缺陷。实现后 `canvas-recycle-green-a.log` **4 通过**，12.66 秒，覆盖原作品和私人参数、已有主画布、本人回执及撤权、并发同键恢复和重新归档代次。随后增加窄作用域与强制回执失败的真实事务回滚测试；`canvas-recycle-browser-b.log` 的这 **5 个后端场景通过**，同轮浏览器未完成，未冒充整轮通过。

真实浏览器使用原右键绘图入口、鼠标笔画、图片上传、文件夹删除、回收站全选和恢复。`canvas-recycle-browser-a.log` 在服务端已恢复后模拟丢失回执，但测试等待了被统一 HTTP 客户端过滤的服务端文本；修正为实际中文 503 提示。`browser-b` 已验证双画布原项目归属、丢失回执刷新重试和全部媒体字节，最后重新编辑时上传图片遮挡了绘图节点，Playwright 报 `subtree intercepts pointer events`。测试改用原拖拽把图片移开，未强制点击被遮挡节点或修改产品布局。`canvas-recycle-browser-c.log` **1 通过**，30.82 秒，无跳过；全新浏览器能在恢复的原笔画上继续编辑，绘图版本正常推进，原媒体字节和 ID 保持。

后端快速回归 `canvas-recycle-unit-api.log` **999 通过**，196.76 秒，无跳过；有原依赖弃用提示及 pytest 缓存目录 WinError 5 警告，测试本身完成。画布 Node **138 通过**；宿主 Node **191 通过**；画布最终构建 **23.98 秒通过**，宿主构建 **7.74 秒通过**。画布构建保留既有大块提示，未为消除警告改源依赖或分块。source:check 为 **802 文件 / 47 处适配**。

最终 `canvas-recycle-full-integration.log` 全部画布、账号协作与私人候选边界共 **137 通过 / 1 跳过**，561.96 秒。跳过的是 `test_actual_minio_import_bytes_are_independent_and_temporary_objects_are_cleaned`：该标准素材专项未启用 `RUN_STORAGE_INTEGRATION=1`，不计为通过；本轮画布专项启用真实 MinIO/浏览器并通过。运行器的随机数据库、测试桶和自建进程均已清理。最终全仓 Ruff check、**470 文件** format check 及 Git 空白检查通过。标准前端完整 Playwright 未重跑，未调用真实付费供应商。

视觉准备时发现源浏览器模式的文件夹来自本地存储，而非远程文件夹 API；对照夹具已按该真实分支提供同数据，且先打开画布完成源会话初始化。源库根目录只展示未分类画布，属于文件夹的卡片不应在根目录等待；前两次对照因此未到截图比较，不计通过。

另发现并向用户提出明确的兼容性选择：原界面删除文件夹时先记录含旧 folderId 的回收快照，删除文件夹只清理仍在 store 中的活动画布；恢复快照后旧 folderId 仍在，根列表过滤会把它隐藏。当前 Python 恢复以规范归属表为准，已删除文件夹中的画布回到未分类。这是待确认的源数据缺陷修正，必须单独记录，不能称为完全相同的源行为。对照脚本保留恢复后两边的截图与卡片数，不把该场景纳入相同画面统计。

本阶段未覆盖回收记录跨设备列举、完整永久删除、未知删除回执刷新恢复、本机未保存内容的完整回收、所有绘图删除竞态或源全量视觉差异；仍不能声明完整一比一。

## 2026-10-06 文件夹与回收站源版对照

`compare-recycle-source.mjs` 在固定源码与同数据、Chrome 154.0.8037.95、1440×900、DPR 1、深色、UTC 下运行。`canvas-recycle-parity-normal-d.log` 的文件夹库、文件夹菜单、空回收站、默认回收条目、选中条目及删除确认共 **6 场景几何一致、像素差异 0**；页面异常与未定义夹具请求均为空。这是原 React 界面的固定 API 夹具对照，不替代 Python 或供应商验收。

前序 c/d/e 对照阻塞在源下拉菜单，最终定位在 `prefers-reduced-motion: reduce`：源 `workspace-product.css` 将全部浮层后代的 transition-duration 设为 1ms，包含定位属性；rc-trigger 从初始 `-1000vh` 临时重置坐标进行测量。诊断中两版按钮与祖先几何相同、滚动为 0，浮层均停在 y=-9002。默认动效下两版定位正常；诊断保存在 `recycle-parity/reduced-motion-menu.json` 和 `normal-motion-menu.json`。本轮保留源样式并单独记录该缺陷，不能声称减少动效模式功能正常。

默认动效的初次菜单截图出现 2px 差异。事件轨迹证明两版 pointerdown/up/click 的坐标相同，rc-trigger 在入场动画结束时重新对齐；测试若提前移开鼠标，按钮悬停位移与该回调存在时序差异。现在等待原入场动画及其回调完成后再移入菜单，没有禁用动画、修改产品 CSS、强制点击或调整像素阈值。`normal-a` 等待隐藏通知彻底离开 DOM 超时，改为等待不可见；`normal-b/c` 保留上述偏移失败证据。诊断脚本的一次括号错误在运行页面之前修正，不计产品失败。

删除文件夹再恢复也完成源界面运行复现：源恢复后作品卡片为 0，目标为 1，符合此前静态发现。恢复后的背景不同，故该状态单独记录为 `folderRestore`；空回收站截图在删除之前的同等数据状态采集。源失效文件夹归属的修正仍待用户选择，不计为相同功能。原 17 场景中尚未解释的素材托盘单像素与绘图阴影差异仍未闭环，本轮 6 场景通过不替代那组验收。

## 2026-10-06 已保存画布的未知删除回执恢复

新增只读 `GET /canvas-runtime/canvas-projects/{source_key}/recycle-status?archive_key=...`。仅本人原删除回执及当前项目权限可授权查询，在窄归档作用域内核对项目、源键与归档代次，不开放子文档或系统 Session。返回 `archived` 或 `superseded`，后者表示已有后续操作，不能再次删除当前作品。不存在或已无权访问返回 404，客户端不能将它当作自动重发的许可。API、Pydantic、宿主 DTO 和接口说明已同步；DAO 仅增加可选非锁定读，原恢复仍按原顺序加锁。

前端新增本人、每画布的持久删除日记：先等待既有保存队列，捕获原版本与完整快照，再落盘后发送 DELETE。后续输入不能修改已发送请求。确认服务端仍处于该归档代次后，先等待回收条目持久化，再移除活动缓存，最后清除原日记；刷新只查询状态，未确认的请求保留草稿，用户明确重试才再发原请求。旧请求被后续恢复取代时清理该旧日记，不改写服务端作品。明确的旧版本 409 保留冲突草稿并释放已拒绝的请求。本机有新编辑时拒绝移除草稿。

回收 store 的写入捕获账号作用域、按账号串行，并提供完成屏障；切换账号前等待在途回收写入。恢复后移除回收条目也等待持久化，写入失败在当前账号内保留卡片。原项目卡片的 `void deleteLocalCanvasProjects` 增加失败捕获与原消息反馈，避免网络故障成为未处理 Promise；原菜单、按钮、确认步骤与样式保持不变。本阶段无 ORM/DDL、依赖或标准界面变更，业务库仍为 **86 表 / 34 张画布表**。

`canvas-delete-recovery-red.log` 真实复现归档后状态接口仍为 404。`canvas-delete-status-green.log` **6 通过**，15.80 秒，包含原恢复权限/回滚/并发场景和新只读状态、源键校验、本人回执、撤权及后续恢复/再归档状态。`canvas-delete-recovery-browser-a.log` 原双画布恢复通过，新测试误用了另一种卡片的“删除”菜单名而未执行删除；按真实项目库的“删除项目”直接操作修正，没有添加源不存在的确认步骤。`browser-b` 未知删除基本场景 **1 通过**，19.41 秒。

最终 `canvas-delete-recovery-complete-a.log` **8 通过**，58.18 秒，无跳过，使用真实 Python、隔离 MySQL 与 MinIO。新增浏览器场景验证：服务端成功后注入 503、刷新通过 GET 找回原回收条目且不重发 DELETE、二次刷新仍保留、原作品/ID/私人参数恢复；另一 API 入口恢复后旧未知删除不再删除作品；请求未到服务端时刷新不执行删除，明确重试使用完全相同的键和正文。原绘图及图片的双画布回收流程也在本轮通过。随机数据库、测试桶及任务自建进程已清理；不同运行含重复用例，不累计为独立场景数。

画布 `canvas-delete-recovery-node-final.log` **141 通过**，后端画布单元 `canvas-delete-recovery-unit.log` **147 通过**，1.81 秒；最终画布与宿主构建通过。首次画布构建暴露 ES2018 目标不支持 BigInt 字面量，已沿用现有 `BigInt(1)` 写法修正，没有升级目标或依赖。全仓 Ruff check、**470 文件** format check 通过，source:check **802 文件 / 48 处适配**。`canvas-delete-recovery-parity.log` 的原 6 个文件夹/回收站画面再次几何一致、像素差异 0。未重跑后端 999 项、137 项大集或标准前端完整 Playwright，未调用真实模型供应商。

本轮恢复依赖本人浏览器保留下来的原请求，不等于跨设备回收条目列表；未保存绘图/其它子文档的完整回收、所有跨窗口删除竞态和永久删除仍需实施。源已删除文件夹的归属缺陷修正仍待用户回答，原 17 场景的未解释像素差异仍未闭环。

## 2026-10-06 编辑器静态画面的两种动效设置补验

`compare-source.mjs` 的截图采集改为等待实际有限动画完成，再等 DOM 几何和连续截图稳定；不再用截图 API 快进或禁用原动画。仍使用相同固定源码、数据和浏览器，像素比较阈值保持 0，没有修改产品 CSS。`canvas-delete-recovery-editor-parity.log` 的减少动效设置及 `canvas-delete-recovery-editor-normal-motion.log` 的默认动效设置，分别 **17 场景几何一致、像素差异 0**，两次进程均成功退出，页面异常及未定义夹具请求均为空。两种设置覆盖同一组场景，不能累加成 34 种功能。

这两次包含此前不同的三个素材托盘画面与空绘图编辑器阴影，说明本次实际采集已通过；尚未通过独立实验隔离此前每个灰阶像素的根因，不把旧失败称为已证明的渲染噪声，也不删除旧失败证据。默认动效的文件夹/回收站另 6 场景也已通过；减少动效下的文件夹菜单负坐标源缺陷仍存在。静态端点通过不等于每一帧动效、所有触控/快捷键或完整功能已验收。源依赖弃用和 Excalidraw 初始化警告仍保留。

## 2026-10-06 绘图节点复制固定完整来源版本

`verify-drawing-copy-concurrency.mjs` 使用两个独立浏览器上下文：目标标签页通过原右键“复制节点”和 Ctrl+V 粘贴，另一窗口在目标第一次读取绘图记录后通过原保存按钮提交新笔画。`canvas-node-copy-concurrency-red-e.log` 实际复现副本保留第 10 版的两条笔画，却保存了第 11 版的预览；预览实际为 61,701 字节，应为 41,034 字节，逐字节比较失败。此前 `red-b/c` 停在测试操作：源已选绘图单击会直接打开编辑器，不能再点击底层节点；系统剪贴板标记还依赖原标签页会话存储，不能只把标记复制到空上下文。测试已按原右键操作、同标签页跨画布粘贴修正，未强制点击或修改产品交互。

`red-d` 的 Node 因内存分配失败退出，不算产品红测。图片比较改用完整 Buffer.equals，并在失败报告中记录字节数及 SHA-256，避免展开大型二进制差异；上一场景已结束的浏览器页面也及时关闭。比较没有改成容差或只检查摘要，未调整测试超时、图像阈值或依赖。

`cloneCanvasDrawing` 现复用已有 `loadCanvasDrawingBundle`，一次读取固定笔画记录，再按其中稳定资源 ID 读取预览和成品；本机草稿按同一缓存代读取。整包读取在获取媒体前后复核删除标记，过期或相同版本的响应不能绕过删除；另一窗口明确恢复出的更高版本仍可复制和导出。原菜单、快捷键、副本位置、目标上传归属及异步元数据回填保持原有行为。本轮无 API、ORM、DDL 或新依赖。

`canvas-node-copy-concurrency-green-a.log` 的真实 Python、隔离 MySQL、MinIO 与浏览器专项 **5 通过**，165.01 秒，无跳过。并发复制只读取来源记录一次，副本笔画和两张图片都保持捕获版本，预览/成品均逐字节一致，目标使用独立资源 ID，来源保留后续新笔画。补充的旧响应整包读取/复制拦截、跨窗口明确恢复，以及已有绘图 CAS、历史、复制和 ZIP 混合媒体导入导出均通过。随机数据库、测试桶及自建进程已清理。该次 PowerShell 重定向包装报告退出 1；已用独立的 stderr/正常退出小实验复现此包装行为，测试日志实际为上述 5 passed，没有将它描述为包装进程退出 0。

画布 Node **141 通过**，生产构建通过（20.43 秒）；Ruff check 与 **470 文件** format check 通过，源登记 **802 文件 / 48 处适配**。源库仍在固定 commit 且工作区干净。未重跑全仓后端大集、宿主完整浏览器或视觉对照，未调用真实模型供应商；本轮仅改变绘图存储适配与验证，不据此宣布完整一比一迁移。

## 2026-10-06 服务端回收目录、预览与不可恢复处置

新增 `GET /canvas-runtime/recycle-bin` 与 `POST /canvas-runtime/canvas-projects/{source_key}/recycle-purge`。目录只列本人、当前仍有项目权限、当前归档代次匹配且未处置的删除记录，按源上限返回 200 条。归档在既有写回执内部冻结本人删除前文档；普通删除响应与通用回执 GET 过滤 `$archive_document`。旧回执通过确切归档画布作用域读取文档，私人字段仍按当前作者过滤。没有新增 ORM 表或 DDL，业务库仍沿用此前 **86 表 / 34 张画布表**。

原资源 GET/HEAD 增加成对的回收源键与删除回执参数；服务端核对本人、当前权限、归档代次和快照资源归属后，保留原 Range、HEAD、no-store 字节传输。普通资源读取仍拒绝归档项目。前端只在展示副本中生成受限预览地址，不污染可恢复快照。服务端目录与本机列表合并时保留同代次的本机完整快照和删除时间，旧 GET 不能覆盖期间已经完成的本机操作。

永久处置复用私人 `canvas.recycle.purge` 写回执，项目与画布按原顺序加锁，同一不确定请求使用原键。恢复先完成时处置返回 409；处置先完成时恢复返回 410。本人撤权、其他作者和错误源键均拒绝，迟到的旧代次请求不能作用于重新归档的作品。**这里是业务不可恢复处置，不是物理擦除全部数据库和 MinIO 字节**；冻结文档、历史、绘图和共享引用的完整生命周期仍需继续。

两项真实缺陷已先复现再修复：

- `canvas-recycle-persistence-red-b.log` 在原“确认删除”成功后，对回收记录的 IndexedDB put 注入一次 `QuotaExceededError`；应保留 1 张卡片，实际 0，断言失败。现在恢复和处置共用确认移除逻辑，落盘失败恢复原列表并保留原 `archiveKey`，新列表已经到达时不回写旧状态。修复后的同场景及同键再次确认通过。较早 `red` 停在测试误以为项目卡片菜单会同时删除两张画布的等待，未到故障注入，不算产品红测。
- `canvas-recycle-persistence-green-a.log` 中不可变回执的删除测试出现 `DID NOT RAISE WorkflowError`。带 Actor 的普通 Session 此前阻止改写，但没有阻止删除原回执；现两者均返回 `canvas_receipt_immutable`，原批量写限制保持。该轮另 11 项通过，包含两项真实浏览器专项；不把这一轮称为整体通过。

最终验证记录：

- `.runtime/canvas-recycle-catalog-final-integration.log`：**44 通过 / 1 跳过**，134.17 秒。真实原生 MySQL 8.0.33 随机测试实例、随机库、隔离 MinIO 桶和浏览器。包含回收目录、撤权、旧回执私人字段、回执公开输出过滤、失败回滚、不可变回执、并发恢复/处置、实际媒体 GET/HEAD/Range，以及相邻画布 HTTP、账号协作与私人候选边界。跳过的是 `test_actual_minio_import_bytes_are_independent_and_temporary_objects_are_cleaned`，因未启用标准素材专用 `RUN_STORAGE_INTEGRATION=1`；不计为通过。本轮画布真实 MinIO 开关已启用。
- 浏览器从全新上下文仅登录账号，打开原回收站找到两张卡片，实际加载已归档图片。恢复成功但回执被改为 503 时，本机暂留卡片；刷新目录移除已恢复条目，没有重复写入。原项目 ID、画布 ID、笔画及预览/成品/上传图片字节保持，全新浏览器可继续画第二条笔画。原“彻底删除”经历发送前 503、提交后 503 和本机落盘失败，再次确认的路径、请求体、幂等键完全相同；新设备不再看到已处置卡片，恢复 API 返回 410，另一画布仍可读取。成功 API 和媒体均是真实服务，仅错误由测试注入。
- `canvas-recycle-catalog-unit-api.log`：后端快速回归 **976 通过 / 23 项准备错误**。共同堆栈为 `_pytest/pathlib.py:175 os.scandir` 访问共享临时目录时 `PermissionError: [WinError 5]`。随后仅将这 23 项移到任务独立临时目录，`canvas-recycle-catalog-unit-retry.log` **23 通过**，49.23 秒，并清理目录；合计 999 项完成验证，没有修改全局 ACL，也没有把首轮描述成全通过。重跑脚本第一次误按 UTF-8 解析 PowerShell UTF-16 日志，在运行测试前停止；修正编码后才实际重跑。
- 画布 `canvas-recycle-catalog-node.log` **145 通过**，宿主 `canvas-recycle-host-node.log` **191 通过**；两套生产构建均通过。`canvas-recycle-project-modes.log` 的创建弹窗/双模式/原键重试浏览器 **1 通过**；`canvas-recycle-editor-e2e.log` 的原编辑器拖拽、视口、外观与撤权浏览器 **1 通过**。这两项使用 API 夹具，不代表供应商或完整标准 UI 验收。
- `canvas-recycle-catalog-parity.log`：原 6 个文件夹/回收站画面**几何一致、像素差异 0**；源与目标的页面异常、未知夹具请求均为空。默认动效保持，未修改原 CSS 或比较阈值。恢复已删除文件夹后源 0 张卡片、目标 1 张的已知差异仍单独记录，未计入一致场景；等待用户对源归属缺陷的选择。
- 全仓 Ruff check 与 **471 文件** format check 通过；source:check **802 文件 / 48 处适配**，源库仍在固定 commit 且干净。本轮更新了原回收弹窗、历史记录和恢复存储边界的适配说明；未新增依赖、未执行 Git commit、未调用真实模型供应商，未重跑标准前端完整 Playwright 集合。

基础设施记录：Docker 引擎在初次集成启动时无响应，已取消本任务的启动 CLI 和遗留只读查询，没有重启 Docker/WSL 或现有业务进程。随后改用独立原生 MySQL。首轮临时实例因 Windows MySQL 子进程继续持有文件而清理失败，已核对完整 datadir/PID 后仅停止并移除该任务实例；运行器改为发 SQL SHUTDOWN，关闭后使连接失效，避免退出连接时误发 rollback。后续运行正常退出并清理各自临时数据目录、数据库和测试桶。最初 Docker 请求的确切容器名保留在 `.runtime/canvas-recycle-pending-container.txt`（`short-drama-canvas-test-07043ff7be63`），引擎恢复后仍需核查是否曾创建；不据此宣称 Docker 临时资源已经清理。

## 2026-10-06 画布子包目录与统一前端入口调整

用户要求将画布移入 `frontend/canvas/`，保留独立运行包、依赖与 HTML，并统一启动和构建；共同声明但版本不同的直接依赖使用宿主版本。此调整仅改变工程结构、版本兼容与运行入口，完整 M0–M5 功能目标继续保留，标准业务与 Python 数据契约不因目录变化重构。

维护入口为 `frontend/` 下的 `install:all`、`dev`、`build` 和 `preview`；`dev:standard` / `dev:canvas` 保留单包启动。两包分别安装，统一开发进程通过各包 Vite API 创建独立服务器，组合构建将画布产物复制到 `dist/canvas-app/`，宿主 preview 提供各自 HTML 回退及 API 代理。画布自己的 package.json、package-lock.json、index.html、构建与测试入口继续保留。源独有 `react-router` 8 不因宿主使用 react-router-dom 7 而误降级；AntD 关联包需随宿主版本做兼容核对。

路径更新覆盖画布 `@host` TypeScript 别名、首次迁入脚本的宿主编译器解析、像素对照脚本的 Python 位置、Python 浏览器测试 cwd、跨语言素材合同样本及当前维护文档。首次迁入脚本没有重新执行，源组件没有因移动被覆盖。推荐 Node 22.18+ 的 22.x 或 24.x LTS；当前已安装 Node 22.22.1，nanoid 6 的实际 engines 为 `^22 || ^24 || >=26`。

路径专项验证：使用现有 Python 虚拟环境执行 `pytest tests/unit/test_canvas_library_contract.py -q`，**81 通过**，0.21 秒；出现已有 `.pytest_cache` 写权限警告，未影响断言。七个涉及路径的 Python 测试文件 Ruff check 与 format check 通过；三个变更的画布脚本 `node --check` 通过；八份维护文档的 60 个相对链接均存在，旧包目录已不存在，新 HTML 与跨语言合同样本存在。该专项未启动业务服务、执行 DDL 或调用供应商，也未运行完整后端集成。依赖与统一入口的实际结果如下，不沿用调整前的构建或截图作为本次证明。

共同直接依赖的声明范围及实际锁定版本逐项对齐，版本表和兼容原因见 [画布 README](../../frontend/canvas/README.md#依赖兼容)。AntD X 使用支持宿主 AntD 5 的 1.6.1，图标包使用 5.6.1；LobeHub 保留原始 321 项 Mono 图标的 649 个文件、MIT 和 SHA-256，避免引入要求 AntD 6 的完整 UI 包。Fiber 9.8.0 支持宿主 React 19.3，Excalidraw 内部 Radix Tabs 限定 override 到 1.1.13。最终正常 `npm ci` 和 `npm ls --all` 完成，没有使用 `--force` 或 `--legacy-peer-deps`；首次安装的 AntD、Fiber 和旧 Radix peer 冲突保留在早期失败日志，不计为最终通过。画布独有依赖仍由子包管理。

实际复现并修复生产分块循环：开发服务正常，统一生产 preview 打开画布时出现空白，`canvas-production-error-stack.log` 为 `TypeError: Cannot read properties of undefined (reading 'primary')`，栈位于 `vendor-icons-BU1wAIgf.js:58:1310`。图标读取 AntD 调色板，AntD 又依赖图标，原独立分块形成初始化循环；现将两者及相关 UI 依赖保留在同一 `vendor-ui` 块。没有通过仅验开发服务或忽略 pageerror 来绕过该问题。

AntD 6→5 兼容只调整 API 映射、DOM 对应的局部样式和源阴影公式，保留控件、原操作与已存在的皮肤覆盖。数量输入复现数字基线下移 1px、整图差 11 像素，按源边框内容区高度修复后选中/浅色诊断均归零。助手模型 Select 另用真实可选择的目录夹具核对选中、下拉、窄屏和键盘 `focus-visible`，补回源透明度、几何及焦点轮廓；补充诊断 8 个场景几何一致、像素差 0。这些诊断不替代下列正式对照，也不代表助手执行验收。

本轮最终验证：

- `frontend/.runtime/canvas-subpackage-host-tests.log`：宿主 Node **191 通过**；`canvas-subpackage-node-final.log`：画布 Node **146 通过**，包含 Logo 完整性；均无跳过。
- `frontend/.runtime/canvas-subpackage-project-modes.log`：原创建弹窗、双模式与未知回执复用原键浏览器 **1 通过**；根 `.runtime/canvas-subpackage-x1-logo-browser-final.log`：真实 X 1 组件的长对话滚动、输入/两种发送、优化方式/采用回填、321 项 Logo 与按需加载 **1 通过**。浏览器使用本机 Chrome；业务与优化供应商使用受控夹具。
- `frontend/.runtime/frontends-runtime-final.log`：统一启动与 preview **3 通过 / 0 跳过**，覆盖两台真实 Vite、独立 HTML、带点深链接、GET/HEAD 与 HTML/通配 Accept、查询参数、斜杠重定向、宿主 HTTP 别名、PNG 原始字节、字体、API query/header、退出释放两端口及端口占用失败清理。
- `frontend/.runtime/canvas-subpackage-build-final.log`：最终统一构建 **通过**，含两包类型检查，宿主 4.22 秒、画布 39.89 秒；产物为 `dist/`、`dist/canvas-app/` 和子包 `canvas/dist/`。无循环分块警告，仍有原大型媒体/编辑器块超过 500kB 的提示，没有放宽警告阈值。
- `frontend/.runtime/canvas-production-final.log`：最终统一生产产物的原编辑器 **1 通过 / 0 跳过**，11.75 秒；初始化无页面异常，原拖拽保存、刷新、独立视口、浅色/线外观与撤权停止全部断言保留。自建 preview 端口 65291 已关闭，浏览器已关闭。这是 API 夹具验收，没有宣称真实 Python 持久化或供应商验收。
- `frontend/canvas/.runtime/canvas-subpackage-recycle-parity.log`：正式文件夹/回收站 **6 个场景几何一致、像素差 0**，进程退出 0。`canvas-subpackage-parity-verified.log`：正式画布 **17 个场景几何全部一致，13 个场景像素差 0，严格零像素断言退出 1**。三个素材托盘场景均只在 `(549,859)` 为源 RGB `(253,253,253)`、目标 `(255,255,255)`，与目录调整前记录完全一致；绘图仍有 `(443,137)`、`(996,137)`、`(444,140)`、`(995,140)` 四个阴影边缘像素由 RGB 17 变为 18。未改比较阈值、删除场景或遮罩区域；两个正式报告的页面异常与未知夹具请求为空，不能把 17 场景写成全部零像素通过。
- 最终 `source:check` **802 文件 / 70 处适配**；`npm ls --all --json` 再次退出 0、依赖问题 0；八份维护文档的 62 个相对链接均存在，本轮范围的 `git diff --check` 与统一运行/测试脚本语法检查通过。未重跑完整后端集成或标准前端完整 Playwright 集合，本轮没有新增 DDL、重导源组件或执行 Git commit。

干净安装时原开发进程占用了画布原生依赖文件，已核对 PID 与完整参数后暂时停止该进程。安装与验证完成后，按原参数 `node scripts/run-frontends.mjs dev --host 127.0.0.1 --port 8081` 恢复，PID 362064 同时监听 8081/8082；宿主 HTML、经宿主访问的画布 HTML 和直接画布 HTML 均返回 200。原 BeefTV 3000/8080 进程保持运行，没有停止其它开发服务。

## 2026-10-06 M2 真实任务准入、四类执行与文本增量

本轮恢复完整 M0–M5 目标，在 `frontend/canvas/` 独立子包及统一运行入口上继续。此前 `canvas_tasks.py` 只有绑定端点，生产链没有调用 `register_locked`，原前端任务创建/查询接口返回 404；旧绑定集成中的手工成功记录不能证明生成闭合。现新增画布专用准入、列表、查询、取消、恢复、状态日志与正文增量/SSE，复用原 `AIGenerationService`、Worker 执行器及真实媒体归档，标准输入契约与候选采用流程保留。

来源和新目标/连线先保存到真实 Python 后端，确认后才提交任务。准入同事务冻结请求、来源和模型，登记私人 binding，并保存私人任务身份，不推进共享图版本。前端持久化固定请求与原操作键，未知回执只重放原正文/原键；刷新按操作键发现同一任务。Config 来源按批次聚合子任务，不被错误写入子任务 ID。稀疏输出保留真实槽位，默认绑定选择实际首槽；成果素材沿用源 SHA-256 稳定身份，重复读取保留用户编辑。已归档素材先私人交付，原版 bind 回填后才发布媒体。

图片参数不再把源质量档错误当成标准像素分辨率。Canvas 专用严格参数在冻结后进入 OpenAI、Ark、Qwen 请求分支，标准路径不改；原合法尺寸/质量和非法尺寸 HTTP 422 均有回归。音频 MP3/语速/指令真正透传，标准默认 WAV 保留；视频按来源指定节点映射首尾帧与实际渠道能力，显式音轨/水印布尔值不静默丢弃。普通画布视频现经过实际 ffprobe 后再生成素材尺寸、时长与轨道元数据。蒙版、全部渠道/协议与任意 providerOptions 等仍未完成，不能据此宣布参数全覆盖。透明字段在源声明式转换中存在未接线缺口，当前接线是显式适配，尚未通过原源透明产物的实际对照。

供应商 SSE 实际正文经跨分块 UTF-8/事件解析及凭据脱敏回调进入 Worker，未用最终正文人工分片。新增私人 `canvas_task_text_deltas`，序号按稳定任务唯一、实际调用外键固定，写入检查执行租约和配额。真实 `short_drama` 已执行新表及作者/到期索引，迁移核验 `ready`；完整 SQL 为 **90 表 / 38 张画布表**，008 增量和数据库说明同步。成功增量 24 小时、失败/取消 7 天，scheduler 定时清理，最终正文仍保留。SSE 按最大游标续读并逐次重验会话/权限，草稿详情带序号避免刷新后重复拼接；客户端不能提交供应商增量或标记执行成功。

实测记录：

- 前期任务请求合同 **19 通过**；本轮正文解析/真实本机 HTTP **8 通过**，包含供应商在终态前实际传入增量、断流保持 unknown、单次 POST 和跨事件凭据脱敏。现有文本/图像适配回归另 **117 通过**（包含上述 6 项早期解析测试，数量不能直接累加）。
- `canvas-generation-runtime-green-b` MySQL 专项 **32 通过 / 0 跳过**；`canvas-generation-runtime-minio-b.log` **36 通过 / 0 跳过**。中间 `minio-a` 因测试为 gpt-image-1 选择了旧协议不支持的 16:9 而有两项失败，随后先验证合法默认，再由专用源图片参数真正补齐比例；没有改弱断言。初次数据库 DTO 的空 startedAt 引起 500 及 postponed annotations 导入问题均已修正，旧失败日志保留。
- `.runtime/canvas-generation-vertical-b.log` **44 通过 / 0 跳过**，88.80 秒。真实随机 MySQL、Python 执行器、FFmpeg/ffprobe、三个随机 MinIO 桶和 Chrome；包含 text/image/video/MP3 的实际归档/字节、视频 submit→poll→save、PUT 成功后丢 ACK 的固定归档身份、私人产物先交付与绑定后成员媒体共享。5 个文本流专项包含终态前增量落库、SSE 游标、在线撤权、断流保留草稿、失租拒写和缩小测试限额下的配额事务回滚；不把缩小限额称为真实 64 MiB 容量验收。
- 同一 44 项中的原 UI 文本生成：实际输入、保存、POST，刷新后找到同一任务，才释放受控供应商终态；直接回填后全新无 IndexedDB 上下文读取已保存正文。源任务 POST 和供应商 submit 均为 1，HTTP 无业务拦截。先前 `browser-a` 因测试未配置源必须的 hasApiKey 而未准入，属于测试准备失败；随后通过真实 API 保存明确非真实占位 key，并未把密钥注入前端。`vertical-a` 的非法尺寸 ValidationError 逃出 HTTP 是真实产品错误，现统一转 422；另两项为账号长度与新增外键下的旧库准备错误，已保留断言并补 008 重入。
- 画布 Node **167 通过 / 0 跳过**，新增 21 项来源保存、准入、Config 批次、稀疏槽和 SSE 游标合同；画布生产构建通过（39.26 秒）。`source:check` **802 文件 / 77 处适配**，未改产品布局/CSS。图片/音视频专用参数回归在其固定阶段 **208 通过**，包括真实本机 HTTP JSON/multipart/轮询；这些受控 HTTP 不是付费供应商验收。

上述测试实例均正常 SHUTDOWN，随机 MySQL 目录、测试桶、任务自建 API/Worker 线程、Vite 和浏览器已清理，4188 已关闭。现有业务服务没有重启。真实 RabbitMQ/Celery 投递、真实收费模型、音视频原 UI 播放、完整渠道/RunningHub/协议、完整日志和一比一全量对照仍须继续；M2 保持未完成，goal 保持 active。当前浏览器报告仍列出 M3 助手 status/ui-session 的实际 404，不能当作全功能可用。

## 2026-10-06 M2 原设置页、渠道目录与独立模型测试

继续以固定源 `4ca2a65a7780a8dfcaaa86c33679f84fb04e055c` 为基准，在 `frontend/canvas/` 独立子包上实施，没有重跑首次 importer、覆盖源适配或提交 Git。原设置页及创作历史页按源闭包恢复，AntD 6→5 只做相邻 API 等效适配；完整目录保存、独立测试和真实任务历史替代缺失的 404 接口。宿主现有模型仍可用，不能用新建自定义渠道绕开旧投影回归。

新增本人 `canvas_model_catalogs` 和 `canvas_channel_models`，源公开渠道字段与独立加密凭据分离，服务端稳定绑定真实执行模型 ID。偏好、目录和执行配置在同一版本事务保存；旧 ACK 不覆盖新输入，409 留草稿并停止自动覆盖。当前业务库 **short_drama** 已实际执行这两张表的 CREATE TABLE（含约束），009 增量与 canonical SQL 已同步。最新只读迁移核验 `ready/missing=[]/changed=[]`，`export_schema.py --check` 实际通过，当前 **92 表 / 40 张画布表**。

原设置页的模型测试没有 project/node，新增独立 `/canvas-runtime/model-tests` 复用真实异步任务、执行器与归档。原键同正文重放；同键换秘密返回 409。test-only 配置不进入正常模型列表或默认选择，只允许服务端冻结标记、本人 personal 范围、版本和凭据身份均匹配的任务执行；普通停用配置检查保留。本人原 `/tasks` 可读取测试、日志、取消与许可恢复；不创建虚构画布、binding 或共享作品。

官方协议目录保存固定源的 **85 个包 / 86 个 provider** 原 manifest、路径及 SHA-256，目录存在与 Python 可执行分别表达。上游模型探测使用实际有限 GET、本人同地址凭据和独立 discovery allowlist，不发起推理、不保存配置。现已支持协议的自定义 headers 按源限制与鉴权顺序冻结到执行记录加密信封，公开 JSON 不含秘密；Secret Key 对这些 Bearer 协议按源不消费，未接通签名协议仍拒绝。新信封必须部署同版 API/Worker；本轮已完成业务 API、scheduler 与六类 Worker 的同版重启，验证结果见下文。

本轮已实际取得的证据（后续接线变化仍按范围复核）：

- 独立模型测试最初真实执行失败，原停用检查返回 `configuration_disabled`；补齐严格 test-only 检查后，实际 HTTP 请求又暴露 `stream=false` 被忽略。现按源非流测试真实透传，标准文本默认流保留。失败日志保留，不算最终通过。
- `.runtime/canvas-model-tests-native-e.log`：模型测试 9、目录 9、既有 workspace 11，共 **29 passed / 0 skipped**，19.62 秒；真实随机 MySQL 与生产 Gateway/本机 HTTP，含 readonly host 投影、账号隔离、稳定 ID、CAS 并发与回滚、明文排除、四种冻结身份防绕过、原 `/tasks` 可见及真实文本响应。实例正常 SHUTDOWN 并清理。供应商为受控本机 HTTP，非付费模型验收。
- `.runtime/canvas-model-tests-unit-c.log`：相关合同与文本流 **45 passed**，2.07 秒。先前 Windows pytest 用超长参数值作为测试名超出环境变量长度，属于测试准备错误；已改短稳定测试 ID，保留原大字节断言。
- 原设置页实际 CRUD 浏览器专项 **1 passed / 0 skipped**，21.48 秒：手动渠道与秘密保存、脱敏 ACK、默认文本选择、全新空缓存恢复、修改保持 ID、另一账号空目录、跨窗 409 留原对话框草稿与删除最后渠道。生成 POST 为 0，该项不能作为模型测试或供应商验收。仍观察到真实 `/beefapi/connection` 404，需继续迁移。
- `.runtime/canvas-generation-vertical-c.log`：原 44、真实 broker 3 与蒙版 7，共 **53 passed / 1 failed**，170.49 秒。原 host 模型 fixture 未改，旧生成 UI 与 broker 均通过；唯一失败是蒙版正例轮询遇到精确并发 GET 409 后测试误索引状态。后续只对同一 GET 做有界重读，保留供应商单次 POST、真实产物及 bind 断言，不把此轮写为 54 全绿。
- OpenAI 已保存蒙版的先前真实 MySQL/MinIO 7 项通过，包含跨项目、同项目其他成员私人蒙版、PNG alpha/尺寸及真实 multipart。作者、全新浏览器和成员实际播放原任务取回视频的专项 **1 passed**，22.98 秒，供应商 POST 1/GET 2、浏览器 query-provider POST 1/新生成 POST 0；仍有 M3 助手 status/ui-session 两个真实 404。

本轮没有完成 M2 全协议、M3–M5 或完整一比一门禁。最终构建、原设置测试按钮→历史结果、四类独立测试的真实消息队列/媒体、完整协议与后续工具仍继续实施；原 17 场景的 4 项严格零像素失败继续保留，阈值没有放宽。

## 2026-10-06 M2 BeefAPI 授权、托管目录与精确回归

源 `internal/beefapi` 的设备授权已迁入 Python API→Service→DAO→ORM；HTTP 固定企业源，公开状态与密文设备码/Key 分离，数据库租约支持进程重启及页面关闭后的 scheduler 续授权。Key 先落库再确认，完成确认与模型目录成功持久化后才 connected；目录失败复用已确认的 Key 重试，过期/拒绝/slow_down 分开处理。GET 仅只读状态，原两秒页面轮询不做上游授权工作。

按源保留官方空入口、按账号目录及 headers、同账号合并/换账号替换、断开保留手工渠道与 headers。外部保存只能改变官方启用开关和 headers，不能伪造托管地址、模型/profile/执行身份。新授权的助手默认以新目录为准，授权标记与目录原子保存；即使该授权没有 Astra，也不能在后续刷新中覆盖用户新选择。转录等非生成模型保留源空能力，不被包装成可执行配置。余额不虚构探测接口。

源调用链复核发现 `noteBeefAPIProviderError` 在固定 commit 仅有定义，没有任何实际调用，`MarkZeroBalance` 也仅位于该未使用函数。初步曾接入生成 4xx 后的自动反馈，发现运行差异后已要求回撤；最终按源保持余额 unknown，仅目录读取与连接核验 401/403 标记 revoked。不能把未调用的辅助意图当作源已运行功能，也不顺手修复源缺陷。

原设置页的布局和按钮保留，网页边界同步预开窗口并断开 opener，收到响应后校验固定企业根及确认页/钱包路径，失败关闭空窗口。钱包以实际导航结果表达 opened，不能把服务器给出 URL 当成已打开。标准包依赖和布局未因这一步调整。

新增 `canvas_beefapi_connections` 与轮询租约索引，010 增量和 canonical SQL 已同步；`export_schema.py --check` 通过 **93 表 / 41 张画布表**。当前业务库 DDL 首次尝试在 MySQL 连接握手超时阶段失败；随后只读确认 Docker WSL 引擎已停止，温和启动及普通重启未恢复，官方 `stop --force`→`start` 成功恢复既有容器，没有删除容器/数据卷或使用 `compose down`。恢复后已在业务库 **short_drama** 实际执行 `CREATE TABLE canvas_beefapi_connections` 和 `CREATE INDEX idx_canvas_beefapi_poll`，随后再次只读核验 `ready/missing=[]/changed=[]`。真实业务库表数核对为 93/41，未改既有业务行或媒体。

本阶段当前证据：

- 原统一 `frontend/npm run build` 已通过，标准与独立 canvas 产物合并为 `dist/canvas-app/`；项目模式夹具回归 **1 passed**，11.5 秒。后续 BeefAPI 前端改动仍需重新完成相关检查。
- 原设置→独立模型测试→原任务历史真实浏览器前一轮 **2 passed / 0 skipped**，42.17 秒；最终未知回执/成功与失败窄复核 **1 passed / 0 skipped、1 deselected**，28.29 秒。实际受理后丢 ACK 复用原键，供应商 POST 保持一次，另一账号不可读，未创建画布绑定。均为受控本机 HTTP。
- 官方目录、headers 与生成边界综合 18 模块 **444 passed**，106.28 秒；公开元数据禁止明文秘密的真实红测后，相关 6 模块 **199 passed**，1.03 秒。这些结果不替代真实队列/数据库或收费供应商验收。
- `.runtime/canvas-model-backend-quick.log` 首次全量快测 **1,285 passed / 23 errors**，错误全部为系统 Temp 拒绝访问。改为专属工作区 basetemp 后 `.runtime/canvas-model-backend-quick-b.log` **1,342 passed / 1 failed**，256.29 秒；唯一失败为新增客户端返回 tuple 次序，已修复且对应真实本机 HTTP 客户端专项 **35 passed**，13.51 秒。阶段代码继续变化，最终全量快测仍须复核。
- `.runtime/canvas-beefapi-root-unit.log`：映射、托管 schema、目录及模型测试合同三模块 **81 passed / 0 skipped**，0.59 秒。
- BeefAPI 真实原生临时 MySQL：连接 13、托管目录 8、既有目录 9、workspace 11，共 **41 passed / 0 skipped**，19.73 秒。覆盖先存 Key 后确认、丢确认回执重启、并发租约、只读 GET 零上游、离开页面后 scheduler 继续、账号隔离、默认只处理一次、目录合并/替换、托管字段防伪、headers 加密/脱敏/大小写复用/断开保留。该轮还包含后来因源未实际调用而撤回的余额反馈，不能作为回撤后最终版本的完整验证；相应闭包调整后须重跑。随机实例已 SHUTDOWN 且目录清理；不是业务库 DDL，也不是真实企业登录。
- broker+蒙版后续 10 项实际运行遇到本机 Docker 引擎停止，捕获日志仅 `..FEEEEEE`，中止后没有完整 pytest 汇总，不能声称通过。已精确回收本轮 pytest/native MySQL/自建 Worker 和 HTTP；第三个随机队列 namespace 未确认清空，MinIO 随机桶清理未确认。新增四类独立模型 broker 测试 **4 collected**，尚未实际运行。
- Docker 恢复后，精确确认第三个随机 namespace 四队列均零消息/零消费者，已清除四队列和两交换机；当轮三个同前缀、同创建时间的随机测试桶均为空，也已清除。原 `queues_absent=false` 清理证据保留，另追加恢复清理成功 JSON；正常业务桶仍存在。后续 10+4 真实队列验证尚待重新执行。
- 无 Docker 的模型测试/鉴权合同 **62 passed / 0 skipped**，12.42 秒，含实际本机 HTTP、自定义 header、同源鉴权和另一源匿名；不能代替四类真实 broker。
- 原 17 视觉场景最新未插桩复核为几何全部一致、**16 场景零像素差 / 1 场景失败**：绘图顶栏圆角 4px 的 RGB 17/18 差异仍存在。插桩诊断版一次 17 全零不能替代原门禁；旧差异证据保留，未调低阈值或新增遮罩。

最终回撤后验证补充：

- `.runtime/canvas-beefapi-final-quick.log`：后端 unit/API **1,376 passed**，287.51 秒，退出 0；使用独立 basetemp，另有既有 Starlette/AnyIO 弃用提示。全仓 Ruff check 与 **547 文件** format check 通过。
- 客户端、目录、凭据与传输最终专项 **121 passed**；画布 Node **199 passed**，source:check **832 文件 / 91 项适配**。`.runtime/canvas-beefapi-unified-build-final-stable.log` 最终统一构建退出 0，两个 HTML 入口及三处产物均成功。
- 原 Settings CRUD 与独立模型测试浏览器 **2 passed**；设备授权基础场景 **1 passed**，托管 headers→原生成测试→原任务历史专项 **1 passed / 1 deselected**。托管专项全部 16 项业务断言通过，供应商 POST 1，另一账号读取任务、日志与模型测试均 404，无页面异常或失败路由。使用受控企业 HTTP 与 direct executor，不能代替真实消息队列、正式企业登录或付费模型验收。
- `.runtime/canvas-mask-broker-native-c.log` 曾为 **10 passed / 1 teardown error**：Worker 已停止，但 RabbitMQ 消费者取消尚未完成，精确测试队列删除返回 AMQP 406。已核验只有该随机 namespace 残留，清除四队列及两交换机，保留原失败和 `queues_absent=false` 证据。仅对清理增加 Worker 退出后的有界等待，未改业务输入、断言或供应商 POST 数；`.runtime/canvas-mask-broker-native-d.log` **10 passed / 0 skipped / 0 errors**，106.92 秒，退出 0，全部清理标志为 true。
- 业务库无活动生成、Agent、渲染或批次后，已备份原日志并按既有脚本重启本仓库八角色。八个记录 PID 与对应 Python 子进程均存活，API `/api/v1/test` 返回 ok，OpenAPI 包含五个 BeefAPI 入口；RabbitMQ 六类业务队列分别有一个消费者。项目默认关闭 Celery remote control，因此空 ping 不是 Worker 故障证据。未修改队列命名空间、已有 `.env` 或加密主密钥。

- `.runtime/canvas-model-test-broker-native-b.log`：四类独立模型测试 **4 passed / 0 skipped / 0 errors**，36.22 秒，退出 0。真实 Publisher、AMQP、生产 Celery 和 MinIO 验证私人产物、Range 下载、冻结 header、原任务中心投影、无画布绑定及另一账号 404；受控本机供应商，未调用付费模型。初轮 fixture 使用被禁止的 `X-Canvas-*` 内部前缀而在准入阶段 422，已仅改为合法测试 header，生产校验未放宽，原失败证据保留。四个清理 JSON 均为 true。
- `.runtime/canvas-beefapi-final-native-a.log`：回撤后的连接、托管目录、模型目录与 workspace 最终 **45 passed / 0 skipped**，退出 0；临时 MySQL 已 SHUTDOWN 并清理。这是受控企业 HTTP 与随机测试数据库，不能代替正式企业登录。

实际企业登录、完整协议和 M3–M5 继续实施，当前不满足完整一比一完成定义，goal 保持 active。

## 2026-10-06 M2 三类视频协议与原视频操作

在已完成的 `frontend/canvas/` 独立子包上继续，没有重导源、改依赖版本或修改原视频产品布局。新增画布专用 OpenAI Videos multipart、NewAPI Channel 2 JSON、官方 BeefAPI Seedance flat JSON 与参考预上传；普通宿主 VideoInput 仍最多 9 图，画布视频 DTO 容纳 30 图，实际上限仍按冻结能力检查。没有新增 ORM、表或 DDL，复用当前业务库 **short_drama 的 93 表 / 41 张画布表**；实际媒体仍以 MySQL 与永久 MinIO 资源为准。

准入检查当前项目范围、永久媒体身份和数据库真实类型/尺寸/时长/字节，客户端元信息不能替代；自定义 Channel 2 本地参考在任务创建前返回原 `reference_media_requires_url`，HTTPS 替换使用原 clientOperationId 重提。仅首个 Seedance 上传创建 404/501 才降级 inline，中途断开或完成校验失败不进入收费提交。Worker 冻结本人凭据、headers、模型能力和稳定 Seedance 提交键，已有供应商 ID 恢复仅 poll/download，不重新 create/upload。

本轮按固定源逐项修正了三个实际差异：原视频 UI 默认 `videoArkPrivateAssetUpload=true` 在非 Ark 协议中不应拒绝；未知提交须返回 `submission_unknown` 而不是普通 failed；已确认生成后的实际下载耗尽须被原界面识别为下载失败，并阻止同一结果变成新付费 retry。对应单元或真实 UI/native 红证据均保留，未改原 UI 的入口或降低验收断言。

视频参数从可信能力快照应用空白默认值、时长 enum/range step、画幅与分辨率约束，并冻结规范 config/能力选项/布尔参数。源 `duration/aspectRatio/resolution` 的能力选项别名已按服务目录分支迁移。操作权限原样取声明；Seedance 2.5 的参考模式不会自动扩成编辑、延长或元素替换。纯 wire 编辑测试现在显式声明所用 operation，保留完整 body 断言。

来源分支须区分：源前台 logical/system 目录会重写参数与能力选项；普通私人渠道与内置 BeefAPI 经托管凭据解析后可走 custom 分支，原 UI 已预填 profile 默认值，普通 wrapper 不发送 capabilityOptions。目标以服务端稳定 logicalModelId 统一执行身份和凭据权限，是安全适配边界，不能据此声称源所有 custom 都走 system。原错误分类仍需更完整的逐项对应，当前下载终止采用明确的安全下载类别；未宣称所有供应商错误与原提示均已一致。

已执行的验证与失败历史：

- `.runtime/canvas-video-final-quick.log`：早期稳定点 **1489 passed**；`.runtime/canvas-video-final-quick-b.log`：默认参数接线后 **1563 passed**；`.runtime/canvas-video-final-quick-c.log`：操作权限闭合后 **1575 passed**。这些结果不覆盖后续下载 retry guard；最终同版结果另记。
- `.runtime/canvas-task-state-red.log` 的画布投影真实为 failed（**1 failed / 11 passed**），共享阶段投影修正后相邻 **95 passed**；全局 Ark 默认的六项先全部失败，再按源分支修正。`.runtime/canvas-video-trusted-freeze-red.log` 的八项先失败，接线及异常合同修正后相关七模块 **183 passed**。
- 选项纯函数先 **37 failed**；能力覆写新增八项先 **8 failed / 40 passed**；操作边界新增十二项先 **8 failed / 52 passed**。最终三视频单元模块 **138 passed**，含 transport、预上传及规范选项；测试供应商为受控 HTTP。
- `.runtime/canvas-video-protocol-options-download-native-f.log`：**40 passed / 6 failed / 0 skipped**，199.98 秒；23 个协议/原 UI、12 个策略和 4 个正常选项已绿。新增异常测试错误地预期 422（既有 GenerationRequestError 为 400），另一个 draft 携带不匹配 credentialRef 而先返回 404。只修测试契约与 fixture，保留非法值、零 task/record/binding/POST 和文档不变断言；`.runtime/canvas-video-options-native-g.log` **11 passed / 0 skipped**，25.43 秒。两轮合计 **46 个唯一用例全部通过**，不是宣称 f 本身全部通过。
- 原视频 UI 的真实 POST 首先因 Ark 默认项 422，两次保留报告；修正后 unknown 场景先绿。HTTPS 场景的第一次播放器验收错误地等待未激活的 video，已按源按钮激活，保留 160×90、1 秒真实 MP4 解码断言。最终 `.runtime/canvas-video-ui-final-f-{https,unknown}-report.json` 的原 HTTPS 弹窗、非法 HTTP 禁用、同键两次准入/仅一次供应商 create、刷新同任务、直接 bind、保留原图、原播放按钮、全新 context 恢复，以及 unknown 无原重试/一次 POST 均通过；page_errors 为空。
- `.runtime/canvas-video-download-exhausted-native-red-h.log` **1 failed**：真实下载终止 errorCode 为 archive_failed，不是源可识别的 download_failed，后续权限断言尚未执行。相应 retry 单元 **3 failed / 14 passed**；修正新画布视频限定 guard 后相关七模块 **104 passed**。`.runtime/canvas-video-download-recovery-native-green-i.log` **4 passed / 0 skipped**，74.22 秒：三 503 下载耗尽为 download_failed、canRetry=false、canResume=true；retryOf 新键返回 **409/canvas_task_retry_not_allowed**，task/record/binding 和供应商 POST 数均不增加。401 单次停止、两次 503 后成功、原 ID 查询恢复也通过。query 实际等待两次生产 30 秒，3 次 content GET、零 create，重放不再次下载。
- 画布 Node **199 passed / 0 skipped**；source ledger **832 文件 / 91 处适配**，新增视频 UI 脚本 Node 语法通过。该片没有改 React 产品文件或构建配置，统一生产 build 的既有最终证据保留，不把测试脚本语法当新生产构建。
- 下载终态与 retry guard 冻结后，`.runtime/canvas-video-final-quick-d.log` 最终同版 unit/API **1580 passed**，295.87 秒；全仓 Ruff check 与 format --check 均通过（564 文件）。DeprecationWarning 来自既有 Starlette TestClient alias，未跳过测试。
- `.runtime/canvas-video-services-restart.log` 退出 0：确认业务库活动生成/Agent/渲染/批次为零，备份已有日志后重启八个本仓库角色。新 PID 为 API **536320**、scheduler **468692**、text **539080**、image **492408**、video **522868**、audio **534764**、render **533744**、agent **532316**；八个 wrapper 的路径/角色和各一个 Python 子进程均核实。API `/api/v1/test` 返回 ok，OpenAPI 包含 tasks/model-tests 和 `/tasks/{task_id}/query-provider`。被动 RabbitMQ 检查六类队列各一个消费者、待处理消息为零；继续保留默认 remote control 关闭，没有用空 ping 重复重启。
- 视频 native 结束后实核 MinIO 总桶 2 / 测试桶 0、随机 native 目录 0、4194 listener 0、视频测试进程 0；最新十四个 broker 清理报告均为 true。未调用真实收费模型，视频专项使用直接执行器；前阶段 broker 证据单独记录，不合并冒称本片经收费供应商或全部 AMQP 验收。

视觉诊断独立归档到[绘图诊断](2026-10-06-beeftv-drawing-visual-diagnosis.md)：17 场景几何相同，16 场景 0px，绘图阴影 4px 仍未通过，固定源自身也观察到同像素波动；没有改正式门禁、mask 或阈值。另一个内部 canvas 尺寸问题及候选修复仍未验证。M3 图片工具与两套 RunningHub 的实际入口见[媒体工具审计](2026-10-06-beeftv-media-tool-audit.md)，文字多模态与 thinking 的可达分支见[文字审计](2026-10-06-beeftv-text-multimodal-thinking-audit.md)。这两份审计不等于对应功能实施或运行验收。

M2 完整协议、批次、结构化产物、完整源事件/错误日志与供应商兼容仍继续；M3–M5 尚未整体交付。原 UI 报告仍有助手 status/ui-session 404，不能标记完整一比一，goal 保持 active。

## 2026-10-06：M3 裁切验收与 M2 文字图片下一切片

画布目录继续使用已完成的 `frontend/canvas/` 独立包，不重新导入源文件，不变更依赖或 HTML 入口。
本轮图片工具验证沿原 React 工具闭包和已有上传/素材/保存适配，没有修改产品 UI、CSS 或裁切算法。
源细节与未验范围记录在[媒体审计](2026-10-06-beeftv-media-tool-audit.md)。

裁切首轮 a 刷新后仅 hover 未选择来源，b 匹配了隐藏 toast，c 等待了源 CSS 隐藏的上传 toast，
三轮均保留真实红证据。按源持续的“云端未保存” Popover 修正测试 driver 后，
`.runtime/canvas-crop-browser-native-d.log` **1 passed / 0 skipped**，29.57 秒。
实际完成原上传、拖动取消、inline 默认选区、派生节点与连线、原图不变、fresh context，
commit 两次 503 的同键草稿/同节点恢复及不重传、upload 503 的本人本地草稿/不共享，
以及 owner/member/outsider 权限。两处 503 为故障注入，正常路径不拦截成功 API。
最后 Python 逐像素、MySQL 归属及三份 MinIO 对象原字节/hash 断言实际执行；
37×19 源图导出为 30×16，矩形 `(3,1,33,17)`。源显示标签使用 round、实际导出使用 ceil，
保持该差异，不用标签改算法。green report 为 `.runtime/canvas-crop-native-d-report.json`，
九项布尔均为 true，page_errors 为空。最后实核测试桶、native 目录、4199/4194 listener 和
相关测试进程均为 0。切格、标注及完整 M3 另行验收。

文字图片先接普通 `canvas_text / operation=text / openai_chat.v1`。新增闭合文字引用信封、
准入媒体范围/真实元信息检查和纯 Chat recipe，保留标准文本字符串 DTO；按源对象存储分支在
Worker 执行期签发 MinIO URL，正文在前、图片按引用顺序，签名 URL 不持久化。
保存的文字能力由服务器冻结，未声明视觉能力默认最大图片数 0；当前非视频 16 图限制仍需后续对照。
`streaming=false` 与显式非流沿源关闭 stream；Chat 流启用 include_usage，reasoning 不加入正文。
图片源协议中 Responses/Claude/Gemini 默认 manifest 与 helper 的差异仍在审计范围，
本片没有用其他 wire 冒称源全部文字协议已完成。

补核固定源普通文字 executor 没有传 `onTextDelta/useTextEvents`；节点沿轮询终态后原消费者回填。
本片原 UI 验证也保留加载态/空正文至终态，不新增逐字显示；私人增量/游标/SSE 由独立 HTTP 验证。

`.runtime/canvas-text-references-red-a.log` **2 failed / 3 passed** 实际复现旧准入拒绝与 recipe 拒绝。
实施后相关五模块 `.runtime/canvas-text-references-quick-d.log` **209 passed**；
中间 c 的唯一红为测试误期望 WorkflowError，而资源越权既有合同为 NotFound/404，已修测试契约，
没有放宽生产权限。新增文字实际 HTTP/MySQL/MinIO 与原 UI 验收分别进行，不能用单测代替。

另已确认旧目录升级缺口：GET 只读、正常 BeefAPI reconnect/scheduler 不更新模型执行缓存；
旧 custom cache=None 与 Seedance 旧有效 Ark cache 可能让 UI 可选、实际准入失败。
已补新任务选择 adapter 前按保存目录刷新本人绑定缓存、深拷贝文字/视频能力及配置版本；
相同缓存不变更，目录/偏好/凭据与旧任务冻结版本保留，旧版本画布任务不回写新缓存。
27 项新单测由 **23 failed / 4 passed** 到 **27 passed**，相关五文件 **150 passed**；
真实旧目录及并发目录保存/准入继续单独验证。

完整 unit/API 首轮 `.runtime/canvas-text-final-quick-e.log` 为 **1613 passed / 23 setup errors**，
23 项均在 `tmp_path` 创建阶段遇到 `PermissionError [WinError 5]`，路径为系统临时根的
`pytest-of-coderedma`，业务断言未执行；已换工作区 `.runtime/` 下独立 basetemp 重跑。
未将该轮错误计作通过。本轮没有新 ORM、DDL、依赖、生产 React 组件或构建配置变更。
完整迁移与 goal 仍未完成。

## 2026-10-06：暂停前最新证据收尾

本节汇总此前待收结果，不表示本次文档整理又启动了测试。

- 宫格正常矩阵 `.runtime/canvas-grid-browser-native-b.log` **1 passed / 0 skipped，100.53s**：4/9/16/25 预设及 3×2 自定义、60 派生逐像素/奇数尾部重组、65 MinIO 字节/hash、65 节点/60 连线及权限实际执行。部分上传/commit 失败、409、导航/换账号和标注仍待验。
- 文字图片真实 HTTP/MySQL/MinIO `.runtime/canvas-text-image-runtime-native-a.log` **14 passed / 0 skipped，35.82s**，覆盖冻结图片能力/凭据、顺序、实际签名图片字节、流/非流、本人游标、重放零 POST、401、断流 unknown 和原子边界；供应商为受控 HTTP。原 UI `test_canvas_text_images_browser.py` 仅语法/Ruff/collection 1 条通过，native 尚未执行，普通源节点仍终态回填。
- 目录升级/并发 native-a **12 failed / 14 passed / 23 deselected** 实际暴露 flush 与旧 header 读取；修正 catalog→model 锁顺序、原事务 flush 和 current read 后，`.runtime/canvas-catalog-upgrade-lock-native-b.log` **26 passed / 23 deselected / 0 skipped，41.30s**。旧任务/同键 replay 保留原快照，未将 deselect 算通过。
- 最终同版 `.runtime/canvas-text-final-quick-h.log` **1637 passed，1 warning，290.53s，exit 0**，运行 session 已收回。e 的系统 Temp 23 setup errors 保留，f 的 1636 属中间版本；最终以 h 为准。文字/刷新/runtime 专项 g **111 passed**，范围交集不相加。
- 全仓 Ruff check 通过，format **571 文件**通过；此后新增的原 UI Python 文件另有静态检查。画布 Node **199 passed / 0 skipped**，source ledger **832 文件 / 91 项适配**。本轮无生产 React/依赖/构建配置变更，未重跑统一 build，不把旧构建当本轮新执行。
- 最新文字图片/缓存刷新生产代码尚未重启同版业务服务；上轮八角色重启仅覆盖视频稳定版。最后 runner 核实测试桶、native 目录、4194/4199 listener 与测试进程均 0，原 UI 文字图片没有启动新 runner。

用户要求先记录、下次读取再继续；goal 当前为 `paused`。本次仅更新接续文档与本记录，不执行 DDL、迁移切片、native 或业务重启。下次先读取[接续文档](2026-10-06-beeftv-resume-handoff.md)，保留已有改动，先闭合原 UI 文字图片验收，再部署同版并推进剩余工作。

## 尚未完成且不能省略

- 完整源/目标布局、控件、快捷键、操作序列、默认值、深浅主题、动效与触控对照；基础截图不能替代全部可达功能验收。
- 完整稳定媒体归一化、按源运行分支核对 URL 导入、播放代理、离线草稿重传、跨项目媒体复制与源导入导出。当前已接通画布上传来源素材的永久删除/回收和六类 data 校验；标准来源文件继续沿用原生命周期，未迁入业务的引用需随功能补齐。外部 URL/本地键尚需转为可授权的持久资源，不能将已有字段校验视为完整导入/复用验收。
- 四类生成剩余协议/能力、原版渠道目录的完整供应商兼容、RunningHub、批次、恢复与直接结果回填的完整对照；目录编辑本身已由后续 M2 设置切片接通，真实模型供应商尚未验收。
- 绘图基础持久化、整图历史绑定与原子恢复、其它浏览器已确认删除缓存的恢复、节点变体/跨画布粘贴和整画布/项目完整复制已接通；节点复制时来源并发编辑的笔画及派生图同版验证已通过。无文件夹的绘图与 PNG/WAV/H.264 MP4 混合 ZIP 已通过跨账号传递、并发导出和单画布回执恢复验证；文件夹及自定义封面的双画布 ZIP 已通过独立跨账号验证。回收站显式恢复、未知恢复回执、已保存画布未知删除回执、跨设备目录/预览和不可恢复处置已通过专项；全部历史与媒体的物理清理、未保存草稿回收、绘图子文档未知删除回执、完整并发删除/恢复、其余归档组合、完整工具与生成引用、版本保留及回收仍需继续。媒体加工、Python canonical timeline plan、字幕/声音/帧率/重叠和空隙语义、导演台及真实媒体验收继续实施。
- Python 画布助手、私人对话、原工具/事件、直接改图、付费提议、确认/取消/撤销、原版工作流/插件和剩余导入导出组合；已通过的 ZIP/归档切片以对应章节为准。
- 伴随功能页面、宿主成员/设置入口、生产同源静态部署、跨窗口保存恢复与大图性能。当前宿主夹具浏览器 227 项及补跑生产 3 项已通过，后续变化仍按范围回归；不等同于真实基础设施/供应商的完整标准验收。
- 嵌套子文档的完整共享字段审计、所有源版本字段的十进制串适配、媒体稳定定位归一化、快照/回执不可变保护仍需专项核对。

最终完成条件仍为 M0–M5 全部对照与实际运行验收通过；当前不满足“布局、交互、操作、功能全部一致”的完整声明条件。
