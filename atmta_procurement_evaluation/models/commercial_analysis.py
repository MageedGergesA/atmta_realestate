# -*- coding: utf-8 -*-
"""M6 — commercial analysis: what an offer evaluates to, and why.

```
    RAW BID          what the vendor submitted        (M5, immutable)
    + adjustments    what evaluation adds or removes  (M6, itemised)
    = EVALUATED COST what the offer is worth to us    (M6, frozen)
```

All three are kept and all three are shown. Replacing 2,800,000 with 2,900,000
because freight was added would destroy the only record of what the vendor
actually offered, and the vendor would rightly dispute it.

### The exchange rate is evidence, not a lookup

`res.currency._convert()` is the right engine and M6 uses it — but a rate read
live at report time is not evidence, it is today's opinion about the past.
Every conversion here stores the source currency, the target currency, the
**date the plan declared**, the rate that was in force on that date, and the
converted amount. A rate published afterwards changes none of them.

### Odoo's rate lookup silently returns 1.0

`res_currency._get_rates` builds `COALESCE((rate on or before date), (earliest
rate), 1.0)` (`base/models/res_currency.py`). So a currency with no rate at all
converts one-for-one and looks converted. For an evaluation that is the worst
possible failure — a USD offer would be compared against EGP offers at par and
would win. M6 therefore checks that a real rate exists for the declared date
and **refuses to normalise** when it does not, rather than accepting a number
Odoo made up.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

#: What an adjustment can be. Typed on purpose: a column of unexplained
#: numbers called "adjustment" is how an evaluation stops being auditable.
ADJUSTMENT_TYPES = [
    ('freight', 'Freight'),
    ('shipping', 'Shipping'),
    ('installation', 'Installation'),
    ('commissioning', 'Commissioning'),
    ('insurance', 'Insurance'),
    ('discount', 'Evaluated Discount'),
    ('warranty', 'Warranty'),
    ('payment_terms', 'Payment Terms'),
    ('delivery', 'Delivery'),
    ('lifecycle', 'Lifecycle Cost'),
    ('other', 'Other (rationale required)'),
]


class CommercialAnalysis(models.Model):
    _name = 'realestate.procurement.commercial.analysis'
    _description = 'Commercial Evaluation Analysis'
    _order = 'candidate_id'
    _rec_name = 'candidate_id'

    candidate_id = fields.Many2one(
        'realestate.procurement.evaluation.candidate', required=True,
        ondelete='cascade', index=True)
    round_id = fields.Many2one(
        related='candidate_id.round_id', store=True, index=True)
    company_id = fields.Many2one(
        related='candidate_id.company_id', store=True, index=True)
    project_id = fields.Many2one(
        related='candidate_id.project_id', store=True, index=True)
    partner_id = fields.Many2one(
        related='candidate_id.partner_id', store=True, index=True)
    bid_response_id = fields.Many2one(
        related='candidate_id.bid_response_id', store=True)

    #: The raw offer, copied — never a related field onto M5. A related field
    #: would make the evaluation follow the bid, and the bid is immutable
    #: precisely so the evaluation can be pinned to it.
    raw_currency_id = fields.Many2one('res.currency', readonly=True)
    raw_amount = fields.Monetary(
        readonly=True, currency_field='raw_currency_id',
        string='Raw Bid (as submitted)')

    evaluation_currency_id = fields.Many2one('res.currency', readonly=True)
    rate_date = fields.Date(readonly=True, string='Rate Date')
    rate_used = fields.Float(
        readonly=True, digits=(16, 8),
        help="The conversion rate in force on the declared date, stored so "
             "the evaluation cannot be re-derived from a later rate.")
    rate_source = fields.Char(readonly=True)
    converted_amount = fields.Monetary(
        readonly=True, currency_field='evaluation_currency_id',
        string='Converted')
    adjustment_total = fields.Monetary(
        readonly=True, currency_field='evaluation_currency_id')
    evaluated_cost = fields.Monetary(
        readonly=True, currency_field='evaluation_currency_id',
        string='Evaluated Cost')
    financial_score = fields.Float(readonly=True)
    normalised_on = fields.Datetime(readonly=True)
    normalised_by_id = fields.Many2one('res.users', readonly=True)

    review_flag = fields.Selection([
        ('none', 'None'),
        ('review_required', 'Review Required'),
    ], default='none', readonly=True,
        help="Set when an offer sits materially away from its peers. It is a "
             "prompt to look, never a disqualification and never an "
             "accusation.")
    review_note = fields.Text(readonly=True)
    adjustment_ids = fields.One2many(
        'realestate.procurement.commercial.adjustment', 'analysis_id')
    leveling_line_ids = fields.One2many(
        'realestate.procurement.leveling.line', 'analysis_id')

    _sql_constraints = [
        ('candidate_uniq', 'unique(candidate_id)',
         'A candidate has one commercial analysis per round.'),
    ]

    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        analyses = super().create(vals_list)
        for analysis in analyses:
            analysis.candidate_id._engine().write(
                {'analysis_id': analysis.id})
        return analyses

    def _normalise(self):
        """Convert the offer onto the plan's basis and freeze the evidence."""
        for analysis in self:
            candidate = analysis.candidate_id
            round_ = candidate.round_id
            if round_.state == 'finalised':
                raise UserError(_(
                    "%s is finalised. Its commercial basis cannot be "
                    "recalculated.") % round_.name)
            plan = round_.plan_id
            bid = candidate.bid_response_id
            source = bid.currency_id
            target = plan.evaluation_currency_id
            date = plan.rate_date_for()

            rate = analysis._rate_for(source, target, date)
            converted = target.round((bid.amount_untaxed or 0.0) * rate)
            analysis._engine().write({
                'raw_currency_id': source.id,
                'raw_amount': bid.amount_untaxed,
                'evaluation_currency_id': target.id,
                'rate_date': date,
                'rate_used': rate,
                'rate_source': _("res.currency rate on %s") % date,
                'converted_amount': converted,
                'normalised_on': fields.Datetime.now(),
                'normalised_by_id': self.env.user.id,
            })
            analysis._recompute_cost()
            analysis._build_leveling()
        return True

    def _rate_for(self, source, target, date):
        """The declared date's rate — or a refusal, never a silent 1.0."""
        self.ensure_one()
        if source == target:
            return 1.0
        company = self.company_id or self.env.company
        if not self._rate_exists(source, date, company) or \
                not self._rate_exists(target, date, company):
            # `source=` is reserved by Odoo's `_()` (it names the message
            # itself), so passing it as a placeholder raises TypeError and the
            # refusal never reaches the user. Caught by
            # `test_a_missing_rate_is_refused_rather_than_treated_as_one_to_one`.
            raise UserError(_(
                "There is no exchange rate for %(from_currency)s → "
                "%(to_currency)s on %(rate_date)s.\n\n"
                "Odoo's own lookup would fall back to 1.00 here, which would "
                "compare a %(from_currency)s offer against %(to_currency)s "
                "ones at par and hand it the tender. Publish the rate for the "
                "declared date, or change the plan's rate basis — the "
                "evaluation will not guess.",
                from_currency=source.name, to_currency=target.name,
                rate_date=date))
        return self.env['res.currency']._get_conversion_rate(
            source, target, company, date)

    @api.model
    def _rate_exists(self, currency, date, company):
        """A real published rate on or before the date, for this company."""
        if currency == company.currency_id:
            return True
        return bool(self.env['res.currency.rate'].sudo().search_count([
            ('currency_id', '=', currency.id),
            ('name', '<=', date),
            ('company_id', 'in', (False, company.root_id.id)),
        ]))

    def _recompute_cost(self):
        for analysis in self:
            total = sum(analysis.adjustment_ids.mapped('signed_amount'))
            currency = analysis.evaluation_currency_id
            analysis._engine().write({
                'adjustment_total': currency.round(total) if currency else total,
                'evaluated_cost': currency.round(
                    (analysis.converted_amount or 0.0) + total)
                if currency else (analysis.converted_amount or 0.0) + total,
            })
        return True

    def _build_leveling(self):
        """Line-by-line comparison, on the same frozen basis as the header."""
        self.ensure_one()
        Line = self.env['realestate.procurement.leveling.line']
        self.leveling_line_ids.sudo().unlink()
        rate = self.rate_used or 1.0
        target = self.evaluation_currency_id
        for bid_line in self.bid_response_id.line_ids:
            Line.create({
                'analysis_id': self.id,
                'bid_line_id': bid_line.id,
                'sourcing_line_id': bid_line.sourcing_line_id.id,
                'description': bid_line.name,
                'quantity': bid_line.quantity,
                'raw_currency_id': self.raw_currency_id.id,
                'raw_unit_price': bid_line.price_unit,
                'raw_line_total': bid_line.amount_untaxed,
                'normalised_unit_price': target.round(
                    bid_line.price_unit * rate) if target else 0.0,
                'normalised_line_total': target.round(
                    bid_line.amount_untaxed * rate) if target else 0.0,
                'evaluation_currency_id': target.id,
                'is_included': True,
            })
        return True

    # ------------------------------------------------------------------
    def action_add_adjustment(self, adjustment_type, amount, rationale=None,
                              evidence=None):
        """An itemised, explained change to the evaluated cost."""
        self.ensure_one()
        if self.round_id.state == 'finalised':
            raise UserError(_(
                "%s is finalised.") % self.round_id.name)
        if not rationale:
            raise UserError(_(
                "A commercial adjustment needs a rationale. An unexplained "
                "figure moving a vendor up or down the ranking is the one "
                "thing an evaluation file cannot survive."))
        adjustment = self.env[
            'realestate.procurement.commercial.adjustment'].create({
                'analysis_id': self.id,
                'adjustment_type': adjustment_type,
                'amount': amount,
                'rationale': rationale,
                'evidence_reference': evidence,
            })
        self._recompute_cost()
        return adjustment

    def action_flag_for_review(self, note=None):
        """Mark an offer as worth a second look. Not after the round closed.

        The flag is a prompt to the committee while it still has a decision to
        make. Adding one to a finalised evaluation changes what the file says
        about an offer after it was judged, which is a different act entirely.
        """
        for analysis in self:
            if analysis.round_id.state == 'finalised':
                raise UserError(_(
                    "%s is finalised. Flagging an offer for review after the "
                    "evaluation closed re-characterises it after the fact.")
                    % analysis.round_id.name)
        self._engine().write({'review_flag': 'review_required',
                              'review_note': note})
        return True

    def _engine(self):
        return self.sudo().with_context(re_evaluation_engine=True)

    def write(self, vals):
        if not self.env.context.get('re_evaluation_engine'):
            for analysis in self:
                if analysis.round_id.state == 'finalised':
                    raise UserError(_(
                        "%s belongs to a finalised evaluation.")
                        % analysis.display_name)
        return super().write(vals)


