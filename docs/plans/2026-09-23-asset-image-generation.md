# 素材详情 AI 图片生成实施计划

> **执行说明：** 使用 `superpowers:executing-plans` 按任务实施；每个任务完成后核对测试证据，再更新复选框。本文同时保存设计与执行步骤，避免另建重复规格文档。

**状态：** 已实施并完成本地验证（2026-09-23），未部署，未调用真实付费模型。

**交付记录：** 任务1–6已实现，任务7的自动回归与核心浏览器链路已验收。下列证据说明已验证范围。代码实现与自动回归已完成；未勾选项为完整扩展环境演练和发布，不计为已执行。

| 检查 | 结果 |
| --- | --- |
| 后端 unit/API | 382项全量通过 |
| MySQL集成 | 77项通过、2项真实外部设施用例按配置跳过；其中新增素材生图20项 |
| 前端测试 | 61项通过，其中新增素材生图7项 |
| TypeScript / Vite生产构建 | 通过；保留现有大体积chunk警告 |
| Ruff检查与格式 | 通过 |
| 本地文档链接 | 通过 |
| 浏览器核心链路 | 共享保存取消不生成、双击只一次提交、使用保存后版本、跨素材历史隔离、重开恢复、完整预览、共享+旧来源确认后采用均通过 |

浏览器使用独立本地前端及内存API；数据库测试使用随机测试库、有效PNG供应商替身及内存对象存储。没有修改业务库、上传真实素材或调用真实模型。真实供应商效果/费用、真实RabbitMQ/MinIO联动和完整弱网、多页交互演练仍需在相应环境验证。

实施中额外修正了随机测试库清理：删除任务前解除可空的 retry_of_id 自引用，避免MySQL逐行RESTRICT导致重试测试污染后续用例。未修改生产DDL或业务删除语义。

**Goal：** 用户从角色、场景、道具详情生成图片，查看候选并明确采用，后续分镜图片生成使用已确认的素材图片。

**Architecture：** 扩展现有图片任务的 `asset_image` 来源，服务端从已保存素材构造不可变输入。沿用现有调度、供应商适配、MinIO 归档和候选采用流程，不增加任务系统。

**Tech Stack：** FastAPI、Pydantic、SQLAlchemy、MySQL 8、Celery/RabbitMQ、MinIO；React、TypeScript、Ant Design。

**Spec：** 本文“设计与契约”章节。相关现状见 [架构](../architecture.md)、[API](../api/README.md)、[开发与验证](../development.md)。

## 全局约束

- 本计划经用户确认后实施。保留当前工作区已有的文档整理、数据库汇总与代码清理修改；不回滚、不混入无关重构。
- 首期仅支持单个素材的文生图，默认 1 张，可选 1–4 张；沿用供应商能力校验。不包含批量生成、图生图、多视图、视频、自动采用或新的模型协议。
- 图片生成不改变素材 `media_id/model_id/state/row_version`。只有明确采用或修改素材内容才更新素材。
- ID 与版本对外使用十进制字符串。来源、快照、有效提示词以服务端为准。
- 现有无来源图片生成、分镜图片生成、上传候选和媒体库采用必须兼容。
- 自动测试不调用付费模型。MySQL 测试使用已有 fixture 创建的随机数据库，不导入或清空业务库。
- 不为这个功能新增 npm/Python 依赖、队列、数据表或数据库字段。
- 先上线具备该来源处理能力的后端和全部图片 Worker，再上线前端入口。

## 设计与契约

### 1. 范围与方案选择

| 方案 | 取舍 | 结论 |
| --- | --- | --- |
| 扩展现有图片任务，自动归档为素材候选 | 复用幂等、恢复、计费调用边界；需要接通候选投影 | 采用 |
| 新建素材专用生成接口与任务表 | 重复调度、状态和异常处理 | 不采用 |
| 前端拼接提示词，完成后前端补写候选 | 页面关闭时关联可能丢失，输入不可核验 | 不采用 |

业务链路：保存素材 → 提交图片任务 → 服务端冻结输入 → Worker 生成并归档 → 候选预览 → 明确采用 → 素材确认 → 分镜读取引用图片。

