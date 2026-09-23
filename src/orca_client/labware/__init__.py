"""Envelope-sourced labware resolution.

`CommandMessage` carries a `labware: LabwareDefinitionDTO | None` field.
When set, orca-client uses the envelope-sourced
geometry instead of querying its own local catalog. This subpackage
houses the resolver that turns the envelope DTO into a PLR Resource.
"""

from orca_client.labware.envelope_resolver import (
    EnvelopeLabwareError,
    resolve_envelope_labware,
)

__all__ = ["EnvelopeLabwareError", "resolve_envelope_labware"]
