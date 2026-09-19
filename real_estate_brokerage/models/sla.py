# -*- coding: utf-8 -*-
"""M18 / M19 — attribution that survives to revenue, and a response clock.

### Attribution

0.1 could tell you a lead came from Facebook. It could not tell you whether
Facebook made any money, because nothing carried the source through the offer
and the transaction, and nothing recorded what the channel cost. A channel with
400 leads and no completions is worse than one with 40 and six, and the 0.1
model reported the first as the better performer.

So the *source* — Odoo's `utm.source`, which is what first-touch attribution
was frozen onto in M1 — gains a spend figure and the numbers that make it mean
something:

```
  Facebook   spend 120,000   leads 412   deals  3   cost/deal  40,000
  Referral   spend       0   leads  38   deals  9   cost/deal       0
```

The first-touch source is already frozen on the opportunity (M1). What is added
here is the arithmetic that turns it into a decision.

### The response clock

The single strongest predictor of conversion in this business is how fast
somebody called back. It is measured here from the moment the opportunity was
created to the first outbound contact logged against it — not to the first time
anybody opened the record, which measures curiosity rather than service.

A deadline is set from the team's target. Missing it is recorded rather than
computed on the fly, so that "we were late on 40% of leads last March" is still
answerable next March.
"""

from odoo import _, api, fields, models

SLA_STATE = [
    ('pending', 'Awaiting First Response'),
    ('on_time', 'Responded On Time'),
    ('late', 'Responded Late'),
    ('breached', 'Overdue — No Response'),
]


class CrmTeamSla(models.Model):
    _inherit = 'crm.team'

    re_first_response_hours = fields.Float(
        string='First Response Target (hours)', default=4.0,
        help="How quickly this team undertakes to call a new enquiry back. "
             "Zero switches the clock off for the team.")


class UtmSourcePerformance(models.Model):
    """Spend and performance, on the model the attribution actually uses.

    Note this is `utm.source` and **not** `realestate.marketing.channel`. The
    two look interchangeable and are not: M1 froze first-touch attribution onto
    Odoo's `utm.source`, while 0.1's marketing channel is a *publication*
    channel — it carries `base_url`, "public site where listings are
    published, e.g. https://bayut.com". Portals a listing is advertised on and
    sources an enquiry arrived through overlap but are not the same set, and
    hanging cost-per-deal off the wrong one would produce numbers that look
    right and mean nothing.
    """
    _inherit = 'utm.source'

    re_company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company, index=True,
        help="UTM sources are global in Odoo; spend is not.")
    re_currency_id = fields.Many2one(
        related='re_company_id.currency_id', readonly=True)

    re_spend = fields.Monetary(
        string='Spend', currency_field='re_currency_id',
        help="What has been spent on this source to date. Without it, cost "
             "per deal cannot be computed and the source with the most leads "
             "always looks like the best one.")
    re_lead_count = fields.Integer(compute='_compute_re_performance',
                                   string='Leads')
    re_won_count = fields.Integer(compute='_compute_re_performance',
                                  string='Deals Won')
    re_revenue = fields.Monetary(compute='_compute_re_performance',
                                 currency_field='re_currency_id',
                                 string='Commission Won')
    re_cost_per_lead = fields.Monetary(compute='_compute_re_performance',
                                       currency_field='re_currency_id')
    re_cost_per_deal = fields.Monetary(compute='_compute_re_performance',
                                       currency_field='re_currency_id')

    def _compute_re_performance(self):
        """Grouped queries — never one per source (M29)."""
        Lead = self.env['crm.lead'].sudo()
        leads = dict(Lead._read_group(
            [('re_first_source_id', 'in', self.ids)],
            groupby=['re_first_source_id'], aggregates=['__count']))
        won = dict(Lead._read_group(
            [('re_first_source_id', 'in', self.ids),
             ('stage_id.is_won', '=', True)],
            groupby=['re_first_source_id'], aggregates=['__count']))

        Txn = self.env['realestate.transaction'].sudo()
        commission = dict(Txn._read_group(
            [('re_source_id', 'in', self.ids), ('state', '=', 'closed')],
            groupby=['re_source_id'],
            aggregates=['commission_gross_amount:sum']))

        for rec in self:
            rec.re_lead_count = leads.get(rec, 0)
            rec.re_won_count = won.get(rec, 0)
            rec.re_revenue = commission.get(rec, 0.0)
            rec.re_cost_per_lead = (rec.re_spend / rec.re_lead_count
                                    if rec.re_lead_count else 0.0)
            rec.re_cost_per_deal = (rec.re_spend / rec.re_won_count
                                    if rec.re_won_count else 0.0)