共享素材以真实 `asset_id` 为生成来源。全局库、项目库、分集库打开同一素材，都查看同一份生成历史。复制素材产生新 ID，不继承原素材任务历史；已有候选/图片复制行为保持现状。

首期不隐式读取当前项目或分集的风格、画幅，防止同一共享素材因入口不同而改变输入。风格要求通过“本次补充要求”显式表达；画幅、分辨率未选择时省略并沿用模型配置。生成操作自身无需共享变更确认；生成前保存和生成后采用继续遵守共享确认。

### 2. 提交、快照与提示词

新增来源模型：

```python
class AssetImageSource(InputModel):
    scene: Literal["asset_image"]
    asset_id: Identifier
    row_version: Identifier
```

`ImageGenerationCreate.source` 改为以 `scene` 为 discriminator 的 `ShotImageSource | AssetImageSource | None`。不能继续对所有来源直接访问 `context_mode`。

```http
POST /api/v1/ai/generations/image
Idempotency-Key: asset-image-attempt-001
Content-Type: application/json
```

```json
{
  "config_id": "201",
  "input": {"prompt": "自然光，突出布料质感", "reference_media_ids": []},
  "parameters": {"count": 1},
  "source": {"scene": "asset_image", "asset_id": "101", "row_version": "7"}
}
```

- `input.prompt` 在此来源中是本次补充要求，允许空串，上限 4000 字符；不传素材完整描述。
- 首期此来源的 `reference_media_ids` 必须为空；非空返回 422。其他来源保持既有规则。
- 服务端锁定素材、校验版本，要求名称非空且描述或素材提示词至少一项非空。缺失素材返回 404；内容不足返回 `asset_content_required`（400，与现有采用规则一致）；版本冲突返回 `asset_version_conflict`（409）。
- 幂等检查仍在读取当前素材之前：同 key、同原始请求重放返回既有任务，即使素材后来变化；同 key、不同请求仍冲突。不能把当前数据库快照纳入客户端请求幂等摘要。
- `source_snapshot` 保存 `asset`（下面列出的内容字段）、`asset_row_version`、`asset_content_hash`、`template_version="asset-image-v1"`、`instructions`。有效提示词写入已有 `request_data.input.prompt`；实际参数沿用 `resolved_parameters`。
- `asset` 内容字段固定为 `kind/name/description/prompt/label/tags/scene_time`；哈希对这些已保存值做 canonical JSON + SHA-256，tags 作为集合排序。不包含 `media_id/model_id/state/row_version/updated_at`、模型选择或本次补充要求。素材 ID 单独通过 source 校验。
- `row_version` 用于提交时并发控制；内容哈希用于采用时判断旧结果。采用一张图只改变图片/状态/版本，不让同批其他图片自动变旧。
- 不额外调用文本模型改写提示词。确定性模板按角色/场景/道具分别强调人物辨识特征、空间与时间、物体外观；素材描述提供事实，素材 prompt 提供视觉约束，本次要求补充风格。首期生成单幅图片，不默认要求拼图、三视图或文字标注。
- 当前输入变更后“按当前素材再生成”走新的 create；已有 retry 按旧快照重新调用模型，UI 必须区分两者；resume 沿用既有安全恢复判断。

### 3. 历史、归档与数据库

扩展两个既有查询：

```http
GET /api/v1/ai/generations?service_type=image&source_scene=asset_image&source_id=101
GET /api/v1/media-library/items?source_scene=asset_image&source_id=101
```

`source_conditions()` 增加 `asset_image → asset_id`，API Literal 同步扩展。素材详情不附加 project/episode 过滤；显式组合这些过滤时仍为 AND，不伪造素材的项目归属。

数据库沿用 `ai_generation_record.request_data` JSON、`media_asset` 和 `asset_image_candidate`。候选来源经 `candidate.media_id → media_asset.media_id → ai_generation_record` 查询；按当前候选页批量加载，避免每张图片一次查询。候选唯一约束 `(asset_id, media_id)` 已存在，因此本期不改 `schema.mysql8.sql`，也不新增迁移。

