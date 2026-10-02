# 后端接口与前端接入审计

审计日期：2026-10-02。范围为当前工作区源码（含尚未提交的代码），以实际挂载的后端路由和当前前端页面、组件、Hook 的可达调用为依据。

共核对 16 个路由模块、139 条 HTTP 方法与路径模板：130 条有可达前端调用；真正缺少页面功能的有 4 条，集中在分镜归档、分镜排序和项目活动记录。另有 1 条保留的全局素材关联接口、1 条功能已覆盖的别名、3 条诊断接口没有直接的前端调用。

| 分类 | 路由数量 | 判定 |
| --- | ---: | --- |
| 已接入 | 130 | 页面、组件或 Hook 有可达请求链路 |
| 仅封装，无 UI 调用 | 3 | 分镜归档、单镜移动、整体排序 |
| 未封装、未调用 | 1 | 项目活动记录 |
| 保留接口，无 UI 调用 | 1 | 全局素材关联分支；当前服务仅允许已有全局关联 |
| 别名，功能已覆盖 | 1 | 批量生成详情的 `/items` 别名 |
| 诊断接口，无 UI 调用 | 3 | 服务、数据库、MinIO 检查 |

## 需要补接的业务功能

以下路径统一省略 `/api/v1`；`E` 表示 `/projects/{project_id}/episodes/{episode_id}`。

| 功能 | 接口 | 当前前端情况 | 源码依据 |
| --- | --- | --- | --- |
| 分镜归档 | `DELETE E/shots/{shot_id}` | `storyboardApi.remove` 已封装，页面没有归档或移除入口 | [后端：episode_storyboard.py:96](../../backend/src/short_drama/api/v1/episode_storyboard.py)；[封装：storyboard.ts:24](../../frontend/src/api/modules/storyboard.ts) |
| 移动单个分镜 | `POST E/shots/{shot_id}/move` | `storyboardApi.move` 已封装，页面没有上移、下移或拖动操作 | [后端：episode_storyboard.py:112](../../backend/src/short_drama/api/v1/episode_storyboard.py)；[封装：storyboard.ts:25](../../frontend/src/api/modules/storyboard.ts) |
| 整体调整分镜顺序 | `PUT E/shots/order` | `storyboardApi.order` 已封装，页面没有排序提交操作 | [后端：episode_storyboard.py:70](../../backend/src/short_drama/api/v1/episode_storyboard.py)；[封装：storyboard.ts:26](../../frontend/src/api/modules/storyboard.ts) |
| 项目活动记录 | `GET /projects/{project_id}/activity` | 未找到请求封装和页面调用；共同创作区域只显示成员和邀请记录 | [后端：collaboration.py:71](../../backend/src/short_drama/api/v1/collaboration.py)；[页面：ProjectCollaboration.tsx](../../frontend/src/features/projects/ProjectCollaboration.tsx) |

[StoryboardStage.tsx:448](../../frontend/src/pages/projects/episode/StoryboardStage.tsx) 当前提供新增、内容编辑、候选生成和采用，没有归档或排序控件；全量源码调用检索也未发现 `remove`、`move`、`order` 的使用。

建议先补分镜归档和排序，使用户能够整理生成结果；再在项目详情的共同创作区域加入活动记录。单镜移动与整体排序属于同一功能，可以根据交互选择对应接口。

## 已封装但没有触发的保留接口

`PUT /libraries/global/assets/{asset_id}` 在 [assets.ts:28](../../frontend/src/api/modules/assets.ts) 的 `assetLibraries.link` 通用封装中存在，但没有传入 `global` 目标的页面操作：

- [AssetsPage.tsx:16](../../frontend/src/pages/assets/AssetsPage.tsx) 的全局/个人库面板没有 `importFrom` 或 `shareTo`。
- [ProjectResourceLibrary.tsx:11](../../frontend/src/features/projects/ProjectResourceLibrary.tsx) 从全局/个人库导入，目标是项目；启用账号验证时走异步独立复制。
- [AssetsStage.tsx:17](../../frontend/src/pages/projects/episode/AssetsStage.tsx) 从项目导入到本集、从本集共享到项目。
- [AssetLibraryPanel.tsx:213](../../frontend/src/features/assets/AssetLibraryPanel.tsx) 的通用关联调用因此仅能到达项目和本集路径。

这条 PUT 不是编辑素材接口。编辑文字已有 `PATCH /assets/{asset_id}` 调用。当前 [AssetLibraryService._may_link / link](../../backend/src/short_drama/service/asset_library_service.py) 的全局分支仅接受已经在全局库中的关联；它不能直接实现“把项目素材添加回个人库”。如果需要这个功能，还需先补充后端分享或复制契约。

## 接口已接入，但能力只覆盖一部分

音频资产列表：`GET /media-library/items` 的 [后端 media_type](../../backend/src/short_drama/api/v1/media_library.py) 接受 `image`、`video`、`audio`；前端 [AssetFilters](../../frontend/src/api/types/generations.ts) 和 [MediaLibraryPage.tsx:14](../../frontend/src/pages/media-library/MediaLibraryPage.tsx) 仅支持图片、视频，[App.tsx:36](../../frontend/src/app/App.tsx) 也拒绝音频资产路由。

因此缺少统一浏览、搜索音频资产的库入口。音频任务预览、试听，以及分集声音面板的候选采用已经接入，不能把这些现有能力算作缺失。这是已接入接口的类型覆盖缺口，不额外增加未接入路由数量。

