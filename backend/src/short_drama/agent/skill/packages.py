"""Deployment input only; accepted tasks always use database and object-store versions."""

import io
import json
from importlib.resources import files
from zipfile import ZIP_DEFLATED, ZipFile

from pydantic import BaseModel, ConfigDict, Field

from short_drama.schemas.skills.catalog.skill import SkillCreate
from short_drama.service.skills.packages.skill_package import parse_skill_import


class BuiltinSkillPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    content_version: int = Field(gt=0)
    capability: str
    content: SkillCreate


def builtin_skill_packages() -> list[BuiltinSkillPackage]:
    root = files("short_drama").joinpath("agent", "skill", "builtin_skill_data")
    catalog = json.loads(root.joinpath("catalog.json").read_text(encoding="utf-8"))
    result = []
    for item in catalog:
        package = root.joinpath(item["directory"])
        output = io.BytesIO()
        with ZipFile(output, "w", ZIP_DEFLATED) as archive:
            for name in ("SKILL.md", "references/method.md", "assets/output-template.md"):
                archive.writestr(name, package.joinpath(name).read_text(encoding="utf-8"))
            if item.get("upstream") == "drama-skills":
                archive.writestr(
                    "references/upstream-license.txt",
                    root.joinpath("LICENSE-drama-skills.txt").read_text(encoding="utf-8"),
                )
        preview = parse_skill_import(output.getvalue(), item["directory"] + ".zip")
        if not preview.compatible:
            raise ValueError(f"Builtin Skill {item['key']} is incompatible: {preview.issues}")
        result.append(
            BuiltinSkillPackage(
                key=item["key"],
                content_version=item["content_version"],
                capability=item["capability"],
                content=SkillCreate.model_validate(preview.draft.model_dump()),
            )
        )
    if len({item.key for item in result}) != len(result):
        raise ValueError("Builtin Skill identities must be unique")
    return result
