# 小说到分镜生图实施计划（已批准，实施中）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 用户审批前不得开始实施。

**Goal:** 完成小说生成剧本、分镜持久化、剧本生成分镜、素材持久化、分镜生图采用五条真实前后端流程。

**Architecture:** 维持 API → Service → DAO，复用三类生成 API 和现有 RabbitMQ/Celery任务设施。业务来源负责快照和结果处理，用户采用通过独立同步事务完成。前端使用按业务拆分的 Axios API 和编辑会话。

**Tech Stack:** Python 3.12+、uv、FastAPI、SQLAlchemy 2、PyMySQL、MySQL8、RabbitMQ/Celery、MinIO、React/TypeScript/Ant Design、Axios；上传新增 python-multipart，图像校验复用Pillow。

**Spec:** [设计](../specs/2026-09-21-production-workflow-design.md)、[API](../../api/2026-09-21-production-workflow-api.md)、[数据库](../../数据库模型/2026-09-21-production-workflow-changes.md)、[验收](2026-09-21-production-workflow-acceptance.md)。

## 全局约束

- 用户已通过“开始实施”批准本方案；按下列范围实施应用、数据库与依赖变更。
- 不安装本地MySQL；集成测试只在明确配置、隔离的 `_test` 库运行。
- 不复用此前一次付费诊断授权；真实模型调用数量/配置/预算另行确认。
- API ID/版本序列化为字符串；正文保留原空白。
- 生成任务状态保持 queued/running/succeeded/failed/cancelled；failed显示“失败”。
- 用户明确采用前不替换当前剧本、分镜或图片；全部生成图片进入资产库。
- 分镜替换和删除为归档；不级联删除已生成文件。
- 不新建消息表、工作流引擎、任务队列类型或统一巨型workflow JSON字段。
- 所有数据库变更同步schema.mysql8.sql、Domain、迁移与表设计文档。
- 本期未开放的AI素材分析、素材独立生图、生视频和切格按钮不得输出演示结果。
- 每阶段都从页面验收到数据库并刷新恢复；依赖后续阶段的交互在最终验收补齐，不能提前勾选通过。

## 开始前的工作区与基线

当前工作区保留上一阶段尚未提交的小说/剧本持久化代码；不得reset、覆盖或把它误认为本次改动。

- [x] 读取本次全部审批稿与用户修订意见；记录已批准范围。
- [x] 记录git分支、状态和已有差异；保留当前 feature 分支未提交实现和可运行配置，不复制/提交.env。
- [x] 运行已有后端单元/API测试、前端Node测试，保存基线；结果见验收记录。
- [ ] 固化验收用project/episode数据前缀及清理策略；输出中不打印连接凭据。

## 模块与内部接口

以下是拟新增内部方法签名，字典均须由闭合Pydantic请求/响应模型验证；API层只转交参数，DAO不commit，事务由Service持有。

```python
class GenerationContextService:
    def prepare_text(self, request: dict) -> dict: ...
    def prepare_shot_image(self, request: dict) -> dict: ...

class GenerationBusinessService:
    def save_text_result(self, task_id: int, record_id: int) -> dict: ...
    def apply_storyboard(self, project_id: int, episode_id: int,
                        generation_id: int, payload: dict) -> dict: ...

class EpisodeStoryboardService:
    def list(self, project_id: int, episode_id: int, *,
             offset: int = 0, limit: int = 100,
             include_archived: bool = False) -> dict: ...
    def get(self, project_id: int, episode_id: int, shot_id: int) -> dict: ...
    def create(self, project_id: int, episode_id: int,
               payload: dict, idempotency_key: str) -> dict: ...
    def update(self, project_id: int, episode_id: int,
               shot_id: int, payload: dict) -> dict: ...
    def reorder(self, project_id: int, episode_id: int, payload: dict) -> dict: ...
    def archive(self, project_id: int, episode_id: int,
                shot_id: int, row_version: int) -> None: ...

class AssetLibraryService:
    def list(self, kind, parent_id=None, project_id=None, **filters) -> dict: ...
    def create(self, kind, parent_id, project_id, payload,
               idempotency_key: str) -> tuple: ...
    def link(self, kind, parent_id, project_id, asset_id: int): ...
    def unlink(self, kind, parent_id, project_id,
               asset_id: int, row_version: int) -> None: ...

class AssetImageService:
    def add_candidate(self, asset_id: int, media_id: int) -> tuple[dict, bool]: ...
    def upload(self, asset_id: int, stream, length: int, name: str) -> tuple[dict, bool]: ...
    def confirm(self, asset_id: int, payload: dict) -> dict: ...
```

