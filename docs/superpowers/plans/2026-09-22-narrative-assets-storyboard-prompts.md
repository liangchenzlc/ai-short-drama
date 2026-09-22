# Narrative Assets and Storyboard Prompts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace noun-scanning asset extraction and sentence-by-sentence storyboard generation with narrative importance filtering, subject-led image prompts, beat-based shots, and configurable average shot duration while keeping the existing v1 APIs.

**Architecture:** Move text prompt templates into a package-backed registry while retaining `business_prompts.text_messages` as the stable facade. Extend the existing v1 text request and structured business results, persist generated shot duration and source excerpts, and expose the new metadata through the existing review UIs. All semantic generation remains one model call per task; Pydantic remains the only output-schema authority.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2, MySQL 8, React 19, TypeScript 5.9, Ant Design 5, Node test runner.

**Spec:** `docs/superpowers/specs/2026-09-22-narrative-assets-storyboard-prompt-design.md`

## Global Constraints

- Keep the existing `/api/v1` routes and business `schema_version: 1`; do not add v2 endpoints.
- Use template revisions `script-assets-v1-r2` and `script-shots-v1-r2` only for historical traceability.
- Default `average_shot_duration_ms` and persisted `duration_ms` to `3000`; accepted range is `1000..10000`.
- Keep one model call per asset extraction or storyboard generation.
- Do not add Prompt Canary, live-model scoring, or semantic quality regression tests.
- Do add deterministic code tests for request validation, parsing, migrations, compatibility, UI contracts, and builds.
- Preserve existing task, idempotency, snapshot, review, append/replace, and concurrency behavior.

---

### Task 1: Package-backed prompt registry

**Files:**
- Create: `backend/src/short_drama/ai/prompts/__init__.py`
- Create: `backend/src/short_drama/ai/prompts/loader.py`
- Create: `backend/src/short_drama/ai/prompts/registry.py`
- Create: `backend/src/short_drama/ai/prompts/common/source_boundary.md`
- Create: `backend/src/short_drama/ai/prompts/common/structured_output.md`
- Create: `backend/src/short_drama/ai/prompts/novel_script/system.md`
- Create: `backend/src/short_drama/ai/prompts/script_assets/system.md`
- Create: `backend/src/short_drama/ai/prompts/script_assets/character_rules.md`
- Create: `backend/src/short_drama/ai/prompts/script_assets/scene_rules.md`
- Create: `backend/src/short_drama/ai/prompts/script_assets/prop_rules.md`
- Create: `backend/src/short_drama/ai/prompts/script_shots/system.md`
- Create: `backend/src/short_drama/ai/prompts/script_shots/segmentation_rules.md`
- Create: `backend/src/short_drama/ai/prompts/script_shots/duration_rules.md`
- Modify: `backend/src/short_drama/ai/business_prompts.py`
- Test: `backend/tests/unit/test_business_prompts.py`

**Interfaces:**
- Produces: `load_prompt(relative_path: str) -> str`
- Produces: `system_prompt(scene: str, *, kinds: Iterable[str] = ()) -> str`
- Preserves: `text_messages(scene, snapshot, instructions="") -> list[dict[str, str]]`

- [ ] **Step 1: Write prompt-loader and composition tests**

```python
def test_asset_prompt_composes_only_requested_category_rules():
    prompt = system_prompt("script_assets", kinds=["prop"])
    assert "删除测试" in prompt
    assert "桌椅、花瓶" in prompt
    assert "角色规则" not in prompt

def test_storyboard_prompt_forbids_sentence_splitting():
    prompt = system_prompt("script_shots")
    assert "一句话一个镜头" in prompt
    assert "平均镜头时长" in prompt
```

- [ ] **Step 2: Run `pytest tests/unit/test_business_prompts.py -q` and confirm imports or assertions fail**

- [ ] **Step 3: Implement UTF-8 resource loading with `importlib.resources`, `lru_cache`, and explicit missing-template failure**

```python
@lru_cache(maxsize=None)
def load_prompt(relative_path: str) -> str:
    root = resources.files("short_drama.ai.prompts")
    text = root.joinpath(*relative_path.split("/")).read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"Empty prompt template: {relative_path}")
    return text
```

- [ ] **Step 4: Write the narrative-selection, category-specific subject, beat segmentation, duration, source-boundary, and existing novel-script templates**

- [ ] **Step 5: Refactor `text_messages` to use `system_prompt` while preserving the user JSON envelope**

- [ ] **Step 6: Run prompt tests and build a wheel; inspect the wheel archive to confirm every `.md` template is packaged**

- [ ] **Step 7: Commit with `feat: centralize business prompt templates`**

### Task 2: Extend v1 business request and structured results

