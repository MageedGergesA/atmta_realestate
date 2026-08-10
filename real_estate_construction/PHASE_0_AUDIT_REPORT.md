# MODULE 6 — `real_estate_construction` 0.1

## Phase 0 Audit

**Scope audited:** 2,099 lines of Python across 14 models, 12 view files, one
wizard, one dashboard, `security.xml`, `ir.model.access.csv`, and every
reference to Construction from the rest of the ATMTA suite.

**Tests in the module before this audit: zero.** There is no behaviour to
preserve on trust, so a baseline was written first —
`tests/test_phase0_baseline.py`, **27 tests, all passing**. Every factual claim
below is backed by one of them. Tests named `test_defect_*` assert the *wrong*
answer on purpose: a defect that cannot be demonstrated cannot be shown to be
fixed either, and each is the test a later milestone must invert.

---

## 1. The headline

The module is a competent **construction tracking** tool. It is not a project
controls system, and the gap is not a list of missing features — it is that
**the numbers it does produce do not reconcile with each other or with Odoo.**

Three findings dominate everything else:

1. **There are three unreconciled "budgets" and nothing notices.**
2. **"Actual cost" never touches the ledger.** No line of Construction code
   reads `account.move.line` or `account.analytic.line`. Actual cost is the sum
   of manually typed cost lines; posted vendor bills do not appear in it.
3. **The quantity control that protects certification is dead code.** A payment
   certificate can certify ten times a BOQ line's quantity without complaint.

Everything in M1–M8 of the brief follows from these.

---

## 2. The eighteen questions, answered from the code

### Q1 — What currently represents budget?

**Three different things, none authoritative.**

| Number | Where | Owner |
|---|---|---|
| `project.expected_budget` | `real_estate_developer/models/project.py:109` | Developer (frozen) |
| `Σ milestone.budget_amount` | `milestone.py` | Construction |
| `boq.total_amount` | `boq.py` | Construction |

They are never compared, never reconciled, and no constraint relates them.
`project.cost_variance` uses the first; the dashboard uses the second; the
certificate's `contract_value` defaults from the second
(`_onchange_milestone_id`); certification consumes the third.

*Proof:* `TestWhatBudgetMeans.test_there_are_three_unreconciled_budgets`.

### Q2 — What represents committed cost?

`realestate.contractor.total_po_committed` — and only there. It is the sum of
`amount_total` for every confirmed PO belonging to that contractor's **partner**,
across **all projects and all companies**. There is no project-level, package-level
or cost-code-level commitment anywhere.

Two further defects in that one field:

- **It is tax-inclusive.** `amount_total` includes tax; every budget it would be
  compared against is tax-exclusive. On a 15% tax a fully committed milestone
  already appears 15% over budget.
- **It is contractor-scoped.** Two projects sharing a contractor read the same
  number, which is the sum of both.

*Proof:* `TestCommitment.test_defect_commitment_is_not_project_scoped`,
`test_defect_commitment_is_tax_inclusive_but_budget_is_not`.

### Q3 — What represents actual cost?

`project.actual_cost = Σ cost_line.amount` — **manually typed cost lines only**,
plus the ones `realestate.construction.labor.log` generates automatically
(`labor_log._sync_cost_line`).

*Proof:* `TestWhatActualCostMeans.test_actual_cost_is_the_sum_of_manually_typed_cost_lines`.

### Q4 — Are PO values included in project cost before invoicing?

**No** — correctly, a commitment is not a cost. But nothing else records the
commitment against the project either, so the number simply does not exist at
project level.

*Proof:* `TestCommitment.test_a_confirmed_po_is_a_commitment_but_reaches_no_project_total`.

### Q5 — Does actual cost double-count vendor bills and manual cost lines?

**It can, and nothing prevents it.** A payment certificate posts a vendor bill
carrying the project's analytic account; `actual_cost` does not move. A cost
controller who then types a cost line for that bill — which
`cost.line.vendor_bill_id` explicitly invites — has the same money in two
places, with no link that would let either notice.

The deeper problem is that **there are two "actual costs"** that are never
compared: the sum of cost lines, and the analytic ledger. Only the first is
displayed; only the second is accounting truth.

*Proof:* `test_defect_a_posted_vendor_bill_is_not_actual_cost`,
`test_defect_the_same_money_can_be_counted_twice`.

### Q6 — Is BOQ the budget, contract scope, or quantity-control document?

