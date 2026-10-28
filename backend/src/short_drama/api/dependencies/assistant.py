"""助手服务依赖：会话、附件和策略分别持有当前请求的同一 Session。"""

from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from short_drama.api.dependencies.session import get_session
from short_drama.service.ai_config.management.assistant_models import AgentModelService
from short_drama.service.assistant.context.attachment import AgentAttachmentService
from short_drama.service.assistant.tasks.policy import AssistantPolicyService
from short_drama.service.assistant.tasks.task import AssistantTaskService
from short_drama.service.skills.catalog.skill import AgentSkillService


def attachment_service(request: Request, session: Session = Depends(get_session)):
    return AgentAttachmentService(
        session, request.app.state.settings, getattr(request.app.state, "storage", None)
    )


def task_service(request: Request, session: Session = Depends(get_session)):
    return AssistantTaskService(
        session, request.app.state.settings, getattr(request.app.state, "storage", None)
    )


def policy_service(session: Session = Depends(get_session)):
    return AssistantPolicyService(session)


def model_service(request: Request, session: Session = Depends(get_session)):
    return AgentModelService(session, request.app.state.settings)


def skill_service(request: Request, session: Session = Depends(get_session)):
    return AgentSkillService(
        session, request.app.state.settings, getattr(request.app.state, "storage", None)
    )


Key = Annotated[str, Header(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_:-]+$")]
Tasks = Annotated[AssistantTaskService, Depends(task_service)]
Attachments = Annotated[AgentAttachmentService, Depends(attachment_service)]
Policies = Annotated[AssistantPolicyService, Depends(policy_service)]
Models = Annotated[AgentModelService, Depends(model_service)]
Skills = Annotated[AgentSkillService, Depends(skill_service)]
