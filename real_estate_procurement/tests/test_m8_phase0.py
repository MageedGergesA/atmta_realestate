# -*- coding: utf-8 -*-
"""M8.0 — the Phase 0 audit, and the two defects it found.

```
    M7 AWARDS THE CONTRACT AND COMMITS THE MONEY.
    M8 RECEIVES THE GOODS, AND RECEIVING IS WHAT TURNS COMMITMENT INTO COST.
```

M7 closed the chain up to a confirmed purchase order. What it did **not** close
is the other end: a commitment that has been made has to be able to become an
actual cost, and material that arrives on site has to be able to be refused.

Phase 0 opened the way M2 through M7 opened — by reproducing what is actually
there — and found two defects sitting at the M7/M8 seam. Both were first
written here as characterisation tests that demonstrated the broken behaviour,
and both now assert the fixed behaviour instead. What they were is recorded in
each docstring, because a test that only states today's truth teaches nobody
why the code is shaped this way.

**The finding that mattered: the entire tender chain M5 through M7 had only
ever been exercised on a project whose purchase governance is `optional`.**
Every tender test sets it explicitly — `test_m5_migration.py:32`,
`test_m7_phase0.py:123`. On a `controlled` or `required` project, which is the
whole point of the governance ladder and the setting any real construction
company would run, an order awarded by M7 could not be confirmed at all. That
was not a gap in coverage. It was a gap in the product, and coverage is how it
stayed invisible.

The rest of the file pins machinery that already works, so M8 triggers it
rather than building a second copy: M2's receipt rollup, and Construction's
Inspection & Test Plan.
"""

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .test_m7_award import M7Common