在 `GenerationArchive.save_one()` 的最终、受 lease fencing 保护的数据库事务中，保存 MediaFile/MediaAsset 后，给 `asset_image` 来源创建候选，再将 manifest 输出标记为 saved。锁顺序保持 AsyncTask → Asset → Candidate，不在持有素材锁时请求 AsyncTask；不在数据库锁内下载图片。

必须覆盖现有两个“MediaAsset 已存在即返回”的分支：在 owned_task 校验后的事务内执行幂等关联、补齐 manifest 标记，再返回。执行器也要让 `asset_image` 已标记 saved 的输出执行本地候选核对，不重新下载、不再次调用模型。候选唯一约束和素材行锁共同防止并发重复。

若素材已不存在，仍保存付费生成结果到媒体库；对应 manifest 输出记录 `candidate_status="source_missing"`，任务详情新增可选 `result.warnings`，每项为 `{code:"asset_source_missing", message:"原素材已不存在，图片已保存至媒体库", output_index:1}`。这是终止关联的业务结果，不重试创建素材、不无限重试。正常输出为 `candidate_status="linked"`。数据库暂时故障则整体回滚并由已有 save 重试恢复。

部分输出失败时，已归档候选继续可见、可采用；任务沿用 `partial_result`。取消任务不会抹去已经归档的候选。候选投影不更新素材时间戳或确认状态。

### 4. 候选来源与明确采用

`AssetImageCandidateRead` 增加可选 `generation`；上传候选和缺少可解析生成记录的历史候选返回 null：

```json
{
  "generation": {
    "generation_id": "301",
    "record_id": "302",
    "source_asset_id": "101",
    "source_row_version": "7",
    "source_content_hash": "保存的SHA-256摘要",
    "is_stale": false,
    "stale_reason": null
  }
}
```

仅 `asset_image` 生成来源填充此对象。`stale_reason` 可为 `content_changed/source_mismatch/snapshot_missing`。另一素材的生成图被显式加入本素材候选时标记 source_mismatch；该来源存在但哈希缺失时标记 snapshot_missing。通用图片、分镜生成图与上传图保留既有采用行为，不伪造素材快照。

`AssetConfirm` 增加 `acknowledge_stale_source: bool = False`。两个采用入口都必须进入 `AssetImageService.confirm_locked()` 的同一校验：

- `POST /assets/{asset_id}/confirm`。
- `POST /media-library/items/{asset_id}/apply`，target.type 为 asset_image；这里路径中的 asset_id 是媒体资产 ID，不能与来源素材 ID 混用。

先校验当前 row_version、expected_media_id、候选有效性和内容。若已经 confirmed 且就是当前图片，按既有语义直接返回，不再次要求旧来源或共享确认。其他情况下，旧来源未经明确确认返回 `stale_source`（409，details 含 reason）；随后执行共享引用确认。前端再次提交必须保留已确认的标志，但只绑定当前候选和当前目标版本；目标再变化时刷新后重新确认，不能用 acknowledge 绕过版本冲突。

成功采用一次更新 `media_id/model_id/state="confirmed"/row_version+1`。分镜生成继续只读取已确认的关联素材图片；生成完成本身不改变分镜引用。

### 5. 前端交互与恢复

沿用现有素材详情 Dialog，不新建独立页面。包含图片模型选择、可选补充要求/画幅/分辨率/张数、生成按钮、当前任务状态与错误、候选预览和采用。表单无可用图片模型、只读模式或内容不满足要求时禁止提交并说明原因。

点击生成时同步设置 in-flight ref，锁定本次短请求操作。保存脏字段后使用保存接口返回的素材版本构造请求，不能读取尚未更新的 React state。保存失败、版本冲突或取消共享确认时不创建任务。保存/提交期间禁用素材编辑；任务被受理后解除短操作锁，用户可关闭详情或继续编辑。

幂等 scope 为 `asset-image:${assetId}`。相同请求遇到网络不确定结果时保留 key，下一次同请求重用；成功接收任务回执后才 clearAttempt。另一次主动生成使用新 key。双击通过 ref 阻止；不要用仅依赖 React state 的 busy 判断。

