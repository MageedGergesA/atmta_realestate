# Wave 17 — claims and the contractual clock.
#
# A delay event is what happened. A notice is telling the other side about it
# inside the period the contract allows. An extension of time is the relief
# granted. A claim is the money asked for, with its cost lines, its evidence,
# its submissions and the determination that answers it.
#
# The cluster sits above documents, quality and change, because a claim cites
# all three: the RFI that asked, the NCR that found, the change event that was
# opened, the drawing revision it all refers to. It sits below daily
# reporting, which stays in the monolith, so the daily records evidencing a
# delay are reached through a seam.
from . import delay_event
from . import notice
from . import eot
from . import claim