class M8Common(M7Common):

    def _approved_award(self, governance='optional', login='m8.approver'):
        """An approved award, with the project's governance already set.

        The policy has to be in force **before** the award is issued, because
        `action_issue()` is what calls `button_confirm()`. The first draft of
        this helper set the policy afterwards, confirmed nothing, and passed
        while proving precisely nothing.
        """
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        award.with_user(self._second_manager(login)).action_approve()
        self._set_po_governance(governance, project=self.project)
        award.invalidate_recordset()
        return award, award.line_ids.purchase_order_id

    def _receive(self, order, ratio=1.0):
        """Validate the order's receipt, in full or in part.

        M8's own machinery is not written yet, so this drives native Inventory
        exactly as a storekeeper would: set the done quantity on each move and
        validate. `qty_received` follows from the moves, which is the whole
        reason `received_qty` is a compute and not a field somebody writes.
        """
        pickings = order.picking_ids.filtered(
            lambda p: p.state not in ('done', 'cancel'))
        self.assertTrue(pickings, "Confirmation produced no receipt to make.")
        for picking in pickings:
            for move in picking.move_ids:
                move.quantity = move.product_uom_qty * ratio
            picking.picking_type_id.create_backorder = 'never'
            picking.button_validate()
        order.invalidate_recordset()
        return pickings

    def _skip_without_construction(self):
        if 're_cost_code_id' not in self.env['purchase.order.line']._fields:
            self.skipTest("Construction is not installed; there is no coding "
                          "to carry.")


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m8')
class TestM8TenderCoding(M8Common):
    """The tender chain now codes what it buys, so a governed project can run one.

    Phase 0 found that `_rfq_line_values()` built a tender RFQ line carrying
    the product, the quantity, the sourcing line and — since M7 — the demand
    link, but never a cost code. Construction overrides `_prepare_rfq_line()`
    for the **requisition** path and there was no equivalent for the **tender**
    path, so every tender line reached award uncoded.
    """

    def test_the_awarded_tender_line_carries_the_cost_code_of_its_demand(self):
        self._skip_without_construction()
        award, order = self._approved_award(login='m8.code')
        lines = order.order_line.filtered(lambda l: not l.display_type)

        self.assertTrue(lines, "The awarded order has no lines at all.")
        self.assertTrue(
            lines.re_material_request_line_id,
            "The demand link M7 added is gone, so this test would no longer "
            "be measuring the tender path.")
        self.assertTrue(
            lines.re_cost_code_id,
            "The tender line reached the award uncoded. This is the M8 "
            "defect returning, and a governed project cannot confirm it.")
        self.assertEqual(
            lines.re_cost_code_id,
            lines.re_material_request_line_id.cost_code_id,
            "The line is coded to something other than the demand behind it.")

    def test_a_governed_project_can_now_issue_an_awarded_tender_order(self):
        """The defect, end to end, and the proof it is closed.

        Before M8 this raised: *"has N line(s) with no cost code."* The refusal
        was correct — uncoded money reaching the cost report as Unassigned is
        exactly what governance exists to prevent. What was wrong is that M5
        gave the buyer no way to code it, so an approved award became an order
        nobody could issue and a vendor who had been told they won.
        """
        self._skip_without_construction()
        award, order = self._approved_award('required', login='m8.gov')

        award.action_issue()

        self.assertEqual(award.state, 'issued')
        self.assertEqual(order.state, 'purchase',
                         "The awarded order still cannot be confirmed on a "
                         "governed project.")

    def test_the_award_issues_under_the_controlled_policy_too(self):
        """`controlled` sits between the two and has its own refusal path.

        One policy per test rather than a loop: `_finalised_round()` builds an
        evaluation plan, and building three in one transaction trips the
        plan-revision unique index — which is the index doing its job, not a
        fixture to work around.
        """
        self._skip_without_construction()
        award, order = self._approved_award('controlled', login='m8.gov.ctl')

        award.action_issue()

        self.assertEqual(award.state, 'issued')
        self.assertEqual(order.state, 'purchase')

    def test_a_tender_line_whose_demand_disagrees_stays_uncoded(self):
        """Derived, never guessed.

        A tender line may aggregate several requisition lines. Where they carry
        different cost codes there is no single answer, and stamping the first
        one would put real money on a code nobody chose. The line stays
        uncoded and the governance gate refuses it at confirmation, which is
        the honest outcome: somebody has to say what kind of money this is.
        """
        self._skip_without_construction()
        event_line = self.event.line_ids.filtered(
            lambda l: len(l.allocation_ids.request_line_id) > 1)[:1]
        if not event_line:
            self.skipTest("This fixture puts one requisition line behind each "
                          "tender line, so there is no disagreement to have. "
                          "The uncoded case below covers the same guard.")
        request_lines = event_line.allocation_ids.request_line_id.with_context(
            re_procurement_revision=True)
        request_lines[0].cost_code_id = self.concrete
        request_lines[1].cost_code_id = self.electrical

        coding = self.event.invitation_ids[:1]._rfq_line_coding(event_line)

        self.assertNotIn('re_cost_code_id', coding,
                         "Two different cost codes produced one answer.")

    def test_a_tender_line_with_any_uncoded_demand_stays_uncoded(self):
        """One uncoded requisition line makes the answer unknown, not majority."""
        self._skip_without_construction()
        event_line = self.event.line_ids[:1]
        request_lines = event_line.allocation_ids.request_line_id
        if not request_lines:
            self.skipTest("The tender line traces to no requisition line.")
        # The demand is approved by this point and M3 refuses a silent rewrite
        # of an approved basis — correctly, and the first draft of this test
        # hit that guard. `re_procurement_revision` is the module's own path
        # for a legitimate change, which is what a buyer removing a wrong cost
        # code would actually go through.
        request_lines.with_context(
            re_procurement_revision=True).cost_code_id = False

        coding = self.event.invitation_ids[:1]._rfq_line_coding(event_line)

        self.assertNotIn(
            're_cost_code_id', coding,
            "An uncoded requisition line was coded from its neighbours.")


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m8')
class TestM8CommitmentBecomesCost(M8Common):
    """Commitment can enter, and actual cost can now follow it in.

    Construction reads commitment from purchase order lines grouped by cost
    code, and deliberately keeps the uncoded ones under Unassigned — money
    committed against a project is a fact whether or not somebody classified
    it. It reads **actual** cost from analytic postings, and the analytic
    distribution on a purchase line is stamped by Construction's own
    `_apply_construction_analytic()`, which needs a project *and a cost code*.

    Phase 0 found a tender order had the first and never the second, so the
    two halves of the same purchase disagreed permanently: the commitment was
    visible and the cost that discharges it could never arrive, however much
    was billed and paid.
    """

    def _confirmed_award_order(self, login='m8.appr2'):
        """Issuing is what confirms, so the tax is stripped before it.

        The control basis is net throughout this suite; stripping tax after
        the order had already confirmed would measure one number against a
        position built from another.
        """
        round_ = self._finalised_round()
        award = self._award(round_)
        award.action_submit()
        award.with_user(self._second_manager(login)).action_approve()
        order = award.line_ids.purchase_order_id
        order.order_line.taxes_id = [(5, 0, 0)]
        award.action_issue()
        order.invalidate_recordset()
        return award, order

    def test_the_awarded_order_commits_and_carries_an_analytic_distribution(
            self):
        self._skip_without_construction()
        award, order = self._confirmed_award_order()
        lines = order.order_line.filtered(lambda l: not l.display_type)

        self.assertGreater(
            self._commitment(self.project), 0.0,
            "The confirmed award did not commit anything, which would be a "
            "different and larger problem than the one being measured.")
        self.assertTrue(
            all(line.analytic_distribution for line in lines),
            "An awarded line carries no analytic distribution, so the bill "
            "against it will post nothing to the project.")

    def test_a_posted_bill_reaches_the_project_as_actual_cost(self):
        """Bill it, post it, and watch the cost report move.

        Asserted by posting a real vendor bill rather than by reasoning about
        what would happen if one were posted. Before M8 this measured 0.00
        against a commitment of millions.
        """
        self._skip_without_construction()
        award, order = self._confirmed_award_order(login='m8.appr3')
        committed = self._commitment(self.project)
        self.assertGreater(committed, 0.0)
        self.assertEqual(self._actual(self.project), 0.0,
                         "Actual cost existed before anything was billed.")

        # Receive it for real before billing it. The first draft of this test
        # built the bill straight from `_prepare_account_move_line()`, which
        # bills `qty_to_invoice` — zero until something is received — so it
        # posted a bill for nothing and measured an actual of zero that had
        # nothing to do with analytic coding.
        self._receive(order)
        invoice = self.env['account.move'].browse(
            order.with_context(create_bill=True)
                 .action_create_invoice()['res_id'])
        invoice.invoice_date = invoice.invoice_date or fields.Date.\
            context_today(order)
        invoice.action_post()

        self.assertEqual(invoice.state, 'posted',
                         "The bill did not post; the measurement below would "
                         "prove nothing.")
        self.assertGreater(
            self._actual(self.project), 0.0,
            "The bill posted and the project's actual cost did not move. The "
            "commitment can never be discharged.")

    def test_the_cost_lands_on_the_code_the_commitment_used(self):
        """Two figures on one line of the cost report, or they are two lines.

        Commitment grouped under one cost code and actual under another is not
        a reconciliation problem — it is a report that shows an over-run and an
        under-run for the same purchase.
        """
        self._skip_without_construction()
        award, order = self._confirmed_award_order(login='m8.appr4')
        lines = order.order_line.filtered(lambda l: not l.display_type)
        code = lines.re_cost_code_id
        self.assertEqual(len(code), 1, "The order spans several cost codes.")

        committed = self.Commitment.current_commitment_by_cost_code(
            self.project)

        self.assertIn(code.id, committed,
                      "The commitment did not land on the awarded cost code.")
        self.assertNotIn(
            False, committed,
            "Part of the commitment is still Unassigned, which is the state "
            "M8 exists to stop a tender producing.")


