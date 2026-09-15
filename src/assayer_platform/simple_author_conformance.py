"""Static, fail-closed gate for ordinary Simple SDK Python source.

Ordinary author code is executable domain policy, but importing it must never be
the first validation step.  This module deliberately accepts a small Python
surface before the compiler imports ``plugin.py``.  It is a determinism and
dependency-boundary gate, not a general-purpose Python sandbox.
"""

from __future__ import annotations

import ast
from pathlib import Path

from assayer_plugin_sdk.contract import PlatformContractError


_SIMPLE_MODULE = "assayer_plugin_sdk.simple"
_SIMPLE_EXPORTS = frozenset({
    "policy_plugin", "Document", "Candidate", "Fact",
    "Relation", "Support", "Unknown", "invariant",
})
_PURE_CALLS = frozenset({
    "abs", "all", "any", "bool", "dict", "enumerate", "filter", "float",
    "int", "isinstance", "iter", "len", "list", "map", "max", "min",
    "next", "range", "repr", "reversed", "round", "set", "sorted", "str",
    "sum", "tuple", "zip",
})
_FORBIDDEN_NAMES = frozenset({
    "__builtins__", "__import__", "breakpoint", "compile", "delattr", "dir",
    "eval", "exec", "getattr", "globals", "input", "locals", "memoryview",
    "open", "setattr", "type", "vars",
    "asyncio", "datetime", "httpx", "importlib", "io", "multiprocessing",
    "os", "pathlib", "random", "requests", "secrets", "shutil", "socket",
    "subprocess", "sys", "tempfile", "threading", "time", "urllib", "uuid",
})
_FORBIDDEN_ATTRIBUTES = frozenset({
    "absolute", "chdir", "connect", "cwd", "environ", "expanduser", "fork",
    "getenv", "glob", "home", "mkdir", "monotonic", "now", "open", "popen",
    "read_bytes", "read_text", "remove", "rename", "replace", "request",
    "resolve", "rglob", "rmdir", "run", "spawn", "system", "time", "today",
    "touch", "unlink", "urlopen", "utcnow", "uuid4", "write_bytes",
    "write_text",
})


def _location(node: ast.AST) -> str:
    return f"/plugin.py:{getattr(node, 'lineno', 1)}:{getattr(node, 'col_offset', 0) + 1}"


def _reject(node: ast.AST, message: str) -> None:
    raise PlatformContractError(
        "SIMPLE_AUTHOR_CODE_FORBIDDEN",
        message,
        errors=({
            "pointer": _location(node),
            "keyword": "ordinaryAuthorSurface",
            "message": message,
        },),
    )


def _decorator_name(node: ast.expr) -> str | None:
    target = node.func if isinstance(node, ast.Call) else node
    return target.id if isinstance(target, ast.Name) else None


def _literal_decorator(node: ast.expr) -> None:
    if not isinstance(node, ast.Call):
        return
    for value in (*node.args, *(item.value for item in node.keywords)):
        try:
            ast.literal_eval(value)
        except (TypeError, ValueError):
            _reject(
                value,
                "Simple SDK decorator arguments must be literal domain declarations",
            )


