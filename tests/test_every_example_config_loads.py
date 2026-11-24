"""Every config in examples/ loads.

An example is copied by people setting up a client. One that does not load fails at their first boot. Devices are not
in it: the server assigns them from each device's setup file.
"""

import re
from pathlib import Path

import pytest

from orca_client.config import load_config

EXAMPLES = sorted((Path(__file__).resolve().parent.parent / "examples").glob("*.json"))


def test_there_are_examples_to_check() -> None:
    assert EXAMPLES


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_every_example_loads(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in re.findall(r"[$][{](\w+)[}]", path.read_text(encoding="utf-8")):
        monkeypatch.setenv(name, "set-by-the-test")

    assert load_config(str(path)).client_id
