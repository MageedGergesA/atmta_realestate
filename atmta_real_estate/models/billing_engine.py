"""Billing schedule generation and invoicing (Phases 9, 12, 13, 15).

This is the enterprise schedule generator. It sits **alongside** the
pre-upgrade ``action_generate_payment_schedule`` (payment plans + increment
rules), which is untouched -- existing leases keep generating exactly the
schedule they always did. New leases can opt into this engine, which
understands escalations, recurring charges, incentives and proration.

Design rules it obeys:

* **Idempotent.** Re-running never duplicates an obligation and never rewrites
  one that has already been invoiced. Billed history is immutable.
* **Explainable.** Every obligation records its gross rent, the relief applied
  and a plain-language proration note. Nothing is silently netted off.
* **Odoo owns the money.** The engine produces ``account.move`` records through
  the standard API and then stops. Taxes, receivables, reconciliation, partial
  payments, credit notes and follow-ups are all Odoo's.
"""

import logging

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from . import proration as prorate_lib
from .lease_states import CLOSED_LIFECYCLE

_logger = logging.getLogger(__name__)

BILLING_FREQUENCIES = [
    ('monthly', 'Monthly'),
    ('quarterly', 'Quarterly'),
    ('semiannual', 'Semi-Annual'),
    ('annual', 'Annual'),
]

FREQUENCY_MONTHS = {
    'monthly': 1,
    'quarterly': 3,
    'semiannual': 6,
    'annual': 12,
}

BILLING_MODES = [
    ('lease', 'One Obligation per Period'),
    ('property', 'One Obligation per Property per Period'),
]

DUE_RULES = [
    ('period_start', 'On Period Start (in advance)'),
    ('period_end', 'On Period End (in arrears)'),
]


