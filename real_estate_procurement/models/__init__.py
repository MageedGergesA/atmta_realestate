from . import procurement_policy
from . import procurement_control
from . import procurement_exception
from . import procurement_reservation
from . import procurement_plan
from . import approval_rule
# M4 — vendor governance. Ordered so the trade taxonomy and the template
# exist before the assessment that snapshots them.
from . import vendor_category
from . import qualification_template
from . import vendor_profile
from . import vendor_qualification
from . import vendor_restriction
from . import vendor_eligibility
# M5 — sourcing and tender. The event exists before the invitation that
# references it, and the invitation before the bid it receives.
from . import sourcing_event
from . import sourcing_invitation
from . import bid_response
from . import sourcing_clarification
from . import sourcing_audit
from . import material_request
from . import material_request_line
from . import material_request_revision
from . import res_partner
from . import product_template
from . import purchase_order
