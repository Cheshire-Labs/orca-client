"""Guard: orca-client/src/ never imports pylabrobot directly.

PLR is a transitive dependency of cheshire-drivers only. Anything
orca-client needs from PLR (backend lookups, concrete types) must be
imported via `cheshire_drivers.plr`.

A failure here means a new direct PLR import slipped into orca-client/src/.
Either route it through cheshire-drivers, or extend cheshire_drivers.plr
to re-export the symbol.
"""

import re
from pathlib import Path

import pytest

from cheshire_source_text import CodeLine, code_lines, code_lines_of, imports_rooted_at, python_files


SRC_ROOT = Path(__file__).parent.parent / "src"

_PLR_ROOT = frozenset({"pylabrobot"})

# A module named as a string is the same coupling as an import statement, so
# the scan that finds these reads literals rather than blanking them.
_DYNAMIC_IMPORT = re.compile(
    r"""(__import__|importlib\.import_module)\s*\(\s*(['"])(pylabrobot(?:\.[\w.]+)?)\2"""
)


def _static_plr_imports(lines: list[CodeLine]) -> list[str]:
    return [found.rendered() for found in imports_rooted_at(lines, _PLR_ROOT)]


def _dynamic_plr_imports(lines: list[CodeLine]) -> list[str]:
    return [
        f"line {line.lineno}: {caller}({module!r}, ...)"
        for line in lines
        for caller, _, module in _DYNAMIC_IMPORT.findall(line.text)
    ]


def _direct_plr_imports_in_source(source: str) -> list[str]:
    """Return rendered source lines that import pylabrobot directly.

    Catches both static `from/import` statements AND dynamic string-based
    `__import__('pylabrobot...')` / `importlib.import_module('pylabrobot...')`
    calls, since either is a direct PLR coupling.
    """
    return (
        _static_plr_imports(code_lines(source))
        + _dynamic_plr_imports(code_lines(source, keep_strings=True))
    )


def _direct_plr_imports(path: Path) -> list[str]:
    return (
        _static_plr_imports(code_lines_of(path))
        + _dynamic_plr_imports(code_lines_of(path, keep_strings=True))
    )


@pytest.mark.parametrize("path", python_files(SRC_ROOT), ids=lambda p: str(p.relative_to(SRC_ROOT)))
def test_no_direct_pylabrobot_imports(path: Path) -> None:
    hits = _direct_plr_imports(path)
    assert not hits, (
        f"{path.relative_to(SRC_ROOT)} imports pylabrobot directly:\n"
        + "\n".join(f"  {h}" for h in hits)
        + "\n\nRoute the import through cheshire_drivers.plr instead "
        + "(re-export the symbol there if it is not already exposed)."
    )


@pytest.mark.parametrize(
    "snippet",
    [
        "import pylabrobot",
        "import pylabrobot.resources",
        "import pylabrobot as plr",
        "import os, pylabrobot",
        "from pylabrobot import resources",
        "from pylabrobot.liquid_handling import LiquidHandler",
        "__import__('pylabrobot.resources')",
        'importlib.import_module("pylabrobot")',
    ],
)
def test_scanner_flags_direct_pylabrobot_import(snippet: str) -> None:
    """Positive control: the per-file guard passes vacuously on clean source,
    so a silently broken scanner would never be caught by it. Each snippet
    carries one direct pylabrobot coupling and must produce a hit."""
    hits = _direct_plr_imports_in_source(snippet)
    assert hits, f"scanner failed to flag direct import: {snippet!r}"


@pytest.mark.parametrize(
    "snippet",
    [
        "from cheshire_drivers.plr import LiquidHandler",
        "import cheshire_drivers.plr as plr",
        "x = 'pylabrobot'",
        "import pylabrobotics",
        "from pylabrobotics import thing",
        "# import pylabrobot",
        "importlib.import_module('cheshire_drivers.plr')",
    ],
)
def test_scanner_ignores_legitimate_imports(snippet: str) -> None:
    """Negative control: re-exported (`cheshire_drivers.plr`) and unrelated
    imports, a bare string literal, a comment, and a same-prefix package
    (`pylabrobotics`) must not be flagged. Guards against an over-eager scanner
    that would also make the per-file guard meaningless by failing on clean
    code."""
    hits = _direct_plr_imports_in_source(snippet)
    assert not hits, f"scanner false-positived on: {snippet!r} -> {hits}"
