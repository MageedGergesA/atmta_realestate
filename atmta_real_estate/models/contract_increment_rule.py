import logging

from dateutil.relativedelta import relativedelta

from markupsafe import Markup
from odoo import _, api, fields, models

from .lease_states import CLOSED_LIFECYCLE

_logger = logging.getLogger(__name__)


class RealEstateContractIncrementRule(models.Model):
    _name = 'realestate.contract.increment.rule'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Legacy Price Rule'
    _order = 'start_month asc'

    start_month = fields.Integer(string="Start After (Months)", required=True, tracking=True)
    increase_type = fields.Selection([
        ('fixed', 'Fixed Amount'),
        ('percent', 'Percentage'),
    ], string="Type", required=True, default='fixed', tracking=True)
    increase_value = fields.Float(string="Value", required=True, tracking=True)
    priority = fields.Integer(string="Priority", default=10)

    name = fields.Char(string="Label", compute="_compute_name", store=True)
    discount = fields.Boolean(string="Is Discount", help="Used to mark this rule as a discount (shown in label only).")
    duration_months = fields.Integer(
        string="Duration (Months)",
        default=0,
        help="How many months this rule remains active. Leave 0 to apply once only."
    )

    @api.depends('increase_value', 'increase_type', 'start_month', 'discount', 'priority')
    def _compute_name(self):
        for rec in self:
            is_discount = rec.discount or rec.increase_value < 0
            direction_label = "Discount" if is_discount else "Increase"

            value = abs(rec.increase_value)
            if rec.increase_type == 'fixed':
                value_str = f"{value:.0f} EGP"
            else:
                value_str = f"{value:.0f}%"

            month_str = f"after month {rec.start_month}" if rec.start_month else "from start"
            duration_str = f" for {rec.duration_months} month(s)" if rec.duration_months else ""
            rec.name = f"{direction_label}: {value_str} after month {rec.start_month}{duration_str} (Priority {rec.priority})"