历史以服务端为准。打开详情加载最新 20 条任务，较早任务按需翻页；另外完整分页读取 queued/running 任务，确保旧的长任务不因跌出第一页而失联。存在活动任务时每 3 秒刷新、无活动时每 15 秒刷新当前页；切换素材或关闭时取消请求和定时器，忽略旧选择的响应。轮询失败保留已有数据并提供重试，不将网络错误解释为任务失败。

候选首次加载和活动轮询使用当前页（20 条），提供加载更多；来源版本变化、任务输出变化或进入终态时刷新候选第一页。用 ID 合并已加载项目并更新过期标识，避免每次轮询遍历全部历史。页面恢复后从历史重建状态，不依赖浏览器保存任务对象。

候选展示生成来源及“素材已修改”提示，提供放大预览、明确采用。任务操作只按 `can_cancel/can_resume/can_retry` 开放；恢复结果与按旧输入重新生成的文案分开，重新调用模型前说明会产生新生成调用。有效提示词与快照通过任务详情按需查看，默认不占用编辑表单。

## 文件职责

下表均为仓库相对路径；“新增”为本次实施创建的文件。

| 文件 | 职责 |
| --- | --- |
| `backend/src/short_drama/schemas/ai_generation.py` | 新来源联合类型与输入验证 |
| `backend/src/short_drama/api/v1/ai_generations.py`、`api/v1/media_library.py` | 查询来源白名单（第二个路径同 backend/src/short_drama 前缀） |
| `backend/src/short_drama/dao/async_task_dao.py` | 公用来源过滤映射 |
| `backend/src/short_drama/service/asset_image_context.py`（新增） | 纯内容投影、哈希与旧来源判断 |
| `backend/src/short_drama/ai/business_prompts.py` | 确定性 asset_image_prompt 模板 |
| `backend/src/short_drama/service/generation_context_service.py` | 锁定读取与 prepare_asset_image |
| `backend/src/short_drama/service/ai_generation_service.py` | 分发新来源、保留幂等先查、返回归档警告 |
| `backend/src/short_drama/service/generation_archive.py`、`generation_execution_service.py`（同目录） | 同事务候选关联与恢复 |
| `backend/src/short_drama/dao/asset_image_candidate_dao.py` | 按页批量关联生成记录 |
| `backend/src/short_drama/schemas/asset_image_candidate.py` | 候选来源响应、采用确认字段 |
| `backend/src/short_drama/service/asset_image_service.py`、`media_asset_service.py`（同目录） | 所有采用入口统一旧来源判断 |
| `frontend/src/api/types/generations.ts`、`frontend/src/api/modules/assets.ts` | 来源、警告、候选与采用类型 |
| `frontend/src/features/assets/asset-image-generation.ts`（新增） | 请求构造与任务/候选合并纯函数 |
| `frontend/src/features/assets/useAssetImageGeneration.ts`（新增） | 提交幂等、轮询、请求取消 |
| `frontend/src/features/assets/AssetImageGeneration.tsx`（新增） | 生成表单与任务列表 |
| `frontend/src/features/assets/AssetLibraryPanel.tsx` | 保存协调、嵌入生成组件、候选预览与采用 |

## 任务 1：扩展生成请求与历史过滤

**依赖：** 无。**交付：** 请求契约与来源查询可验证；此时还不开放前端入口。

**修改：** 文件职责表中的 schemas/ai_generation、两个 API 文件、dao/async_task_dao，以及 `frontend/src/api/types/generations.ts`。

**测试：** 新增 `backend/tests/unit/test_asset_image_generation.py`；修改 `backend/tests/api/test_generations.py`。

- [x] 加入下面的 schema 回归测试，再加入非空引用图、超过 4000 字补充要求、遗漏版本、无来源空 prompt 的拒绝用例；保留 saved-shot 与 legacy-shot 成功用例。

```python
from short_drama.schemas.ai_generation import ImageGenerationCreate


def test_asset_image_accepts_empty_supplement():
    request = ImageGenerationCreate.model_validate({
        "input": {"prompt": ""},
        "source": {"scene": "asset_image", "asset_id": "101", "row_version": "7"},
    })
    assert request.source.scene == "asset_image"
    assert request.parameters.count == 1
```

