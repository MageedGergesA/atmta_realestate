# -*- coding: utf-8 -*-
"""M5 — clarifications, and the line between answering and changing.

A clarification explains what the tender already said. The moment it changes
what the tender says — quantity, scope, specification, commercial terms, the
document basis or the deadline — it is not a clarification any more, and
answering it in free text would quietly move the basis under vendors who
already priced the old one.

So a clarification marked material cannot be published on its own: it has to
carry an addendum, which is a new tender version with a reason and an issuer.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

#: The six things a clarification cannot change by itself.
MATERIAL_SUBJECTS = [
    ('quantity', 'Quantity'),
    ('scope', 'Scope'),
    ('specification', 'Specification'),
    ('commercial_terms', 'Commercial Terms'),
    ('documents', 'Document Basis'),
    ('deadline', 'Deadline'),
]


class SourcingClarification(models.Model):
    _name = 'realestate.procurement.sourcing.clarification'
    _description = 'Tender Clarification'
    _inherit = ['mail.thread']
    _order = 'event_id, id'

    name = fields.Char(readonly=True, copy=False, default=lambda s: _('New'))
    event_id = fields.Many2one(
        'realestate.procurement.sourcing.event', required=True,
        ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='event_id.company_id', store=True, index=True)
    clarification_type = fields.Selection([
        ('vendor_question', 'Vendor Question'),
        ('buyer_clarification', 'Buyer Clarification'),
        ('general', 'General Clarification'),
    ], required=True, default='vendor_question', tracking=True)
    visibility = fields.Selection([
        ('vendor_only', 'Asking Vendor Only'),
        ('all_invited', 'All Invited Vendors'),
        ('internal', 'Internal Only'),
    ], required=True, default='all_invited', tracking=True,
        help="Who the answer is for. Internal clarifications are never part "
             "of what vendors were told.")
    partner_id = fields.Many2one(
        'res.partner', string='Vendor',
        help="Required for a vendor question, and for a vendor-only answer.")
    invitation_id = fields.Many2one(
        'realestate.procurement.sourcing.invitation')
    version_id = fields.Many2one(
        'realestate.procurement.sourcing.version', readonly=True,
        string='Asked Against',
        help="The tender version in force when the question was asked.")

    question = fields.Text(required=True)
    answer = fields.Text()
    asked_on = fields.Datetime(default=fields.Datetime.now, readonly=True)
    answered_on = fields.Datetime(readonly=True, copy=False)
    answered_by_id = fields.Many2one('res.users', readonly=True, copy=False)

    is_material = fields.Boolean(
        string='Materially Changes the Tender', tracking=True,
        help="Set when the answer would change what vendors are quoting. A "
             "material clarification is published as an addendum, never as "
             "text alone.")
    material_subject = fields.Selection(MATERIAL_SUBJECTS)
    addendum_version_id = fields.Many2one(
        'realestate.procurement.sourcing.version', readonly=True, copy=False,
        string='Issued As')
    state = fields.Selection([
        ('open', 'Open'),
        ('answered', 'Answered'),
        ('cancelled', 'Cancelled'),
    ], default='open', required=True, tracking=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.procurement.sourcing.clarification') or _('New')
            event = self.env[
                'realestate.procurement.sourcing.event'].browse(
                    vals.get('event_id'))
            vals.setdefault('version_id', event.current_version_id.id)
        return super().create(vals_list)

    @api.constrains('clarification_type', 'partner_id', 'visibility')
    def _check_vendor(self):
        for record in self:
            if record.clarification_type == 'vendor_question' \
                    and not record.partner_id:
                raise UserError(_(
                    "A vendor question needs the vendor who asked it."))
            if record.visibility == 'vendor_only' and not record.partner_id:
                raise UserError(_(
                    "A vendor-only answer needs to name the vendor it is "
                    "for."))

    def action_answer(self, answer=None):
        """Publish the answer — refusing when it is really an amendment."""
        for record in self:
            body = answer or record.answer
            if not body:
                raise UserError(_("There is no answer to publish."))
            if record.is_material and not record.addendum_version_id:
                raise UserError(_(
                    "%(name)s changes %(subject)s. Vendors priced the current "
                    "version, so this goes out as an addendum — issue one from "
                    "the tender and the clarification will be attached to it. "
                    "Answering in text would move the basis without telling "
                    "anybody it moved.",
                    name=record.name,
                    subject=dict(MATERIAL_SUBJECTS).get(
                        record.material_subject, _('the tender basis'))))
            record.write({
                'answer': body,
                'answered_on': fields.Datetime.now(),
                'answered_by_id': self.env.user.id,
                'state': 'answered',
            })
        return True

    def action_issue_addendum(self, reason=None, close_datetime=None):
        """Turn a material clarification into a tender version."""
        self.ensure_one()
        version = self.event_id.action_issue_addendum(
            reason=reason or _("Clarification %s: %s") % (self.name,
                                                          self.question),
            close_datetime=close_datetime, clarification=self)
        return version

    def _visible_to_requester(self):
        """Nothing commercial here is requester-readable by default."""
        self.ensure_one()
        return self.visibility == 'all_invited' and self.state == 'answered'