class CommercialAdjustment(models.Model):
    _name = 'realestate.procurement.commercial.adjustment'
    _description = 'Commercial Evaluation Adjustment'
    _order = 'analysis_id, id'

    analysis_id = fields.Many2one(
        'realestate.procurement.commercial.analysis', required=True,
        ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='analysis_id.company_id', store=True, index=True)
    adjustment_type = fields.Selection(ADJUSTMENT_TYPES, required=True)
    amount = fields.Monetary(
        required=True, currency_field='currency_id',
        help="Positive adds to the evaluated cost, negative reduces it.")
    currency_id = fields.Many2one(
        related='analysis_id.evaluation_currency_id', store=True)
    signed_amount = fields.Monetary(
        compute='_compute_signed', store=True, currency_field='currency_id')
    rationale = fields.Text(required=True)
    evidence_reference = fields.Char()
    entered_by_id = fields.Many2one(
        'res.users', readonly=True, default=lambda self: self.env.user)
    entered_on = fields.Datetime(
        readonly=True, default=fields.Datetime.now)
    approved_by_id = fields.Many2one('res.users', readonly=True)
    approved_on = fields.Datetime(readonly=True)

    @api.depends('amount', 'adjustment_type')
    def _compute_signed(self):
        for adjustment in self:
            amount = adjustment.amount or 0.0
            # A discount is expressed as a positive number by the person
            # entering it and subtracts here, so nobody has to remember to
            # type a minus sign for the one case where it is obvious.
            adjustment.signed_amount = (
                -abs(amount) if adjustment.adjustment_type == 'discount'
                else amount)

    def action_approve(self):
        for adjustment in self:
            if adjustment.analysis_id.round_id.state == 'finalised':
                raise UserError(_(
                    "%s is finalised; its adjustments were approved, or not, "
                    "before the ranking was struck.")
                    % adjustment.analysis_id.round_id.name)
            if adjustment.entered_by_id == self.env.user:
                raise UserError(_(
                    "%s entered this adjustment. Approving your own is not a "
                    "review.") % self.env.user.display_name)
            adjustment.sudo().write({'approved_by_id': self.env.user.id,
                                     'approved_on': fields.Datetime.now()})
        return True

    @api.model_create_multi
    def create(self, vals_list):
        adjustments = super().create(vals_list)
        adjustments.mapped('analysis_id')._recompute_cost()
        return adjustments


