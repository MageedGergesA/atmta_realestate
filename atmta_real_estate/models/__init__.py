# -*- coding: utf-8 -*-

# Base entities (formerly in real_estate_base — now self-contained)
from . import property_stock
from . import maintenance_request

# Rental-specific extensions and entities
from . import rental_property
from . import contract
from . import payment_plans
from . import contract_line
from . import contract_payment
from . import contract_payment_line
from . import contract_increment_rule
from . import account_move
from . import account_tools
from . import sale_order
from . import contract_pivot_report
from . import contract_line_pivot
from . import contract_utility_line
from . import property_rental_history
from . import res_partner
from . import contract_deposit
from . import rental_invoicing
from . import rental_dashboard
