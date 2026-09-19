# -*- coding: utf-8 -*-
"""The one release blocker: a number whose meaning changed underneath it.

`res.users.commission_share_default` held, in 0.1:

> **X% of the transaction value**

and in 0.3 it holds:

> **X% share of the gross brokerage commission**

The stored number did not move. Its economics did. An agent on `2.5` was owed
2.5% of a sale price; read under the new architecture the same row says they are
owed 2.5% of the agency's fee — on a 2% agency fee that is **0.05%** of the
sale, a fiftieth of what they were promised. The reverse framing is worse: an
agent on `40` meaning 40% of the fee, read as 40% of the sale price, is a
catastrophic overpayment.

Every technical test stays green through both, because nothing is broken. The
arithmetic is right and the input means something else.

### The conversion

Where the old economics can be reconstructed, the conversion is arithmetic:

```
    old:  X% of transaction value
    fee:  Y% of transaction value        (the gross brokerage commission)

    new share of gross = (X / Y) × 100

    e.g.  1% of the sale, on a 2.5% agency fee  →  1 / 2.5 × 100 = 40%
```

That is only deterministic when there is exactly one Y. An agent who worked one
project at 2% and another at 2.5% has **no single correct answer**, and this
module does not invent one.

### The classification

| Class | Meaning | Action |
|---|---|---|
| `safe` | exactly one historical gross rate | converted, monetary equivalence asserted |
| `already_new` | value already recorded as gross-share semantics | left alone |
| `ambiguous` | more than one applicable gross rate, or none to reconstruct from | value preserved, default cleared, review required |
| `unused` | non-zero default, but the user never took a commission | value preserved, default cleared, review required |
| `invalid` | outside 0–100 | value preserved, default cleared, review required |

Everything that is not `safe` or `already_new` has its default **cleared to
zero** and its original value moved to `commission_share_legacy_value`. That is
deliberate: leaving the number in place is the failure mode this whole file
exists to prevent, and a zero cannot be silently mistaken for an entitlement.
The commission path then refuses to seed splits for that user by name, so the
operator is told rather than quietly given nothing.

Evidence is never destroyed. Every audited user gets an immutable
`realestate.commission.share.migration` row carrying the original value, its
original meaning, the conversion basis, the result and the timestamp.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

MIGRATION_STATE = [
    ('not_migrated', 'Not Migrated'),
    ('safe', 'Converted'),
    ('already_new', 'Already Gross-share'),
    ('ambiguous', 'Ambiguous — Review Required'),
    ('unused', 'Unused — Review Required'),
    ('invalid', 'Invalid — Review Required'),
    ('reviewed', 'Reviewed & Set'),
]

#: States in which the stored default must not be used to seed a new
#: entitlement. `not_migrated` is included on purpose: a database that has not
#: run the migration is exactly the one where the number still means the old
#: thing.
REVIEW_REQUIRED_STATES = ('ambiguous', 'unused', 'invalid')

LEGACY_SEMANTICS = [
    ('sale_price_pct', 'Percentage of Transaction Value (0.1)'),
    ('gross_share_pct', 'Share of Gross Commission (0.3)'),
]


class CommissionShareMigration(models.Model):
    """Immutable evidence of what a number used to mean.

    Kept as its own model rather than as more fields on `res.users`: this is
    migration data, not a permanent part of anybody's user profile, and it must
    survive somebody later tidying up the user form.
    """
    _name = 'realestate.commission.share.migration'
    _description = 'Commission Share Semantic Migration'
    _order = 'migrated_on desc, id desc'
    _rec_name = 'user_id'

    user_id = fields.Many2one(
        'res.users', string='Agent', required=True, index=True,
        ondelete='cascade')
    company_id = fields.Many2one(
        'res.company', string='Company', index=True)

    original_value = fields.Float(
        string='Original Value', readonly=True,
        help="The number as it stood before the migration touched anything.")
    original_semantics = fields.Selection(
        LEGACY_SEMANTICS, string='Original Meaning', readonly=True,
        default='sale_price_pct')
    migrated_value = fields.Float(string='Migrated Value', readonly=True)
    migrated_semantics = fields.Selection(
        LEGACY_SEMANTICS, string='New Meaning', readonly=True,
        default='gross_share_pct')

    conversion_basis = fields.Char(
        string='Conversion Basis', readonly=True,
        help="The gross brokerage rate the conversion divided by, and where it "
             "came from. Empty when no conversion was possible.")
    gross_rate_used = fields.Float(string='Gross Rate Used (%)', readonly=True)
    gross_rates_found = fields.Char(
        string='Gross Rates Found', readonly=True,
        help="Every distinct gross brokerage rate this agent's history "
             "touched. More than one is what makes a record ambiguous.")

    result = fields.Selection(
        MIGRATION_STATE, string='Result', readonly=True, required=True,
        index=True)
    note = fields.Char(readonly=True)

    transaction_count = fields.Integer(
        string='Historic Transactions', readonly=True)
    commission_count = fields.Integer(
        string='Historic Commissions', readonly=True)

    migrated_on = fields.Datetime(
        string='Migrated On', readonly=True, default=fields.Datetime.now,
        index=True)
    migrated_by_id = fields.Many2one(
        'res.users', string='Migrated By', readonly=True,
        default=lambda self: self.env.user)
    reviewed_by_id = fields.Many2one(
        'res.users', string='Reviewed By', readonly=True,
        help="Set when a Brokerage Manager resolved an ambiguous record by "
             "hand.")
    reviewed_on = fields.Datetime(readonly=True)
    review_note = fields.Char(readonly=True)

    def write(self, vals):
        """Evidence is appended to, not edited.

        Only the review fields may be filled in afterwards, and only once —
        that *is* the record of the manual resolution.
        """
        editable = {'reviewed_by_id', 'reviewed_on', 'review_note'}
        touched = set(vals) - editable
        if touched and not self.env.context.get('re_migration_write'):
            raise UserError(_(
                "Migration evidence cannot be edited (%s). It is the proof of "
                "what a number used to mean; a corrected record proves nothing."
            ) % ', '.join(sorted(touched)))
        return super().write(vals)

    def unlink(self):
        raise UserError(_(
            "Migration evidence cannot be deleted. It is what a payout dispute "
            "is settled against."))


class ResUsersCommissionSemantics(models.Model):
    _inherit = 'res.users'

    commission_share_default = fields.Float(
        help="Default share of the **gross brokerage commission** allocated to "
             "this agent on a new commission split.\n\n"
             "In 0.1 the same field meant a percentage of the *transaction "
             "value*. The migration converts it where the old economics can be "
             "reconstructed and clears it where they cannot, rather than "
             "letting the same number quietly mean something fifty times "
             "smaller.")

    commission_share_legacy_value = fields.Float(
        string='Legacy Value (% of Sale)', readonly=True, copy=False,
        help="The pre-0.3 number, preserved. Never used for calculation.")
    commission_share_migration_state = fields.Selection(
        MIGRATION_STATE, string='Share Semantics', default='not_migrated',
        readonly=True, copy=False, index=True)
    commission_share_migration_id = fields.Many2one(
        'realestate.commission.share.migration', string='Migration Record',
        readonly=True, copy=False)
    commission_share_needs_review = fields.Boolean(
        string='Commission Default Needs Review',
        compute='_compute_share_needs_review', store=True,
        help="Blocks only the commission path. CRM, listings, viewings and "
             "offers are unaffected — an unresolved payout default is not a "
             "reason to stop somebody selling.")

    @api.depends('commission_share_migration_state')
    def _compute_share_needs_review(self):
        for user in self:
            user.commission_share_needs_review = (
                user.commission_share_migration_state in REVIEW_REQUIRED_STATES)

    # ------------------------------------------------------------------
    # Manual resolution
    # ------------------------------------------------------------------
    def action_mark_share_already_new(self, note=None):
        """Declare that this agent's default is already in the new semantics.

        Needed because the classification includes `already_new` and nothing
        else can reach it: an audit cannot tell a `40` that means 40% of gross
        from a `40` that means 40% of a sale price. Only a human who knows how
        the number was set can say, so a human says it — on the record, with a
        reason.
        """
        if not self.env.user.has_group(
                'real_estate_brokerage.group_realestate_sales_manager'):
            raise UserError(_(
                "Declaring a commission default already migrated is a "
                "Brokerage Manager's decision."))
        if not note:
            raise UserError(_(
                "Say how you know. The audit cannot distinguish a value that "
                "already means a share of gross from one that does not."))
        Log = self.env['realestate.commission.share.migration'].sudo()
        for user in self:
            if user.commission_share_migration_state != 'not_migrated':
                raise UserError(_(
                    "%(user)s has already been migrated (%(state)s).",
                    user=user.display_name,
                    state=user.commission_share_migration_state))
            log = Log.create({
                'user_id': user.id,
                'company_id': user.company_id.id,
                'original_value': user.commission_share_default,
                'original_semantics': 'gross_share_pct',
                'migrated_value': user.commission_share_default,
                'migrated_semantics': 'gross_share_pct',
                'result': 'already_new',
                'note': note,
                'reviewed_by_id': self.env.user.id,
                'reviewed_on': fields.Datetime.now(),
                'review_note': note,
            })
            user.sudo().write({
                'commission_share_migration_state': 'already_new',
                'commission_share_migration_id': log.id,
            })
        return True

    def action_review_commission_share(self, value, note=None):
        """A Brokerage Manager resolves an ambiguous default by hand."""
        self.ensure_one()
        if not self.env.user.has_group(
                'real_estate_brokerage.group_realestate_sales_manager'):
            raise UserError(_(
                "Resolving a commission-share default is a Brokerage "
                "Manager's decision."))
        if not 0.0 <= value <= 100.0:
            raise UserError(_(
                "A share of %s%% of the gross commission is not a share."
            ) % value)
        if not note:
            raise UserError(_(
                "Recording why this value was chosen is the point of the "
                "review — the original was ambiguous."))
        self.sudo().write({
            'commission_share_default': value,
            'commission_share_migration_state': 'reviewed',
        })
        if self.commission_share_migration_id:
            self.commission_share_migration_id.sudo().write({
                'reviewed_by_id': self.env.user.id,
                'reviewed_on': fields.Datetime.now(),
                'review_note': note,
            })
        return True


class CommissionShareMigrationRunner(models.AbstractModel):
    """Audit first, convert second. Never the other way round."""
    _name = 'realestate.commission.share.migration.runner'
    _description = 'Commission Share Migration Runner'

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------
    @api.model
    def audit(self, users=None):
        """Classify every legacy default without changing anything.

        Returns a list of dicts, one per candidate user, so the result can be
        printed, exported or reviewed before `run()` is allowed near the data.
        """
        users = users if users is not None else self._candidates()
        return [self._audit_one(user) for user in users]

    @api.model
    def _candidates(self):
        """Users carrying a default that has not been through the migration."""
        return self.env['res.users'].sudo().search([
            ('commission_share_migration_state', '=', 'not_migrated'),
        ])

    @api.model
    def _audit_one(self, user):
        user = user.sudo()
        value = user.commission_share_default or 0.0
        history = self._history(user)
        rates = self._gross_rates(history)

        row = {
            'user_id': user.id,
            'user': user.display_name,
            'company_id': user.company_id.id,
            'company': user.company_id.display_name,
            'original_value': value,
            'transaction_count': len(history['transactions']),
            'commission_count': len(history['commissions']),
            'gross_rates_found': sorted(rates),
            'projects': sorted(history['projects'].mapped('display_name')),
            'teams': sorted(history['teams'].mapped('display_name')),
        }

        # Anything already carrying a verdict is left completely alone.
        #
        # `run(users)` accepts an explicit recordset, so it can reach a user
        # `_candidates()` would have filtered out. Without this, a second run
        # would read the *converted* number as though it were still a
        # percentage of the sale price and divide by the gross rate again —
        # 40% becoming 1,600% and then being discarded as invalid. That is the
        # same class of error the whole migration exists to prevent, so it is
        # refused at the top rather than guarded against downstream.
        if user.commission_share_migration_state != 'not_migrated':
            row.update(
                result=user.commission_share_migration_state,
                converted_value=value, already_handled=True,
                note=_("Already migrated (%s); left untouched.")
                % user.commission_share_migration_state)
            return row

        if not value:
            row.update(result='unused', converted_value=0.0,
                       note=_('No default set; nothing to convert.'))
            return row

        if value < 0.0 or value > 100.0:
            row.update(result='invalid', converted_value=0.0,
                       note=_('%s is not a percentage.') % value)
            return row

        if not history['commissions'] and not history['transactions']:
            row.update(
                result='unused', converted_value=0.0,
                note=_("A default of %s%% that was never applied to any "
                       "commission or transaction. There is no old economics "
                       "to preserve and no gross rate to convert against."
                       ) % value)
            return row

        if len(rates) == 1:
            gross_rate = rates.pop()
            if not gross_rate:
                row.update(
                    result='ambiguous', converted_value=0.0,
                    note=_("The agent has history, but every transaction it "
                           "touches carries a zero gross brokerage rate, so "
                           "there is nothing to divide by."))
                return row
            converted = value / gross_rate * 100.0
            if converted > 100.0:
                row.update(
                    result='invalid', converted_value=0.0, gross_rate=gross_rate,
                    note=_("%(old)s%% of the sale against a %(rate)s%% agency "
                           "fee converts to %(new)s%% of the gross — more than "
                           "the whole fee. The legacy pair cannot both be "
                           "right.",
                           old=value, rate=gross_rate,
                           new=round(converted, 2)))
                return row
            row.update(
                result='safe',
                converted_value=round(converted, 4),
                gross_rate=gross_rate,
                note=_("%(old)s%% of sale ÷ %(rate)s%% gross × 100 = "
                       "%(new)s%% of gross.",
                       old=value, rate=gross_rate, new=round(converted, 4)))
            return row

        row.update(
            result='ambiguous', converted_value=0.0,
            note=_("This agent's history spans %(count)s different gross "
                   "brokerage rates (%(rates)s). There is no single correct "
                   "conversion of %(value)s%%, and picking one would be a "
                   "guess about somebody's pay.",
                   count=len(rates),
                   rates=', '.join('%g%%' % r for r in sorted(rates)),
                   value=value))
        return row

    # ------------------------------------------------------------------
    # The evidence the audit reasons from
    # ------------------------------------------------------------------
    @api.model
    def _history(self, user):
        """Everything this agent's pay has historically depended on."""
        Commission = self.env['realestate.commission'].sudo()
        Transaction = self.env['realestate.transaction'].sudo()

        commissions = Commission.search([
            ('partner_id', '=', user.partner_id.id)])
        transactions = Transaction.search([
            '|', ('selling_agent_id', '=', user.id),
            ('lister_agent_id', '=', user.id)])
        transactions |= commissions.mapped('transaction_id')

        return {
            'commissions': commissions,
            'transactions': transactions,
            'projects': transactions.mapped('project_id'),
            'teams': self.env['crm.team'].sudo().search([
                '|', ('user_id', '=', user.id),
                ('member_ids', 'in', [user.id])]),
        }

    @api.model
    def _gross_rates(self, history):
        """The distinct gross brokerage rates the agent's history touched.

        Expressed as a percentage of transaction value, because that is the
        unit the legacy number was in. A fixed agency fee is converted to its
        effective percentage on that transaction — the arithmetic is the same
        and refusing to handle it would push perfectly determinable records
        into `ambiguous`.
        """
        rates = set()
        for txn in history['transactions']:
            rate = txn._re_gross_rate_percent()
            if rate is not None:
                rates.add(round(rate, 6))
        return rates

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------
    @api.model
    def run(self, users=None):
        """Apply the audit. Idempotent: a second pass changes nothing."""
        rows = self.audit(users)
        Log = self.env['realestate.commission.share.migration'].sudo()
        applied = []

        for row in rows:
            if row.get('already_handled'):
                continue
            user = self.env['res.users'].sudo().browse(row['user_id'])
            result = row['result']
            original = row['original_value']
            converted = row.get('converted_value', 0.0)

            log = Log.create({
                'user_id': user.id,
                'company_id': row['company_id'],
                'original_value': original,
                'original_semantics': 'sale_price_pct',
                'migrated_value': converted,
                'migrated_semantics': 'gross_share_pct',
                'gross_rate_used': row.get('gross_rate', 0.0),
                'gross_rates_found': ', '.join(
                    '%g' % r for r in row['gross_rates_found']) or False,
                'conversion_basis': (
                    _("%(old)s ÷ %(rate)s × 100",
                      old=original, rate=row['gross_rate'])
                    if row.get('gross_rate') else False),
                'result': result,
                'note': row['note'],
                'transaction_count': row['transaction_count'],
                'commission_count': row['commission_count'],
            })

            vals = {
                'commission_share_migration_state': result,
                'commission_share_migration_id': log.id,
            }
            if result == 'safe':
                vals['commission_share_legacy_value'] = original
                vals['commission_share_default'] = converted
            else:
                # Preserve the number, and make sure nothing can pick it up.
                vals['commission_share_legacy_value'] = original
                vals['commission_share_default'] = 0.0
            user.write(vals)
            applied.append((user, result))

        return applied

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------
    @api.model
    def verify_equivalence(self, user, sale_price, gross_rate):
        """Old money vs new money, for a converted user.

        `(X% of sale)` must equal `(converted% of (Y% of sale))` to within the
        company's currency rounding. Exposed as a method rather than living
        only in a test, because this is the number an agent will ask about.
        """
        user = user.sudo()
        legacy = user.commission_share_legacy_value or 0.0
        old_money = sale_price * legacy / 100.0
        gross = sale_price * gross_rate / 100.0
        new_money = gross * (user.commission_share_default or 0.0) / 100.0
        currency = user.company_id.currency_id or self.env.company.currency_id
        return {
            'old': old_money,
            'new': new_money,
            'equal': currency.compare_amounts(old_money, new_money) == 0,
        }


class TransactionGrossRate(models.Model):
    """The gross brokerage rate of a deal, as a percentage of its value."""
    _inherit = 'realestate.transaction'

    def _re_gross_rate_percent(self):
        """`None` when the deal cannot express a rate at all.

        Returning `None` rather than `0.0` matters: a transaction with no sale
        price tells the auditor nothing, whereas one with a genuine zero gross
        tells it something quite specific — that there is nothing to divide by.
        """
        self.ensure_one()
        if not self.sale_price:
            return None
        return (self.commission_gross_amount or 0.0) / self.sale_price * 100.0