class BillingEngine(models.Model):
    _inherit = 'realestate.contract'

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    billing_frequency = fields.Selection(
        BILLING_FREQUENCIES, string='Billing Frequency', default='monthly',
        required=True, tracking=True,
        help="How often rent is billed. The lease's Base Rent is the rent for "
             "ONE billing period.",
    )
    billing_mode = fields.Selection(
        BILLING_MODES, string='Billing Mode', default='lease', required=True,
        help="Whether each period produces one obligation for the whole lease "
             "or one per allocated property.",
    )
    billing_due_rule = fields.Selection(
        DUE_RULES, string='Due Date Rule', default='period_start', required=True,
    )
    billing_due_offset_days = fields.Integer(
        string='Due Offset (days)', default=0,
        help="Days added to the due date -- e.g. 5 for 'due on the 5th of the "
             "month billed'.",
    )
    use_billing_engine = fields.Boolean(
        string='Use Advanced Billing', default=True, tracking=True,
        help="Generate the billing schedule from the lease's rent, escalations, "
             "charges and incentives. New leases always use it; existing leases "
             "keep the value they had, and leases billed by a single invoice for "
             "their whole term keep that invoice until they end.",
    )

    # ==================================================================
    # Period construction
    # ==================================================================
    def _billing_periods(self):
        """Yield ``(period_start, period_end)`` closed intervals for the term.

        The final period is truncated at the lease end date, which is exactly
        the case proration exists to price.
        """
        self.ensure_one()
        if not self.start_date:
            return []
        months = FREQUENCY_MONTHS[self.billing_frequency]
        end = self.end_date
        if not end:
            raise UserError(_(
                "Lease '%s' has no end date. Set one before generating a "
                "billing schedule.", self.display_name))
        periods = []
        cursor = self.start_date
        # Hard stop: a 1-month lease billed monthly cannot produce more than a
        # few thousand periods; anything beyond that is a data error, not a
        # long lease.
        guard = 0
        while cursor <= end and guard < 2000:
            guard += 1
            natural_end = cursor + relativedelta(months=months) - relativedelta(days=1)
            # Both ends are returned: the NATURAL period end is what proration
            # measures against, the TRUNCATED one is what the obligation
            # records. Collapsing them (the original mistake) makes a
            # part-month look like a whole one, so the last month of a lease
            # ending mid-period was charged in full.
            periods.append((cursor, min(natural_end, end), natural_end))
            cursor = cursor + relativedelta(months=months)
        return periods

    def _due_date_for(self, period_start, period_end):
        self.ensure_one()
        base = period_start if self.billing_due_rule == 'period_start' else period_end
        return base + relativedelta(days=self.billing_due_offset_days or 0)

    # ==================================================================
    # Schedule generation
    # ==================================================================
    def _billing_authority(self):
        """Which invoicing path owns this lease.

        Returns ``whole_lease`` when the lease was invoiced for its whole term
        the legacy way (one invoice through the sales bridge, linked only by
        ``invoice_id``), ``engine`` when it bills per period through the billing
        engine, and ``legacy`` otherwise.

        A whole-term lease keeps that invoice as its billing until it ends. Its
        obligations stay uninvoiced by design and must never be invoiced again
        per period -- that is the double-billing route this guards.
        """
        self.ensure_one()
        if self.invoice_id and self.invoice_id.state != 'cancel':
            return 'whole_lease'
        return 'engine' if self.use_billing_engine else 'legacy'

    def action_generate_billing_schedule(self):
        """Build (or refresh) the obligation schedule for the whole term."""
        self._require_group('atmta_real_estate.group_rental_agent')
        created = self.env['realestate.contract.payment']
        for contract in self:
            created |= contract._generate_billing_schedule()
        return created

    def _generate_billing_schedule(self):
        self.ensure_one()
        if not self.use_billing_engine:
            raise UserError(_(
                "Lease '%s' is not configured for advanced billing. Enable "
                "'Use Advanced Billing' first, or use the legacy 'Generate "
                "Payment Schedule' action.", self.display_name))
        if self._billing_authority() == 'whole_lease':
            raise UserError(_(
                "Lease '%s' is billed by a single invoice for its whole term. It "
                "cannot also get a per-period billing schedule.", self.display_name))
        # Rows from the legacy generator carry no billing period, so the engine
        # cannot match them and would create a second, parallel schedule that
        # invoices the same rent again.
        legacy_rows = self.contract_payment_ids.filtered(
            lambda o: not o.period_start and o.state != 'cancelled')
        if legacy_rows:
            raise UserError(_(
                "Lease '%(lease)s' still has %(count)s payment(s) from the legacy "
                "schedule. Generating a billing schedule now would bill those "
                "periods twice; they must be converted to billing periods first.",
                lease=self.display_name, count=len(legacy_rows)))
        Obligation = self.env['realestate.contract.payment']
        targets = self._billing_targets()
        touched = Obligation

        for period_start, period_end, natural_end in self._billing_periods():
            for property_line in targets:
                existing = self._find_obligation(period_start, property_line)
                if existing and existing.state in ('invoiced', 'paid'):
                    # Never rewrite something already billed.
                    continue
                vals = self._obligation_values(
                    period_start, period_end, property_line, natural_end)
                if existing:
                    existing.charge_line_ids.filtered(
                        lambda line: line.charge_rule_id).unlink()
                    existing.write(vals)
                    touched |= existing
                else:
                    touched |= Obligation.create(dict(vals, contract_id=self.id))

        self.last_generated = fields.Datetime.now()
        if touched:
            self.message_post(body=_(
                "Billing schedule generated: %s obligation(s) created or "
                "refreshed.", len(touched)))
        return touched

    def _billing_targets(self):
        """What each period is billed against."""
        self.ensure_one()
        if self.billing_mode == 'property':
            return self.property_line_ids
        # ``[False]`` = a single whole-lease obligation per period.
        return [False]

    def _find_obligation(self, period_start, property_line):
        """Locate the obligation already covering this period/target.

        The idempotency key. Also matches legacy rows that predate
        ``period_start`` by falling back to the due date.
        """
        self.ensure_one()
        Obligation = self.env['realestate.contract.payment']
        domain = [
            ('contract_id', '=', self.id),
            ('period_start', '=', period_start),
        ]
        domain.append(('property_line_id', '=', property_line.id if property_line else False))
        found = Obligation.search(domain, limit=1)
        return found

    def _obligation_values(self, period_start, period_end, property_line,
                           natural_end=None):
        """Everything owed for one period against one target.

        ``natural_end`` is the period's full length; ``period_end`` may be
        shorter because the lease ends inside it. Proration measures the
        liable window against the NATURAL period -- otherwise a lease ending
        on the 15th would be charged a whole month for a half month.
        """
        self.ensure_one()
        method = self.company_id.re_proration_method or 'actual'

        natural_end = natural_end or period_end
        liable_start, liable_end = self._liable_window(property_line)
        # Cap the liable window at the truncated period end so the lease's
        # own end date shortens the charge.
        effective_end = min(liable_end, period_end) if liable_end else period_end
        gross = self._gross_rent_for(period_start, natural_end, property_line,
                                     method, liable_start, effective_end)
        relief = self._incentive_relief(period_start, period_end, gross, property_line)
        net = max(gross - relief, 0.0)

        note = prorate_lib.describe(
            method, period_start, natural_end, liable_start, effective_end)

        charge_commands = self._charge_line_commands(
            period_start, period_end, gross, property_line, method,
            liable_start, effective_end, natural_end)

        return {
            'property_line_id': property_line.id if property_line else False,
            'property_id': (property_line.property_id.id if property_line
                            else self.property_id.id or False),
            'period_start': period_start,
            'period_end': period_end,
            'date_due': self._due_date_for(period_start, period_end),
            'gross_rent': gross,
            'incentive_relief': relief,
            'amount': net,
            'proration_note': note,
            'charge_line_ids': charge_commands,
        }

    def _liable_window(self, property_line):
        """The window the tenant is actually liable for.

        For a per-property obligation that is the allocation's own term (a unit
        added mid-lease starts billing when it is added, not when the lease
        started).
        """
        self.ensure_one()
        if property_line:
            return property_line.start_date, property_line.end_date or self.end_date
        return self.start_date, self.end_date

    def _gross_rent_for(self, period_start, period_end, property_line, method,
                        liable_start, liable_end):
        """Contracted rent for the period, before incentives, after escalation."""
        self.ensure_one()
        full_period_rent = (property_line.allocated_rent
                            if property_line and property_line.allocated_rent
                            else self._rent_on(period_start))
        return prorate_lib.prorate(
            full_period_rent, method, period_start, period_end,
            liable_start, liable_end,
            precision=self.currency_id.decimal_places or 2,
        )

    def _incentive_relief(self, period_start, period_end, gross, property_line):
        self.ensure_one()
        total = 0.0
        for incentive in self.incentive_ids.filtered('active'):
            total += incentive._relief_for_period(
                period_start, period_end, gross, property_line)
        return min(total, gross)

    def _charge_line_commands(self, period_start, period_end, base_rent,
                              property_line, method, liable_start, liable_end,
                              natural_end=None):
        """One charge line per applicable rule."""
        self.ensure_one()
        commands = []
        for rule in self.charge_rule_ids.filtered('active'):
            if rule.property_line_id and property_line and rule.property_line_id != property_line:
                continue
            if rule.property_line_id and not property_line and self.billing_mode == 'property':
                continue
            amount = rule._amount_for_period(
                period_start, period_end, base_rent, property_line)
            if not amount:
                continue
            # `base_rent` arrives already prorated -- it is the gross rent
            # actually billed for this period. A percentage of it is therefore
            # already a part-period amount, and prorating it again applied the
            # time fraction twice: half a period at 1,000 rent with a 10%
            # charge billed 25 instead of 50. The other calculation types are
            # priced for a whole occurrence, so they still need the fraction.
            prorate_this = (rule.prorate_partial_periods
                            and rule.frequency != 'one_time'
                            and rule.calculation_type != 'percentage')
            if prorate_this:
                amount = prorate_lib.prorate(
                    amount, method, period_start, natural_end or period_end,
                    liable_start, liable_end,
                    precision=self.currency_id.decimal_places or 2)
            if not amount:
                continue
            commands.append((0, 0, {
                'name': rule.name,
                'amount': amount,
                'charge_type': self._charge_type_for(rule.charge_category),
                'product_id': rule.product_id.id or False,
                'charge_rule_id': rule.id,
                'property_line_id': property_line.id if property_line else False,
            }))
        return commands

    @api.model
    def _charge_type_for(self, category):
        """Map the rich charge category onto the legacy charge_type selection.

        The legacy field only has five values and downstream code reads it, so
        it is preserved and populated with the closest match rather than
        widened.
        """
        return {
            'maintenance': 'maintenance',
            'utilities': 'utility',
            'internet': 'utility',
            'service_charge': 'service',
            'management_fee': 'service',
            'municipality': 'other',
            'insurance': 'other',
            'parking': 'service',
            'furnished_premium': 'service',
        }.get(category, 'other')

    # ==================================================================
    # Invoicing -- Phase 15
    # ==================================================================
    def action_invoice_due_obligations(self, up_to=None):
        """Invoice every obligation due on or before ``up_to``.

        One customer invoice per obligation, so the tenant's statement lines up
        one-to-one with the schedule and a partial payment can be reconciled
        against a specific period.
        """
        self._require_group('atmta_real_estate.group_rental_agent')
        up_to = up_to or fields.Date.context_today(self)
        obligations = self.mapped('contract_payment_ids').filtered(
            lambda o: not o.move_id
            and o.state == 'draft'
            and o.date_due
            and o.date_due <= up_to
            and o.contract_id.lifecycle_state not in CLOSED_LIFECYCLE
        )
        return obligations._create_invoices()