## 不应计作业务缺失的接口

- `GET /ai/generation-batches/{batch_id}/items` 与 `GET /ai/generation-batches/{batch_id}` 绑定 [同一个后端 detail 函数](../../backend/src/short_drama/api/v1/generation_batches.py)。[generationBatches.detail](../../frontend/src/api/modules/generation-batches.ts) 和 [批次详情页面](../../frontend/src/features/generations/BatchGeneration.tsx) 已通过后者读取并分页展示 `items`。
- `GET /test`、`GET /test/db`、`GET /test/minio` 是 [诊断接口](../../backend/src/short_drama/api/v1/test.py)，当前没有产品页面入口，不需要为普通业务页面补接。

## 人工核对与统计口径

1. 后端以 [v1/router.py](../../backend/src/short_drama/api/v1/router.py) 的 `include_router` 为准，提取 16 个已挂载模块的路由装饰器。按 HTTP 方法与路径模板计数，同一函数的两个别名分别计数；`/{action}`、`/{kind}` 这类动态模板不按枚举值展开。不包含 FastAPI 自动生成的 `/docs`、`/openapi.json`。
2. 前端扫描 `frontend/src` 的 HTTP 请求及下载链接，从 `main.tsx` 跟踪静态、动态导入和 API 方法引用；核对共享组件参数及页面入口，避免把仅导入或仅封装当作接入。当前扫描到的前端请求路径均能匹配已挂载路由。
3. 写作接口通过显式 `WritingTransport` 接口间接调用，自动引用检查容易漏判。已核对 [useEpisodeWriting.ts:9](../../frontend/src/features/projects/useEpisodeWriting.ts) → [writing-session.ts](../../frontend/src/features/projects/writing-session.ts)：`get`、`novel`、`script`、`select`、`confirm` 都有调用；[SourceStage.tsx:47](../../frontend/src/pages/projects/episode/SourceStage.tsx) 和 [NovelScriptGeneration.tsx:131](../../frontend/src/features/projects/NovelScriptGeneration.tsx) 提供实际交互入口。这 5 条路由均列为已接入。
4. 素材库通用 `path(scope)` 会展开全局、项目、本集三种路径；已根据所有面板的实际目标修正全局 PUT 分支。全局、项目、本集的创建、列表、移除及其他素材操作均有页面入口。
5. 批次控制模板已人工核对 `pause`、`resume`、`cancel` 三种页面动作；素材和媒体两种异步项目导入均有前端调用。账号注册、登录、当前账号、退出、邮箱验证、密码重置、账号搜索及模型偏好均已接入。

“已接入”仅表示当前源码有可达请求链路，受登录状态、权限和功能开关影响。此次未启动服务、未逐条调用真实接口、未触发模型生成；参数兼容性、运行时权限及端到端成功率不在结论内。审计只新增此文档，未修改业务实现。

## 完整路由对照表

下表路径省略 `/api/v1`。每个模块的链接指向后端定义文件，“后端行”是路由装饰器位置。前端证据列给出请求封装和至少一个调用点；仅展示一处证据不表示只有一处调用。通过 Hook、会话对象的间接调用同样计入已接入。

### [ai_generations.py](../../backend/src/short_drama/api/v1/ai_generations.py)（10 条）

| 方法 | 路径 | 状态 | 前端证据 | 后端行 |
| --- | --- | --- | --- | ---: |
| POST | `/ai/generations/audio` | 已接入 | `generations.generateAudio` → [SoundPanel.tsx:77](../../frontend/src/features/projects/SoundPanel.tsx)<br>`nativeVoiceApi.design` → [NativeVoicePanel.tsx:58](../../frontend/src/features/projects/NativeVoicePanel.tsx) | 27 |
| POST | `/ai/generations/text` | 已接入 | `generations.generateText` → [NovelScriptGeneration.tsx:112](../../frontend/src/features/projects/NovelScriptGeneration.tsx) | 34 |
| POST | `/ai/generations/image` | 已接入 | `generations.generateImage` → [useAssetImageGeneration.ts:176](../../frontend/src/features/assets/useAssetImageGeneration.ts) | 41 |
| POST | `/ai/generations/video` | 已接入 | `generations.generateVideo` → [ShotVideoCandidates.tsx:81](../../frontend/src/features/projects/ShotVideoCandidates.tsx) | 48 |
| GET | `/ai/generations` | 已接入 | `generations.list` → [useAssetImageGeneration.ts:43](../../frontend/src/features/assets/useAssetImageGeneration.ts) | 55 |
| GET | `/ai/generations/{generation_id}` | 已接入 | `generations.detail` → [useAssetImageGeneration.ts:64](../../frontend/src/features/assets/useAssetImageGeneration.ts) | 100 |
| GET | `/ai/generations/{generation_id}/records` | 已接入 | `generations.records` → [TaskDetail.tsx:50](../../frontend/src/features/generations/TaskDetail.tsx) | 105 |
| POST | `/ai/generations/{generation_id}/cancel` | 已接入 | `generations.cancel` → [useAssetImageGeneration.ts:210](../../frontend/src/features/assets/useAssetImageGeneration.ts) | 110 |
| POST | `/ai/generations/{generation_id}/resume` | 已接入 | `generations.resume` → [useAssetImageGeneration.ts:211](../../frontend/src/features/assets/useAssetImageGeneration.ts) | 115 |
| POST | `/ai/generations/{generation_id}/retry` | 已接入 | `generations.retry` → [useAssetImageGeneration.ts:215](../../frontend/src/features/assets/useAssetImageGeneration.ts) | 120 |

