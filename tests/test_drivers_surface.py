"""Arm/transporter symbols are re-exported on the orca_client.drivers surface.

orca-client re-exports the driver layer from cheshire_drivers. The arm symbols
are re-exported eagerly (cheshire_drivers hard-requires pylabrobot.arms, so there
is no arms-free import to defer for) and must resolve to the same objects
cheshire_drivers exports. This pins the re-export so a dropped or broken symbol
is caught here rather than at device-build time.
"""

import cheshire_drivers
import orca_client.drivers as drivers

_ARM_SYMBOLS = (
    "PLRTransporterBackendWrapper",
    "convert_cartesian_to_plr_coord",
    "convert_joint_to_plr_dict",
)


def test_arm_symbols_re_exported_from_cheshire_drivers() -> None:
    """Each arm symbol is on the orca_client.drivers surface (in __all__ and as a
    real module attribute) and is the same object cheshire_drivers exports."""
    for name in _ARM_SYMBOLS:
        assert name in drivers.__all__, f"{name} missing from orca_client.drivers __all__"
        assert getattr(drivers, name) is getattr(cheshire_drivers, name)
