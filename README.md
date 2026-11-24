# Orca Client

Connects lab devices on this computer to an Orca runtime over WebSocket.

## Overview

orca-client runs on the computer your instruments are plugged into. It connects to an Orca runtime, builds the drivers for the devices the runtime assigns to it, and runs the commands the runtime sends. The runtime can be a local daemon started with `orca start` or a hosted deployment.

**Full documentation**: [cheshirelabs.io/docs/orca/device-bridge](https://cheshirelabs.io/docs/orca/device-bridge)

## Quick Start

### Prerequisites

- Python 3.10+
- An Orca runtime to connect to. This package does not install one. The runtime is a separate package, [Orca](https://github.com/Cheshire-Labs/orca), and on a real deployment the two usually run on different computers. For a local runtime, see [Running a local runtime](#running-a-local-runtime).

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

### Running a local runtime

The install above puts orca-client in this environment, not the runtime, so `orca` is not on this PATH. The two are separate packages because they usually run on separate computers. For a local runtime, install [Orca](https://github.com/Cheshire-Labs/orca) into a second virtual environment, following its README, and run it from a second shell:

```bash
# Second shell, second virtual environment: the runtime, not orca-client.
# Run it in your package folder, the one holding topology.py and workflows/.
orca start --port 8765
orca topology mount
```

`orca start` leaves the daemon idle. It accepts orca-client connections right away, but it has no devices until its package is mounted. The mounted package says which devices this client runs. The daemon builds the folder it started in, or `ORCA_PACKAGE_DIR`: its `topology.py` and every `workflows/<name>.py`. Writing a package is covered by the Orca docs.

The daemon performs no authentication: it accepts any API key. It listens on
127.0.0.1 only, so run orca-client on the same computer and treat that computer
as the trust boundary. See [SECURITY.md](SECURITY.md).

### Configuration

Create a `config.json` file. It says who this computer is and which runtime to connect to:

```json
{
  "client_id": "local",
  "site": "test",
  "lab": "simulation",
  "platform": {
    "url": "ws://127.0.0.1:8765/ws/devices",
    "api_key": "local"
  }
}
```

orca-client does not list devices. Each device is set up in the deployment package on the runtime, in `devices/<name>.json`. The name is the device's name in `topology.py`, and `client` must equal this computer's `client_id`. On the Orca framework, a device with no file runs as a sim on the client called `local`, so one local install sims every device with no files. A file with no `client` also goes to `local`, real driver included. The daemon's `ORCA_DEFAULT_CLIENT` setting names a different client.

```json
{
  "client": "local",
  "real": {
    "type": "plr",
    "backend": "PreciseFlex",
    "connection": { "type": "tcp", "host": "192.168.1.100" }
  }
}
```

A connection is `tcp` (`host`, optional `port`), `serial` (`port`, such as `COM3`), or `usb` (`serial_number`, `device_address` or `device_id`; with none, the first instrument found). A device with no `real` block runs simulated. A person who moves plates is a `transporter` with `"real": {"type": "human"}`: in LIVE, orca-client prompts in its terminal at each pick and place, so run it in a terminal you can type into.

Paths on this computer stay in `config.json`, in `machine`: where Venus is installed, and where the vendor Opentrons simulator is. The runtime can never tell this computer which program to start.

To connect to a hosted deployment instead, set `url` to its `wss://` address and `api_key` to the key it issued you.

For real hardware configuration, see the [configuration docs](https://cheshirelabs.io/docs/orca/device-bridge).

### Run

Back in the first shell, the one with this package installed:

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
