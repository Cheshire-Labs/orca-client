# Orca Client

Connects lab devices on this computer to an Orca runtime over WebSocket.

## Overview

Orca Client is the device bridge: it runs on the computer your instruments are plugged into. It loads a driver for each device in its config, connects to an Orca runtime, and runs the commands the runtime sends. The runtime can be a local daemon started with `orca start` or a hosted deployment.

**Full documentation**: [cheshirelabs.io/docs/orca/device-bridge](https://cheshirelabs.io/docs/orca/device-bridge)

## Quick Start

### Prerequisites

- Python 3.10+
- An Orca runtime to connect to. For a local one, install [Orca](https://github.com/Cheshire-Labs/orca) and run `orca start`.

### Installation

Install into a new virtual environment:

```bash
git clone https://github.com/Cheshire-Labs/orca-client.git
cd orca-client
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # Linux/macOS
pip install -e .
```

pip also installs [cheshire-drivers](https://github.com/Cheshire-Labs/cheshire-drivers) from GitHub, at the release this version pins. cheshire-drivers installs a fork of PyLabRobot under the name `pylabrobot`, which replaces any upstream PyLabRobot already in the environment. That is why the environment should be a new one. The cheshire-drivers README explains the fork.

### Configuration

Start a local Orca daemon on a port you choose:

```bash
orca start --port 8765
```

The daemon performs no authentication: it accepts any API key. It listens on
127.0.0.1 only, so run the client on the same computer and treat that computer
as the trust boundary. See [SECURITY.md](SECURITY.md).

Create a `config.json` file. This minimal example connects one simulated arm to that daemon:

```json
{
  "client_id": "my-workstation",
  "site": "test",
  "lab": "simulation",
  "platform": {
    "url": "ws://127.0.0.1:8765/ws/devices",
    "api_key": "local"
  },
  "devices": [
    {
      "type": "transporter",
      "name": "Simulated Arm",
      "driver": { "type": "sim" }
    }
  ]
}
```

To connect to a hosted deployment instead, set `url` to its `wss://` address and `api_key` to the key it issued you.

For real hardware configuration, see the [configuration docs](https://cheshirelabs.io/docs/orca/device-bridge).

### Run

```bash
python -m orca_client --config config.json
# Add --verbose for debug logging
```

## Supported Devices

See [supported devices](https://cheshirelabs.io/docs/orca/devices) for the full list of compatible hardware.

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Type checking
mypy src/orca_client

# Format code
black src/orca_client
```

## Resources

- **Documentation**: [cheshirelabs.io/docs/orca/device-bridge](https://cheshirelabs.io/docs/orca/device-bridge)
- **Issues**: [GitHub Issues](https://github.com/Cheshire-Labs/orca-client/issues)

## Contributing

See [CONTRIBUTING](./CONTRIBUTING) for how contributions reach this repository.

Contributors must sign the [Cheshire Labs Contributor Agreement](https://cla-assistant.io/Cheshire-Labs/orca-client), which assigns copyright in the contribution to Cheshire Labs.

## License

Source-available under the [Server Side Public License v1 (SSPL-1.0)](LICENSE)
from 1.0.0 onward. Earlier releases were AGPL-3.0.
[NOTICE](./NOTICE) names the copyright holder.

## Acknowledgments

This project uses [PyLabRobot](https://github.com/PyLabRobot/pylabrobot), an open-source, hardware-agnostic interface for liquid-handling robots and accessories.

> Wierenga, R.P., Golas, S.M., Ho, W., Coley, C.W., & Esvelt, K.M. (2023). PyLabRobot: An open-source, hardware-agnostic interface for liquid-handling robots and accessories. *Device*, 1(4), 100111. https://doi.org/10.1016/j.device.2023.100111
