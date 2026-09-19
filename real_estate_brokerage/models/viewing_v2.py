# -*- coding: utf-8 -*-
"""M10 — viewings that survive contact with a real diary.

0.1's viewing had four states — scheduled, completed, cancelled, no-show — and a
free-text feedback box. Two things are missing from that, and both cost money.

**The lifecycle starts before the appointment exists.** An agent proposes three
units on Tuesday; the customer confirms one on Thursday. Between those, the
viewing is real work — it is in the pipeline, it needs chasing — but 0.1 has
nowhere to put it, so it either does not exist or it is a fake `scheduled` that
corrupts every no-show statistic. Hence **SUGGESTED**, and a separate
**CONFIRMED** for "the customer has actually said yes to this slot", because the
gap between scheduled and confirmed is exactly where no-shows are born.

**Rescheduling is not editing.** 0.1 would have you change `scheduled_at` in
place, and the fact that the customer moved the appointment four times vanishes.
Here a reschedule creates the new appointment and moves the old one to
**RESCHEDULED**, linked. The chain is the evidence.

```
SUGGESTED ─▶ SCHEDULED ─▶ CONFIRMED ─▶ COMPLETED
                │             │            │
                ├──▶ RESCHEDULED (─▶ new viewing)
                ├──▶ CANCELLED
                └──▶ NO SHOW
```

**Feedback becomes structured.** "They didn't like it" cannot be reported on. An
outcome and a rejection reason can, and they are the same vocabulary the
shortlist uses (M9), so a viewing verdict flows straight back to the match
record without a human retyping it.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

VIEWING_STATE = [
    ('suggested', 'Suggested'),
    ('scheduled', 'Scheduled'),
    ('confirmed', 'Confirmed'),
    ('completed', 'Completed'),
    ('rescheduled', 'Rescheduled'),
    ('cancelled', 'Cancelled'),
    ('no_show', 'No Show'),
]

#: Where a viewing may go from where it is. Terminal states have no exits —
#: a completed viewing is history, and history is not edited.
VIEWING_TRANSITIONS = {
    'suggested': {'scheduled', 'cancelled'},
    'scheduled': {'confirmed', 'completed', 'rescheduled', 'cancelled',
                  'no_show'},
    'confirmed': {'completed', 'rescheduled', 'cancelled', 'no_show'},
    'completed': set(),
    'rescheduled': set(),
    'cancelled': set(),
    'no_show': set(),
}

VIEWING_OUTCOME = [
    ('very_interested', 'Very Interested — wants to proceed'),
    ('interested', 'Interested — needs to think'),
    ('needs_alternatives', 'Wants Alternatives'),
    ('not_interested', 'Not Interested'),
    ('offer_intent', 'Intends to Offer'),
]

#: Deliberately the same vocabulary as the shortlist's, so a viewing verdict
#: flows into the match record without anybody retyping it.
VIEWING_REJECTION = [
    ('price', 'Price'),
    ('layout', 'Layout'),
    ('location', 'Location'),
    ('view', 'View'),
    ('condition', 'Condition'),
    ('payment_plan', 'Payment Plan'),
    ('timing', 'Timing'),
    ('floor', 'Floor'),
    ('other', 'Other'),
]

CANCEL_REASON = [
    ('customer', 'Customer Cancelled'),
    ('agent', 'Agent Cancelled'),
    ('owner', 'Owner/Access Unavailable'),
    ('unit_unavailable', 'Unit No Longer Available'),
    ('weather', 'Weather / Force Majeure'),
    ('other', 'Other'),
]


class ViewingV2(models.Model):
    _inherit = 'realestate.viewing'
    _check_company_auto = True

    company_id = fields.Many2one(
        'res.company', string='Company', required=True, index=True,
        default=lambda self: self.env.company)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    state = fields.Selection(
        VIEWING_STATE, default='scheduled', tracking=True, required=True,
        help="SUGGESTED is a proposal the customer has not answered yet. "
             "CONFIRMED means they have said yes to this specific slot — the "
             "gap between scheduled and confirmed is where no-shows are born, "
             "so the two are kept apart.")

    #: Suggested viewings have no agreed time yet, so the field cannot stay
    #: required. The constraint below enforces it from `scheduled` onwards.
    scheduled_at = fields.Datetime(required=False)

    confirmed_on = fields.Datetime(readonly=True, string='Confirmed On')
    confirmed_by_id = fields.Many2one(
        'res.users', readonly=True, string='Confirmed By')
    completed_on = fields.Datetime(readonly=True, string='Completed On')

    # ------------------------------------------------------------------
    # Reschedule chain — the evidence that the customer moved it four times
    # ------------------------------------------------------------------
    rescheduled_to_id = fields.Many2one(
        'realestate.viewing', string='Rescheduled To', readonly=True,
        copy=False, ondelete='set null')
    rescheduled_from_id = fields.Many2one(
        'realestate.viewing', string='Rescheduled From', readonly=True,
        copy=False, ondelete='set null')
    reschedule_count = fields.Integer(
        string='Times Rescheduled', compute='_compute_reschedule_count',
        help="How many times this appointment has already moved. Walking the "
             "chain backwards, so the number is on the live record where "
             "somebody will actually see it.")

    # ------------------------------------------------------------------
    # Structured outcome (replaces the free-text-only verdict)
    # ------------------------------------------------------------------
    outcome = fields.Selection(
        VIEWING_OUTCOME, string='Outcome', tracking=True,
        help="Required to complete a viewing. \"They didn't like it\" cannot "
             "be reported on; this can.")
    rejection_reason = fields.Selection(
        VIEWING_REJECTION, string='Rejection Reason', tracking=True)
    cancel_reason = fields.Selection(
        CANCEL_REASON, string='Cancellation Reason', tracking=True)
    cancel_note = fields.Char(string='Cancellation Note')

    match_id = fields.Many2one(
        'realestate.property.match', string='From Match', index=True,
        ondelete='set null',
        help="The shortlist entry this viewing came from. Set so the outcome "
             "can be written back without a human retyping it.")

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    def _compute_reschedule_count(self):
        for rec in self:
            count, node, guard = 0, rec.rescheduled_from_id, 0
            while node and guard < 50:
                count += 1
                node = node.rescheduled_from_id
                guard += 1
            rec.reschedule_count = count

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains('state', 'scheduled_at')
    def _check_time_known_once_scheduled(self):
        for rec in self:
            if rec.state != 'suggested' and not rec.scheduled_at:
                raise UserError(_(
                    "Viewing %s has no date. A suggestion may be undated; "
                    "anything past that is an appointment and needs a time."
                ) % rec.display_name)

    def _check_transition(self, target):
        self.ensure_one()
        allowed = VIEWING_TRANSITIONS.get(self.state, set())
        if target not in allowed:
            raise UserError(_(
                "Viewing %(ref)s is %(state)s and cannot become %(target)s.\n\n"
                "%(state)s is a closed state — correcting history by moving it "
                "is how no-show and conversion statistics stop meaning "
                "anything.",
                ref=self.display_name,
                state=dict(VIEWING_STATE).get(self.state),
                target=dict(VIEWING_STATE).get(target)))
        return True

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_schedule(self):
        """Turn a suggestion into a real appointment."""
        for rec in self:
            rec._check_transition('scheduled')
            if not rec.scheduled_at:
                raise UserError(_(
                    "Set a date on %s before scheduling it.") % rec.display_name)
            rec.state = 'scheduled'
        return True

    def action_confirm(self):
        """The customer has said yes to this slot."""
        for rec in self:
            rec._check_transition('confirmed')
            rec.write({
                'state': 'confirmed',
                'confirmed_on': fields.Datetime.now(),
                'confirmed_by_id': self.env.user.id,
            })
        return True

    def action_mark_completed(self):
        """Completion requires a verdict — that is the whole point."""
        for rec in self:
            rec._check_transition('completed')
            if not rec.outcome:
                raise UserError(_(
                    "Record an outcome for %s before completing it. A viewing "
                    "with no verdict is a slot in a diary, not information."
                ) % rec.display_name)
            rec.write({'state': 'completed',
                       'completed_on': fields.Datetime.now()})
            rec._push_outcome_to_match()
        return True

    def action_mark_no_show(self):
        for rec in self:
            rec._check_transition('no_show')
            rec.state = 'no_show'
        return True

    def action_cancel(self):
        for rec in self:
            rec._check_transition('cancelled')
            rec.state = 'cancelled'
        return True

    def action_reschedule(self, new_datetime=None, note=None):
        """Move an appointment by superseding it, never by editing it.

        Returns the **new** viewing. The old one is closed as `rescheduled` and
        linked both ways, because "the customer moved this four times" is a
        fact worth keeping and an in-place edit destroys it.
        """
        self.ensure_one()
        self._check_transition('rescheduled')
        new_datetime = new_datetime or self.env.context.get('reschedule_to')
        if not new_datetime:
            raise UserError(_("A reschedule needs a new date and time."))

        successor = self.copy({
            'scheduled_at': new_datetime,
            'state': 'scheduled',
            'rescheduled_from_id': self.id,
            'rescheduled_to_id': False,
            'confirmed_on': False,
            'confirmed_by_id': False,
            'completed_on': False,
            'outcome': False,
            'rejection_reason': False,
        })
        self.write({'state': 'rescheduled', 'rescheduled_to_id': successor.id})
        body = _("Rescheduled to %(when)s as %(ref)s.",
                 when=new_datetime, ref=successor.display_name)
        if note:
            body += ' ' + note
        self.message_post(body=body)
        return successor

    # ------------------------------------------------------------------
    # Feedback loop into the shortlist (M9 ↔ M10)
    # ------------------------------------------------------------------
    def _push_outcome_to_match(self):
        """Write the viewing verdict onto the match record it came from.

        Only ever fills in the customer's response and reason; it does not
        overwrite a response the customer gave directly, because the customer's
        own words outrank an agent's summary of them.
        """
        self.ensure_one()
        match = self.match_id
        if not match or match.customer_response != 'pending':
            return False
        mapping = {
            'very_interested': 'favorite',
            'offer_intent': 'favorite',
            'interested': 'interested',
            'needs_alternatives': 'maybe',
            'not_interested': 'rejected',
        }
        vals = {'customer_response': mapping.get(self.outcome, 'pending')}
        if self.outcome == 'not_interested' and self.rejection_reason:
            vals['rejection_reason'] = self.rejection_reason
        match.write(vals)
        return True

    # ------------------------------------------------------------------
    # Creation from a shortlist entry (M9)
    # ------------------------------------------------------------------
    @api.model
    def _create_from_match(self, match, scheduled_at=None, agent=None):
        """One shortlisted property → one suggested or scheduled viewing."""
        listing = match.listing_id or self.env['realestate.listing'].search([
            ('property_id', '=', match.property_id.id)], limit=1)
        if not listing:
            raise UserError(_(
                "%s has no listing, so there is nothing to arrange a viewing "
                "against.") % match.property_id.display_name)
        return self.create({
            'listing_id': listing.id,
            'crm_lead_id': match.crm_lead_id.id,
            'partner_id': match.partner_id.id,
            'match_id': match.id,
            'company_id': match.company_id.id or self.env.company.id,
            'agent_id': (agent or match.crm_lead_id.user_id
                         or self.env.user).id,
            'scheduled_at': scheduled_at,
            'state': 'scheduled' if scheduled_at else 'suggested',
        })


class MatchViewingActions(models.Model):
    _inherit = 'realestate.property.match'

    viewing_ids = fields.One2many(
        'realestate.viewing', 'match_id', string='Viewings')
    viewing_count = fields.Integer(compute='_compute_viewing_count')

    def _compute_viewing_count(self):
        counts = dict(self.env['realestate.viewing']._read_group(
            [('match_id', 'in', self.ids)],
            groupby=['match_id'], aggregates=['__count']))
        for rec in self:
            rec.viewing_count = counts.get(rec, 0)

    def action_suggest_viewing(self):
        """Propose viewings for the selected shortlist entries."""
        viewings = self.env['realestate.viewing']
        for rec in self:
            viewings |= self.env['realestate.viewing']._create_from_match(rec)
        return {
            'type': 'ir.actions.act_window',
            'name': _('Suggested Viewings'),
            'res_model': 'realestate.viewing',
            'view_mode': 'list,form',
            'domain': [('id', 'in', viewings.ids)],
        }
