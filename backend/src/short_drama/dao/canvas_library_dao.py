from datetime import UTC, datetime, timedelta

from sqlalchemy import String, Text, and_, case, cast, func, or_, select

from short_drama.domain import (
    CanvasLibraryAsset,
    CanvasLibraryAssetReference,
    CanvasLibraryFolder,
    CanvasLibraryFolderItem,
)
from short_drama.schemas.canvas_library import CanvasLibraryFilter


class CanvasLibraryDAO:
    def __init__(self, session):
        self.session = session

    def asset(self, key, *, lock=False):
        query = select(CanvasLibraryAsset).where(CanvasLibraryAsset.source_key == key)
        return self.session.scalar(query.with_for_update() if lock else query)

    def references(self, identifier):
        return list(
            self.session.scalars(
                select(CanvasLibraryAssetReference).where(
                    CanvasLibraryAssetReference.library_asset_id == identifier
                )
            )
        )

    def assets(self, keys=None, *, lock=False):
        query = select(CanvasLibraryAsset)
        if keys is not None:
            query = query.where(CanvasLibraryAsset.source_key.in_(keys))
        query = query.order_by(CanvasLibraryAsset.updated_at.desc(), CanvasLibraryAsset.id.desc())
        return list(self.session.scalars(query.with_for_update() if lock else query))

    def folders(self) -> list[CanvasLibraryFolder]:
        return list(
            self.session.scalars(
                select(CanvasLibraryFolder).order_by(
                    CanvasLibraryFolder.position,
                    CanvasLibraryFolder.created_at,
                    CanvasLibraryFolder.id,
                )
            )
        )

    def folder(self, identifier: int, *, lock: bool = False) -> CanvasLibraryFolder | None:
        query = select(CanvasLibraryFolder).where(CanvasLibraryFolder.id == identifier)
        return self.session.scalar(query.with_for_update() if lock else query)

    def folder_named(self, name_key: str) -> CanvasLibraryFolder | None:
        return self.session.scalar(
            select(CanvasLibraryFolder).where(CanvasLibraryFolder.name_key == name_key)
        )

    def next_folder_position(self) -> int:
        last = self.session.scalar(select(func.max(CanvasLibraryFolder.position)))
        return 0 if last is None else last + 1

    def folder_items(self, identifiers: list[int]) -> dict[int, CanvasLibraryFolderItem]:
        if not identifiers:
            return {}
        return {
            row.library_asset_id: row
            for row in self.session.scalars(
                select(CanvasLibraryFolderItem).where(
                    CanvasLibraryFolderItem.library_asset_id.in_(identifiers)
                )
            )
        }

    def assets_in_folder(self, identifier: int) -> list[CanvasLibraryAsset]:
        return list(
            self.session.scalars(
                select(CanvasLibraryAsset)
                .join(
                    CanvasLibraryFolderItem,
                    CanvasLibraryFolderItem.library_asset_id == CanvasLibraryAsset.id,
                )
                .where(CanvasLibraryFolderItem.folder_id == identifier)
                .with_for_update()
            )
        )

    @staticmethod
    def folder_value():
        return func.coalesce(
            cast(
                select(CanvasLibraryFolderItem.folder_id)
                .where(CanvasLibraryFolderItem.library_asset_id == CanvasLibraryAsset.id)
                .correlate(CanvasLibraryAsset)
                .scalar_subquery(),
                String,
            ),
            "",
        )

    @staticmethod
    def project_label():
        payload = CanvasLibraryAsset.payload_json
        name = func.trim(func.coalesce(payload["metadata"]["projectName"].as_string(), ""))
        return case(
            (name != "", name),
            (func.json_length(payload["metadata"]["projectIds"]) > 0, "已关联项目"),
            else_="未关联项目",
        )

    def conditions(self, filters):
        model = CanvasLibraryAsset
        payload = model.payload_json
        result = [model.kind != "entity"]
        for field in ("kind", "category"):
            if value := getattr(filters, field).strip():
                result.append(getattr(model, field) == value)
        folder = self.folder_value()
        if filters.uncategorized:
            result.append(folder == "")
        elif filters.folder_id is not None:
            result.append(folder == filters.folder_id.strip())
        if filters.status == "active":
            result.append(model.status != "archived")
        elif filters.status:
            result.append(model.status == filters.status)
        if value := filters.q.strip().lower():
            result.append(
                or_(
                    func.lower(model.title).like("%" + value + "%"),
                    func.lower(cast(payload, Text)).like("%" + value + "%"),
                )
            )
        if filters.favorite:
            result.append(payload["metadata"]["favorite"].as_string().in_(["true", "1"]))
        if filters.recent:
            result.append(
                model.updated_at >= datetime.now(UTC).replace(tzinfo=None) - timedelta(days=30)
            )
        if filters.project.strip():
            result.append(self.project_label() == filters.project.strip())
        if filters.generated:
            result.append(
                and_(
                    model.kind.in_(["image", "video", "audio"]),
                    or_(
                        payload["source"].as_string() == "生成任务",
                        func.json_type(payload["metadata"]["generationEffectKey"]) == "STRING",
                    ),
                )
            )
        return result

    def count(self, filters):
        return (
            self.session.scalar(
                select(func.count(CanvasLibraryAsset.id)).where(*self.conditions(filters))
            )
            or 0
        )

    def facets(self, column, filters):
        return {
            str(key or ""): count
            for key, count in self.session.execute(
                select(column, func.count(CanvasLibraryAsset.id))
                .where(*self.conditions(filters))
                .group_by(column)
            )
        }

    def folder_facets(self, filters: CanvasLibraryFilter) -> dict[str, int]:
        # Group by the actual joined key. Grouping a correlated scalar subquery
        # fails MySQL ONLY_FULL_GROUP_BY and can lose the uncategorized bucket.
        return {
            str(key) if key is not None else "": count
            for key, count in self.session.execute(
                select(CanvasLibraryFolderItem.folder_id, func.count(CanvasLibraryAsset.id))
                .select_from(CanvasLibraryAsset)
                .outerjoin(
                    CanvasLibraryFolderItem,
                    CanvasLibraryFolderItem.library_asset_id == CanvasLibraryAsset.id,
                )
                .where(*self.conditions(filters))
                .group_by(CanvasLibraryFolderItem.folder_id)
            )
        }

    def page(self, filters):
        model = CanvasLibraryAsset
        rows = list(
            self.session.scalars(
                select(model)
                .where(*self.conditions(filters))
                .order_by(model.updated_at.desc(), model.source_key.desc())
                .offset((filters.page - 1) * filters.page_size)
                .limit(filters.page_size)
            )
        )
        total = self.count(filters)
        facets = CanvasLibraryFilter(status=filters.status)
        generated = CanvasLibraryFilter(status="active", generated=True)
        return {
            "assets": rows,
            "page": filters.page,
            "page_size": filters.page_size,
            "total": total,
            "has_more": filters.page * filters.page_size < total,
            "kind_counts": self.facets(model.kind, facets),
            "category_counts": self.facets(model.category, facets),
            "folder_counts": self.folder_facets(facets),
            "favorite_total": self.count(CanvasLibraryFilter(status="active", favorite=True)),
            "recent_total": self.count(CanvasLibraryFilter(status="active", recent=True)),
            "project_counts": self.facets(
                self.project_label(), CanvasLibraryFilter(status="active")
            ),
            "generated_total": self.count(generated),
            "generated_kind_counts": self.facets(model.kind, generated),
        }
