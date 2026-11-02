"""Conversation-authorized trace paging and reads of saved, frozen detail references."""

from copy import deepcopy

from short_drama.agent.runtime.trace_content import display_safe
from short_drama.core.exceptions import NotFound
from short_drama.dao.assistant.execution.trace import AssistantTraceDAO
from short_drama.domain.assistant.context.context import AgentSkill
from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.domain.assistant.execution.scope import AgentExecutionScope
from short_drama.domain.skills.catalog.skill import AgentSkillVersion
from short_drama.schemas.assistant.operations.trace import TracePage, TraceResourceRead
from short_drama.schemas.base import parse_identifier
from short_drama.service.assistant.execution.run import AgentRunService
from short_drama.service.skills.catalog.skill import AgentSkillService


class AssistantTraceService(AgentRunService):
    def _trace_run(self, conversation_id: int | str, run_id: int | str):
        conversation = self._conversation(conversation_id)
        run = self.session.get(AgentRun, parse_identifier(run_id))
        if run is None or run.conversation_id != conversation.id:
            raise NotFound("执行轨迹不存在")
        return conversation, run

    def page(
        self,
        conversation_id: int | str,
        run_id: int | str,
        before_seq: int | None = None,
        limit: int = 50,
    ) -> TracePage:
        self.dao.validate_pagination(0, limit)
        if before_seq is not None and (type(before_seq) is not int or before_seq < 1):
            raise ValueError("before_seq must be positive")
        with self._transaction(read_only=True):
            conversation, run = self._trace_run(conversation_id, run_id)
            return AssistantTraceDAO(self.session).page(
                conversation.id,
                run.id,
                conversation.next_event_seq - 1,
                before_seq=before_seq,
                limit=limit,
            )

    def _binding(self, conversation_id, run_id, block_id, resource_id):
        conversation, run = self._trace_run(conversation_id, run_id)
        row = AssistantTraceDAO(self.session).latest(
            conversation.id, run.id, parse_identifier(block_id), conversation.next_event_seq - 1
        )
        if row is None:
            raise NotFound("轨迹详情不可访问")
        scope = self.session.get(AgentExecutionScope, int(row.payload["execution_scope_id"]))
        if (
            scope is None
            or scope.run_id != run.id
            or scope.owner_user_id != conversation.owner_user_id
        ):
            raise NotFound("轨迹详情不可访问")
        binding = next(
            (
                item
                for item in row.payload.get("_resources", [])
                if item["resource_id"] == str(resource_id) and item.get("available", True)
            ),
            None,
        )
        if binding is None:
            raise NotFound("轨迹详情不可访问")
        return conversation, deepcopy(binding)

    def _version(self, conversation, binding):
        version = self.session.get(AgentSkillVersion, int(binding["version_id"]))
        if (
            version is None
            or version.owner_user_id not in {None, conversation.owner_user_id}
            or (
                version.skill_key != binding["skill_id"]
                or version.checksum_sha256 != binding["checksum"]
            )
        ):
            raise NotFound("本次使用的 Skill 版本不可访问")
        if version.skill_id:
            skill = self.session.get(AgentSkill, version.skill_id)
            if (
                skill is None
                or skill.owner_user_id != conversation.owner_user_id
                or skill.deleted_at
            ):
                raise NotFound("本次使用的 Skill 版本不可访问")
        return version

    def resource(
        self,
        conversation_id: int | str,
        run_id: int | str,
        block_id: int | str,
        resource_id: int | str,
    ) -> TraceResourceRead:
        with self._transaction(read_only=True):
            conversation, binding = self._binding(conversation_id, run_id, block_id, resource_id)
            text, manifest = None, None
            if binding["kind"] == "skill":
                version = self._version(conversation, binding)
                if binding.get("path"):
                    manifest = next(
                        (
                            deepcopy(item)
                            for item in version.resources
                            if item["path"] == binding["path"]
                        ),
                        None,
                    )
                    if manifest is None:
                        raise NotFound("本次使用的 Skill 资源不可访问")
                else:
                    text = version.instructions
        # No database transaction spans object reads or signing. Reauthorize after I/O.
        url = None
        if binding["kind"] == "archive":
            if f"/agent-harness/operation/{block_id}/" not in binding["locator"]:
                raise NotFound("轨迹详情不可访问")
            url = self.storage.download_url(binding["locator"])
        elif manifest:
            if manifest["kind"] == "image":
                url = self.storage.download_url(
                    f"minio://{manifest['bucket']}/{manifest['object_name']}"
                )
            else:
                text = AgentSkillService(None, self.settings, self.storage.storage)._resource_bytes(
                    manifest
                )
        with self._transaction(read_only=True):
            conversation, current = self._binding(conversation_id, run_id, block_id, resource_id)
            if current != binding:
                raise NotFound("轨迹详情不可访问")
            if binding["kind"] == "skill":
                self._version(conversation, binding)
        offset = binding.get("offset") or 0
        length = binding.get("length")
        content = None
        if text is not None:
            content = text[offset:] if length is None else text[offset : offset + length]
        return TraceResourceRead(
            resource_id=resource_id,
            name=display_safe(binding["name"]),
            content=display_safe(content),
            offset=offset if content is not None else None,
            length=len(content) if content is not None else None,
            url=url,
        )
