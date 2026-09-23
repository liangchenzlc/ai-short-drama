# 分镜图片闭环实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans 按任务执行；需要并行时使用 superpowers:subagent-driven-development。每个任务验证通过后才勾选，不自动提交 Git。

**Goal:** 跑通素材图片确认、分镜关联、参考图生图及明确采用，并验证失败恢复与刷新后的持久化。

**Architecture:** 复用现有 asset_image/shot_image、任务中心、媒体归档及采用接口。仅补齐前端上下文衔接、能力反馈、按需历史和采用契约；后端先补回归，只有复现缺口时才改对应服务。

**Tech Stack:** React 19、TypeScript、Ant Design、Node test；Python 3.12+、FastAPI、Pydantic、SQLAlchemy、pytest；MySQL 8、RabbitMQ、Celery、MinIO。

**Spec:** [分镜图片闭环设计](../specs/2026-09-23-shot-image-workflow-design.md)。执行前同时阅读此设计和本计划。

**状态：** 任务1–7已实施并通过分层验收，任务8的真实隔离基础设施部分通过；付费视觉验收未执行。用户本轮明确要求完成后提交并推送，覆盖原“不提交 Git”约束。实际验证范围及未执行项见 [验收记录](../../reviews/2026-09-23-shot-image-workflow-acceptance.md)。

## Global Constraints

- 不新增数据库表或迁移，不重构任务系统，不恢复已删除旧文档，不提交 Git。
- 不扩展批量生图、宫格切图、视频、配音、字幕；保留现有其他布局功能，首轮以单镜单图验收。
- 自动测试使用有效图片、模型/存储替身及随机 MySQL 测试库，不操作业务库。
- 模型替身证明数据与状态链路；本地 HTTP 供应商替身证明实际协议载荷；两者不能证明真实供应商视觉效果。
- 真实 RabbitMQ/MinIO 演练与真实模型验收分开记录；付费前确认配置、样本和费用上限，无自动付费重试。
- 当前工作区有大量既有修改；执行前记录 `git status --short`，只修改本任务涉及文件，不重置或批量格式化。
- 后端命令在 `backend/` 执行，前端命令在 `frontend/` 执行。Windows 使用 `./uv.cmd`；测试用例名保持唯一，unit/API 分开运行以避免已有跨目录同名模块收集冲突。

## 任务依赖与检查点

`1 基线 → 2 参考图可见性 → 3 保存/刷新协调 → 4 模型与提交 → 5 历史恢复 → 6 采用 → 7 本地验收 → 8 真实环境验收`

每个开发任务先添加行为测试并观察结果：新增能力应先失败；既有行为若已通过则保留回归，不为了制造红灯修改正常逻辑。完成后记录修改文件、命令结果、未验证边界，再推进下一项。

## 任务 1：把现有集成用例延伸至最终采用

**文件：** 修改 `backend/tests/integration/test_asset_image_generation.py`、`backend/tests/unit/test_generation_adapters.py`。本任务默认不改业务代码。

**接口：** 使用已有 `flow` fixture、`drain(flow, task_id)`、`flow.generations.detail()`、`flow.media.apply()`、`EpisodeStoryboardService.get()`。输出可重复运行的闭环基线。

- [x] 记录工作区状态与现有测试结果。运行 `./uv.cmd run pytest tests/unit/test_generation_business.py tests/unit/test_generation_adapters.py -q`；结果失败时区分本任务缺口与既有故障。
- [x] 扩展 `test_generated_candidates_require_adoption_then_supply_shot_references`：当前用例的 `shot_task` 创建及快照断言后，继续执行任务、读取候选、采用，再用新读取验证持久化。追加核心断言如下：

```python
assert drain(flow, shot_task["generation_id"]) == "succeeded"
detail = flow.generations.detail(shot_task["generation_id"])
candidate = detail["result"]["assets"][0]
before = board.get(flow.project.id, flow.episode.id, shot.id)["shot"]
assert before["image"] is None
flow.media.apply(candidate["asset_id"], {
    "target": {"type": "shot_image", "id": str(shot.id)},
    "expected_media_id": None,
    "expected_row_version": before["row_version"],
    "expected_context_hash": before["context_hash"],
})
after = board.get(flow.project.id, flow.episode.id, shot.id)["shot"]
assert after["image"]["media_id"] == candidate["media_id"]
assert after["image"]["is_stale"] is False
assert len(flow.provider.calls) == 2
```

