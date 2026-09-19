# -*- coding: utf-8 -*-
"""Phases 25, 26 & 35 — the sale installment as the commercial obligation.

This model was the module's central defect. Version 0.2 deleted the plan engine
that created these records and moved to a single invoice split by an
`account.payment.term`, but left the model, its views, its cron and **all three
of its consumers** in place:

* `real_estate_checks` maps post-dated cheques 1:1 onto instalments;
* `real_estate_api` publishes the schedule to the buyer's portal;
* the developer dashboard sums them for its revenue KPI.

Since that release the table has been empty in every upgraded database, so PDC
issuance against a sale contract has been impossible, the portal has shown an
empty schedule, and the dashboard's revenue figure has been permanently zero.
M5 restores the generation (see `sale_contract_v2._generate_installments`); this
file makes the obligation itself sound.

### Phase 26 — what "paid" means

The audit found:

```python
if rec.move_id.payment_state in ('paid', 'in_payment', 'reversed'):
    rec.state = 'paid'
```

`in_payment` means registered but not yet reconciled with the bank.
`reversed` means the invoice was **cancelled by a credit note**. Both were
counted as collected, and the residual was never consulted. Every figure below
is derived from `amount_residual` instead, so a partial payment reads as
partial and a credit note does not read as a payment.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

#: Aging buckets, for the collections module that will consume these (Phase 35).
AGING_BUCKET = [
    ('not_due', 'Not Due'),
    ('current', '1–30 Days'),
    ('bucket_60', '31–60 Days'),
    ('bucket_90', '61–90 Days'),
    ('bucket_120', '91–120 Days'),
    ('bucket_over', 'Over 120 Days'),
]


class SaleInstallmentV2(models.Model):
    _inherit = 'realestate.sale.installment'
    _check_company_auto = True

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------
    #: `down`, `installment` and `balloon` are V1 values that
    #: `real_estate_checks` groups on, so they keep their exact meaning.
    kind = fields.Selection(
        selection_add=[
            ('down',),
            ('installment',),
            ('balloon',),
            ('handover', 'Handover Payment'),
            ('maintenance', 'Maintenance'),
            ('service', 'Service Charge'),
        ],
        ondelete={'handover': 'set default',
                  'maintenance': 'set default',
                  'service': 'set default'},
    )
    description = fields.Char(string='Description')
    percent = fields.Float(string='% of Price')

    # ------------------------------------------------------------------
    # Amounts — commercial vs accounting (Rule 3)
    # ------------------------------------------------------------------
    original_amount = fields.Monetary(
        string='Original Amount',
        help="What the schedule said when the contract was signed. Never "
             "changed — an adjustment is recorded separately.")
    adjustment_amount = fields.Monetary(
        string='Adjustments', readonly=True, copy=False,
        help="Net effect of approved adjustments. Kept apart from the original "
             "so the history of the obligation stays legible.")
    current_amount = fields.Monetary(
        string='Amount Due', compute='_compute_current_amount', store=True,
        help="Original plus adjustments. This is what is actually owed.")

    invoiced_amount = fields.Monetary(
        compute='_compute_accounting_state', store=True,
        help="The invoice's UNTAXED amount, so it is directly comparable with "
             "the commercial obligation. Tax is Odoo's business, not the "
             "schedule's — a 1,000,000 plan does not become a 1,150,000 plan "
             "because VAT applies.")
    paid_amount = fields.Monetary(
        compute='_compute_accounting_state', store=True,
        help="Cash actually received against the invoice, tax included — "
             "because that is what the customer paid.")
    residual_amount = fields.Monetary(
        compute='_compute_accounting_state', store=True,
        help="Taken from the invoice's own residual, which is the only figure "
             "that reflects reconciliation.")

    # `amount` is V1's field and is what Checks and the API read. It is kept as
    # a stored mirror of `current_amount` so nothing downstream has to change.
    amount = fields.Monetary(
        compute='_compute_current_amount', store=True, readonly=False,
        required=False)

    # ------------------------------------------------------------------
    # State (Phase 26)
    # ------------------------------------------------------------------
    state = fields.Selection(
        selection_add=[
            ('pending',),
            ('invoiced',),
            ('partially_paid', 'Partially Paid'),
            ('paid',),
            ('overdue', 'Overdue'),
            ('cancelled',),
        ],
        compute='_compute_accounting_state', store=True, readonly=True,
        ondelete={'partially_paid': 'set default', 'overdue': 'set default'},
    )
    is_cancelled = fields.Boolean(
        string='Cancelled', copy=False,
        help="Set by a restructuring or a contract cancellation. Kept as its "
             "own flag so the computed state can still be derived.")

    # ------------------------------------------------------------------
    # Collections hand-off (Phase 35)
    # ------------------------------------------------------------------
    days_overdue = fields.Integer(
        compute='_compute_accounting_state', store=True)
    aging_bucket = fields.Selection(
        AGING_BUCKET, compute='_compute_accounting_state', store=True)
    collector_id = fields.Many2one(
        'res.users', string='Responsible Collector',
        help="Extension point for a future Collections module. Unused here.")
    promise_to_pay_date = fields.Date(
        help="Extension point for a future Collections module. Unused here.")

    plan_line_id = fields.Many2one(
        'realestate.payment.plan.line', string='Plan Line', readonly=True,
        ondelete='set null')
    project_id = fields.Many2one(
        related='sale_contract_id.project_id', store=True, readonly=True,
        index=True)

    # ==================================================================
    # Computes
    # ==================================================================
    @api.depends('original_amount', 'adjustment_amount')
    def _compute_current_amount(self):
        for rec in self:
            rec.current_amount = (rec.original_amount or 0.0) + (
                rec.adjustment_amount or 0.0)
            rec.amount = rec.current_amount

    @api.depends('move_id', 'move_id.state', 'move_id.amount_total',
                 'move_id.amount_residual', 'move_id.payment_state',
                 'current_amount', 'date_due', 'is_cancelled')
    def _compute_accounting_state(self):
        """Every figure here comes from the invoice, not from a payment existing.

        Deliberately reads `amount_residual`: it is the only number that
        reflects reconciliation, partial settlement and credit notes together.
        """
        today = fields.Date.context_today(self)
        for rec in self:
            move = rec.move_id
            posted = move and move.state == 'posted'

            # A REVERSED invoice is not a settled obligation.
            #
            # Reversing with `cancel=True` reconciles the credit note against
            # the invoice, so `amount_residual` drops to zero — and reading the
            # residual alone would call that "paid". That is the same category
            # of error the audit found in V1 (which listed `reversed` among the
            # paid states), reached by a different route: the money never
            # arrived, the document was cancelled.
            #
            # The commercial obligation therefore returns to un-invoiced. It
            # still stands and still has to be billed again.
            # `move_id` is deliberately NOT cleared here: a compute must not
            # write a stored non-computed field, and keeping the link means the
            # cancelled invoice stays visible on the obligation it failed to
            # settle. `action_generate_invoice` knows to replace it.
            if posted and move.payment_state == 'reversed':
                posted = False

            if not posted:
                rec.invoiced_amount = 0.0
                rec.paid_amount = 0.0
                rec.residual_amount = rec.current_amount
            else:
                # Two different questions, two different bases:
                #   "has this obligation been billed?"  -> untaxed, comparable
                #                                          with the schedule
                #   "what is still owed / has come in?" -> gross, because that
                #                                          is the cash reality
                rec.invoiced_amount = move.amount_untaxed
                rec.residual_amount = move.amount_residual
                rec.paid_amount = move.amount_total - move.amount_residual

            # ---- state ----
            if rec.is_cancelled:
                rec.state = 'cancelled'
            elif not posted:
                rec.state = 'pending'
            elif rec.residual_amount <= 0.0001:
                rec.state = 'paid'
            elif rec.paid_amount > 0.0001:
                rec.state = 'partially_paid'
            elif rec.date_due and rec.date_due < today:
                rec.state = 'overdue'
            else:
                rec.state = 'invoiced'

            # ---- aging ----
            if rec.state in ('paid', 'cancelled') or not rec.date_due:
                rec.days_overdue = 0
                rec.aging_bucket = 'not_due'
                continue
            days = (today - rec.date_due).days
            rec.days_overdue = max(days, 0)
            if days <= 0:
                rec.aging_bucket = 'not_due'
            elif days <= 30:
                rec.aging_bucket = 'current'
            elif days <= 60:
                rec.aging_bucket = 'bucket_60'
            elif days <= 90:
                rec.aging_bucket = 'bucket_90'
            elif days <= 120:
                rec.aging_bucket = 'bucket_120'
            else:
                rec.aging_bucket = 'bucket_over'

    @api.constrains('original_amount')
    def _check_amount(self):
        for rec in self:
            if rec.original_amount < 0:
                raise ValidationError(_(
                    "An instalment cannot be for a negative amount."))

    # ==================================================================
    # Invoicing (Rule 3 — Odoo owns the accounting)
    # ==================================================================
    def action_generate_invoice(self):
        """Raise the accounting obligation for this commercial obligation.

        Overrides V1, which dated the invoice on the **due date** — posting an
        invoice dated in the future distorts every period report between now and
        then — and created it with no explicit company or currency.
        """
        for rec in self:
            if rec.is_cancelled:
                raise UserError(_(
                    "Instalment %s is cancelled and cannot be invoiced."
                ) % rec.display_name)
            if rec.move_id and rec.move_id.payment_state != 'reversed':
                raise UserError(_(
                    "Instalment %s is already invoiced (%s)."
                ) % (rec.display_name, rec.move_id.name))
            replaced = rec.move_id if rec.move_id else None
            prop = rec.property_id
            if not prop or not prop.product_variant_id:
                raise UserError(_(
                    "Unit %s has no linked product, so it cannot be invoiced."
                ) % (prop.display_name if prop else '?'))

            today = fields.Date.context_today(rec)
            invoice = self.env['account.move'].create({
                'move_type': 'out_invoice',
                'partner_id': rec.partner_id.id,
                'company_id': rec.company_id.id,
                'currency_id': rec.currency_id.id,
                # Dated today, due on the schedule date. The invoice date drives
                # the ledger period; the due date drives collection.
                'invoice_date': today,
                'invoice_date_due': rec.date_due,
                'invoice_origin': rec.sale_contract_id.name,
                'payment_reference': '%s #%s' % (
                    rec.sale_contract_id.name, rec.sequence),
                'invoice_line_ids': [(0, 0, {
                    'name': rec.description or rec._default_line_name(),
                    'product_id': prop.product_variant_id.id,
                    'quantity': 1,
                    'price_unit': rec.current_amount,
                })],
            })
            rec.move_id = invoice.id
            self.env['realestate.account.tools'].post_moves(invoice)
            if replaced:
                rec.sale_contract_id.message_post(body=_(
                    "Instalment %s re-invoiced as %s; %s had been reversed."
                ) % (rec.description or rec.sequence, invoice.name,
                     replaced.name))
        return True

    def _default_line_name(self):
        self.ensure_one()
        labels = dict(self._fields['kind'].selection)
        return '%s — %s' % (labels.get(self.kind, self.kind),
                            self.property_id.display_name)

    def action_mark_paid(self):
        """Register a payment through Odoo and let reconciliation decide.

        V1 set `state = 'paid'` whenever `payment_state` was one of
        `paid / in_payment / reversed`. The state is now computed from the
        residual, so this method only has to move the money; whether the
        obligation is settled is Odoo's answer, not ours.
        """
        for rec in self:
            if rec.is_cancelled:
                raise UserError(_(
                    "Instalment %s is cancelled.") % rec.display_name)
            if not rec.move_id:
                rec.action_generate_invoice()
            self.env['realestate.account.tools'].post_moves(rec.move_id)
            self.env['realestate.account.tools'].register_payment(rec.move_id)
        return True

    def action_cancel_installment(self, reason=None):
        """Cancel a future obligation without touching what has been paid."""
        for rec in self:
            if rec.paid_amount > 0.0001:
                raise UserError(_(
                    "Instalment %s has %s paid against it and cannot be "
                    "cancelled. Use a credit note or an adjustment instead — "
                    "money that has been received must stay accounted for."
                ) % (rec.display_name, rec.paid_amount))
            rec.is_cancelled = True
            if rec.sale_contract_id:
                rec.sale_contract_id.message_post(body=_(
                    "Instalment %s cancelled. %s"
                ) % (rec.description or rec.sequence, reason or ''))
        return True

    # ==================================================================
    # Cron
    # ==================================================================
    @api.model
    def cron_auto_invoice_due_installments(self):
        """Invoice instalments falling due within the lead time.

        V1's version was an explicit no-op left behind when the schedule engine
        was deleted, while its cron record kept firing daily. It does the job
        again, bounded and idempotent.
        """
        lead_days = int(self.env['ir.config_parameter'].sudo().get_param(
            'real_estate_developer.installment_lead_days', 7))
        horizon = fields.Date.add(fields.Date.context_today(self),
                                  days=lead_days)
        due = self.search([
            ('state', '=', 'pending'),
            ('is_cancelled', '=', False),
            ('move_id', '=', False),
            ('date_due', '<=', horizon),
            ('sale_contract_id.state', 'in',
             ('signed', 'active', 'financially_cleared', 'handed_over')),
        ], limit=200)
        invoiced = 0
        for rec in due:
            try:
                # Each attempt in its own savepoint: a failure undoes only this
                # instalment. Rolling back the cursor instead discarded every
                # invoice created earlier in the same run while still counting
                # them.
                with self.env.cr.savepoint():
                    rec.action_generate_invoice()
                invoiced += 1
            except UserError:
                # One unbillable instalment (a unit with no product, say) must
                # not stop the rest of the run. It stays pending and will be
                # picked up again tomorrow.
                rec.invalidate_recordset()
                continue
        return invoiced

    @api.model
    def cron_refresh_installment_aging(self):
        """Keep the stored state and ageing current as the days pass.

        `state` (overdue), `days_overdue` and `aging_bucket` are stored because
        the collections views group and filter on them, but they depend on
        today's date, and a stored compute only reruns when a record changes.
        An invoice posted before its due date would otherwise stay "invoiced",
        with its original ageing, until something else touched it.

        Every open instalment already past its due date is recomputed: its
        day count moves every day, and one that has just fallen due changes
        state. Flagging `date_due` as modified also refreshes what depends on
        it, such as the contract's collection state.
        """
        today = fields.Date.context_today(self)
        stale = self.search([
            ('state', 'not in', ('paid', 'cancelled')),
            ('date_due', '<', today),
        ])
        if stale:
            stale.modified(['date_due'])
            self.env.flush_all()
        return len(stale)