以上为已实现方法的接口摘要。分镜 create 的内部结果含 `created`，API 将它转换为 201/200；对外仍只返回 shot 与 storyboard_version。任务锁/租约校验由 Worker 先完成，save_text_result 在已持有的事务 session 中运行，不自行新开独立事务。
素材 scope 在 API 路由内转换为 kind/parent_id/project_id；前端闭合结构仍为 `{kind:"global"}`、`{kind:"project",projectId}`、`{kind:"episode",projectId,episodeId}`。

## 阶段1：小说生成剧本

**新增文件**

- `backend/src/short_drama/service/generation_context_service.py`：来源校验与输入快照。
- `backend/src/short_drama/service/generation_business_service.py`：文本结果保存和分镜采用。
- `backend/src/short_drama/ai/business_prompts.py`：三个固定版本提示词构造函数，不建模板平台。
- `frontend/src/features/projects/useEpisodeGenerations.ts`：分页、轮询、切换分集时取消读取。
- `frontend/src/features/projects/ScriptCandidates.tsx`：候选列表/预览/切换。
- `backend/tests/unit/test_generation_business.py`、`backend/tests/api/test_episode_generation_api.py`、`backend/tests/integration/test_production_generation.py`。
- `frontend/tests/episode-generations.test.mjs`。

**修改文件**

- `schemas/ai_generation.py`、`schemas/episode_writing.py`、`api/v1/ai_generations.py`、`api/v1/episode_writing.py`、`api/dependencies.py`。
- `service/ai_generation_service.py`、`service/generation_execution_service.py`、`service/generation_service.py`、`dao/episode_writing_dao.py`、`dao/async_task_dao.py`、`tasks/state.py`。
- `main.py`、`core/exceptions.py`：为可展示引用/版本的错误增加白名单details，禁止透出任意异常对象。
- `frontend/src/api/types/generations.ts`、`api/modules/generations.ts`、`api/modules/episode-writing.ts`、`features/generations/TaskDetail.tsx`、`pages/projects/EpisodePage.tsx`、`episode/SourceStage.tsx`、`episode/ScriptStage.tsx`。

上面未带backend前缀的Python路径均相对于 `backend/src/short_drama/`。

**交付接口**：文本业务创建、业务来源筛选、候选列表/详情、成功任务业务结果；通用text/video行为不回归。

- [ ] 先为同键重放、来源越界、旧版本、空小说、通用/业务输入互斥编写失败测试。
- [ ] 运行 `uv run pytest tests/unit/test_generation_business.py tests/api/test_episode_generation_api.py -q`，确认失败对应缺失功能。
- [ ] 实现小说快照和有限source分派；幂等查询先于快照重建，明确模型/模板更新不改变已创建任务。
- [ ] 为Worker结果保存编写重复消费/崩溃恢复测试：先存raw text和save动作，业务保存失败后不得再次调用gateway.submit。
- [ ] 实现候选专用写入：task锁→episode锁→分配position→候选/来源/结果标记同事务；不改编辑指针和content_version。
- [ ] 实现GET候选和详情、任务筛选、result.business；列表禁止携带整篇正文。
- [ ] 前端flush后提交；轮询结果不覆盖编辑框；候选切换走已有WritingSession.select，再确认。
- [ ] 测试旧source snapshot的retry和最新输入新任务的区别；确定性结果校验失败禁用无效resume，尤其不能沿用当前“text_content存在即成功”的save捷径绕过截断/结构校验。
- [ ] 执行本阶段测试和验收N01–N08；本地可控上游不产生真实供应商费用。
- [ ] 审查变更后单独提交本阶段代码（不包含.env和运行日志，不自动push）。

关键断言示例，测试需建立真实任务/小说后在隔离MySQL事务中执行：

```python
before = writing.get(project_id, episode_id)
worker.execute(task_id, message_version)
worker.execute(task_id, message_version)  # 重复消息
after = writing.get(project_id, episode_id)
assert after["editing_script"] == before["editing_script"]
assert after["content_version"] == before["content_version"]
assert gateway.submit.call_count == 1
assert candidate_count_for_task(task_id) == 1
```

