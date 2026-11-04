"""助手服务依赖：会话、附件和策略分别持有当前请求的同一 Session。"""

from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from short_drama.agent.tools.registry import get_spec
from short_drama.api.dependencies.session import get_session
from short_drama.service.ai_config.management.assistant_models import AgentModelService
from short_drama.service.assistant.context.attachment import AgentAttachmentService
from short_drama.service.assistant.operations.questions import QuestionService
from short_drama.service.assistant.settings.preferences import AssistantSettingsService
from short_drama.service.assistant.tasks.task import AssistantTaskService
from short_drama.service.assistant.tasks.trace import AssistantTraceService
from short_drama.service.skills.catalog.skill import AgentSkillService


def attachment_service(request: Request, session: Session = Depends(get_session)):
    return AgentAttachmentService(
        session, request.app.state.settings, getattr(request.app.state, "storage", None)
    )


def task_service(request: Request, session: Session = Depends(get_session)):
    return AssistantTaskService(
        session,
        request.app.state.settings,
        getattr(request.app.state, "storage", None),
        tool_lookup=get_spec,
    )


def preferences_service(request: Request, session: Session = Depends(get_session)):
    return AssistantSettingsService(session, request.app.state.settings)


def model_service(request: Request, session: Session = Depends(get_session)):
    return AgentModelService(session, request.app.state.settings)


def skill_service(request: Request, session: Session = Depends(get_session)):
    return AgentSkillService(
        session,
        request.app.state.settings,
        getattr(request.app.state, "storage", None),
        tool_lookup=get_spec,
    )


def question_service(request: Request, session: Session = Depends(get_session)):
    return QuestionService(session, request.app.state.settings)


Key = Annotated[str, Header(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_:-]+$")]


def trace_service(request: Request, session: Session = Depends(get_session)):
    return AssistantTraceService(
        session, request.app.state.settings, getattr(request.app.state, "storage", None)
    )


Traces = Annotated[AssistantTraceService, Depends(trace_service)]
Questions = Annotated[QuestionService, Depends(question_service)]
Tasks = Annotated[AssistantTaskService, Depends(task_service)]
Attachments = Annotated[AgentAttachmentService, Depends(attachment_service)]
Preferences = Annotated[AssistantSettingsService, Depends(preferences_service)]
Models = Annotated[AgentModelService, Depends(model_service)]
Skills = Annotated[AgentSkillService, Depends(skill_service)]
