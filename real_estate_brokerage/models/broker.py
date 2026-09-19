# -*- coding: utf-8 -*-
"""M6 — external brokers and channel partners, on `res.partner`.

A broker is a **partner**, not a new model. They are invoiced, they have a
contact record, they may also be a buyer on a different deal; giving them a
second identity would mean reconciling two of everything for the rest of the
system's life.

### What a broker needs that a partner does not have

Standing to sell, and its limits. A licence with an expiry. An agreement that
says which projects, at what commission, for how long. And a KYC status,
because a brokerage that pays commission to an unverified counterparty has a
problem that is not an accounting problem.

```
res.partner (is_realestate_broker)
      │
      ├── realestate.broker.agreement   ── the terms, per period
      │        · projects allowed
      │        · commission %
      │        · protection window
      │
      └── realestate.lead.registration  ── "this customer is mine"
```

### The licence is not decoration

An expired licence is the difference between a legitimate introduction and one
that cannot be paid without exposing the company. It is checked where it
matters — at registration and at agreement activation — rather than merely
displayed.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

BROKER_TYPE = [
    ('individual', 'Individual Broker'),
    ('agency', 'Brokerage Agency'),
    ('channel_partner', 'Channel Partner'),
    ('referrer', 'Referrer'),
]

BROKER_STATE = [
    ('prospect', 'Prospect'),
    ('pending', 'Pending Approval'),
    ('active', 'Active'),
    ('suspended', 'Suspended'),
    ('terminated', 'Terminated'),
]

KYC_STATE = [
    ('not_started', 'Not Started'),
    ('submitted', 'Documents Submitted'),
    ('verified', 'Verified'),
    ('rejected', 'Rejected'),
]

AGREEMENT_STATE = [
    ('draft', 'Draft'),
    ('active', 'Active'),
    ('expired', 'Expired'),
    ('terminated', 'Terminated'),
]


class ResPartnerBroker(models.Model):
    _inherit = 'res.partner'

    is_realestate_broker = fields.Boolean(
        string='Real Estate Broker', index=True,
        help="A broker is a partner, not a separate model. They get invoiced, "
             "they have a contact record, and on another deal they may be the "
             "buyer.")
    broker_type = fields.Selection(BROKER_TYPE, string='Broker Type')
    broker_state = fields.Selection(
        BROKER_STATE, string='Broker Status', default='prospect',
        tracking=True, index=True)

    broker_license_number = fields.Char(string='Licence Number')
    broker_license_expiry = fields.Date(string='Licence Expires')
    broker_license_expired = fields.Boolean(
        compute='_compute_license_expired', search='_search_license_expired',
        string='Licence Expired')

    broker_kyc_state = fields.Selection(
        KYC_STATE, string='KYC', default='not_started', tracking=True)
    broker_kyc_note = fields.Char(string='KYC Note')

    broker_agreement_ids = fields.One2many(
        'realestate.broker.agreement', 'broker_partner_id',
        string='Agreements')
    active_broker_agreement_id = fields.Many2one(
        'realestate.broker.agreement', string='Current Agreement',
        compute='_compute_active_agreement', store=True)
    broker_registration_ids = fields.One2many(
        'realestate.lead.registration', 'broker_partner_id',
        string='Registered Leads')

    @api.depends('broker_license_expiry')
    def _compute_license_expired(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.broker_license_expired = bool(
                rec.broker_license_expiry and rec.broker_license_expiry < today)

    def _search_license_expired(self, operator, value):
        today = fields.Date.context_today(self)
        expired = [('broker_license_expiry', '!=', False),
                   ('broker_license_expiry', '<', today)]
        wants_expired = (operator == '=') == bool(value)
        if wants_expired:
            return expired
        return ['|', ('broker_license_expiry', '=', False),
                ('broker_license_expiry', '>=', today)]

    @api.depends('broker_agreement_ids.state', 'broker_agreement_ids.date_end')
    def _compute_active_agreement(self):
        for rec in self:
            rec.active_broker_agreement_id = rec.broker_agreement_ids.filtered(
                lambda a: a.state == 'active')[:1]

    # ------------------------------------------------------------------
    # The one question everything else asks
    # ------------------------------------------------------------------
    def _check_may_transact(self):
        """Whether this broker may register a lead or be paid today."""
        self.ensure_one()
        if not self.is_realestate_broker:
            raise UserError(_(
                "%s is not registered as a broker.") % self.display_name)
        if self.broker_state != 'active':
            raise UserError(_(
                "Broker %(name)s is %(state)s.",
                name=self.display_name,
                state=dict(BROKER_STATE).get(self.broker_state, 'unknown')))
        if self.broker_license_expired:
            raise UserError(_(
                "Broker %(name)s's licence expired on %(date)s. An "
                "introduction from an unlicensed broker is not one the company "
                "can pay for.",
                name=self.display_name, date=self.broker_license_expiry))
        if self.broker_kyc_state != 'verified':
            raise UserError(_(
                "Broker %s has not passed KYC. Paying commission to an "
                "unverified counterparty is not an accounting problem."
            ) % self.display_name)
        return True

    def action_activate_broker(self):
        for rec in self:
            if rec.broker_kyc_state != 'verified':
                raise UserError(_(
                    "Verify %s's KYC before activating them.")
                    % rec.display_name)
            rec.write({'is_realestate_broker': True,
                       'broker_state': 'active'})
        return True

    def action_suspend_broker(self):
        self.write({'broker_state': 'suspended'})
        return True


class BrokerAgreement(models.Model):
    """The terms, for a period. Not a set of fields on the partner.

    Terms change — a channel partner renegotiates, a protection window is
    shortened, a project is added. Keeping them on the partner would mean the
    current terms silently reinterpreting deals done under the old ones.
    """
    _name = 'realestate.broker.agreement'
    _description = 'Broker / Channel Partner Agreement'
    _inherit = ['mail.thread']
    _order = 'date_start desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string='Reference', required=True, copy=False, readonly=True,
        default=lambda self: _('New'))
    broker_partner_id = fields.Many2one(
        'res.partner', string='Broker', required=True, index=True,
        domain=[('is_realestate_broker', '=', True)], tracking=True)
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, index=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        related='company_id.currency_id', readonly=True)

    date_start = fields.Date(
        string='From', required=True, default=fields.Date.context_today,
        tracking=True)
    date_end = fields.Date(string='Until', tracking=True, index=True)

    commission_percentage = fields.Float(
        string='Commission %', tracking=True,
        help="The broker's share of the company's gross commission on a deal "
             "they introduced — not a slice of the sale price. M13 settled "
             "that distinction for the whole module.")
    commission_fixed = fields.Monetary(string='Fixed Fee', tracking=True)

    allowed_project_ids = fields.Many2many(
        'realestate.project', string='Authorised Projects',
        help="Empty means every project. A channel partner signed for one "
             "tower should not be registering leads against the whole estate.")

    protection_days = fields.Integer(
        string='Lead Protection (days)', default=90, tracking=True,
        help="How long a registered customer stays the broker's after "
             "registration. The single most argued-about number in channel "
             "sales, so it is a term of the agreement rather than a global "
             "setting.")
    is_exclusive = fields.Boolean(
        string='Exclusive Channel', tracking=True,
        help="Whether this broker is the only channel for the authorised "
             "projects.")

    state = fields.Selection(
        AGREEMENT_STATE, default='draft', required=True, tracking=True,
        index=True, copy=False)
    signed_on = fields.Date(readonly=True, copy=False)
    terminated_on = fields.Date(readonly=True, copy=False)
    termination_reason = fields.Char(readonly=True, copy=False)
    notes = fields.Html()

    registration_ids = fields.One2many(
        'realestate.lead.registration', 'agreement_id',
        string='Registrations')
    registration_count = fields.Integer(compute='_compute_registration_count')

    def _compute_registration_count(self):
        counts = dict(self.env['realestate.lead.registration']._read_group(
            [('agreement_id', 'in', self.ids)],
            groupby=['agreement_id'], aggregates=['__count']))
        for rec in self:
            rec.registration_count = counts.get(rec, 0)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.broker.agreement') or _('New')
        return super().create(vals_list)

    @api.constrains('date_start', 'date_end')
    def _check_dates(self):
        for rec in self:
            if rec.date_end and rec.date_end < rec.date_start:
                raise ValidationError(_(
                    "Agreement %s ends before it starts.") % rec.name)

    @api.constrains('state', 'broker_partner_id', 'company_id')
    def _check_one_active_agreement(self):
        """Two live agreements with the same broker means two sets of terms."""
        for rec in self.filtered(lambda a: a.state == 'active'):
            clash = self.search([
                ('id', '!=', rec.id),
                ('broker_partner_id', '=', rec.broker_partner_id.id),
                ('company_id', '=', rec.company_id.id),
                ('state', '=', 'active'),
            ], limit=1)
            if clash:
                raise ValidationError(_(
                    "%(broker)s already has an active agreement (%(other)s). "
                    "Two live agreements means two answers to what they are "
                    "owed.",
                    broker=rec.broker_partner_id.display_name,
                    other=clash.name))

    def action_activate(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_(
                    "Only a draft agreement can be activated."))
            rec.broker_partner_id._check_may_transact()
            rec.write({'state': 'active',
                       'signed_on': fields.Date.context_today(rec)})
        return True

    def action_terminate(self, reason=None):
        for rec in self:
            if rec.state != 'active':
                raise UserError(_("Only an active agreement can be "
                                  "terminated."))
            rec.write({
                'state': 'terminated',
                'terminated_on': fields.Date.context_today(rec),
                'termination_reason': reason or _('Not stated'),
            })
        return True

    def _check_still_valid(self):
        self.ensure_one()
        today = fields.Date.context_today(self)
        if self.state != 'active':
            raise UserError(_(
                "Agreement %(name)s is %(state)s.",
                name=self.name,
                state=dict(AGREEMENT_STATE).get(self.state)))
        if self.date_end and self.date_end < today:
            raise UserError(_(
                "Agreement %(name)s expired on %(date)s.",
                name=self.name, date=self.date_end))
        return True

    def _covers_project(self, project):
        """Empty authorisation means every project."""
        self.ensure_one()
        if not self.allowed_project_ids:
            return True
        return project in self.allowed_project_ids

    @api.model
    def _cron_expire_agreements(self):
        today = fields.Date.context_today(self)
        stale = self.search([
            ('state', '=', 'active'),
            ('date_end', '!=', False),
            ('date_end', '<', today),
        ])
        for agreement in stale:
            agreement.state = 'expired'
            agreement.message_post(body=_(
                "Agreement expired on %s.") % agreement.date_end)
        return True
