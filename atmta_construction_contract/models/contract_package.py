# -*- coding: utf-8 -*-
"""M2 — the commercial construction package.

A package is the commercial agreement with one contractor for one scope: Main
Civil, MEP, Façade, Fit-Out. It is **not** the DOCX contract template engine in
`real_estate_contract_template`, which produces the legal document; this is the
money and the scope behind it.

### Why the original value is snapshotted rather than read from the PO

Phase 0 found `contractor.total_po_committed` reading live PO totals, which
means the "original" commitment silently changed whenever a purchase order was
edited. At award, `original_contract_value` is written once and never
recalculated:

```
    ORIGINAL CONTRACT VALUE + APPROVED VARIATIONS = CURRENT CONTRACT VALUE
```

M4 owns the variation workflow. M2 owns the equation it lands in, which is why
`approved_variation_amount` exists here and is zero until M4 fills it.

### One package, one commitment

The double-count the brief warns about is a package of 8M with a PO of 8M
reading as 16M. The rule is in `commitment.py`: a package that has purchase
orders is represented by **its purchase orders**, and contributes nothing of
its own.
"""

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

PACKAGE_STATES = [
    ('draft', 'Draft'),
    ('tender', 'Tender / Negotiation'),
    ('awarded', 'Awarded'),
    ('active', 'Active'),
    ('substantially_complete', 'Substantially Complete'),
    ('complete', 'Complete'),
    ('closed', 'Closed'),
    ('cancelled', 'Cancelled'),
]

#: States in which a package represents a live commercial obligation.
COMMITTED_STATES = ('awarded', 'active', 'substantially_complete', 'complete')

PACKAGE_TYPES = [
    ('civil', 'Main Civil / Structure'),
    ('mep', 'MEP'),
    ('hvac', 'HVAC'),
    ('elevator', 'Elevators'),
    ('facade', 'Façade / Cladding'),
    ('finishes', 'Finishes / Fit-Out'),
    ('landscape', 'Landscaping'),
    ('infrastructure', 'Infrastructure'),
    ('professional', 'Professional Services'),
    ('other', 'Other'),
]


