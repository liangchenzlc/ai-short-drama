from sqlalchemy import select

from short_drama.dao.base import BaseDAO
from short_drama.domain import ImageGroup, ImageGroupItem


class ImageGroupDAO(BaseDAO):
    def __init__(self, session):
        super().__init__(session, ImageGroup)

    def for_target(self, kind, identifier, *, current=False, lock=False):
        field = ImageGroup.asset_id if kind == "asset" else ImageGroup.shot_id
        query = select(ImageGroup).where(field == identifier)
        if current:
            query = query.where(ImageGroup.active_slot == 1)
        query = query.order_by(ImageGroup.created_at.desc(), ImageGroup.id.desc())
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.session.scalars(query)

    def items(self, group_id, *, lock=False):
        query = select(ImageGroupItem).where(ImageGroupItem.group_id == group_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return list(self.session.scalars(query.order_by(ImageGroupItem.output_index)))
