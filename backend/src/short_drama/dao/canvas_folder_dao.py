from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.domain.canvas import ProjectCanvas
from short_drama.domain.canvas_folder import CanvasProjectFolder, CanvasProjectFolderItem


class CanvasFolderDAO:
    def __init__(self, session: Session):
        self.session = session

    def folder(self, key: str, *, lock: bool = False) -> CanvasProjectFolder | None:
        query = select(CanvasProjectFolder).where(CanvasProjectFolder.source_key == key)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(query)

    def folders(self) -> list[CanvasProjectFolder]:
        return list(
            self.session.scalars(
                select(CanvasProjectFolder)
                .where(CanvasProjectFolder.tombstoned_at.is_(None))
                .order_by(
                    CanvasProjectFolder.updated_at.desc(),
                    CanvasProjectFolder.created_at.desc(),
                    CanvasProjectFolder.source_key,
                )
            )
        )

    def items(self, key: str) -> list[CanvasProjectFolderItem]:
        return list(
            self.session.scalars(
                select(CanvasProjectFolderItem)
                .where(CanvasProjectFolderItem.folder_key == key)
                .order_by(CanvasProjectFolderItem.canvas_id)
            )
        )

    def item(self, canvas_id: int, *, lock: bool = False) -> CanvasProjectFolderItem | None:
        query = select(CanvasProjectFolderItem).where(
            CanvasProjectFolderItem.canvas_id == canvas_id
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(query)

    def targets(self, canvas_ids: list[int]) -> list[tuple[int, int]]:
        # Do not populate the identity map before taking each project's lock:
        # another member may commit a newer canvas while this operation waits.
        return [
            (project_id, canvas_id)
            for project_id, canvas_id in self.session.execute(
                select(ProjectCanvas.project_id, ProjectCanvas.id)
                .where(ProjectCanvas.id.in_(canvas_ids))
                .order_by(ProjectCanvas.project_id, ProjectCanvas.id)
            )
        ]
