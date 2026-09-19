# -*- coding: utf-8 -*-
"""The terminology gate.

A treasury system that calls paper "revenue" is worse than no treasury system,
because it produces a number a finance director will act on. The brief names
seven things that must never be confused:

    cheque received · cheque amount on hand · PDC coverage ·
    deposited · in clearing · cleared · cash collected

and two rules in particular:

* **`PDC received` must never be labelled `Collected Revenue`.**
* **`Deposited` must never be labelled `Cleared`.**

These are asserted against the actual field labels, selection labels, payload
keys, view arch and report templates — not against a style guide. The browser
tours assert the same thing again in the rendered DOM, because a label can be
right in Python and wrong on screen.
"""

import os
import re

from odoo.tests.common import TransactionCase, tagged

#: Wording that would misrepresent paper as money.
#:
#: Matched with a negative lookbehind for a negation, because the module says
#: "NOT cash collected" in several places deliberately — the disclaimer is the
#: opposite of the offence and must not be flagged as one.
FORBIDDEN = (
    'collected revenue',
    'revenue collected',
    'cash collected',
    'cash received',
    'collection amount',
    'amount collected',
    'income received',
    'total revenue',
    'revenue received',
)

#: Words that turn a forbidden phrase into an honest disclaimer.
_NEGATIONS = ('not ', 'never ', 'is not ', 'are not ', "isn't ", 'rather than ',
              'no ')


def offending(text):
    """Return the forbidden phrases a string actually claims.

    "PDC amount received, NOT cash collected" is exactly the wording this
    module is supposed to use, so a plain substring test would fail the very
    thing it exists to enforce.
    """
    lowered = (text or '').lower()
    hits = []
    for phrase in FORBIDDEN:
        for match in re.finditer(re.escape(phrase), lowered):
            preceding = lowered[max(0, match.start() - 24):match.start()]
            if any(preceding.endswith(neg) for neg in _NEGATIONS):
                continue
            hits.append(phrase)
    return hits

#: Words that may only ever appear about money Odoo has confirmed.
CASH_WORDS = ('collected', 'revenue', 'income')


@tagged('post_install', '-at_install', 'atmta_checks')
class TestFieldTerminology(TransactionCase):

    def _user_visible_strings(self, model):
        """Every label, help text and selection label on a model."""
        strings = []
        for name, field in self.env[model]._fields.items():
            strings.append((f'{model}.{name}.string', field.string or ''))
            strings.append((f'{model}.{name}.help', field.help or ''))
            if field.type in ('selection',) and isinstance(field.selection, list):
                for value, label in field.selection:
                    strings.append(
                        (f'{model}.{name}.{value}', label or ''))
        return strings

    def test_no_model_calls_paper_money(self):
        models = ('realestate.check', 'realestate.check.deposit',
                  'realestate.check.bounce', 'realestate.check.allocation',
                  'realestate.check.presentation', 'realestate.check.custody',
                  'realestate.sale.contract')
        for model in models:
            for where, text in self._user_visible_strings(model):
                hits = offending(text)
                self.assertFalse(
                    hits,
                    "%s says %r, which presents paper as money" % (where, hits))

    def test_the_coverage_fields_are_named_honestly(self):
        """`secured_by_checks_amount`, never `collected` or `paid`."""
        installment = self.env['realestate.sale.installment']._fields
        self.assertIn('secured_by_checks_amount', installment)
        self.assertIn('unsecured_amount', installment)
        for banned in ('checks_paid_amount', 'collected_by_checks_amount',
                       'checks_collected_amount', 'checks_revenue'):
            self.assertNotIn(banned, installment,
                             "%s conflates securing with collecting" % banned)

        label = installment['secured_by_checks_amount'].string.lower()
        self.assertIn('secured', label)
        for word in CASH_WORDS:
            self.assertNotIn(word, label)

    def test_the_contract_statistics_say_face_value(self):
        contract = self.env['realestate.sale.contract']._fields
        self.assertIn(
            'face value', contract['check_amount_total'].string.lower(),
            "The contract's cheque total must say it is face value, not money")
        help_text = (contract['check_amount_total'].help or '').lower()
        self.assertIn('paper', help_text)
        self.assertIn('not cash', help_text)

    def test_deposited_and_cleared_are_different_words(self):
        """The second explicit rule from the brief."""
        states = dict(self.env['realestate.check']._fields['state'].selection)
        self.assertIn('deposited', states)
        self.assertIn('cleared', states)
        self.assertNotEqual(states['deposited'], states['cleared'])
        # And the label for `deposited` must not contain "cleared".
        self.assertNotIn('cleared', states['deposited'].lower())
        # `in_clearing` is its own state, distinct from both.
        self.assertIn('in_clearing', states)
        self.assertNotEqual(states['in_clearing'], states['cleared'])

    def test_the_accounting_states_do_not_claim_cash(self):
        states = dict(
            self.env['realestate.check']._fields['accounting_state'].selection)
        self.assertEqual(states['reconciled'], 'Bank Reconciled')
        for value, label in states.items():
            if value == 'reconciled':
                continue
            for word in CASH_WORDS:
                self.assertNotIn(
                    word, label.lower(),
                    "Accounting state %r is labelled %r" % (value, label))