- [x] 增加同镜关联角色、场景、道具的组合用例；加一个未确认素材和两个共用媒体的素材，断言仅包含确认媒体、去重、不包含未采用候选；沿用同一 `flow`，不复制整套 fixture。
- [x] 检查 `flow.provider.calls` 中分镜提交的参考图映射；扩展已有 `test_ark_image_reference_and_sequential_images`，断言本地 HTTP 请求 `image` 字段完整保留多张参考图顺序。测试用域名及有效图片均为替身，不使用真实 URL。
- [x] 运行 `./uv.cmd run python scripts/run_integration.py tests/integration/test_asset_image_generation.py -q`；记录新鲜 session 读回结果，不能只检查内存 ORM 对象。

**完成条件：** 从素材生成至分镜采用有闭环证据；重复执行 Worker 不增加供应商调用或媒体，生成完成本身不采用。

## 任务 2：让关联素材与实际参考图状态可见

**文件：** 新建 `frontend/src/features/projects/shot-image-workflow.ts`、`frontend/src/features/projects/ShotReferenceAssets.tsx`、`frontend/tests/shot-image-workflow.test.mjs`；修改 `StoryboardStage.tsx`、`ShotImageCandidates.tsx`（均位于现有目录）；必要样式限于 `frontend/src/app/web.css`。

**接口：** 在 `shot-image-workflow.ts` 定义 `summarizeShotReferences(assetIds: readonly string[], assets: readonly LibraryAssetRead[]): ShotReferenceSummary`，使用 type-only import。`ShotReferenceSummary` 包含 `linkedCount: number`、`referenceMediaIds: string[]`、`unreadyAssetIds: string[]`、`missingAssetIds: string[]`。ID 全部保持字符串。

- [x] 沿用现有 `.mjs` 的 TypeScript transpile/data-URL 加载模式，添加函数行为测试。测试数据用轻量对象即可，运行 `node --test tests/shot-image-workflow.test.mjs`，先验证缺少导出时报错。

```javascript
const assets = [
  { id: '1', state: 'confirmed', media_id: '90' },
  { id: '2', state: 'confirmed', media_id: '90' },
  { id: '3', state: 'unconfirmed', media_id: '91' },
];
assert.deepEqual(summarizeShotReferences(['1', '2', '3', '4'], assets), {
  linkedCount: 4,
  referenceMediaIds: ['90'],
  unreadyAssetIds: ['3'],
  missingAssetIds: ['4'],
});
```

- [x] 实现纯函数：按关联 ID 找素材，仅选 `state === 'confirmed' && media_id`，参考媒体用 Set 去重；未找到与未准备分开，不依赖缩略图 URL 判定引用资格。
- [x] 实现 `ShotReferenceAssets({assetIds, assets, loading, error, onRefresh})`；显示名称、种类、状态、缩略图和汇总，无图时明确说明仅文字上下文。沿用现有图片预览组件，不新增素材编辑器。
- [x] 图片区打开及用户主动刷新时更新素材摘要；相关查询失败保留上次内容并标记非最新，不宣称参考图已验证。
- [x] 扩展测试覆盖空关联、缺失素材、确认但无图、URL 失效、重复媒体；运行上述 Node 测试及 `npm run typecheck`。

**完成条件：** 用户能区分素材数量、图片数量与未确认项；不改变现有素材确认规则，不发送未确认图片。

## 任务 3：明确保存、刷新和冲突的执行顺序

**文件：** 修改 `frontend/src/pages/projects/episode/StoryboardStage.tsx`、`frontend/src/features/projects/storyboard-session.ts`、`ShotImageCandidates.tsx`、`shot-image-workflow.ts`；新增 `frontend/tests/shot-image-session.test.mjs`。

**接口：** 在 `shot-image-workflow.ts` 定义 `PreparedShot = { shot: ShotRead; release: () => void; isCurrent: () => boolean }`；`ShotImageCandidates` 使用父组件提供的 `prepareShot: () => Promise<PreparedShot | null>`，替代分散的 `flush/getShot` 调用。父组件以 ref 集合同步占锁，以 state 更新编辑禁用状态；`null` 分支由父组件自行释放锁并给出原因，成功分支由调用方 finally 调用幂等的 release，并在每次网络修改前检查 isCurrent。生成和采用共用这把本镜短操作锁。

- [x] 在纯逻辑中定义 `shotPreparationChanged(local: ShotRead, remote: ShotRead): boolean`；比较 `row_version`、`context_hash`、当前 `media_id` 和图片设置。定义测试：只改 context_hash 也返回 true，只换签名 URL 返回 false，服务器归档必须中止。
- [x] 抽出可注入 save/read 回调的准备协调逻辑测试，验证保存失败时 read/create 均不调用，读取完成后本地又有编辑时不覆盖、不提交，切换分集后忽略旧响应。
- [x] 父组件按以下步骤实现准备，不用无条件整页刷新代替：

