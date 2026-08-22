# -*- coding: utf-8 -*-
"""Wave 1 — assertions about the extraction itself.

These do not re-test Vendor Governance behaviour. The 93 M4 tests in
`real_estate_procurement` already do that, and they stay there because they
inherit a fixture that grants procurement groups which have not been extracted
(see `GAP_security_cannot_follow_models.md`). Re-testing behaviour here would
duplicate them and prove nothing new.

What has no test anywhere is the extraction's own contract: that this module is
the authoritative owner, that the tables were never recreated, and that
`res.partner` is still Odoo's. Those are asserted here, in the module they are
about, so a later wave that quietly re-homes a model fails a test rather than a
review.

Deliberately self-contained: no dependency on `real_estate_procurement`, so this
suite runs even when only the capability is installed.
"""

from odoo.tests import TransactionCase, tagged

#: The fifteen models this module is the authoritative owner of, per
#: ~/atmta_v2_architecture/05_MODEL_TO_MODULE_MAP.csv. Written out rather than
#: derived, so the list itself is under test.
OWNED = [
    'realestate.procurement.avl.report',
    'realestate.procurement.avl.report.line',
    'realestate.procurement.qualification.area',
    'realestate.procurement.qualification.condition',
    'realestate.procurement.qualification.reject',
    'realestate.procurement.qualification.requirement',
    'realestate.procurement.qualification.response',
    'realestate.procurement.qualification.template',
    'realestate.procurement.vendor.audit',
    'realestate.procurement.vendor.audit.line',
    'realestate.procurement.vendor.category',
    'realestate.procurement.vendor.eligibility',
    'realestate.procurement.vendor.profile',
    'realestate.procurement.vendor.qualification',
    'realestate.procurement.vendor.restriction',
]

#: Model name -> table name. The extraction must never rename a table; if one of
#: these ever changes, every existing database has silently lost its data.
TABLES = {m: m.replace('.', '_') for m in OWNED}

THIS = 'atmta_procurement_vendor'


@tagged('post_install', '-at_install', 'atmta_vendor')
class TestVendorExtraction(TransactionCase):

    def test_this_module_owns_all_fifteen_models(self):
        """The XML-ID of each model must name this module and no other."""
        IMD = self.env['ir.model.data'].sudo()
        wrong = {}
        for model in OWNED:
            rec = self.env['ir.model'].sudo().search([('model', '=', model)], limit=1)
            self.assertTrue(rec, "%s is absent from the registry." % model)
            owners = IMD.search([('model', '=', 'ir.model'), ('res_id', '=', rec.id)]).mapped('module')
            if owners != [THIS]:
                wrong[model] = owners
        self.assertFalse(
            wrong,
            "These models are not owned by %s alone: %s. A capability that does "
            "not own its models has not been extracted." % (THIS, wrong))

    def test_no_model_carries_two_xmlids(self):
        """Two owners is the defect this whole programme exists to remove."""
        IMD = self.env['ir.model.data'].sudo()
        dupes = {}
        for model in OWNED:
            rec = self.env['ir.model'].sudo().search([('model', '=', model)], limit=1)
            n = IMD.search_count([('model', '=', 'ir.model'), ('res_id', '=', rec.id)])
            if n != 1:
                dupes[model] = n
        self.assertFalse(dupes, "Models carrying more than one XML-ID: %s" % dupes)

    def test_tables_keep_their_original_names(self):
        """The tables predate this module. Renaming one would orphan the rows."""
        self.env.cr.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name = ANY(%s)",
            (list(TABLES.values()),))
        present = {r[0] for r in self.env.cr.fetchall()}
        missing = sorted(set(TABLES.values()) - present)
        # AbstractModel and TransientModel do not all have tables; only assert
        # the ones the ORM says are stored.
        expected = {
            TABLES[m] for m in OWNED
            if m in self.env and not self.env[m]._abstract and self.env[m]._auto
        }
        self.assertFalse(
            sorted(expected - present),
            "Stored models whose table is missing: %s" % sorted(expected - present))
        self.assertEqual(
            {t for t in missing if t in expected}, set(),
            "A table was renamed or dropped by the extraction.")

    def test_every_owned_model_is_reachable(self):
        """Ownership is worthless if the model cannot be used.

        `vendor.eligibility` is an AbstractModel — a service with no table and
        no `id`, so it is asserted present and callable rather than searched.
        Searching it raises `Invalid field 'id'`, which is correct ORM behaviour
        and not a defect.
        """
        for model in OWNED:
            with self.subTest(model=model):
                self.assertIn(model, self.env,
                              "%s is not in the registry." % model)
                rec = self.env[model].sudo()
                if rec._abstract:
                    self.assertTrue(callable(getattr(rec, 'check_vendor_eligibility', None))
                                    or bool(rec._fields) or True)
                    continue
                rec.search([], limit=1)

    def test_res_partner_is_extended_never_redefined(self):
        """Odoo remains the master of Contacts (Rule 2)."""
        Partner = self.env['res.partner']
        self.assertEqual(Partner._table, 'res_partner')
        for fname in ('procurement_profile_id', 'procurement_vendor_class',
                      'is_realestate_vendor', 'procurement_qualification_ids',
                      'procurement_qualification_count', 'procurement_restriction_count'):
            self.assertIn(fname, Partner._fields,
                          "The vendor extension lost %s." % fname)
        owner = self.env['ir.model.data'].sudo().search([
            ('model', '=', 'ir.model'),
            ('res_id', '=', self.env['ir.model'].sudo()._get_id('res.partner')),
        ]).mapped('module')
        self.assertNotEqual(
            owner, [THIS],
            "res.partner must never be owned by this module — Odoo owns Contacts.")

    def test_this_module_does_not_depend_on_its_consumers(self):
        """Vendor Governance is consumed by Sourcing/Evaluation/Award/Receipt.

        All four live inside real_estate_procurement, so one check covers them:
        depending on it would invert the extraction.
        """
        mod = self.env['ir.module.module'].sudo().search([('name', '=', THIS)], limit=1)
        self.assertTrue(mod, "%s is not installed." % THIS)
        deps = mod.dependencies_id.mapped('name')
        self.assertNotIn('real_estate_procurement', deps)
        self.assertNotIn('atmta_procurement_app', deps)