class ConstructionContractPackage(models.Model):
    _name = 'realestate.construction.contract.package'
    _description = 'Construction Contract Package'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, name'
    _check_company_auto = True

    name = fields.Char(
        string='Reference', copy=False, required=True, readonly=True,
        default=lambda self: _('New'), index='trigram')
    title = fields.Char(required=True, tracking=True,
                        help="Main Civil Works, MEP Package…")
    project_id = fields.Many2one(
        'realestate.project', required=True, ondelete='restrict', index=True,
        check_company=True, tracking=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    contractor_id = fields.Many2one(
        'realestate.contractor', tracking=True, ondelete='restrict',
        help="Empty while tendering. Required to award.")
    partner_id = fields.Many2one(
        related='contractor_id.partner_id', store=True, readonly=True)
    package_type = fields.Selection(
        PACKAGE_TYPES, default='other', required=True, tracking=True)

    wbs_ids = fields.Many2many(
        'realestate.construction.wbs',
        # Explicit relation names: the generated ones
        # (`..._contract_package_realestate_construction_wbs_rel`) exceed
        # Postgres's 63-character identifier limit.
        relation='construction_package_wbs_rel',
        column1='package_id', column2='wbs_id',
        string='WBS Scope',
        domain="[('project_id', '=', project_id)]",
        help="The scope this package covers. Reporting only — the money is "
             "coded on the purchase orders and certificates.",
    )
    cost_code_ids = fields.Many2many(
        'realestate.construction.cost.code',
        relation='construction_package_cost_code_rel',
        column1='package_id', column2='cost_code_id',
        string='Cost Codes',
        help="Which cost codes this package is expected to consume.",
    )
    scope_description = fields.Html()

    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id, tracking=True)
    original_contract_value = fields.Monetary(
        readonly=True, copy=False, tracking=True,
        help="Snapshotted at award, untaxed, and never recalculated. What was "
             "agreed is a fact about the past.",
    )
    tender_value = fields.Monetary(
        tracking=True,
        help="The value being negotiated. Becomes the original contract value "
             "at award unless a different figure is awarded.",
    )
    approved_variation_amount = fields.Monetary(
        string='Approved Variations', compute='_compute_variations',
        store=True, readonly=True, copy=False, tracking=True,
        help="The sum of implemented commitment changes against this package. "
             "Derived, so every unit of it points at the change order that "
             "authorised it.",
    )

    def _variation_amount(self):
        """Approved variations against this package.

        Seam. The change-order workflow that authorises a variation lives in
        `real_estate_construction`, above this module, so the sum cannot be
        taken here. That module supplies `commitment_change_ids` and the
        arithmetic; on its own the floor reports no variations, which is the
        truthful answer when nothing can raise one.
        """
        self.ensure_one()
        return 0.0

    def _compute_variations(self):
        for rec in self:
            rec.approved_variation_amount = rec._variation_amount()
    current_contract_value = fields.Monetary(
        compute='_compute_current_value', store=True, tracking=True)

    date_start = fields.Date(tracking=True)
    original_completion_date = fields.Date(
        readonly=True, copy=False, tracking=True,
        help="Snapshotted at award. Extensions of time are added beside it "
             "(M8), never written over it.",
    )
    current_completion_date = fields.Date(
        tracking=True, compute='_compute_current_completion', store=True,
        readonly=False,
        help="Original completion plus approved extensions of time plus any "
             "separately authorised adjustment. Before award it is a "
             "planning date and is typed directly.")
    approved_eot_days = fields.Float(
        compute='_compute_eot_days', store=True, tracking=True,
        help="Sum of implemented extensions of time. Determined-but-not-"
             "implemented days are deliberately excluded.")
    claimed_eot_days = fields.Float(
        compute='_compute_eot_days',
        help="Days asked for and not yet granted. Reported beside the "
             "contract date, never inside it.")
    other_time_adjustment_days = fields.Float(
        tracking=True,
        help="A contractual time adjustment that is not an extension of time "
             "— a suspension agreement, a re-baselining somebody signed. It "
             "needs a reason, and it moves the current date the same way.")
    other_time_adjustment_reason = fields.Char(tracking=True)
    substantial_completion_date = fields.Date(
        tracking=True, help="Taking-over / substantial completion, if the "
                            "contract uses one.")
    actual_completion_date = fields.Date(tracking=True)

    # -- Notice requirements. Deliberately per contract: 28 days is FIDIC's
    # -- number, not everybody's, and a hard-coded period would quietly
    # -- mis-classify every contract that uses a different one.
    notice_required = fields.Boolean(
        tracking=True,
        help="Whether this contract requires notice of a claim event.")
    notice_period_days = fields.Integer(
        tracking=True,
        help="Days from awareness to the notice deadline, under THIS "
             "contract. Nothing here assumes a standard form.")
    notice_day_basis = fields.Selection([
        ('calendar', 'Calendar Days'),
        ('business', 'Business Days'),
    ], default='calendar', tracking=True)
    notice_reminder_days = fields.Integer(
        default=7, help="How long before the deadline a notice counts as due "
                        "soon.")
    detailed_claim_period_days = fields.Integer(
        tracking=True,
        help="Days from notice to fully detailed particulars, where the "
             "contract sets one.")

    retention_pct = fields.Float(
        string='Retention (%)', tracking=True,
        help="Defaults from the contractor when one is set.")
    advance_pct = fields.Float(string='Advance (%)', tracking=True)
    advance_amount = fields.Monetary(tracking=True)
    performance_guarantee = fields.Char(
        help="Reference of the guarantee held. M7 formalises recovery.")

    purchase_order_ids = fields.One2many(
        'purchase.order', 're_package_id', string='Purchase Orders')
    purchase_order_count = fields.Integer(compute='_compute_po_stats')
    committed_amount = fields.Monetary(
        string='Current Commitment', compute='_compute_po_stats',
        help="What this package commits, following the source precedence in "
             "`commitment.py`: its purchase orders when it has any, its own "
             "contract value when it does not.",
    )
    commitment_source = fields.Selection([
        ('purchase_orders', 'Purchase Orders'),
        ('package', 'Package Contract Value'),
        ('none', 'Not Committed'),
    ], compute='_compute_po_stats')

    state = fields.Selection(
        PACKAGE_STATES, default='draft', required=True, tracking=True,
        copy=False, index=True)
    awarded_on = fields.Datetime(readonly=True, copy=False)
    awarded_by_id = fields.Many2one(
        'res.users', readonly=True, copy=False, string='Awarded By')
    notes = fields.Html()

    def _eot_day_totals(self):
        """(approved days, claimed days) for this package.

        Seam. Extensions of time and claims are `real_estate_construction`
        models. Approved days move the contract completion date; claimed days
        are reported beside it and never inside it. That module overrides this
        with the real sums.
        """
        self.ensure_one()
        return 0.0, 0.0

    def _compute_eot_days(self):
        for rec in self:
            rec.approved_eot_days, rec.claimed_eot_days = rec._eot_day_totals()

    @api.depends('original_completion_date', 'approved_eot_days',
                 'other_time_adjustment_days')
    def _compute_current_completion(self):
        for rec in self:
            if rec.original_completion_date:
                rec.current_completion_date = (
                    rec.original_completion_date
                    + relativedelta(days=int(rec.approved_eot_days
                                             + rec.other_time_adjustment_days)))
            else:
                # Before award there is no contract date to extend. Whatever
                # was typed as a planning date stands.
                rec.current_completion_date = rec.current_completion_date

    @api.depends('original_contract_value', 'approved_variation_amount')
    def _compute_current_value(self):
        for rec in self:
            rec.current_contract_value = (
                rec.original_contract_value + rec.approved_variation_amount)

    @api.depends('purchase_order_ids.state',
                 'purchase_order_ids.amount_untaxed',
                 'current_contract_value', 'state')
    def _package_commitment(self):
        """(amount, source) this package currently commits.

        Seam. The precedence rule -- purchase orders when there are any, the
        package's own contract value when there are none -- is stated once, in
        `commitment.py` in `real_estate_construction`. Repeating it here would
        be a second place for the double-count this suite exists to prevent.
        The floor answers from the purchase orders it can see.
        """
        self.ensure_one()
        confirmed = self.purchase_order_ids.filtered(
            lambda po: po.state in ('purchase', 'done'))
        if confirmed:
            return sum(confirmed.mapped('amount_untaxed')), 'purchase_orders'
        if self.current_contract_value:
            return self.current_contract_value, 'package'
        return 0.0, 'none'

    def _compute_po_stats(self):
        for rec in self:
            rec.purchase_order_count = len(rec.purchase_order_ids)
            amount, source = rec._package_commitment()
            rec.committed_amount = amount
            rec.commitment_source = source

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.construction.contract.package') or 'PKG/NEW'
        return super().create(vals_list)

    @api.onchange('contractor_id')
    def _onchange_contractor(self):
        if self.contractor_id and not self.retention_pct:
            self.retention_pct = self.contractor_id.retention_pct

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def action_tender(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only a draft package can go to tender."))
            rec.state = 'tender'
        return True

    def action_award(self, value=None):
        """Award the package and freeze what was agreed.

        The snapshot is the whole point: `original_contract_value` is written
        once, here, and every later question about "what did we originally
        agree" reads it rather than re-deriving it from documents that have
        moved on since.
        """
        for rec in self:
            if rec.state not in ('draft', 'tender'):
                raise UserError(_(
                    "Only a draft or tendering package can be awarded."))
            if not rec.contractor_id:
                raise UserError(_(
                    "A package is awarded to somebody. Set the contractor."))
            awarded_value = (
                value if value is not None else rec.tender_value)
            if not awarded_value or awarded_value <= 0:
                raise UserError(_(
                    "Award value must be positive — a package worth nothing "
                    "commits nothing and should not be awarded."))
            rec.write({
                'state': 'awarded',
                'original_contract_value': awarded_value,
                'original_completion_date': rec.current_completion_date,
                'awarded_on': fields.Datetime.now(),
                'awarded_by_id': self.env.user.id,
            })
            rec.message_post(body=_(
                "Awarded to %(contractor)s at %(value)s (untaxed).",
                contractor=rec.contractor_id.display_name,
                value=rec.currency_id.format(awarded_value)))
        return True

    def action_activate(self):
        for rec in self:
            if rec.state != 'awarded':
                raise UserError(_("Only an awarded package becomes active."))
            rec.state = 'active'
        return True

    def action_substantially_complete(self):
        for rec in self:
            if rec.state != 'active':
                raise UserError(_(
                    "Only an active package can be substantially complete."))
            rec.state = 'substantially_complete'
        return True

    def action_complete(self):
        for rec in self:
            if rec.state not in ('active', 'substantially_complete'):
                raise UserError(_("Only an active package can complete."))
            rec.state = 'complete'
        return True

    def action_close(self):
        """Closeout is M10's business; M2 only records the state."""
        for rec in self:
            if rec.state != 'complete':
                raise UserError(_(
                    "Complete the package before closing it."))
            rec.state = 'closed'
        return True

    def action_cancel(self):
        for rec in self:
            if rec.state in ('complete', 'closed'):
                raise UserError(_(
                    "A completed package cannot be cancelled."))
            if rec.purchase_order_ids.filtered(
                    lambda po: po.state in ('purchase', 'done')):
                raise UserError(_(
                    "This package has confirmed purchase orders. Cancel those "
                    "in Purchase first — cancelling the package alone would "
                    "leave a commitment nobody owns."))
            rec.state = 'cancelled'
        return True

    def action_reset_to_draft(self):
        for rec in self:
            if rec.original_contract_value:
                raise UserError(_(
                    "An awarded package keeps its award. Cancel it instead — "
                    "reopening would let the original value be rewritten."))
            rec.state = 'draft'
        return True

    # ------------------------------------------------------------------
    @api.constrains('contractor_id', 'company_id')
    def _check_contractor_company(self):
        for rec in self:
            partner = rec.contractor_id.partner_id
            if partner and partner.company_id and \
                    partner.company_id != rec.company_id:
                raise ValidationError(_(
                    "%(contractor)s belongs to another company and cannot hold "
                    "a package in %(company)s.",
                    contractor=rec.contractor_id.display_name,
                    company=rec.company_id.display_name))

    @api.constrains('project_id', 'company_id')
    def _check_project_company(self):
        for rec in self:
            if rec.project_id.company_id and \
                    rec.project_id.company_id != rec.company_id:
                raise ValidationError(_(
                    "Package %(name)s is in %(company)s but its project is "
                    "in %(other)s.",
                    name=rec.name, company=rec.company_id.display_name,
                    other=rec.project_id.company_id.display_name))

    def _consumed_value(self):
        """What has already been certified or invoiced under this package.

        An omission cannot take a contract below this — a contract worth less
        than what has been paid under it is not a contract, it is an error
        waiting to be discovered by an auditor.

        Seam. Payment certificates are a `real_estate_construction` model.
        On the floor nothing has been certified, so nothing constrains an
        omission; that module supplies the real figure.
        """
        self.ensure_one()
        return 0.0

    def action_view_purchase_orders(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Purchase Orders'),
            'res_model': 'purchase.order',
            'view_mode': 'list,form',
            'domain': [('re_package_id', '=', self.id)],
            'context': {'default_re_package_id': self.id,
                        'default_re_project_id': self.project_id.id},
        }
