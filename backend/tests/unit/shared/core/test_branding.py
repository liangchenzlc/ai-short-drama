import importlib
import json

import pytest


@pytest.fixture
def brand_package(tmp_path, monkeypatch):
    branding = importlib.import_module("short_drama.core.branding")
    original_files = branding.resources.files
    monkeypatch.setattr(
        branding.resources,
        "files",
        lambda package: (
            tmp_path if package == "short_drama.core.branding" else original_files(package)
        ),
    )
    branding.get_brand.cache_clear()
    config = {
        "name": "Paper Stage",
        "tagline": "另一种创作空间",
        "titles": {"app": "{name}", "canvas": "画布 · {name}"},
        "descriptions": {"app": "欢迎来到 {name}。", "canvas": "{name} 的画布。"},
        "assets": {
            "mark": "assets/mark.svg",
            "lockup": "assets/logo.svg",
            "icon": "assets/icon.svg",
        },
    }
    (tmp_path / "assets").mkdir()
    for path in config["assets"].values():
        (tmp_path / path).write_text("<svg/>", encoding="utf-8")
    yield branding, tmp_path, config
    branding.get_brand.cache_clear()


def write_brand(root, config):
    (root / "brand.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")


def test_brand_package_expands_independent_name_and_caches_the_validated_result(brand_package):
    branding, root, config = brand_package
    write_brand(root, config)

    brand = branding.get_brand()
    assert brand.name == "Paper Stage"
    assert brand.titles.app == "Paper Stage"
    assert brand.titles.canvas == "画布 · Paper Stage"
    assert brand.descriptions.app == "欢迎来到 Paper Stage。"
    assert brand.descriptions.canvas == "Paper Stage 的画布。"
    assert brand.assets.mark == "assets/mark.svg"

    (root / "brand.json").unlink()
    assert branding.get_brand() is brand


def test_independent_brand_reaches_application_and_assistant_consumers(brand_package):
    from short_drama.ai.prompts.registry import assistant_prompt
    from short_drama.core.config import Settings
    from short_drama.main import create_app

    _, root, config = brand_package
    write_brand(root, config)

    app = create_app(Settings(_env_file=None, auth_enabled=False, agent_enabled=False))

    assert app.openapi()["info"]["title"] == "Paper Stage"
    assert assistant_prompt("写一个场景").startswith("你是 Paper Stage 的通用创作助手。")


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        (None, "name", " "),
        (None, "tagline", ""),
        (None, "extra", "unsupported"),
        ("titles", "app", ""),
        ("titles", "unknown", "unsupported"),
        ("titles", "canvas", "{unknown}"),
        ("descriptions", "app", "{name!r}"),
        ("descriptions", "canvas", "{name:>20}"),
        ("titles", "app", "{name"),
        ("titles", "app", "{name:}"),
        ("titles", "app", "{{name}}"),
        ("assets", "mark", "../outside.svg"),
        ("assets", "mark", "/outside.svg"),
        ("assets", "mark", "C:/outside.svg"),
        ("assets", "mark", "assets\\mark.svg"),
        ("assets", "mark", "assets/missing.svg"),
    ],
)
def test_brand_rejects_invalid_fields_with_config_path(brand_package, section, field, value):
    branding, root, config = brand_package
    target = config[section] if section else config
    target[field] = value
    write_brand(root, config)

    with pytest.raises(RuntimeError, match=r"brand\.json"):
        branding.get_brand()


def test_brand_rejects_missing_required_fields(brand_package):
    branding, root, config = brand_package
    del config["assets"]["icon"]
    write_brand(root, config)

    with pytest.raises(RuntimeError, match=r"brand\.json"):
        branding.get_brand()


@pytest.mark.parametrize("content", [None, "{not JSON", "{}"])
def test_brand_reports_missing_or_malformed_configuration(brand_package, content):
    branding, root, _ = brand_package
    if content is not None:
        (root / "brand.json").write_text(content, encoding="utf-8")

    with pytest.raises(RuntimeError, match=r"brand\.json"):
        branding.get_brand()


def test_brand_reports_invalid_utf8_with_configuration_path(brand_package):
    branding, root, _ = brand_package
    (root / "brand.json").write_bytes(b"\xff")

    with pytest.raises(RuntimeError, match=r"brand\.json"):
        branding.get_brand()
