# -*- coding: utf-8 -*-
"""Wave 14 — the commercial consequence of a document.

Submittals and RFIs moved to `atmta_construction_documents`, below every
capability that files a document against a contract. What could not move with
them is the link to a change event: `realestate.construction.change.event` is
declared here, and a Many2one may not point at a model defined above it.

So the link and the action that creates it stay, added back onto the two models
by inheritance. Both were already deliberate actions somebody takes rather than
automatic consequences, and that is unchanged: an RFI that raised change events
by itself would turn every "might this cost more?" into a commercial record
nobody decided to raise.
"""
from odoo import _, fields, models
from odoo.exceptions import UserError


class SubmittalChangeEvent(models.Model):
    _inherit = 'realestate.construction.submittal'

    change_event_id = fields.Many2one(
        'realestate.construction.change.event', readonly=True, copy=False)

    def action_create_change_event(self):
        """A review outcome may have commercial consequences. Somebody decides."""
        self.ensure_one()
        if self.change_event_id:
            raise UserError(_("%s already has a change event.") % self.name)
        event = self.env['realestate.construction.change.event'].create({
            'title': _("Submittal %(number)s — %(title)s",
                       number=self.name, title=self.title),
            'project_id': self.project_id.id,
            'company_id': self.company_id.id,
            'package_id': self.package_id.id or False,
            'contractor_id': self.contractor_id.id or False,
            'wbs_id': self.wbs_id.id or False,
            'source': 'specification_change',
            'source_reference': self.name,
            'source_model': self._name,
            'source_id': self.id,
        })
        self.change_event_id = event
        return event


class RfiChangeEvent(models.Model):
    _inherit = 'realestate.construction.rfi'

    change_event_id = fields.Many2one(
        'realestate.construction.change.event', readonly=True, copy=False,
        help="Created deliberately, never automatically.")

    def action_create_change_event(self):
        """Hand the commercial question to M4, which owns it from here.

        Deliberately an action somebody takes. An RFI that created change
        events by itself would turn every "might this cost more?" into a
        commercial record nobody decided to raise.
        """
        self.ensure_one()
        if self.change_event_id:
            raise UserError(_(
                "%(rfi)s already has change event %(event)s.",
                rfi=self.name, event=self.change_event_id.name))
        event = self.env['realestate.construction.change.event'].create({
            'title': _("RFI %(number)s — %(subject)s",
                       number=self.name, subject=self.subject),
            'project_id': self.project_id.id,
            'company_id': self.company_id.id,
            'currency_id': self.currency_id.id,
            'package_id': self.package_id.id or False,
            'contractor_id': self.contractor_id.id or False,
            'wbs_id': self.wbs_id.id or False,
            'cost_code_id': self.cost_code_id.id or False,
            'source': 'rfi',
            'source_reference': self.name,
            'source_model': self._name,
            'source_id': self.id,
            'estimated_cost_impact': self.estimated_cost_impact,
            'estimated_schedule_days': self.estimated_schedule_days,
            'description': self.official_response or self.question,
        })
        self.change_event_id = event
        if self.cost_impact == 'potential':
            self.cost_impact = 'confirmed'
        return event
