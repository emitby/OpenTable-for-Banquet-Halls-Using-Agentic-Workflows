"""Project paths and the confinement rule for every file this app writes.

CLAUDE.md requires that no code reads or writes outside the project folder, never uses
``os`` and never deletes files. This module is the single place that enforces it:

* :data:`PROJECT_DIR` is the folder containing ``pyproject.toml``.
* :func:`confine` resolves a path and refuses anything that lands outside the project.
  Every module that touches the filesystem (database, run folders, the PDF guide)
  passes its paths through here first.
* Writing helpers (:func:`write_text`, :func:`write_bytes`) write atomically: they
  write a sibling ``.tmp`` file and rename it over the target with ``Path.replace``,
  so a crash never leaves a half-written file and nothing is ever deleted.

No function here deletes anything.
"""

from pathlib import Path

#: The project root: this file lives in ``<root>/banquet/paths.py``.
PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data"
RUNS_DIR = PROJECT_DIR / "runs"
CACHE_DIR = PROJECT_DIR / ".cache"
DOCS_DIR = PROJECT_DIR / "docs"


class PathError(ValueError):
    """A path that resolves outside the project folder."""


def _strip_long_prefix(path):
    """Remove Windows' ``\\\\?\\`` long-path prefix so path comparisons are like for like.

    ``Path.resolve()`` can return ``\\\\?\\C:\\...`` for some paths on Windows. Without
    normalizing, a path inside the project could look like it's outside (and vice versa).

    Args:
        path: A resolved :class:`~pathlib.Path`.

    Returns:
        The same path without the prefix (unchanged on other systems).
    """
    text = str(path)
    return Path(text[4:]) if text.startswith("\\\\?\\") else path


def confine(path):
    """Resolve ``path`` (relative paths are taken from the project root) and check it's inside.

    Args:
        path: A ``str`` or :class:`~pathlib.Path`. The special SQLite name ``":memory:"``
            is passed through untouched, because it isn't a file.

    Returns:
        The absolute, resolved :class:`~pathlib.Path` (or ``":memory:"``).

    Raises:
        PathError: If the resolved path is outside :data:`PROJECT_DIR`. This includes
            ``..`` escapes, absolute paths elsewhere and symlinks pointing out.
    """
    if str(path) == ":memory:":
        return ":memory:"
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_DIR / p
    resolved = _strip_long_prefix(p.resolve())
    root = _strip_long_prefix(PROJECT_DIR)
    if resolved != root and root not in resolved.parents:
        raise PathError(f"Refusing to use {resolved}: it is outside the project folder {root}.")
    return resolved


def write_bytes(path, data):
    """Atomically write bytes to a confined path, creating parent folders as needed.

    Args:
        path: Target path (confined).
        data: Bytes to write.

    Returns:
        The resolved target path.

    Raises:
        PathError: If the path is outside the project.

    Side effects:
        Creates parent folders, writes ``<name>.tmp`` and renames it over the target.
    """
    target = confine(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(target)          # atomic rename; the .tmp file no longer exists afterwards
    return target


def write_text(path, text):
    """Atomically write UTF-8 text to a confined path. See :func:`write_bytes`."""
    return write_bytes(path, text.encode("utf-8"))
