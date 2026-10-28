"""Decode bounded, non-executable skill imports in memory; never extract to disk."""

import base64
import hashlib
import io
import json
import posixpath
import re
import stat
import unicodedata
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit
from zipfile import BadZipFile, ZipFile

import yaml
from pydantic import ValidationError
from yaml.events import AliasEvent

from short_drama.core.exceptions import WorkflowError
from short_drama.schemas.skills.catalog.skill import (
    IMAGE_SUFFIXES,
    MAX_PACKAGE_BYTES,
    MAX_PACKAGE_FILES,
    MAX_RESOURCE_BYTES,
    MAX_SKILL_BYTES,
    TEXT_SUFFIXES,
    SkillCreate,
    SkillImportDraft,
    SkillImportPreview,
    SkillResourceInput,
    bounded_text,
    resource_path,
)

MAX_IMPORT_BYTES = 4 * 1048576
MAX_FRONTMATTER_BYTES = 65536


def invalid_package(message: str) -> WorkflowError:
    return WorkflowError("invalid_agent_skill_package", message, 422)


class PackageLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(AliasEvent):
            raise invalid_package("Skill 元数据不支持 YAML 别名")
        return super().compose_node(parent, index)

    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise invalid_package("Skill 元数据的键须为不重复的字符串")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def text_content(data: bytes, maximum: int) -> str:
    if len(data) > maximum:
        raise invalid_package("Skill 文件超过允许的文本体积")
    try:
        return bounded_text(data.decode("utf-8-sig"), maximum)
    except (UnicodeDecodeError, ValueError):
        raise invalid_package("Skill 文件须为不含 NUL 的 UTF-8 文本") from None


def markdown_parts(content: str) -> tuple[dict, str]:
    content = content.strip()
    if not content:
        raise invalid_package("SKILL.md 必须包含正文")
    if not content.startswith("---\n") and not content.startswith("---\r\n"):
        return {}, content
    lines = content.splitlines(keepends=True)
    end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if end is None:
        raise invalid_package("Skill 元数据缺少结束分隔符")
    header = "".join(lines[1:end])
    if len(header.encode("utf-8")) > MAX_FRONTMATTER_BYTES:
        raise invalid_package("Skill 元数据不能超过 64 KiB")
    try:
        metadata = yaml.load(header, Loader=PackageLoader)
    except (yaml.YAMLError, RecursionError):
        raise invalid_package("Skill 元数据不是有效的受限 YAML") from None
    if not isinstance(metadata, dict):
        raise invalid_package("Skill 元数据须为对象")
    body = "".join(lines[end + 1 :]).strip()
    if not body:
        raise invalid_package("SKILL.md 必须包含正文")
    return metadata, body


def local_reference_issues(instructions: str, resources: list[SkillResourceInput]) -> list[str]:
    rows = [
        (item["path"], item.get("content", ""))
        if isinstance(item, dict)
        else (item.path, item.content)
        for item in resources
    ]
    available = {path for path, _ in rows} | {"SKILL.md"}
    issues = []
    for path, text in [("SKILL.md", instructions), *rows]:
        if PurePosixPath(path).suffix.lower() not in TEXT_SUFFIXES:
            continue
        for target in re.findall(r"\[[^\]]*\]\(([^\s)]+)(?:\s+[^)]*)?\)", text):
            target = target.strip("<>")
            try:
                external = bool(urlsplit(target).scheme)
            except ValueError:
                issues.append(f"{path} 含无法识别的引用：{target}")
                continue
            if external or target.startswith(("#", "//")):
                continue
            local = unquote(target.split("#", 1)[0])
            if not local:
                continue
            combined = posixpath.normpath(str(PurePosixPath(path).parent / local))
            if combined == "SKILL.md":
                continue
            try:
                normalized = resource_path(combined)
            except ValueError:
                issues.append(f"{path} 引用了不支持的本地路径：{target}")
            else:
                if normalized not in available:
                    issues.append(f"{path} 缺少引用文件：{normalized}")
    return list(dict.fromkeys(issues))


def package_digest(metadata: dict, instructions: str, resources: list[dict]) -> str:
    manifest = [
        {key: item[key] for key in ("path", "kind", "byte_size", "checksum_sha256")}
        for item in sorted(resources, key=lambda item: item["path"])
    ]
    value = {"metadata": metadata, "instructions": instructions, "resources": manifest}
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def platform_tool_issues(names: list[str]) -> list[str]:
    from short_drama.agent.tools.registry import REGISTRY

    return [f"平台未提供必需工具：{name}" for name in names if name not in REGISTRY]


