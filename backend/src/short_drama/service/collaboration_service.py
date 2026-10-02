import secrets
from datetime import timedelta

from sqlalchemy import select

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.core.identity import token_hash
from short_drama.domain import AsyncTask, Project
from short_drama.domain.collaboration import AuditEvent, ProjectInvitation, ProjectMember, User
from short_drama.schemas.identity import normalize_email
from short_drama.utils.snowflake import next_id

from .auth_service import AuthService
from .base import utcnow


class CollaborationService:
    """Explicit project checks on independent sessions, including pre-membership proofs."""

    def __init__(self, factory, settings, actor):
        self.factory, self.settings, self.actor = factory, settings, actor
        self.auth = AuthService(factory, settings)

    def project(self, session, identifier, owner=False):
        project = session.scalar(select(Project).where(Project.id == identifier).with_for_update())
        if project is None or project.archived_at or self.actor is None:
            raise NotFound("Project does not exist")
        if project.owner_user_id != self.actor.user_id:
            member = session.scalar(
                select(ProjectMember)
                .where(
                    ProjectMember.project_id == identifier,
                    ProjectMember.user_id == self.actor.user_id,
                    ProjectMember.status == "active",
                )
                .with_for_update()
            )
            if member is None:
                raise NotFound("Project does not exist")
            if owner:
                raise WorkflowError(
                    "project_owner_required", "Only the project owner can do this", 403
                )
        return project

    def audit(self, session, project_id, action, identifier, kind="project_invitations"):
        session.add(
            AuditEvent(
                id=next_id(),
                actor_user_id=self.actor.user_id,
                project_id=project_id,
                object_type=kind,
                object_id=str(identifier),
                action=action,
                request_id=self.actor.request_id,
                created_at=utcnow(),
            )
        )

    def members(self, project_id):
        with self.factory.begin() as session:
            project = self.project(session, project_id)
            owner = session.get(User, project.owner_user_id)
            rows = session.execute(
                select(ProjectMember, User)
                .join(User, User.id == ProjectMember.user_id)
                .where(ProjectMember.project_id == project_id, ProjectMember.status == "active")
                .order_by(ProjectMember.joined_at)
            ).all()
            return {
                "items": [
                    {
                        "user_id": str(owner.id),
                        "username": owner.username,
                        "display_name": owner.display_name,
                        "role": "owner",
                    }
                ]
                + [
                    {
                        "user_id": str(u.id),
                        "username": u.username,
                        "display_name": u.display_name,
                        "role": "collaborator",
                    }
                    for m, u in rows
                ],
                "can_manage": project.owner_user_id == self.actor.user_id,
            }

    def invitation_dto(self, invitation):
        return {
            "id": str(invitation.id),
            "target_user_id": str(invitation.target_user_id) if invitation.target_user_id else None,
            "target_email": invitation.target_email,
            "status": "expired"
            if invitation.status == "pending" and invitation.expires_at <= utcnow()
            else invitation.status,
            "expires_at": invitation.expires_at,
        }

    def create_invitation(self, project_id, payload):
        if (payload.target_user_id is None) == (payload.target_email is None):
            raise WorkflowError(
                "invitation_target_required", "Choose one account or specify one email", 422
            )
        self.auth.limit("create_invitation", str(self.actor.user_id), 30, 3600)
        with self.factory.begin() as session:
            project = self.project(session, project_id, owner=True)
            target_id, email = payload.target_user_id, None
            if target_id:
                user = session.get(User, target_id)
                if user is None or user.status != "active" or not user.email_verified_at:
                    raise NotFound("Recipient account does not exist")
            else:
                try:
                    email = normalize_email(payload.target_email)
                except ValueError:
                    raise WorkflowError(
                        "invitation_email_invalid", "Enter a valid email", 422
                    ) from None
                user = session.scalar(
                    select(User).where(User.email == email, User.status == "active")
                )
                if user:
                    target_id, email = user.id, None
            if target_id == project.owner_user_id:
                raise WorkflowError("already_member", "The owner already has access", 409)
            if target_id and session.scalar(
                select(ProjectMember.id).where(
                    ProjectMember.project_id == project_id,
                    ProjectMember.user_id == target_id,
                    ProjectMember.status == "active",
                )
            ):
                raise WorkflowError("already_member", "This account already has access", 409)
            token = secrets.token_urlsafe(32)
            now = utcnow()
            invitation = ProjectInvitation(
                id=next_id(),
                project_id=project_id,
                invited_by=self.actor.user_id,
                target_user_id=target_id,
                target_email=email,
                token_hash=token_hash(token),
                status="pending",
                expires_at=now + timedelta(days=7),
                created_at=now,
            )
            session.add(invitation)
            self.audit(session, project_id, "invite", invitation.id)
            return {
                **self.invitation_dto(invitation),
                "url": self.settings.public_origin.rstrip("/") + "/invite/" + token,
            }

    def invitations(self, project_id):
        with self.factory.begin() as session:
            self.project(session, project_id, owner=True)
            return {
                "items": [
                    self.invitation_dto(i)
                    for i in session.scalars(
                        select(ProjectInvitation)
                        .where(ProjectInvitation.project_id == project_id)
                        .order_by(ProjectInvitation.created_at.desc())
                        .limit(100)
                    )
                ]
            }

    def revoke(self, project_id, identifier):
        with self.factory.begin() as session:
            self.project(session, project_id, owner=True)
            invitation = session.scalar(
                select(ProjectInvitation)
                .where(
                    ProjectInvitation.id == identifier, ProjectInvitation.project_id == project_id
                )
                .with_for_update()
            )
            if not invitation:
                raise NotFound("Invitation does not exist")
            if invitation.status == "pending":
                invitation.status = "revoked"
                self.audit(session, project_id, "revoke", identifier)
            return self.invitation_dto(invitation)

    def find(self, session, token, lock=False):
        if len(token) != 43:
            raise NotFound("Invitation does not exist")
        query = select(ProjectInvitation).where(ProjectInvitation.token_hash == token_hash(token))
        invitation = session.scalar(query)
        if not invitation:
            raise NotFound("Invitation does not exist")
        # All membership mutations share project -> invitation -> user lock order.
        project = session.scalar(
            select(Project).where(Project.id == invitation.project_id).with_for_update()
            if lock
            else select(Project).where(Project.id == invitation.project_id)
        )
        if not project or project.archived_at:
            raise NotFound("Invitation does not exist")
        if lock:
            invitation = session.scalar(
                query.with_for_update().execution_options(populate_existing=True)
            )
        return invitation, project

    def recipient(self, session, invitation):
        if not self.actor:
            raise WorkflowError("authentication_required", "Please sign in", 401)
        user = session.scalar(select(User).where(User.id == self.actor.user_id).with_for_update())
        if not user or user.status != "active" or not user.email_verified_at:
            raise WorkflowError(
                "email_verification_required", "Verify your registered email first", 403
            )
        if (invitation.target_user_id is not None and invitation.target_user_id != user.id) or (
            invitation.target_email is not None and invitation.target_email != user.email
        ):
            raise WorkflowError(
                "invitation_identity_mismatch", "This invitation is for a different account", 403
            )
        return user

    def valid(self, invitation):
        if invitation.status != "pending" or invitation.expires_at <= utcnow():
            raise WorkflowError(
                "invitation_unavailable", "Invitation has expired or been revoked", 410
            )

    def preview(self, token):
        with self.factory() as session:
            invitation, project = self.find(session, token)
            if not self.actor:
                return {
                    "status": "pending"
                    if invitation.status == "pending" and invitation.expires_at > utcnow()
                    else "unavailable",
                    "requires_login": True,
                }
            self.recipient(session, invitation)
            if invitation.status == "accepted":
                member = session.scalar(
                    select(ProjectMember.id).where(
                        ProjectMember.project_id == project.id,
                        ProjectMember.user_id == self.actor.user_id,
                        ProjectMember.status == "active",
                    )
                )
                if not member:
                    raise WorkflowError("invitation_unavailable", "Membership was removed", 410)
            else:
                self.valid(invitation)
            return {
                "status": invitation.status,
                "project_name": project.name,
                "project_id": str(project.id),
                "requires_email_proof": True,
            }

    def request_proof(self, token):
        self.auth.limit("invitation_email", str(self.actor.user_id), 5)
        with self.factory.begin() as session:
            invitation, _project = self.find(session, token, lock=True)
            self.valid(invitation)
            user = self.recipient(session, invitation)
            return self.auth.issue(session, user, "invitation", invitation.id)

    def accept(self, token, proof):
        self.auth.limit("invitation_proof", str(self.actor.user_id), 15)
        with self.factory.begin() as session:
            invitation, project = self.find(session, token, lock=True)
            self.recipient(session, invitation)
            member = session.scalar(
                select(ProjectMember)
                .where(
                    ProjectMember.project_id == project.id,
                    ProjectMember.user_id == self.actor.user_id,
                )
                .with_for_update()
            )
            if (
                invitation.status == "accepted"
                and invitation.accepted_by == self.actor.user_id
                and member
                and member.status == "active"
            ):
                return {"project_id": str(project.id), "joined": True}
            self.valid(invitation)
            user = self.auth.check_proof(
                session, proof, "invitation", self.actor.user_id, invitation.id
            )
            if user:
                now = utcnow()
                if member:
                    member.status, member.joined_at, member.removed_at = "active", now, None
                else:
                    session.add(
                        ProjectMember(
                            id=next_id(),
                            project_id=project.id,
                            user_id=user.id,
                            status="active",
                            joined_at=now,
                        )
                    )
                invitation.status, invitation.accepted_by, invitation.accepted_at = (
                    "accepted",
                    user.id,
                    now,
                )
                self.audit(session, project.id, "join", user.id, "project_members")
        if not user:
            raise WorkflowError("email_proof_invalid", "Code is invalid or expired", 422)
        return {"project_id": str(project.id), "joined": True}

    def remove(self, project_id, user_id, leave=False):
        with self.factory.begin() as session:
            project = self.project(session, project_id, owner=not leave)
            if user_id == project.owner_user_id or (leave and user_id != self.actor.user_id):
                raise WorkflowError(
                    "project_owner_required", "The owner cannot leave their project", 403
                )
            member = session.scalar(
                select(ProjectMember)
                .where(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
                .with_for_update()
            )
            if not member:
                raise NotFound("Member does not exist")
            member.status, member.removed_at = ("left" if leave else "removed"), utcnow()
            # Lock project -> batches/imports -> tasks. Old pending work never revives
            # when the same account rejoins; supplier-accepted work may still archive.
            from short_drama.domain import GenerationBatchJob
            from short_drama.domain.collaboration import ResourceImport

            for batch in session.scalars(
                select(GenerationBatchJob)
                .where(
                    GenerationBatchJob.project_id == project_id,
                    GenerationBatchJob.initiated_by == user_id,
                    GenerationBatchJob.status.in_(["running", "paused", "needs_review"]),
                )
                .order_by(GenerationBatchJob.id)
                .with_for_update()
            ):
                batch.status = "cancelled"
            for job in session.scalars(
                select(ResourceImport)
                .where(
                    ResourceImport.project_id == project_id,
                    ResourceImport.initiated_by == user_id,
                    ResourceImport.status.in_(["pending", "copying"]),
                )
                .order_by(ResourceImport.id)
                .with_for_update()
            ):
                job.status = "cancelled"
            from short_drama.domain import Episode, EpisodeAssembly, EpisodeRenderJob

            for job in session.scalars(
                select(EpisodeRenderJob)
                .join(
                    EpisodeAssembly,
                    EpisodeAssembly.id == EpisodeRenderJob.assembly_id,
                )
                .join(Episode, Episode.id == EpisodeAssembly.episode_id)
                .where(
                    Episode.project_id == project_id,
                    EpisodeRenderJob.initiated_by == user_id,
                    EpisodeRenderJob.status == "queued",
                )
                .order_by(EpisodeRenderJob.id)
                .with_for_update()
            ):
                job.status, job.stage, job.finished_at = "cancelled", "cancelled", utcnow()
            from short_drama.dao.task_runtime_dao import finish, latest_record

            tasks = session.scalars(
                select(AsyncTask)
                .where(
                    AsyncTask.project_id == project_id,
                    AsyncTask.initiated_by == user_id,
                    AsyncTask.status.in_(["queued", "running"]),
                )
                .order_by(AsyncTask.id)
                .with_for_update()
            ).all()
            for task in tasks:
                record = latest_record(session, task.id)
                if record and record.status == "prepared":
                    finish(task, "cancelled", {"code": "access_revoked"})
            self.audit(
                session, project_id, "leave" if leave else "remove", user_id, "project_members"
            )
        return {"removed": True}