- [x] 在 backend 运行 `.venv/Scripts/python.exe -m pytest tests/unit/test_asset_image_generation.py tests/api/test_generations.py -q`，确认新增场景先因不支持来源而失败。
- [x] 实现 AssetImageSource 与 discriminated union；validator 先按 scene 分支，asset_image 拒绝引用图，shot_image 继续使用 context_mode；同步 TS 图片请求类型为图片来源子集。
- [x] 扩展 source_conditions 与两个 HTTP 查询 Literal；API 测试断言 source_scene/source_id 原样传到服务，未知 scene 422，source_id 无 scene 保持既有错误行为。
- [x] 重跑同一命令，确认通过；检查旧 source 查询未改变。MySQL JSON 精确过滤在任务 7 验证。

## 任务 2：建立素材快照、哈希与提示词

**依赖：** 任务 1。**交付：** 新任务使用已保存内容，幂等重放不受后续素材编辑影响。

**新增：** `backend/src/short_drama/service/asset_image_context.py`。**修改：** generation_context_service、ai_generation_service、ai/business_prompts。**测试：** 任务 1 新建的 unit 文件。

纯函数接口固定如下，供任务 4 复用：

```python
def asset_image_content(asset) -> dict:
    fields = ("kind", "name", "description", "prompt", "label", "tags", "scene_time")
    result = {field: getattr(asset, field) for field in fields}
    result["tags"] = sorted(set(result["tags"] or []))
    return result


def asset_image_content_hash(content: dict) -> str:
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
```

文件需导入 json/hashlib。新增 `asset_image_prompt(content: dict, supplement: str) -> str`，使用稳定序列化的素材数据、分类说明和独立补充要求段落。`GenerationContextService.prepare_asset_image(request: dict) -> dict` 只在外层已有事务中锁定读取，不自行提交。

- [x] 写参数化测试：name/description/prompt/label/tags/scene_time 变化使哈希变化；仅 row_version/state/media_id/model_id 变化不影响；tags 顺序变化不影响。用 `SimpleNamespace` 构造素材，断言哈希相等/不等，不对整段提示词写脆弱的全文快照。
- [x] 增加服务测试：不存在素材、版本冲突、内容为空均无新 AsyncTask；三种分类模板包含保存内容与补充要求；提交后编辑素材不改变 request_data；生成不改素材状态。
- [x] 运行任务 1 的 unit 命令确认预期失败；实现纯函数、模板和 prepare_asset_image，再在 `_prepare` 分发。
- [x] 保留 create 中 `_existing` 在 `_prepare` 之前的顺序；测试同 key/同请求在编辑后返回同任务、同 key/不同请求冲突；retry 使用冻结输入，resume 不重建输入。
- [x] 重跑 unit 用例与 `tests/unit/test_generation_business.py`，确认 shot_image 上下文构造未退化。

## 任务 3：将归档图片可靠关联为候选

**依赖：** 任务 2。**交付：** 页面关闭也能自动收到候选；保存恢复不重复生成。

**修改：** generation_archive、generation_execution_service、ai_generation_service；`frontend/src/api/types/generations.ts` 添加可选 result.warnings。**测试：** `backend/tests/unit/test_generation_archive.py`，新增 `backend/tests/integration/test_asset_image_generation.py`。

新增归档内部接口 `GenerationArchive._link_asset_candidate(session, request: dict, media_id: int) -> str | None`。非 asset_image 返回 None；找到素材并确保候选返回 linked；来源不存在返回 source_missing。调用方已持有任务 lease 锁；函数按 Asset → Candidate 锁定，不提交事务。

核心数据库操作遵循现有 DAO：

```python
candidate = AssetImageCandidateDAO(session).get_for_asset(
    asset.id, media.id, for_update=True,
)
if candidate is None:
    AssetImageCandidateDAO(session).create({"asset_id": asset.id, "media_id": media.id})
# 与 MediaAsset 创建、manifest.saved/candidate_status 更新处于同一事务。
```

