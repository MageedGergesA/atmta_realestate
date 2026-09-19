# -*- coding: utf-8 -*-
"""Regressions for what the construction lifecycle run found on site screens."""
from lxml import etree

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestBoqFormLines(TransactionCase):

    def test_the_boq_form_declares_its_lines_once(self):
        """The form carried `line_ids` twice, once editable and once readonly.

        Two nodes for one x2many give the client two different answers to
        "may these lines be edited", and whichever it loads last wins.
        """
        view = self.env.ref('atmta_construction_site.view_boq_form')
        arch = self.env['realestate.boq'].get_view(view.id, 'form')['arch']
        nodes = etree.fromstring(arch).xpath("//field[@name='line_ids']")
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].get('readonly'),
                         "state in ('locked','cancelled')")
