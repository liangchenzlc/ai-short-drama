from urllib.parse import quote, urlsplit

from minio.helpers import check_bucket_name
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "AI Short Drama"
    db_host: str = "127.0.0.1"
    db_port: int = Field(default=3306, ge=1, le=65535)
    db_user: str = "short_drama"
    db_password: SecretStr = SecretStr("")
    db_name: str = "short_drama"
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=5, ge=0)
    db_connect_timeout: int = Field(default=3, ge=1, le=60)
    encryption_key: SecretStr | None = None
    snowflake_worker_id: int = Field(default=1, ge=0, le=1023)
    model_discovery_allowed_hosts: list[str] = Field(default_factory=list)
    minio_endpoint: str | None = None
    minio_access_key: SecretStr | None = None
    minio_secret_key: SecretStr | None = None
    minio_secure: bool = False
    minio_region: str = "us-east-1"
    minio_image_bucket: str = Field(default="image", pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
    minio_video_bucket: str = Field(default="video", pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
    minio_connect_timeout: int = Field(default=5, ge=1, le=60)
    minio_read_timeout: int = Field(default=60, ge=1, le=600)
    minio_presign_expiry: int = Field(default=900, ge=1, le=604800)
    rabbitmq_host: str = "127.0.0.1"
    rabbitmq_port: int = Field(default=5672, ge=1, le=65535)
    rabbitmq_user: str = "short_drama"
    rabbitmq_password: SecretStr = SecretStr("")
    rabbitmq_vhost: str = "/"
    rabbitmq_tls: bool = False
    generation_queue_namespace: str = Field(default="short_drama", pattern=r"^[a-zA-Z0-9_]{1,80}$")
    generation_lease_seconds: int = Field(default=120, ge=30, le=3600)
    generation_publish_lease_seconds: int = Field(default=15, ge=5, le=120)
    generation_poll_seconds: int = Field(default=5, ge=3, le=60)
    generation_text_budget_seconds: int = Field(default=3600, ge=10, le=3600)
    generation_image_budget_seconds: int = Field(default=300, ge=10, le=3600)
    generation_video_budget_seconds: int = Field(default=1800, ge=30, le=86400)
    generation_archive_budget_seconds: int = Field(default=86400, ge=60, le=604800)
    generation_download_timeout: int = Field(default=60, ge=10, le=600)
    generation_max_response_bytes: int = Field(default=8 * 1024**2, ge=1024, le=64 * 1024**2)
    extraction_max_script_chars: int = Field(default=30000, ge=100, le=200000)
    extraction_max_candidates: int = Field(default=100, ge=1, le=100)
    extraction_max_output_tokens: int = Field(default=8192, ge=1024, le=32768)

    @property
    def rabbitmq_url(self) -> SecretStr:
        scheme = "amqps" if self.rabbitmq_tls else "amqp"
        user = quote(self.rabbitmq_user, safe="")
        password = quote(self.rabbitmq_password.get_secret_value(), safe="")
        vhost = quote(self.rabbitmq_vhost, safe="")
        host = self.rabbitmq_host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return SecretStr(f"{scheme}://{user}:{password}@{host}:{self.rabbitmq_port}/{vhost}")

    @field_validator("minio_image_bucket", "minio_video_bucket")
    @classmethod
    def valid_minio_bucket(cls, value: str) -> str:
        check_bucket_name(value, strict=True)
        return value

    @field_validator("minio_endpoint")
    @classmethod
    def valid_minio_endpoint(cls, value):
        if value is None:
            return value
        endpoint = urlsplit("//" + value)
        if (
            not endpoint.hostname
            or endpoint.username
            or endpoint.password
            or endpoint.path
            or endpoint.query
            or endpoint.fragment
            or any(c.isspace() for c in value)
        ):
            raise ValueError("MINIO_ENDPOINT must be a host with optional port, without a scheme")
        if endpoint.port is not None and not 1 <= endpoint.port <= 65535:
            raise ValueError("Invalid MinIO port")
        return value

    @model_validator(mode="after")
    def distinct_media_buckets(self):
        if self.minio_image_bucket == self.minio_video_bucket:
            raise ValueError("Image and video buckets must be different")
        return self

    @property
    def database_url(self) -> URL:
        return URL.create(
            "mysql+pymysql",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
            query={"charset": "utf8mb4"},
        )