class ContractLegacyPriceRules(models.Model):
    """Carry legacy price rules over to the rent escalation and incentive engines.

    ``realestate.contract.increment.rule`` is a shared catalogue of "+X after
    month N" rules that only the retired legacy schedule generator applied.
    Nothing reads them any more, so a lease's agreed increases and discounts
    would silently stop existing. This translates them, per lease, into dated
    rent escalations (increases) and incentives (discounts) -- the records the
    billing engine uses -- but only where the translation reproduces the rent
    the retired generator billed.

    Rules the engines cannot represent are **flagged, never guessed**:

    * an increase that starts with the lease (an escalation must come after the
      starting rent);
    * a temporary increase (``duration_months``) -- escalations are permanent
      steps;
    * two increases starting the same month (one escalation per date);
    * any set of increases whose escalation chain would not reproduce the legacy
      rent at each rule's start month (the generator applied rules by priority
      on each instalment; escalations compound in date order).

    Increases and discounts are each converted all-or-nothing per lease, so a
    partial conversion never misrepresents what was agreed. Every created record
    carries a marker, so running the conversion again creates nothing new.
    Closed leases are skipped: there are no future terms to protect.
    """
    _inherit = 'realestate.contract'

    def _legacy_rule_marker(self, rule):
        return 'legacy price rule #%s' % rule.id

    def _legacy_rent_at_month(self, months, rules):
        """Rent the retired generator billed ``months`` after start, before discounts."""
        self.ensure_one()
        price = self.price or 0.0
        active = rules.filtered(
            lambda r: r.start_month <= months
            and (not r.duration_months or months < r.start_month + r.duration_months))
        for rule in active.sorted(lambda r: (r.priority, r.start_month, r._origin.id or 0)):
            value = abs(rule.increase_value or 0.0)
            if rule.increase_type == 'percent':
                value = price * value / 100.0
            price += value
        return price

    def _escalated_rent_at_month(self, months, rules):
        """Rent the escalation chain would produce for the same rules."""
        self.ensure_one()
        rent = self.price or 0.0
        for rule in rules.sorted(lambda r: (r.start_month, r.priority, r._origin.id or 0)):
            if rule.start_month > months:
                break
            value = abs(rule.increase_value or 0.0)
            rent = rent * (1.0 + value / 100.0) if rule.increase_type == 'percent' else rent + value
        return rent

    def _convert_legacy_price_rules(self):
        """Convert each lease's legacy rules. Returns a report dict."""
        Escalation = self.env['realestate.rent.escalation.rule']
        Incentive = self.env['realestate.contract.incentive']
        report = {'escalations': 0, 'incentives': 0, 'flagged': {}, 'skipped': {}}

        for lease in self:
            if lease.lifecycle_state in CLOSED_LIFECYCLE or not lease.start_date:
                continue
            flagged, skipped, created = [], [], []
            currency = lease.currency_id

            # ---- increases -> escalations -----------------------------------
            increments = lease.increment_rule_ids
            in_term = increments.browse()
            # Set whenever an increase cannot be represented: the lease's
            # increases then convert all-or-nothing, never partially.
            increments_blocked = False
            for rule in increments:
                effective = lease.start_date + relativedelta(months=rule.start_month)
                if rule.duration_months:
                    increments_blocked = True
                    flagged.append(_('"%s" is a temporary increase; rent escalations are permanent.', rule.display_name))
                elif rule.start_month <= 0:
                    increments_blocked = True
                    flagged.append(_('"%s" applies from the lease start; set it as the base rent instead.', rule.display_name))
                elif lease.end_date and effective > lease.end_date:
                    skipped.append(_('"%s" would start after the lease ends.', rule.display_name))
                else:
                    in_term |= rule
            if len(set(in_term.mapped('start_month'))) != len(in_term):
                increments_blocked = True
                flagged.append(_('Two increases start in the same month; a lease can have one escalation per date.'))
            elif in_term:
                for rule in in_term:
                    legacy = lease._legacy_rent_at_month(rule.start_month, in_term)
                    chained = lease._escalated_rent_at_month(rule.start_month, in_term)
                    if currency.compare_amounts(legacy, chained):
                        increments_blocked = True
                        flagged.append(_(
                            'The increases would give %(chained)s from month %(month)s instead '
                            'of the %(legacy)s billed before; their order depends on priority.',
                            chained=currency.round(chained), month=rule.start_month,
                            legacy=currency.round(legacy)))
                        break
            if in_term and not increments_blocked:
                existing_markers = set(lease.escalation_rule_ids.mapped('notes'))
                existing_dates = set(lease.escalation_rule_ids.mapped('effective_date'))
                for rule in in_term.sorted(lambda r: (r.start_month, r.priority, r._origin.id or 0)):
                    marker = lease._legacy_rule_marker(rule)
                    if marker in existing_markers:
                        continue
                    effective = lease.start_date + relativedelta(months=rule.start_month)
                    if effective in existing_dates:
                        flagged.append(_('"%s" falls on a date that already has an escalation.', rule.display_name))
                        continue
                    value = abs(rule.increase_value or 0.0)
                    Escalation.create({
                        'contract_id': lease.id,
                        'effective_date': effective,
                        'sequence': rule.priority,
                        'escalation_type': 'percentage' if rule.increase_type == 'percent' else 'fixed',
                        'percentage': value if rule.increase_type == 'percent' else 0.0,
                        'fixed_amount': value if rule.increase_type == 'fixed' else 0.0,
                        'notes': marker,
                    })
                    created.append(_('escalation from %s', effective))
                    report['escalations'] += 1

            # ---- discounts -> incentives -------------------------------------
            existing_reasons = set(lease.with_context(active_test=False).incentive_ids.mapped('reason'))
            for rule in lease.discount_rule_ids:
                marker = lease._legacy_rule_marker(rule)
                if marker in existing_reasons:
                    continue
                start = lease.start_date + relativedelta(months=rule.start_month)
                if lease.end_date and start > lease.end_date:
                    skipped.append(_('"%s" would start after the lease ends.', rule.display_name))
                    continue
                end = (start + relativedelta(months=rule.duration_months, days=-1)
                       if rule.duration_months else lease.end_date)
                if lease.end_date:
                    end = min(end, lease.end_date)
                value = abs(rule.increase_value or 0.0)
                if not end:
                    flagged.append(_('"%s" has no end date to carry over.', rule.display_name))
                    continue
                if rule.increase_type == 'percent' and not 0 < value <= 100:
                    flagged.append(_('"%s" is not a percentage between 0 and 100.', rule.display_name))
                    continue
                Incentive.create({
                    'contract_id': lease.id,
                    'name': _('Discount carried over: %s', rule.display_name),
                    'incentive_type': 'percent_discount' if rule.increase_type == 'percent' else 'fixed_discount',
                    'percentage': value if rule.increase_type == 'percent' else 0.0,
                    'amount': value if rule.increase_type == 'fixed' else 0.0,
                    'start_date': start,
                    'end_date': end,
                    'reason': marker,
                })
                created.append(_('discount %(start)s to %(end)s', start=start, end=end))
                report['incentives'] += 1

            if flagged:
                report['flagged'][lease.display_name] = flagged
            if skipped:
                report['skipped'][lease.display_name] = skipped
            if created or flagged:
                items = created + [_('Needs attention: %s', f) for f in flagged]
                lines = Markup('').join(Markup('<li>%s</li>') % item for item in items)
                lease.message_post(body=Markup(_(
                    'Legacy price rules carried over to rent escalations and incentives:<ul>%s</ul>'
                )) % lines)
        return report