class LevelingLine(models.Model):
    _name = 'realestate.procurement.leveling.line'
    _description = 'Bid Leveling Line'
    _order = 'analysis_id, id'

    analysis_id = fields.Many2one(
        'realestate.procurement.commercial.analysis', required=True,
        ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='analysis_id.company_id', store=True, index=True)
    partner_id = fields.Many2one(
        related='analysis_id.partner_id', store=True, index=True)
    round_id = fields.Many2one(
        related='analysis_id.round_id', store=True, index=True)
    # Carried for the same reason every other M6 model carries it: so a
    # project-scoped record rule can be added later without a migration. It
    # was the one model that had been left without one.
    project_id = fields.Many2one(
        related='analysis_id.project_id', store=True, index=True)
    bid_line_id = fields.Many2one(
        'realestate.procurement.bid.response.line', readonly=True)
    sourcing_line_id = fields.Many2one(
        'realestate.procurement.sourcing.line', readonly=True, index=True)

    description = fields.Text(readonly=True)
    quantity = fields.Float(readonly=True)
    raw_currency_id = fields.Many2one('res.currency', readonly=True)
    raw_unit_price = fields.Float(readonly=True, digits='Product Price')
    raw_line_total = fields.Monetary(
        readonly=True, currency_field='raw_currency_id')
    evaluation_currency_id = fields.Many2one('res.currency', readonly=True)
    normalised_unit_price = fields.Float(readonly=True, digits='Product Price')
    normalised_line_total = fields.Monetary(
        readonly=True, currency_field='evaluation_currency_id')
    is_included = fields.Boolean(default=True, readonly=True)
    exclusion_reason = fields.Text(readonly=True)
