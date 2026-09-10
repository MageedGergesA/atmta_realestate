# -*- coding: utf-8 -*-
"""Rule 1 — `res.partner` stays the vendor master, and gains almost nothing.

Two additions only, and both are pointers rather than governance:

```
    procurement_profile_id      where the governance record lives
    procurement_vendor_class    what the migration made of this supplier
```

Everything a governance decision actually consists of — assessments,
evidence, conditions, restrictions, validity — lives on its own models. The
partner is not the place to answer "may we buy from them", because the answer
is different per company, per trade, per project and per date, and a field on
a contact can hold exactly one of those.
"""

from odoo import _, api, fields, models

#: M4AB — how an existing supplier population is described on upgrade day.
#: None of these is a qualification. Prior commercial activity is evidence
#: that somebody once decided to buy; it is not a governance decision, and
#: turning it into one on upgrade day is the single dishonest thing this
#: migration could do.
VENDOR_CLASS = [
    ('assessed', 'Assessed'),
    ('needs_qualification', 'Needs Qualification'),
    ('has_active_purchase_history', 'Has Purchase History'),
    ('has_current_vendor_pricelist', 'Has Vendor Price List'),
    ('legacy_active_vendor', 'Legacy Active Vendor'),
    ('existing_vendor_unassessed', 'Existing Vendor — Unassessed'),
]


