"""Review and atomically adopt extraction results into an episode's library."""

import hashlib
import json
import unicodedata
from copy import deepcopy

from sqlalchemy import select

from short_drama.core.exceptions import WorkflowError
from short_drama.domain import AIGenerationRecord, Asset, AsyncTask, EpisodeScript
from short_drama.schemas.asset_extraction import ExtractionApply, ExtractionDraft, ExtractionPatch
from short_drama.schemas.base import parse_identifier

from .asset_library_service import AssetLibraryService, creation_fingerprint
from .base import BaseService, utcnow
from .generation_context_service import content_hash


def normalized(value):
    return unicodedata.normalize("NFKC", value).strip()


def candidate_key(item):
    draft = item["draft"]
    return (draft["kind"], normalized(draft["name"]), normalized(draft["scene_time"]))


class AssetExtractionService(BaseService):
    model = AsyncTask

    def _load(self, project_id, episode_id, generation_id):
        project_id, episode_id, generation_id = map(
            parse_identifier, (project_id, episode_id, generation_id)
        )
        task = self._require(AsyncTask, generation_id)
        library = AssetLibraryService(self.session)
        episode = library._lock_scope("episode", episode_id, project_id)
        record = self.session.scalar(
            select(AIGenerationRecord)
            .where(AIGenerationRecord.task_id == generation_id)
            .order_by(AIGenerationRecord.call_no.desc())
            .limit(1)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        source = (record.request_data.get("source") or {}) if record else {}
        if (
            source.get("scene") != "script_assets"
            or str(source.get("project_id")) != str(project_id)
            or str(source.get("episode_id")) != str(episode_id)
        ):
            raise WorkflowError("not_found", "此提取结果不属于当前分集", 404)
        result = deepcopy((record.response_data or {}).get("business_result") or {})
        if task.status != "succeeded" or result.get("kind") != "script_assets":
            raise WorkflowError("result_not_ready", "素材提取结果尚不可用")
        return library, episode, record, result

    def _stale(self, episode, record):
        script_id = int(record.request_data["source"]["script_id"])
        script = self.session.scalar(
            select(EpisodeScript)
            .where(EpisodeScript.id == script_id, EpisodeScript.episode_id == episode.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return (
            script is None
            or episode.editing_script_id != script_id
            or script.state != "confirmed"
            or content_hash(script.content)
            != record.request_data["source_snapshot"]["content_hash"]
        )

    def _available(self, library, episode):
        found = {}
        for scope, parent_id in (("episode", episode.id), ("project", episode.project_id)):
            model, parent_field = library.library.scopes[scope]
            rows = self.session.scalars(
                select(Asset)
                .join(model, model.asset_id == Asset.id)
                .where(getattr(model, parent_field) == parent_id)
                .order_by(Asset.id)
            )
            for asset in rows:
                found.setdefault(asset.id, (asset, scope))
        return found

    def _matches(self, candidate, available):
        draft = candidate["draft"]
        names = {normalized(draft["name"]), *map(normalized, candidate["original"]["aliases"])}
        matches = []
        for asset, scope in available.values():
            if asset.kind != draft["kind"] or normalized(asset.name) not in names:
                continue
            if asset.kind == "scene" and normalized(asset.scene_time) != normalized(
                draft["scene_time"]
            ):
                continue
            matches.append(
                {
                    "asset_id": str(asset.id),
                    "name": asset.name,
                    "scope": scope,
                    "row_version": str(asset.row_version),
                    "description": asset.description,
                    "exact": normalized(asset.name) == normalized(draft["name"]),
                }
            )
        return matches

    def _view(self, library, episode, record, result):
        available = self._available(library, episode)
        return {
            "generation_id": str(record.task_id),
            "result_version": result["result_version"],
            "content_version": str(episode.content_version),
            "stale": self._stale(episode, record),
            "kinds": record.request_data["source_snapshot"]["extraction"]["kinds"],
            "items": [
                {
                    **item,
                    "original": {
                        key: value for key, value in item["original"].items() if key != "evidence"
                    },
                    "matches": self._matches(item, available),
                    "duplicate_candidates": [
                        other["candidate_id"]
                        for other in result["items"]
                        if other["candidate_id"] != item["candidate_id"]
                        and not other["applied"]
                        and candidate_key(other) == candidate_key(item)
                    ],
                }
                for item in result["items"]
            ],
        }

    def _persist(self, record, result):
        result["result_version"] = str(int(result["result_version"]) + 1)
        record.response_data = {**deepcopy(record.response_data), "business_result": result}
        record.updated_at = utcnow()
        self.session.flush()

    @staticmethod
    def _version(result, expected):
        if int(result["result_version"]) != expected:
            raise WorkflowError(
                "result_version_conflict", "提取候选已在其他页面更新，请重新载入后核对"
            )

    def get(self, project_id, episode_id, generation_id):
        with self._transaction():
            return self._view(*self._load(project_id, episode_id, generation_id))

    def patch(self, project_id, episode_id, generation_id, payload):
        parsed = ExtractionPatch.model_validate(payload)
        with self._transaction():
            library, episode, record, result = self._load(project_id, episode_id, generation_id)
            self._version(result, parsed.result_version)
            by_id = {item["candidate_id"]: item for item in result["items"]}
            for edit in parsed.items:
                item = by_id.get(edit.candidate_id)
                if item is None or item["applied"]:
                    raise WorkflowError("candidate_unavailable", "候选不存在或已经采用")
                item["draft"] = edit.draft.model_dump(mode="json")
            self._persist(record, result)
            return self._view(library, episode, record, result)

    def apply(self, project_id, episode_id, generation_id, payload, idempotency_key):
        from .ai_generation_service import AIGenerationService

        parsed = ExtractionApply.model_validate(payload)
        key = AIGenerationService._key(idempotency_key)
        digest = hashlib.sha256(
            json.dumps(parsed.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        with self._transaction():
            library, episode, record, result = self._load(project_id, episode_id, generation_id)
            receipt = result["receipts"].get(key)
            if receipt:
                if receipt["hash"] != digest:
                    raise WorkflowError("idempotency_conflict", "此操作标识已用于另一批采用请求")
                return {**receipt["response"], "already_applied": True}
            self._version(result, parsed.result_version)
            if episode.content_version != parsed.content_version:
                raise WorkflowError("writing_version_conflict", "正文版本已变化，请刷新候选后重试")
            if self._stale(episode, record):
                raise WorkflowError("source_changed", "剧本已变化，请根据当前已确认剧本重新提取")
            by_id = {item["candidate_id"]: item for item in result["items"]}
            # Stable lock order for batches reusing multiple shared assets.
            reuse_ids = sorted(
                {item.asset_id for item in parsed.items if item.asset_id is not None}
            )
            locked = (
                {
                    asset.id: asset
                    for asset in self.session.scalars(
                        select(Asset)
                        .where(Asset.id.in_(reuse_ids))
                        .order_by(Asset.id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                }
                if reuse_ids
                else {}
            )
            available = self._available(library, episode)
            mappings = []
            created = reused = 0
            for adoption in parsed.items:
                item = by_id.get(adoption.candidate_id)
                if item is None:
                    raise WorkflowError("candidate_unavailable", "候选不存在")
                if item["applied"]:
                    previous = item["applied"]
                    if previous["action"] != adoption.action or (
                        adoption.action == "reuse"
                        and previous["asset_id"] != str(adoption.asset_id)
                    ):
                        raise WorkflowError(
                            "candidate_already_applied", "候选已采用，不能更换采用方式"
                        )
                    mappings.append({"candidate_id": adoption.candidate_id, **previous})
                    continue
                draft = ExtractionDraft.model_validate(item["draft"])
                if adoption.action == "reuse":
                    asset = locked.get(adoption.asset_id)
                    if asset is None or asset.id not in available or asset.kind != draft.kind:
                        raise WorkflowError(
                            "invalid_asset_reference", "此素材不能在当前分集复用", 422
                        )
                    if asset.row_version != adoption.expected_row_version:
                        raise WorkflowError(
                            "asset_version_conflict", "已有素材已变化，请刷新后核对"
                        )
                    library.link_locked("episode", episode, asset.id)
                    reused += 1
                else:
                    if self._matches(item, available) and not adoption.confirm_duplicate:
                        raise WorkflowError(
                            "duplicate_review_required", "存在同名或别名素材，请核对后明确另建"
                        )
                    asset, _ = library.create_locked(
                        "episode",
                        episode.id,
                        draft,
                        f"extraction:{record.task_id}:{adoption.candidate_id}",
                        creation_fingerprint("episode", episode.id, draft),
                        model_id=record.config_id,
                    )
                    available[asset.id] = (asset, "episode")
                    created += 1
                item["applied"] = {
                    "asset_id": str(asset.id),
                    "action": adoption.action,
                    "applied_at": utcnow().isoformat() + "Z",
                }
                mappings.append({"candidate_id": adoption.candidate_id, **item["applied"]})
            response = {
                "generation_id": str(record.task_id),
                "created": created,
                "reused": reused,
                "items": mappings,
                "already_applied": False,
            }
            result["receipts"][key] = {"hash": digest, "response": response}
            self._persist(record, result)
            return response
