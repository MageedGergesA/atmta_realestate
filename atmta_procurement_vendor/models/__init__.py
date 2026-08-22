# -*- coding: utf-8 -*-
# Ordered so the trade taxonomy and the template exist before the assessment
# that snapshots them — the same order the models were loaded in when they
# lived in real_estate_procurement.
from . import vendor_category
from . import qualification_template
from . import vendor_profile
from . import vendor_qualification
from . import vendor_restriction
from . import vendor_eligibility
from . import res_partner