@tagged('post_install', '-at_install', 'atmta_procurement', 'atmta_m8')
class TestM8ExistingMachinery(M8Common):
    """What M8 must trigger rather than rebuild."""

    def test_the_receipt_rollup_already_exists_and_is_wired_to_inventory(self):
        """M2 built this. M8 extends the meaning of a receipt, not the plumbing."""
        Line = self.env['realestate.material.request.line']
        Request = self.env['realestate.material.request']
        Picking = self.env['stock.picking']

        self.assertIn('received_qty', Line._fields,
                      "M2's receipt rollup field is gone.")
        self.assertTrue(Line._fields['received_qty'].compute,
                        "received_qty stopped being computed; something now "
                        "writes it by hand, which M8 must not do either.")
        self.assertTrue(hasattr(Request, '_refresh_state_from_lines'),
                        "The request-side rollup is gone.")
        self.assertTrue(
            hasattr(Picking, '_action_done'),
            "Procurement no longer hooks receipt validation, which is where "
            "M8's inspection gate has to sit.")

    def test_construction_owns_inspection_and_procurement_does_not_link_to_it(
            self):
        """The seam M8 fills next.

        Construction has a full Inspection & Test Plan with checkpoints and
        hold points. Procurement has no reference to it anywhere. M8 links the
        two; it does not build a second quality model, for the same reason M7
        does not compute commitment.
        """
        self._require_construction()

        self.assertIn('realestate.construction.itp', self.env,
                      "Construction's ITP is gone; M8's inspection gate was "
                      "going to hold on to it.")
        self.assertIn('realestate.construction.itp.item', self.env,
                      "The ITP checkpoint model is gone.")

    def test_the_requisition_receipt_compute_is_dead_code(self):
        """A decorated method wired to no field, which reads as if it works.

        `_compute_state_from_receipts` carries an `@api.depends` and a body of
        `pass`, and no field names it as a compute. It is harmless today and
        actively misleading tomorrow: the next person to touch receipt state
        will find it, believe the rollup runs there, and edit a method that
        has never executed. M8 removes it.
        """
        Request = self.env['realestate.material.request']
        method = getattr(Request, '_compute_state_from_receipts', None)

        if method is None:
            self.skipTest("Already removed, which is the intended end state.")
        computes = {f.compute for f in Request._fields.values() if f.compute}
        self.assertNotIn(
            '_compute_state_from_receipts', computes,
            "It is wired to a field now, so it is no longer dead code and "
            "this test is wrong.")
