from odoo import api, fields, models


READINESS_KEYS = ['structural', 'finishing', 'utilities', 'inspection']


class PropertyHandover(models.Model):
    _inherit = 'realestate.property'

    handover_ids = fields.One2many('realestate.handover', compute='_compute_handover_ids', string='Handovers')
    handover_count = fields.Integer(compute='_compute_handover_ids')
    warranty_ids = fields.One2many('realestate.warranty', compute='_compute_warranty_ids', string='Warranties')

    readiness_structural = fields.Float(string='Structural %', default=0.0)
    readiness_finishing = fields.Float(string='Finishing %', default=0.0)
    readiness_utilities = fields.Float(string='Utilities %', default=0.0)
    readiness_inspection = fields.Float(string='Inspection %', default=0.0)
    readiness_overall = fields.Float(
        string='Overall Readiness %',
        compute='_compute_readiness_overall', store=True,
    )
    handover_ready = fields.Boolean(
        string='Ready for Handover',
        compute='_compute_readiness_overall', store=True,
        help='True when all readiness dimensions are at 100%.',
    )

    ready_to_deliver = fields.Boolean(
        string='Ready to Deliver',
        compute='_compute_handover_milestones', store=True,
        help='Construction is physically complete and the unit can begin the handover process.',
    )
    ready_to_move = fields.Boolean(
        string='Ready to Move',
        compute='_compute_handover_milestones', store=True,
        help='Handover has been completed: occupant can move in immediately.',
    )

    def _compute_handover_ids(self):
        Handover = self.env['realestate.handover']
        for rec in self:
            handovers = Handover.search([('property_id', '=', rec.id)])
            rec.handover_ids = handovers
            rec.handover_count = len(handovers)

    def _compute_warranty_ids(self):
        Warranty = self.env['realestate.warranty']
        for rec in self:
            rec.warranty_ids = Warranty.search([('property_id', '=', rec.id)])

    @api.depends('readiness_structural', 'readiness_finishing', 'readiness_utilities', 'readiness_inspection')
    def _compute_readiness_overall(self):
        for rec in self:
            vals = [
                rec.readiness_structural or 0.0,
                rec.readiness_finishing or 0.0,
                rec.readiness_utilities or 0.0,
                rec.readiness_inspection or 0.0,
            ]
            rec.readiness_overall = sum(vals) / len(vals)
            rec.handover_ready = all(v >= 100.0 for v in vals)

    @api.depends('handover_ready', 'readiness_overall')
    def _compute_handover_milestones(self):
        """Driven by both readiness and whether a handover event has completed."""
        Handover = self.env['realestate.handover']
        for rec in self:
            completed = Handover.search_count([
                ('property_id', '=', rec.id),
                ('state', '=', 'completed'),
            ])
            rec.ready_to_deliver = rec.handover_ready and not completed
            rec.ready_to_move = bool(completed) and rec.readiness_overall >= 100.0

    def action_view_handovers(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Handovers',
            'res_model': 'realestate.handover',
            'view_mode': 'list,form',
            'domain': [('property_id', '=', self.id)],
        }
