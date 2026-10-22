"""Read current, scoped works while the caller owns the project transaction."""

from sqlalchemy import String, cast, func, select

from short_drama.dao.canvas_dao import CanvasDAO
from short_drama.dao.project_writing_dao import ProjectWritingDAO
from short_drama.domain import (
    Asset,
    MediaFile,
    Project,
    ProjectCanvas,
    ShotScript,
)


class AssistantContextDAO:
    def __init__(self, session):
        self.session = session

    def project(self, identifier):
        return self.session.scalar(select(Project).where(Project.id == identifier))

    def creation(self, project_id, identifier):
        return self.session.scalar(
            select(Project)
            .where(Project.id == identifier, Project.id == project_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def writing(self, creation):
        dao = ProjectWritingDAO(self.session)
        novel = dao.novel(creation.id, for_update=True)
        script = (
            dao.script(creation.id, creation.editing_script_id, for_update=True)
            if creation.editing_script_id
            else None
        )
        return novel, script

    def assets(self, project_id):
        return list(
            self.session.scalars(
                select(Asset)
                .where(Asset.project_id == project_id, Asset.deleted_at.is_(None))
                .order_by(Asset.id)
                .limit(101)
                .with_for_update()
            )
        )

    def asset(self, project_id, identifier):
        return self.session.scalar(
            select(Asset)
            .where(
                Asset.id == identifier,
                Asset.deleted_at.is_(None),
                Asset.project_id == project_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def shots(self, project_id):
        return list(
            self.session.scalars(
                select(ShotScript)
                .where(ShotScript.project_id == project_id, ShotScript.deleted_at.is_(None))
                .order_by(ShotScript.position, ShotScript.id)
                .limit(101)
                .with_for_update()
            )
        )

    def shot(self, project_id, identifier):
        return self.session.scalar(
            select(ShotScript)
            .where(
                ShotScript.id == identifier,
                ShotScript.project_id == project_id,
                ShotScript.deleted_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def canvas(self, project_id, source_key):
        return self.session.scalar(
            select(ProjectCanvas)
            .where(
                ProjectCanvas.project_id == project_id,
                ProjectCanvas.source_key == source_key,
                ProjectCanvas.archived_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def canvas_nodes(self, canvas_id):
        from short_drama.domain.canvas import CanvasNode

        return CanvasDAO(self.session).children(CanvasNode, canvas_id, active=True, lock=True)

    def media(self, identifier):
        # The actor-scoped Session also excludes other users' unpublished outputs.
        return self.session.scalar(
            select(MediaFile)
            .where(MediaFile.id == identifier)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def image_group(self, project_id, identifier):
        from short_drama.domain import ImageGroup

        return self.session.scalar(
            select(ImageGroup)
            .where(ImageGroup.id == identifier, ImageGroup.project_id == project_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def image_slot(self, identifier):
        from short_drama.domain import ImageGroupItem

        return self.session.scalar(
            select(ImageGroupItem)
            .where(ImageGroupItem.id == identifier)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def sound_scope(self, project_id, *, lock=False):
        from short_drama.domain.project_assembly import ProjectAssembly
        from short_drama.domain.project_sound import ProjectSound

        statement = (
            select(ProjectAssembly, ProjectSound)
            .join(ProjectSound, ProjectSound.assembly_id == ProjectAssembly.id)
            .where(ProjectAssembly.project_id == project_id)
        )
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self.session.execute(statement).one_or_none()

    def subject_directory(self, project_id, kind, query, offset, limit):
        from short_drama.domain import ImageGroup, ImageGroupItem
        from short_drama.domain.canvas import CanvasNode

        if kind == "sound_line":
            scope = self.sound_scope(project_id)
            rows = [
                {
                    "id": line["id"],
                    "name": f"{line['character']} · {line['id']}",
                    "revision": scope[1].row_version,
                }
                for line in (scope[1].document.get("dialogue", []) if scope else [])
                if not query or query.casefold() in f"{line['character']} {line['id']}".casefold()
            ]
            return rows[offset : offset + limit], len(rows)
        if kind in {"asset", "shot"}:
            model = Asset if kind == "asset" else ShotScript
            name = model.name if kind == "asset" else model.title
            statement = select(
                model.id, model.row_version.label("revision"), name.label("name")
            ).where(model.project_id == project_id, model.deleted_at.is_(None))
        elif kind == "node":
            name = CanvasNode.content_json["title"].as_string()
            statement = (
                select(
                    CanvasNode.node_key.label("id"),
                    CanvasNode.row_version.label("revision"),
                    CanvasNode.content_version.label("content_revision"),
                    name.label("name"),
                    ProjectCanvas.source_key.label("source_id"),
                    ProjectCanvas.title.label("source_name"),
                )
                .join(ProjectCanvas, ProjectCanvas.id == CanvasNode.canvas_id)
                .where(
                    CanvasNode.project_id == project_id,
                    CanvasNode.archived_at.is_(None),
                    ProjectCanvas.archived_at.is_(None),
                )
            )
        else:
            available_target = (
                select(Asset.id)
                .where(Asset.id == ImageGroup.asset_id, Asset.deleted_at.is_(None))
                .exists()
                | select(ShotScript.id)
                .where(ShotScript.id == ImageGroup.shot_id, ShotScript.deleted_at.is_(None))
                .exists()
            )
            if kind == "image_group":
                name = cast(ImageGroup.id, String)
                statement = select(
                    ImageGroup.id, ImageGroup.primary_version.label("revision")
                ).where(ImageGroup.project_id == project_id, available_target)
            else:
                name = cast(ImageGroupItem.id, String)
                statement = (
                    select(
                        ImageGroupItem.id,
                        ImageGroupItem.item_version.label("revision"),
                        ImageGroupItem.output_index,
                        ImageGroupItem.group_id,
                    )
                    .join(ImageGroup, ImageGroup.id == ImageGroupItem.group_id)
                    .where(ImageGroup.project_id == project_id, available_target)
                )
        if query:
            statement = statement.where(name.contains(query, autoescape=True))
        total = self.session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = (
            self.session.execute(statement.order_by("id").offset(offset).limit(limit))
            .mappings()
            .all()
        )
        return rows, total
