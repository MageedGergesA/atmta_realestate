# -*- coding: utf-8 -*-
"""M3 — where the control policies live, and why they are configuration.

Two policies decide everything M3 does:

```
    BUDGET POLICY       what happens when approved demand exceeds capacity
    PO GOVERNANCE       whether a project purchase order may confirm without
                        a procurement authorisation behind it
```

Both are company defaults with a per-project override, and that is the whole
precedence chain. A third level keyed on cost code was considered and rejected:
cost codes belong to Construction, Procurement cannot see them without it, and
a policy that changed meaning depending on which modules were installed would
be worse than one nobody could override that finely.

### The defaults, and why they are what they are

`warn` and `optional`. Neither refuses anything.

That is a deliberate choice about an upgrade, not a view about what good
governance looks like. Existing databases have live requisitions and live
purchase orders; shipping a version that starts refusing approvals and
confirmations the moment it is installed would take an operational decision
away from the people whose operation it is. Turning control on is a policy act
with a runbook (`Activate Procurement Reservations`), not a side effect of a
software update.

What the defaults *do* provide from the first minute: every approval creates a
reservation, every position is computed, and every over-budget approval leaves
an exception record behind. The numbers are true before anybody is stopped by
them.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

#: What happens when approved demand exceeds available capacity.
BUDGET_POLICY = [
    ('none', 'No Control'),
    ('warn', 'Warn and Record'),
    ('approval_required', 'Approval Required'),
    ('block', 'Block'),
]

#: Whether a project-coded purchase order may confirm on its own authority.
PO_GOVERNANCE = [
    ('optional', 'Optional — direct purchase orders allowed'),
    ('controlled', 'Controlled — requisition or approved exception'),
    ('required', 'Required — approved requisition only'),
]

#: M4K — how hard vendor qualification is enforced, and at which moment.
#:
#: The ladder is short on purpose. A fifth level ("required for PO") was
#: considered and left out: with no Award document in the system until M7 it
#: would enforce at exactly the same moment as `required_for_award` and be a
#: setting that changed nothing, which is worse than a gap somebody can see.
#: When M7 introduces a formal award, that check moves earlier and purchase
#: confirmation keeps this one as the backstop.
VENDOR_POLICY = [
    ('optional', 'Optional — qualification available, never required'),
    ('warn', 'Warn — record the gap and continue'),
    ('required_for_sourcing', 'Required to be invited'),
    ('required_for_award', 'Required to receive the order'),
]

INHERIT = [('company', 'Company Default')]


class ResCompany(models.Model):
    _inherit = 'res.company'

    procurement_budget_policy = fields.Selection(
        BUDGET_POLICY, default='warn', required=True,
        string='Procurement Budget Control',
        help="What happens when approved demand exceeds what the project has "
             "left. None disables reservation entirely; Warn records the "
             "overage and continues; Approval Required needs an authorised "
             "exception; Block refuses.")
    procurement_po_governance = fields.Selection(
        PO_GOVERNANCE, default='optional', required=True,
        string='Project Purchase Governance',
        help="Whether a purchase order coded to a construction project may be "
             "confirmed without a procurement authorisation behind it.")
    procurement_reservation_expiry_days = fields.Integer(
        string='Reservation Expiry (days)', default=0,
        help="Days an unsourced reservation keeps consuming capacity. Zero "
             "means it never expires — which is the honest default, because "
             "an expiry nobody chose would release real demand on a date "
             "nobody knew about.")
    procurement_amount_tolerance_pct = fields.Float(
        string='Amount Tolerance (%)', default=0.0,
        help="How far a purchase order may exceed the approved requisition "
             "basis before the approval stops covering it. Zero means an "
             "approved amount is an approved amount.")
    procurement_amount_tolerance_amount = fields.Monetary(
        string='Amount Tolerance', default=0.0,
        currency_field='currency_id',
        help="A flat alternative to the percentage. Whichever is larger "
             "applies; both default to zero.")
    procurement_allow_self_approval = fields.Boolean(
        string='Allow Procurement Self-Approval', default=False,
        help="Off by default. When on, a requester may satisfy their own "
             "approval step up to the limit below — a decision a company "
             "makes explicitly, not one it discovers.")
    procurement_self_approval_limit = fields.Monetary(
        string='Self-Approval Limit', default=0.0,
        currency_field='currency_id',
        help="The ceiling on self-approval, in company currency. Zero with "
             "self-approval enabled means nothing qualifies.")

    # -- M4K ------------------------------------------------------------
    procurement_vendor_policy = fields.Selection(
        VENDOR_POLICY, default='optional', required=True,
        string='Vendor Qualification Control',
        help="Whether a vendor has to be qualified before this company will "
             "deal with them. Optional by default: an upgrade must not stop "
             "an existing supply chain that was legal the day before.")
    procurement_qualification_warn_days = fields.Integer(
        string='Expiring Soon (days)', default=30,
        help="How far ahead the register flags an expiring qualification. "
             "Thirty days is a starting point, not a rule — a supplier whose "
             "ISO renewal takes a quarter needs more.")
    procurement_qualification_allow_self_approval = fields.Boolean(
        string='Allow Qualification Self-Approval', default=False,
        help="Off by default. Assessing a vendor and approving that "
             "assessment are two jobs; this is deliberately separate from "
             "the spend self-approval switch because a company may "
             "reasonably allow one and not the other.")

    @api.constrains('procurement_qualification_warn_days')
    def _check_warn_days(self):
        for company in self:
            if company.procurement_qualification_warn_days < 0:
                raise ValidationError(_(
                    "A warning period cannot run backwards."))

    @api.constrains('procurement_amount_tolerance_pct')
    def _check_tolerance(self):
        for company in self:
            if company.procurement_amount_tolerance_pct < 0:
                raise ValidationError(_(
                    "A negative tolerance would mean an order had to come in "
                    "under the approved amount to be allowed."))


class ProjectProcurementPolicy(models.Model):
    """Per-project overrides. `company` means "whatever the company says"."""
    _inherit = 'realestate.project'

    procurement_budget_policy = fields.Selection(
        INHERIT + BUDGET_POLICY, default='company', required=True,
        string='Procurement Budget Control',
        help="Override the company's budget-control policy for this project. "
             "A cost-plus project and a fixed-price tower rarely want the "
             "same answer.")
    procurement_po_governance = fields.Selection(
        INHERIT + PO_GOVERNANCE, default='company', required=True,
        string='Project Purchase Governance',
        help="Override the company's purchase-governance policy for this "
             "project.")
    procurement_vendor_policy = fields.Selection(
        INHERIT + VENDOR_POLICY, default='company', required=True,
        string='Vendor Qualification Control',
        help="Override the company's vendor-qualification policy. A "
             "landmark tower with a client-approved vendor list and a small "
             "refurbishment do not need the same answer.")
    procurement_vendor_endorsement_required = fields.Boolean(
        string='Requires Project Endorsement',
        help="On for developments where a generally qualified vendor still "
             "needs this project's own sign-off — usually because the client "
             "or the lender insists. Off everywhere else, so the general "
             "qualification is enough.")