### [ai_model_configs.py](../../backend/src/short_drama/api/v1/ai_model_configs.py)（8 条）

| 方法 | 路径 | 状态 | 前端证据 | 后端行 |
| --- | --- | --- | --- | ---: |
| POST | `/ai-model-configs/discover-models` | 已接入 | `aiModelConfigs.discoverModels` → [ModelIdentifierField.tsx:41](../../frontend/src/features/ai-config/ModelIdentifierField.tsx) | 23 |
| GET | `/ai-model-configs` | 已接入 | `aiModelConfigs.list` → [ConfigSelect.tsx:39](../../frontend/src/features/generations/ConfigSelect.tsx) | 28 |
| POST | `/ai-model-configs` | 已接入 | `aiModelConfigs.create` → [AiConfigPage.tsx:59](../../frontend/src/pages/ai-config/AiConfigPage.tsx) | 39 |
| GET | `/ai-model-configs/{config_id}` | 已接入 | `aiModelConfigs.get` → [AiConfigPage.tsx:65](../../frontend/src/pages/ai-config/AiConfigPage.tsx) | 44 |
| GET | `/ai-model-configs/{config_id}/capabilities` | 已接入 | `aiModelConfigs.capabilities` → [ShotVideoCandidates.tsx:55](../../frontend/src/features/projects/ShotVideoCandidates.tsx) | 49 |
| PATCH | `/ai-model-configs/{config_id}` | 已接入 | `aiModelConfigs.update` → [AiConfigPage.tsx:58](../../frontend/src/pages/ai-config/AiConfigPage.tsx) | 54 |
| DELETE | `/ai-model-configs/{config_id}` | 已接入 | `aiModelConfigs.remove` → [AiConfigPage.tsx:80](../../frontend/src/pages/ai-config/AiConfigPage.tsx) | 59 |
| PUT | `/ai-model-configs/{config_id}/default` | 已接入 | `aiModelConfigs.setDefault` → [AiConfigPage.tsx:72](../../frontend/src/pages/ai-config/AiConfigPage.tsx) | 69 |

### [assets.py](../../backend/src/short_drama/api/v1/assets.py)（18 条）

| 方法 | 路径 | 状态 | 前端证据 | 后端行 |
| --- | --- | --- | --- | ---: |
| GET | `/libraries/global/assets` | 已接入 | `assetLibraries.list` → [useAssetLibrary.ts:15](../../frontend/src/features/assets/useAssetLibrary.ts) | 48 |
| POST | `/libraries/global/assets` | 已接入 | `assetLibraries.create` → [AssetLibraryPanel.tsx:137](../../frontend/src/features/assets/AssetLibraryPanel.tsx) | 59 |
| PUT | `/libraries/global/assets/{asset_id}` | 保留接口，无 UI 调用 | `assetLibraries.link`：[assets.ts:25](../../frontend/src/api/modules/assets.ts)；无页面调用 | 68 |
| DELETE | `/libraries/global/assets/{asset_id}` | 已接入 | `assetLibraries.unlink` → [AssetLibraryPanel.tsx:173](../../frontend/src/features/assets/AssetLibraryPanel.tsx) | 73 |
| GET | `/projects/{project_id}/assets` | 已接入 | `assetLibraries.list` → [useAssetLibrary.ts:15](../../frontend/src/features/assets/useAssetLibrary.ts) | 83 |
| POST | `/projects/{project_id}/assets` | 已接入 | `assetLibraries.create` → [AssetLibraryPanel.tsx:137](../../frontend/src/features/assets/AssetLibraryPanel.tsx) | 95 |
| PUT | `/projects/{project_id}/assets/{asset_id}` | 已接入 | `assetLibraries.link` → [AssetLibraryPanel.tsx:213](../../frontend/src/features/assets/AssetLibraryPanel.tsx) | 108 |
| DELETE | `/projects/{project_id}/assets/{asset_id}` | 已接入 | `assetLibraries.unlink` → [AssetLibraryPanel.tsx:173](../../frontend/src/features/assets/AssetLibraryPanel.tsx) | 113 |
| GET | `/projects/{project_id}/episodes/{episode_id}/assets` | 已接入 | `assetLibraries.list` → [useAssetLibrary.ts:15](../../frontend/src/features/assets/useAssetLibrary.ts) | 124 |
| POST | `/projects/{project_id}/episodes/{episode_id}/assets` | 已接入 | `assetLibraries.create` → [AssetLibraryPanel.tsx:137](../../frontend/src/features/assets/AssetLibraryPanel.tsx) | 140 |
| PUT | `/projects/{project_id}/episodes/{episode_id}/assets/{asset_id}` | 已接入 | `assetLibraries.link` → [AssetLibraryPanel.tsx:213](../../frontend/src/features/assets/AssetLibraryPanel.tsx) | 154 |
| DELETE | `/projects/{project_id}/episodes/{episode_id}/assets/{asset_id}` | 已接入 | `assetLibraries.unlink` → [AssetLibraryPanel.tsx:173](../../frontend/src/features/assets/AssetLibraryPanel.tsx) | 164 |
| GET | `/assets/{asset_id}` | 已接入 | `assetLibraries.detail` → [AssetLibraryPanel.tsx:353](../../frontend/src/features/assets/AssetLibraryPanel.tsx) | 179 |
| PATCH | `/assets/{asset_id}` | 已接入 | `assetLibraries.update` → [AssetLibraryPanel.tsx:147](../../frontend/src/features/assets/AssetLibraryPanel.tsx) | 184 |
| GET | `/assets/{asset_id}/image-candidates` | 已接入 | `assetLibraries.candidates` → [AssetLibraryPanel.tsx:89](../../frontend/src/features/assets/AssetLibraryPanel.tsx) | 189 |
| POST | `/assets/{asset_id}/image-candidates` | 已接入 | `assetLibraries.addCandidate` → [AssetLibraryPanel.tsx:241](../../frontend/src/features/assets/AssetLibraryPanel.tsx) | 202 |
| POST | `/assets/{asset_id}/image-candidates/upload` | 已接入 | `assetLibraries.upload` → [AssetLibraryPanel.tsx:232](../../frontend/src/features/assets/AssetLibraryPanel.tsx) | 211 |
| POST | `/assets/{asset_id}/confirm` | 已接入 | `assetLibraries.confirm` → [AssetLibraryPanel.tsx:276](../../frontend/src/features/assets/AssetLibraryPanel.tsx) | 225 |

