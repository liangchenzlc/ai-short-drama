"""Immutable candidate creation inside the coordinator's locked tool transaction."""

import re
from copy import deepcopy

from sqlalchemy import select

from short_drama.agent.core.events import append_event
from short_drama.agent.core.state import wait_locked
from short_drama.agent.legacy.authorization import check_source, scoped_task, source_snapshot
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.base import BaseDAO
from short_drama.dao.project_writing_dao import ProjectWritingDAO
from short_drama.domain import (
    AgentArtifact,
    Asset,
    Project,
    ProjectScript,
    ShotScript,
)
from short_drama.schemas.agent_artifacts import CandidateAssetPatch, CandidateShotPatch
from short_drama.service.asset_service import AssetService
from short_drama.service.base import utcnow


def public_content(value, secret):
    """Redact only shared values; never rewrite protocol/field names or private intent."""
    if isinstance(value, str):
        secrets = [secret] if isinstance(secret, str) else secret
        pattern = "|".join(
            re.escape(item) for item in sorted(set(filter(None, secrets)), key=len, reverse=True)
        )
        return re.sub(pattern, "[redacted]", value) if pattern else value
    if isinstance(value, list):
        return [public_content(item, secret) for item in value]
    if isinstance(value, dict):
        return {key: public_content(item, secret) for key, item in value.items()}
    return value


def candidate_secret(run, settings):
    from short_drama.ai.canvas_credentials import CanvasCredentials
    from short_drama.service.agent_model_service import model_credentials

    credential = model_credentials(run.config_snapshot, getattr(settings, "encryption_key", None))
    if not isinstance(credential, CanvasCredentials):
        return credential
    return [credential.api_key.get_secret_value()] + [
        header.value.get_secret_value() for header in credential.headers
    ]


def asset_in_creation(session, project_id, asset_id):
    asset = session.scalar(
        select(Asset)
        .where(
            Asset.id == asset_id,
            Asset.deleted_at.is_(None),
            Asset.project_id == project_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if asset is None:
        raise NotFound("Asset is outside this creation")
    return asset


def candidate_patch(kind, patch, *, version=None):
    if kind == "asset_patch":
        if {"row_version"} & set(patch):
            raise ValueError("Authorization is not a proposed field")
        return CandidateAssetPatch.model_validate({**patch, "row_version": version}).model_dump(
            exclude_unset=True, exclude={"row_version"}
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
    from short_drama.service.agent_conversation_service import validate_conversation_subject

    if _pending_parent(session, run) is not None:
        raise WorkflowError("agent_review_required", "请先审核或修订当前计划", 409)
    validate_conversation_subject(session, conversation, lock=True)
    scoped_task(
        conversation,
        {
            key: step[key]
            for key in (
                "kind",
                "target_id",
                "instructions",
                "model_config_id",
                "count",
                "parameters",
            )
        },
    )
    pending = [
        item
        for item in authorization.get("steps", [])
        if item["id"] not in authorization.get("consumed_steps", [])
    ]
    if not pending or pending[0]["id"] != args.step_id:
        raise WorkflowError("agent_step_not_authorized", "Execute the approved steps in order", 409)
    if step["kind"] in {"extract", "storyboard", "image", "video"}:
        from short_drama.agent.native.tasks import admit_native_locked

        return admit_native_locked(session, conversation, run, tool, step, settings=settings)
    if step["kind"] not in {"novel", "script", "asset_patch", "shot_patch"}:
        raise ValueError("Unsupported candidate kind")
    check_source(session, step)
    creation = session.scalar(
        select(Project)
        .where(Project.id == conversation.project_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if creation is None:
        raise NotFound("Project does not exist")
    now, secret = utcnow(), candidate_secret(run, settings)
    values = {
        "project_id": conversation.project_id,
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
        values["parent_script_id"] = creation.editing_script_id
        if step["kind"] == "novel":
            values["source_content"] = content
        else:
            values["source_content"] = content
            script = BaseDAO(session, ProjectScript).create(
                {
                    "project_id": creation.id,
                    "position": ProjectWritingDAO(session).next_position(creation.id),
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
            target = asset_in_creation(session, creation.id, int(step["target_id"]))
            AssetService(session)._validate_update(target, patch)
            values["target_asset_id"] = target.id
        else:
            target = session.scalar(
                select(ShotScript)
                .where(
                    ShotScript.id == int(step["target_id"]),
                    ShotScript.project_id == creation.id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if target is None:
                raise NotFound("Shot is outside this creation")
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


# ── merged from artifact_sources.py ──

def _pending_parent(session, run):
    from short_drama.domain.agent import AgentRun

    # Earlier queued supplements may still point at a plan replaced by a prior
    # supplement. Always follow the current pending plan in this conversation.
    query = select(AgentRun).where(
        AgentRun.conversation_id == run.conversation_id,
        AgentRun.id < run.id,
        AgentRun.status == "waiting_review",
    )
    parents = session.scalars(query.order_by(AgentRun.id.desc()).with_for_update()).all()
    return next(
        (parent for parent in parents if not (parent.checkpoint or {}).get("awaiting_artifacts")),
        None,
    )