```text
取得本镜同步锁 → 锁定本镜编辑 → saveShot(id)
保存失败：保留 dirty，返回 null
取得保存后的本地镜头 → api.shot(id)
请求过期/已归档/仍有未结算编辑：返回 null
比较版本、哈希、当前媒体和设置
有变化：更新干净镜头、显示核对提示，返回 null
无变化：返回 { shot: 服务端镜头, release }，锁保持至提交结束
调用方操作 finally：调用 prepared?.release()，释放本镜锁
```

- [x] 更新本集画幅的变更核对；不把旧的 `episodeAspect` 与新镜头混合提交。有冲突让用户重新核对；最终仍由服务端检查设置与哈希。
- [x] 测试刷新保持 dirty 镜头原文、409 不继续生成、双窗口更新后必须再点一次；运行 `node --test tests/shot-image-stage.test.mjs tests/production-workflow.test.mjs` 和 `npm run typecheck`。

**完成条件：** 不用旧上下文生成，不静默采用其他窗口修改，不覆盖未保存草稿。

## 任务 4：复用模型能力接口并保护提交幂等

**文件：** 修改 `frontend/src/api/modules/ai-model-configs.ts`、`frontend/src/api/types/ai-model-configs.ts`、`frontend/src/features/projects/EpisodeModelSelect.tsx`、`StoryboardStage.tsx`、`ShotImageCandidates.tsx`、`shot-image-workflow.ts` 和相应测试；后端仅在新增回归证明缺口时修改对应校验服务。

**接口：** 新增 `aiModelConfigs.capabilities(id: string, signal?: AbortSignal)`，响应类型为已有后端字段 `{known: boolean, parameters: string[], reference_images: boolean, first_frame: boolean, last_frame: boolean}`。`EpisodeModelSelect` 增加可选 `onResolvedChange` 并透传既有 `ConfigSelect` 回调，不改变其他调用者语义。

- [x] 测试默认配置解析、无配置、停用配置、模型切换后旧能力响应晚到；已知不支持参考图时不得调用 generateImage；未知能力不显示已支持。
- [x] 图片模型在分集级查询能力一次并下传，不每个镜头重复查询。能力明确不匹配时显示更换模型提示；未知或查询失败需先解决能力状态，不悄悄降级成纯文生图。
- [x] `generate()` 进入时用 ref 同步锁保护，覆盖准备、幂等 key 获取和 HTTP 回执全过程；形成 `config_id`、source、parameters 不可变的本次请求。局部编辑只在短提交阶段锁定，任务受理后恢复。

```text
同步锁未占用才进入 → prepareShot() → 能力/参考图核对
冻结 body → requestAttempt(scope, body, storage) → generateImage(body, key)
成功回执：clearAttempt，刷新任务
受理未知：保留 key 和本次意图，先核对任务，不自动换 key 再发
明确参数拒绝：保留表单，显示服务端错误，不自动调整参数
finally：释放同步提交锁并调用 prepared?.release()
```

- [x] 在 `frontend/tests/shot-image-attempt.test.mjs` 补充独立提交owner、晚到回执保护及存储不可用回归；既有 generation-attempt 和浏览器测试验证幂等重放及跨页面未知受理保护。
- [x] 在 `backend/tests/unit/test_generation_adapters.py`、`test_ai_generation.py` 验证不支持参考图/参数在供应商调用前失败且不创建任务；不在前端维护供应商参数值表。
- [x] 运行 `node --test tests/shot-image-workflow.test.mjs tests/generation-attempt.test.mjs tests/production-workflow.test.mjs`；后端运行 `./uv.cmd run pytest tests/unit/test_generation_adapters.py tests/unit/test_ai_generation.py -q`。

**完成条件：** 不静默丢参考图，双击只产生一个逻辑任务，默认模型选择与能力提示一致，配置变更仍由后端最终校验。

## 任务 5：按需加载任务、候选及恢复入口

**文件：** 新建 `frontend/src/features/projects/useShotImageGeneration.ts`；修改 `ShotImageCandidates.tsx`、`StoryboardStage.tsx`、`shot-image-workflow.ts`、`frontend/tests/shot-image-workflow.test.mjs`。复用 `frontend/src/features/generations/TaskDetail.tsx`，不复制其重试逻辑。

**接口：** Hook 输入 `{shotId: string, enabled: boolean}`；输出 `tasks, candidates, loading, error, refresh, loadMoreTasks, loadMoreCandidates, hasMoreTasks, hasMoreCandidates`。TaskDetail 的 `onChanged/onCreated` 调用 refresh。生成提交仍使用任务 4 的独立提交保护。