### [auth.py](../../backend/src/short_drama/api/v1/auth.py)（10 条）

| 方法 | 路径 | 状态 | 前端证据 | 后端行 |
| --- | --- | --- | --- | ---: |
| GET | `/auth/capabilities` | 已接入 | [AuthSession.tsx:21](../../frontend/src/features/auth/AuthSession.tsx) | 27 |
| POST | `/auth/register` | 已接入 | [AccountPages.tsx:27](../../frontend/src/features/auth/AccountPages.tsx) | 32 |
| POST | `/auth/login` | 已接入 | [AuthSession.tsx:37](../../frontend/src/features/auth/AuthSession.tsx) | 37 |
| GET | `/auth/me` | 已接入 | [AuthSession.tsx:24](../../frontend/src/features/auth/AuthSession.tsx) | 62 |
| POST | `/auth/logout` | 已接入 | [AuthSession.tsx:42](../../frontend/src/features/auth/AuthSession.tsx) | 71 |
| POST | `/auth/verification/request` | 已接入 | [EmailProofForm.tsx:28](../../frontend/src/features/auth/EmailProofForm.tsx) | 86 |
| POST | `/auth/verification/confirm` | 已接入 | [EmailProofForm.tsx:38](../../frontend/src/features/auth/EmailProofForm.tsx) | 93 |
| POST | `/auth/password/request` | 已接入 | [EmailProofForm.tsx:28](../../frontend/src/features/auth/EmailProofForm.tsx) | 98 |
| POST | `/auth/password/reset` | 已接入 | [EmailProofForm.tsx:38](../../frontend/src/features/auth/EmailProofForm.tsx) | 105 |
| GET | `/users/search` | 已接入 | [ProjectCollaboration.tsx:33](../../frontend/src/features/projects/ProjectCollaboration.tsx) | 112 |

### [collaboration.py](../../backend/src/short_drama/api/v1/collaboration.py)（14 条）

| 方法 | 路径 | 状态 | 前端证据 | 后端行 |
| --- | --- | --- | --- | ---: |
| GET | `/projects/{project_id}/members` | 已接入 | [ProjectCollaboration.tsx:19](../../frontend/src/features/projects/ProjectCollaboration.tsx) | 26 |
| DELETE | `/projects/{project_id}/members/{user_id}` | 已接入 | [ProjectCollaboration.tsx:31](../../frontend/src/features/projects/ProjectCollaboration.tsx) | 31 |
| POST | `/projects/{project_id}/leave` | 已接入 | [ProjectCollaboration.tsx:28](../../frontend/src/features/projects/ProjectCollaboration.tsx) | 36 |
| GET | `/projects/{project_id}/invitations` | 已接入 | [ProjectCollaboration.tsx:19](../../frontend/src/features/projects/ProjectCollaboration.tsx) | 41 |
| POST | `/projects/{project_id}/invitations` | 已接入 | [ProjectCollaboration.tsx:37](../../frontend/src/features/projects/ProjectCollaboration.tsx) | 46 |
| DELETE | `/projects/{project_id}/invitations/{invitation_id}` | 已接入 | [ProjectCollaboration.tsx:40](../../frontend/src/features/projects/ProjectCollaboration.tsx) | 51 |
| GET | `/invitations/{token}` | 已接入 | [AccountPages.tsx:47](../../frontend/src/features/auth/AccountPages.tsx) | 56 |
| POST | `/invitations/{token}/verification` | 已接入 | [AccountPages.tsx:49](../../frontend/src/features/auth/AccountPages.tsx) | 61 |
| POST | `/invitations/{token}/accept` | 已接入 | [AccountPages.tsx:50](../../frontend/src/features/auth/AccountPages.tsx) | 66 |
| GET | `/projects/{project_id}/activity` | 未封装、未调用 | 未找到 | 71 |
| GET | `/users/me/model-preferences` | 已接入 | [ModelPreferences.tsx:22](../../frontend/src/features/auth/ModelPreferences.tsx) | 98 |
| PUT | `/users/me/model-preferences` | 已接入 | [ModelPreferences.tsx:36](../../frontend/src/features/auth/ModelPreferences.tsx) | 112 |
| POST | `/projects/{project_id}/imports` | 已接入 | [ImagePicker.tsx:41](../../frontend/src/features/media-library/ImagePicker.tsx)<br>[AssetLibraryPanel.tsx:203](../../frontend/src/features/assets/AssetLibraryPanel.tsx) | 125 |
| GET | `/projects/{project_id}/imports/{import_id}` | 已接入 | [ImagePicker.tsx:44](../../frontend/src/features/media-library/ImagePicker.tsx)<br>[AssetLibraryPanel.tsx:207](../../frontend/src/features/assets/AssetLibraryPanel.tsx) | 139 |

