# -*- coding: utf-8 -*-
from . import vendor_governance_wizards
# Wave 11 — moved from atmta_procurement_control. Its only field is a
# Many2one to `realestate.procurement.vendor.restriction`, a model this
# module owns; control never had a claim on it.
from . import restriction_lift
