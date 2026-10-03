"""Immutable candidate creation inside the coordinator's locked tool transaction."""

from copy import deepcopy

from sqlalchemy import select

from short_drama.agent.authorization import check_source
from short_drama.agent.state import append_event, wait_locked
from short_drama.core.crypto import KeyCipher
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.base import BaseDAO
from short_drama.dao.episode_writing_dao import EpisodeWritingDAO
from short_drama.domain import (
    AgentArtifact,
    Asset,
    Episode,
    EpisodeAsset,
    EpisodeScript,
    ShotScript,
)
from short_drama.schemas.agent_artifacts import CandidateAssetPatch, CandidateShotPatch
from short_drama.service.asset_service import AssetService
from short_drama.service.base import utcnow


def public_content(value, secret):
    """Redact only shared values; never rewrite protocol/field names or private intent."""
    if isinstance(value, str):
        return value.replace(secret, "[redacted]") if secret else value
    if isinstance(value, list):
        return [public_content(item, secret) for item in value]
    if isinstance(value, dict):
        return {key: public_content(item, secret) for key, item in value.items()}
    return value


def candidate_secret(run, settings):
    cipher = run.config_snapshot.get("credential_cipher")
    if not cipher:
        return ""
    key = settings.encryption_key
    return KeyCipher(key.get_secret_value() if hasattr(key, "get_secret_value") else key).decrypt(
        cipher
    )


def source_snapshot(step, run, now):
    source = step["source"]
    return {
        "episode_id": str(source["episode_id"]),
        "content_version": source["content_version"],
        "storyboard_version": source["storyboard_version"],
        "episode_row_version": source["episode_row_version"],
        "target_kind": step["target_kind"],
        "target_id": str(step["target_id"]),
        "target_row_version": source.get("target_row_version"),
        "model_name": step.get("model_name") or run.config_snapshot.get("name"),
        "generated_at": now.isoformat(),
    }


