# 无限画布与宿主管理功能对齐审计

记录日期：2026-10-06。目标仓库审计基线：`af863615f5bdb48537e6eaab0393764baa8092c5`；只读源 BeefTV：`4ca2a65a7780a8dfcaaa86c33679f84fb04e055c`。

本文最初是只读审计。随后用户授权模型配置统一与整站清理，具体代码、DDL 与验证见[实施记录](2026-10-05-beeftv-implementation-log.md)。整站页面和导航已物理删除，旧路径转宿主，详见[清理记录](2026-10-06-canvas-only-cleanup.md)。项目/资产数据语义与画布级管理能力仍须继续对齐。没有在用户现有浏览器会话中完成现场验证，也没有进行真实供应商调用；以下对比保留为实施前证据。

## 1. 用户最新范围，优先于早期整站保留方案

用户要求：先比较模型配置的差异，再决定实施；模型配置、项目和资产统一使用当前工作台，画布子包不再显示 BeefTV 的首页和整站管理页面。

- 一比一对照对象是无限画布编辑器内部的布局、交互、操作及创作能力；不再将 BeefTV 整站外壳、首页、项目管理、资产管理、模型配置页和外部 Agent 管理页作为迁移目标。
- 宿主 `/ai_config` 是模型管理入口，`/projects` 是项目管理入口。素材与媒体管理沿用宿主已有入口，并按本审计补足画布资源类型与管理能力。
- 画布内保留节点与连线、视口、快捷键、媒体工具、模型选择与生成参数、保存与恢复、创作助手、同项目多画布操作、资源插入与预览、源已启用的导入导出等编辑器能力。
- 画布内创作助手不等于 BeefTV 的“外部 Agent”整站管理页，不能因为移除后者而删掉编辑器助手。
- 不给标准模式插入画布流程；无限画布生成回填和助手操作继续遵守源行为及仓库已明确的限定例外。
- 继续保留 `frontend/canvas/` 独立子包、依赖锁、HTML、React root 与宿主统一启动、构建脚本，不重新导入或合并两套前端。
- 下文是实施前范围纠正和对比建议，不能把它视为全部修正已完成；模型统一和页面收敛已实施，项目/资产数据语义与剩余画布功能仍需另行切片。

早期迁移文档、`frontend/PRODUCT.md` 与子包说明中关于“保留原版设置页、资产页、伴随整站入口”的描述，有的记录当前实现，有的属于历史方案；不能据此继续扩大迁移范围。下一轮实施应以本节最新要求为准，同步修改相关维护说明。

## 2. 为什么 settings URL 会出现 BeefTV 整站导航

审计时的画布 `router.tsx` 不仅注册具体编辑器 `/canvas/:id`，还注册 `/canvas` 画布库、`/assets`、`/settings`、`/tasks`，所有路由都包在 `UserLayout → AppWorkspaceShell` 内。当前[路由](../../frontend/canvas/src/router.tsx)已经改为编辑器与宿主重定向。

审计时的 `workspace-sidebar-nav.tsx` 明确渲染“首页、项目、资产、模型配置、外部 Agent”。所以当时 `/canvas-app/settings?section=channels` 实际命中源 `SettingsPage` 并显示整站外壳。该组件与源设置页已删除，来源哈希保存在[适配账本](../../frontend/canvas/source-adaptations.json)。

这说明迁入范围过宽，但不说明整个 BeefTV 产品的全部功能已经迁移可用：当前首页、外部 Agent 等并未都注册成可执行页面，未知路由会跳宿主 `/projects`。本结论来自路由和组件源码，未宣称成功操作了用户的现有浏览器。

不能只隐藏侧栏或删除几个 router 条目：

- 审计时的 `app-top-nav.tsx` 在具体编辑器路径隐藏可见外壳，但仍安装整站 Ctrl/Cmd+K 面板、workspace 导航事件和桌面更新 Hook；已随本轮整站清理删除。
- [workspace-route-modules.ts](../../frontend/canvas/src/lib/workspace-route-modules.ts) 仍有首页、外部 Agent、资产、设置、任务、创建、项目与项目详情的动态模块入口及预加载。
- 编辑器顶部返回、项目菜单、缺少模型时的设置引导、助手设置与页面内直接导航都需要一起检查。
- 两包使用不同 React root，画布路由有 `/canvas-app` basename。跨包进入 `/ai_config`、`/projects` 应采用宿主导航适配与全页跳转，不能继续调用源 `navigate('/ai_config')` 而生成 `/canvas-app/ai_config`。
- 离开前仍要等待串行保存和相关配置回执；失败或 409 保留草稿，不得以“改入口”为由跳过保存保护。

