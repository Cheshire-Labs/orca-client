"""Canonical per-device-type Sim* driver class lookup.

Single source of truth for the cheshire-drivers ``Sim*Driver`` class
that pairs with each device type label. Both the factory (which builds
the live driver for ``driver.type in {sim, lab_sim}``) and the registry
(which lazily pairs every registered live driver with a fresh
``Sim*Driver`` for DEVICE_SIM dispatch) read from this table, so the
two surfaces cannot drift out of sync on device-type coverage.
"""

from typing import List, Optional, Protocol

from cheshire_drivers import (
    BaseDriver,
    FaultSpec,
    ITransporterDriver,
    SimCentrifugeDriver,
    SimDelidderDriver,
    SimLiquidHandlerDriver,
    SimPlateWasherDriver,
    SimReaderDriver,
    SimSealerDriver,
    SimShakerDriver,
    SimStorageDriver,
    SimStrategy,
    SimThermocyclerDriver,
    SimTransporterDriver,
    SimTranslatorDriver,
    SimWasteDriver,
)


class SimDriverCtor(Protocol):
    """Structural type for a Sim*Driver constructor.

    Sim* drivers accept ``cls(name)`` (no strategy/faults; instant-settle)
    OR ``cls(name, sim_strategy=..., faults=...)`` (lab_sim configured
    pacing + fault injection). Defaults are mandatory in the protocol so
    the no-kwargs call site (the plain ``sim`` driver type) also
    satisfies this type.
    """

    def __call__(
        self,
        name: str,
        sim_strategy: Optional[SimStrategy] = ...,
        faults: Optional[List[FaultSpec]] = ...,
    ) -> BaseDriver | ITransporterDriver: ...


SIM_DRIVER_CLS_BY_TYPE: dict[str, SimDriverCtor] = {
    "shaker": SimShakerDriver,
    "centrifuge": SimCentrifugeDriver,
    "sealer": SimSealerDriver,
    "transporter": SimTransporterDriver,
    "translator": SimTranslatorDriver,
    "liquid_handler": SimLiquidHandlerDriver,
    "plate_washer": SimPlateWasherDriver,
    "reader": SimReaderDriver,
    "delidder": SimDelidderDriver,
    "storage": SimStorageDriver,
    "waste": SimWasteDriver,
    "thermocycler": SimThermocyclerDriver,
}
