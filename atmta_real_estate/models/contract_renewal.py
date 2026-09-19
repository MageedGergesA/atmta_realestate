"""Lease renewals (Phase 17).

A renewal is a negotiation, not an edit. The pre-upgrade module had
``is_renewed`` / ``old_contract_id`` on the contract and nothing else, so the
only way to renew was to change the dates and the rent on the running lease --
which destroys the commercial history the business is judged on: what the unit
used to rent for, when it was renegotiated, and what was asked versus agreed.

Here a renewal is its own record with its own workflow. When it completes it
**creates the next lease** and links the two; the historical lease keeps its
original dates and its original rent forever.

Reminders are raised as Odoo activities at the company's configured windows
(default 180/120/90/60/30 days), by a batch-safe scheduled action.
"""

import logging
import operator as py_operator
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.osv import expression

from .lease_states import CLOSED_LIFECYCLE

_logger = logging.getLogger(__name__)

RENEWAL_STATES = [
    ('draft', 'Draft'),
    ('proposed', 'Proposed'),
    ('negotiating', 'Negotiating'),
    ('approved', 'Approved'),
    ('accepted', 'Accepted'),
    ('renewed', 'Renewed'),
    ('rejected', 'Rejected'),
    ('expired', 'Expired'),
]

OPEN_RENEWAL_STATES = ('draft', 'proposed', 'negotiating', 'approved', 'accepted')

EXPIRY_BUCKETS = [
    ('expired', 'Expired'),
    ('lte_30', '≤ 30 Days'),
    ('31_60', '31-60 Days'),
    ('61_90', '61-90 Days'),
    ('91_180', '91-180 Days'),
    ('gt_180', '> 180 Days'),
]
_COMPARE = {'=': py_operator.eq, '!=': py_operator.ne, '<': py_operator.lt,
            '<=': py_operator.le, '>': py_operator.gt, '>=': py_operator.ge}


def expiry_bucket(days):
    """Expiry bucket for a lease ``days`` days from its end, or False."""
    if days is None:
        return False
    if days < 0:
        return 'expired'
    for limit, key in ((30, 'lte_30'), (60, '31_60'), (90, '61_90'), (180, '91_180')):
        if days <= limit:
            return key
    return 'gt_180'