**All three at once, in one model.** `boq.py`'s own docstring says "A BOQ is the
engineering budget" *and* "once approved, how much a contractor can certify".
There is a `contractor_id` (contract scope) and a `revision` integer, but no
`type` and no separation between an engineering estimate and a contractual BOQ.

### Q7 — Can a BOQ be revised after approval?

**Yes, silently.** There is no `write()` guard on `realestate.boq` or
`realestate.boq.line` in any state. An approved — or locked — BOQ line's
quantity can be changed by anyone who may edit the record, and the `revision`
counter does not move. `action_reset_to_draft` additionally reopens an approved
BOQ whenever nothing has been certified yet.

*Proof:* `TestBOQCertificationControl.test_defect_an_approved_boq_can_be_edited_in_place`.

### Q8 — Can a payment certificate exceed remaining BOQ quantity?

**Yes, by any amount.** `payment_certificate_line._check_qty` reads:

```python
if ln.qty > ln.boq_line_id.quantity - ln.boq_line_id.certified_qty + \
            sum(l.qty for l in ln.boq_line_id.certification_line_ids if l.id == ln.id):
```

The line being validated is *already in* `certification_line_ids`, so its own
quantity is added back to the ceiling. The test becomes `1000 > 100 - 0 + 1000`
— never true. The guard cannot fire on a first certification, whatever the
quantity. `remaining_qty` then floors at zero, so the overrun is invisible.

A separate guard — cumulative percentage ≤ 100% — sometimes stops the extreme
cases by accident, but it measures a different quantity and disappears whenever
`contract_value` is large or unset.

*Proof:* `test_defect_a_certificate_may_exceed_the_boq_quantity`.

### Q9 — Can two certificates concurrently certify the same remaining quantity?