class ResPartner(models.Model):
    _inherit = 'res.partner'

    is_realestate_vendor = fields.Boolean(
        compute='_compute_is_realestate_vendor', store=True,
        help='True when the partner carries any real-estate vendor tag.',
    )

    procurement_profile_id = fields.Many2one(
        'realestate.procurement.vendor.profile',
        string='Governance Profile', compute='_compute_procurement_profile',
        help="This company's governance record for the vendor. Computed and "
             "not stored: the profile is per company, and a stored field on "
             "a shared partner would show one company's answer to another.")
    procurement_qualification_ids = fields.One2many(
        'realestate.procurement.vendor.qualification', 'partner_id',
        string='Qualifications')
    procurement_qualification_count = fields.Integer(
        compute='_compute_procurement_counts')
    procurement_restriction_count = fields.Integer(
        compute='_compute_procurement_counts')

    procurement_vendor_class = fields.Selection(
        VENDOR_CLASS, string='Procurement Vendor Class', index=True,
        copy=False, readonly=True,
        help="Set by the M4 migration and by Reclassify Vendors. A "
             "description of what this database already knows about the "
             "supplier — never an approval.")

    _RE_VENDOR_TAG_XMLIDS = (
        'atmta_procurement_vendor.tag_vendor_contractor',
        'atmta_procurement_vendor.tag_vendor_material_supplier',
        'atmta_procurement_vendor.tag_vendor_service',
        'atmta_procurement_vendor.tag_vendor_marketing',
        'atmta_procurement_vendor.tag_vendor_consultant',
        'atmta_procurement_vendor.tag_vendor_utility',
        'atmta_procurement_vendor.tag_vendor_landowner',
    )

    @api.depends('category_id')
    def _compute_is_realestate_vendor(self):
        tag_ids = {
            tag.id for tag in (
                self.env.ref(xmlid, raise_if_not_found=False)
                for xmlid in self._RE_VENDOR_TAG_XMLIDS
            ) if tag
        }
        for rec in self:
            rec.is_realestate_vendor = bool(tag_ids & set(rec.category_id.ids))

    def _compute_procurement_profile(self):
        Profile = self.env['realestate.procurement.vendor.profile']
        company = self.env.company
        profiles = Profile.sudo().search([
            ('partner_id', 'in', self.ids),
            ('company_id', '=', company.id),
        ])
        by_partner = {p.partner_id.id: p.id for p in profiles}
        for rec in self:
            rec.procurement_profile_id = by_partner.get(rec.id, False)

    def _compute_procurement_counts(self):
        Qualification = self.env[
            'realestate.procurement.vendor.qualification']
        Restriction = self.env['realestate.procurement.vendor.restriction']
        today = fields.Date.context_today(self)
        quals = dict(Qualification.sudo()._read_group(
            [('partner_id', 'in', self.ids)],
            groupby=['partner_id'], aggregates=['__count'])) if self.ids else {}
        restrictions = dict(Restriction.sudo()._read_group(
            [('partner_id', 'in', self.ids), ('state', '=', 'active'),
             '|', ('effective_to', '=', False),
             ('effective_to', '>=', today)],
            groupby=['partner_id'], aggregates=['__count'])) if self.ids else {}
        for rec in self:
            rec.procurement_qualification_count = quals.get(rec, 0)
            rec.procurement_restriction_count = restrictions.get(rec, 0)

    # ------------------------------------------------------------------
    def action_open_vendor_governance(self):
        """Open — and if need be create — this vendor's governance profile.

        Profiles are made here, one at a time, when somebody actually starts
        governing a vendor. See `_get_or_create` for why the migration does
        not make them in bulk.
        """
        self.ensure_one()
        profile = self.env[
            'realestate.procurement.vendor.profile']._get_or_create(
                self, self.env.company)
        return {
            'type': 'ir.actions.act_window',
            'name': _('Vendor Governance'),
            'res_model': 'realestate.procurement.vendor.profile',
            'res_id': profile.id,
            'view_mode': 'form',
        }

    # ------------------------------------------------------------------
    @api.model
    def _classify_procurement_vendors(self, partners=None, batch_size=1000):
        """M4AB — describe the supplier population without judging it.

        Deliberately a written field rather than a stored compute. The inputs
        are confirmed purchase orders and supplier price lists across the
        whole database; a compute would either re-read all of that on every
        partner write or go stale, and neither is what a classification for a
        rollout worklist needs. It is rerun by hand or nightly, and rerunning
        it is idempotent because every branch is decided from current data.
        """
        Partner = partners or self.search([('supplier_rank', '>', 0)])
        if not Partner:
            return {}

        Qualification = self.env[
            'realestate.procurement.vendor.qualification'].sudo()
        today = fields.Date.context_today(self)
        qualified = {p.id for p, _c in Qualification._read_group(
            [('partner_id', 'in', Partner.ids), ('is_current', '=', True),
             ('state', '=', 'approved'),
             '|', ('expiry_date', '=', False), ('expiry_date', '>=', today)],
            groupby=['partner_id'], aggregates=['__count'])}

        with_orders = {p.id for p, _c in self.env['purchase.order'].sudo(
        )._read_group([('partner_id', 'in', Partner.ids),
                       ('state', 'in', ('purchase', 'done'))],
                      groupby=['partner_id'], aggregates=['__count'])}
        with_prices = {p.id for p, _c in self.env['product.supplierinfo'].sudo(
        )._read_group([('partner_id', 'in', Partner.ids)],
                      groupby=['partner_id'], aggregates=['__count'])}

        requires = self.env['res.company'].search([]).filtered(
            lambda c: c.procurement_vendor_policy in (
                'required_for_sourcing', 'required_for_award'))
        counts = {}
        for index in range(0, len(Partner), batch_size):
            batch = Partner[index:index + batch_size]
            for partner in batch:
                in_use = partner.id in with_orders or partner.id in with_prices
                if partner.id in qualified:
                    label = 'assessed'
                elif requires and in_use:
                    # A supplier this database actually buys from, in a
                    # company that has switched control on. This is the
                    # rollout worklist and nothing else here is.
                    label = 'needs_qualification'
                elif partner.id in with_orders:
                    label = 'has_active_purchase_history'
                elif partner.id in with_prices:
                    label = 'has_current_vendor_pricelist'
                elif partner.active:
                    label = 'legacy_active_vendor'
                else:
                    label = 'existing_vendor_unassessed'
                if partner.procurement_vendor_class != label:
                    partner.procurement_vendor_class = label
                counts[label] = counts.get(label, 0) + 1
        return counts

    @api.model
    def action_reclassify_procurement_vendors(self):
        counts = self._classify_procurement_vendors()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Vendors reclassified'),
                'message': ', '.join(
                    '%s: %s' % (dict(VENDOR_CLASS)[key], value)
                    for key, value in sorted(counts.items())) or _(
                        'No supplier partners found.'),
                'sticky': False,
            },
        }
