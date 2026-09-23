"""config.json fields the loader must actually carry onto ClientConfig."""

import json
from pathlib import Path

import pytest
from pydantic import JsonValue

from orca_client.config import load_config


def _config_path(tmp_path: Path, **overrides: JsonValue) -> Path:
    data: dict[str, JsonValue] = {
        "client_id": "bench-01",
        "site": "test",
        "lab": "simulation",
        "platform": {"url": "wss://orca.example.com/ws/devices", "api_key": "k"},
        "devices": [],
    }
    data.update(overrides)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    return path


def test_enable_plr_visualizer_is_read_from_the_file(tmp_path: Path):
    """It drives DeviceFactory, so dropping it silently disables the visualizer."""
    config = load_config(str(_config_path(tmp_path, enable_plr_visualizer=True)))

    assert config.enable_plr_visualizer is True


def test_enable_plr_visualizer_defaults_off_when_absent(tmp_path: Path):
    assert load_config(str(_config_path(tmp_path))).enable_plr_visualizer is False


def test_workcell_is_optional(tmp_path: Path):
    assert load_config(str(_config_path(tmp_path))).workcell is None
    assert load_config(str(_config_path(tmp_path, workcell="cell-a"))).workcell == "cell-a"


def test_the_platform_falls_back_to_the_orca_client_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("ORCA_CLIENT_URL", "ws://127.0.0.1:8765/ws/devices")
    monkeypatch.setenv("ORCA_CLIENT_API_KEY", "from-env")
    monkeypatch.setenv("ORCA_CLIENT_RECONNECT_BACKOFF", "1,5")
    monkeypatch.setenv("ORCA_CLIENT_HEARTBEAT_INTERVAL", "7")

    platform = load_config(str(_config_path(tmp_path, platform={}))).platform

    assert platform.url == "ws://127.0.0.1:8765/ws/devices"
    assert platform.api_key == "from-env"
    assert platform.reconnect_backoff == [1.0, 5.0]
    assert platform.heartbeat_interval == 7.0


def test_a_missing_url_names_the_variable_to_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ORCA_CLIENT_URL", raising=False)

    with pytest.raises(ValueError, match="ORCA_CLIENT_URL"):
        load_config(str(_config_path(tmp_path, platform={"api_key": "k"})))


def test_a_placeholder_for_an_unset_variable_is_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Left as literal text, the placeholder would go out as the API key and be rejected."""
    monkeypatch.delenv("NOT_SET_ANYWHERE", raising=False)
    platform: dict[str, JsonValue] = {"url": "ws://127.0.0.1:8765/ws/devices", "api_key": "${NOT_SET_ANYWHERE}"}

    with pytest.raises(ValueError, match="NOT_SET_ANYWHERE"):
        load_config(str(_config_path(tmp_path, platform=platform)))


def test_a_placeholder_for_a_set_variable_is_expanded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("ORCA_CLIENT_API_KEY", "secret")
    platform: dict[str, JsonValue] = {"url": "ws://127.0.0.1:8765/ws/devices", "api_key": "${ORCA_CLIENT_API_KEY}"}

    assert load_config(str(_config_path(tmp_path, platform=platform))).platform.api_key == "secret"


@pytest.mark.parametrize(
    ("variable", "text"),
    [("ORCA_CLIENT_RECONNECT_BACKOFF", "1,2,"), ("ORCA_CLIENT_HEARTBEAT_INTERVAL", "abc")],
)
def test_a_malformed_seconds_variable_is_refused_by_name(
    variable: str, text: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Otherwise the agent stops on "could not convert string to float" with no hint which setting."""
    monkeypatch.setenv(variable, text)

    with pytest.raises(ValueError, match=variable):
        load_config(str(_config_path(tmp_path)))


def test_a_refused_placeholder_names_the_file_it_came_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """`--config` can name any file, so the refusal must not assume config.json."""
    monkeypatch.delenv("NOT_SET_ANYWHERE", raising=False)
    path = _config_path(tmp_path, platform={"url": "ws://127.0.0.1:8765/ws/devices", "api_key": "${NOT_SET_ANYWHERE}"})
    renamed = path.rename(tmp_path / "bench-01.json")

    with pytest.raises(ValueError, match="bench-01.json"):
        load_config(str(renamed))
