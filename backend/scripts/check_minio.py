"""Read-only check of the configured MinIO buckets; no bucket/object writes."""

from short_drama.core.config import Settings
from short_drama.core.exceptions import BusinessError
from short_drama.service.storage_service import StorageService
from short_drama.storage.minio import MinioStorage


def main() -> int:
    settings = Settings()
    storage = MinioStorage(settings)
    try:
        for kind, status in StorageService(storage, settings).check_storage().items():
            print(f"{kind}: {status}")
        return 0
    except BusinessError as error:
        print(f"{error.code}: {error.message}")
        return 1
    finally:
        storage.close()


if __name__ == "__main__":
    raise SystemExit(main())
