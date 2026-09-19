# -*- coding: utf-8 -*-
"""M1 / M30 — the legacy Brokerage lead, bridged and deprecated.

`realestate.lead` was Brokerage 0.1's private customer pipeline. The Phase 0
audit found it had **no consumers outside this module**, while `crm.lead` was
already the object the website, the public API and the customer portal created
and read. So `crm.lead` becomes canonical (see `crm_lead.py`) and this model
becomes a bridge.

### What happens to it

* **The table is not dropped.** Production rows carry references that appear on
  printed documents, and viewings and offers point at them.
* **New records cannot be created.** `create()` refuses with a message naming
  the CRM pipeline, so nobody re-opens the second spine out of habit.
* **Every row gains `crm_lead_id`**, filled by the migration, so the two are
  reconcilable in both directions.
* **Existing rows stay readable and editable** for one release, so a
  half-finished conversation can be closed out rather than stranded.

Nothing here re-implements a pipeline. `state` survives only as history; it is
no longer advanced by anything.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class Lead(models.Model):
    _name = 'realestate.lead'
    _description = 'Real Estate Buyer Lead (deprecated — use CRM)'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    name = fields.Char(string='Reference', copy=False, required=True,
                       readonly=True, default=lambda self: _('New'))
    partner_id = fields.Many2one('res.partner', string='Contact', tracking=True,
                                 ondelete='restrict')
    partner_name = fields.Char(related='partner_id.name', readonly=False,
                               string='Name')
    phone = fields.Char(related='partner_id.phone', readonly=False)
    email = fields.Char(related='partner_id.email', readonly=False)

    source = fields.Selection([
        ('walk_in', 'Walk-in'),
        ('website', 'Website'),
        ('referral', 'Referral'),
        ('portal', 'Property Portal'),
        ('call', 'Phone Call'),
        ('other', 'Other'),
    ], default='other', tracking=True)

    state = fields.Selection([
        ('new', 'New'),
        ('qualified', 'Qualified'),
        ('matched', 'Matched'),
        ('viewing_scheduled', 'Viewing Scheduled'),
        ('offer', 'Offer Made'),
        ('converted', 'Converted'),
        ('lost', 'Lost'),
    ], default='new', tracking=True, required=True,
        help="Historical only. The pipeline is now `crm.lead.stage_id`; this "
             "value is frozen at whatever it held when the record was "
             "migrated.")

    agent_id = fields.Many2one(
        'res.users', string='Assigned Agent',
        domain=[('is_realestate_agent', '=', True)],
        default=lambda self: self.env.user, tracking=True,
    )

    # Preferences — migrated onto `crm.lead` as the requirements layer.
    budget_min = fields.Monetary(string='Budget Min')
    budget_max = fields.Monetary(string='Budget Max')
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id,
    )
    area_min = fields.Float(string='Area Min (sqm)')
    area_max = fields.Float(string='Area Max (sqm)')
    bedroom_count_min = fields.Integer(string='Bedrooms Min')
    property_type_ids = fields.Many2many('property.type',
                                         string='Preferred Property Types')
    preferred_country_id = fields.Many2one('res.country',
                                           string='Preferred Country')
    preferred_state_id = fields.Many2one(
        'res.country.state', string='Preferred State',
        domain="[('country_id', '=', preferred_country_id)]",
    )
    preferred_city = fields.Char(string='Preferred City')
    preferred_district = fields.Char(string='Preferred District')

    matched_listing_ids = fields.Many2many(
        'realestate.listing',
        'realestate_lead_listing_rel', 'lead_id', 'listing_id',
        string='Matched Listings',
    )
    viewing_ids = fields.One2many('realestate.viewing', 'lead_id',
                                  string='Viewings')
    offer_ids = fields.One2many('realestate.offer', 'lead_id', string='Offers')

    loss_reason_id = fields.Many2one('realestate.lost.reason',
                                     string='Loss Reason')
    notes = fields.Html()
    color = fields.Integer()
    priority = fields.Selection([
        ('0', 'Normal'),
        ('1', 'Low'),
        ('2', 'Medium'),
        ('3', 'High'),
        ('4', 'Very High'),
        ('5', 'Critical'),
    ], default='0')

    # ------------------------------------------------------------------
    # The bridge (M30)
    # ------------------------------------------------------------------
    crm_lead_id = fields.Many2one(
        'crm.lead', string='CRM Opportunity', readonly=True, copy=False,
        index=True, ondelete='set null',
        help="The canonical opportunity this legacy lead now lives as. Filled "
             "by the 0.2 migration; the link is what makes that migration "
             "idempotent.")
    migration_state = fields.Selection([
        ('pending', 'Not Migrated'),
        ('migrated', 'Migrated'),
        ('linked', 'Linked to Existing'),
        ('ambiguous', 'Ambiguous — Needs Review'),
        ('multiple', 'Legitimate Multiple Opportunity'),
        ('skipped', 'Skipped — Insufficient Data'),
    ], default='pending', readonly=True, copy=False, index=True,
        help="How the 0.2 migration classified this record. Nothing is ever "
             "merged on a guess; ambiguous and skipped rows are reported for a "
             "human to resolve.")
    migration_note = fields.Char(readonly=True, copy=False)

    # ------------------------------------------------------------------
    # Creation is closed
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        """Refuse, unless the migration is the caller.

        M1 is explicit that two pipelines must not coexist for the same
        customer. Leaving `create` open would let the second spine grow back one
        habit at a time, so it is closed at the model rather than by hiding a
        menu — menu visibility is not a constraint.
        """
        if not self.env.context.get('re_legacy_migration'):
            raise UserError(_(
                "Brokerage leads are now CRM opportunities.\n\n"
                "Create the opportunity in the CRM pipeline instead — it is the "
                "same customer spine the website, the public API and the "
                "customer portal already use, so the record you make there is "
                "visible everywhere.\n\n"
                "This legacy model is kept read-mostly so existing references "
                "and history stay readable."))
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.lead') or _('New')
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Actions — kept so existing buttons do not 500, but they route to CRM
    # ------------------------------------------------------------------
    def action_open_crm_lead(self):
        self.ensure_one()
        if not self.crm_lead_id:
            raise UserError(_(
                "Legacy lead %(name)s has no CRM opportunity yet. The migration "
                "classified it as '%(state)s'.\n\n%(note)s",
                name=self.name,
                state=dict(self._fields['migration_state'].selection)[
                    self.migration_state],
                note=self.migration_note or ''))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Opportunity'),
            'res_model': 'crm.lead',
            'res_id': self.crm_lead_id.id,
            'view_mode': 'form',
        }

    def action_qualify(self):
        raise UserError(self._deprecated_message(_('qualify')))

    def action_match(self):
        raise UserError(self._deprecated_message(_('run matching for')))

    def action_mark_lost(self):
        raise UserError(self._deprecated_message(_('mark lost')))

    def action_reopen(self):
        raise UserError(self._deprecated_message(_('reopen')))

    def action_schedule_viewing(self):
        raise UserError(
            self._deprecated_message(_('schedule a viewing from')))

    def _deprecated_message(self, what):
        self.ensure_one()
        if self.crm_lead_id:
            return _(
                "This lead now lives as CRM opportunity '%(opp)s'. Use the "
                "opportunity to %(what)s it — working the same customer in two "
                "places is exactly what 0.2 removed.",
                opp=self.crm_lead_id.display_name, what=what)
        return _(
            "Brokerage leads are now CRM opportunities, and this one has not "
            "been migrated (%(state)s). Open it in CRM, or resolve the "
            "migration classification first.", state=self.migration_state)
