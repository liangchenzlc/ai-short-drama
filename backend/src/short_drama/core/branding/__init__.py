"""Validated, process-cached brand resources shared by backend consumers."""

from functools import cache
from importlib import resources

from pydantic import BaseModel, ConfigDict, ValidationError, ValidationInfo, field_validator


class _BrandFields(BaseModel):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, str_min_length=1, frozen=True
    )


class BrandTemplates(_BrandFields):
    app: str
    canvas: str

    @field_validator("*")
    @classmethod
    def validate_template(cls, value: str) -> str:
        remaining = value.replace("{name}", "")
        if "{" in remaining or "}" in remaining:
            raise ValueError("Brand templates only support {name}")
        return value


class BrandAssets(_BrandFields):
    mark: str
    lockup: str
    icon: str

    @field_validator("*")
    @classmethod
    def validate_asset(cls, value: str, info: ValidationInfo) -> str:
        parts = value.split("/")
        if "\\" in value or ":" in value or any(part in {"", ".", ".."} for part in parts):
            raise ValueError("Brand assets must use paths relative to the brand package")
        if not info.context["resources"].joinpath(*parts).is_file():
            raise ValueError(f"Brand asset not found: {value}")
        return value


class Brand(_BrandFields):
    name: str
    tagline: str
    titles: BrandTemplates
    descriptions: BrandTemplates
    assets: BrandAssets


@cache
def get_brand() -> Brand:
    root = resources.files("short_drama.core.branding")
    config_path = root.joinpath("brand.json")
    try:
        brand = Brand.model_validate_json(
            config_path.read_text(encoding="utf-8"), context={"resources": root}
        )
    except (OSError, UnicodeError, ValidationError) as exc:
        raise RuntimeError(f"Invalid brand configuration {config_path}: {exc}") from exc
    return brand.model_copy(
        update={
            section: getattr(brand, section).model_copy(
                update={
                    key: value.replace("{name}", brand.name)
                    for key, value in getattr(brand, section).model_dump().items()
                }
            )
            for section in ("titles", "descriptions")
        }
    )