class ContractRenewal(models.Model):
    _name = 'realestate.contract.renewal'
    _description = 'Lease Renewal'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'proposed_start_date desc, id desc'

    name = fields.Char(
        string='Reference', required=True, copy=False, readonly=True,
        default=lambda self: _('New'),
    )
    contract_id = fields.Many2one(
        'realestate.contract', string='Current Lease', required=True,
        ondelete='cascade', index=True, tracking=True,
    )
    company_id = fields.Many2one(
        related='contract_id.company_id', store=True, index=True, readonly=True,
    )
    currency_id = fields.Many2one(
        related='contract_id.currency_id', store=True, readonly=True,
    )
    partner_id = fields.Many2one(
        related='contract_id.partner_id', string='Tenant', store=True, readonly=True,
    )
    property_ids = fields.Many2many(
        'realestate.property', string='Properties',
        compute='_compute_properties', store=True,
    )

    state = fields.Selection(
        RENEWAL_STATES, default='draft', required=True, tracking=True,
        index=True, copy=False,
    )

    # ---------------- Current terms (snapshot, never rewritten) ----------------
    current_start_date = fields.Date(
        string='Current Start', compute='_compute_current_terms', store=True)
    current_end_date = fields.Date(
        string='Current End', compute='_compute_current_terms', store=True)
    current_rent = fields.Monetary(
        string='Current Rent', compute='_compute_current_terms', store=True,
        help="Rent in force at the end of the current term, after escalations.",
    )

    # ---------------- Proposed terms ----------------
    proposed_start_date = fields.Date(string='Proposed Start', tracking=True, index=True)
    proposed_end_date = fields.Date(string='Proposed End', tracking=True)
    proposed_rent = fields.Monetary(string='Proposed Rent', tracking=True)
    proposed_term_months = fields.Integer(
        string='Term (months)', compute='_compute_proposed_term', store=True)

    increase_amount = fields.Monetary(
        string='Increase', compute='_compute_increase', store=True)
    increase_pct = fields.Float(
        string='Increase (%)', compute='_compute_increase', store=True)

    agreed_rent = fields.Monetary(
        string='Agreed Rent', tracking=True,
        help="What the tenant actually accepted. Falls back to the proposed "
             "rent when the offer is taken as-is.",
    )

    copy_charge_rules = fields.Boolean(
        string='Carry Over Charges', default=True,
        help="Copy the recurring charge rules onto the new lease.",
    )
    copy_parties = fields.Boolean(string='Carry Over Parties', default=True)
    copy_escalations = fields.Boolean(
        string='Carry Over Escalations', default=False,
        help="Off by default -- a renewal usually renegotiates the escalation "
             "profile rather than inheriting it.",
    )

    # ---------------- Ownership ----------------
    user_id = fields.Many2one(
        'res.users', string='Responsible', tracking=True,
        default=lambda self: self.env.user,
    )
    decision_date = fields.Date(tracking=True)
    rejection_reason = fields.Text()
    notes = fields.Html()

    new_contract_id = fields.Many2one(
        'realestate.contract', string='Renewal Lease', readonly=True, copy=False,
        help="The lease created when this renewal completed.",
    )

    def init(self):
        """At most one open renewal per lease.

        Expressed as a partial unique index rather than an EXCLUDE constraint
        so it needs no ``btree_gist`` extension on the target database.
        """
        super_init = getattr(super(), 'init', None)
        if super_init:
            super_init()
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
                realestate_contract_renewal_one_open_uniq
            ON realestate_contract_renewal (contract_id)
            WHERE state IN ('draft','proposed','negotiating','approved','accepted')
        """)

    # ==================================================================
    # Computes
    # ==================================================================
    @api.depends('contract_id.property_line_ids.property_id')
    def _compute_properties(self):
        for rec in self:
            rec.property_ids = rec.contract_id.property_line_ids.mapped('property_id')

    @api.depends('contract_id.start_date', 'contract_id.end_date',
                 'contract_id.price', 'contract_id.escalation_rule_ids.resulting_amount')
    def _compute_current_terms(self):
        for rec in self:
            contract = rec.contract_id
            rec.current_start_date = contract.start_date
            rec.current_end_date = contract.end_date
            rec.current_rent = (
                contract._rent_on(contract.end_date) if contract.end_date
                else contract.price)

    @api.depends('proposed_start_date', 'proposed_end_date')
    def _compute_proposed_term(self):
        for rec in self:
            if rec.proposed_start_date and rec.proposed_end_date:
                rec.proposed_term_months = (
                    (rec.proposed_end_date.year - rec.proposed_start_date.year) * 12
                    + (rec.proposed_end_date.month - rec.proposed_start_date.month))
            else:
                rec.proposed_term_months = 0

    @api.depends('current_rent', 'proposed_rent', 'agreed_rent')
    def _compute_increase(self):
        for rec in self:
            target = rec.agreed_rent or rec.proposed_rent or 0.0
            base = rec.current_rent or 0.0
            rec.increase_amount = target - base
            rec.increase_pct = ((target - base) / base * 100.0) if base else 0.0

    # ==================================================================
    # Defaults
    # ==================================================================
    @api.onchange('contract_id')
    def _onchange_contract_id(self):
        """Seed a sensible offer: same term length, same rent, starting the day
        after the current lease ends."""
        for rec in self:
            contract = rec.contract_id
            if not contract or not contract.end_date:
                continue
            rec.proposed_start_date = contract.end_date + relativedelta(days=1)
            months = 12
            if contract.start_date:
                months = max(
                    (contract.end_date.year - contract.start_date.year) * 12
                    + (contract.end_date.month - contract.start_date.month), 1)
            rec.proposed_end_date = (
                rec.proposed_start_date + relativedelta(months=months)
                - relativedelta(days=1))
            rec.proposed_rent = contract._rent_on(contract.end_date)

    # ==================================================================
    # Workflow
    # ==================================================================
    def action_propose(self):
        for rec in self:
            rec._validate_proposal()
            rec.state = 'proposed'
            rec.message_post(body=_(
                "Renewal proposed: %(rent)s for %(months)s months from %(start)s "
                "(%(pct).1f%% on the current rent).",
                rent=rec.proposed_rent, months=rec.proposed_term_months,
                start=rec.proposed_start_date, pct=rec.increase_pct))
        return True

    def action_negotiate(self):
        for rec in self:
            if rec.state not in ('proposed', 'negotiating'):
                raise UserError(_("Only a proposed renewal can move to negotiation."))
            rec.state = 'negotiating'
        return True

    def action_approve(self):
        """Commercial sign-off on the terms being offered."""
        self.contract_id._require_group('atmta_real_estate.group_rental_manager')
        for rec in self:
            if rec.state not in ('proposed', 'negotiating'):
                raise UserError(_(
                    "Renewal %s must be proposed before it can be approved.",
                    rec.name))
            rec._validate_proposal()
            rec.write({'state': 'approved', 'decision_date': fields.Date.context_today(rec)})
            rec.message_post(body=_("Renewal terms approved."))
        return True

    def action_accept(self):
        """The tenant has accepted."""
        for rec in self:
            if rec.state != 'approved':
                raise UserError(_(
                    "Renewal %s must be approved before the tenant can accept "
                    "it.", rec.name))
            rec.write({
                'state': 'accepted',
                'agreed_rent': rec.agreed_rent or rec.proposed_rent,
                'decision_date': fields.Date.context_today(rec),
            })
            rec.message_post(body=_(
                "Tenant accepted at %s.", rec.agreed_rent))
        return True

    def action_reject(self):
        for rec in self:
            if not rec.rejection_reason:
                raise UserError(_(
                    "Record why renewal %s was rejected -- it feeds the "
                    "non-renewal analysis.", rec.name))
            rec.write({'state': 'rejected',
                       'decision_date': fields.Date.context_today(rec)})
            rec.message_post(body=_("Renewal rejected: %s", rec.rejection_reason))
        return True

    def action_create_renewal_lease(self):
        """Materialise the accepted renewal as the next lease.

        The current lease is **not** modified beyond being flagged as renewed
        and linked forward: its dates, rent and billing history stay exactly as
        they were.
        """
        self.contract_id._require_group('atmta_real_estate.group_rental_manager')
        for rec in self:
            if rec.state != 'accepted':
                raise UserError(_(
                    "Renewal %s must be accepted by the tenant first.", rec.name))
            if rec.new_contract_id:
                raise UserError(_(
                    "Renewal %(name)s already created lease %(lease)s.",
                    name=rec.name, lease=rec.new_contract_id.display_name))
            rec._validate_proposal()
            rec._assert_properties_free()
            new_contract = rec._build_renewal_lease()
            rec.write({'state': 'renewed', 'new_contract_id': new_contract.id})
            rec.contract_id.write({'is_renewed': True})
            # Markup keeps the link a link; the values interpolated into it
            # are still escaped.
            rec.contract_id.message_post(body=Markup(_(
                "Renewed by <a href='#' data-oe-model='realestate.contract' "
                "data-oe-id='%(id)s'>%(name)s</a> from %(start)s.")) % {
                    'id': new_contract.id, 'name': new_contract.display_name,
                    'start': new_contract.start_date})
            new_contract.message_post(body=_(
                "Created from renewal %(renewal)s of lease %(old)s.",
                renewal=rec.name, old=rec.contract_id.display_name))
        return True

    # ==================================================================
    # Validation
    # ==================================================================
    def _validate_proposal(self):
        self.ensure_one()
        if not self.proposed_start_date or not self.proposed_end_date:
            raise UserError(_(
                "Set the proposed start and end dates on renewal %s.", self.name))
        if self.proposed_start_date > self.proposed_end_date:
            raise UserError(_(
                "Renewal %s ends before it starts.", self.name))
        if not (self.proposed_rent or self.agreed_rent):
            raise UserError(_("Set a proposed rent on renewal %s.", self.name))
        current_end = self.contract_id.end_date
        if current_end and self.proposed_start_date <= current_end:
            raise UserError(_(
                "The renewal starts %(start)s but the current lease runs to "
                "%(end)s. A renewal must start after the term it renews.",
                start=self.proposed_start_date, end=current_end))

    def _assert_properties_free(self):
        """Pre-flight the overlap check so the failure names the renewal, not a
        constraint deep inside the new lease's allocations."""
        self.ensure_one()
        Allocation = self.env['realestate.contract.property.line']
        for prop in self.contract_id.property_line_ids.mapped('property_id'):
            clash = Allocation._check_property_free(
                prop.id, self.proposed_start_date, self.proposed_end_date,
                exclude_contract=self.contract_id.id)
            if clash:
                raise ValidationError(_(
                    "Cannot renew: property '%(prop)s' is already committed to "
                    "lease '%(lease)s' for %(start)s → %(end)s.",
                    prop=prop.display_name,
                    lease=clash.contract_id.display_name,
                    start=clash.start_date, end=clash.end_date or _('open-ended')))

    # ==================================================================
    # Lease construction
    # ==================================================================
    def _build_renewal_lease(self):
        """Create the successor lease from the accepted terms."""
        self.ensure_one()
        source = self.contract_id
        rent = self.agreed_rent or self.proposed_rent
        Contract = self.env['realestate.contract'].with_company(self.company_id)

        new_contract = Contract.create({
            'partner_id': source.partner_id.id,
            'company_id': source.company_id.id,
            'currency_id': source.currency_id.id,
            'start_date': self.proposed_start_date,
            'end_date': self.proposed_end_date,
            'price': rent,
            'property_id': source.property_id.id,
            'is_single_property': source.is_single_property,
            'is_multi_property': source.is_multi_property,
            'billing_frequency': source.billing_frequency,
            'billing_mode': source.billing_mode,
            'billing_due_rule': source.billing_due_rule,
            'billing_due_offset_days': source.billing_due_offset_days,
            'use_billing_engine': source.use_billing_engine,
            'contract_type': source.contract_type.id,
            'is_renewed': False,
            'old_contract_id': source.id,
            'notes': source.notes,
        })

        if source.is_multi_property:
            # Units carry over as allocations for the new term. Only units the
            # lease still holds at its end are renewed: an amendment that
            # removed or substituted a unit ended that allocation early.
            Allocation = self.env['realestate.contract.property.line']
            for allocation in source.property_line_ids.filtered(
                    lambda a: not a.end_date or not source.end_date
                    or a.end_date >= source.end_date):
                Allocation.create({
                    'contract_id': new_contract.id,
                    'property_id': allocation.property_id.id,
                    'allocated_rent': allocation.allocated_rent,
                    'security_deposit': allocation.security_deposit,
                    'sequence': allocation.sequence,
                    'notes': allocation.notes,
                    'start_date': self.proposed_start_date,
                    'end_date': self.proposed_end_date,
                })

        if self.copy_parties:
            for party in source.party_ids.filtered(lambda p: not p.is_primary):
                party.copy({
                    'contract_id': new_contract.id,
                    'start_date': self.proposed_start_date,
                    'end_date': self.proposed_end_date,
                })
        if self.copy_charge_rules:
            for rule in source.charge_rule_ids.filtered('active'):
                rule.copy({
                    'contract_id': new_contract.id,
                    'property_line_id': False,
                    'start_date': False,
                    'end_date': False,
                })
        if self.copy_escalations:
            offset_years = (self.proposed_start_date.year - source.start_date.year
                            if source.start_date else 0)
            for rule in source.escalation_rule_ids:
                rule.copy({
                    'contract_id': new_contract.id,
                    'effective_date': rule.effective_date + relativedelta(
                        years=offset_years),
                })
        return new_contract

    # ==================================================================
    # ORM
    # ==================================================================
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.contract.renewal') or _('New')
        return super().create(vals_list)

    def action_open_new_contract(self):
        self.ensure_one()
        if not self.new_contract_id:
            raise UserError(_("This renewal has not created a lease yet."))
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'realestate.contract',
            'res_id': self.new_contract_id.id,
            'view_mode': 'form',
        }


