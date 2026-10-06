from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.domain import AIModelConfig, CanvasChannelModel, CanvasModelCatalog


class CanvasModelCatalogDAO:
    def __init__(self, session: Session):
        self.session = session

    def catalog(self, user_id: int, *, lock=False) -> CanvasModelCatalog | None:
        query = select(CanvasModelCatalog).where(CanvasModelCatalog.user_id == user_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(query)

    def bindings(self, user_id: int, *, lock=False) -> list[CanvasChannelModel]:
        query = (
            select(CanvasChannelModel)
            .where(CanvasChannelModel.user_id == user_id)
            .order_by(CanvasChannelModel.id)
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return list(self.session.scalars(query))

    def models(self, identifiers: list[int], *, lock=False) -> dict[int, AIModelConfig]:
        if not identifiers:
            return {}
        query = (
            select(AIModelConfig)
            .where(AIModelConfig.id.in_(identifiers))
            .order_by(AIModelConfig.id)
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return {row.id: row for row in self.session.scalars(query)}

    def binding(self, model_id: int) -> CanvasChannelModel | None:
        return self.session.scalar(
            select(CanvasChannelModel).where(CanvasChannelModel.model_config_id == model_id)
        )