- [x] 用测试替身返回有效 PNG；新增“重复保存仅一条候选”“两张成功一张失败保留两张候选”“过期 lease 不写候选”用例。断言素材四个字段 media_id/model_id/state/row_version 未变。
- [x] 在 backend 运行 `.venv/Scripts/python.exe -m pytest tests/unit/test_generation_archive.py -q`；MySQL 用 `.venv/Scripts/python.exe scripts/run_integration.py tests/integration/test_asset_image_generation.py -q`。先确认新断言失败，再实现。
- [x] 将两个已存在输出分支收敛至受 owned_task 保护的本地关联/manifest 更新；保留输出 ID 冲突检查。执行器对 asset_image 的 saved 输出执行补齐，其他场景保持现状。
- [x] 注入候选插入失败，断言 MediaAsset 与 saved 标记一并回滚；恢复后 provider 调用次数不增加、候选仅一条。模拟已有 MediaAsset 但候选/manifest 缺失，验证可修复。
- [x] 模拟来源消失，验证永久图片保留、source_missing 已记录、result.warnings 可读、任务不会反复保存；不改变正常任务的成功/部分失败语义。
- [x] 重跑上述测试，确认重复消息、取消后的已有结果、部分归档恢复均不触发额外供应商提交。

## 任务 4：补充候选来源与统一采用校验

**依赖：** 任务 2、3。**交付：** 候选可解释、旧结果需明确确认，两个采用入口行为一致。

**修改：** schemas/asset_image_candidate、dao/asset_image_candidate_dao、service/asset_image_service、service/media_asset_service；`frontend/src/api/modules/assets.ts`。**测试：** `backend/tests/api/test_asset_library_api.py`、`backend/tests/unit/test_asset_image_generation.py`、`backend/tests/integration/test_asset_image_generation.py`。

新增纯函数 `asset_image_stale_reason(asset, request: dict) -> str | None`，位于 asset_image_context：非 asset_image 返回 None；来源 ID 不同返回 source_mismatch；缺哈希返回 snapshot_missing；当前内容哈希不同返回 content_changed，否则 None。读取展示与采用都调用它。

- [x] 在两个采用入口写同一组参数化断言：新候选成功；素材改描述后返回 stale_source；传确认标志后成功；确认标志无法绕过版本或 expected_media_id 冲突；共享素材仍需 confirm_shared。
- [x] 加入兼容测试：上传/通用生成候选照常采用；重复确认当前图不加版本；采用第一张后同批第二张不因版本增加而过期；不同素材来源需明确确认。
- [x] 运行 `.venv/Scripts/python.exe -m pytest tests/unit/test_asset_image_generation.py tests/api/test_asset_library_api.py -q`，确认缺失字段/校验的失败，再增加 generation DTO 和批量来源查询。
- [x] 在 confirm_locked 按设计顺序校验；媒体库入口转发现有 `parsed.acknowledge_stale_source`。示例新增键：

```python
"acknowledge_stale_source": parsed.acknowledge_stale_source,
```

- [x] 确保 add_candidate/upload 返回同一扩展响应；旧候选 generation=null；分页不产生按图片数量增长的来源查询。
- [x] 重跑上述 unit/API 和任务 3 的 MySQL 命令，核对两入口的错误 code/status/details 相同。

## 任务 5：接通详情表单与防重复提交

**依赖：** 任务 1、2、4。**交付：** 素材详情能保存后提交真实任务，错误时保留用户输入。

**新增：** asset-image-generation.ts、AssetImageGeneration.tsx、useAssetImageGeneration.ts；`frontend/tests/asset-image-generation.test.mjs`。**修改：** AssetLibraryPanel.tsx。

接口约定：`buildAssetImageRequest(asset, options): ImageGenerationRequest`；options 为 `{configId?: string; supplement: string; count: number; aspect?: ImageGenerationRequest['parameters']['aspect']; resolution?: string}`（如现有 parameters 可选，用 NonNullable 提取）。组件接收 `asset/readOnly/onSaveBeforeGenerate`；回调返回 `Promise<LibraryAssetRead | null>`，null 表示取消保存，禁止继续提交。轮询与服务访问集中在 hook，纯函数文件仅使用 type import，以兼容现有 node 测试编译方式。

- [x] 使用现有测试中的 typescript.transpileModule + data URL 方式导入纯函数，写请求行为测试：保存返回版本进入 source；空补充要求允许；未选参数省略；scope 不写入来源；当前图片不自动作为 reference。

