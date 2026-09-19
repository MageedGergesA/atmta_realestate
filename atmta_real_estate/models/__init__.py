# -*- coding: utf-8 -*-

# Shared vocabulary (constants only, no models) — imported first so every other
# module can pull lifecycle/state definitions without an import cycle.
from . import lease_states
from . import proration

# Company-level leasing configuration
from . import res_company
from . import rental_roles           # Rental roles replace the legacy groups

# ---------------------------------------------------------------------------
# Property master
# ---------------------------------------------------------------------------
from . import property_status        # Phase 2 — status dimensions
from . import property_identity      # Phase 1/3 — usage + identifiers
from . import property_availability  # Phase 4 — availability engine
from . import property_meter         # Phase 23 — meters + readings
from . import maintenance_request

# ---------------------------------------------------------------------------
# Lease core
# ---------------------------------------------------------------------------
from . import rental_property
from . import contract
from . import contract_property_line  # Phase 5/6 — allocations + overlap
from . import contract_lifecycle      # Phase 8 — lifecycle redesign
from . import contract_party          # Phase 7 — multi-party leases
from . import contract_increment_rule

# ---------------------------------------------------------------------------
# Billing
# ---------------------------------------------------------------------------
from . import contract_charge_rule    # Phase 10 — recurring charges
from . import rent_escalation         # Phase 11 — escalation rules
from . import contract_incentive      # Phase 12 — rent-free / discounts
from . import contract_payment
from . import contract_payment_line
from . import billing_obligation      # Phase 9/16 — obligation + arrears
from . import billing_engine          # Phase 9/13 — schedule generation
from . import rental_invoicing

# ---------------------------------------------------------------------------
# Deposits & accounting
# ---------------------------------------------------------------------------
from . import contract_deposit        # legacy deposit_state bridge
from . import deposit_record          # Phase 14 — deposit financial record
from . import account_move

# ---------------------------------------------------------------------------
# Lease operations
# ---------------------------------------------------------------------------
from . import contract_renewal        # Phase 17
from . import contract_amendment      # Phase 18
from . import contract_termination    # Phase 19
from . import move_in                 # Phase 20
from . import move_out                # Phase 21
from . import unit_turn               # Phase 22

# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
from . import contract_utility_line
from . import property_rental_history
from . import res_partner
from . import rent_roll               # Phase 24
from . import rental_dashboard        # Phase 26
from . import calendar_refresh        # date-derived stored values
from . import accounting_access       # money buttons follow Invoicing rights
