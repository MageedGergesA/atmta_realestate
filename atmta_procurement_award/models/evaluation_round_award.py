"""Starting an award from the evaluation it is founded on.

An award is refused unless its round is finalised and belongs to its tender,
and both fields are fixed once the award exists. Rather than make them
choosable on a blank award form — where the obvious mistake is picking a round
of another tender — the award is started from the finalised round itself,
which supplies both. Evaluation cannot know about awards, so the button and
its method live here.
"""
from odoo import _, models
from odoo.exceptions import UserError

from .procurement_award import DRAFTING_GROUPS


class EvaluationRoundAward(models.Model):
    _inherit = 'realestate.procurement.evaluation.round'

    def action_create_award(self):
        """Open a new draft award founded on this round."""
        self.ensure_one()
        Award = self.env['realestate.procurement.award']
        # The same authority, and the same refusals, as creating the award
        # directly — asked here so the button fails before a form opens that
        # could never be saved.
        Award._assert_authority(_("raise an award"), groups=DRAFTING_GROUPS)
        if self.state != 'finalised':
            raise UserError(_(
                "%(round)s is %(state)s. An award is founded on a completed "
                "evaluation.", round=self.name, state=self.state))
        live = Award.sudo().search([
            ('round_id', '=', self.id),
            ('state', '!=', 'cancelled'),
            ('superseded', '=', False),
        ], limit=1)
        if live:
            raise UserError(_(
                "%(round)s already has a live award (%(award)s, %(state)s). "
                "Revise or cancel that one instead of starting another.",
                round=self.name, award=live.name, state=live.state))
        return {
            'type': 'ir.actions.act_window',
            'name': _('New Award'),
            'res_model': 'realestate.procurement.award',
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'current',
            'context': {
                'default_sourcing_event_id': self.sourcing_event_id.id,
                'default_round_id': self.id,
            },
        }