```javascript
const body = buildAssetImageRequest(
  { id: '101', row_version: '8' },
  { configId: '201', supplement: '', count: 1 },
);
assert.deepEqual(body.source, { scene: 'asset_image', asset_id: '101', row_version: '8' });
assert.deepEqual(body.input.reference_media_ids, []);
assert.equal(body.parameters.aspect, undefined);
```

- [x] frontend 运行 `node --test tests/asset-image-generation.test.mjs` 确认未实现失败；实现请求构造与表单，启用现有禁用按钮并移除“下一步接入”提示。
- [x] 重构本面板的短操作协调：外层 ref 控制一次保存→提交链；保存内部不得因外层 busy 提前返回；保存成功使用返回值构造 body。已有手动保存和采用复用同一保存逻辑。
- [x] 复用 requestAttempt/clearAttempt；scope 为 asset-image 加真实 ID。成功回执合入当前任务；网络失败保留尝试 key 和表单；不将服务端冲突静默变为新请求。
- [x] 候选采用先完成未保存内容处理，再发采用请求；按 stale_source/shared_asset_confirmation_required 显示明确提示，保留同一次候选采用的确认标志。
- [x] 运行 `npm test` 和 `npm run typecheck`。用受控请求验证双击仅一次 POST、取消共享保存零 POST、保存版本冲突零 POST、网络不确定重试 key 不变；这些交互必须在任务 7 浏览器验收中执行，不以源码字符串断言代替。

## 任务 6：历史恢复、候选预览与异常状态

**依赖：** 任务 3–5。**交付：** 关闭/刷新页面后可恢复任务并采用结果。

**修改：** useAssetImageGeneration.ts、AssetImageGeneration.tsx、AssetLibraryPanel.tsx、asset-image-generation.ts；扩展 frontend/tests/asset-image-generation.test.mjs。

纯合并函数定义为 `mergeAssetImageTasks(current: GenerationSummary[], incoming: GenerationSummary[]): GenerationSummary[]`：以 ID 去重，incoming 覆盖同 ID，按 created_at/id 降序；不得让旧请求覆盖较新轮次结果，hook 在合并前核验选择 ID 与请求序号。

- [x] 写纯测试：终态覆盖 queued、重复 ID 只一条、跨页合并保留活动旧任务、部分失败任务仍显示已有结果。
- [x] 运行 `node --test tests/asset-image-generation.test.mjs` 确认新断言失败，再实现合并与 active 任务分页查询。
- [x] 按设计加入 AbortController、请求序号及 3/15 秒轮询；关闭清理定时器；每轮结束再安排下一轮，禁止重叠轮询。服务器错误显示“暂时无法刷新”，保留原状态。
- [x] 将候选读取改为当前页刷新与按需加载更多；任务运行中已归档图片立即可见，进入终态刷新；素材内容保存后重新读取来源过期标识。
- [x] 候选增加完整图片预览和来源状态；任务增加 can_* 对应操作与结果警告，恢复成功后重新读取任务。只读详情保留查看，所有写操作禁用。
- [x] 运行 `npm test`、`npm run typecheck`、`npm run build`；任务 7 浏览器覆盖 A→B 切换时 A 慢响应不得写入 B、关窗不中断生成、重新打开从服务端恢复、旧任务超出第一页仍可追踪。

## 任务 7：业务闭环、回归与文档交付

**依赖：** 任务 1–6。**交付：** 验收记录与当前文档一致，功能入口可发布。

**测试：** 完成 `backend/tests/integration/test_asset_image_generation.py`；保留现有 test_asset_library.py 与 test_production_generation.py；不删除现有有效用例来获得通过。

**文档：** 更新 `docs/api/README.md`、`docs/architecture.md`、`frontend/PRODUCT.md`、`frontend/src/api/README.md` 和根 README 中实际受影响的能力描述；开发命令有变化才修改 docs/development.md。数据库不变时不修改建表 SQL。