**Yes, and it needs no unlucky interleaving.** `certified_qty` counts only
certification lines whose certificate is already `certified`, `invoiced` or
`paid`. Two drafts are invisible to each other, and `action_certify()` performs
**no quantity validation of its own** — it checks a percentage and changes
state. There is no lock, no re-read, and no `pg_advisory_xact_lock` anywhere in
the module (Developer's reservation engine has one; Construction has none).

*Proof:* `test_defect_two_certificates_certify_the_same_quantity_twice`.

### Q10 — How is retention accounting represented?

**As a negative line on the vendor bill, with no account of its own.**

```python
invoice_lines.append((0, 0, {
    'name': _('Retention withheld %.2f%% — %s') % (...),
    'quantity': 1,
    'price_unit': -rec.retention_amount,
}))
```

It therefore posts to the same expense account as the work and **reduces the
cost** rather than recording a liability. There is no retention payable, so
there is nothing to release; "release" is a boolean on the contractor
(`retention_released`) plus a computed `total_retention_held`, neither of which
is scoped to a project or a company, and neither of which is on the ledger.

Retention Accrued / Released / Outstanding do not exist as separate concepts.

*Proof:* `TestRetention.test_defect_retention_reduces_the_expense_instead_of_holding_a_liability`,
`test_defect_retention_outstanding_is_a_number_on_the_contractor_only`.

### Q11 — How are advance/mobilization payments handled?

**On the owner side only, and only as a free-text deduction.**
`realestate.owner.progress.billing.deduction` has a `mobilization` kind. On the
contractor side there is no advance field at all: no advance amount, no
percentage, no guarantee, no recovery tracking, no outstanding balance. A
contractor advance cannot be recorded, let alone recovered.

### Q12 — Does owner billing relate to actual construction progress?

**No.** `realestate.owner.progress.billing` is a percentage typed by a user
against a `contract_value`, with `previous_certified_pct` reconciled against
prior billings on the same sale contract. Nothing connects it to
`construction_progress`, to certified BOQ quantity, or to contractor
certification. Owner billing and contractor certification are two independent
percentage ladders.

### Q13 — Is progress quantity-based, milestone-based, task-based or manual?

**Milestone-weighted, computed; with a task-derived input.**
`project.construction_progress = Σ(milestone.completion × weight) / Σ weight`.
Milestone completion may come from weighted tasks (`completion_from_tasks`) or
be typed.

BOQ certification produces a *different* percentage (`boq.certified_pct`) that
does not touch project progress at all. Both are called progress; neither is
labelled as physical, financial or certified.

*Proof:* `TestProgressMeaning.test_project_progress_is_milestone_weighted_only`,
`test_defect_boq_certification_does_not_move_project_progress`.

### Q14 — Can approved change orders modify baselines today?

**There are no change orders.** No change event, no variation, no change order
model exists in the suite.

*Proof:* `TestForecastAndChangeControl.test_defect_no_change_order_model_exists`.

### Q15 — Can the dashboard calculate ETC or EAC?

**No.** There is no forecast model and no ETC or EAC field anywhere. The only
variance is `actual_cost − expected_budget`, which reports what has been spent
against a scalar, and says nothing about what the project will cost.

*Proof:* `test_defect_no_forecast_model_exists`,
`test_the_only_variance_is_actual_minus_a_scalar_budget`.

### Q16 — Does procurement automatically become a commitment?

**Partly, and only at contractor level.** `real_estate_procurement` stamps
`re_project_id` and an analytic distribution on PO lines it creates, so the
project reference survives into accounting. But no Construction record consumes
it: the only "commitment" is the contractor-level PO sum of Q2.

### Q17 — Can subcontract packages be tracked independently?

**No.** `contractor.contract_po_id` is **one PO per contractor**, and the wizard
refuses to build a second one ("This contractor already has a Subcontract PO").
A contractor working on three projects, or holding both a civil and a fit-out
package, has one purchase order and one commitment figure. There is no package
concept at all.

### Q18 — Are there company/project security gaps?

**Yes, comprehensively.**

- **Not one of the 14 models has a `company_id`.** Not the contractor, not the
  BOQ, not the payment certificate, not the owner billing.
- Zero `check_company=True`, zero `_check_company_auto`.
- **`security.xml` contains two groups and no `ir.rule` whatsoever.** There is
  no record rule guarding construction data of any kind.
- A certificate will happily certify a project in company A against a vendor in
  company B.
- The vendor bill built by `action_create_vendor_bill` sets no `company_id`, so
  it lands in whatever company the user happens to be in.
- Two roles exist (User, Manager) against the eight the target design needs.

*Proof:* `TestMultiCompanyGaps` — four tests.

---

## 3. Analytic integration, as it stands

One analytic account per project, created on demand
(`project_analytic._get_or_create_analytic_account`). Two defects in eleven
lines:

```python
plan = Plan.search([], limit=1) or Plan.create({'name': _('Real Estate')})
account = self.env['account.analytic.account'].sudo().create({
    'name': self.display_name,
    'plan_id': plan.id,
    'company_id': self.env.company.id,          # ← the *user's* company
})
```

- The plan is **whichever one sorts first**, not a project plan. On a database
  with a "Departments" plan, project cost centres are filed under Departments.
- The account takes **the user's company**, not the project's.

There is exactly one dimension: the project. No cost code, no WBS, no package.
Every PO line, vendor bill and customer invoice Construction generates carries
`{project_analytic: 100%}` and nothing finer, so the ledger cannot answer "how
much concrete" — only "how much project".

*Proof:* `TestAnalyticIntegration` — three tests.

---

## 4. THE COST CONTROL EQUATION — as it exists today

This is the deliverable the brief asks for before any model is designed. Stated
honestly, the current system computes:

```
    project.actual_cost      =  Σ manually typed cost lines
                                (+ labor logs, which write cost lines)

    project.cost_variance    =  actual_cost − project.expected_budget
```

and, entirely separately and never compared:

```
    contractor.total_po_committed  =  Σ amount_total of that vendor's POs
                                      (all projects, all companies, tax-inclusive)

    contractor.total_billed        =  Σ posted vendor bills for that vendor
    contractor.total_retention_held=  Σ retention on certified certificates

    boq.certified_pct              =  Σ certified amount / BOQ total
    project.construction_progress  =  Σ(milestone completion × weight) / Σ weight
```

### What is wrong with it, precisely

1. **The equation does not close.** Budget, commitment and actual are drawn from
   three different sources on three different scopes (project scalar,
   contractor-wide, manual entry). No identity relates them.
2. **The two sides use different money.** Commitment includes tax; budget does
   not.
3. **Actual cost is not accounting truth.** The ledger has the real number; the
   project shows a typed one. Neither is reconciled to the other, and a
   diligent user who keeps both in step is double-counting by definition.
4. **There is no forward-looking term at all.** No ETC, so no EAC, so the only
   variance available is backward-looking and cannot warn anybody.
5. **Certified is not connected to cost.** Certification consumes BOQ quantity
   and produces a bill, but the certified value never enters actual cost.

### The target equation

```
    ORIGINAL BUDGET     + APPROVED BUDGET CHANGES      = CURRENT BUDGET
    ORIGINAL COMMITMENT + APPROVED COMMITMENT CHANGES  = CURRENT COMMITMENT
    ACTUAL COST         + ETC                          = EAC
    CURRENT BUDGET      − EAC                          = FORECAST VARIANCE
```

with every term defined at **(project, cost code)** granularity, and:

```
    ACTUAL COST  ≡  the analytic ledger, filtered to the project's cost codes
```

That equivalence is the single most important architectural decision in this
module, and it is what makes Rule 1 real: Construction stops holding its own
opinion about what has been spent and starts *reading* Odoo's.

### The migration path, without double-counting money

The danger in adopting a ledger-based actual is obvious: existing cost lines
would be counted a second time. The path:

```
   STEP 1  Cost code dimension exists; every new posting carries it.
   STEP 2  Actual cost is redefined as the analytic truth, and every legacy
           cost line is classified:
              LEDGER_BACKED  → it names a vendor bill that posted ⇒ the ledger
                               already has it; the line becomes presentation
                               only and stops contributing to actual cost.
              ACCRUAL        → labor/equipment with no accounting document
                               ⇒ keep contributing, flagged as an accrual, and
                               reported separately from ledger actuals.
              LEGACY_UNKNOWN → neither ⇒ preserved, reported, and excluded
                               from the equation until a human classifies it.
   STEP 3  ACTUAL = ledger actuals + declared accruals, with the two shown
           separately and never summed silently.
```

Nothing is deleted; nothing is overwritten; the classification is recorded per
line, exactly as the Brokerage commission migration recorded its evidence.

---

## 5. Weaknesses ranked, which is what Phase 0 is for

| # | Weakness | Severity | Milestone |
|---|---|---|---|
| 1 | Actual cost divorced from the ledger; double-count possible | **P0** | M2 |
| 2 | BOQ over-certification guard is dead code | **P0** | M4/M7 |
| 3 | Two certificates may certify the same quantity; no locking | **P0** | M4/M7 |
| 4 | No company on any model; no record rules at all | **P0** | M9 |
| 5 | No change control (event/variation) — baselines cannot move | **P0** | M4 |
| 6 | Retention nets against expense; no liability, no release | **P0** | M7 |
| 7 | Approved BOQ editable in place, no revision | **P0** | M4 |
| 8 | Three unreconciled budgets | **P1** | M2 |
| 9 | Commitment contractor-scoped and tax-inclusive | **P1** | M2 |
| 10 | No ETC/EAC | **P1** | M3 |
| 11 | No cost code / WBS dimension | **P1** | M1 |
| 12 | No contract packages; one PO per contractor | **P1** | M2 |
| 13 | No contractor advance/mobilization | **P1** | M7 |
| 14 | Progress conflates physical, financial and certified | **P1** | M6 |
| 15 | Analytic plan chosen by `search([], limit=1)`; wrong company | **P1** | M1 |
| 16 | Dashboard reads every record and sums in Python | **P2** | M9 |
| 17 | No RFI/submittal/NCR/inspection/claim/EOT/risk | **P2** | M5/M6/M8 |
| 18 | Two roles where eight are needed | **P2** | M9 |

---

## 6. What must not break

Contracts the rest of the suite depends on, verified by reference search:

- `realestate.contractor` — `real_estate_handover.snagging_issue.contractor_id`
  points at it, and `real_estate_contract_template` **inherits** it to add DOCX
  generation. The model name and `partner_id` must survive.
- `realestate.construction.task` and `realestate.construction.milestone` appear
  in `real_estate_procurement`'s `re_source_model` selection.
- `purchase.order.re_project_id` and the analytic distribution stamped by
  `material_request` are the existing procurement bridge and should be extended,
  not replaced (Rule 2).
- Every model ID listed in M47 keeps its identity; no table is dropped.

## 7. Escalation

**None required.** No destructive architecture decision is implied: the target
adds dimensions and baselines beside the existing records rather than replacing
them, and the one semantic change — redefining actual cost as the ledger — has
a non-destructive, classified migration path (§4) modelled on the commission
migration that Module 4 already proved.

Proceeding to M1: WBS / cost-code structure and the analytic integration that
makes the equation addressable.