最终子包只开放具体画布编辑器；旧整站深链应按用途安全回宿主，不再渲染源首页或整站菜单。保留所需 Provider、CSS、浮层和编辑器容器，避免删除外壳时改变原画布尺寸及操作。

## 3. 模型配置：差异较大，但已有宿主桥接可复用

### 3.1 三方对照

| 对照项 | 当前工作台 | BeefTV 固定源及迁入结构 | 统一宿主时需要做的事 |
| --- | --- | --- | --- |
| 基础组织 | 一条 `AIModelConfig` 对应一个模型 | channel 共享连接，包含多个 model profile；后端还有 variant 到实际 SKU 的映射 | 保留稳定 `config_id`；需要连接分组时放入宿主内部，不保留源整站模型中心 |
| 能力类别 | 前后端均支持 text/image/video/audio；宿主 UI 已有四个分组 | 模型 profile 同样声明四类 capability | 直接映射，不能将宿主描述为没有音频模型 |
| 基础字段 | 名称、厂商、模型编码、地址、API Key、启停、默认、版本 | 相同基础信息，另有渠道名称、别名、图标、排序 | 复用宿主 CRUD；厂商显示名与调用协议分开 |
| 账号归属 | 通过可信 Actor 限定本人 `owner_user_id`，没有管理员自动共享模型例外 | 源有 system/user 渠道；当前 Python catalog 和绑定属于本人 | 沿用宿主本人配置权限；画布投影的 `scope: system` 仅表示只读，并非全局共享模型 |
| 调用协议 | 创建/编辑 DTO 未暴露显式 protocol；后端按类型、地址和已有缓存选 adapter | 模型 profile 显式 protocol/API format，可区分 Chat、Responses、不同图片和视频 API | 补宿主可信高级配置，不能只靠 provider 名称或模型名称猜协议 |
| 文本能力 | 已有简化能力结果和业务参数，未开放完整 profile 编辑 | streaming/thinking、参考图片/视频数量、大小等限制 | 补可信能力声明与前后端校验；未声明视觉能力的宿主模型不能自动获得图片引用能力 |
| 图片能力 | 简化参考图和可用参数信息 | 参考图、蒙版、尺寸、质量、透明背景、格式、份数、可选值与默认值 | 将必要 profile 纳入宿主高级配置，保留画布原参数交互 |
| 视频能力 | 部分首尾帧、时长、分辨率、音频能力 | 图/视频/音频参考限制、操作、比例/像素/时长、音轨/水印、variant 到上游 SKU | 差异较大，须保存规格和执行映射；不是添加几个下拉字段就完整支持 |
| 鉴权与连接 | 常规单 API Key，服务端加密 | Secret Key、自定义 headers、referenceAssetOrigin、托管 credentialRef/BeefAPI 授权 | 必要扩展在服务端加密存储，由宿主管理；浏览器只读脱敏状态 |
| 默认与偏好 | 每类全局默认，另有业务上下文偏好 | text/image/video/audio/assistant 独立选择及编辑器生成参数 | 模型身份统一；保留画布上下文偏好，定义明确默认优先级 |
| 发现与测试 | 模型发现、CRUD、静态能力；发现可处理官方 Gemini/Anthropic 认证 | 目录 profile、企业目录同步、私人独立测试任务 | 复用发现和测试服务；发现到 ID 不等于执行协议已支持或真实生成已验收 |
| 其他能力 | 没有对应的完整渠道级管理 | 并发、定价、企业钱包、RunningHub 工作流等 | 分别列能力与执行状态；不能将结构存在描述为 Python 已完整执行 |

宿主字段依据：[前端 DTO](../../frontend/src/api/types/ai-model-configs.ts)、[四类 UI 分组](../../frontend/src/features/ai-config/AiConfigSession.tsx)、[Pydantic 合同](../../backend/src/short_drama/schemas/ai_model_config.py)、[ORM](../../backend/src/short_drama/domain/ai_model_config.py)、[适配器选择](../../backend/src/short_drama/ai/adapters.py)。