测试模块提供 `candidate_count_for_task`：通过novel_script_records.batch_id=task_id统计候选；不以UI列表长度代替数据库断言。

## 阶段2：分镜持久化

**新增文件**

- `backend/src/short_drama/api/v1/episode_storyboard.py`、`schemas/episode_storyboard.py`、`service/episode_storyboard_service.py`、`dao/episode_storyboard_dao.py`。
- `backend/src/short_drama/service/shot_context.py`：唯一的规范化上下文hash函数。
- `frontend/src/api/modules/episode-storyboard.ts`、`features/projects/storyboard-session.ts`、`features/projects/useEpisodeStoryboard.ts`。
- `backend/tests/unit/test_episode_storyboard.py`、`tests/api/test_episode_storyboard_api.py`、`tests/integration/test_episode_storyboard.py`。
- `frontend/tests/storyboard-session.test.mjs`。
- 数据库文档指定的迁移目录及 `001_episode_storyboard.sql`、`003_shot_image_context.sql`。

**修改文件**

- backend域模型 `episode.py/shot_script.py/shot_image.py`，`shot_script_service.py`、`shot_asset_service.py`、相关DAO/Schema、router/dependencies。
- `schema.mysql8.sql`、`MySQL8数据表设计.md`。
- `EpisodePage.tsx`、`StoryboardStage.tsx`、`ShotAdvancedSettings.tsx`、`ShotProductionTable.tsx`，移除真实流程中的sampleShots写入。

**依赖与交付**：承接阶段1已保存剧本；提供ShotRead、行版本、集合版本、归档语义。素材选择先只读已有数据库引用；完整素材页面交互在阶段4验收。

阶段2的上下文服务对尚未上线的素材tags/scene_time/state使用明确默认值（[]/空串/unconfirmed）；阶段4加入真实映射和回归测试。最终正式部署执行全部迁移后再启用整套新页面，不在未迁移库上启用新字段查询。

- [ ] 编写MySQL测试：同版本两个写入仅一个成功；无变化不加版本；相同创建键不重复建镜头。
- [ ] 在测试库执行镜头迁移；验证旧镜头、图、视频、回溯记录行数不变，迁移重入不重置版本。
- [ ] 实现新增/编辑/读取和scope验证；字段只按API白名单更新，不接受整个EpisodeWorkflow。
- [ ] 实现两段正整数重排、集合完整性校验、归档筛选；审计内部旧CRUD避免绕过版本或物理删除。
- [ ] 实现服务端context_hash，校验文本、素材和分集风格变化会影响hash，纯UI/下次分辨率变化不影响。
- [ ] 前端按shot_id保存队列，设置和关联串行；新增/排序/删除等待队列完成，导航未确定状态也要拦截。
- [ ] 添加新增/上下移动/删除按钮、保存指示、冲突恢复和旧草稿下载提示。
- [ ] 运行 `uv run pytest tests/unit/test_episode_storyboard.py tests/api/test_episode_storyboard_api.py tests/integration/test_episode_storyboard.py -q`。
- [ ] 运行 `node --test tests/storyboard-session.test.mjs`，验收S01–S08，分别记录页面、API和DB结果。
- [ ] 审查并提交此阶段及相应schema变更；正式库迁移按最终部署步骤统一安排，不在设计期执行。

核心并发测试：

```python
# 两个独立Session在线程屏障后提交同一row_version；沿用已有integration fixture。
assert sorted(outcomes) == ["conflict", "saved"]
assert int(reloaded["shot"]["row_version"]) == int(original_version) + 1
assert active_positions == list(range(1, len(active_positions) + 1))
assert archived_shot_id in history_ids
assert old_media_id in persisted_media_ids
```

## 阶段3：剧本生成分镜

**新增** `backend/src/short_drama/schemas/storyboard_result.py`、`frontend/src/features/projects/StoryboardResultPreview.tsx`、`backend/tests/unit/test_storyboard_result.py`、`backend/tests/integration/test_storyboard_apply.py`、`frontend/tests/storyboard-results.test.mjs`。

**修改** 阶段1的context/business/prompt服务、阶段2API/DAO，以及文本生成Schema、任务详情、StoryboardStage和Axios模块。

