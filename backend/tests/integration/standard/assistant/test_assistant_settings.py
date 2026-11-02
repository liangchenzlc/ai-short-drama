"""Real MySQL CAS and Task/Run setting boundaries; no provider calls."""

import pytest
from sqlalchemy import select

from short_drama.core.exceptions import Conflict, WorkflowError
from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.domain.assistant.tasks.task import AgentTask
from short_drama.service.assistant.tasks.policy import AssistantPolicyService
from short_drama.service.base import utcnow
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    harness_workspace as harness_workspace,
)
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    task_service,
)

pytestmark = pytest.mark.integration


def update(session, settings, **changes):
    from short_drama.service.assistant.settings.preferences import AssistantSettingsService

    service = AssistantSettingsService(session, settings)
    before = service.get_settings()
    values = before.model_dump(exclude={"default_instructions", "deployment_limits"})
    return service.update_settings({**values, **changes})


def test_settings_cas_is_complete_and_independent_from_live_media_policy(harness_workspace):
    from short_drama.service.assistant.settings.preferences import AssistantSettingsService

    factory, settings, _model = harness_workspace
    with factory() as session:
        task_service(session, settings)
        service = AssistantSettingsService(session, settings)
        initial = service.get_settings()
        assert initial.settings_version == 0
        saved = update(
            session, settings, custom_instructions="按保存成果交付", model_call_limit=120
        )
        assert saved.settings_version == 1
        with pytest.raises(Conflict):
            update(session, settings, settings_version=0, custom_instructions="过期覆盖")
        policy = AssistantPolicyService(session).update_generation_policy(
            {"media_generation_enabled": False, "policy_version": 0}
        )
        assert policy.policy_version == 1
        current = service.get_settings()
        assert current.settings_version == 1
        assert current.custom_instructions == "按保存成果交付"
        restored = update(session, settings, custom_instructions=None)
        assert restored.settings_version == 2
        assert AssistantPolicyService(session).get_generation_policy().policy_version == 1


def test_deployment_cap_is_reported_and_excess_rejected(harness_workspace):
    from short_drama.service.assistant.settings.preferences import AssistantSettingsService

    factory, settings, _model = harness_workspace
    settings.agent_run_decision_cap = 100
    settings.agent_run_output_token_cap = 500_000
    with factory() as session:
        task_service(session, settings)
        service = AssistantSettingsService(session, settings)
        current = service.get_settings()
        assert current.model_call_limit == current.deployment_limits.model_call_limit == 100
        with pytest.raises(WorkflowError) as failure:
            update(session, settings, model_call_limit=101)
        assert failure.value.status_code == 422
        assert service.get_settings().settings_version == 0


@pytest.mark.parametrize(
    ("specified", "expected"),
    [
        (None, {"images": 5, "videos": 2, "audio": 1}),
        ({"images": 3}, {"images": 3, "videos": None, "audio": None}),
        ({"images": None}, {"images": None, "videos": None, "audio": None}),
        ({"images": 0}, {"images": 0, "videos": None, "audio": None}),
    ],
)
def test_task_quantity_inheritance_is_whole_object(harness_workspace, specified, expected):
    factory, settings, model = harness_workspace
    with factory() as session:
        service = task_service(session, settings)
        update(session, settings, default_media_limits={"images": 5, "videos": 2, "audio": 1})
        conversation = service.create_global_conversation({}, "settings-conversation")
        payload = {"content": "讨论节奏", "model_config_id": model}
        if specified is not None:
            payload["media_limits"] = specified
        accepted = service.send_task_message(conversation.id, payload, "settings-task")
        assert accepted.task.media_limits.model_dump() == expected


def test_new_run_refreshes_preferences_but_keeps_task_quantity(harness_workspace):
    factory, settings, model = harness_workspace
    with factory() as session:
        service = task_service(session, settings)
        update(session, settings, custom_instructions="旧指令", default_media_limits={"images": 4})
        conversation = service.create_global_conversation({}, "freeze-conversation")
        accepted = service.send_task_message(
            conversation.id, {"content": "讨论节奏", "model_config_id": model}, "first"
        )
        run = session.get(AgentRun, accepted.run.id)
        old = dict(run.config_snapshot["assistant_settings"])
        assert old["instructions"] == "旧指令"
        assert "旧指令" in old["system_prompt"]
        assert old["model_call_limit"] == 240
        run.status, run.finished_at = "cancelled", utcnow()
        task = session.get(AgentTask, accepted.task.id)
        task.status, task.phase = "stopped", "terminal"
        session.commit()
        update(
            session,
            settings,
            custom_instructions="新指令",
            model_call_limit=100,
            output_token_limit=500_000,
            subagent_max_concurrency=1,
            default_media_limits={"images": 9},
        )
        again = service.send_task_message(conversation.id, {"content": "继续讨论"}, "second")
        assert again.task.id == accepted.task.id
        assert again.task.media_limits.images == 4
        new_run = session.get(AgentRun, again.run.id)
        frozen = new_run.config_snapshot["assistant_settings"]
        assert frozen["instructions"] == "新指令"
        assert "新指令" in frozen["system_prompt"]
        assert frozen["model_call_limit"] == new_run.budget["decision_calls"] == 100
        assert frozen["output_token_limit"] == new_run.budget["output_tokens"] == 500_000
        assert frozen["subagent_max_concurrency"] == 1
        assert frozen["prompt_versions"]
        assert frozen["tool_catalog_version"]
        session.commit()
        previous = session.scalar(select(AgentRun).where(AgentRun.id == accepted.run.id))
        assert previous.config_snapshot["assistant_settings"] == old
        session.commit()
        with pytest.raises(WorkflowError):
            service.send_task_message(
                conversation.id, {"content": "改旧任务数量", "media_limits": {"images": 8}}, "third"
            )


def test_capacity_patch_preserves_omitted_value_and_can_clear_declaration(harness_workspace):
    from short_drama.service.ai_config.management.assistant_models import AgentModelService
    from tests.integration.standard.assistant.test_global_assistant_tasks import task_actor

    factory, settings, model = harness_workspace
    with factory() as session:
        session.info["actor"] = task_actor()
        service = AgentModelService(session, settings)
        first = service.patch_inputs(
            model,
            {"row_version": "1", "image": True, "audio": False, "context_window_tokens": 131072},
        )
        assert first.input_capabilities.context_window_tokens == 131072
        assert first.input_capabilities.context_window_evidence == "declared"
        second = service.patch_inputs(
            model, {"row_version": first.row_version, "image": True, "audio": False}
        )
        assert second.input_capabilities.context_window_tokens == 131072
        cleared = service.patch_inputs(
            model,
            {
                "row_version": second.row_version,
                "image": True,
                "audio": False,
                "context_window_tokens": None,
            },
        )
        assert cleared.input_capabilities.context_window_tokens == 65536
        assert cleared.input_capabilities.context_window_evidence == "platform_default"
