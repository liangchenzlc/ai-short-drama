"""私人生成草稿：可信本人、对象范围、版本冲突与锁内冻结。"""

from copy import deepcopy

from pydantic import TypeAdapter
from sqlalchemy import select

from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.core.identity import require_actor
from short_drama.dao.user_creation_draft_dao import UserCreationDraftDAO
from short_drama.domain import (
    Asset,
    Episode,
    EpisodeAsset,
    MediaFile,
    ShotScript,
    UserCreationDraft,
)
from short_drama.schemas.base import UINT64_MAX, parse_identifier
from short_drama.schemas.user_creation_draft import (
    DraftPurpose,
    UserCreationDraftCreate,
    UserCreationDraftRead,
    UserCreationDraftUpdate,
    draft_prompt_limit,
)

from .base import BaseService, utcnow
from .storage_service import StorageService

PURPOSE_KIND = {
    "novel_adaptation": "text",
    "asset_image": "image",
    "storyboard_image": "image",
    "storyboard_video": "video",
}
SCENE_PURPOSE = {
    "novel_script": "novel_adaptation",
    "asset_image": "asset_image",
    "shot_image": "storyboard_image",
    "shot_video": "storyboard_video",
}


class UserCreationDraftService(BaseService):
    model = UserCreationDraft
    read_schema = UserCreationDraftRead

    def __init__(self, session, settings=None, storage=None):
        super().__init__(session)
        self.dao = UserCreationDraftDAO(session)
        self.storage = (
            StorageService(storage, settings) if storage is not None and settings else None
        )

    def _scope(self, project_id, episode_id, object_id=None, purpose=None, *, lock=False):
        actor = require_actor(self.session)
        project_id, episode_id = parse_identifier(project_id), parse_identifier(episode_id)
        episode_query = (
            select(Episode)
            .where(
                Episode.id == episode_id,
                Episode.project_id == project_id,
            )
            .execution_options(populate_existing=True)
        )
        episode = self.session.scalar(episode_query.with_for_update() if lock else episode_query)
        if episode is None:
            raise NotFound("分集不存在于当前项目")
        if object_id is None:
            return actor, episode, None
        purpose = TypeAdapter(DraftPurpose).validate_python(purpose)
        object_id = parse_identifier(object_id)
        if purpose == "novel_adaptation":
            if object_id != episode.id:
                raise NotFound("小说改编草稿的对象必须是当前分集")
            return actor, episode, episode
        model = Asset if purpose == "asset_image" else ShotScript
        query = select(model).where(model.id == object_id).execution_options(populate_existing=True)
        row = self.session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise NotFound("创作对象不存在")
        if model is Asset:
            if (
                self.session.scalar(
                    select(EpisodeAsset.id).where(
                        EpisodeAsset.episode_id == episode.id,
                        EpisodeAsset.asset_id == object_id,
                    )
                )
                is None
            ):
                raise NotFound("素材不属于当前分集")
        elif row.episode_id != episode.id or row.deleted_at is not None:
            raise NotFound("镜头不存在于当前分集")
        return actor, episode, row

    @staticmethod
    def _source_version(obj, purpose):
        return obj.content_version if purpose == "novel_adaptation" else obj.row_version

    def _validate_content(self, purpose, values, project_id, user_id):
        maximum = draft_prompt_limit(purpose)
        if len(values.get("input_text", "")) > maximum:
            raise WorkflowError(
                "invalid_creation_draft", f"当前生成用途的输入不能超过 {maximum} 字符", 422
            )
        if values.get("model_config_id") is not None:
            self._validate_model(values["model_config_id"], PURPOSE_KIND[purpose])
        image = purpose in {"asset_image", "storyboard_image"}
        video = purpose == "storyboard_video"
        if (
            not image
            and values.get("image_settings") is not None
            or not video
            and values.get("video_settings") is not None
            or not image
            and values.get("reference_media_ids")
        ):
            raise WorkflowError("invalid_creation_draft", "草稿参数与生成用途不一致", 422)
        from short_drama.db.access import scope_of

        ids = list(dict.fromkeys(map(str, values.get("reference_media_ids") or [])))
        for identifier in ids:
            media = self._validate_media(identifier, "image")
            if not media.storage_locator.startswith("minio://") or scope_of(
                self.session, media
            ) not in {(user_id, None), (None, project_id)}:
                raise NotFound("参考图不可用于当前私人草稿")
        values["reference_media_ids"] = ids

    def _read(self, row):
        values = {
            key: getattr(row, key) for key in self.read_schema.model_fields if hasattr(row, key)
        }
        ids = [int(value) for value in row.reference_media_ids or []]
        media = {
            m.id: m for m in self.session.scalars(select(MediaFile).where(MediaFile.id.in_(ids)))
        }
        values["reference_images"] = [
            {
                "media_id": str(m.id),
                "width": m.width,
                "height": m.height,
                "url": self.storage.download_url(m.storage_locator) if self.storage else None,
            }
            for identifier in ids
            if (m := media.get(identifier)) is not None
        ]
        return self.read_schema.model_validate(values).model_dump(mode="json")

    def get_for_scope(self, project_id, episode_id, object_id, purpose):
        with self._transaction(read_only=True):
            actor, episode, _ = self._scope(project_id, episode_id, object_id, purpose)
            row = self.dao.scoped(
                actor.user_id, episode.project_id, episode.id, parse_identifier(object_id), purpose
            )
            return self._read(row) if row else None

    def list_for_episode(self, project_id, episode_id):
        with self._transaction(read_only=True):
            actor, episode, _ = self._scope(project_id, episode_id)
            return [
                self._read(row)
                for row in self.dao.list_for_episode(
                    actor.user_id,
                    episode.project_id,
                    episode.id,
                )
            ]

    def create_for_scope(self, project_id, episode_id, payload):
        raw = payload.model_dump() if isinstance(payload, UserCreationDraftCreate) else payload
        values = UserCreationDraftCreate.model_validate(raw).model_dump()
        with self._transaction():
            purpose, object_id = values["purpose"], values["object_id"]
            actor, episode, obj = self._scope(project_id, episode_id, object_id, purpose, lock=True)
            existing = self.dao.scoped(
                actor.user_id, episode.project_id, episode.id, object_id, purpose, for_update=True
            )
            if existing:
                raise WorkflowError("draft_version_conflict", "草稿已存在，请读取当前版本后保存")
            self._validate_content(purpose, values, episode.project_id, actor.user_id)
            values.update(
                user_id=actor.user_id,
                project_id=episode.project_id,
                episode_id=episode.id,
                source_version=self._source_version(obj, purpose),
                row_version=1,
                draft_version=1,
            )
            return self._read(self.dao.create(self._creation_audit(values)))

    def update_for_scope(self, project_id, episode_id, object_id, purpose, payload):
        values = self._payload(UserCreationDraftUpdate, payload)
        expected = values.pop("row_version")
        with self._transaction():
            actor, episode, obj = self._scope(project_id, episode_id, object_id, purpose, lock=True)
            row = self.dao.scoped(
                actor.user_id,
                episode.project_id,
                episode.id,
                parse_identifier(object_id),
                purpose,
                for_update=True,
            )
            if row is None:
                raise NotFound("私人草稿不存在")
            if row.row_version != expected:
                raise WorkflowError(
                    "draft_version_conflict",
                    "草稿已变化，请保留输入并核对",
                    details={
                        "current_version": str(row.row_version),
                    },
                )
            merged = {key: getattr(row, key) for key in UserCreationDraftCreate.model_fields}
            merged.update(values)
            self._validate_content(purpose, merged, episode.project_id, actor.user_id)
            merged = UserCreationDraftCreate.model_validate(merged).model_dump()
            merged["source_version"] = self._source_version(obj, purpose)
            changed = {key: value for key, value in merged.items() if getattr(row, key) != value}
            if row.row_version >= UINT64_MAX or changed and row.draft_version >= UINT64_MAX:
                raise Conflict("私人草稿版本已耗尽")
            draft_version = row.draft_version + 1 if changed else row.draft_version
            changed.update(
                row_version=row.row_version + 1,
                draft_version=draft_version,
                updated_at=utcnow(),
            )
            self.dao.update(row, changed)
            return self._read(row)

    def delete_for_scope(self, project_id, episode_id, object_id, purpose):
        with self._transaction():
            actor, episode, _ = self._scope(project_id, episode_id, object_id, purpose, lock=True)
            row = self.dao.scoped(
                actor.user_id,
                episode.project_id,
                episode.id,
                parse_identifier(object_id),
                purpose,
                for_update=True,
            )
            if row:
                self.dao.delete(row)

    def freeze_for_generation(self, request):
        source = request.get("source") or {}
        if not source.get("draft_id"):
            return request
        actor = require_actor(self.session)
        row = self.session.scalar(
            select(UserCreationDraft)
            .where(
                UserCreationDraft.id == int(source["draft_id"]),
                UserCreationDraft.user_id == actor.user_id,
            )
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFound("私人生成草稿不存在")
        purpose = SCENE_PURPOSE.get(source["scene"])
        object_id = source.get("asset_id") or source.get("shot_id") or source.get("episode_id")
        if (
            purpose != row.purpose
            or str(object_id) != str(row.object_id)
            or source.get("episode_id")
            and str(source["episode_id"]) != str(row.episode_id)
            or source.get("project_id")
            and str(source["project_id"]) != str(row.project_id)
        ):
            raise WorkflowError(
                "creation_draft_scope_mismatch", "私人草稿不属于此对象和生成用途", 422
            )
        _, episode, obj = self._scope(
            row.project_id, row.episode_id, row.object_id, purpose, lock=True
        )
        # 保存与生成统一按分集、对象、草稿加锁，避免两条路径交叉等待。
        row = self.session.scalar(
            select(UserCreationDraft)
            .where(
                UserCreationDraft.id == int(source["draft_id"]),
                UserCreationDraft.user_id == actor.user_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFound("私人生成草稿不存在")
        if row.draft_version != int(source["draft_version"]):
            raise WorkflowError("draft_version_conflict", "私人生成参数已变化，请保存并核对")
        if request.get("project_id") and int(request["project_id"]) != episode.project_id:
            raise NotFound("私人草稿不属于请求项目")
        if row.model_config_id is None:
            raise WorkflowError("creation_draft_model_required", "请在私人草稿中选择生成模型", 422)
        if request.get("config_id") and int(request["config_id"]) != row.model_config_id:
            raise WorkflowError(
                "creation_draft_model_mismatch", "请求模型与已保存私人草稿不一致", 422
            )
        values = {key: deepcopy(getattr(row, key)) for key in UserCreationDraftCreate.model_fields}
        self._validate_content(purpose, values, episode.project_id, actor.user_id)
        request["config_id"] = str(row.model_config_id)
        request["creation_draft_snapshot"] = {
            **values,
            "id": str(row.id),
            "object_id": str(row.object_id),
            "model_config_id": str(row.model_config_id),
            "draft_version": str(row.draft_version),
            "source_version": str(row.source_version) if row.source_version is not None else None,
            "current_source_version": str(self._source_version(obj, purpose)),
        }
        if purpose == "novel_adaptation":
            request["instructions"] = row.input_text
        elif purpose in {"asset_image", "storyboard_image"}:
            request["input"] = {
                "prompt": row.input_text,
                "reference_media_ids": values["reference_media_ids"],
            }
            defaults = {k: v for k, v in (row.image_settings or {}).items() if v is not None}
            request["parameters"].update(defaults)
        return request
