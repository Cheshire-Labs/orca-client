"""The README's quick start has to end with a bridge that works.

Two ways it did not. Its example device was named for nothing any topology
declares, so a reader who copied it got a bridge that connected, bound no
device, and reported success. And it told the reader to run `orca`, which this
package does not install, in the environment it had just built.
"""

import json
import re
from pathlib import Path

from orca_client.config import load_config
from orca_client.session import ClientSession

_REPO = Path(__file__).resolve().parent.parent
_README = _REPO / "README.md"


def _readme() -> str:
    return _README.read_text(encoding="utf-8")


def _example_config() -> dict:
    """The one JSON block in the README: the config a reader copies."""
    blocks = re.findall(r"```json\n(.*?)```", _readme(), re.S)
    assert len(blocks) == 1, f"expected one JSON example, found {len(blocks)}"
    return json.loads(blocks[0])


def _section(heading: str) -> str:
    for section in _readme().split("\n### "):
        if section.startswith(heading):
            return section
    return ""


def test_the_example_config_is_one_the_bridge_accepts(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(_example_config()))

    session = ClientSession(load_config(str(config_path)))

    assert session.registry.get_all_device_ids() == ["shaker_1"]


def test_every_example_device_is_shown_bound_to_a_topology_declaration() -> None:
    """A device binds by name, so a name no declaration carries binds nothing."""
    readme = _readme()

    for device in _example_config()["devices"]:
        declaration = f'("{device["name"]}")'
        assert declaration in readme, (
            f"the example device {device['name']!r} is shown bound to no topology "
            f"declaration, so a reader who copies it binds nothing"
        )


def test_the_runtime_is_started_from_its_own_environment() -> None:
    """`orca` is not on the PATH the install section builds."""
    runtime = _section("Running a local runtime")

    assert "orca start" in runtime
    assert re.search(r"(second|separate|its own) virtual environment", runtime), (
        "the section does not tell the reader the runtime needs its own environment"
    )


def test_the_bridge_does_not_install_the_runtime() -> None:
    """Depending on the runtime is the wrong way to make `orca start` work here.

    They are separate packages because they run on separate computers.
    """
    pyproject = (_REPO / "pyproject.toml").read_text(encoding="utf-8")
    declared = re.search(r"\ndependencies = \[(.*?)\]", pyproject, re.S)

    assert declared is not None
    assert "orca" not in declared.group(1)