### [episode_assembly.py](../../backend/src/short_drama/api/v1/episode_assembly.py)（12 条）

| 方法 | 路径 | 状态 | 前端证据 | 后端行 |
| --- | --- | --- | --- | ---: |
| GET | `/projects/{project_id}/episodes/{episode_id}/assembly` | 已接入 | `assemblyApi.get` → [useAssembly.ts:23](../../frontend/src/features/projects/useAssembly.ts) | 33 |
| POST | `/projects/{project_id}/episodes/{episode_id}/assembly/initialize` | 已接入 | `assemblyApi.initialize` → [AssemblyStage.tsx:80](../../frontend/src/pages/projects/episode/AssemblyStage.tsx) | 38 |
| PATCH | `/projects/{project_id}/episodes/{episode_id}/assembly` | 已接入 | `assemblyApi.save` → [useAssembly.ts:44](../../frontend/src/features/projects/useAssembly.ts) | 43 |
| POST | `/projects/{project_id}/episodes/{episode_id}/assembly/sync` | 已接入 | `assemblyApi.sync` → [AssemblyStage.tsx:200](../../frontend/src/pages/projects/episode/AssemblyStage.tsx) | 48 |
| POST | `/projects/{project_id}/episodes/{episode_id}/assembly/exports` | 已接入 | `assemblyApi.export` → [AssemblyStage.tsx:159](../../frontend/src/pages/projects/episode/AssemblyStage.tsx) | 53 |
| GET | `/projects/{project_id}/episodes/{episode_id}/assembly/exports` | 已接入 | `assemblyApi.history` → [AssemblyStage.tsx:163](../../frontend/src/pages/projects/episode/AssemblyStage.tsx) | 64 |
| POST | `/projects/{project_id}/episodes/{episode_id}/assembly/previews` | 已接入 | `assemblyApi.preview` → [AssemblyStage.tsx:109](../../frontend/src/pages/projects/episode/AssemblyStage.tsx) | 75 |
| GET | `/projects/{project_id}/episodes/{episode_id}/assembly/exports/{job_id}` | 已接入 | `assemblyApi.job` → [AssemblyWorkbench.tsx:108](../../frontend/src/features/projects/AssemblyWorkbench.tsx) | 86 |
| POST | `/projects/{project_id}/episodes/{episode_id}/assembly/exports/{job_id}/cancel` | 已接入 | `assemblyApi.cancel` → [AssemblyStage.tsx:191](../../frontend/src/pages/projects/episode/AssemblyStage.tsx) | 91 |
| POST | `/projects/{project_id}/episodes/{episode_id}/assembly/exports/{job_id}/retry` | 已接入 | `assemblyApi.retry` → [AssemblyStage.tsx:167](../../frontend/src/pages/projects/episode/AssemblyStage.tsx) | 96 |
| POST | `/projects/{project_id}/episodes/{episode_id}/assembly/exports/{job_id}/apply` | 已接入 | `assemblyApi.apply` → [AssemblyStage.tsx:212](../../frontend/src/pages/projects/episode/AssemblyStage.tsx) | 103 |
| GET | `/projects/{project_id}/episodes/{episode_id}/assembly/exports/{job_id}/download` | 已接入 | `assemblyApi.download` → [AssemblyWorkbench.tsx:108](../../frontend/src/features/projects/AssemblyWorkbench.tsx) | 114 |

### [episode_generation.py](../../backend/src/short_drama/api/v1/episode_generation.py)（5 条）

| 方法 | 路径 | 状态 | 前端证据 | 后端行 |
| --- | --- | --- | --- | ---: |
| POST | `/projects/{project_id}/episodes/{episode_id}/storyboard-results/{generation_id}/apply` | 已接入 | `storyboardApi.apply` → [StoryboardStage.tsx:435](../../frontend/src/pages/projects/episode/StoryboardStage.tsx) | 18 |
| GET | `/projects/{project_id}/episodes/{episode_id}/asset-extraction-results/{generation_id}` | 已接入 | `assetExtractionApi.get` → [ScriptAssetExtraction.tsx:95](../../frontend/src/features/projects/ScriptAssetExtraction.tsx) | 31 |
| PATCH | `/projects/{project_id}/episodes/{episode_id}/asset-extraction-results/{generation_id}` | 已接入 | `assetExtractionApi.save` → [ScriptAssetExtraction.tsx:105](../../frontend/src/features/projects/ScriptAssetExtraction.tsx) | 41 |
| POST | `/projects/{project_id}/episodes/{episode_id}/asset-extraction-results/{generation_id}/apply` | 已接入 | `assetExtractionApi.apply` → [ScriptAssetExtraction.tsx:169](../../frontend/src/features/projects/ScriptAssetExtraction.tsx) | 52 |
| GET | `/projects/{project_id}/episodes/{episode_id}/storyboard-results/{generation_id}/shots` | 已接入 | `storyboardApi.resultShots` → [StoryboardResultPreview.tsx:22](../../frontend/src/features/projects/StoryboardResultPreview.tsx) | 66 |