**交付**：script_shots source、规范化结果、append/replace事务。直接重用阶段2ShotRead和版本字段。

- [ ] 编写结构化结果边界测试：裸JSON/单层代码围栏、空结果、101镜、超限正文、额外键、未知/重复素材ID、模型截断。
- [ ] 实现提示词构造和严格结果Schema；确定性无效结果保留raw text并failed，不自动追加模型修复请求。
- [ ] 实现只写response_data.business_result的结果处理，不创建活动分镜。
- [ ] 编写采用测试：同任务并发只采用一次；输入剧本变化拒绝；列表变化拒绝；素材被移除拒绝；同模式重放不复活归档分镜。
- [ ] 实现apply：task锁→episode锁→检查内容hash/版本→归档或追加→关系/来源记录/applied标记原子提交。
- [ ] 前端添加候选预览和追加/替换确认，替换文案明确保留旧镜头历史；成功重载活动列表。
- [ ] 运行 `uv run pytest tests/unit/test_storyboard_result.py tests/integration/test_storyboard_apply.py -q`、`node --test tests/storyboard-results.test.mjs`。
- [ ] 验收G01–G08；再验证阶段2已编辑镜头不会被旧生成结果偷偷覆盖，审查并提交。

```python
first = business.apply_storyboard(project_id, episode_id, task_id, payload)
again = business.apply_storyboard(project_id, episode_id, task_id, payload)
assert again["shot_ids"] == first["shot_ids"]
assert again["already_applied"] is True
assert count_source_records(task_id) == len(first["shot_ids"])
# count_source_records通过script_shot_records.batch_id统计；替换前旧关联行必须仍在。
```

## 阶段4：角色、场景、道具持久化

**新增文件**

- `backend/src/short_drama/api/v1/assets.py`、`schemas/asset_library.py`、`service/asset_library_service.py`、`service/asset_image_service.py`、`dao/asset_library_dao.py`。
- `domain/asset_image_candidate.py`、`dao/asset_image_candidate_dao.py`、`schemas/asset_image_candidate.py`，补齐domain/schema注册。
- `frontend/src/api/modules/assets.ts`、`features/assets/useAssetLibrary.ts`。
- `backend/tests/unit/test_asset_library.py`、`tests/api/test_asset_library_api.py`、`tests/integration/test_asset_library.py`、`tests/unit/test_asset_image_upload.py`。
- `frontend/tests/asset-library.test.mjs`，迁移002和候选回填004。

**修改** `assets` Domain/Schema/Service、共享media apply的asset分支、storage边界、pyproject/uv.lock；AssetsPage、AssetForm、AssetCard、ProjectResourceLibrary、AssetsStage、StoryboardStage、共享Axios错误映射、schema和表文档。

**交付**：三库CRUD/引用、同一本体编辑版本、图片候选/上传/采用、分镜关联真实接通。

- [ ] 为共享引用、新建幂等、跨项目限制、仍被分镜引用时拒绝移除、同名不合并编写失败测试。
- [ ] 测试库执行素材迁移及Snowflake候选回填，保留旧图片；确认重复回填不新增重复关系。
- [ ] 实现三库服务与本体编辑；所有旧内部写路径推进版本；公共asset_image采用复用本服务。
- [ ] 定义上传失败注入：解码失败不上传、MinIO失败不落候选、DB失败补偿、并发同文件去重、20MiB/40MP上限；新增python-multipart并锁依赖。
- [ ] 实现候选列表、选择预览和明确确认；采用生成图不覆盖用户编辑的素材prompt。
- [ ] 接通全局/项目/分集三页面，分类/标签/时间各自保存，搜索由后端分页执行；移除localStorage作为主数据源。
- [ ] 接通分镜asset_ids选择；共享素材改动后重新查询context_hash并标识已有图片需核对。
- [ ] 添加旧本地数据下载入口，提示手动迁移/重新上传；去掉真实页面里的生成演示图片按钮。
- [ ] 执行本阶段pytest与Node测试，验收A01–A12及阶段2素材关联补验，审查并提交。

```python
# 引用同一asset后，在另一Session修改，再以旧版本更新必须冲突。
assert project_item["id"] == episode_item["id"]
assert library_link_count(scope, asset_id) == 1
assert before["media_id"] == after_upload["media_id"]  # 上传不自动采用
assert confirmed["state"] == "confirmed"
assert edited["state"] == "unconfirmed"
assert edited["media_id"] == confirmed["media_id"]
```

