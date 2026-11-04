"""Real MySQL account settings CAS and immutable Run snapshots; no provider calls."""

import pytest
from sqlalchemy import select

from short_drama.core.exceptions import Conflict
from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.domain.assistant.tasks.task import AgentTask
from short_drama.service.assistant.settings.preferences import AssistantSettingsService
from short_drama.service.base import utcnow
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    harness_workspace as harness_workspace,
)
from tests.integration.standard.assistant.test_global_assistant_tasks import task_service

pytestmark = pytest.mark.integration


def update(session, settings, **changes):
    service = AssistantSettingsService(session, settings)
    version = service.get_settings().settings_version
    return service.update_settings({"settings_version": version, **changes})


def test_settings_share_cas_and_partial_edits_preserve_other_fields(harness_workspace):
    factory, settings, _model = harness_workspace
    with factory() as session:
        task_service(session, settings)
        service = AssistantSettingsService(session, settings)
        assert service.get_settings().settings_version == 0
        saved = update(session, settings, custom_instructions="按保存成果交付")
        assert saved.settings_version == 1
        with pytest.raises(Conflict):
            update(session, settings, settings_version=0, custom_instructions="过期覆盖")
        disabled = update(session, settings, media_generation_enabled=False)
        assert disabled.settings_version == 2
        assert disabled.custom_instructions == "按保存成果交付"
        assert disabled.workbench_enabled and disabled.long_term_memory_enabled
        restored = update(session, settings, custom_instructions=None)
        assert restored.settings_version == 3
        assert restored.custom_instructions is None and not restored.media_generation_enabled


def test_public_settings_exclude_deployment_budgets_and_task_media_quotas(harness_workspace):
    factory, settings, model = harness_workspace
    with factory() as session:
        service = task_service(session, settings)
        current = AssistantSettingsService(session, settings).get_settings()
        assert (
            not {
                "model_call_limit",
                "output_token_limit",
                "subagent_max_concurrency",
                "deployment_limits",
                "default_media_limits",
            }
            & type(current).model_fields.keys()
        )
        conversation = service.create_global_conversation({}, "settings-conversation")
        accepted = service.send_task_message(
            conversation.id, {"content": "讨论节奏", "model_config_id": model}, "settings-task"
        )
        assert "media_limits" not in type(accepted.task).model_fields
        run = session.get(AgentRun, accepted.run.id)
        frozen = run.config_snapshot["assistant_settings"]
        assert "model_call_limit" not in frozen and "output_token_limit" not in frozen
        assert run.budget["decision_calls"] is None and run.budget["output_tokens"] is None
        assert frozen["subagent_max_concurrency"] == 3


def test_new_run_refreshes_preferences_and_keeps_the_previous_snapshot(harness_workspace):
    factory, settings, model = harness_workspace
    with factory() as session:
        service = task_service(session, settings)
        update(session, settings, custom_instructions="旧指令")
        conversation = service.create_global_conversation({}, "freeze-conversation")
        accepted = service.send_task_message(
            conversation.id, {"content": "讨论节奏", "model_config_id": model}, "first"
        )
        run = session.get(AgentRun, accepted.run.id)
        old = dict(run.config_snapshot["assistant_settings"])
        assert old["instructions"] == "旧指令" and "旧指令" in old["system_prompt"]
        assert "model_call_limit" not in old and "output_token_limit" not in old
        run.status, run.finished_at = "cancelled", utcnow()
        task = session.get(AgentTask, accepted.task.id)
        task.status, task.phase = "stopped", "terminal"
        session.commit()
        update(session, settings, custom_instructions="新指令")
        again = service.send_task_message(conversation.id, {"content": "继续讨论"}, "second")
        assert again.task.id == accepted.task.id
        new_run = session.get(AgentRun, again.run.id)
        frozen = new_run.config_snapshot["assistant_settings"]
        assert frozen["instructions"] == "新指令" and "新指令" in frozen["system_prompt"]
        assert new_run.budget["decision_calls"] is None
        assert new_run.budget["output_tokens"] is None
        assert frozen["subagent_max_concurrency"] == 3
        assert frozen["prompt_versions"] and frozen["tool_catalog_version"]
        session.commit()
        previous = session.scalar(select(AgentRun).where(AgentRun.id == accepted.run.id))
        assert previous.config_snapshot["assistant_settings"] == old


def test_capacity_patch_preserves_omitted_value_and_can_clear_declaration(harness_workspace):
    from short_drama.service.ai_config.management.assistant_models import AgentModelService
    from tests.integration.standard.assistant.test_global_assistant_tasks import task_actor

    factory, settings, model = harness_workspace
    with factory() as session:
        session.info["actor"] = task_actor()
        service = AgentModelService(session, settings)
        first = service.patch_inputs(
            model,
            {
                "row_version": "1",
                "image": True,
                "audio": False,
                "context_window_tokens": 131072,
                "max_output_tokens": 32768,
            },
        )
        assert first.input_capabilities.context_window_tokens == 131072
        assert first.input_capabilities.context_window_evidence == "declared"
        assert first.input_capabilities.max_output_tokens == 32768
        assert first.input_capabilities.output_capacity_evidence == "declared"
        second = service.patch_inputs(
            model, {"row_version": first.row_version, "image": True, "audio": False}
        )
        assert second.input_capabilities.context_window_tokens == 131072
        assert second.input_capabilities.max_output_tokens == 32768
        cleared = service.patch_inputs(
            model,
            {
                "row_version": second.row_version,
                "image": True,
                "audio": False,
                "context_window_tokens": None,
                "max_output_tokens": None,
            },
        )
        assert cleared.input_capabilities.context_window_tokens == 65536
        assert cleared.input_capabilities.context_window_evidence == "platform_default"
        assert cleared.input_capabilities.max_output_tokens == 8192
        assert cleared.input_capabilities.output_capacity_evidence == "platform_default"