### [episode_sound.py](../../backend/src/short_drama/api/v1/episode_sound.py)（11 条）

| 方法 | 路径 | 状态 | 前端证据 | 后端行 |
| --- | --- | --- | --- | ---: |
| GET | `/projects/{project_id}/episodes/{episode_id}/sound/capabilities` | 已接入 | `soundApi.enabled` → [SoundPanel.tsx:29](../../frontend/src/features/projects/SoundPanel.tsx) | 26 |
| GET | `/projects/{project_id}/episodes/{episode_id}/sound` | 已接入 | `soundApi.get` → [SoundPanel.tsx:67](../../frontend/src/features/projects/SoundPanel.tsx) | 31 |
| PUT | `/projects/{project_id}/episodes/{episode_id}/sound` | 已接入 | `soundApi.save` → [SoundPanel.tsx:57](../../frontend/src/features/projects/SoundPanel.tsx) | 36 |
| PUT | `/projects/{project_id}/episodes/{episode_id}/sound/voices` | 已接入 | `soundApi.voices` → [SoundPanel.tsx:115](../../frontend/src/features/projects/SoundPanel.tsx) | 41 |
| POST | `/projects/{project_id}/episodes/{episode_id}/sound/music` | 已接入 | `soundApi.upload` → [SoundPanel.tsx:125](../../frontend/src/features/projects/SoundPanel.tsx) | 46 |
| GET | `/projects/{project_id}/episodes/{episode_id}/sound/candidates/{line_id}` | 已接入 | `soundApi.candidates` → [SoundPanel.tsx:34](../../frontend/src/features/projects/SoundPanel.tsx) | 51 |
| POST | `/projects/{project_id}/episodes/{episode_id}/sound/adopt` | 已接入 | `soundApi.adopt` → [SoundPanel.tsx:113](../../frontend/src/features/projects/SoundPanel.tsx) | 56 |
| GET | `/projects/{project_id}/episodes/{episode_id}/sound/extractions/{task_id}` | 已接入 | `soundApi.extraction` → [SoundPanel.tsx:41](../../frontend/src/features/projects/SoundPanel.tsx) | 61 |
| POST | `/projects/{project_id}/episodes/{episode_id}/sound/subtitles/import` | 已接入 | `soundApi.importSrt` → [SoundPanel.tsx:118](../../frontend/src/features/projects/SoundPanel.tsx) | 66 |
| GET | `/projects/{project_id}/episodes/{episode_id}/sound/subtitles.srt` | 已接入 | `soundApi` → [SoundPanel.tsx:5](../../frontend/src/features/projects/SoundPanel.tsx) | 80 |
| GET | `/projects/{project_id}/episodes/{episode_id}/sound/native-subtitles` | 已接入 | `soundApi.nativeSubtitles` → [SoundPanel.tsx:90](../../frontend/src/features/projects/SoundPanel.tsx) | 90 |

### [episode_storyboard.py](../../backend/src/short_drama/api/v1/episode_storyboard.py)（7 条）

| 方法 | 路径 | 状态 | 前端证据 | 后端行 |
| --- | --- | --- | --- | ---: |
| GET | `/projects/{project_id}/episodes/{episode_id}/shots` | 已接入 | `storyboardApi.shots` → [StoryboardStage.tsx:32](../../frontend/src/pages/projects/episode/StoryboardStage.tsx) | 38 |
| POST | `/projects/{project_id}/episodes/{episode_id}/shots` | 已接入 | `storyboardApi.create` → [StoryboardStage.tsx:369](../../frontend/src/pages/projects/episode/StoryboardStage.tsx) | 56 |
| PUT | `/projects/{project_id}/episodes/{episode_id}/shots/order` | 仅封装，无 UI 调用 | `storyboardApi.order`：[storyboard.ts:26](../../frontend/src/api/modules/storyboard.ts)；无页面调用 | 70 |
| GET | `/projects/{project_id}/episodes/{episode_id}/shots/{shot_id}` | 已接入 | `storyboardApi.shot` → [StoryboardStage.tsx:315](../../frontend/src/pages/projects/episode/StoryboardStage.tsx) | 80 |
| PATCH | `/projects/{project_id}/episodes/{episode_id}/shots/{shot_id}` | 已接入 | `storyboardApi.update` → [StoryboardStage.tsx:259](../../frontend/src/pages/projects/episode/StoryboardStage.tsx) | 85 |
| DELETE | `/projects/{project_id}/episodes/{episode_id}/shots/{shot_id}` | 仅封装，无 UI 调用 | `storyboardApi.remove`：[storyboard.ts:24](../../frontend/src/api/modules/storyboard.ts)；无页面调用 | 96 |
| POST | `/projects/{project_id}/episodes/{episode_id}/shots/{shot_id}/move` | 仅封装，无 UI 调用 | `storyboardApi.move`：[storyboard.ts:25](../../frontend/src/api/modules/storyboard.ts)；无页面调用 | 112 |

### [episode_writing.py](../../backend/src/short_drama/api/v1/episode_writing.py)（7 条）