## 阶段5：分镜生图及采用

**新增** `frontend/src/features/projects/ShotImageCandidates.tsx`、`backend/tests/api/test_shot_image_generation.py`、`tests/integration/test_shot_image_adoption.py`、`frontend/tests/shot-image-flow.test.mjs`。

**修改** generation_context/prompt、ai_generation Schema/Service、media_asset Schema/Service、shot_media/shot_image Service、shot_context、ShotProductionTable/ImagePreview、资产库AssetDetail和任务详情、相关Axios模块。

**交付**：saved上下文生图、永久结果、分页候选、统一采用及所有入口并发保护。

- [ ] 编写正文/设置/素材引用snapshot测试；未保存版本、归档镜头和超过16参考图拒绝创建任务。
- [ ] 实现saved模式提示词与已确认参考图组装，保留旧显式prompt模式；两种输入严格分开，不猜测协议。
- [ ] 实现图片列表预览和任务状态，引用任务模块查询恢复；布局是整图规则，数量是整图候选数量。
- [ ] 编写采用并发测试：两图同expected_media_id只允许一图获胜；旧上下文拒绝；已采用同图重放不写回收站；部分归档成功结果可采用。
- [ ] 统一分镜页与公共资产库apply入口；从任务快照取历史参数，签名URL重取不能触发生成。
- [ ] 实现上下文变化提示、明确采用旧来源、采用后刷新镜头和资产库状态。
- [ ] 执行本阶段测试与I01–I10；回归素材修改→镜头提示过期→采用重新核对完整路径，审查并提交。

```python
assert shot_before["image"] is None
assert saved_candidate_count(task_id) == 2
assert shot_after_generation["image"] is None
assert shot_after_apply["image"]["media_id"] == chosen_media_id
assert saved_candidate_count(task_id) == 2  # 未选图片仍在资产库
assert recycle_count_after_replay == recycle_count_after_first_apply
```

## 最终部署与验收

- [ ] 自检API文档所有路由均在OpenAPI，输入输出一致；新返回值有明确response_model。
- [x] 自检schema.mysql8.sql与迁移后测试库字段、CHECK、索引一致；不只比较ORM create_all结果。
- [x] 后端在backend目录执行 `uv run pytest tests/unit tests/api -q`、`uv run ruff check src tests`。
- [ ] 配置隔离TEST_DATABASE_URL后执行本计划所有新增MySQL集成用例和原writing/生成/媒体采用相关集成用例；缺库skip不能记为通过。
- [x] 前端执行 `node --test tests/*.test.mjs`、`npm run build`；不重复扩大与本次无关的测试范围。
- [x] 对本次改动review，重点锁顺序、旧入口绕过版本、重复付费、候选误采用和数据丢失。
- [x] 按迁移README执行已批准的云端变更并重启API、worker、scheduler；记录执行的SQL版本和核验结果，不打印密码。
- [ ] 浏览器逐项完成验收清单，每阶段保留页面截图/请求摘要/数据库断言；清理只涉及专用测试数据和本次上传的测试对象。
- [ ] 经真实模型调用授权后，使用指定文本/图片配置完成N/G/I真实样本；报告费用/数量及供应商限制，不把模拟测试算真实生成。
- [x] 写 `docs/superpowers/plans/2026-09-21-production-workflow-verification.md`，记录通过/失败/未执行项目、运行命令、迁移、版本与剩余范围。
- [ ] 向用户交付五阶段结果和未通过项；未经新指示不自动push或上线到另一部署环境。

## 设计自检记录

- 五阶段均有页面、API、Service/DAO、落库及验收条目。
- 数据库字段与API并发令牌一一对应；当前编辑内容版本不被后台新增候选误推进。
- 阶段2引用选择依赖阶段4，在最终验收补齐；阶段3可用空素材列表独立验收。
- 图片上传与模型生成记录分开，所有生成图片持久保留，旧分镜归档避免外键断裂。
- 任务成功和用户采用分别定义，没有新增“未知”等任务状态。
- 实施中的实际文件归并、测试结果及部署状态以同目录 `2026-09-21-production-workflow-verification.md` 为准；未执行的验收不得标记通过。
