# Episode writing persistence implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Persist episode novels and scripts through the existing editor with safe autosave and explicit confirmation.

**Architecture:** Reuse episode_novels and episode_scripts; episodes owns editing_script_id and content_version. Scoped API → writing service → DAO, short transactions locking the parent episode. Frontend uses a serial queue per mounted episode; backend is authoritative for novel/script, other production stages remain local demonstrations.

**Tech Stack:** FastAPI, SQLAlchemy/PyMySQL, React/TypeScript/Axios, existing pytest and Node test tooling.

**Spec:** User-approved interface design in this conversation, reproduced below as the binding contract.

## Constraints and contract

- Five routes under `/api/v1/projects/{project_id}/episodes/{episode_id}`: GET writing, PUT novel, PUT script, PUT editing-script, POST scripts/{script_id}/confirm.
- GET response: `{episode_id, content_version, novel: {id,content,updated_at}|null, editing_script: {id,content,state,updated_at}|null, confirmed_script_id: string|null}`.
- All mutations accept `content_version` as a positive uint64 decimal string. Novel also requires content; script requires content and script_id (nullable, null means expect no editing script); selection requires script_id.
- Novel response `{content_version,novel}`. Script response `{content_version,script}`. Selection/confirmation return the full writing response. All response IDs/version strings; UTC timestamps with Z.
- State only unconfirmed/confirmed. At most one confirmed script; edits to a confirmed script reset that script. Novel changes do not change script content or confirmation. Confirmation requires nonblank content and current editing script.
- Explicit same-project/episode scope, 404 on foreign IDs, 409 on version/selection conflict, 422 invalid input/blank confirmation, 503 database failure. UTF-8 content max 1 MiB, whitespace preserved, empty permitted, null forbidden.
- Lock episode first for all participating writes; version mismatch fails before changes; no-op does not increment. First writes create via Snowflake; GET never creates rows. Increment version only with actual changes, prevent uint64 overflow.
- Add only nullable editing_script_id (service-enforced reference to avoid circular FK) and content_version default 1 to episodes. Migration initializes pointer to confirmed script, else lowest position; preserves every existing script.
- Legacy novel/script services and generation entry points must bump the episode writing version on relevant writes, clear pointers on deletion, and preserve consistent locking. Document migration prerequisite before runtime restart.
- No real AI calls or fake candidates stored as real scripts. Model selections remain local until generation integration. Local workflow remains for unrelated demonstration stages; show its distinction in UI.
- Autosave after 1s idle, novel/script serialized with freshest version, coalesce edits made during requests. Never overwrite newer editor text with old responses. Errors pause queue and preserve draft; uncertain result reads server and only acknowledges if version/content are consistent. 409 requires explicit conflict resolution.
- Flush on stage/candidate switch and confirmation, warn on page unload, guard internal navigation. Legacy browser content is offered for explicit import/preview, never silently sent to server. Loading error must not show editable blank data.

## Task 1: backend contract, migration and consistency

Files: domain/episode.py; schemas/episode_writing.py; dao/episode_writing_dao.py; service/episode_writing_service.py; api/v1/episode_writing.py; api/dependencies.py and router.py; existing novel/script services; canonical schema and new migration directory; tests/unit/test_episode_writing.py and integration/test_episode_writing.py.

- [x] Write behavior tests against the service with generation_session: empty GET creates no rows; saving preserves whitespace; current script creation is unique; stale version rejected; no-op stable; confirmation resets previous and edit resets confirmation; foreign scope rejected. Example assertion: `assert service.get(p.id,e.id)['novel'] is None` then save version '1', assert version '2' and preserved content.
- [x] Run `.venv/Scripts/python.exe -m pytest tests/unit/test_episode_writing.py -q` and observe missing service failure.
- [x] Implement DAO selectors under episode lock, strict schemas, service transactions, routes and numeric/string serialization. Test all five paths and failure statuses through ASGI.
- [x] Add migration and canonical DDL fields, deterministic pointer backfill, read-only pre/post checks. Cover MySQL transaction race and migration behavior in disposable test DB.
- [x] Adapt internal write paths to participate in versioning; run relevant unit/API regression and isolated MySQL tests.

## Task 2: frontend API and editor persistence

Files: api/modules/episode-writing.ts; features/projects/writing-session.ts and useEpisodeWriting.ts; EpisodePage.tsx, SourceStage.tsx, ScriptStage.tsx; small WritingStatus component if needed; frontend/tests/writing-session.test.mjs.

- [x] Write tests before implementation for serial saves, edits during inflight requests, 409 pause, timeout reconciliation, same-ID confirmation, initial null script, and disposal/navigation barriers. Use deferred Promise transport and assert saved payload/version and preserved draft rather than internal calls alone.
- [x] Implement typed API and independently testable queue; hook exposes loading/error/draft/state, edit, flush, retry, reload, confirm and explicit legacy import.
- [x] Integrate editor: no sampleScript/sampleAssets actions in persisted text stages; leave future AI buttons clearly disabled; confirmation separate from analysis. Do not write restored browser content to backend implicitly.
- [x] Run Node tests and `npm run build`; inspect empty/error/conflict/unsaved states, no newer text overwrite, safe episode switch.

## Task 3: integration, review and running application

- [x] Review backend/frontend contract together and fix regressions; run backend unit/API, targeted MySQL tests and frontend tests/build.
- [x] Apply additive migration to configured application DB after read-only schema inspection and local metadata backup; no model calls, credential output or destructive migration. Restart affected backend with existing script.
- [x] Exercise API CRUD/confirmation/conflict using a clearly marked temporary project and remove only owned temporary rows. Verify frontend proxy and browser interactions if browser tooling is available.
- [x] Document endpoints, migration order and test results. Keep changes on feature branch; no unsolicited GitHub push.

## Execution decisions

- Work in a feature branch in the existing clean checkout so the running development environment and ignored credentials continue to work; no duplicate dependency installation.
- Backend and frontend can be implemented independently against the above fixed contract. Parent owns backend and deployment; delegated frontend changes are restricted to frontend files. Final review checks both together.

## Completion

All tasks completed. See `2026-09-21-episode-writing-verification.md` for test evidence, deployed migration and scoped review fixes. No GitHub push performed.
