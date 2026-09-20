from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from short_drama.api.dependencies import get_session, get_storage_service
from short_drama.service.health_service import HealthService
from short_drama.service.storage_service import StorageService

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