源结构依据：`D:\code\BeefTV\backend\internal\model\models_channel.go` 的 `ModelChannel / ChannelModel / ChannelModelVariant`，以及 `web/src/stores/use-config-store.ts` 的 `ModelChannel / AiConfig`。当前 Python profile/鉴权合同见 [canvas_model_catalog.py](../../backend/src/short_drama/schemas/canvas_model_catalog.py)，画布能力结构见 [model-capabilities.ts](../../frontend/canvas/src/lib/model-capabilities.ts)。

### 3.2 已复用哪些，为什么仍存在两套写入来源

现有 [host-model-config.ts](../../frontend/canvas/src/services/host-model-config.ts) 已将未绑定目录的宿主模型投影为 `host-<config_id>` 只读 channel，携带 `logicalModelId=config_id` 和服务端凭据引用；画布可以读取并选择宿主配置。

同时，[CanvasModelCatalog](../../backend/src/short_drama/domain/canvas_model_catalog.py) 还保存本人 `channels_json / credentials_cipher`，[CanvasChannelModel](../../backend/src/short_drama/domain/canvas_model_catalog.py) 将源 channel/model 映射到同一张 `ai_model_configs` 表。原渠道保存会创建或更新该表的执行配置。

[CanvasWorkspaceDAO.models()](../../backend/src/short_drama/dao/canvas_workspace_dao.py) 排除已有 channel binding 的宿主行以避免重复展示；宿主模型 CRUD 列表没有同样排除。这是“部分共用模型表，但目录、能力和凭据仍有两个管理来源”，不能简单称为两套完全独立数据库。

### 3.3 必须先解决的实际一致性风险

以下由源码核对得出，没有执行破坏性复现：

1. 宿主修改已经绑定 channel 的模型，只修改执行行，旧 catalog profile/凭据未同步。宿主更换模型编码后，`refresh_runtime_model_locked()` 会因编码与绑定不一致而拒绝新画布任务。
2. 宿主替换同类绑定模型的 API Key 后，画布 `runtime_credentials_locked()` 对普通渠道仍从 catalog 加密凭据读取；随后保存旧渠道又可能覆盖宿主模型行与密钥。
3. [saveLocalModelConfig()](../../frontend/canvas/src/services/api/workspace.ts) 每次保存编辑器偏好也提交 `channels`。catalog `_persist_locked()` 会软删除未出现在 active 集合的绑定配置。只改为展示宿主模型、继续提交 `channels: []`，存在误删旧绑定模型的风险。
4. 当前宿主只读投影只提供基础 capability，没有完整 protocol/profile；能选模型或执行纯文字不等于完整参考图、视频规格、thinking 等功能可用。

关键服务依据：[canvas_model_catalog_service.py](../../backend/src/short_drama/service/canvas_model_catalog_service.py) 的 `_update_model`、`_persist_locked`、`refresh_runtime_model_locked`、`runtime_credentials_locked`，及 [AIModelConfigService](../../backend/src/short_drama/service/ai_model_config_service.py)。

### 3.4 推荐的统一模型方案

- 宿主 `/ai_config` 成为创建、编辑、启停、删除和凭据的唯一写入口。保留当前基础表单，必要的协议、能力、规格和扩展鉴权放在高级配置中；不再复制整张 BeefTV 设置页面。
- 为每个稳定 `config_id` 建立可信 protocol、capability profile、版本与必要 variant 映射。可考虑一对一扩展实体，具体 DDL 在实施前另行定稿；`capability_cache` 继续作为派生缓存，不能充当唯一可信配置来源。
- 迁移现有 catalog 的额外能力与加密凭据，保留原 `config_id`，保留旧 `channel::model` 选择到 config_id 的兼容映射。不能靠删除旧表、重新创建 ID 完成统一。
- 画布只读宿主模型目录，只写自己的模型选择、助手选择和生成参数偏好。服务端已有 `channels` 可省略的合同，但停止渠道写入仍须与旧数据迁移一起实施。
- 配置后返回原画布并刷新目录；导航遵守自动保存和冲突保护。原画布模型选择、参数操作继续保留。
- 历史任务继续使用受理时冻结的模型、能力和加密凭据信封；统一只改变新任务的读取来源，不改历史任务快照。

