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
# Wave 9 moved receipt inspection, the vendor-bill three-way match and the
# receiving audit checks to atmta_procurement_receipt. What stays is the
# purchase-order bridge: extensions of native purchase.order that reach across
# five procurement capabilities and belong to no single one of them.
from . import purchase_order
