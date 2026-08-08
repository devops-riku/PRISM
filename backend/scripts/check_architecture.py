"""Offline guard for PRISM's clean feature-first DDD architecture."""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

APP = BACKEND / "app"
CANONICAL_ROOTS = (APP / "features", APP / "shared")
FEATURES = {
    "documents", "intakes", "jobs", "notifications", "platform",
    "quotations", "rendering", "team", "workspaces",
}
APP_ROOT_FILES = {"__init__.py", "main.py"}
APP_ROOT_DIRECTORIES = {"features", "shared"}
REMOVED_NAMES = {
    "api", "application", "domain", "infrastructure", "presentation", "renderers",
    "attachments.py", "auth.py", "clientview.py", "config.py", "costing.py",
    "design.py", "documents.py", "gemini_service.py", "hub.py", "inbox.py",
    "intakefiles.py", "intakes.py", "jobs.py", "kinds.py", "mailer.py",
    "members.py", "payments.py", "policies.py", "prompts.py", "ratecard.py",
    "reference.py", "schemas.py", "settings.py", "storage.py", "template.py",
    "tokens.py", "workspaces.py",
}
HTTP_OPERATION_HASH = "aee4dbf61b3b5c778c73ac70f3f92b645100acbf6abf3ef2526996bc38c13916"
OPENAPI_HASH = "80d59b548f8c5c49eaab2a5c9ef0d28cd9778844f5b8cf5c5b78057c7845718c"


def imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return found


def assert_clean_root() -> None:
    files = {path.name for path in APP.iterdir() if path.is_file()}
    directories = {
        path.name for path in APP.iterdir()
        if path.is_dir() and path.name != "__pycache__"
    }
    assert files == APP_ROOT_FILES, f"unexpected app root files: {files ^ APP_ROOT_FILES}"
    assert directories == APP_ROOT_DIRECTORIES, (
        f"unexpected app root directories: {directories ^ APP_ROOT_DIRECTORIES}"
    )
    assert not any((APP / name).exists() for name in REMOVED_NAMES)


def assert_feature_tree() -> None:
    present = {path.name for path in (APP / "features").iterdir() if path.is_dir() and path.name != "__pycache__"}
    assert present == FEATURES, f"feature folders differ: expected {FEATURES}, got {present}"


def assert_boundaries() -> None:
    failures: list[str] = []
    for root in CANONICAL_ROOTS:
        for path in sorted(root.rglob("*.py")):
            relative = path.relative_to(BACKEND)
            parts = set(path.relative_to(APP).parts[:-1])
            layer = next((name for name in ("domain", "application", "infrastructure", "presentation") if name in parts), "")
            for imported in imports(path):
                if layer == "domain" and (
                    any(token in imported.split(".") for token in ("application", "infrastructure", "presentation"))
                    or imported == "fastapi" or imported.startswith(("fastapi.", "starlette."))
                ):
                    failures.append(f"{relative} has outward domain dependency {imported}")
                if layer in {"application", "infrastructure"} and (
                    "presentation" in imported.split(".")
                    or imported == "fastapi" or imported.startswith(("fastapi.", "starlette."))
                ):
                    failures.append(f"{relative} has transport dependency {imported}")
                if root == APP / "shared" and layer == "infrastructure" and imported.startswith("app.features."):
                    failures.append(f"{relative} shared infrastructure depends on feature {imported}")
    if failures:
        raise AssertionError("Architecture violations:\n" + "\n".join(failures))


def assert_config_paths() -> None:
    from app.shared.infrastructure import config

    assert config.APP_DIR == APP
    assert config.BACKEND_DIR == BACKEND


def assert_http_contract() -> None:
    from app.main import app

    schema = app.openapi()
    methods = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}
    operations = sorted(
        (path, method.upper())
        for path, item in schema.get("paths", {}).items()
        for method in item
        if method.lower() in methods
    )
    operation_blob = json.dumps(operations, separators=(",", ":")).encode()
    assert len(schema.get("paths", {})) == 42
    assert len(operations) == 52
    assert hashlib.sha256(operation_blob).hexdigest() == HTTP_OPERATION_HASH

    schema_blob = json.dumps(
        schema, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    assert hashlib.sha256(schema_blob).hexdigest() == OPENAPI_HASH


if __name__ == "__main__":
    assert_clean_root()
    assert_feature_tree()
    assert_boundaries()
    assert_config_paths()
    assert_http_contract()
    print("architecture check passed: clean feature root, DDD boundaries, and HTTP contract")
