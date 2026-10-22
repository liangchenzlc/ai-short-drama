"""Versioned image adoption snapshots shared by manual and generated adoption."""

from copy import deepcopy

from sqlalchemy import select

from short_drama.dao.image_group_dao import ImageGroupDAO
from short_drama.domain import ShotImage


def _group_state(group):
    return {
        "is_current": group.active_slot == 1,
        "primary_index": group.primary_index,
        "primary_mode": group.primary_mode,
        "primary_version": str(group.primary_version),
        "notice": group.notice,
    }


def _target_state(session, row, kind):
    result = {"row_version": str(row.row_version)}
    if kind == "asset":
        result.update(
            media_id=str(row.media_id) if row.media_id else None,
            model_id=str(row.model_id) if row.model_id else None,
            state=row.state,
            current_version_id=str(row.current_version_id) if row.current_version_id else None,
        )
    else:
        image = session.scalar(
            select(ShotImage).where(ShotImage.shot_id == row.id).with_for_update()
        )
        result["image"] = (
            {
                "id": str(image.id),
                "media_id": str(image.media_id),
                "model_id": str(image.model_id) if image.model_id else None,
                **{
                    field: deepcopy(getattr(image, field))
                    for field in ("prompt", "layout", "aspect", "resolution", "context_hash")
                },
            }
            if image
            else None
        )
    return result


def image_adoption_state(session, row, kind):
    """Versioned binding fields only; generated files and private prompts stay in history."""
    groups = list(ImageGroupDAO(session).for_target(kind, row.id, lock=True))
    return {
        **_target_state(session, row, kind),
        "image_groups": {
            str(group.id): {
                **{key: value for key, value in _group_state(group).items() if key != "notice"},
                "items": [
                    {
                        "id": str(item.id),
                        "item_version": str(item.item_version),
                        "media_id": str(item.media_id) if item.media_id else None,
                    }
                    for item in ImageGroupDAO(session).items(group.id, lock=True)
                ],
            }
            for group in groups
        },
    }
