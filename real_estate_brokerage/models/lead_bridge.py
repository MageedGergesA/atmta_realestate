# -*- coding: utf-8 -*-
"""M1 — re-pointing the workflow at the CRM spine.

`realestate.viewing` and `realestate.offer` both carried `lead_id`, pointing at
the legacy Brokerage lead. They now carry `crm_lead_id` as well, and it is the
authoritative link.

`lead_id` is **kept, not removed**. Production rows reference it, and Rule 5 of
every ATMTA upgrade so far is that a downstream field does not vanish. It is
mirrored: setting either one keeps the other consistent wherever the migration
established a bridge, so old code and new code see the same customer.
"""

from odoo import api, fields, models


class ViewingCrmBridge(models.Model):
    _inherit = 'realestate.viewing'

    crm_lead_id = fields.Many2one(
        'crm.lead', string='Opportunity', tracking=True, index=True,
        ondelete='set null',
        help="The canonical customer opportunity. This is the authoritative "
             "link; the legacy `Lead` field is a mirror kept for "
             "compatibility.")

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._sync_legacy_lead_bridge()
        return records

    def write(self, vals):
        res = super().write(vals)
        if {'crm_lead_id', 'lead_id'} & set(vals):
            self._sync_legacy_lead_bridge()
        return res

    def _sync_legacy_lead_bridge(self):
        """Keep the two links pointing at the same customer.

        Only ever fills a blank. Neither field is overwritten when it already
        holds a value: a record that legitimately points at a legacy lead with
        no CRM counterpart (an `ambiguous` or `skipped` migration outcome) must
        keep saying so rather than being quietly re-pointed.
        """
        for rec in self:
            if rec.crm_lead_id and not rec.lead_id:
                legacy = self.env['realestate.lead'].search(
                    [('crm_lead_id', '=', rec.crm_lead_id.id)], limit=1)
                if legacy:
                    rec.with_context(
                        re_legacy_migration=True).lead_id = legacy.id
            elif rec.lead_id and not rec.crm_lead_id and rec.lead_id.crm_lead_id:
                rec.crm_lead_id = rec.lead_id.crm_lead_id.id


class OfferCrmBridge(models.Model):
    _inherit = 'realestate.offer'

    crm_lead_id = fields.Many2one(
        'crm.lead', string='Opportunity', tracking=True, index=True,
        ondelete='set null',
        help="The canonical customer opportunity. This is the authoritative "
             "link; the legacy `Lead` field is a mirror kept for "
             "compatibility.")

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._sync_legacy_lead_bridge()
        return records

    def write(self, vals):
        res = super().write(vals)
        if {'crm_lead_id', 'lead_id'} & set(vals):
            self._sync_legacy_lead_bridge()
        return res

    def _sync_legacy_lead_bridge(self):
        for rec in self:
            if rec.crm_lead_id and not rec.lead_id:
                legacy = self.env['realestate.lead'].search(
                    [('crm_lead_id', '=', rec.crm_lead_id.id)], limit=1)
                if legacy:
                    rec.with_context(
                        re_legacy_migration=True).lead_id = legacy.id
            elif rec.lead_id and not rec.crm_lead_id and rec.lead_id.crm_lead_id:
                rec.crm_lead_id = rec.lead_id.crm_lead_id.id