def _safe_annotation(node: ast.expr, imported: dict[str, str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in imported or node.id in {
            "bool", "bytes", "dict", "float", "int", "list", "object", "set",
            "str", "tuple",
        }
    if isinstance(node, ast.Constant):
        return node.value is None
    if isinstance(node, ast.Subscript):
        return _safe_annotation(node.value, imported) and _safe_annotation(node.slice, imported)
    if isinstance(node, ast.Tuple):
        return all(_safe_annotation(item, imported) for item in node.elts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _safe_annotation(node.left, imported) and _safe_annotation(node.right, imported)
    return False


class _SimpleAuthorVisitor(ast.NodeVisitor):
    def __init__(self, imported: dict[str, str]) -> None:
        self.imported = imported
        self.class_depth = 0
        self.function_depth = 0

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        _reject(
            node,
            "Use explicit names from assayer_plugin_sdk.simple in ordinary plugins",
        )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if node.level or node.module not in {_SIMPLE_MODULE, "__future__"}:
            _reject(node, "Ordinary plugins may import only assayer_plugin_sdk.simple")
        if node.module == "__future__":
            if tuple(item.name for item in node.names) != ("annotations",):
                _reject(node, "Only future annotations may be enabled in ordinary plugins")
            return
        if any(item.name == "*" or item.name not in _SIMPLE_EXPORTS for item in node.names):
            _reject(node, "Simple imports must name only the eight public author concepts")

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        if self.class_depth or self.function_depth:
            _reject(node, "Nested classes are outside the ordinary plugin surface")
        if node.bases or node.keywords:
            _reject(node, "A Simple plugin class cannot inherit from custom runtime types")
        policy_name = next(
            (local for local, original in self.imported.items() if original == "policy_plugin"),
            None,
        )
        if len(node.decorator_list) != 1 or _decorator_name(node.decorator_list[0]) != policy_name:
            _reject(node, "The ordinary plugin class requires exactly one policy_plugin decorator")
        _literal_decorator(node.decorator_list[0])
        self.class_depth += 1
        try:
            for item in node.body:
                if isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant) and isinstance(item.value.value, str):
                    continue
                if isinstance(item, ast.Pass):
                    continue
                if not isinstance(item, ast.FunctionDef):
                    _reject(item, "Simple plugin classes may contain only domain methods")
                self.visit(item)
        finally:
            self.class_depth -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        if not self.class_depth or self.function_depth:
            _reject(node, "Ordinary plugins cannot declare module or nested functions")
        if node.name.startswith("_"):
            _reject(node, "Private and lifecycle methods are outside the Simple SDK surface")
        invariant_name = next(
            (local for local, original in self.imported.items() if original == "invariant"),
            None,
        )
        decorators = tuple(_decorator_name(item) for item in node.decorator_list)
        if node.name in {"scan", "relations"}:
            if decorators:
                _reject(node, "scan and relations cannot install custom decorators")
        elif decorators != (invariant_name,):
            _reject(node, "Additional Simple plugin methods must declare exactly one invariant")
        for decorator in node.decorator_list:
            _literal_decorator(decorator)
        for default in (*node.args.defaults, *(item for item in node.args.kw_defaults if item is not None)):
            try:
                ast.literal_eval(default)
            except (TypeError, ValueError):
                _reject(default, "Simple plugin method defaults must be literal values")
        annotations = tuple(
            item.annotation
            for item in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
            if item.annotation is not None
        ) + ((node.returns,) if node.returns is not None else ())
        for annotation in annotations:
            if not _safe_annotation(annotation, self.imported):
                _reject(
                    annotation,
                    "Simple plugin annotations must be non-executable type expressions",
                )
        self.function_depth += 1
        try:
            for item in node.body:
                self.visit(item)
        finally:
            self.function_depth -= 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        _reject(node, "Async execution is outside the deterministic Simple SDK surface")

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        _reject(node, "Dynamic callable construction is outside the Simple SDK surface")

    def visit_Global(self, node: ast.Global) -> None:  # noqa: N802
        _reject(node, "Simple plugin methods cannot mutate module state")

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:  # noqa: N802
        _reject(node, "Simple plugin methods cannot mutate enclosing state")

    def visit_With(self, node: ast.With) -> None:  # noqa: N802
        _reject(node, "Context-managed external resources are outside the Simple SDK surface")

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:  # noqa: N802
        _reject(node, "Async external resources are outside the Simple SDK surface")

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if isinstance(node.ctx, ast.Load) and node.id in _FORBIDDEN_NAMES:
            _reject(node, f"Forbidden runtime access in ordinary plugin: {node.id}")

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        if node.attr.startswith("_") or node.attr in _FORBIDDEN_ATTRIBUTES:
            _reject(node, f"Forbidden runtime attribute in ordinary plugin: {node.attr}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if isinstance(node.func, ast.Name):
            allowed = set(self.imported) | set(_PURE_CALLS)
            if node.func.id not in allowed:
                _reject(node, f"Call is outside the deterministic Simple SDK surface: {node.func.id}")
        self.generic_visit(node)


def validate_simple_author_source(path: str | Path) -> None:
    """Reject ordinary author source that can cross the declared Simple boundary."""
    source_path = Path(path)
    try:
        source = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise PlatformContractError(
            "INVALID_SIMPLE_PLUGIN", "plugin.py must be readable UTF-8 source",
        ) from error
    try:
        tree = ast.parse(source, filename=str(source_path))
    except SyntaxError as error:
        raise PlatformContractError(
            "INVALID_SIMPLE_PLUGIN",
            f"plugin.py contains invalid Python syntax: {error.msg}",
            errors=({
                "pointer": f"/plugin.py:{error.lineno or 1}:{error.offset or 1}",
                "keyword": "syntax",
                "message": error.msg,
            },),
        ) from error

    imported: dict[str, str] = {}
    classes = 0
    for item in tree.body:
        if isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant) and isinstance(item.value.value, str):
            continue
        if isinstance(item, ast.ImportFrom):
            if item.module == _SIMPLE_MODULE and not item.level:
                imported.update((alias.asname or alias.name, alias.name) for alias in item.names)
            continue
        if isinstance(item, ast.Import):
            continue
        if isinstance(item, ast.ClassDef):
            classes += 1
            continue
        _reject(item, "plugin.py may contain only Simple imports and one policy plugin class")
    if classes != 1:
        _reject(tree, "plugin.py must contain exactly one policy plugin class")
    _SimpleAuthorVisitor(imported).visit(tree)


__all__ = ["validate_simple_author_source"]