当前 Python 协议白名单仅覆盖阶段已接通的 Chat、Responses、部分图片/音频/视频及 BeefAPI Seedance 分支。Claude、Gemini、RunningHub 和其他源协议不能笼统称为生成已可用。企业授权/钱包、复杂工作流和渠道级并发的执行支持需要单列实施与验收，不因移除设置页就默认删除，也不强制用户继续使用 BeefTV 官方服务。

## 4. 项目：父实体已统一，管理入口和生命周期还没有完全对齐

| 对照项 | 当前实现与差异 | 对齐方向 |
| --- | --- | --- |
| 父项目 | 已使用宿主 `projects` 和 standard/infinite_canvas 模式 | 保留统一创建弹窗、列表、权限和成员体系 |
| 多画布 | `Project.id → ProjectCanvas[]`，主画布配置已经存在 | 宿主管理项目，编辑器内部继续同父切换、新建、复制、重命名和删除 |
| 项目详情 | 无限项目在详情渲染前直接 `CanvasLaunch`，宿主信息/成员/资源管理入口被跳过 | 为无限项目提供可达的宿主管理入口；不能直接套用会请求和创建 episodes 的标准项目概览 |
| 名称 | 宿主卡片读 `Project.name`，编辑器顶栏读 root canvas.title；顶栏重命名只改画布 | 区分项目名与画布名，项目名走宿主 Project 契约；不要批量改所有子画布名称 |
| 删除与归档 | 宿主归档父项目；画布删除按版本归档单画布、切换主画布、记录回收回执，最后一张还会归档父 | 明确父归档与子画布回收的权限、恢复粒度和主画布选择，统一服务编排 |
| 复制与分类 | 源列表复制整个工作区，编辑器复制当前文档；源项目文件夹为个人整理数据 | 不能用单画布复制替代完整项目复制；保留个人分类隔离，管理入口迁入宿主 |

关键依据：[ProjectRoute.tsx](../../frontend/src/pages/projects/ProjectRoute.tsx)、[CanvasLaunch.tsx](../../frontend/src/features/projects/CanvasLaunch.tsx)、[ProjectOverview.tsx](../../frontend/src/features/projects/ProjectOverview.tsx)、[ProjectService](../../backend/src/short_drama/service/project_service.py)、[CanvasService](../../backend/src/short_drama/service/canvas_service.py)、[画布生命周期 Hook](../../frontend/canvas/src/pages/canvas/use-canvas-project-lifecycle.ts)。

三种身份必须分开：

- 源文档 `id` 是 `canvas.source_key`，应作为不透明字符串保留。
- `workspaceProjectId` 对应宿主十进制字符串 `Project.id`。
- 源 `projectId` 是 BeefTV 另一套短剧业务绑定，不是宿主父项目 ID。Python 当前明确拒绝非空旧绑定，不能把宿主 ID 塞进去来启用源短剧侧栏。

源短剧侧栏 `/projects/{id}/canvases`、`/settings`、`/chapters/{unitId}` 在宿主不存在对应业务页面，不能只换 URL 前缀。源主页、全部项目、创建项目、退出、最后画布删除后的返回，应走宿主导航适配。

额外生命周期细节：宿主 `DELETE /projects/{id}` 只归档父 Project，不生成 `canvas.archive` 回执；当前画布回收列表依赖本人回执，因此两者不能直接互换。源编辑器“删除项目”菜单实际闭包只删除当前画布，源列表批量删除才会展开工作区全部画布。源项目文件夹删除还会先删除其中画布，与资产分类删除保留资产的规则不同；需明确保留或调整的语义，不能静默替换。

## 5. 资产：物理文件已复用，资源类型和管理记录差异较大

### 5.1 现有四层身份

