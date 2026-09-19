# -*- coding: utf-8 -*-
"""The clearance oracle, pinned.

ATMTA uses **`account.payment.is_matched`** as its accounting clearance oracle.
That choice is load-bearing, subtle, and easy to undo by accident, so it is
pinned here rather than left to a comment.

### Why not `payment.state == 'paid'`

Odoo 18's `account_payment.py::_compute_state` contains:

```python
if (payment.state == 'in_process' and payment.reconciled_invoice_ids and
        all(invoice.payment_state == 'paid'
            for invoice in payment.reconciled_invoice_ids)):
    payment.state = 'paid'
```

and `account.move::_get_invoice_in_payment_state()` returns `'paid'` unless
`account_accountant` overrides it. So a payment is promoted to `paid` the
moment the invoices it is reconciled against read paid — **with no bank
transaction anywhere**. Keying clearance off `state` would mark a cheque
cleared the instant it was *presented*.

### The nuance, stated explicitly

`is_matched` does not mean the same thing in every journal configuration, and
the module relies on that being true:

* **Outstanding-receipts configuration** — the inbound payment method posts to
  a reconcilable Outstanding Receipts account. `is_matched` becomes true only
  when the liquidity line's residual reaches zero, i.e. when the bank-side
  liquidity reconciliation completes. This is the configuration in which
  "presented" and "cleared" are genuinely different states.

* **Direct-to-bank configuration** — the method posts straight into the
  journal's default liquidity account. Odoo then treats the payment as matched
  **immediately** (`_compute_reconciliation_status` short-circuits on
  `journal.default_account_id in liquidity_lines.account_id`, commented in
  Odoo's own source as *"Allow user managing payments without any statement
  lines by using the bank account directly"*). A cheque banked this way clears
  on presentation, and that is **correct for that configuration** — there is no
  separate matching step to wait for. It is not a loophole and must not be
  "fixed".

* **No outstanding account at all** — Odoo raises no journal entry and
  `is_matched` falls back to `state == 'paid'`, the only signal available.

Every test below asserts one of those three, so a future change that swaps the
oracle fails loudly and specifically.
"""

import ast
import inspect
import io
import os
import tokenize

from odoo.tests.common import tagged

from .common import ChecksCommon


def code_only(source):
    """Strip comments and string literals, leaving executable code.

    Every gate below reads the module's own source, and this module documents
    the things it forbids — at length, on purpose. Without this, each gate
    would trip over the docstring explaining why the thing is forbidden.
    """
    out = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for tok_type, tok_str, start, _end, _line in tokens:
            if tok_type == tokenize.COMMENT:
                continue
            if tok_type == tokenize.STRING:
                # Keep short literals (they may be real values like 'cleared');
                # drop docstrings, which are the only multi-line ones here.
                if '\n' in tok_str or len(tok_str) > 120:
                    continue
            out.append((start[0], tok_str))
    except tokenize.TokenError:
        return source
    lines = {}
    for lineno, text in out:
        lines.setdefault(lineno, []).append(text)
    return {no: ' '.join(parts) for no, parts in lines.items()}


def code_lines(path):
    with open(path, encoding='utf-8') as handle:
        return code_only(handle.read())


def module_sources(module_dir):
    for root, _dirs, files in os.walk(module_dir):
        if '__pycache__' in root or os.sep + 'tests' in root:
            continue
        for name in sorted(files):
            if name.endswith('.py'):
                yield os.path.join(root, name)