| 方法 | 路径 | 状态 | 前端证据 | 后端行 |
| --- | --- | --- | --- | ---: |
| GET | `/projects/{project_id}/episodes/{episode_id}/scripts` | 已接入 | `storyboardApi.scripts` → [NovelScriptGeneration.tsx:20](../../frontend/src/features/projects/NovelScriptGeneration.tsx) | 26 |
| GET | `/projects/{project_id}/episodes/{episode_id}/scripts/{script_id}` | 已接入 | `storyboardApi.script` → [NovelScriptGeneration.tsx:122](../../frontend/src/features/projects/NovelScriptGeneration.tsx) | 37 |
| GET | `/projects/{project_id}/episodes/{episode_id}/writing` | 已接入 | `episodeWritingApi.get` → [writing-session.ts:42](../../frontend/src/features/projects/writing-session.ts) | 42 |
| PUT | `/projects/{project_id}/episodes/{episode_id}/novel` | 已接入 | `episodeWritingApi.novel` → [writing-session.ts:102](../../frontend/src/features/projects/writing-session.ts) | 47 |
| PUT | `/projects/{project_id}/episodes/{episode_id}/script` | 已接入 | `episodeWritingApi.script` → [writing-session.ts:105](../../frontend/src/features/projects/writing-session.ts) | 52 |
| PUT | `/projects/{project_id}/episodes/{episode_id}/editing-script` | 已接入 | `episodeWritingApi.select` → [writing-session.ts:157](../../frontend/src/features/projects/writing-session.ts) | 57 |
| POST | `/projects/{project_id}/episodes/{episode_id}/scripts/{script_id}/confirm` | 已接入 | `episodeWritingApi.confirm` → [writing-session.ts:157](../../frontend/src/features/projects/writing-session.ts) | 64 |

### [generation_batches.py](../../backend/src/short_drama/api/v1/generation_batches.py)（8 条）

| 方法 | 路径 | 状态 | 前端证据 | 后端行 |
| --- | --- | --- | --- | ---: |
| GET | `/ai/generation-batches/capabilities` | 已接入 | `generationBatches.capabilities` → [BatchGeneration.tsx:25](../../frontend/src/features/generations/BatchGeneration.tsx) | 24 |
| POST | `/ai/generation-batches/preflight` | 已接入 | `generationBatches.preflight` → [BatchGeneration.tsx:71](../../frontend/src/features/generations/BatchGeneration.tsx) | 35 |
| POST | `/ai/generation-batches` | 已接入 | `generationBatches.create` → [BatchGeneration.tsx:82](../../frontend/src/features/generations/BatchGeneration.tsx) | 40 |
| GET | `/ai/generation-batches` | 已接入 | `generationBatches.list` → [BatchGeneration.tsx:159](../../frontend/src/features/generations/BatchGeneration.tsx) | 47 |
| GET | `/ai/generation-batches/{batch_id}` | 已接入 | `generationBatches.detail` → [BatchGeneration.tsx:119](../../frontend/src/features/generations/BatchGeneration.tsx) | 52 |
| GET | `/ai/generation-batches/{batch_id}/items` | 别名，功能已覆盖 | 批次详情使用无 `/items` 后缀的详情路径 | 53 |
| POST | `/ai/generation-batches/{batch_id}/retry-failed` | 已接入 | `generationBatches.retry` → [BatchGeneration.tsx:130](../../frontend/src/features/generations/BatchGeneration.tsx) | 58 |
| POST | `/ai/generation-batches/{batch_id}/{action}` | 已接入 | `generationBatches.control` → [BatchGeneration.tsx:131](../../frontend/src/features/generations/BatchGeneration.tsx) | 63 |

### [generation_references.py](../../backend/src/short_drama/api/v1/generation_references.py)（4 条）

| 方法 | 路径 | 状态 | 前端证据 | 后端行 |
| --- | --- | --- | --- | ---: |
| POST | `/generation-references/uploads` | 已接入 | `uploadTaskReference` → [CreateGeneration.tsx:11](../../frontend/src/features/generations/CreateGeneration.tsx) | 16 |
| GET | `/generation-references/{kind}/{owner_id}` | 已接入 | `generationReferences.list` → [ReferenceImages.tsx:24](../../frontend/src/features/generations/ReferenceImages.tsx) | 33 |
| POST | `/generation-references/{kind}/{owner_id}` | 已接入 | `generationReferences.upload` → [ReferenceImages.tsx:37](../../frontend/src/features/generations/ReferenceImages.tsx) | 40 |
| DELETE | `/generation-references/{kind}/{owner_id}/{media_id}` | 已接入 | `generationReferences.remove` → [ReferenceImages.tsx:37](../../frontend/src/features/generations/ReferenceImages.tsx) | 55 |

### [media_library.py](../../backend/src/short_drama/api/v1/media_library.py)（4 条）

| 方法 | 路径 | 状态 | 前端证据 | 后端行 |
| --- | --- | --- | --- | ---: |
| GET | `/media-library/items` | 已接入 | `mediaLibrary.list` → [ImagePicker.tsx:26](../../frontend/src/features/media-library/ImagePicker.tsx) | 14 |
| GET | `/media-library/items/{asset_id}` | 已接入 | `mediaLibrary.detail` → [AssetDetail.tsx:49](../../frontend/src/features/media-library/AssetDetail.tsx) | 46 |
| PATCH | `/media-library/items/{asset_id}` | 已接入 | `mediaLibrary.rename` → [AssetDetail.tsx:61](../../frontend/src/features/media-library/AssetDetail.tsx) | 51 |
| POST | `/media-library/items/{asset_id}/apply` | 已接入 | `mediaLibrary.apply` → [ShotVideoCandidates.tsx:101](../../frontend/src/features/projects/ShotVideoCandidates.tsx) | 56 |

