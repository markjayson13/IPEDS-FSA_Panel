"""Read verified FSA helper code without copying it or extending sys.path.

The selected checkout supplies parsing semantics, not linkage implementation.
Only the reviewed modules in the dependency lock may be loaded. Imports use
private module names and restore any existing public-module aliases afterward.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import types

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = REPO_ROOT / "Metadata/fsa_dependency_lock.json"
_configured_root = None
_loaded = {}
_guard = threading.RLock()


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def resolve_source_repo(source_repo=None, *, downstream_repo=None):
    """Explicit argument, configured selection, environment, then exact sibling.

    An invalid explicit selection fails; it never triggers a search through
    parents, the current working directory, PYTHONPATH, or an unrelated checkout.
    """
    selected = source_repo or _configured_root or os.environ.get("FSA_SOURCE_REPO")
    if selected is None:
        base = Path(downstream_repo or REPO_ROOT).resolve()
        selected = base.parent / "FSAVolumeReports_Panel-main"
    root = Path(selected).expanduser().resolve()
    if not (root / "Scripts/fsa_observations.py").is_file():
        raise FileNotFoundError(
            f"FSA source dependency missing at {root}. Set FSA_SOURCE_REPO or "
            "--fsa-source-repo to the reviewed FSAVolumeReports_Panel checkout."
        )
    return root


def dependency_receipt(source_repo=None):
    root = resolve_source_repo(source_repo)
    lock = json.loads(LOCK_PATH.read_text())
    actual = {}
    for relative, expected in lock["modules"].items():
        path = root / relative
        if not path.is_file() or sha256(path) != expected:
            raise ValueError(f"FSA dependency differs from reviewed content: {relative}")
        actual[relative] = expected
    return {"repository": str(root), "lock_sha256": sha256(LOCK_PATH), "modules": actual}


def configure_source_repo(source_repo=None):
    global _configured_root
    receipt = dependency_receipt(source_repo)
    _configured_root = Path(receipt["repository"])
    return receipt


def upstream_module(name, source_repo=None):
    """Load only locked helper code, bypassing ordinary path-based imports."""
    with _guard:
        receipt = dependency_receipt(source_repo)
        root = Path(receipt["repository"])
        relative = f"Scripts/{name}.py"
        if relative not in receipt["modules"]:
            raise ValueError(f"FSA module is not an approved dependency: {name}")
        key = (str(root), name, receipt["modules"][relative])
        if key in _loaded:
            return _loaded[key]
        aliases = {}
        if name == "fsa_build_utils":
            aliases["fsa_observations"] = upstream_module("fsa_observations", root)
        private_name = "_ipeds_fsa_dependency_" + hashlib.sha256(str(key).encode()).hexdigest()
        module = types.ModuleType(private_name)
        module.__file__ = str(root / relative)
        module.__package__ = ""
        missing = object()
        previous = {alias: sys.modules.get(alias, missing) for alias in aliases}
        sys.modules[private_name] = module
        try:
            sys.modules.update(aliases)
            # Compile bytes directly: importing never creates __pycache__ in FSA.
            source = (root / relative).read_bytes()
            if hashlib.sha256(source).hexdigest() != receipt["modules"][relative]:
                raise ValueError(f"FSA dependency changed while loading: {relative}")
            exec(compile(source, module.__file__, "exec"), module.__dict__)
        except BaseException:
            sys.modules.pop(private_name, None)
            raise
        finally:
            for alias, old in previous.items():
                if old is missing:
                    sys.modules.pop(alias, None)
                else:
                    sys.modules[alias] = old
        _loaded[key] = module
        return module


def require_separate_output(output, *readonly_roots):
    """Reject output inside, equal to, or above any read-only input root."""
    target = Path(output).expanduser().resolve()
    for source in readonly_roots:
        if source is None:
            continue
        root = Path(source).expanduser().resolve()
        if target == root or target.is_relative_to(root) or root.is_relative_to(target):
            raise ValueError(f"Output must be separate from read-only input: {root}")
    return target