class TransactionAttribution(models.Model):
    """The source, stored on the deal.

    A stored related, not a dotted `_read_group` path: Odoo 18 refuses to group
    by `crm_lead_id.re_first_source_id` ("Property access on non-property
    field"), and attribution that cannot be grouped is attribution nobody will
    ever look at.
    """
    _inherit = 'realestate.transaction'

    re_source_id = fields.Many2one(
        related='crm_lead_id.re_first_source_id', store=True, index=True,
        readonly=True, string='Marketing Source')


class CrmLeadSla(models.Model):
    _inherit = 'crm.lead'

    re_response_deadline = fields.Datetime(
        string='Response Due', readonly=True, index=True, copy=False,
        help="Set once, from the team's target, at creation. Deliberately not "
             "recomputed when the target changes: a deadline that moves is not "
             "a deadline.")
    re_first_response_on = fields.Datetime(
        string='First Responded', readonly=True, index=True, copy=False)
    re_first_response_by_id = fields.Many2one(
        'res.users', string='First Responder', readonly=True, copy=False)
    re_response_hours = fields.Float(
        string='Response Time (h)', compute='_compute_sla', store=True)
    re_sla_state = fields.Selection(
        SLA_STATE, string='Response SLA', compute='_compute_sla', store=True,
        index=True)

    @api.depends('create_date', 're_first_response_on', 're_response_deadline')
    def _compute_sla(self):
        now = fields.Datetime.now()
        for rec in self:
            if not rec.re_response_deadline:
                rec.re_sla_state = False
                rec.re_response_hours = 0.0
                continue
            if rec.re_first_response_on:
                delta = rec.re_first_response_on - rec.create_date
                rec.re_response_hours = delta.total_seconds() / 3600.0
                rec.re_sla_state = (
                    'on_time'
                    if rec.re_first_response_on <= rec.re_response_deadline
                    else 'late')
            else:
                rec.re_response_hours = 0.0
                rec.re_sla_state = (
                    'breached' if now > rec.re_response_deadline else 'pending')

    @api.model_create_multi
    def create(self, vals_list):
        leads = super().create(vals_list)
        for lead in leads:
            lead._re_start_response_clock()
        return leads

    def _re_start_response_clock(self):
        self.ensure_one()
        if self.type != 'opportunity' or self.re_response_deadline:
            return False
        hours = self.team_id.re_first_response_hours or 0.0
        if hours <= 0:
            return False
        self.re_response_deadline = fields.Datetime.add(
            self.create_date or fields.Datetime.now(), hours=hours)
        return True

    def _re_log_first_response(self, user=None):
        """Stop the clock. Only the first one counts."""
        self.ensure_one()
        if self.re_first_response_on:
            return False
        self.write({
            're_first_response_on': fields.Datetime.now(),
            're_first_response_by_id': (user or self.env.user).id,
        })
        return True

    def message_post(self, **kwargs):
        """An outbound message to the customer is the response.

        Internal notes are not: a note to a colleague is not somebody calling
        the customer back, and counting it would make the metric flattering and
        useless.
        """
        message = super().message_post(**kwargs)
        # Both halves matter. `message_type` separates a human message from
        # Odoo's own tracking notifications, which fire on every stage change;
        # the subtype separates a reply to the customer from an internal note.
        # Either check alone counts something that is not a callback.
        outbound = message.message_type in ('email', 'comment') \
            and message.subtype_id \
            and not message.subtype_id.internal
        if outbound and self.type == 'opportunity':
            self._re_log_first_response(message.author_id.user_ids[:1])
        return message

    def action_log_first_response(self):
        """For a response made by phone, which leaves no message behind."""
        for rec in self:
            rec._re_log_first_response()
        return True

    @api.model
    def _cron_flag_sla_breaches(self):
        """Move overdue leads to `breached` so a report can find them.

        The state is computed, but the recompute has to be *triggered* by
        something — otherwise a lead that quietly went overdue at 2am keeps
        reading `pending` until somebody happens to touch it.
        """
        overdue = self.search([
            ('re_first_response_on', '=', False),
            ('re_response_deadline', '!=', False),
            ('re_response_deadline', '<', fields.Datetime.now()),
            ('re_sla_state', '=', 'pending'),
        ])
        overdue.modified(['re_response_deadline'])
        return len(overdue)