| 层次 | 用途与现状 | 统一时的处理 |
| --- | --- | --- |
| 宿主 `Asset` | 角色/场景/道具业务素材，描述、提示词、当前图片、参考图、确认状态与个人/项目/分集范围 | 保留原业务含义，不能将所有文本/视频/3D 资源硬映成三类素材 |
| 宿主 `MediaAsset` | 关联生成记录和 MediaFile 的生成媒体资产 | 保留生成来源与作者；普通上传不能伪造生成记录 |
| 画布 `CanvasLibraryAsset` | 本人六类资源元数据：text/image/video/audio/model/entity；独立分类、收藏与源键 | 首步保留记录和稳定引用，宿主提供统一管理入口与适配查询 |
| 文件与资源 | 图片/视频/音频复用 `MediaFile + MinIO`；非媒体文件使用 `CanvasBinaryResource` | 保留 media/binary 身份、checksum、引用、发布状态和删除保护 |

依据：[Asset](../../backend/src/short_drama/domain/asset.py)、[MediaAssetDAO](../../backend/src/short_drama/dao/media_asset_dao.py)、[CanvasLibraryAsset/Reference/Folder](../../backend/src/short_drama/domain/canvas_library.py)、[CanvasBinaryResource](../../backend/src/short_drama/domain/canvas_resource.py)。这里的 `model` 是 3D 模型资源，**不是 AI 模型配置**；`entity` 也不等于宿主三类业务素材。

### 5.2 当前数据来源和界面缺口

- 宿主 `/assets/:kind` 是角色、场景、道具素材库；`/media-library/:kind` 当前页面只开放 image/video。后端生成媒体记录可包含 audio，不能据页面范围断言后端没有音频。
- 宿主媒体库查询 inner join `MediaAsset → AIGenerationRecord → MediaFile`，不是列出全部 MinIO 文件或全部上传。
- 媒体生成归档后建立 `MediaFile + MediaAsset`；画布媒体交付另外建立 `CanvasLibraryAsset + Reference`，引用同一 MediaFile，并没有因此再存一份物理字节。统一列表应按稳定媒体身份归并显示，同时保留生成记录、库记录与画布用途，不丢多来源关联。
- 普通画布上传只建立 `MediaFile` 或 `CanvasBinaryResource` 与上传回执，不自动创建生成 `MediaAsset`；登记私人库是另一条操作。只跳现有宿主媒体库会遗漏此类上传、文本和 3D 文件。
- 文字任务交付直接返回正文，不自动建立 MediaAsset 或私人库资产；未存库结果仍有本人 CanvasResult/生成记录，图内正文由节点与历史保存。不能为了列表统一给所有文本任务伪造媒体记录。
- 源库底层支持六类，但源 `/assets` 页面排除 entity，tab 实际是 text/image/video/audio；model 有上传与预览，不是单独 tab。原托盘只显示图片。保留源编辑器交互时按实际行为对照，不将底层六类误说为六个已存在 tab。

链路依据：[generation_archive.py](../../backend/src/short_drama/service/generation_archive.py)、[canvas_task_outputs.py](../../backend/src/short_drama/service/canvas_task_outputs.py)、[canvas_resource_service.py](../../backend/src/short_drama/service/canvas_resource_service.py)、[宿主路由](../../frontend/src/app/App.tsx)。

### 5.3 推荐的统一资产方案与保护要求

- 宿主继续保留三类业务素材入口；现有资产管理补通用资源视图/查询，纳入画布上传、文本、音频和 3D 等实际需要的类型及本人分类。样式沿用宿主，不再开放 BeefTV 独立资产页面。
- 源已有搜索、分类/文件夹、未分类、收藏、最近使用、来源项目、排序、网格/列表、详情、标签/备注、下载、批量移动/导出、归档与恢复等管理操作，应逐项映射到宿主；必要的选择和预览继续保留在画布内。不能以替换资产首页为由取消这些已启用能力，也不能将宿主新增的类型入口描述成源本来已有。
- 首阶段以宿主入口聚合和业务适配为主，保留现有实体表、source_key、`resource:<ID>` 与引用关系；“统一管理”不要求马上把不同语义的表强行合并。实体重构如确有必要，应另定迁移和回滚方案。
- 画布内选取、拖入、预览、节点/时间线绑定与原回填交互保留，由适配层读取宿主资源。跨项目/个人资源导入仍按现有复制与发布规则处理，不只复制临时 URL。
- 库记录标记 confirmed 不等于已发布共享。标准模式明确采用时发布；画布作品进入共享图时按原流程发布。私人库、提示词、生成记录和助手对话继续本人可见。
- 删除库记录、归档资源、清理物理文件是不同操作。现有画布删除会保护图、历史、任务和其他业务引用；只回收符合本人和上传回执条件的资源。标准导入与生成资源不能因新统一列表被错误套用画布上传清理流程。
- 保留作者和范围校验、不可变历史、签名 URL 刷新、引用删除保护、上传/复制幂等和旧 ID 映射，不能通过批量删除画布资源表实现页面收敛。

