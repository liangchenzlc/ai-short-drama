"""Atomic editor operations; no model requests or browser workflow persistence."""

from sqlalchemy import func, select

from short_drama.core.exceptions import BusinessError, Conflict
from short_drama.dao.base import BaseDAO
from short_drama.dao.episode_writing_dao import EpisodeWritingDAO, bump_writing_version
from short_drama.domain import AsyncTask, Episode, EpisodeNovel, EpisodeScript, NovelScriptRecord
from short_drama.schemas.base import parse_identifier
from short_drama.schemas.episode_writing import (
    NovelSave,
    NovelSaved,
    ScriptSave,
    ScriptSaved,
    ScriptSelect,
    WritingDocument,
    WritingRead,
    WritingScript,
    WritingVersion,
)

from .base import BaseService, utcnow


class EmptyScript(BusinessError):
    status_code = 422
    code = "empty_script"


class EpisodeWritingService(BaseService):
    model = Episode

    def __init__(self, session):
        super().__init__(session)
        self.dao = EpisodeWritingDAO(session)

    def _scope(self, project_id, episode_id, version=None):
        episode = self.dao.scoped_episode(
            parse_identifier(project_id), parse_identifier(episode_id)
        )
        if version is not None and episode.content_version != version:
            raise Conflict("Content changed elsewhere; reload before saving")
        return episode

    def _view(self, episode):
        novel = self.dao.novel(episode.id)
        script = (
            self.dao.script(episode.id, episode.editing_script_id)
            if episode.editing_script_id
            else None
        )
        confirmed = self.dao.confirmed(episode.id)
        return WritingRead(
            episode_id=episode.id,
            content_version=episode.content_version,
            novel=WritingDocument.model_validate(novel) if novel else None,
            editing_script=WritingScript.model_validate(script) if script else None,
            confirmed_script_id=confirmed.id if confirmed else None,
        ).model_dump(mode="json")

    def get(self, project_id, episode_id):
        with self._transaction():
            return self._view(self._scope(project_id, episode_id))

    def candidates(self, project_id, episode_id, offset=0, limit=20, script_id=None):
        with self._transaction():
            episode = self._scope(project_id, episode_id)
            statement = (
                select(EpisodeScript, AsyncTask.id)
                .outerjoin(NovelScriptRecord, NovelScriptRecord.script_id == EpisodeScript.id)
                .outerjoin(AsyncTask, AsyncTask.id == NovelScriptRecord.batch_id)
                .where(EpisodeScript.episode_id == episode.id)
            )
            if script_id is not None:
                statement = statement.where(EpisodeScript.id == parse_identifier(script_id))
            total = self.session.scalar(select(func.count()).select_from(statement.subquery()))
            rows = self.session.execute(
                statement.order_by(EpisodeScript.position.desc()).offset(offset).limit(limit)
            )
            items = [
                {
                    "id": str(row.id),
                    "position": row.position,
                    "state": row.state,
                    "preview": row.content[:200],
                    "is_editing": row.id == episode.editing_script_id,
                    "is_confirmed": row.state == "confirmed",
                    "generation_id": str(task_id) if task_id else None,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                    **({"content": row.content} if script_id is not None else {}),
                }
                for row, task_id in rows
            ]
            if script_id is not None:
                if not items:
                    from short_drama.core.exceptions import NotFound

                    raise NotFound("剧本不存在于当前分集")
                return items[0]
            return {"items": items, "total": total, "offset": offset, "limit": limit}

    def _create_document(self, model, episode_id, content, **extra):
        now = utcnow()
        return BaseDAO(self.session, model).create(
            {
                "episode_id": episode_id,
                "content": content,
                "created_at": now,
                "updated_at": now,
                "created_by": None,
                "updated_by": None,
                **extra,
            }
        )

    def _changed(self, episode):
        bump_writing_version(episode)
        self.session.flush()

    def save_novel(self, project_id, episode_id, payload):
        data = self._payload(NovelSave, payload)
        with self._transaction():
            episode = self._scope(project_id, episode_id, data["content_version"])
            novel = self.dao.novel(episode.id)
            if novel is None:
                novel = self._create_document(EpisodeNovel, episode.id, data["content"])
                self._changed(episode)
            elif novel.content != data["content"]:
                novel.content, novel.updated_at, novel.updated_by = data["content"], utcnow(), None
                self._changed(episode)
            return NovelSaved(content_version=episode.content_version, novel=novel).model_dump(
                mode="json"
            )

    def save_script(self, project_id, episode_id, payload):
        data = self._payload(ScriptSave, payload)
        with self._transaction():
            episode = self._scope(project_id, episode_id, data["content_version"])
            script = self.dao.script(episode.id, data["script_id"]) if data["script_id"] else None
            if episode.editing_script_id != data["script_id"]:
                raise Conflict("The current editing script has changed")
            if script is None:
                script = self._create_document(
                    EpisodeScript,
                    episode.id,
                    data["content"],
                    position=self.dao.next_position(episode.id),
                    state="unconfirmed",
                )
                episode.editing_script_id = script.id
                self._changed(episode)
            elif script.content != data["content"]:
                script.content, script.state = data["content"], "unconfirmed"
                script.updated_at, script.updated_by = utcnow(), None
                self._changed(episode)
            return ScriptSaved(content_version=episode.content_version, script=script).model_dump(
                mode="json"
            )

    def select_script(self, project_id, episode_id, payload):
        data = self._payload(ScriptSelect, payload)
        with self._transaction():
            episode = self._scope(project_id, episode_id, data["content_version"])
            target = self.dao.script(episode.id, data["script_id"])
            if episode.editing_script_id != target.id:
                episode.editing_script_id = target.id
                self._changed(episode)
            return self._view(episode)

    def confirm(self, project_id, episode_id, script_id, payload):
        data = self._payload(WritingVersion, payload)
        with self._transaction():
            episode = self._scope(project_id, episode_id, data["content_version"])
            target = self.dao.script(episode.id, parse_identifier(script_id))
            if episode.editing_script_id != target.id:
                raise Conflict("Only the current editing script can be confirmed")
            if not target.content.strip():
                raise EmptyScript("请先填写剧本正文再确认")
            if target.state != "confirmed":
                previous = self.dao.confirmed(episode.id)
                if previous is not None:
                    previous.state, previous.updated_at, previous.updated_by = (
                        "unconfirmed",
                        utcnow(),
                        None,
                    )
                    self.session.flush()  # Release the unique confirmed_episode_id first.
                target.state, target.updated_at, target.updated_by = "confirmed", utcnow(), None
                self._changed(episode)
            return self._view(episode)
