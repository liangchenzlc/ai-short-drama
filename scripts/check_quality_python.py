"""Extract Python import dependencies with the stdlib parser, including local imports."""

import ast
import json
import sys
from pathlib import Path


def scan(root: Path) -> dict:
    source = root / "backend/src"
    files = sorted((source / "short_drama").rglob("*.py"))
    modules = {}
    for file in files:
        parts = file.relative_to(source).with_suffix("").parts
        name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        modules[name] = file
    packages = {name for name, file in modules.items() if file.name == "__init__.py"}
    imports = []
    bans = []
    metrics = {}
    for name, file in modules.items():
        text = file.read_text(encoding="utf-8-sig")
        tree = ast.parse(text, filename=str(file))
        package = name if file.name == "__init__.py" else name.rpartition(".")[0]
        rel = file.relative_to(root).as_posix()
        size = {"lines": len(text.split("\n")), "maxClass": 0, "maxFn": 0, "params": 0}
        metrics[rel] = size
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                size["maxClass"] = max(size["maxClass"], node.end_lineno - node.lineno + 1)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                size["maxFn"] = max(size["maxFn"], node.end_lineno - node.lineno + 1)
                args = node.args
                size["params"] = max(
                    size["params"],
                    len(args.posonlyargs)
                    + len(args.args)
                    + len(args.kwonlyargs)
                    + bool(args.vararg)
                    + bool(args.kwarg),
                )
            targets = set()
            if isinstance(node, ast.Import):
                targets.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    parts = package.split(".")
                    base = ".".join(parts[: len(parts) - node.level + 1])
                    base = ".".join(filter(None, (base, node.module)))
                else:
                    base = node.module or ""
                if base in modules and base != name:
                    targets.add(base)
                for alias in node.names:
                    child = f"{base}.{alias.name}"
                    if child in modules:
                        targets.add(child)
                # Record external/layer imports even when the target is not in this checkout.
                if not targets and base != name:
                    targets.add(base)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"
            ):
                bans.append({"rule": "调试输出 print", "file": rel, "line": node.lineno})
            for target in tuple(targets):
                parts = target.split(".")
                for end in range(1, len(parts)):
                    parent = ".".join(parts[:end])
                    # Importer ancestors are initializing already; newly entered packages run here.
                    if (
                        parent in packages
                        and parent != package
                        and not package.startswith(f"{parent}.")
                    ):
                        targets.add(parent)
            for target in sorted(targets):
                imports.append(
                    {
                        "file": rel,
                        "target": modules[target].relative_to(root).as_posix()
                        if target in modules
                        else None,
                        "module": target,
                        "line": node.lineno,
                    }
                )
    return {"imports": imports, "bans": bans, "metrics": metrics}


if __name__ == "__main__":
    print(json.dumps(scan(Path(sys.argv[1]).resolve()), ensure_ascii=True))