**Files:**
- Modify: `backend/src/short_drama/schemas/ai_generation.py`
- Modify: `backend/src/short_drama/schemas/asset_extraction.py`
- Modify: `backend/src/short_drama/schemas/storyboard_result.py`
- Modify: `backend/tests/unit/test_ai_generation.py`
- Modify: `backend/tests/unit/test_asset_extraction.py`
- Modify: `backend/tests/unit/test_generation_business.py`

**Interfaces:**
- Produces: `StoryboardOptions(average_shot_duration_ms: int = 3000)`
- Produces: `TextGenerationCreate.storyboard: StoryboardOptions | None`
- Extends: `ExtractedAsset.importance: Literal["core", "continuity"]`
- Extends: `ExtractedAsset.story_function: RequiredText`
- Extends: `GeneratedShot(title, source_excerpt, story_beat, script, duration_ms, asset_ids)`
- Changes: `parse_storyboard_result(content, allowed_asset_ids, source_content)`

- [ ] **Step 1: Add failing request-validation tests**

```python
def test_storyboard_options_default_and_scene_ownership():
    parsed = TextGenerationCreate.model_validate({
        "source": {"scene": "script_shots", "project_id": "1", "episode_id": "2", "script_id": "3", "content_version": "4"},
        "parameters": {},
    })
    assert parsed.storyboard.average_shot_duration_ms == 3000
    with pytest.raises(ValidationError):
        TextGenerationCreate.model_validate({
            "source": {"scene": "script_assets", "project_id": "1", "episode_id": "2", "script_id": "3", "content_version": "4"},
            "storyboard": {"average_shot_duration_ms": 5000}, "parameters": {},
        })
```

- [ ] **Step 2: Add failing asset parser assertions for `importance` and `story_function`**

- [ ] **Step 3: Add failing storyboard parser tests for source substring, nondecreasing source order, duration range, and unknown asset IDs**

- [ ] **Step 4: Implement `StoryboardOptions` and cross-field ownership validation without changing generic text behavior**

- [ ] **Step 5: Extend asset and storyboard Pydantic result models and return normalized JSON**

- [ ] **Step 6: Run the three focused unit files and confirm all pass**

- [ ] **Step 7: Commit with `feat: extend v1 narrative generation contracts`**

### Task 3: Persist shot duration and source excerpt

**Files:**
- Create: `docs/数据库模型/migrations/2026-09-22-storyboard-prompts/000_precheck.sql`
- Create: `docs/数据库模型/migrations/2026-09-22-storyboard-prompts/001_add_shot_duration_source.sql`
- Create: `docs/数据库模型/migrations/2026-09-22-storyboard-prompts/002_verify.sql`
- Modify: `docs/数据库模型/schema.mysql8.sql`
- Modify: `backend/src/short_drama/domain/shot_script.py`
- Modify: `backend/src/short_drama/schemas/episode_storyboard.py`
- Modify: `backend/src/short_drama/service/episode_storyboard_service.py`
- Modify: `backend/tests/unit/test_episode_storyboard.py`
- Modify: `backend/tests/api/test_episode_storyboard_api.py`
- Modify: `backend/tests/integration/test_episode_storyboard.py`

**Interfaces:**
- Adds DB columns: `shot_scripts.duration_ms INTEGER UNSIGNED NOT NULL DEFAULT 3000`
- Adds DB columns: `shot_scripts.source_excerpt MEDIUMTEXT NOT NULL DEFAULT ''`
- Adds check: `duration_ms BETWEEN 1000 AND 10000`
- Extends reads with `duration_ms: int` and `source_excerpt: str`
- Extends manual create/update with editable `duration_ms`; source excerpt remains service-owned

- [ ] **Step 1: Add failing service/API tests asserting manual create defaults to 3000ms and update accepts 5000ms**

- [ ] **Step 2: Add failing generated-shot test asserting `duration_ms` and `source_excerpt` are persisted**

- [ ] **Step 3: Update ORM and schemas; include `duration_ms` in creation idempotency hashing and shot context hashing**

- [ ] **Step 4: Update create, generated-create, update, and read projections while keeping `source_excerpt` read-only in normal PATCH**

- [ ] **Step 5: Write idempotent precheck/add/verify SQL and update canonical schema**

- [ ] **Step 6: Run focused storyboard unit/API tests and available isolated integration tests**

- [ ] **Step 7: Commit with `feat: persist storyboard timing and source`**

### Task 4: Prepare narrative prompts and save enriched business results

**Files:**
- Modify: `backend/src/short_drama/service/generation_context_service.py`
- Modify: `backend/src/short_drama/service/generation_business_service.py`
- Modify: `backend/tests/unit/test_asset_extraction.py`
- Modify: `backend/tests/unit/test_generation_business.py`
- Modify: `backend/tests/integration/test_production_generation.py`