- [x] 添加历史合并与轮询测试：历史超过 20 条、旧活动任务跌出首页、任务部分结果、取消仍有已归档候选、网络失败后恢复、镜头切换晚响应。
- [x] 历史与候选各请求第一页 20 条；活动任务使用 queued/running 过滤完整分页，消失的已知活动 ID 查详情对账。结果以 ID 合并，不反复遍历全历史。
- [x] 打开的操作区用递归定时器，活动 3 秒、闲置 15 秒；取消请求与定时器并忽略旧序号响应，避免请求堆积。窗口重新可见立即刷新。
- [x] 默认不挂载每镜的历史 Hook；用户展开图片操作区才启用，关闭停止轮询。保持分镜脚本原有编辑状态，折叠不丢本次补充要求。多镜头页面不启动几十个后台查询循环。
- [x] 添加“查看任务”入口打开现有 TaskDetail；按服务器能力显示取消、安全恢复、按旧输入重试，刷新候选不自动采用。
- [x] 运行新增 Node 测试、`npm run typecheck`；浏览器用 80 镜样本记录请求数：未打开图片区时不发逐镜任务/候选请求，打开一镜只加载该镜范围。

**完成条件：** 刷新或重开后任务可继续跟踪，失败仍能看到已有输出，长历史不导致每次全量查询。

## 任务 6：历史候选采用保持参数真实性与并发保护

**文件：** 修改 `frontend/src/features/projects/workflow-contract.ts`、`ShotImageCandidates.tsx`、`frontend/tests/production-workflow.test.mjs`；扩展 `backend/tests/integration/test_asset_image_generation.py`、`backend/tests/unit/test_media_asset.py`。后端服务仅在测试证实必要时修改。

**接口：** 将 `shotImageApplyRequest(shot, acknowledgeStaleSource)` 用于当前分镜候选入口，不再从当前 image_settings 构造 parameters；后端已有从生成记录补齐参数的逻辑继续负责。其他通用媒体采用入口保持原契约。

- [x] 先新增前端失败测试：以当前设置已改成另一分辨率的镜头构造请求，断言仍含三个并发 token，但没有 `parameters`。

```javascript
const body = workflow.shotImageApplyRequest({
  id: '11', row_version: '9', context_hash: 'a'.repeat(64),
  image: { media_id: '80' },
  image_settings: { layout: 'single', aspect: 'inherit', resolution: '4K' },
}, true);
assert.equal(body.expected_row_version, '9');
assert.equal(body.expected_context_hash, 'a'.repeat(64));
assert.equal(body.expected_media_id, '80');
assert.equal(body.acknowledge_stale_source, true);
assert.equal('parameters' in body, false);
```

- [x] 调整 helper 及本页调用方，删除当前设置参数；全仓搜索 `shotImageApplyRequest` 更新签名调用，不改变其他目标类型。
- [x] 按需读取候选 `generation_id` 对应详情，在预览内展示原生成布局、画幅、分辨率与当前差异；不为整个候选列表逐张请求详情。来源或参数缺失不猜测，显示无法直接采用原因。
- [x] 采用调用任务 3 的准备流程。`stale_generation_source` 弹确认后重新核对目标；期间再次变化时停止，不能把 acknowledge=true 带到新目标版本后无提示提交。
- [x] 后端回归覆盖：2K 候选生成后把当前设置改为 4K，采用仍记录 2K且不改未来设置；素材换图后旧候选需确认；确认不能绕过 stale row/media/hash；重复采用不重复回收；替换后旧媒体存在，跨镜候选未经来源确认不能采用。
- [x] 运行 `node --test tests/production-workflow.test.mjs`、`./uv.cmd run pytest tests/unit/test_media_asset.py -q`、`./uv.cmd run python scripts/run_integration.py tests/integration/test_asset_image_generation.py tests/integration/test_media_workflows.py -q`（按各自工作目录）。

**完成条件：** 旧候选可按真实参数采用，刷新后显示正确当前图；不削弱并发校验或伪造历史媒体元数据。

## 任务 7：自动回归与浏览器闭环验收

**文件：** 新建 `docs/reviews/2026-09-23-shot-image-workflow-acceptance.md` 记录实际证据；如能力/交互有变，更新 `frontend/PRODUCT.md`、`docs/api/README.md`、`docs/development.md` 对应段落，不改无关章节。