def mode_tool_issues(names: list[str], modes: list[str]) -> list[str]:
    from short_drama.agent.tools.contracts import CANVAS_TOOL, IMAGE_PRIMARY_TOOL, STANDARD_TOOL

    supported = {
        CANVAS_TOOL: {"infinite_canvas"},
        STANDARD_TOOL: {"standard"},
        IMAGE_PRIMARY_TOOL: {"standard"},
    }
    return [
        "所选模式不支持这个方法需要的能力"
        for name in names
        if name in supported and not set(modes) <= supported[name]
    ][:1]


def package_markdown(metadata: dict, instructions: str, resources: list[dict]) -> str:
    extra = {
        "short-drama-display-name": metadata["name"],
        "short-drama-modes": ",".join(metadata["modes"]),
        "short-drama-tags": metadata["tags"],
        "short-drama-exclusions": metadata["exclusions"],
    }
    kinds = {
        item["path"]: item["kind"]
        for item in resources
        if item["kind"] != ("template" if item["path"].startswith("assets/") else "reference")
    }
    if kinds:
        extra["short-drama-resource-kinds"] = kinds
    header = dict(name=metadata["slug"], description=metadata["description"], metadata=extra)
    if metadata["required_tools"]:
        header["allowed-tools"] = metadata["required_tools"]
    if metadata["source"]:
        header["license"] = metadata["source"]
    return (
        "---\n"
        + yaml.safe_dump(header, allow_unicode=True, sort_keys=False)
        + "---\n\n"
        + instructions
        + "\n"
    )


def validate_package_size(metadata: dict, instructions: str, resources: list[dict]) -> None:
    main = package_markdown(metadata, instructions, resources)
    size = len(main.encode("utf-8"))
    if (
        size > MAX_RESOURCE_BYTES
        or size + sum(item["byte_size"] for item in resources) > MAX_PACKAGE_BYTES
    ):
        raise invalid_package("Skill 主文件连同元数据不能超过 128 KiB，完整文本包不能超过 1 MiB")


def _archive(data: bytes) -> dict[str, bytes]:
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if not 1 <= len(infos) <= MAX_PACKAGE_FILES * 2:
                raise invalid_package("ZIP 目录项过多或为空")
            files, seen, total = {}, set(), 0
            for info in infos:
                path = unicodedata.normalize("NFC", info.filename)
                parts = path.rstrip("/").split("/")
                if (
                    not path
                    or any(part in {"", ".", ".."} or part.endswith((".", " ")) for part in parts)
                    or any(ord(c) < 32 or c in "\\:%?#" for c in path)
                    or len(parts) > 7
                    or len(path.encode()) > 320
                    or path.casefold() in seen
                    or info.flag_bits & 1
                    or stat.S_IFMT(info.external_attr >> 16) not in {0, stat.S_IFREG, stat.S_IFDIR}
                ):
                    raise invalid_package("ZIP 含重复、越界、加密或非普通文件路径")
                seen.add(path.casefold())
                if info.is_dir():
                    continue
                total += info.file_size
                if len(files) >= MAX_PACKAGE_FILES or total > MAX_PACKAGE_BYTES:
                    raise invalid_package("ZIP 解压后不能超过 32 个文件或 1 MiB")
                if info.file_size > MAX_RESOURCE_BYTES:
                    raise invalid_package("ZIP 单文件不能超过 128 KiB")
                with archive.open(info) as source:
                    content = source.read(MAX_RESOURCE_BYTES + 1)
                if len(content) != info.file_size:
                    raise invalid_package("ZIP 文件体积与声明不一致")
                files[path] = content
            roots = [path for path in files if PurePosixPath(path).name == "SKILL.md"]
            if len(roots) != 1:
                raise invalid_package("一个 ZIP 必须且只能包含一个 SKILL.md")
            root = str(PurePosixPath(roots[0]).parent)
            if root == ".":
                return files
            if "/" in root or any(not path.startswith(root + "/") for path in files):
                raise invalid_package("SKILL.md 仅可位于根目录或一个共同外层目录")
            return {path[len(root) + 1 :]: value for path, value in files.items()}
    except (BadZipFile, NotImplementedError, RuntimeError, OSError):
        raise invalid_package("无法读取 Skill ZIP 包；请核对压缩格式和文件完整性") from None


