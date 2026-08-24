# Wave 5 — procurement policy and product classification moved to
# atmta_procurement_core, the domain floor this module now depends on.
# Wave 6 — demand moved to atmta_procurement_request and pre-commitment
# control to atmta_procurement_control. Requisitions, plans, revisions,
# reservations, approvals and control exceptions are defined there and are
# reached through the ORM registry exactly as before.
# M4 — vendor governance was extracted in Wave 1 and is now defined by
# atmta_procurement_vendor, which this module depends on. The models are
# unchanged and are reached through the ORM registry exactly as before.
# M5 — sourcing and tender moved to atmta_procurement_sourcing in Wave 7.
# What stays here is the readiness description, which points upward at the
# Evaluation and Award models this module still owns.
# M6 evaluation and M7 award moved to atmta_procurement_evaluation and
# atmta_procurement_award in Wave 8. What stays here is the receiving half of
# the integrity audit, which reads models this module still owns.
from . import evaluation_audit_receipt
from . import purchase_order
from . import receipt_inspection
from . import vendor_bill
