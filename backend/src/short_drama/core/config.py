from urllib.parse import quote, urlsplit

from minio.helpers import check_bucket_name
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    auth_enabled: bool = True
    auth_cookie_secure: bool = True
    public_origin: str = "https://localhost"
    auth_session_days: int = Field(default=7, ge=1, le=30)
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from: str = ""
    smtp_starttls: bool = True
    email_proof_key: SecretStr | None = None
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
    minio_presign_expiry: int = Field(default=300, ge=1, le=604800)
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
    audio_production_enabled: bool = False
    native_video_enabled: bool = False
    render_subtitle_font_path: str = ""
    render_subtitle_font_family: str = Field(
        default="Noto Sans CJK SC", min_length=1, max_length=100, pattern=r"^[^,\r\n]+$"
    )
    minio_audio_bucket: str = "short-drama-audio"
    generation_audio_budget_seconds: int = 300
    generation_batches_enabled: bool = False
    agent_enabled: bool = True
    agent_lease_seconds: int = Field(default=180, ge=150, le=3600)
    agent_poll_seconds: int = Field(default=5, ge=3, le=60)
    generation_batch_image_concurrency: int = Field(default=2, ge=1, le=8)
    generation_batch_video_concurrency: int = Field(default=1, ge=1, le=4)
    generation_download_timeout: int = Field(default=60, ge=10, le=600)
    generation_max_response_bytes: int = Field(default=8 * 1024**2, ge=1024, le=64 * 1024**2)
    extraction_max_script_chars: int = Field(default=30000, ge=100, le=200000)
    extraction_max_candidates: int = Field(default=100, ge=1, le=100)
    extraction_max_output_tokens: int = Field(default=8192, ge=1024, le=32768)
    render_ffmpeg_path: str = "ffmpeg"
    render_ffprobe_path: str = "ffprobe"
    render_scratch_root: str = ".runtime/renders"
    render_timeout_seconds: int = Field(default=3600, ge=30, le=86400)
    render_max_duration_ms: int = Field(default=3600000, ge=1000, le=86400000)
    render_max_source_bytes: int = Field(default=2 * 1024**3, ge=1024)
    render_max_scratch_bytes: int = Field(default=20 * 1024**3, ge=1024)

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

    @field_validator("minio_image_bucket", "minio_video_bucket", "minio_audio_bucket")
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
    def agent_requires_identity(self):
        if self.agent_enabled and not self.auth_enabled:
            raise ValueError("AGENT_ENABLED requires AUTH_ENABLED")
        return self

    @model_validator(mode="after")
    def distinct_media_buckets(self):
        if len({self.minio_image_bucket, self.minio_video_bucket, self.minio_audio_bucket}) != 3:
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