## 6. 建议实施顺序与受影响模块

本轮不执行这些步骤。后续修改前重新检查工作区，阅读具体调用链和测试，再给出切片计划；涉及表变更时同时完成 ORM、实际 DDL、完整 SQL 与增量迁移。

1. **确定编辑器边界和管理映射。** 对 router、UserLayout/AppWorkspaceShell、命令面板、预加载、顶部菜单、设置引导和旧深链列清单；管理入口全部归宿主，保留画布容器和内部操作。
2. **统一模型写入与运行配置。** 涉及宿主模型 DTO/表单/API/Service/ORM、画布 catalog/credential freeze、投影与偏好保存。先解决能力元数据和旧绑定迁移，再停止渠道写入；保留稳定 ID 与旧任务冻结快照。
3. **补无限项目的宿主管理入口。** 涉及 ProjectRoute/项目详情/CanvasLaunch、父子命名、归档与恢复编排；不引入伪分集，不改变标准模式。
4. **在宿主管理画布资源。** 涉及宿主素材/媒体页面和查询、画布库与资源适配，补缺失类型和来源、去重展示、引用与删除保护。先复用现有记录，不先做大范围表合并。
5. **收敛子包路由与导航。** 源 settings/assets/canvas 库/tasks 等整站页面退出可达路由；外部 Agent 和首页模块不再由画布整站外壳引入。所需高级配置、私人历史和资源能力通过宿主入口或编辑器原有交互提供。
6. **同步维护记录并专项验证。** 修改源哈希跟踪范围内的文件时记录明确 adaptation，不重跑 importer 覆盖本地适配；更新受影响的 API、产品、子包与接续说明。

## 7. 下一轮必须覆盖的验收点

| 范围 | 需要验证的具体行为 |
| --- | --- |
| 管理入口 | 旧 `/canvas-app/settings?section=channels`、assets、canvas 库、首页/agents/tasks 深链不再显示 BeefTV 整站；具体 source_key 深链、登录返回与刷新正常 |
| 模型一致性 | 宿主新增、改名称/编码/地址/密钥/协议、启停/删除后，画布选择和执行一致；不被偏好保存覆盖；旧 `channel::model` 恢复，无 `channels: []` 误删除 |
| 能力和任务 | 图片/视频/音频引用与参数限制前后端一致，变体执行正确；未实现协议明确错误；历史任务按冻结配置运行，未知受理不重提 |
| 保存和导航 | 离开等待自动保存；409/失败保留草稿；返回刷新目录；跨包链接没有错误 basename 前缀；标准模式不变 |
| 项目 | 信息/成员/资源可达；项目名与画布名一致且各自独立；多画布/主画布创建切换、单图删除、最后一图删除、父归档与恢复语义正确 |
| 资源 | 上传与生成都可找到；同一媒体去重且保留来源；文本/音频/3D可管理；源资源插入交互、跨项目复制、私人隔离与共享图发布、历史引用和删除保护正常 |
| 编辑器一比一 | 管理外壳退出后，布局、容器尺寸、快捷键、节点/连线、绘图/裁切/宫格、撤销重做、助手、生成回填与导入导出继续按固定源逐项对照 |

旧 `verify-model-settings-python.mjs`、`verify-beefapi-python.mjs` 以及 `verify-resource-python.mjs` 的部分路径依赖源整站 settings/tasks/assets。它们过去的结果仅覆盖当时 UI；路由收敛后需改为宿主管理验收，底层权限、持久化和协议测试仍可保留。宿主 `project-modes.spec.ts` 的 HTML 夹具跳转也不证明真实编辑器或项目管理已完整验收。

本轮只做静态审计和文档链接/差异核对，未运行产品测试、构建、数据库迁移或真实模型验收；旧验证不能充当本次范围修正已完成的证明。