def parse_skill_import(data: bytes, filename: str) -> SkillImportPreview:
    name = PurePosixPath(filename.replace("\\", "/")).name[:255]
    if not 0 < len(data) <= MAX_IMPORT_BYTES:
        raise invalid_package("导入文件须大于零且不超过 4 MiB")
    if name.lower().endswith(".md"):
        files = {"SKILL.md": data}
    elif name.lower().endswith(".zip"):
        files = _archive(data)
    else:
        raise invalid_package("仅支持 Markdown 和 ZIP Skill 包")
    metadata, instructions = markdown_parts(text_content(files["SKILL.md"], MAX_RESOURCE_BYTES))
    if len(instructions.encode("utf-8")) > MAX_SKILL_BYTES:
        raise invalid_package("Skill 主指令正文不能超过 64 KiB")
    extra = metadata.get("metadata", {})
    if not isinstance(extra, dict):
        raise invalid_package("metadata 必须为对象")
    if not metadata.get("name"):
        raise invalid_package("SKILL.md 缺少名称")
    if not metadata.get("description"):
        raise invalid_package("SKILL.md 缺少用途")
    raw_modes = extra.get("short-drama-modes", "standard,infinite_canvas")
    raw_tools = metadata.get("allowed-tools", [])
    if isinstance(raw_tools, str):
        raw_tools = re.split(r"[\s,]+", raw_tools.strip()) if raw_tools.strip() else []
    resources, issues, descriptions = [], [], []
    kinds = extra.get("short-drama-resource-kinds", {})
    if not isinstance(kinds, dict) or any(path not in files for path in kinds):
        raise invalid_package("资料类型声明须匹配包内真实路径")
    for path, content in files.items():
        if path == "SKILL.md":
            descriptions.append({"path": path, "byte_size": len(content), "supported": True})
            continue
        suffix = PurePosixPath(path).suffix.lower()
        if "scripts" in path.casefold().split("/") or kinds.get(path) == "script":
            raise invalid_package(f"暂不支持脚本：{path}")
        supported = suffix in TEXT_SUFFIXES | IMAGE_SUFFIXES
        descriptions.append({"path": path, "byte_size": len(content), "supported": supported})
        if not supported:
            raise invalid_package(f"暂不支持此文件：{path}")
        try:
            resources.append(
                SkillResourceInput(
                    path=path,
                    kind=kinds.get(
                        path,
                        "image"
                        if suffix in IMAGE_SUFFIXES
                        else "template"
                        if path.startswith("assets/")
                        else "reference",
                    ),
                    encoding="base64" if suffix in IMAGE_SUFFIXES else "utf-8",
                    content=base64.b64encode(content).decode()
                    if suffix in IMAGE_SUFFIXES
                    else text_content(content, MAX_RESOURCE_BYTES),
                )
            )
        except ValidationError:
            raise invalid_package(f"资料路径不符合受限格式：{path}") from None
    try:
        draft = SkillImportDraft(
            name=extra.get("short-drama-display-name")
            or metadata.get("name")
            or PurePosixPath(name).stem,
            slug=metadata["name"],
            description=metadata.get("description", ""),
            instructions=instructions,
            modes=raw_modes.split(",") if isinstance(raw_modes, str) else raw_modes,
            tags=extra.get("short-drama-tags", []),
            required_tools=raw_tools,
            resources=resources,
            source=metadata.get("license", ""),
            exclusions=extra.get("short-drama-exclusions", ""),
        )
        SkillCreate.model_validate(draft.model_dump())
    except ValidationError:
        raise invalid_package("Skill 名称、用途、适用模式或工具声明格式不正确") from None
    if metadata.get("compatibility"):
        issues.append("包声明了额外运行依赖，请适配为平台无脚本方法后发布")
    issues.extend(local_reference_issues(instructions, resources))
    issues.extend(platform_tool_issues(draft.required_tools))
    return SkillImportPreview(
        filename=name,
        draft=draft,
        compatible=not issues,
        issues=issues,
        files=descriptions,
        byte_size=sum(len(content) for content in files.values()),
        tags_provided="short-drama-tags" in extra,
        mode_issues={
            "general": mode_tool_issues(draft.required_tools, ["standard", "infinite_canvas"]),
            "standard": mode_tool_issues(draft.required_tools, ["standard"]),
            "infinite_canvas": mode_tool_issues(draft.required_tools, ["infinite_canvas"]),
        },
    )
