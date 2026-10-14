"""Tools a harness defines for its agents. One Python file per tool, in the harness's tools/ folder.

A tool is a plain function with type hints and a docstring. The file name is the tool name.
Strands reads the hints and the docstring to tell the model what the tool does and when to call it.
An agent spec lists the tools that agent may use.

These files are code you or your team wrote. They run on your machine, inside the agent's
process, when an agent calls them during a live build.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

TEMPLATE = '''def {name}(value: float, factor: float = 1.0) -> dict:
    """Say in one sentence what this computes and when an agent should call it."""
    return {{"result": round(value * factor, 3)}}
'''


def valid_name(name: str) -> bool:
    return bool(re.fullmatch(r"[a-z][a-z0-9_]{1,39}", name))


def inspect_code(name: str, code: str) -> dict:
    """Read a tool's source without running it. Returns what the interface shows, and any problem."""
    info = {"name": name, "code": code, "doc": "", "signature": "", "error": ""}
    if not valid_name(name):
        info["error"] = "A tool name is lowercase letters, digits, and underscores, and starts with a letter."
        return info
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        info["error"] = f"Python could not read this: {exc.msg} on line {exc.lineno}"
        return info
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name), None)
    if fn is None:
        info["error"] = f"The file must define a function named {name}."
        return info
    info["doc"] = (ast.get_docstring(fn) or "").strip().splitlines()[0] if ast.get_docstring(fn) else ""
    args = [a.arg + (": " + ast.unparse(a.annotation) if a.annotation else "") for a in fn.args.args]
    info["signature"] = f"{name}({', '.join(args)})" + (f" -> {ast.unparse(fn.returns)}" if fn.returns else "")
    if not info["doc"]:
        info["error"] = "Add a docstring. The model reads it to decide when to call the tool."
    elif any(a.annotation is None for a in fn.args.args):
        info["error"] = "Give every argument a type hint, such as value: float."
    return info


def list_tools(root: Path) -> list[dict]:
    folder = Path(root) / "tools"
    return [inspect_code(f.stem, f.read_text(encoding="utf-8")) for f in sorted(folder.glob("*.py"))] if folder.exists() else []


def load_tools(root: Path, names: list[str]) -> list:
    """Turn the named tool files into Strands tools. A tool with a problem is skipped, not fatal."""
    from strands import tool
    loaded = []
    for name in names:
        file = Path(root) / "tools" / f"{name}.py"
        if not file.exists() or inspect_code(name, file.read_text(encoding="utf-8"))["error"]:
            continue
        namespace: dict = {"__name__": f"harness_tool_{name}"}
        exec(compile(file.read_text(encoding="utf-8"), str(file), "exec"), namespace)
        loaded.append(tool(namespace[name]))
    return loaded


def save_tools(root: Path, tools: list[dict]) -> list[Path]:
    """Write the tool files a draft holds, and remove the ones the person deleted."""
    folder = Path(root) / "tools"
    folder.mkdir(parents=True, exist_ok=True)
    names, written = set(), []
    for t in tools:
        info = inspect_code(t.get("name", ""), t.get("code", ""))
        if info["error"]:
            raise ValueError(f"Tool '{t.get('name', '')}': {info['error']}")
        if info["name"] in names:
            raise ValueError(f"Two tools are named '{info['name']}'.")
        names.add(info["name"])
        file = folder / f"{info['name']}.py"
        file.write_text(t["code"].rstrip() + "\n", encoding="utf-8")
        written.append(file)
    for file in folder.glob("*.py"):
        if file.stem not in names:
            file.unlink()
    return written