@tagged('post_install', '-at_install', 'atmta_checks')
class TestPayloadTerminology(TransactionCase):
    """The dashboard payload is what a front end renders; it must be honest."""

    def test_only_cleared_kpis_are_marked_cash(self):
        data = self.env['realestate.check.dashboard'].get_dashboard_data()
        for kpi in data['on_hand'] + data['deposit']:
            self.assertEqual(
                kpi['kind'], 'paper',
                "KPI %r is marked %r; cheques on hand and at the bank are "
                "paper" % (kpi['key'], kpi['kind']))
        for kpi in data['cleared']:
            self.assertEqual(kpi['kind'], 'cash')
            self.assertIn('cleared', kpi['label'].lower())

    def test_pdc_received_is_never_called_revenue(self):
        """The first explicit rule from the brief."""
        data = self.env['realestate.check.dashboard'].get_dashboard_data()
        received = data['coverage']['pdc_received']
        self.assertEqual(received['kind'], 'paper')
        label = received['label'].lower()
        self.assertIn('face value', label)
        for word in CASH_WORDS:
            self.assertNotIn(
                word, label,
                "PDC received is labelled %r" % received['label'])
        self.assertIn('not cash collected', received['note'].lower())

    def test_no_payload_string_presents_paper_as_money(self):
        data = self.env['realestate.check.dashboard'].get_dashboard_data()

        def walk(node, path='payload'):
            if isinstance(node, dict):
                for key, value in node.items():
                    walk(value, '%s.%s' % (path, key))
            elif isinstance(node, (list, tuple)):
                for index, value in enumerate(node):
                    walk(value, '%s[%s]' % (path, index))
            elif isinstance(node, str):
                hits = offending(node)
                self.assertFalse(hits, "%s claims %r" % (path, hits))

        walk(data)

    def test_the_forecast_refuses_to_promise_cash(self):
        forecast = self.env['realestate.check.dashboard'].get_maturity_forecast()
        note = forecast['note'].lower()
        self.assertIn('not guaranteed cash', note)
        self.assertIn('can still bounce', note)


@tagged('post_install', '-at_install', 'atmta_checks')
class TestViewAndReportTerminology(TransactionCase):
    """Arch and QWeb templates, read as text."""

    def _module_views(self):
        return self.env['ir.ui.view'].search([
            ('model', 'in', ('realestate.check', 'realestate.check.deposit',
                             'realestate.check.bounce',
                             'realestate.check.allocation')),
        ])

    def test_no_view_arch_presents_paper_as_money(self):
        for view in self._module_views():
            hits = offending(view.arch_db or '')
            self.assertFalse(hits, "View %r claims %r" % (view.name, hits))

    def test_the_contract_section_is_labelled_paper(self):
        view = self.env.ref('real_estate_checks.view_sale_contract_form_checks')
        self.assertIn('paper received', view.arch_db.lower())
        self.assertIn('not cash collected', view.arch_db.lower())

    def test_the_source_files_carry_no_forbidden_wording(self):
        """Belt and braces: the templates and tours as they sit on disk."""
        module_dir = os.path.dirname(os.path.dirname(__file__))
        checked = 0
        for root, _dirs, files in os.walk(module_dir):
            if '__pycache__' in root or '/tests' in root:
                continue
            for name in files:
                if not name.endswith(('.xml', '.py', '.js')):
                    continue
                path = os.path.join(root, name)
                with open(path, encoding='utf-8') as handle:
                    text = handle.read()
                checked += 1
                # The forbidden list itself lives in the test package, which is
                # excluded above; negated uses are disclaimers, not claims.
                hits = offending(text)
                self.assertFalse(
                    hits, "%s claims %r" % (path, hits))
        self.assertGreater(checked, 20, "The scan found almost no files")


@tagged('post_install', '-at_install', 'atmta_checks')
class TestAcknowledgementWording(TransactionCase):
    """M24 — the document must not call itself a payment receipt."""

    def test_the_template_disclaims_being_a_receipt(self):
        view = self.env.ref('real_estate_checks.report_pdc_acknowledgement')
        # The disclaimer is emphasised and wrapped, so tags and newlines are
        # normalised away before the wording is asserted.
        text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', view.arch_db))
        self.assertIn('PDC Acknowledgement', text)
        self.assertIn('not a payment receipt', text)
        self.assertIn('no payment has been made', text)

    def test_the_report_action_is_not_called_a_receipt(self):
        action = self.env.ref(
            'real_estate_checks.action_report_pdc_acknowledgement')
        self.assertNotIn('payment receipt', action.name.lower())
        self.assertIn('acknowledgement', action.name.lower())
