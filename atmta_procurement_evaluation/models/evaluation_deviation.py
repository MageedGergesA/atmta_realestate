# -*- coding: utf-8 -*-
"""M6 — the deviation register.

A deviation is where an offer departs from what the tender asked for. It is
recorded against the bid and the criterion it departs from, so that the
evaluation says *what* was different and *what was decided about it* — rather
than leaving it in an evaluator's free-text note where nobody reviewing the
file later can find it.

Recording a deviation never edits the offer. The submitted bid is M5 evidence
and stays exactly as it arrived; the register sits beside it.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class EvaluationDeviation(models.Model):
    _name = 'realestate.procurement.evaluation.deviation'
    _description = 'Evaluation Deviation'
    _order = 'round_id, id'

    name = fields.Char(readonly=True, copy=False, default=lambda s: _('New'))
    round_id = fields.Many2one(
        'realestate.procurement.evaluation.round', required=True,
        ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='round_id.company_id', store=True, index=True)
    candidate_id = fields.Many2one(
        'realestate.procurement.evaluation.candidate', required=True,
        ondelete='cascade', index=True)
    partner_id = fields.Many2one(
        related='candidate_id.partner_id', store=True, index=True)
    criterion_id = fields.Many2one(
        'realestate.procurement.evaluation.criterion')
    sourcing_line_id = fields.Many2one(
        'realestate.procurement.sourcing.line')

    deviation_type = fields.Selection([
        ('technical', 'Technical'),
        ('commercial', 'Commercial'),
        ('qualification', 'Qualification Clarification'),
        ('scope', 'Scope'),
        ('delivery', 'Delivery'),
        ('warranty', 'Warranty'),
        ('payment_terms', 'Payment Terms'),
        ('other', 'Other'),
    ], required=True, default='technical')
    is_material = fields.Boolean(
        string='Material Deviation',
        help="The offer departs from the tender basis in a way that changes "
             "what is being compared. Material deviations are surfaced on the "
             "evaluation report rather than left inside a score.")
    description = fields.Text(required=True)
    tender_requirement = fields.Text(
        help="What the tender asked for, quoted so the comparison is legible "
             "without opening three other documents.")
    state = fields.Selection([
        ('open', 'Open'),
        ('accepted', 'Accepted for Evaluation'),
        ('rejected', 'Rejected'),
        ('clarified', 'Clarified'),
        ('resolved', 'Resolved'),
    ], default='open', required=True)
    resolution = fields.Text()
    raised_by_id = fields.Many2one(
        'res.users', readonly=True, default=lambda self: self.env.user)
    raised_on = fields.Datetime(readonly=True, default=fields.Datetime.now)
    resolved_by_id = fields.Many2one('res.users', readonly=True)
    resolved_on = fields.Datetime(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.procurement.evaluation.deviation') or _('New')
        return super().create(vals_list)

    def action_resolve(self, state='resolved', resolution=None):
        for deviation in self:
            if not resolution:
                raise UserError(_(
                    "Closing a deviation needs the decision that closed it."))
            if deviation.round_id.state == 'finalised':
                raise UserError(_(
                    "%s is finalised.") % deviation.round_id.name)
            deviation.write({
                'state': state,
                'resolution': resolution,
                'resolved_by_id': self.env.user.id,
                'resolved_on': fields.Datetime.now(),
            })
        return True
