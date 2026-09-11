# Wave 15 — quality. Whether the work was built to the specification, and
# what happened when it was not: inspection and test plans, checklist
# templates, inspection requests and the inspections that answer them,
# observations, and non-conformance reports.
#
# Quality sits below the commercial capabilities for the same reason document
# control does: an inspection is raised *against* a package and coded to a WBS
# node, all of which are already beneath it. What it produces that costs money
# -- the change event an NCR may raise -- is declared above and added back.
from . import quality
from . import quality_wizard

# Wave 23 — the NCR's change-event link, which Wave 15 had to leave above.
from . import ncr_change_event