### [native_voice.py](../../backend/src/short_drama/api/v1/native_voice.py)（7 条）

| 方法 | 路径 | 状态 | 前端证据 | 后端行 |
| --- | --- | --- | --- | ---: |
| GET | `/native-voice/capabilities` | 已接入 | `nativeVoiceApi.enabled` → [NativeVoicePanel.tsx:15](../../frontend/src/features/projects/NativeVoicePanel.tsx) | 21 |
| GET | `/projects/{project_id}/sound-mode` | 已接入 | `nativeVoiceApi.mode` → [NativeVoicePanel.tsx:23](../../frontend/src/features/projects/NativeVoicePanel.tsx) | 32 |
| PUT | `/projects/{project_id}/sound-mode` | 已接入 | `nativeVoiceApi.setMode` → [NativeVoicePanel.tsx:29](../../frontend/src/features/projects/NativeVoicePanel.tsx) | 37 |
| GET | `/projects/{project_id}/characters/{asset_id}/voice` | 已接入 | `nativeVoiceApi.voices` → [NativeVoicePanel.tsx:40](../../frontend/src/features/projects/NativeVoicePanel.tsx) | 42 |
| POST | `/projects/{project_id}/characters/{asset_id}/voice/adopt` | 已接入 | `nativeVoiceApi.adopt` → [NativeVoicePanel.tsx:66](../../frontend/src/features/projects/NativeVoicePanel.tsx) | 47 |
| GET | `/projects/{project_id}/episodes/{episode_id}/shots/{shot_id}/dialogue` | 已接入 | `nativeVoiceApi.dialogue` → [NativeVoicePanel.tsx:78](../../frontend/src/features/projects/NativeVoicePanel.tsx) | 52 |
| PUT | `/projects/{project_id}/episodes/{episode_id}/shots/{shot_id}/dialogue` | 已接入 | `nativeVoiceApi.saveDialogue` → [NativeVoicePanel.tsx:87](../../frontend/src/features/projects/NativeVoicePanel.tsx) | 57 |

### [projects.py](../../backend/src/short_drama/api/v1/projects.py)（11 条）

| 方法 | 路径 | 状态 | 前端证据 | 后端行 |
| --- | --- | --- | --- | ---: |
| GET | `/projects` | 已接入 | `projectsApi.list` → [ProjectsPage.tsx:34](../../frontend/src/pages/projects/ProjectsPage.tsx) | 26 |
| GET | `/projects/{project_id}` | 已接入 | `projectsApi.get` → [ProjectOverview.tsx:96](../../frontend/src/features/projects/ProjectOverview.tsx) | 36 |
| PATCH | `/projects/{project_id}` | 已接入 | `projectsApi.update` → [ProjectOverview.tsx:54](../../frontend/src/features/projects/ProjectOverview.tsx) | 41 |
| POST | `/projects/{project_id}/open` | 已接入 | `projectsApi.open` → [ProjectRoute.tsx:37](../../frontend/src/pages/projects/ProjectRoute.tsx) | 46 |
| DELETE | `/projects/{project_id}` | 已接入 | `projectsApi.remove` → [ProjectOverview.tsx:81](../../frontend/src/features/projects/ProjectOverview.tsx) | 51 |
| GET | `/projects/{project_id}/episodes` | 已接入 | `projectsApi.listEpisodes` → [ProjectList.tsx:16](../../frontend/src/features/projects/ProjectList.tsx) | 57 |
| GET | `/projects/{project_id}/episodes/{episode_id}` | 已接入 | `projectsApi.getEpisode` → [ProjectOverview.tsx:118](../../frontend/src/features/projects/ProjectOverview.tsx) | 67 |
| PATCH | `/projects/{project_id}/episodes/{episode_id}` | 已接入 | `projectsApi.updateEpisode` → [ProjectOverview.tsx:72](../../frontend/src/features/projects/ProjectOverview.tsx) | 72 |
| DELETE | `/projects/{project_id}/episodes/{episode_id}` | 已接入 | `projectsApi.removeEpisode` → [ProjectOverview.tsx:82](../../frontend/src/features/projects/ProjectOverview.tsx) | 82 |
| POST | `/projects` | 已接入 | `projectsApi.create` → [ProjectsPage.tsx:50](../../frontend/src/pages/projects/ProjectsPage.tsx) | 88 |
| POST | `/projects/{project_id}/episodes` | 已接入 | `projectsApi.createEpisode` → [ProjectOverview.tsx:70](../../frontend/src/features/projects/ProjectOverview.tsx) | 96 |

### [test.py](../../backend/src/short_drama/api/v1/test.py)（3 条）

| 方法 | 路径 | 状态 | 前端证据 | 后端行 |
| --- | --- | --- | --- | ---: |
| GET | `/test` | 诊断接口，无 UI 调用 | 未找到 | 11 |
| GET | `/test/db` | 诊断接口，无 UI 调用 | 未找到 | 16 |
| GET | `/test/minio` | 诊断接口，无 UI 调用 | 未找到 | 22 |
