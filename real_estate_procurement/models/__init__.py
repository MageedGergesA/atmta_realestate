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
# M6 — evaluation. The plan exists before the round that uses it, the round
# before the candidates in it, and the candidates before their commercial
# analysis.
from . import evaluation_plan
from . import evaluation_round
from . import evaluation_candidate
from . import commercial_analysis
from . import evaluation_deviation
from . import evaluation_audit
# M7 — the award. It authorises; M3 converts and Construction commits.
from . import procurement_award
from . import material_request
from . import material_request_line
from . import material_request_revision
from . import res_partner
from . import product_template
from . import purchase_order