class ObligationInvoicing(models.Model):
    _inherit = 'realestate.contract.payment'

    def _create_invoices(self):
        """Create and post one customer invoice per obligation.

        Rent lines and charge lines are emitted separately so the tenant can
        see what each amount is for. Taxes come from the product / rule -- Odoo
        computes them.
        """
        if not self:
            return self.env['account.move']
        whole_term = self.mapped('contract_id').filtered(
            lambda lease: lease._billing_authority() == 'whole_lease')
        if whole_term:
            raise UserError(_(
                "These leases are billed by a single invoice for their whole term "
                "and cannot be invoiced again per period: %s. Credit or cancel "
                "that invoice first if the lease must move to per-period billing.",
                ', '.join(whole_term.mapped('display_name'))))
        Move = self.env['account.move']
        created = Move
        for obligation in self:
            if obligation.move_id:
                continue
            contract = obligation.contract_id
            lines = obligation._invoice_line_commands()
            if not lines:
                continue
            move = Move.with_company(contract.company_id).create({
                'move_type': 'out_invoice',
                'partner_id': contract.partner_id.id,
                'company_id': contract.company_id.id,
                'currency_id': contract.currency_id.id,
                'invoice_date': obligation.date_due,
                'invoice_date_due': obligation.date_due,
                'contract_id': contract.id,
                'payment_reference': obligation.name or obligation.display_name,
                'invoice_origin': contract.name,
                'invoice_line_ids': lines,
            })
            obligation.move_id = move.id
            created |= move
        if created:
            self.env['realestate.account.tools'].post_moves(created)
        return created

    def _invoice_line_commands(self):
        """Invoice lines for one obligation: rent, incentive relief, charges."""
        self.ensure_one()
        contract = self.contract_id
        commands = []

        rent_product = self._rent_product()
        if self.gross_rent:
            rent_label = _("Rent %(period)s — %(prop)s", period=self.period_label or '',
                           prop=(self.property_id.display_name
                                 or contract.display_name))
            rent_line = {
                'name': rent_label,
                'quantity': 1.0,
                'price_unit': self.gross_rent,
            }
            if rent_product:
                rent_line['product_id'] = rent_product.id
            commands.append((0, 0, rent_line))

            # The concession is its own negative line so the tenant sees both
            # the contracted rent and what they were granted.
            if self.incentive_relief:
                relief_line = {
                    'name': _("Incentive / rent-free relief"),
                    'quantity': 1.0,
                    'price_unit': -self.incentive_relief,
                }
                if rent_product:
                    relief_line['product_id'] = rent_product.id
                commands.append((0, 0, relief_line))
        elif self.amount:
            # Legacy obligation with no gross recorded: bill the net amount.
            legacy_line = {
                'name': self.label or _("Rent"),
                'quantity': 1.0,
                'price_unit': self.amount,
            }
            if rent_product:
                legacy_line['product_id'] = rent_product.id
            commands.append((0, 0, legacy_line))

        for charge in self.charge_line_ids:
            if charge.charge_rule_id:
                commands.append((0, 0, charge.charge_rule_id._invoice_line_vals(
                    charge.amount)))
                continue
            manual = {
                'name': charge.name,
                'quantity': 1.0,
                'price_unit': charge.amount,
            }
            product = charge.product_id or rent_product
            if product:
                manual['product_id'] = product.id
            commands.append((0, 0, manual))
        return commands

    def _rent_product(self):
        """Product backing the rent line.

        Preference order: the property's own product (so income lands on the
        property's account), then the company default, then nothing -- in which
        case Odoo derives the account from the journal.
        """
        self.ensure_one()
        prop = self.property_id or self.contract_id.property_id
        if prop and prop.product_variant_id:
            return prop.product_variant_id
        return self.contract_id.company_id.re_rent_product_id

    # ------------------------------------------------------------------
    # Automation -- Phase 30
    # ------------------------------------------------------------------
    @api.model
    def _cron_generate_rent_invoices(self, limit=200):
        """Invoice obligations coming due, company by company.

        Batch-safe (``limit``), idempotent (skips anything already invoiced),
        multi-company safe (respects each company's lead days and its own
        opt-in flag), and retry-safe (a failure on one company is logged and
        does not abort the others).
        """
        Company = self.env['res.company'].sudo()
        total = 0
        for company in Company.search([('re_auto_bill', '=', True)]):
            lead = company.re_billing_lead_days or 0
            horizon = fields.Date.context_today(self) + relativedelta(days=lead)
            obligations = self.sudo().search([
                ('company_id', '=', company.id),
                ('move_id', '=', False),
                ('state', '=', 'draft'),
                ('date_due', '<=', horizon),
                ('contract_id.lifecycle_state', 'in', ('active', 'notice')),
                ('contract_id.use_billing_engine', '=', True),
            ], limit=limit, order='date_due')
            if not obligations:
                continue
            try:
                moves = obligations.with_company(company)._create_invoices()
                total += len(moves)
                self.env.cr.commit()
            except Exception:  # noqa: BLE001 - one company must not kill the run
                self.env.cr.rollback()
                _logger.exception(
                    "Rent invoicing failed for company %s", company.display_name)
        _logger.info("Rent invoicing cron issued %s invoice(s)", total)
        return total
