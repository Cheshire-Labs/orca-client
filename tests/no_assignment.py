"""For tests of the websocket client that are not about what an assignment builds."""

from cheshire_drivers.gateway_protocol import AssignmentResult, DeviceAssignment


async def build_nothing(assignment: DeviceAssignment) -> AssignmentResult:
    return AssignmentResult(assignment_id=assignment.assignment_id, devices=[])