- [ ] 后端分别运行 `./uv.cmd run pytest tests/unit -q`、`./uv.cmd run pytest tests/api -q`；限定改动文件运行 Ruff check/format --check。已有无关故障单列，不扩散修复。
- [ ] 前端运行 `npm test`、`npm run typecheck`、`npm run build`。Node 纯函数/源码约定测试不计为浏览器行为验收。
- [ ] 浏览器使用隔离 API/供应商替身和有效图片，执行下表；桌面及窄屏检查同一批步骤，修正本任务问题后最多再确认一轮。记录实际工具和隔离方式，不凭静态源码断言交互已通过。

| 场景 | 操作 | 必须观察到的结果 |
| --- | --- | --- |
| 正常闭环 | 三类素材各生成1图并确认，关联同镜，生成1图并采用 | 候选不自动采用；引用正确；重开后当前图一致 |
| 未确认素材 | 保留一个有候选但未采用的素材 | 明示不作为图片参考，任务不携带该候选 |
| 共享素材 | 取消共享采用确认，再确认采用 | 取消不改当前图；确认遵循版本控制 |
| 双击及延迟 | 连点生成，延迟回执 | 仅一个逻辑任务；不重复模型调用 |
| 素材换图 | 另一窗口替换已关联素材图片，再点生成 | 先核对变化，不使用旧哈希强行继续 |
| 保存冲突 | 修改正文同时制造409 | 草稿保留，不创建图片任务 |
| 模型不支持 | 带参考图选择不支持的模型 | 明确阻止或服务端提交前拒绝，不静默丢图 |
| 归档失败 | 替身返回图片后注入一次归档失败，再安全恢复 | 原结果保留，恢复不再调用模型 |
| 旧候选采用 | 生成后修改素材或脚本，再采用旧图 | 核对来源，确认不绕过目标并发比较 |
| 设置已变 | 2K候选生成后选择4K，再采用2K图 | 展示差异，采用元数据仍为2K |
| 部分输出 | 生成多图但仅一图归档成功 | 已保存候选可见可采用，其余错误明确 |
| 长列表及切换 | 80镜、历史多页，快速打开关闭两镜图片区 | 不串候选；请求范围受控；关闭停止轮询 |
| URL过期 | 让预览签名失效，再刷新 | 不丢采用记录；重新获取预览链接 |

- [ ] 验收文档逐项标记通过、失败或未执行；记录浏览器可见状态、稳定 ID 和必要请求次数，不保存密钥或带签名的 URL。只有本轮实际结果才能写入通过清单。

**完成条件：** 本地逻辑、协议载荷、数据库和浏览器层的证据分开齐备，未验证事项清楚列明。

## 任务 8：真实基础设施与小样本模型验收

**文件：** 仅补充上述验收文档，必要部署命令写入 `docs/development.md` 的既有运行说明。

- [ ] 在隔离命名空间/测试库/测试对象中演练 RabbitMQ 调度、图片 Worker 和真实 MinIO；优先模型替身。记录服务健康及消费者情况，不把进程启动当作链路成功。
- [ ] 明确真实验收使用的项目/分集、模型配置、支持参数、参考图上限及费用上限，获得付费调用授权后再继续；没有授权标记“真实模型验收未执行”。
- [ ] 最小付费样本为角色、场景、道具各1张，加分镜1张，共4张预期输出；若已有合适确认素材可复用，只调用1次分镜生成。视觉不满意时停止并展示结果，不自动重试或扩至整集。
- [ ] 核对实际供应商请求中的参考图、输出归档、候选预览、采用与刷新。视觉检查包括人物外观、场景布局、道具是否受参考影响；参数正确不等于画面质量已达标。
- [ ] 若发布需要重启，按既有启动脚本识别并优雅重启受影响角色，核对 API、调度、图片 Worker 和对象访问；不因为仅 API 健康就宣称完成。
- [ ] 保存本次新增数据 ID 清单。业务验收结果默认保留；清理必须按清单明确授权，禁止按名称批量删除。

**完成条件：** 真实环境证据与本地测试分开交付；若只完成任务1–7，则结论只能写“本地闭环通过，真实供应商待验收”。

## 自检与交接

- 参考图来源、去重与确认规则：任务1、2、4。
- 草稿保护、版本/上下文变化及重复提交：任务3、4。
- 模型能力、参数本地校验：任务4。
- 任务与候选隔离、部分失败、安全恢复、按需加载：任务5、7。
- 旧来源确认、历史参数、采用幂等与刷新持久化：任务1、6、7。
- 付费边界、真实基础设施、发布与数据保留：任务8。

建议先顺序执行任务1–3并交付第一个检查点，再推进4–6；最后执行7。任务8单独安排，不是默认自动执行步骤。