**Interfaces:**
- Asset task snapshot includes requested kinds and uses `script-assets-v1-r2`.
- Storyboard task snapshot includes `storyboard.average_shot_duration_ms` and uses `script-shots-v1-r2`.
- `GenerationBusinessService.save_text_result` passes frozen script content to `parse_storyboard_result`.
- `EpisodeStoryboardService.create_generated_locked` consumes enriched shots and persists only supported fields plus asset links.

- [ ] **Step 1: Add failing context tests asserting template revisions, prompt fragments, and frozen 3000/5000ms settings**

- [ ] **Step 2: Add failing business-save test for enriched storyboard result and enriched asset original/draft separation**

- [ ] **Step 3: Pass selected asset kinds to the prompt registry and write the storyboard options into the frozen snapshot/user envelope**

- [ ] **Step 4: Update business parsing and apply plumbing; ensure candidate-only `title` and `story_beat` remain in the task result**

- [ ] **Step 5: Run generation context, business, and production-generation tests**

- [ ] **Step 6: Commit with `feat: apply narrative prompt business rules`**

### Task 5: Expose narrative asset reasons in the review UI

**Files:**
- Modify: `frontend/src/api/modules/asset-extraction.ts`
- Modify: `frontend/src/api/types/generations.ts`
- Modify: `frontend/src/features/projects/ScriptAssetExtraction.tsx`
- Modify: `frontend/tests/asset-extraction.test.mjs`

**Interfaces:**
- Extends extraction originals with `importance: 'core' | 'continuity'` and `story_function: string`.
- Keeps adopted `AssetDraft` unchanged.
- Candidate cards render `推动剧情`/`维持连续性` and the narrative reason before description.

- [ ] **Step 1: Add a failing AST/source contract test for the two importance labels and story-function rendering**

- [ ] **Step 2: Extend API types without leaking review-only fields into asset PATCH/apply drafts**

- [ ] **Step 3: Render a compact importance tag and “剧情作用” block in each candidate card**

- [ ] **Step 4: Run `node --test tests/asset-extraction.test.mjs` and `npm.cmd run typecheck`**

- [ ] **Step 5: Commit with `feat: explain narrative asset selection`**

### Task 6: Add storyboard duration controls and enriched candidate review

**Files:**
- Modify: `frontend/src/api/modules/storyboard.ts`
- Modify: `frontend/src/api/types/generations.ts`
- Modify: `frontend/src/features/projects/workflow-contract.ts`
- Modify: `frontend/src/features/projects/StoryboardResultPreview.tsx`
- Modify: `frontend/src/pages/projects/episode/StoryboardStage.tsx`
- Modify: `frontend/tests/production-workflow.test.mjs`
- Modify: `frontend/src/app/web.css`

**Interfaces:**
- Changes: `scriptShotsRequest(..., instructions, averageShotDurationMs = 3000)` emits `storyboard.average_shot_duration_ms`.
- Extends `ShotRead` with `duration_ms` and `source_excerpt`.
- Extends generated shot result with title, source excerpt, story beat, duration, and assets.

- [ ] **Step 1: Add failing request-contract tests for default 3000ms and explicit 5000ms**

- [ ] **Step 2: Add failing UI contract tests for average-duration options and candidate total/average duration summaries**

- [ ] **Step 3: Extend TypeScript API types and request builder**

- [ ] **Step 4: Add duration selection with 2s, 3s default, 5s, and custom 1–10s controls; keep model and supplemental instructions**

- [ ] **Step 5: Render candidate title, duration, story beat, source excerpt, body, and asset names/IDs; preserve append/replace actions**

- [ ] **Step 6: Add editable shot duration to active shots and include it in serialized save requests**

- [ ] **Step 7: Add responsive styles using existing tokens and run focused Node tests plus typecheck**

- [ ] **Step 8: Commit with `feat: control and review storyboard timing`**

### Task 7: Full compatibility and release verification

**Files:**
- Modify only files required by failures attributable to Tasks 1–6.

**Interfaces:**
- Verifies existing v1 callers without `storyboard` still use 3000ms.
- Verifies old task results without enriched fields remain readable.
- Verifies prompt templates ship in the backend artifact.

- [ ] **Step 1: Run backend Ruff on changed Python files**

- [ ] **Step 2: Run the full backend unit/API suite available without external MySQL/MinIO**

- [ ] **Step 3: Run migration verification against the configured isolated test database when available; otherwise report it as not executed**

- [ ] **Step 4: Run all frontend Node tests**

- [ ] **Step 5: Run `npm.cmd run build` and confirm TypeScript and Vite complete successfully**

- [ ] **Step 6: Run `git diff --check`, inspect the final diff, and confirm no generated build output or secrets are staged**

- [ ] **Step 7: Commit verification-only fixes, if any, with `fix: complete narrative generation verification`**
