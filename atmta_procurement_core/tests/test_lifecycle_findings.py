# -*- coding: utf-8 -*-
"""Regressions found by the procurement lifecycle run, on configuration."""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'atmta_v2')
class TestPolicyIsConfigurable(TransactionCase):

    def test_the_company_policies_declared_here_are_on_the_company_form(self):
        """A policy nobody can reach from a screen is set by SQL or never."""
        arch = self.env['res.company'].get_view(view_type='form')['arch']
        for name in ('procurement_receipt_inspection',
                     'procurement_late_bid_policy',
                     'procurement_allow_self_late_exception',
                     'procurement_addendum_ack_policy'):
            self.assertIn('name="%s"' % name, arch,
                          "%s is not on the company form" % name)