def writing_functions(module_dir, field, value, model_hint='check'):
    """Names of the functions that write `{field: value}` onto a CHEQUE.

    Attributing the write to its enclosing function is the point: the
    invariant is not "this string appears once" but "one method decides".
    Writes onto other models (a presentation attempt has its own `state`) are
    excluded by looking at what the `.write()` is called on.
    """
    found = set()
    for path in module_sources(module_dir):
        with open(path, encoding='utf-8') as handle:
            try:
                tree = ast.parse(handle.read())
            except SyntaxError:
                continue
        for func in [n for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef)]:
            for node in ast.walk(func):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ('write', 'create')):
                    continue
                # `rec.write(...)` writes the cheque; anything with a longer
                # attribute chain (`rec.current_presentation_id.write(...)`)
                # writes something else.
                receiver = node.func.value
                if not isinstance(receiver, ast.Name):
                    continue
                for arg in node.args:
                    if not isinstance(arg, ast.Dict):
                        continue
                    for key, val in zip(arg.keys, arg.values):
                        if (isinstance(key, ast.Constant) and key.value == field
                                and isinstance(val, ast.Constant)
                                and val.value == value):
                            found.add(func.name)
    return found


@tagged('post_install', '-at_install', 'atmta_checks')
class TestClearanceOracle(ChecksCommon):
    """`is_matched` is the oracle, and `state == 'paid'` is not."""

    def test_the_oracle_is_is_matched(self):
        """Read the implementation, so the intent cannot drift silently.

        Comments and the docstring are stripped first: this method's own
        documentation explains at length why `state == 'paid'` is wrong, and a
        naive substring search would trip over the explanation.
        """
        source = inspect.getsource(
            type(self.env['realestate.check'])._is_cash_confirmed)
        executable = ' '.join(code_only(
            '\n'.join(l[4:] if l.startswith('    ') else l
                      for l in source.splitlines())).values())
        self.assertIn(
            'is_matched', executable,
            "_is_cash_confirmed no longer consults payment.is_matched")
        self.assertNotIn(
            "state == 'paid'", executable,
            "_is_cash_confirmed has been switched back to payment.state, "
            "which Odoo 18 sets without any bank transaction")

    def test_odoo_still_promotes_state_to_paid_without_a_bank(self):
        """The premise. If Odoo ever stops doing this, revisit the comment —
        but do not revisit the oracle, which is correct either way."""
        contract = self._signed_contract()
        target = contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))[0]
        invoice = self._invoice_installment(target)
        check = self._check(amount=invoice.amount_total,
                            sale_contract_id=contract.id,
                            sale_installment_id=target.id,
                            journal=self.journal_outstanding)
        self._deposit(check, journal=self.journal_outstanding)

        payment = check.payment_id
        self.assertEqual(
            payment.state, 'paid',
            "Odoo 18 no longer promotes a fully-reconciling payment to 'paid' "
            "without a bank match. The oracle stays is_matched regardless; "
            "update the reasoning in this module's docstrings.")
        self.assertFalse(
            payment.is_matched,
            "The payment claims a bank match with no statement line")
        # And therefore:
        self.assertFalse(check._is_cash_confirmed())
        self.assertEqual(check.state, 'deposited')

    # ------------------------------------------------------------------
    # Configuration 1 — outstanding receipts
    # ------------------------------------------------------------------
    def test_outstanding_configuration_waits_for_the_bank(self):
        check = self._check(journal=self.journal_outstanding)
        self._deposit(check, journal=self.journal_outstanding)

        payment = check.payment_id
        self.assertTrue(payment.outstanding_account_id)
        self.assertTrue(
            payment.outstanding_account_id.reconcile,
            "The outstanding account is not reconcilable; the configuration "
            "under test is not the one described")
        self.assertNotEqual(
            payment.outstanding_account_id,
            payment.journal_id.default_account_id,
            "This journal posts straight to the bank account; that is the "
            "OTHER configuration")

        self.assertFalse(payment.is_matched)
        self.assertFalse(check._is_cash_confirmed())
        self.assertEqual(check.state, 'deposited')

        self._reconcile_with_bank(payment)

        self.assertTrue(
            payment.is_matched,
            "Bank-side liquidity reconciliation did not mark the payment "
            "matched")
        self.assertTrue(check._is_cash_confirmed())
        check._sync_clearance_from_accounting()
        self.assertEqual(check.state, 'cleared')
        self.assertEqual(check.accounting_state, 'reconciled')

    # ------------------------------------------------------------------
    # Configuration 2 — direct to the bank / default liquidity account
    # ------------------------------------------------------------------
    def test_direct_configuration_is_matched_immediately_and_that_is_correct(self):
        check = self._check(journal=self.journal_direct)
        self._deposit(check, journal=self.journal_direct)

        payment = check.payment_id
        self.assertEqual(
            payment.outstanding_account_id,
            payment.journal_id.default_account_id,
            "This journal is not posting to its default liquidity account; "
            "the configuration under test is not the one described")

        # Odoo short-circuits `_compute_reconciliation_status` here, because a
        # payment posted straight into the bank account has no outstanding
        # balance left to reconcile. There is no separate bank-matching step,
        # so clearing on presentation is the right answer for this setup.
        self.assertTrue(payment.is_matched)
        self.assertTrue(check._is_cash_confirmed())
        check._sync_clearance_from_accounting()
        self.assertEqual(check.state, 'cleared')

    def test_the_two_configurations_diverge_only_on_the_bank_step(self):
        """Same code path, different journals, different timing — by design."""
        direct = self._check(journal=self.journal_direct)
        self._deposit(direct, journal=self.journal_direct)

        outstanding = self._check(journal=self.journal_outstanding)
        self._deposit(outstanding, journal=self.journal_outstanding)

        self.assertTrue(direct._is_cash_confirmed())
        self.assertFalse(outstanding._is_cash_confirmed())

        self._reconcile_with_bank(outstanding.payment_id)
        self.assertTrue(outstanding._is_cash_confirmed())

    # ------------------------------------------------------------------
    # The single writer
    # ------------------------------------------------------------------
    def test_only_one_method_writes_cleared(self):
        """Every route to `cleared` must go through the same test.

        Parsed rather than grepped, and attributed to the enclosing function,
        so the invariant asserted is the one that matters: *which method* may
        write it. A comparison (`== 'cleared'`) is a read and is fine anywhere.
        """
        module_dir = os.path.dirname(os.path.dirname(__file__))
        writers = writing_functions(module_dir, 'state', 'cleared')
        self.assertEqual(
            writers, {'_sync_clearance_from_accounting'},
            "`cleared` is written by %s. It must be written only by "
            "_sync_clearance_from_accounting, so that no route can clear a "
            "cheque on a weaker test than payment.is_matched." % sorted(writers))

    def test_the_manual_action_cannot_bypass_the_oracle(self):
        from odoo.exceptions import UserError
        check = self._check(journal=self.journal_outstanding)
        self._deposit(check, journal=self.journal_outstanding)
        with self.assertRaises(UserError) as err:
            check.action_mark_cleared()
        message = str(err.exception)
        self.assertIn('not been matched against a bank transaction', message)
        self.assertIn('would record cash that has not arrived', message)
        self.assertEqual(check.state, 'deposited')

    def test_the_module_never_writes_an_accounting_field(self):
        """Rule 3, asserted as a source-level gate.

        Parsed, not grepped: this module *declares* a related `payment_state`
        field and quotes Odoo's own `_compute_state` in a docstring, and
        neither is a write.
        """
        module_dir = os.path.dirname(os.path.dirname(__file__))
        offenders = []
        for path in module_sources(module_dir):
            with open(path, encoding='utf-8') as handle:
                try:
                    tree = ast.parse(handle.read())
                except SyntaxError:
                    continue
            for node in ast.walk(tree):
                # `x.payment_state = ...` or `x.amount_residual = ...`
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if (isinstance(target, ast.Attribute)
                                and target.attr in ('payment_state',
                                                    'amount_residual')):
                            offenders.append('%s:%s' % (path, node.lineno))
                # `{'payment_state': ...}` in a write/create payload
                if isinstance(node, ast.Dict):
                    for key in node.keys:
                        if (isinstance(key, ast.Constant)
                                and key.value in ('payment_state',
                                                  'amount_residual')):
                            offenders.append('%s:%s' % (path, node.lineno))
        self.assertFalse(
            offenders,
            "This module writes an accounting field, which Rule 3 forbids:\n%s"
            % '\n'.join(offenders))