def asset_in_episode(session, project_id, episode_id, asset_id):
    asset = session.scalar(
        select(Asset)
        .join(EpisodeAsset, EpisodeAsset.asset_id == Asset.id)
        .where(
            Asset.id == asset_id,
            Asset.project_id == project_id,
            EpisodeAsset.episode_id == episode_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if asset is None:
        raise NotFound("Asset is outside this episode")
    return asset


def candidate_patch(kind, patch, *, version=None):
    if kind == "asset_patch":
        if {"row_version", "confirm_shared"} & set(patch):
            raise ValueError("Authorization is not a proposed field")
        return CandidateAssetPatch.model_validate({**patch, "row_version": version}).model_dump(
            exclude_unset=True, exclude={"row_version", "confirm_shared"}
        )
    return CandidateShotPatch.model_validate(patch).model_dump(exclude_unset=True)


def create_candidate_locked(session, conversation, run, tool, args, *, settings):
    """Tool effect and result are committed together by execute_tools; no nested commit."""
    existing = session.scalar(
        select(AgentArtifact)
        .where(AgentArtifact.tool_call_id == tool.id, AgentArtifact.result_index == 1)
        .with_for_update()
    )
    if existing is not None:
        return {"artifact_id": str(existing.id), "kind": existing.kind}
    authorization = run.checkpoint["authorization"]
    if (
        not settings.agent_enabled
        or authorization["mode"] not in {"single", "workflow"}
        or authorization["mode"] == "workflow"
        and not authorization.get("approved_plan")
        or args.step_id in authorization.get("consumed_steps", [])
    ):
        raise WorkflowError("agent_step_not_authorized", "Task step is not authorized", 409)
    step = next((s for s in authorization.get("steps", []) if s["id"] == args.step_id), None)
    if step is None:
        raise WorkflowError("agent_step_not_authorized", "Task step is not authorized", 409)
    pending = [
        item
        for item in authorization.get("steps", [])
        if item["id"] not in authorization.get("consumed_steps", [])
    ]
    if not pending or pending[0]["id"] != args.step_id:
        raise WorkflowError("agent_step_not_authorized", "Execute the approved steps in order", 409)
    if step["kind"] in {"extract", "storyboard", "image", "video"}:
        from short_drama.agent.native_tasks import admit_native_locked

        return admit_native_locked(session, conversation, run, tool, step, settings=settings)
    if step["kind"] not in {"novel", "script", "asset_patch", "shot_patch"}:
        raise ValueError("Unsupported candidate kind")
    check_source(session, step)
    episode = session.scalar(
        select(Episode)
        .where(Episode.id == conversation.episode_id, Episode.project_id == conversation.project_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if episode is None:
        raise NotFound("Episode does not exist")
    now, secret = utcnow(), candidate_secret(run, settings)
    values = {
        "project_id": conversation.project_id,
        "episode_id": conversation.episode_id,
        "tool_call_id": tool.id,
        "result_index": 1,
        "kind": {"novel": "novel_proposal", "script": "script_candidate"}.get(
            step["kind"], step["kind"]
        ),
        "source_snapshot": source_snapshot(step, run, now),
        "created_by": run.initiated_by,
        "created_at": now,
        "updated_at": now,
    }
    if step["kind"] in {"novel", "script"}:
        content = public_content(args.content, secret)
        if not content.strip() or args.patch or len(content.encode("utf-8")) > 1048576:
            raise ValueError("Provide only a nonblank candidate body within the size limit")
        values["parent_script_id"] = episode.editing_script_id
        if step["kind"] == "novel":
            values["source_content"] = content
        else:
            script = BaseDAO(session, EpisodeScript).create(
                {
                    "episode_id": episode.id,
                    "position": EpisodeWritingDAO(session).next_position(episode.id),
                    "state": "unconfirmed",
                    "content": content,
                    "created_at": now,
                    "updated_at": now,
                    "created_by": run.initiated_by,
                    "updated_by": run.initiated_by,
                }
            )
            values["script_id"] = script.id
    else:
        if args.content:
            raise ValueError("A patch cannot include a candidate body")
        patch = candidate_patch(
            step["kind"],
            public_content(args.patch, secret),
            version=step["source"]["target_row_version"],
        )
        if step["kind"] == "asset_patch":
            target = asset_in_episode(
                session, conversation.project_id, episode.id, int(step["target_id"])
            )
            AssetService(session)._validate_update(target, patch)
            values["target_asset_id"] = target.id
        else:
            target = session.scalar(
                select(ShotScript)
                .where(
                    ShotScript.id == int(step["target_id"]),
                    ShotScript.episode_id == episode.id,
                    ShotScript.deleted_at.is_(None),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if target is None:
                raise NotFound("Shot is outside this episode")
            values["target_shot_id"] = target.id
        values["proposed_patch"] = patch
        values["metadata_json"] = {
            "diff": [
                {
                    "field": key,
                    "before": public_content(getattr(target, key), secret),
                    "after": value,
                }
                for key, value in patch.items()
            ]
        }
    artifact = BaseDAO(session, AgentArtifact).create(values)
    checkpoint = deepcopy(run.checkpoint)
    checkpoint["authorization"]["consumed_steps"] = [
        *authorization.get("consumed_steps", []),
        args.step_id,
    ]
    if authorization["mode"] == "workflow":
        checkpoint["awaiting_artifacts"] = [str(artifact.id)]
        checkpoint["awaiting_step_id"] = args.step_id
    run.checkpoint = checkpoint
    append_event(
        session,
        conversation,
        "artifact.ready",
        {"artifact_id": str(artifact.id), "kind": artifact.kind},
        run_id=run.id,
    )
    if authorization["mode"] == "workflow":
        wait_locked(session, conversation, run, payload={"artifact_id": str(artifact.id)})
    return {"artifact_id": str(artifact.id), "kind": artifact.kind}
