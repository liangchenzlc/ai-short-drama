from typing import Annotated

from fastapi import APIRouter, File, Header, Query, Response, UploadFile

from short_drama.api.dependencies.assistant import Skills
from short_drama.schemas.assistant.context.context import (
    SkillDelete,
    SkillPatch,
    SkillRead,
)
from short_drama.schemas.base import Identifier
from short_drama.schemas.common import PageResponse
from short_drama.schemas.skills.catalog.skill import (
    SkillCopy,
    SkillCreate,
    SkillImportPreview,
    SkillResourceContent,
    SkillSettingsPatch,
    SkillSummary,
)

router = APIRouter(prefix="/agent")


@router.get("/skills", response_model=PageResponse[SkillSummary])
def list_skills(
    service: Skills,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    query: Annotated[str | None, Query(max_length=120)] = None,
    source: Annotated[str | None, Query(pattern="^(builtin|personal)$")] = None,
    enabled: bool | None = None,
    mode: Annotated[str | None, Query(pattern="^(standard|infinite_canvas)$")] = None,
):
    return service.list(
        offset,
        limit,
        query=query,
        source=source,
        enabled=enabled,
        mode=mode,
    )


@router.post("/skills", response_model=SkillRead, status_code=201)
def create_skill(
    payload: SkillCreate,
    service: Skills,
    idempotency_key: Annotated[
        str, Header(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_:-]+$")
    ],
):
    return service.create(payload, idempotency_key)


@router.post("/skills/imports/preview", response_model=SkillImportPreview)
def preview_skill_import(service: Skills, file: Annotated[UploadFile, File()]):
    return service.preview_import(file.file, file.filename or "")


@router.post("/skills/uploads", response_model=SkillRead, status_code=201)
def upload_skill(service: Skills, file: Annotated[UploadFile, File()]):
    return service.upload(file.file, file.filename or "")


@router.get("/skills/{skill_id}", response_model=SkillRead)
def get_skill(skill_id: str, service: Skills):
    return service.detail(skill_id)


@router.get("/skills/requests/{key}", response_model=SkillRead)
def get_skill_request(key: str, service: Skills):
    return service.request_receipt(key)


@router.post("/skills/{skill_id}/copy", response_model=SkillRead, status_code=201)
def copy_skill(
    skill_id: str,
    payload: SkillCopy,
    service: Skills,
    idempotency_key: Annotated[
        str, Header(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_:-]+$")
    ],
):
    return service.copy(skill_id, payload, idempotency_key)


@router.patch("/skills/{skill_id}/settings", response_model=SkillRead)
def patch_skill_settings(skill_id: str, payload: SkillSettingsPatch, service: Skills):
    return service.patch_settings(skill_id, payload)


@router.get(
    "/skills/{skill_id}/versions/{content_version}/resources", response_model=SkillResourceContent
)
def read_skill_resource(
    skill_id: str,
    content_version: Identifier,
    service: Skills,
    path: Annotated[str, Query(min_length=1, max_length=255)],
    offset: Annotated[int, Query(ge=0, le=131072)] = 0,
    limit: Annotated[int, Query(ge=1, le=6000)] = 6000,
):
    return service.resource_content(skill_id, content_version, path, offset=offset, limit=limit)


@router.get("/skills/{skill_id}/export")
def export_skill(skill_id: str, content_version: Identifier, service: Skills):
    data, filename = service.export(skill_id, content_version)
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.patch("/skills/{skill_id}", response_model=SkillRead)
def patch_skill(skill_id: str, payload: SkillPatch, service: Skills):
    return service.patch(skill_id, payload)


@router.delete("/skills/{skill_id}", status_code=204)
def delete_skill(skill_id: str, payload: SkillDelete, service: Skills):
    service.delete(skill_id, payload)
    return Response(status_code=204)
