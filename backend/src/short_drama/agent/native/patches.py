from sqlalchemy import select

from short_drama.core.exceptions import NotFound
from short_drama.domain import (
    Asset,
)
from short_drama.schemas.assistant.operations.artifacts import (
    CandidateAssetPatch,
    CandidateShotPatch,
)


def asset_in_creation(session, project_id, asset_id):
    asset = session.scalar(
        select(Asset)
        .where(
            Asset.id == asset_id,
            Asset.deleted_at.is_(None),
            Asset.project_id == project_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if asset is None:
        raise NotFound("Asset is outside this creation")
    return asset


def candidate_patch(kind, patch, *, version=None):
    if kind == "asset_patch":
        if {"row_version"} & set(patch):
            raise ValueError("Authorization is not a proposed field")
        return CandidateAssetPatch.model_validate({**patch, "row_version": version}).model_dump(
            exclude_unset=True, exclude={"row_version"}
        )
    return CandidateShotPatch.model_validate(patch).model_dump(exclude_unset=True)
