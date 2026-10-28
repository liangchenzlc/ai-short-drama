from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from short_drama.api.dependencies.media import get_storage_service
from short_drama.api.dependencies.session import get_session
from short_drama.service.media.files.storage import StorageService
from short_drama.service.system.health.health import HealthService

router = APIRouter(prefix="/test", tags=["test"])


@router.get("")
def test() -> dict[str, str]:
    return {"message": "ok"}


@router.get("/db")
def test_db(session: Session = Depends(get_session)) -> dict[str, str]:
    HealthService(session).check_database()
    return {"message": "ok", "database": "ok"}


@router.get("/minio")
def test_minio(storage: StorageService = Depends(get_storage_service)) -> dict:
    return {"message": "ok", "buckets": storage.check_storage()}
