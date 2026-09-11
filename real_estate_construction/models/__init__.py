from . import project_analytic
from . import purchase_order_construction
from . import project_construction
from . import phase_construction
from . import construction_dashboard
from . import control_exceptions
from . import control_tower
from . import integrity_audit
from . import procurement_requisition

# Wave 12 — cost_structure, construction_analytic and construction_accounts
# moved to `atmta_construction_core`, the floor this module now depends on.
# The WBS, the cost codes, the analytic distribution helper and the four
# company accounts are defined there and are reached through the ORM registry
# exactly as before.

# Wave 13 — `realestate.contractor` and
# `realestate.construction.contract.package` moved to
# `atmta_construction_contract`, the commercial floor this module now depends
# on. These two files add back the half that only construction can know:
# milestones, certificates, retention, variations, extensions of time, claims
# and the commitment precedence rule.
from . import contractor_construction
from . import contract_package_construction

# Wave 14 — documents, submittals, transmittals and RFIs moved to
# `atmta_construction_documents`. This file adds back the one thing that could
# not go with them: the link from a submittal or an RFI to the change event it
# raises, which this module declares.
from . import information_change_event

# Wave 15 — inspections, NCRs and observations moved to
# `atmta_construction_quality`. This file adds back the NCR's change-event
# link, and registers the daily-report mode on the reason wizard.
from . import quality_change_event

# Wave 16 — change events and change orders moved to
# `atmta_construction_change`. This file supplies the implementation: the
# budget, commitment and revenue records an approved order produces, the
# authority matrix it is checked against, and the forecast anticipations it
# converts.
from . import change_implementation

# Wave 17 — claims, delay events, extensions of time and notices moved to
# `atmta_construction_claims`. This file keeps the one relation that could not
# go with them: the daily-report records that evidence a delay.
from . import delay_daily_link

# Wave 18 — site execution moved to `atmta_construction_site`: milestones, the
# bill of quantities, tasks, daily reports, the labour log and cost lines. This
# file keeps the one relation that could not go with it, because payment
# certificates are still declared here.

# Wave 19 — payment certificates, their lines, retention, advances and owner
# progress billing moved to `atmta_construction_certification`. The BOQ-line
# relation Wave 18 had to leave here went with them: the module that owns the
# certificate line is the one that says which BOQ line it consumes.

# Wave 20 — budgets, change impact, commitment, exposure, cost sheets,
# forecasts, cost reporting, project controls and risk moved to
# `atmta_construction_cost`. It is the last cluster that owned data.
#
# What stays here owns nothing and only reads: the control tower, the
# integrity audit, the dashboard and the data-control exceptions. They
# consume every module below and add no figure of their own, which is why
# they sit with the screens rather than in a capability of their own.