- [x] 使用 mysql_engine/db_session 随机库与测试内 ControlledProvider/FakeStorage，按 test_production_generation.py 的有效 PNG 输出模式驱动实际执行器；默认不启用其真实基础设施分支。
- [x] 参数化角色/场景/道具，验证“保存→create→执行→候选→confirm→绑定分镜→prepare_shot_image”的完整链路。断言采用前不覆盖原图片，采用后分镜 reference_media_ids 包含新图且不再引用被替换旧图。
- [x] 验证 source_scene/source_id 精确过滤和分页 total：同素材跨库历史相同，两个素材不串历史，复制素材新 ID 查不到原任务。验证任务 3 的事务回滚、重复关联和任务 4 的两个采用入口。
- [x] 按下列命令完成自动回归。新增测试数量随实现统计，不能套用上一轮清理工作的通过数字。

```powershell
# backend 目录
.venv/Scripts/python.exe -m pytest tests/unit tests/api -q
.venv/Scripts/python.exe scripts/run_integration.py tests/integration/test_asset_image_generation.py tests/integration/test_asset_library.py tests/integration/test_production_generation.py -q
.venv/Scripts/python.exe -m ruff check src tests scripts
.venv/Scripts/python.exe -m ruff format --check src tests scripts
```

```powershell
# frontend 目录
npm test
npm run typecheck
npm run build
```

- [ ] 完整扩展环境验收：核心浏览器链路已通过（见交付记录）；完整弱网、多页交互矩阵及真实供应商联调尚未执行。真实调用需记录费用和结果，不能用替身通过宣称供应商已验收。
- [x] 更新当前 API/产品文档；检查本地链接、移除已失效的“素材图片生成未接通”描述。将本文状态改为已实施时须填入实际检查结果和剩余限制。
- [ ] 发布顺序：后端与图片 Worker 全量更新 → 验证旧任务正常和新来源可执行 → 前端开放入口。若回滚前端，保留新后端处理能力直到 asset_image 在途任务结束；不能把新任务交给旧 Worker。

## 验收矩阵

| 场景 | 必须看到的结果 |
| --- | --- |
| 三种素材，各生成 1 张；再生成多张 | 输入按分类构造，所有归档输出进入该素材候选 |
| 未保存编辑后生成 | 先保存，任务记录保存后的版本；取消保存不创建任务 |
| 快速双击/请求超时重试 | 一次意图仅一个任务；同 key 重放不多调用模型 |
| 任务运行中修改素材 | 旧结果保留，候选显示内容已变化；明确确认后可采用 |
| 采用第一张，再查看同批第二张 | 第二张不因纯图片采用导致的版本增加而过期 |
| 两人并发采用/编辑 | 旧 row_version 或 expected_media_id 被拒绝；确认标志不能绕过 |
| 从另一共享库打开同一素材 | 相同历史与候选；采用仍提示共享影响 |
| 复制共享素材后打开副本 | 副本生成任务按新 ID 查询，不混入原任务 |
| 超过 20 条历史且旧任务仍运行 | 刷新后能找回该活动任务并继续追踪 |
| 刷新页面/关窗/快速切换素材 | 任务继续；重开恢复；旧响应不覆盖新素材 |
| 部分结果保存失败/Worker 重复消息 | 成功候选可用，恢复只补保存，候选无重复 |
| 来源消失或归档期间数据库故障 | 前者结果留在媒体库并给出警告；后者原子回滚且可恢复 |
| 手动上传、通用媒体选择、已有分镜生成 | 原有行为正常，未被素材专用校验误伤 |
| 素材候选被明确采用后生成分镜图 | 模型输入包含最新已确认的素材参考图片 |

## 执行与评审边界

顺序为 **1 → 2 → 3 → 4 → 5 → 6 → 7**。任务 4 完成后检查后端候选/采用闭环；任务 6 完成后检查页面恢复与竞争条件；任务 7 完成后才宣称功能交付。

每个任务按“新增失败测试 → 最小实现 → 对应验证 → 差异评审”推进。任务中的小步骤是操作顺序，不是工期承诺。若提交版本，仅按用户的提交安排整理已审查变更；当前工作区已有大量修改，禁止为方便而使用 git add . 混合提交。

首期最主要的正确性风险是归档和候选的事务边界、提交前保存版本、采用时过期判断。这三项分别由任务 3、5、4 覆盖，不能留到界面完成后再补。
