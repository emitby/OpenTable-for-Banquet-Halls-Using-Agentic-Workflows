"""CLAUDE.md rules, checked on the source itself.

* No ``os`` module anywhere (imports or ``os.`` calls).
* No file-deletion calls.
* A docstring on every module, class and function (including nested ones and tests).
* Every path is confined to the project, including ``..`` escapes and absolute paths.
* The web server binds to 127.0.0.1 only.
"""

import ast
import re
import sys

import pytest

from banquet.paths import PROJECT_DIR, PathError, confine
from banquet.serve import HOST

SOURCES = sorted(p for d in ("banquet", "tests", "evals") for p in (PROJECT_DIR / d).rglob("*.py"))


def test_sources_found():
    """The scans below actually see the project's Python files."""
    assert len(SOURCES) > 15


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.relative_to(PROJECT_DIR).as_posix())
def test_no_os_module(path):
    """No file imports ``os`` or calls ``os.something``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(a.name.split(".")[0] != "os" for a in node.names), path
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] != "os", path
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            assert node.value.id != "os", path


DELETION = re.compile(r"\.(unlink|rmdir)\(|\brmtree\b|\bos\.remove\b|\bshutil\.move\b")


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.relative_to(PROJECT_DIR).as_posix())
def test_no_deletion_calls(path):
    """No file contains a file-deletion call."""
    code = path.read_text(encoding="utf-8")
    if path.name == "test_project_guardrails.py":
        code = code.replace(DELETION.pattern, "")     # this file names the patterns it searches for
    assert not DELETION.search(code), path


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.relative_to(PROJECT_DIR).as_posix())
def test_every_module_class_and_function_has_a_docstring(path):
    """CLAUDE.md: docstrings on every module, class and function."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    missing = [] if ast.get_docstring(tree) else ["<module>"]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not ast.get_docstring(node):
            missing.append(f"{node.name} (line {node.lineno})")
    assert not missing, f"{path.name}: {missing}"


@pytest.mark.skipif(sys.version_info < (3, 12), reason="on 3.10/3.11 ast.parse in the scans above already catches this")
@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.relative_to(PROJECT_DIR).as_posix())
def test_no_backslash_in_fstring_expressions(path):
    """CI runs Python 3.10-3.12; before 3.12 an f-string's ``{...}`` part can't contain a backslash.

    Regression: the PDF guide passed locally on 3.12 but failed to parse in CI on 3.11.
    Python 3.12's tokenizer exposes f-string parts, so this catches it on 3.12 too.
    """
    import io
    import tokenize
    depth, bad = 0, []
    for tok in tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline):
        if tok.type == tokenize.FSTRING_START:
            depth += 1
        elif tok.type == tokenize.FSTRING_END:
            depth -= 1
        elif depth and tok.type == tokenize.STRING and "\\" in tok.string:
            bad.append(tok.start[0])
    assert not bad, f"{path.name}: backslash inside an f-string expression on line(s) {bad}"


def test_confine_allows_project_paths():
    """Relative and absolute paths inside the project are allowed, and ':memory:' passes through."""
    assert confine("data/x.db") == PROJECT_DIR / "data" / "x.db"
    assert confine(PROJECT_DIR / "runs") == PROJECT_DIR / "runs"
    assert confine(":memory:") == ":memory:"


@pytest.mark.parametrize("bad", ["../outside.txt", "data/../../outside.txt"])
def test_confine_rejects_escapes(bad):
    """``..`` escapes are refused on every OS."""
    with pytest.raises(PathError):
        confine(bad)


def test_confine_rejects_absolute_paths_elsewhere():
    """An absolute path at the drive or filesystem root is refused."""
    with pytest.raises(PathError):
        confine(PROJECT_DIR.anchor + "elsewhere.txt")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only path spelling")
def test_confine_handles_windows_long_path_prefix():
    """The Windows long-path prefix doesn't break the inside/outside comparison."""
    assert confine("\\\\?\\" + str(PROJECT_DIR / "data")) == PROJECT_DIR / "data"


def test_server_binds_to_loopback_only():
    """The launcher's server listens on 127.0.0.1, not on every interface."""
    assert HOST == "127.0.0.1"
