# -*- coding: utf-8 -*-
"""Treasury policy — configured per company, never hard-coded.

M5 and M28 are explicit that cheque law and segregation-of-duties rules differ
by country and by house policy, and that this generic module must not decide
them. Egypt's six-month cheque validity, Saudi Arabia's presentation rules, a
given developer's "never present a PDC early" instruction — all of them live
here as configuration with conservative defaults, and none of them is written
into the engine.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .check_states import LOCATION_KIND


class ResCompany(models.Model):
    _inherit = 'res.company'

    # ---------- Presentation policy (M5) ----------
    check_allow_early_deposit = fields.Boolean(
        string='Allow Early Presentation', default=False,
        help="Whether a post-dated cheque may be sent to the bank before its "
             "due date. Off by default: in most MENA jurisdictions presenting "
             "a PDC early is at best a commercial discourtesy and at worst "
             "unlawful, so the safe answer is the default one.")
    check_early_deposit_days = fields.Integer(
        string='Early Presentation Window (days)', default=0,
        help="How many days before the due date a cheque may be presented, "
             "when early presentation is allowed at all.")
    check_stale_days = fields.Integer(
        string='Cheque Validity (days)', default=0,
        help="Days after the due date beyond which the instrument is treated "
             "as stale and is flagged rather than presented. 0 disables the "
             "check entirely — there is no universal legal duration and this "
             "module does not invent one. Localisation modules may set it.")
    check_overdue_presentation_days = fields.Integer(
        string='Overdue Presentation Alert (days)', default=3,
        help="A matured cheque still sitting on hand this many days after its "
             "due date is flagged to Treasury.")
    check_due_soon_days = fields.Integer(
        string='Due-Soon Alert (days)', default=7,
        help="How far ahead the due-soon cron looks. Raise it to see further; "
             "set 0 to silence that cron for this company.")

    # ---------- Allocation policy (M3) ----------
    check_require_full_allocation = fields.Boolean(
        string='Require Full Allocation Before Deposit', default=False,
        help="Strict mode: a cheque may not be deposited while any part of its "
             "face value is unallocated to an obligation. Off by default "
             "because a genuine consolidated cheque often arrives before the "
             "schedule it will settle.")

    # ---------- Segregation of duties (M28) ----------
    check_maker_checker = fields.Boolean(
        string='Maker / Checker on Deposits', default=False,
        help="When on, the user who prepared a deposit may not also confirm "
             "it, unless its total is under the self-approval limit.")
    check_self_approve_limit = fields.Monetary(
        string='Self-Approval Limit', currency_field='currency_id',
        help="A deposit at or below this total may be confirmed by whoever "
             "prepared it even under maker/checker. Zero means no exemption.")

    # ---------- Accounting configuration (M7, M11) ----------
    check_payment_method_line_id = fields.Many2one(
        'account.payment.method.line', string='Cheque Payment Method',
        domain="[('payment_type', '=', 'inbound'),"
               " ('company_id', '=', id)]",
        help="Inbound payment method used when a deposit is confirmed. Leave "
             "empty to let Odoo pick the journal's default — but naming it "
             "here is what makes cheques land in an Outstanding Receipts "
             "account rather than straight into the bank balance, which is "
             "what allows 'presented' and 'cleared' to differ at all.")
    check_bounce_penalty_product_id = fields.Many2one(
        'product.product', string='Bounce Penalty Product',
        domain="[('type', '=', 'service')]",
        help="Service product used to invoice the customer for a returned "
             "cheque. No account is hard-coded: the product's category drives "
             "the income account, exactly as for any other sale.")
    check_bank_charge_product_id = fields.Many2one(
        'product.product', string='Bank Return-Charge Product',
        domain="[('type', '=', 'service')]",
        help="Service product for the fee the BANK charges us on a return. A "
             "different thing from the penalty we charge the customer, and "
             "deliberately a different field.")
    check_default_location_id = fields.Many2one(
        'realestate.check.location', string='Default Custody Location',
        help="Where a newly registered cheque is presumed to be.")

    @api.constrains('check_early_deposit_days', 'check_stale_days',
                    'check_due_soon_days', 'check_overdue_presentation_days')
    def _check_treasury_windows(self):
        for company in self:
            if company.check_early_deposit_days < 0:
                raise ValidationError(_(
                    "The early presentation window cannot be negative."))
            if company.check_stale_days < 0:
                raise ValidationError(_(
                    "Cheque validity cannot be negative."))
            if company.check_due_soon_days < 0:
                raise ValidationError(_(
                    "The due-soon window cannot be negative."))
            if company.check_overdue_presentation_days < 0:
                raise ValidationError(_(
                    "The overdue presentation window cannot be negative."))


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    check_allow_early_deposit = fields.Boolean(
        related='company_id.check_allow_early_deposit', readonly=False)
    check_early_deposit_days = fields.Integer(
        related='company_id.check_early_deposit_days', readonly=False)
    check_stale_days = fields.Integer(
        related='company_id.check_stale_days', readonly=False)
    check_overdue_presentation_days = fields.Integer(
        related='company_id.check_overdue_presentation_days', readonly=False)
    check_due_soon_days = fields.Integer(
        related='company_id.check_due_soon_days', readonly=False)
    check_require_full_allocation = fields.Boolean(
        related='company_id.check_require_full_allocation', readonly=False)
    check_maker_checker = fields.Boolean(
        related='company_id.check_maker_checker', readonly=False)
    check_self_approve_limit = fields.Monetary(
        related='company_id.check_self_approve_limit', readonly=False)
    check_payment_method_line_id = fields.Many2one(
        related='company_id.check_payment_method_line_id', readonly=False)
    check_bounce_penalty_product_id = fields.Many2one(
        related='company_id.check_bounce_penalty_product_id', readonly=False)
    check_bank_charge_product_id = fields.Many2one(
        related='company_id.check_bank_charge_product_id', readonly=False)
    check_default_location_id = fields.Many2one(
        related='company_id.check_default_location_id', readonly=False)


class CheckLocation(models.Model):
    """Where a physical cheque can be.

    Records rather than a hard-coded selection (M4 asks for exactly this):
    a developer with four sales offices and two safes needs six locations, and
    a selection field cannot give them one.
    """
    _name = 'realestate.check.location'
    _description = 'Cheque Custody Location'
    _order = 'company_id, sequence, name'

    name = fields.Char(required=True, translate=True)
    code = fields.Char(help="Short code used on the deposit slip and registers.")
    sequence = fields.Integer(default=10)
    kind = fields.Selection(
        LOCATION_KIND, string='Type', required=True, default='treasury',
        help="What sort of place this is. Drives the few behaviours that "
             "genuinely differ — a cheque at a `bank` location is not on hand.")
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    custodian_id = fields.Many2one(
        'res.users', string='Default Custodian',
        help="Who is answerable for cheques held here, unless a movement names "
             "someone else.")
    active = fields.Boolean(default=True)
    note = fields.Text()

    check_count = fields.Integer(compute='_compute_check_count')

    _sql_constraints = [
        ('location_code_company_uniq',
         'unique(company_id, code)',
         'A custody location with this code already exists in this company.'),
    ]

    def _compute_check_count(self):
        """Grouped count — never one query per location (M33)."""
        counts = dict(self.env['realestate.check']._read_group(
            [('location_id', 'in', self.ids)],
            groupby=['location_id'], aggregates=['__count']))
        for rec in self:
            rec.check_count = counts.get(rec, 0)

    def action_view_checks(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Cheques at %s') % self.display_name,
            'res_model': 'realestate.check',
            'view_mode': 'list,form',
            'domain': [('location_id', '=', self.id)],
            'context': {'default_location_id': self.id},
        }
