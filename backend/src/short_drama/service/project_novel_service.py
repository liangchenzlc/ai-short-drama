from short_drama.dao.project_writing_dao import VersionedDocumentDAO
from short_drama.domain import Project, ProjectNovel
from short_drama.schemas import ProjectNovelCreate, ProjectNovelRead, ProjectNovelUpdate

from .base import BaseService


class ProjectNovelService(BaseService):
    model = ProjectNovel
    create_schema = ProjectNovelCreate
    update_schema = ProjectNovelUpdate
    read_schema = ProjectNovelRead
    parent_model = Project
    parent_field = "project_id"

    def __init__(self, session):
        super().__init__(session)
        self.dao = VersionedDocumentDAO(session, self.model)