class ContractRenewalMixin(models.Model):
    _inherit = 'realestate.contract'

    renewal_ids = fields.One2many(
        'realestate.contract.renewal', 'contract_id', string='Renewals',
    )
    renewal_count = fields.Integer(compute='_compute_renewal_state')
    renewal_state = fields.Selection(
        RENEWAL_STATES, string='Renewal Status',
        compute='_compute_renewal_state', store=True, index=True,
        help="Status of the open renewal, for the expiry board.",
    )
    renewed_from_id = fields.Many2one(
        'realestate.contract', string='Renewed From',
        related='old_contract_id', store=False, readonly=True,
    )
    # Days to expiry change every day, so they are computed for today when
    # read and searched through the end date. The bucket is stored because the
    # expiry board groups by it; ``realestate.calendar.refresh`` moves a lease
    # to its next bucket on the day it crosses a boundary.
    days_to_expiry = fields.Integer(
        string='Days to Expiry', compute='_compute_days_to_expiry',
        search='_search_days_to_expiry',
    )
    expiry_bucket = fields.Selection(
        EXPIRY_BUCKETS, string='Expiry Bucket', compute='_compute_expiry_bucket',
        store=True, index=True,
    )

    @api.depends('renewal_ids.state')
    def _compute_renewal_state(self):
        for rec in self:
            rec.renewal_count = len(rec.renewal_ids)
            open_renewals = rec.renewal_ids.filtered(
                lambda r: r.state in OPEN_RENEWAL_STATES)
            if open_renewals:
                rec.renewal_state = open_renewals[0].state
            elif rec.renewal_ids.filtered(lambda r: r.state == 'renewed'):
                rec.renewal_state = 'renewed'
            elif rec.renewal_ids.filtered(lambda r: r.state == 'rejected'):
                rec.renewal_state = 'rejected'
            else:
                rec.renewal_state = False

    def _days_to_expiry_on(self, day):
        """Days from ``day`` to the end of an open lease; None when closed or open-ended."""
        self.ensure_one()
        if not self.end_date or self.lifecycle_state in CLOSED_LIFECYCLE:
            return None
        return (self.end_date - day).days

    @api.depends('end_date', 'lifecycle_state')
    def _compute_days_to_expiry(self):
        today = fields.Date.context_today(self)
        for rec in self:
            days = rec._days_to_expiry_on(today)
            rec.days_to_expiry = days or 0

    @api.depends('end_date', 'lifecycle_state')
    def _compute_expiry_bucket(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.expiry_bucket = expiry_bucket(rec._days_to_expiry_on(today))

    def _search_days_to_expiry(self, operator, value):
        if operator not in _COMPARE:
            raise UserError(_("Days to Expiry cannot be searched with '%s'.", operator))
        value = int(value or 0)
        today = fields.Date.context_today(self)
        open_leases = [('end_date', '!=', False),
                       ('lifecycle_state', 'not in', list(CLOSED_LIFECYCLE))]
        domain = expression.AND([
            open_leases, [('end_date', operator, today + timedelta(days=value))]])
        # Closed and open-ended leases read 0 days.
        if _COMPARE[operator](0, value):
            domain = expression.OR([
                domain, ['!'] + expression.normalize_domain(open_leases)])
        return domain

    def action_start_renewal(self):
        self.ensure_one()
        existing = self.renewal_ids.filtered(lambda r: r.state in OPEN_RENEWAL_STATES)
        if existing:
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'realestate.contract.renewal',
                'res_id': existing[0].id,
                'view_mode': 'form',
            }
        return {
            'type': 'ir.actions.act_window',
            'name': _('Lease Renewal'),
            'res_model': 'realestate.contract.renewal',
            'view_mode': 'form',
            'context': {'default_contract_id': self.id},
        }

    def action_view_renewals(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Renewals'),
            'res_model': 'realestate.contract.renewal',
            'view_mode': 'list,form',
            'domain': [('contract_id', '=', self.id)],
            'context': {'default_contract_id': self.id},
        }

    # ------------------------------------------------------------------
    # Automation -- Phase 30
    # ------------------------------------------------------------------
    @api.model
    def _cron_renewal_reminders(self, limit=500):
        """Raise a renewal activity at each configured window before expiry.

        Idempotent: an activity is only created when one does not already
        exist for that lease and window. Batch-safe via ``limit``.
        Multi-company safe: each company's own windows are used.
        Timezone-safe: all comparisons are on dates, never datetimes.
        """
        today = fields.Date.context_today(self)
        has_todo_activity = bool(self.env.ref(
            'mail.mail_activity_data_todo', raise_if_not_found=False))
        created = 0
        for company in self.env['res.company'].sudo().search([]):
            windows = company._renewal_reminder_windows()
            if not windows:
                continue
            horizon = today + relativedelta(days=max(windows))
            leases = self.sudo().search([
                ('company_id', '=', company.id),
                ('lifecycle_state', 'in', ('active', 'notice')),
                ('end_date', '!=', False),
                ('end_date', '>=', today),
                ('end_date', '<=', horizon),
            ], limit=limit)
            for lease in leases:
                window = lease._due_reminder_window(windows, today)
                if window is None:
                    continue
                if lease._reminder_already_raised(window):
                    continue
                if has_todo_activity:
                    lease.activity_schedule(
                        act_type_xmlid='mail.mail_activity_data_todo',
                        date_deadline=today,
                        summary=_("Lease renewal — %s days to expiry") % window,
                        note=_(
                            "Lease <b>%(name)s</b> for %(tenant)s expires on "
                            "%(end)s. Start the renewal conversation."
                        ) % {'name': lease.display_name,
                             'tenant': lease.partner_id.display_name,
                             'end': lease.end_date},
                        user_id=(lease.user_id.id or lease.create_uid.id
                                 or self.env.uid),
                    )
                lease.message_post(body=_(
                    "Renewal reminder raised (%s days to expiry).", window))
                created += 1
        _logger.info("Renewal reminder cron raised %s activities", created)
        return created

    def _due_reminder_window(self, windows, today):
        """The largest configured window whose threshold we have just crossed."""
        self.ensure_one()
        days_left = (self.end_date - today).days
        candidates = [w for w in windows if days_left <= w]
        return min(candidates) if candidates else None

    def _reminder_already_raised(self, window):
        """Has this specific window already produced an activity?"""
        self.ensure_one()
        summary = _("Lease renewal — %s days to expiry") % window
        return bool(self.env['mail.activity'].sudo().search_count([
            ('res_model', '=', self._name),
            ('res_id', '=', self.id),
            ('summary', '=', summary),
        ]))
