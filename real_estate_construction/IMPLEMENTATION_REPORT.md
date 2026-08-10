# MODULE 6 — `real_estate_construction`

## Implementation Report

**Baseline:** 0.1 — 2,099 lines of Python, 14 models, **zero tests**
**Current:** 0.6 — M1–M5 delivered
**Frozen dependencies:** `atmta_real_estate`, `real_estate_developer`,
`real_estate_checks`, `real_estate_brokerage`, `real_estate_maquette`,
`real_estate_plan` — **none modified**

Phase 0 is `PHASE_0_AUDIT_REPORT.md`. This report covers what is built on top of
it, milestone by milestone, and states plainly what is not.

---

## 1. Phase 0 findings — the short version

The full audit answers all eighteen questions from the brief against the actual
source, with 27 tests backing every claim. Three findings dominate:

1. **Three unreconciled budgets** — `project.expected_budget`,
   `Σ milestone.budget_amount`, `boq.total_amount`. Nothing relates them.
2. **Actual cost never touches the ledger.** No line of Construction code reads
   `account.move.line` or `account.analytic.line`. Actual cost is the sum of
   manually typed cost lines; a posted vendor bill does not appear in it, and
   typing a cost line for that bill double-counts it.
3. **The BOQ quantity guard is dead code.** `_check_qty` adds the line's own
   quantity back into its ceiling, so `1000 > 100 - 0 + 1000` — never true. A
   certificate may certify ten times a BOQ line's quantity, and two drafts may
   each certify the whole of it because the guard counts only *already
   certified* certificates and `action_certify()` re-checks nothing.

Plus: retention posts as a negative expense line rather than a liability; an
approved BOQ can be edited in place with no revision; commitment is
contractor-wide and tax-inclusive while every budget it is compared against is
tax-exclusive; **no model has a `company_id`** and there are **no record rules
at all**; there is no change control and no forecast.

### The cost control equation, as it was

```
    actual_cost   = Σ manually typed cost lines
    cost_variance = actual_cost − project.expected_budget
```

with commitment, certification and progress computed on three other scopes and
never related to it. The equation did not close, the two sides used different
money (tax-inclusive vs exclusive), and there was no forward-looking term at
all — so no ETC, no EAC, and no variance that could warn anyone.

### The target

```
    ORIGINAL BUDGET     + APPROVED BUDGET CHANGES     = CURRENT BUDGET
    ORIGINAL COMMITMENT + APPROVED COMMITMENT CHANGES = CURRENT COMMITMENT
    ACTUAL COST         + ETC                         = EAC
    CURRENT BUDGET      − EAC                         = FORECAST VARIANCE
```

every term at **(project, cost code)** granularity, and

```
    ACTUAL COST  ≡  the analytic ledger, filtered to the project's cost codes
```

That last identity is the architectural decision the whole module turns on, and
M1 is what makes it addressable.

---

## 2. M1 — WBS / cost breakdown structure

### Two hierarchies, deliberately not one

```
    WBS        where in the works    03 Concrete → 03.02 Slabs → 03.02.01 GF
    COST CODE  what kind of money    SUB-CIV, MAT-CON, LAB-GEN, CONT-01
```

A slab contains labour and concrete; labour appears in slabs and in masonry.
Neither is a parent of the other, so `realestate.construction.wbs` is a
per-project scope tree and `realestate.construction.cost.code` is a cost
catalogue. Budget, commitment and actual are addressed at the intersection.

The brief's rule — scope and category must not share a field — is asserted as a
shape, not a sentence: `test_scope_and_category_are_separate_fields` fails if
anybody ever puts a `category` on the WBS or a `wbs_id` on a cost code.

### Why the cost code is a catalogue

Scope is per project by nature; nobody's "03.02 Ground Floor Slabs" means
anything on another site. Cost codes are the opposite — "SUB-CIV" has to mean
the same thing everywhere or projects cannot be compared.

That difference is also what keeps the analytic architecture affordable, which
is the brief's explicit warning about creating hundreds of analytic accounts:

```
    catalogue:  100 cost codes             → 100 analytic accounts, ever
    per project: 100 codes × N projects    → 100 × N, forever
```

`test_the_catalogue_does_not_multiply_accounts_by_project` builds ten projects
across five cost codes and asserts that exactly **five** cost-code analytic
accounts exist afterwards.

### Analytic integration

Two plans, crossed at posting time, rather than one plan multiplied out:

```
    Plan "Real Estate Projects"     → one account per project
    Plan "Construction Cost Codes"  → one account per cost code

    every Construction-generated line carries one from each:
        {project_account: 100%, cost_code_account: 100%}
```

Odoo validates analytic distributions **per plan**, so both are 100% — this is
one posting seen from two angles, not a posting split in half. The ledger
becomes cross-tabbable by project × cost code with no second accounting
dimension of our own, which is what Rule 4 requires.

`realestate.construction.analytic` is the only place distributions are built
(`distribution_for`) and the only place actual cost is defined
(`actual_domain`, `actual_by_cost_code`). One rule, one place to correct it.

A missing cost code is deliberately **not** an error: a project-level fee knows
its project and nothing finer, and refusing it would push people back to
posting with no analytic at all — which is how the ledger stopped being able to
answer questions in the first place.

### Reading actuals back

`actual_by_cost_code()` uses `_read_group`, not search-and-sum — M46 assumes
hundreds of thousands of cost transactions. Two details that are easy to get
wrong and are asserted:

- Analytic lines carry **one column per plan** (`account_id` for the root plan,
  `x_plan<id>_id` for others). The column names are asked of the plans at run
  time because they depend on plan ids in the database. `auto_account_id` looks
  like the obvious field and cannot be searched — it is a non-stored compute.
- Analytic expenditure is **negative**. The sign is flipped on the way out, so
  a cost report does not show costs as negative numbers.

### Two defects fixed in passing

`project_analytic._get_or_create_analytic_account()` had:

```python
plan = Plan.search([], limit=1) or Plan.create({'name': _('Real Estate')})
... 'company_id': self.env.company.id,
```

- the plan was **whichever sorted first** — on a database with a "Departments"
  plan, every project cost centre was filed under Departments;
- the account took the **user's** company, not the project's.

Both are fixed, and the Phase 0 defect tests that proved them are **inverted
rather than deleted**, so the fix keeps a test in the file that documented the
bug. Existing analytic accounts are never moved: the fix governs the next
account created, not the last one, because moving a cost centre moves history
with it.

### Details worth recording

- **Uniqueness that survives NULLs.** `unique(company_id, project_id, code)`
  looks right and is not — Postgres treats NULLs as distinct, so every
  catalogue code escaped it and `SUB-CIV` could be created any number of times.
  Two partial unique indexes say what was meant.
- **`_has_cycle()`, not `_check_recursion()`** — the latter is deprecated in
  18.0.
- A WBS node cannot be re-parented into another project: a cost booked under
  03.02 must not change project silently.
- WBS `company_id` is a stored related on the project, so scope and company can
  never disagree.

### M1 tests

**54 tests** in `real_estate_construction` (0 before this module was touched):
27 Phase 0 baseline/defect tests, 27 M1 tests covering WBS pathing and
re-parenting, cost-code catalogue and scoping, uniqueness, recursion, the two
analytic plans, distribution shape, catalogue scale, and reading actual cost
back out of the ledger by cost code.

---

---

## 3. M2 — budget baselines, packages, commitment, cost report

### The equation, and the worked case that fixes its meaning

```
    ORIGINAL BUDGET     + APPROVED BUDGET CHANGES     = CURRENT BUDGET
    ORIGINAL COMMITMENT + APPROVED COMMITMENT CHANGES = CURRENT COMMITMENT
    ACTUAL                                            = posted ledger cost
```

`test_m2_equation.py` was written **before** the models, and is the definition
the models were built to satisfy:

```
    Budget            10,000,000
    Confirmed PO       8,000,000 + 1,200,000 VAT
    Bill posted        3,000,000 +   450,000 VAT

    Original Budget    = 10,000,000
    Current Budget     = 10,000,000
    Current Commitment =  8,000,000     ← not 9,200,000
    Actual             =  3,000,000     ← not 3,450,000
```

### Budget — which model is authoritative

`realestate.construction.budget` and its lines. One baselined budget per
project, enforced by a **partial unique index** (`WHERE state = 'baselined'`)
rather than a Python check, because two people baselining two revisions at the
same moment would both read "no baseline yet" and both write one. An advisory
lock on the project makes the loser get a sentence rather than a constraint
traceback.

Baselined figures cannot be edited: `write()` refuses the money and scope
fields, lines cannot be added or removed, and the record cannot be deleted or
cancelled. Change arrives as a **revision** (`action_create_revision` →
`action_supersede`) or, from M4, as an approved change into
`approved_change_amount`. The original stays readable forever, which is the
entire point of a baseline.

Maker/checker: the preparer cannot approve their own budget unless
`real_estate_construction.allow_self_approval` is set — a genuinely one-person
company would otherwise be unable to baseline anything.

### What happened to the three legacy budgets

**All three are preserved. None was promoted, and none was forced to agree.**

| Legacy value | Classification now |
|---|---|
| `project.expected_budget` | High-level estimate. A migration *source*, never a control. |
| `Σ milestone.budget_amount` | Planning allocation. Still drives milestone views. |
| `boq.total_amount` | A valuation. May *seed* a draft budget; never a baseline by itself. |

`realestate.construction.budget.migration.classify()` reports one row per
project — the three values, their spread, and a status:

```
    DETERMINISTIC   one source, or several agreeing within 1%
    AMBIGUOUS       sources disagree materially → a person chooses
    NO_SOURCE       nothing to derive a budget from
    LEGACY_ONLY     already baselined; legacy values kept for reference
```

It **writes nothing** and repeats exactly, asserted by
`test_classifying_writes_nothing_and_repeats_exactly`. Where sources agree it
prefers the one with the most structure (BOQ → milestones → scalar), because
when they agree the choice costs nothing and quantities are worth more than a
number.

`create_draft_budget_from_boq()` produces a **draft**, never a baseline, and
BOQ lines without a cost code are **reported on the record rather than guessed**
— inventing a classification for somebody's quantities is exactly the silent
decision this milestone exists to prevent.

### Commitment — when cost becomes committed

```
    RFQ / draft PO   →  potential. Not committed.
    CONFIRMED PO     →  committed, on confirmation.
    CANCELLED PO     →  no longer committed; the order stays on file.
    RECEIPT / BILL / PAYMENT
                     →  change nothing. Later stages of the same money.
```

### Source precedence — package vs PO, without double counting

```
    package WITH purchase orders    →  the purchase orders are the commitment
    package WITHOUT purchase orders →  its own current contract value
    PO with no package              →  the PO
```

A package is the commercial agreement; a purchase order is how it is executed.
When both exist the order is the operative document, so the package's value
becomes a **comparison, not an addition**. `test_a_package_and_its_po_are_one_commitment`
asserts 8M + 8M = 8M, and `test_orders_take_over_from_the_package_the_moment_they_exist`
watches the source switch as the first order is confirmed.

A package with several cost codes and no orders splits evenly — a stated
convention rather than a guess dressed up as precision, and it disappears the
moment real orders arrive.

Aggregation is **line level** (§19): one order spanning concrete 2M, steel 3M
and plant 1M appears under three cost codes. Lines with no cost code are
reported under "Unassigned" rather than dropped, because money committed
against a project with no code is a fact somebody needs to fix.

### Tax basis — proving the control values are comparable

Everything in the control equation is **untaxed**: budgets are entered untaxed,
commitment reads `price_subtotal` / `amount_untaxed`, and actual excludes tax
lines. Two separate defects sat here, one found by Phase 0 and one found during
M2:

1. **Commitment was tax-inclusive** (`amount_total`), so a fully committed
   1,000,000 milestone appeared 150,000 over budget the moment it was ordered.
   Pinned permanently by `test_commitment_is_untaxed` (§20).
2. **Actual included VAT.** A tax move line inherits its base line's analytic
   distribution — even with `account.tax.analytic` switched off — so VAT
   arrived in the analytic ledger and inflated every actual by the tax rate.
   Found by the equation test returning 3,450,000; fixed by excluding tax lines
   from the actual domain, and asserted in the same test.

VAT accounting itself is untouched. Odoo continues to account for tax normally.

### Actual — exactly which ledger records count

`account.analytic.line`, filtered to the project's analytic account, **excluding
tax lines**, grouped by the cost-code plan column with `_read_group`. The sign
is flipped on the way out because analytic expenditure is negative and a cost
report showing negative costs is read wrong by everyone who opens it.

```
    PO confirmed      →  actual unchanged
    bill in draft     →  actual unchanged
    bill posted       →  actual moves
    payment           →  actual unchanged
```

All four asserted. Paying an expense does not incur it a second time.

### Cost lines — what they are for now

`realestate.construction.cost.line` is **kept**, and every line now says what
it is:

| `cost_basis` | Meaning | Counts as actual? |
|---|---|---|
| `ledger_backed` | The money is already in Accounting | **No** — the ledger has it |
| `accrual` | Real cost, no accounting document yet | Yes, and reported *beside* ledger actuals |
| `estimate` | Informational | No |
| `legacy` | Nobody has classified it | No |

The default is `legacy`, so nothing is silently promoted to a cost.
`project.actual_cost` survives for the views that use it but now sums declared
accruals only — which is why the Phase 0 double-count test could be inverted
into `test_the_same_money_cannot_be_counted_twice`.

### Retention — isolated and disclosed, **not** fixed

M2 did **not** correct it, and the report says so on its face.

The defect is real: retention posts as a negative line on the vendor bill with
no account of its own, so it nets against the expense. A 100,000 certificate
with 5% retention posts 95,000 of cost, and the 5,000 being held appears
nowhere as a liability. Since actual cost is now *defined* as the ledger, that
understates it.

Why it was not brought forward: the correct treatment —

```
    gross certified    →  project cost
    retention withheld →  retention payable (liability)
    net                →  vendor payable
```

— changes how certificates post, and needs the retention register, the release
workflow and advance recovery beside it. That is M7. Doing it here would mean
designing M7 inside M2; doing it partially would leave two certificate
accounting paths, which is worse than one wrong one.

What M2 does instead: `realestate.construction.retention.disclosure` identifies
the affected certificates, quantifies the understatement **exactly**, and puts a
warning on the face of the Cost Report naming the amount. Posted moves are
never rewritten (Rule 3) — correcting existing bills is a Finance
reclassification, and the report says that too.

### Cost Report — every column, and one that is deliberately missing

| Column | Equation |
|---|---|
| Original Budget | baselined lines, per cost code |
| Approved Budget Changes | M4 writes these; 0 until then |
| Current Budget | original + changes |
| Original Commitment | confirmed POs and package values, untaxed |
| Approved Commitment Changes | M4 writes these; 0 until then |
| Current Commitment | original + changes |
| Actual Cost | posted analytic ledger, tax excluded |
| Available Before Commitment | Current Budget − Current Commitment |
| Remaining vs Actual | Current Budget − Actual |
| ETC / EAC / Forecast Variance | **blank** — M3 owns them |

**There is no total-cost column, and there is no way to make one.** Commitment
and actual are two views of the same money: a 10M order with 4M billed has not
cost 14M. `test_the_report_never_offers_commitment_plus_actual` asserts the
field does not exist, because offering the number at all invites somebody to
use it.

ETC/EAC are **blank rather than zero**: a zero reads as "nothing left to spend",
which is the opposite of "we have not forecast yet".

Every figure drills into its records (§29) — budget lines, the orders behind a
commitment, the analytic postings behind an actual. The actual drilldown runs
**as the user, not `sudo()`**: a cost controller who may not read the general
ledger should be told so by Accounting, not shown it by us.

### Multi-company

Every M2 model carries `company_id`, `_check_company_auto` and `check_company=True`
on its relations, and constraints refuse: a budget whose company differs from
its project's, a package whose contractor belongs to another company, a budget
line pointing at another project's WBS, a cost code scoped to a project in a
different company. `test_another_companys_project_contributes_nothing` asserts
that control totals do not leak across companies.

### Performance

`_read_group` throughout — budget by cost code, commitment by cost code, actual
by cost code. No search-and-sum anywhere in the M2 path.

### Concurrency

Baseline activation takes `pg_advisory_xact_lock` on the **project** (the thing
being protected is "this project has one baseline", which a row lock on either
budget would miss), backed by the partial unique index. Commitment is a read:
recomputing it repeatedly creates no rows, asserted.

### Tests

**126 tests** in `real_estate_construction` (54 at the end of M1).

Phase 0 defect tests converted to positive regressions in M2 — **none deleted**:

| Was | Now |
|---|---|
| `test_defect_a_posted_vendor_bill_is_not_actual_cost` | `test_a_posted_vendor_bill_is_actual_cost` |
| `test_defect_the_same_money_can_be_counted_twice` | `test_the_same_money_cannot_be_counted_twice` |
| `test_actual_cost_is_the_sum_of_manually_typed_cost_lines` | `test_a_typed_cost_line_no_longer_becomes_actual_cost_by_itself` |
| `test_the_only_variance_is_actual_minus_a_scalar_budget` | `test_the_legacy_variance_now_runs_on_declared_accruals_only` |

Still open as defect tests, by design — they belong to later milestones:
the BOQ quantity guard and concurrent certification (M4), retention treatment
(M7), missing change control and forecasting (M3/M4), the dashboard's
search-and-sum (M9), and the multi-company gaps on the **legacy** models (M9).

### Two bugs M2 caught in its own work

- **The analytic distribution format.** Odoo 18 encodes a multi-plan
  distribution as **one comma-joined key** (`{"7,42": 100}`). Two separate keys
  are two distributions: Odoo writes two analytic lines, each for the full
  amount, double-counting every cost and leaving neither able to say both which
  project and which cost code it belongs to. Caught by the equation test.
- **A default on an editable computed field.** `original_amount` had
  `default=0.0`, which means every `create()` supplies a value and the compute
  never runs — every quantity line landed as zero.

---

---

## 4. M3 — Forecast / ETC / EAC

### Forecast architecture

```
    ETC               = expected future cost from the forecast date forward
    EAC               = ACTUAL + ETC
    FORECAST VARIANCE = CURRENT BUDGET − EAC       positive = favourable
```

Three invariant tests were written before the models and are what everything
else elaborates:

| Test | Case | Expected |
|---|---|---|
| 1 | Budget 10M, commitment 8M, actual 3M, manual ETC 5M | EAC 8M, variance **+2M** |
| 2 | Budget 10M, actual 4M, ETC 8M | EAC 12M, variance **−2M**, budget still 10M |
| 3 | Budget 10M, no commitment, no actual, no method | ETC **missing**, not zero |

`realestate.construction.forecast` is a dated snapshot with lines per cost code
and named adjustments beneath them.

### Forecast methods, and their prerequisites

There is no universal ETC formula, and the brief is right that pretending
otherwise is the bug. Each method states what it needs and refuses when it does
not have it:

| Method | ETC | Refuses when |
|---|---|---|
| `manual` | what a person entered | no stated basis |
| `remaining_commitment` | commitment − attributable actual | nothing committed; attribution unknown |
| `finish_at_budget` | current budget − actual | never — but it is *named* an assumption |
| `percent_complete` | actual ÷ progress − actual | progress ≤ 0, > 100, or no posted cost |
| `date_range` | remaining periods × rate | negative periods or rate |
| `remaining_boq` | — | **disabled entirely** (below) |
| `not_required` | — | a decision, not a gap |

`finish_at_budget` is deliberately not called an ETC calculation. It is the
assumption "we will finish at budget", and its status line says so, because a
forecast that merely repeats the budget should never look like a prediction.

**`remaining_boq` is unavailable.** §34: Phase 0 proved a certificate may
exceed its BOQ quantity and that two certificates may consume the same
remaining quantity. Forecasting from those figures would put a known-wrong
number into EAC. The method exists in the selection so the intent is visible,
returns `insufficient_data` with that explanation, and
`test_the_boq_method_is_unavailable_while_its_data_is_untrustworthy` asserts a
forecast using it **cannot be approved**. M4/M7 will enable it.

### Actual cutoff

`as_of_date` is the cutoff, and actual is read through the **same M1 service**
extended with `date_to` — not a second actual-cost calculation. A bill dated
April is not March's cost, asserted directly.

### Commitment attribution — when remaining commitment is authoritative

`committed_remaining` is only produced where the actual on a cost code can be
attributed to the commitment on that same cost code. Where it cannot,
`committed_remaining_known` is **False** and the report shows **N/A**, never 0.
Zero means known zero; N/A means unknown, and M2 was right to refuse to fake
the difference.

When actual exceeds commitment the remaining is floored at zero and
`actual_exceeds_commitment` is raised — a negative remaining commitment is not
a forecast, it is a contradiction.

### Unawarded scope

Budget with no commitment is exactly where a naive `commitment − actual` gives
zero and says the remaining work is free. `remaining_commitment` **refuses** on
such a line rather than returning zero, `is_unawarded` flags it, and
`uncommitted_etc` shows how much of the forecast still needs procuring.

### Missing data — why missing ≠ zero

The rule that shapes the whole milestone. `has_etc` distinguishes "no forecast"
from "a forecast of zero"; `forecast_status` names which of missing /
insufficient / not-required applies and `status_reason` says why in a sentence.
Totals sum only lines that *have* an ETC, `forecast_coverage` says how much of
the money that leaves out (weighted by budget, not by line count), and
`eac_is_complete` refuses to call a partial total authoritative. An incomplete
forecast **cannot be approved**.

### Snapshot and versioning

A draft refreshes budget, commitment and actual on demand and keeps manual
inputs — a refresh updates facts, not judgement. An **approved** forecast is
frozen: its lines reject writes, `action_refresh` refuses, and it cannot be
reopened. One approved forecast per project per date, enforced by a partial
unique index plus an advisory lock, because a Python check cannot stop two
people approving competing forecasts for the same period simultaneously.

Copy-forward carries method, manual estimate, basis and comments to the next
period, and **never** the previous actual.

### Forecast movement

`previous_eac`, `eac_movement`, and a bridge that categorises the movement by
the adjustment types people recorded — and reports the **unexplained** residual
rather than hiding it. An unexplained 3M swing is exactly what a bridge is for.

Adjustments (productivity, inflation, anticipated variation, market rate,
delay, unawarded scope, risk, FX assumption, management) change the forecast and
**nothing else**: not budget, not commitment, not the ledger. Asserted.

### Cost Report V2

Adds ETC, EAC, Forecast Variance, Previous EAC, EAC Movement, Forecast Method,
Committed Remaining and forecast freshness. Three rules survive intact:

- **still no commitment + actual column**, asserted again;
- a cost code the approved forecast does not cover shows `has_forecast = False`
  rather than a confident zero;
- **draft forecasts never reach the report** — an unapproved number is somebody's
  working paper, and a reader cannot tell once it is in a column.

Freshness: `ctrl_forecast_age_days`, `ctrl_forecast_is_stale` against a
configurable interval (`forecast_interval_days`, default 35 — not hard-coded at
30), and `ctrl_forecast_state` of none / incomplete / complete / stale.

### Retention

Unchanged and still disclosed. Forecast actual uses ledger actual **as-is**, so
Forecast Actual and Cost Report Actual agree; the same retention warning
appears on the forecast. Quietly adding retention back into EAC would make the
two disagree, which is worse than the known understatement. M7 corrects the
accounting path.

### Multi-company

`company_id`, `_check_company_auto` and `check_company=True` throughout; a
forecast generated for another company's project produces no lines and zero
totals, asserted.

### Performance

Forecast generation pre-aggregates budget, commitment, actual and the previous
forecast **once per project** and then creates lines in a single batch. A
project with two thousand cost codes costs four queries, not eight thousand.

### Tests

**173 tests** in `real_estate_construction` (126 at the end of M2).

Edge cases covered: actual > commitment, actual > budget, zero budget, zero
progress, progress > 100, negative periods, unbudgeted scope, unawarded scope,
ETC above remaining commitment, negative (saving) adjustments, override without
reason blocking approval, manual without basis, stale forecast, coverage
weighting, approval races, and company isolation.

Phase 0 defect test converted: `test_defect_no_forecast_model_exists` →
`test_forecasting_exists_and_produces_etc_and_eac`.

One real model bug caught by its own test: `manual_reason` was missing from
`_compute_etc`'s `@api.depends`, so adding the basis to a manual estimate left
the line stuck at `insufficient_data`.

---

---

## 5. M4 — Change Events and Change Orders

### The four invariants, written before the models

| Test | Case | Expected |
|---|---|---|
| A | Budget 10M, potential change estimated 2M | Budget still 10M; exposure 2M, labelled potential |
| B | Original 10M + approved implemented 2M | Original 10M, changes 2M, **current 12M** |
| C | Commitment 8M + approved variation 2M | Current 10M, **actual unchanged** |
| D | Anticipated 2M later approved as 2M | Counted **once**; March's approved forecast untouched |

### Change Event — something happened that *may* matter

`realestate.construction.change.event` records the occurrence and somebody's
estimate of it. Its lifecycle is draft → identified → under review → pricing →
assessed → change required (or no change / rejected / duplicate / cancelled).

**Its estimates are read by nobody in the control equations.** That is the
whole point: an estimate that could move a budget would make every site
engineer's guess an authorisation, and "what has actually been approved?" would
stop having an answer. Exposure reporting reads them, and labels them potential
everywhere they appear.

Duplicates are **warned about, never merged**: two people recording the same
site instruction is common and worth flagging, but an event that quietly
absorbed another would lose somebody's estimate without anybody deciding to.

### Change Order — the commercial act, and the only thing that moves a baseline

Seven types (contractor / supplier / owner variation, budget change, budget
transfer, contingency drawdown, internal), one model, because four models named
after other systems' acronyms would be four sets of the same bugs.

### Amount stages — a history, not a field

```
    estimated   1,500,000     the site's first view
    quoted      1,900,000     what the contractor asked
    submitted   1,900,000     what went forward
    assessed    1,650,000     what the QS thought it was worth
    negotiated  1,700,000     what was agreed
    approved    1,700,000     what authority granted
```

Every stage is its own column on every line, and none overwrites another.
Re-submission snapshots a **revision**, so a contractor's second quotation does
not erase the first — the pair *is* the negotiation.

Markups (overhead, profit, bond, insurance, supervision) are priced **beside**
the scope, never inside a unit rate: a markup buried in a rate cannot be
negotiated and cannot be audited.

### Approved is not implemented

Approval is authority; implementation is the transaction that writes the impact
records. Separating them lets an approved change be reviewed before anything
moves, and gives implementation a single idempotent entry point.

### Budget propagation

Implementation writes `realestate.construction.budget.change.line` records, and
the baselined budget line's `approved_change_amount` is **computed from them**.
So `current = original + changes` holds by construction, every unit of change
points at the change order that authorised it, and the baseline is never
rewritten. The change records reject `write()` and `unlink()` outright.

- **Increase**: original 10M, approved 2M → current 12M, original still 10M.
- **Transfer**: −500K on one code, +500K on another; project total unchanged,
  and a transfer that does not net to zero is **refused at approval** as "an
  increase wearing a transfer's name".
- **Contingency drawdown**: contingency −1M, scope +1M; total unchanged,
  because spending contingency is using authority already granted, not new
  authority.

### Commitment propagation

Implementation writes `realestate.construction.commitment.change` records. The
engine's sources become three, and the M2 precedence rule extends to
variations:

```
    confirmed purchase orders
  + awarded packages with no orders
  + approved commitment changes NOT yet folded into an order
```

`reflected_in_purchase_order` is the switch. When a variation is folded into an
amended PO, the order carries the new value and the change record stops
contributing — so package 8M + PO 8M + variation 2M is **10M**, never 12M and
never 18M, before *and* after the order is amended. Both paths asserted.

A package's `approved_variation_amount` is likewise computed from its
implemented changes; `original_contract_value` is never touched.

### Revenue changes

Owner-side value lives in `realestate.construction.revenue.change`, separate
from commitment, because cost and revenue variations are different money and
routinely different amounts: cost +1.0M against revenue +1.3M gives a change
margin of +300K. Labelled variation margin, not project profit.

### Forecast integration

M3's `anticipated_variation` adjustment now carries a `change_event_id`. When
the change is implemented, the adjustment is marked `converted_to_change_order`
so the **next** forecast does not count the anticipation and the approved
commitment as two separate impacts.

**Approved historical forecasts are never rewritten.** March's forecast still
contains its anticipation, because that is what was believed in March, and
altering it would destroy the record the milestone exists to keep.

### Approval authority

Thresholds live in configuration parameters, never in code — every company's
delegation differs, and a hard-coded number is one somebody must fork the
module to change. Judged on **gross** impact: +5M on one code and −5M on
another nets to zero and is emphatically a decision worth approving.

Self-approval is refused by default, with a configurable limit. Every decision
is recorded with who, when, and the amount at the time.

### Idempotency

`action_implement()` is idempotent three ways: an already-implemented order
returns early, an advisory lock serialises concurrent callers, and the
existence of the linked impact records — not a flag somebody might forget to
set — is what stops a second application. Calling it twice produces exactly one
budget change, one commitment change and one revenue change; asserted.

All effects run in the caller's transaction, so a failure part-way leaves
nothing applied. A half-applied change — budget moved, commitment not — is
worse than one that failed cleanly.

### Negative changes and omissions

Omissions are supported and reduce the commitment. What is refused is an
omission that would take a contract **below what has already been certified**:
original 10M, certified 8M, omission −5M is rejected, because a contract worth
less than what has been paid under it is not a contract but an error waiting
for an auditor.

### Tax

Control amounts are tax-exclusive throughout. A 1M variation is 1M of
commitment whatever the VAT on the eventual invoice; asserted alongside a
posted 15%-VAT bill that moves actual by 1M.

### Migration

**No historical change orders are invented.** Legacy Construction has no change
management, and reconstructing it from budget differences, edited PO values or
edited BOQ quantities would be fabricating decisions nobody made. Authoritative
change history starts at M4.

### Tests

**214 tests** in `real_estate_construction` (173 at the end of M3).

Covered: event lifecycle and dismissal, duplicate warning, event → order,
one event with several orders, all six amount stages surviving, revisions,
markups, multi-cost-code lines, budget increase / transfer / contingency
drawdown, transfer netting validation, package variation with original frozen,
linked package+PO counted once (both before and after PO amendment), omission,
omission below certified value refused, approved change never creating actual,
commitment exceeding budget without moving it, cost vs revenue vs margin,
idempotent implementation, immutability after implementation, self-approval,
gross-vs-net authority, server-side authority refusal, tax basis, multi-company
refusal, and exposure counts matching their drilldown.

Phase 0 defect test converted: `test_defect_no_change_order_model_exists` →
`test_change_control_exists_and_can_move_a_baseline`.

Two bugs caught by these tests: `action_implement` raised instead of skipping on
a second call (its own docstring promised otherwise), and a markup compute that
depended on the order's stored total rather than on the source lines, so it
could resolve to zero depending on recompute order.

---

---

## 6. M5 — RFI, submittals, document control, transmittals

### M5.0 audit — what already existed

**Nothing.** A repository-wide search for RFI, submittal, transmittal, document
register, drawing revision and document number found no model representing any
of them. Two near-misses were checked and are different concepts:

- `realestate.contract.document` (contract_template) is *generated contract
  output* from a DOCX template — not a controlled-document register.
- `realestate.offer.revision` (brokerage) and
  `realestate.construction.change.order.revision` (M4) are revision records for
  offers and change orders.

Attachments are used as plain `ir.attachment` many2many across the suite, which
is the file store M5 builds on.

**Odoo Documents is not installed** — this is Community, and `addons/documents`
does not exist. So Documents is **not used and not a dependency**: the register,
the metadata and every workflow run on `ir.attachment`. If a deployment ever
adds Enterprise Documents, folder and file-version handling could be layered on
without changing any of the M5 models.

### The four invariants

| Test | Case | Expected |
|---|---|---|
| A | RFI says "potential cost 2M" | Budget, commitment and actual all unmoved |
| B | RFI raised against Rev B; Rev C arrives | RFI still references **Rev B** |
| C | Submittal Rev 0 revise-and-resubmit, Rev 1 approved | Both survive; Rev 1 current |
| D | Transmittal sent with Rev B; Rev C arrives | Transmittal still records **Rev B** |

### RFI architecture

```
    question → ball in court → official response → close
                                    └→ (somebody decides) → M4 CHANGE EVENT
```

- **Ball in court** is a computed field, not an inference from a status label:
  the question a project manager asks is "who has it?", so the record answers
  it.
- **One official response**, with an author and a date — not the last chatter
  message. Revising it archives the previous one into
  `realestate.construction.rfi.response`, which rejects `write()` and
  `unlink()`. An issued answer is never erased.
- **Closing without an answer requires a stated reason.** An unanswered
  question that quietly disappears is how disputes start.
- **Response time uses real dates** (submitted → answered), never "today" once
  an answer exists, and an answered RFI stops accruing overdue days.
- Cost and schedule impact are **classifications** (unknown / none / potential /
  confirmed), not free numbers that could be mistaken for authority.

### Submittal architecture

```
    SUB-00425
      └ Rev 0  submitted → reviewed → revise & resubmit
      └ Rev 1  submitted → reviewed → approved          ← current
```

- **State is not response.** "Responded / revise and resubmit" is a normal
  combination, and one field could not express it.
- A revision that has been responded to cannot be resubmitted or re-answered; a
  new revision is required, and the old one keeps the comments that *caused* it.
- **Parallel reviewers**: finalising is refused while reviewers are outstanding
  ("2 of 3 have replied"), unless somebody finalises deliberately. Marking a
  review complete because one of three replied is how comments get lost.
- An accepted submittal updates the controlled revision's status — but **only**
  when the submittal explicitly references a registered revision. Not every
  attachment becomes a controlled document.
- Responsible contractor is both a `realestate.contractor` and a partner,
  because not every submitter is an Odoo user.

### Document control — three concepts, deliberately not one

```
    DOCUMENT     stable identity        A-ARC-DRG-1001
      └ REVISION formal issue           Rev A, B, C
          └ FILE technical replacement  file_version 1, 2 …
```

Correcting a title-block typo before issue bumps `file_version` and leaves the
revision code alone — the project is not told there is a new revision because
somebody fixed a spelling mistake. Once issued, the file and the revision
identity are frozen: `write()` refuses, `unlink()` refuses, and a correction is
a new revision.

**Ordering is explicit.** Revision codes are `A, B, C` here and `00, 01, 02`
there, and `max()` on a string eventually picks `9` over `10`. Every revision
carries an integer `sequence`, and a test asserts that a document with
revisions 8, 9 and 10 reports **10** as current.

**Three different "currents", because they are three different questions:**

| | Meaning |
|---|---|
| `current_revision_id` | the latest revision, whatever its state |
| `current_approved_revision_id` | the latest revision that was ever approved |
| `current_issued_revision_id` | the latest approved revision issued for construction |

A test builds Rev A (approved, for construction) then Rev B (for review) and
asserts all three answer differently. That test caught a real modelling error:
approval was originally judged from the current *state*, so superseding an
approved Rev A with a for-review Rev B silently un-approved it — and the
register would have stopped telling site what it was building from. Approval is
now judged on `approved_on`, which is a durable fact; supersession is another.

**Status and purpose are separate fields.** A revision may be Approved *and*
For Construction; those are two facts and one field cannot hold both.

### Transmittals — exact revision evidence

A line **snapshots** the document number, title, revision code, status and
purpose at the moment of sending, as copied text rather than related fields. A
related field would follow the document if it were renumbered; the point of a
transmittal is that it does not move. The revision link is kept as well, so the
file is one click away.

Once sent: lines cannot be edited or removed, the transmittal cannot be
cancelled, and a transmitted revision cannot be voided. Acknowledgement records
receipt and is explicitly **not** approval — asserted by checking the revision's
state is untouched after acknowledgement.

### Change integration — proving nothing bypasses M4

`test_drawing_to_rfi_to_change_event_to_approved_change` walks the whole path
and asserts the budget at each step:

```
    Rev B issued                        budget 10M
    RFI raised, answered                budget 10M
    change event created from the RFI   budget 10M   ← still
    M4 change order approved+implemented budget 12M   ← only here
```

and then adds Rev C to confirm the RFI still points at Rev B.
`test_information_records_never_touch_the_ledger` creates a document, an RFI, a
submittal and a transmittal and asserts the project's control totals dict is
**identical** before and after.

### Security and multi-company

Every M5 model carries `company_id`, `_check_company_auto` and
`check_company=True` on its relations, with constraints refusing a record whose
company differs from its project's. Documents carry a `confidentiality`
classification so read access follows the document rather than whoever can open
a related record.

### Performance

Indexed: project, company, state, due dates, document number (trigram),
discipline, package, revision sequence. Overdue flags are stored so registers
filter in the database rather than in Python.

### Migration

**Nothing is converted.** Existing attachments stay attachments: a file called
`drawing.pdf` with no number, discipline or revision cannot be turned into a
controlled document without inventing all three, and inventing document control
is worse than not having it. Document Controllers register documents
deliberately; the register starts empty and fills with what somebody decided
belongs in it.

### Tests

**258 tests** in `real_estate_construction` (214 at the end of M4).

Covered: RFI numbering, lifecycle, ball in court, answer-requires-response,
close-without-answer-requires-reason, response history immutability, overdue,
response time, void refusal, one change event per RFI, company isolation;
document identity, revision supersession, sequence ordering, three currents,
status vs purpose, issued-file immutability, draft file version, delete refusal,
issue-without-file refusal, transmitted-revision void refusal, number
uniqueness; submittal revision zero, state vs response, resubmission refusal,
double-response refusal, revision gating, parallel reviewers, deliberate
finalisation, controlled-revision approval, close gating, immutability,
packages; transmittal content requirements, send freezing, draft cancellation,
acknowledgement-is-not-approval, overdue acknowledgement, multi-revision sends;
and the two cross-module flows.

### Not built in M5, by instruction

QA/QC, ITP, NCR, daily site reports, certificate or retention redesign, claims,
EOT, scheduling, a generic DMS, CAD viewing or markup. The change-event source
hooks (`source_model` / `source_id`) are the integration point and are exercised
by the cross-module test.

---

# M6 — QA/QC, ITP, Inspections, NCR, Daily Site Reports

## The four principles, as enforced code

| Principle | Where it is enforced |
|---|---|
| Inspection failure ≠ NCR | `action_record_result()` records a failed result and stops. `action_create_ncr()` and `action_create_observation()` are separate, manual, and never called by the result path. |
| NCR ≠ change order | `ncr.action_create_change_event()` creates a **change event** (M4 gate 1), never a change order. Nothing calls it automatically. |
| Quality records reference exact project information | Inspections store `itp_revision` and `document_revision_id` as recorded values; `write()` refuses to move them once a result exists. |
| Quality ≠ progress | No quality model writes progress, BOQ quantity, certified value, or actual cost. `is_quality_released()` returns a fact for M7 to consult; it grants nothing. |

## ITP architecture

`realestate.construction.itp` is per project, optionally per package/contractor,
and holds ordered `realestate.construction.itp.item` checkpoints. It is not a
checklist: it is the plan that says *which* checks exist, at what point, against
what criteria, and who must be present.

Revision control is real. `action_create_revision()` copies the plan to
`revision + 1` in `draft` and links `supersedes_id` / `superseded_by_id`; the old
revision is only marked `superseded` when the new one is activated. A partial
unique index enforces one live revision per plan:

```sql
CREATE UNIQUE INDEX construction_itp_one_active
    ON realestate_construction_itp (project_id, name)
 WHERE state = 'active';
```

`write()` refuses to change scope-bearing fields on an active plan — the answer
is a revision, not an edit. An inspection copies `itp_revision` at creation, so
revising the plan in 2027 cannot retroactively change what a 2026 inspection was
measured against. This is the same rule M5 applies to documents, and the test
`test_revising_an_itp_does_not_rewrite_past_inspections` is what holds it.

## Hold, witness, review and surveillance points

`inspection_point` on the ITP item carries the industry meaning:

| Point | Meaning | Work release |
|---|---|---|
| `hold` | Work **cannot proceed** past it without release | `work_release_required = True` |
| `witness` | Notify; work may proceed if the witness does not attend | notified, not blocking |
| `review` | Document/record review only | not blocking |
| `surveillance` | Observation at the inspector's discretion | not blocking |

`work_release_required` is computed from the point type, not typed by hand.
`is_quality_released(wbs=, itp_item=, project=)` answers whether a passing
hold-point inspection exists. It returns a boolean fact. M7's certification will
consult it and decide; quality does not hold payment on its own authority,
because "quality blocks the certificate" is a contract decision, not a data one.

## Inspection requests

`realestate.construction.inspection.request` is the contractor's notice. It
computes `lead_time_hours` from `requested_datetime` to `required_datetime` and
flags `lead_time_shortfall` against the configured notice period.

Short notice is **recorded, not blocked**. A system that refuses to accept a
late request does not stop late requests; it stops them being recorded, and the
lead-time argument at the end of the job then has no evidence. The banner on the
form says exactly that.

One request can produce several inspections — the original and its
reinspections. `action_create_inspection()` copies the subject (ITP item,
document revision, submittal, product, PO, quantity) onto the inspection so the
inspection is legible on its own.

## Inspections

`realestate.construction.inspection` records what was found:

- The checklist is **instantiated by value** from the ITP item's template at
  `action_start()` — `_instantiate_checklist()` copies criteria, tolerances and
  mandatory flags. Editing the template afterwards does not alter a performed
  inspection.
- Six results: `accepted`, `accepted_with_comments`, `rejected`,
  `reinspection_required`, `not_applicable`, `cancelled`. Only the first two are
  in `PASSING_RESULTS`; `reinspection_required` is a failure that names its own
  remedy, and `not_applicable` records that the inspector attended and the check
  did not apply — which is not the same as passing it.
- Acceptance is refused while a mandatory checklist item is unanswered.
- **Every failed item must carry a disposition** before the inspection can be
  signed off. This is enforced in `action_record_result()`, not only as a line
  constraint: the line does not change when the inspection is signed off, so a
  line-level `@api.constrains` would never fire at the one moment that matters.
  The line constraint remains as a guard against later edits.
- Tolerance is computed, not asserted: `within_tolerance` comes from
  `measured_value` against `minimum_value` / `maximum_value`.
- Once a result exists, `write()` refuses to change the result, the checklist
  outcome or the recorded references. `action_record_result()` refuses a second
  result outright. The correction path is a reinspection.

`action_create_reinspection()` builds a child inspection with
`parent_inspection_id` and `reinspection_sequence = parent + 1`, carrying the
same ITP item and revision. The chain is the audit trail: first inspection
rejected, second accepted, both preserved.

Material inspection (`inspection_type = 'material'`) references
`product_id` / `purchase_order_id` / `picking_id` and records the quality verdict
against them. It **does not** create moves, change picking state, or write
`qty_done`. Inventory owns stock; quality owns the verdict.
`test_a_material_inspection_does_not_touch_inventory` asserts it.

## Checklist templates

`realestate.construction.checklist.template` is a reusable question set
(visual / measurement / test / document / dimensional) with acceptance criteria,
target, min/max, UoM and mandatory flag. Templates are library data. Inspections
never point at them at runtime — they copy from them, once.

## Quality observations

`realestate.construction.quality.observation` is the lightweight finding: a
punch-list item with a severity, an assignee and a due date, closed by
verification (`open → action_required → ready_for_verification → closed`),
overdue computed from `due_date`.

An observation is deliberately *not* an NCR. `action_escalate_to_ncr(reason)`
exists for the case where a finding turns out to be a genuine non-conformance;
it links `ncr_id` and leaves the observation's own history intact. Escalation is
a human decision — severity does not auto-promote, because a system that raises
NCRs by itself teaches people to stop raising them.

## Non-conformance reports

`realestate.construction.ncr` is the formal record. Lifecycle:

```
draft → open → under_investigation → disposition_proposed
      → corrective_action → ready_for_verification → verified → closed
                                                   ↘ reopened → …
```

Enforced rules:

- **Root cause before disposition.** `action_propose_disposition()` refuses
  without `root_cause_category`: "a disposition without a root cause fixes this
  one and nothing else."
- **`use_as_is` and `concession` need authority.** `_check_disposition_authority()`
  requires the construction manager group, and for a `critical` NCR refuses
  approval by the person who raised it unless self-approval is explicitly
  configured. Permanently accepting non-conforming work is a decision with a
  name attached to it.
- **Verification is not self-service.** `action_verify()` refuses when the
  verifier is the assignee responsible for the corrective action.
- **Closed evidence is immutable.** `write()` blocks edits to description, root
  cause, corrective action, disposition, requirement violated, severity and
  discovery date once verified or closed. The path back is
  `action_reopen(reason)`, which records who reopened it and why.
- `action_create_reinspection()` raises the verification inspection and links it
  as `reinspection_id`, so "we verified it" is backed by an inspection record.

### Cost exposure — the line that is not crossed

`estimated_rework_cost` is quality **exposure**, with `cost_impact` in
`potential / managed / none / unknown`. It is not actual cost, not commitment,
and not budget. `quality_exposure(project)` returns:

```python
{'potential_rework_cost': ..., 'open_ncr_count': ...}
```

for M3 to *consider*. Nothing consumes it. An estimate that inserted itself into
an approved ETC would be a quality record silently moving the forecast, and the
forecast would stop being something anyone signed. When
`action_create_change_event()` hands the commercial consequence to M4, the NCR's
`cost_impact` moves `potential → managed` — the exposure is now tracked where
commercial change belongs, and is no longer double-counted as loose exposure.

## Daily site reports

`realestate.construction.daily.report` is one record per project, date and shift
(SQL unique constraint), holding weather, site conditions, work performed,
manpower, equipment, deliveries, delays, visitors, and quality/safety notes.

- **Manpower is not re-entered.** `labor_log_ids` are the existing
  `realestate.construction.labor.log` records, extended in M6 with `wbs_id`,
  `cost_code_id`, `location`, `shift` and `daily_report_id`. There is no
  competing manpower table; totals are computed from the logs.
- **Closure freezes the record.** `action_close()` writes
  `labor_summary_snapshot` — trade, contractor, headcount, hours, as they stood
  at closure — so later edits to a labour log cannot silently rewrite what the
  site report said on the day. `write()` then blocks edits to closed content.
- **Amendment is explicit.** `action_amend(reason)` requires a reason and records
  it on the report. A daily report is the evidence a delay claim is argued from;
  it is amended in the open or not at all.
- **Recorded production is not certification.** Work lines carry quantity, UoM
  and an optional BOQ line reference. They write nothing to the BOQ, to progress,
  or to a payment certificate.
  `test_daily_production_is_not_certification` asserts the BOQ line is untouched.
- **A delay is evidence, not entitlement.**
  `daily.delay.action_create_change_event()` exists so a delay *someone decides*
  is commercially significant reaches M4 as a change event. Nothing is automatic,
  and extension of time remains M8's problem.

## Views, and the reason prompt

Six form/list pairs (ITP, inspection request, inspection, observation, NCR,
daily site report) plus checklist templates, under a **Quality & Site** menu.

Two details in the UI layer are load-bearing rather than cosmetic:

- **A form button cannot pass an argument.** `action_record_result(result)` takes
  one, so each outcome has its own named wrapper — `action_accept()`,
  `action_accept_with_comments()`, `action_reject_result()`,
  `action_require_reinspection()`, `action_not_applicable()` — and the button
  calls that. Wiring the button straight to the method with a context default
  would have called it with no result and raised a `TypeError` at the user.
- **Three actions refuse to run without a written reason** — amending a closed
  daily report, reopening a closed NCR, escalating an observation. Those methods
  take the reason as an argument, so `realestate.construction.reason.wizard`
  (transient) asks for it and dispatches. The alternative — a button that calls
  the method with `None` — turns a question into an error message.

`test_each_inspection_result_has_its_own_button`,
`test_a_reason_prompt_carries_the_reason_to_the_record` and
`test_amending_a_closed_report_through_the_prompt` cover both.

## Integration summary

| Boundary | Direction | Rule held |
|---|---|---|
| M1 WBS / cost codes | quality → reads | Quality records locate themselves in the same tree; they post nothing. |
| M2 commitment / actual | none | No quality model writes cost. |
| M3 forecast | read-only advisory | `quality_exposure()` is offered; never consumed. |
| M4 change events | manual creation | NCR and delay create change **events** with `source_model` / `source_id`. Never change orders. |
| M5 documents / submittals | reference by revision | ITPs and inspections point at a *revision*, never a document. |
| Procurement / Inventory | read-only | Material inspections reference PO/picking; they never move stock. |
| M7 certification | fact provider | `is_quality_released()` — a fact, not a permission. |

## Security

Twenty-eight ACL rows added: user (create/read/write, no unlink) and manager
(full) on every M6 model. Unlink stays with managers on all quality records —
inspection results and NCRs are evidence. Company scoping is enforced by
`_check_company_auto` plus an explicit `@api.constrains('project_id',
'company_id')` on each root model, matching M1–M5.

## Performance

Indexed: `project_id`, `state`, `discipline` on the root models;
`(project_id, report_date, shift)` unique on daily reports;
`construction_itp_one_active` partial unique on ITPs. `quality_exposure()` and
the compute methods use `_read_group` rather than record loops.

## Migration

M6 adds tables only; the five columns added to `realestate.construction.labor.log`
are nullable with no default, so existing labour logs upgrade untouched and simply
have no daily report attached. **No historical ITPs, inspections or NCRs are
invented.** A project that had none before has none now; quality history starts
the day quality is used. Re-running the upgrade is idempotent — verified in the
release gate below by upgrading the freshly installed database in place.

## Browser verification

`static/tests/tours/quality_tour.js` + `tests/test_m6_browser.py` run a real
headless Chrome over the seeded records: ITP register → active plan with its
checkpoints and its *Create Revision* button; inspection register → a rejected
inspection with the checklist that was copied from the plan and a *Raise
Reinspection* button; NCR register → the record showing the quality-exposure
banner; daily report → the Manpower tab showing the labour log rather than a
second table. Every checkpoint asserts no error dialog is open and no `NaN` or
`[object Object]` reached the DOM.

An ORM test cannot see a form that raises on load, and a rule nobody can reach
through the UI enforces nothing. 27 steps, tour succeeded. `start_tour`
authenticates server-side — no credential is typed anywhere in the gate.

## Tests

`tests/test_m6_quality_invariants.py` (written first, against principles) and
`tests/test_m6_quality.py` cover:

- ITP lifecycle, revision creation, one-active-revision index, hold-point release
- Inspection lead time, checklist instantiation by value, tolerance evaluation,
  all six results and their form buttons, mandatory-answer refusal, undisposed-failure refusal,
  result immutability, reinspection chains
- Material inspection not touching Inventory
- Observation verification, overdue, escalation to NCR
- NCR root-cause-before-disposition, use-as-is authority, self-verification
  refusal, closed-evidence immutability, reopen with reason, quality exposure
- Daily report uniqueness per date/shift, labour from logs, snapshot at closure,
  amendment reason, production ≠ certification, delay → change event
- Cross-milestone: inspection failure creates no NCR, NCR creates no change
  order, quality exposure never reaches the forecast

Construction module total: **310 tests, 0 failed, 0 errors** (258 before M6),
including the browser gate.

---

# M7 — Payment Certificates, Retention, Advances, Owner Billing

## What Phase 0 found here, and what M7 did about it

| Finding | Severity | M7 |
|---|---|---|
| Retention posted as a negative expense line — a 100,000 certificate with 5% retention recorded **95,000 of cost** | P0 | Posted to a liability account; cost is the value of the work |
| Cumulative certification filled by an onchange — anything not going through the form skipped it | P0 | Read from the database inside `action_certify()`, under an advisory lock |
| Certified figures editable after certification, with the bill posted | P0 | `applied_amount` and `certified_amount` are separate; certified freezes |
| Certificates carried no cost dimension — the largest document on the job reached the project and no cost code | P1 | `cost_code_id` → `distribution_for()`, one comma-joined key |
| No advance document anywhere; owner-side "Mobilization Advance Recovery" was a free-typed number | P1 | `realestate.construction.advance` with an outstanding balance that recovery cannot exceed |
| `contractor.action_release_retention()` released everything, everywhere, and flipped a boolean | P1 | A release document per stage, checked against the register |
| `action_mark_paid()` created the payment | P1 | Reads `payment_state`; refuses if the ledger disagrees |
| BOQ quantity guard was dead code (`1000 > 100 - 0 + 1000`) | P1 | Checked at certification against the authorised quantity |
| An approved BOQ could be edited in place | P0 | Refused; `action_create_revision()` instead |
| Remaining-BOQ forecasting disabled because certified quantities were untrustworthy | P1 | Input fixed → method enabled |

## The accounting, in full

Everything below is an ordinary Odoo bill or invoice. Rule 1 holds: there is no
second ledger, and no model in this module computes a balance the ledger does
not already hold.

**Certificate** — 100,000 certified, 5% retention, 20,000 advance recovery:

```
Dr  Work / expense (analytic: project + cost code)   100,000
Cr  Retention payable                                  5,000
Cr  Advances to contractors                           20,000
Cr  Accounts payable                                  75,000
```

**Advance paid** — an asset, not a cost:

```
Dr  Advances to contractors    200,000
Cr  Accounts payable           200,000
```

**Retention released** — a liability settled, not a new expense:

```
Dr  Retention payable    5,000
Cr  Accounts payable     5,000
```

**Owner progress billing** with 10% retention withheld by the owner:

```
Dr  Accounts receivable          450,000
Dr  Owner retention receivable    50,000
Cr  Revenue                      500,000
```

Two properties are load-bearing:

- **Only the work lines carry an analytic distribution.** Retention and advance
  recovery lines carry none. That single fact is the difference between actual
  cost meaning "the value of the work" and meaning "whatever was left after
  deductions" — the defect this milestone exists to fix.
- **A missing control account refuses the posting.** `realestate.construction.accounts`
  raises rather than falling back to the expense account. A fallback here would
  reproduce the original defect silently, and a control balance in the wrong
  account is harder to find than one that was never written.

## Claim, certificate, disallowance

`applied_amount` is what the contractor claimed. `certified_amount` is what was
certified. `disallowed_amount` is the difference, and `disallowance_reason` is
required whenever it is non-zero.

Storing one number and overwriting it destroyed the only evidence of a
disallowance — which is the number the final account argues about. Certifying
more than was claimed is refused; the extra belongs on the next application.

Once certified, `write()` refuses the amounts, the retention percentage, the
period, the counterparty and the cost code. The correction path is the next
certificate, or a credit note in Accounting.

## The cumulative ceiling

`_check_within_authorised()` runs inside `action_certify()`:

1. Take `pg_advisory_xact_lock(package or project, contractor)`.
2. `_read_group` the certified amount of every other counted certificate on the
   same contract.
3. Refuse if the cumulative would exceed `authorised_amount`.

`authorised_amount` is the package's `current_contract_value` — original plus
approved variations — or the typed contract value. When neither exists,
`has_authorised_base` is **False** and no ceiling is applied. It is not a
ceiling of zero: a project with no contract base recorded would otherwise have
every certificate refused, and "unknown" is not "nothing".

## Retention as a register

`realestate.construction.retention` holds one row per movement — `hold` from a
certificate, `release` from a release document — each carrying the
`account.move` that made it true. The balance is the sum of `signed_amount`.
The register is append-only: `write()` refuses.

`side` separates contractor retention (a liability we owe back) from owner
retention (an asset somebody owes us). Same arithmetic, opposite money, one
table rather than two competing ones.

`realestate.construction.retention.release` names the stage — practical
completion, end of the defects liability period, partial — carries a reason,
checks the register under a lock, posts the bill and writes the movement. A
confirmed release cannot be cancelled behind the ledger's back.

`contractor.total_retention_held` now reads the register plus any pre-M7
certificates that were never posted correctly, instead of re-deriving from
percentages and zeroing on a boolean.

## Advances

`realestate.construction.advance` (again with a `side`) records the amount, the
recovery rate, the advance payment guarantee and its expiry.
`recovered_amount` counts only recoveries on **certified** certificates, so a
draft claim cannot make an advance look repaid.

`_check_recovery_within_advances()` runs at certification and refuses recovery
beyond what was advanced — including across certificates, by re-reading the
other recoveries rather than trusting a stored total. Recovering an advance
reduces what is paid; it never reduces what the work cost.

## BOQ: original quantity, authorised quantity

`quantity` is the contract. `authorised_quantity = quantity + Σ approved
variations`, and variations arrive only through
`boq_line.apply_variation(delta, change_order)`, which refuses a change order
that is not approved or implemented. The variation rows are immutable.

Certification checks the authorised quantity from the database, so the two
Phase 0 defects — certifying 1,000 against a BOQ of 100, and two drafts each
certifying the whole line — are both refused. `is_over_certified` exists
because `remaining_qty` floors at zero, which is how the original overrun
stayed invisible.

An approved BOQ refuses edits to quantity, rate and work item;
`action_create_revision()` copies it forward with `supersedes_id` /
`superseded_by_id`.

## Remaining-BOQ forecasting, re-enabled

M3 disabled this method with a written reason: forecasting from quantities that
could be exceeded and double-claimed would have put a known-wrong number into
an approved EAC. M7 fixed the input, so `DISABLED_METHODS` is now empty — the
mechanism stays for the next unreliable input.

The method values `max(0, authorised − certified) × rate` over approved BOQ
lines on the cost code. It refuses in two cases rather than answering badly:

- no BOQ line on the cost code has an authorised quantity;
- any line has no rate — a partial answer presented as a whole one is worse
  than no answer, because it looks complete.

`forecast_quantity` and `forecast_unit_rate` became readonly and are filled in
from the BOQ. A typed quantity under a field named "BOQ" is a manual estimate
wearing somebody else's authority.

## Owner billing

Brought to the same standard: `company_id`, a real `retention_pct` posting to
the owner retention receivable, and a movement in the same register with
`side = 'owner'`. Owner-side advances use the same advance model with
`side = 'owner'`, posting to the advances-received liability.

## The retention disclosure, retired for new work

M2 shipped `realestate.construction.retention.disclosure` to report the defect
rather than quietly patch it. M7 stamps `retention_posted_correctly` on every
certificate it posts, and the disclosure now searches only for certificates
without that stamp. New work stops being reported; pre-M7 history keeps being
reported, because those entries really are mis-posted and reversing them is a
Finance decision, not a module's.

## Migration

- New columns only. `company_id` on certificates, BOQs, BOQ lines, certificate
  lines and owner billing fills from the company default on upgrade.
- `applied_amount` / `certified_amount` compute from the legacy percentage and
  contract value, so a pre-M7 certificate keeps the gross amount it billed.
  `test_a_pre_m7_certificate_keeps_its_gross_amount` reproduces the upgrade by
  nulling both columns and recomputing.
- **The retention register is not back-filled.** Movements are evidence of
  postings; seeding rows for entries the ledger never saw would fabricate
  exactly the balance this milestone exists to make true.
  `test_a_pre_m7_certificate_is_still_disclosed_as_mis_posted` holds that line.
- No historical advances, releases or variations are invented.

## Security

Twelve ACL rows: the retention register is **read-only to users and
un-deletable by anyone** — it is the audit trail of a control account.
Releases, advances and recoveries follow the usual user/manager split. BOQ line
variations cannot be written by anyone, including managers; they are created by
`apply_variation()` and never edited.

## Browser verification

`static/tests/tours/certification_tour.js` + `tests/test_m7_browser.py` run
headless Chrome over a seeded certificate with a disallowance, retention and an
advance recovery: the certificate register and form (applied for, certified,
disallowed, retention, recovery all present), the retention register showing
the movement, the advance form with its "not project cost" banner and
outstanding balance, and the release register. 19 steps, tour succeeded.

## Tests

`tests/test_m7_certification_invariants.py` — ten invariants written before the
models: retention is a liability; nothing posts without an account; release
creates no cost; release cannot exceed held; an advance is an asset; recovery
cannot exceed the advance; the same work cannot be certified twice; a certified
amount is never rewritten; the claim survives the certificate; certified cost
reaches its cost code.

`tests/test_m7_certification.py` — the milestone in detail, plus the
remaining-BOQ forecast method and the upgrade path.

Six Phase 0 defect tests were **converted rather than deleted**, so the fix
keeps a test in the file that documented the bug:

| Was | Now |
|---|---|
| `test_defect_a_certificate_may_exceed_the_boq_quantity` | `test_certifying_beyond_the_boq_quantity_is_refused` |
| `test_defect_two_certificates_certify_the_same_quantity_twice` | `test_two_certificates_cannot_certify_the_same_quantity` |
| `test_defect_an_approved_boq_can_be_edited_in_place` | `test_an_approved_boq_is_revised_rather_than_edited` |
| `test_defect_retention_reduces_the_expense_instead_of_holding_a_liability` | `test_retention_is_held_as_a_liability` |
| `test_defect_retention_outstanding_is_a_number_on_the_contractor_only` | `test_retention_outstanding_is_a_register_scoped_to_a_project` |
| `test_defect_a_certificate_can_bill_a_vendor_of_another_company` | `test_a_certificate_cannot_bill_a_vendor_of_another_company` |
| `test_defect_no_construction_model_has_a_company` | split: `test_the_money_bearing_models_now_carry_a_company` + the still-unfixed remainder, kept counted |
| `test_the_boq_method_is_unavailable_while_its_data_is_untrustworthy` (M3) | `test_the_boq_method_answers_only_from_a_priced_authorised_boq` |

Construction module total: **370 tests, 0 failed, 0 errors** (310 before M7),
including the browser gate.

## Release gate

Fourteen modules, fresh install and then an in-place upgrade of the same
database, all tests enabled:

| Phase | Result | Construction | Frozen baselines |
|---|---|---|---|
| Fresh install | RC=0 — **1906 tests, 0 failed, 0 errors** | 370 | 299 / 352 / 325 / 247 / 291 / 15 / 7 |
| Upgrade in place | RC=0 — **1906 tests, 0 failed, 0 errors** | 370 | 299 / 352 / 325 / 247 / 291 / 15 / 7 |

Identical counts in both phases: the upgrade is idempotent, and every frozen
module — `atmta_real_estate`, brokerage, checks, developer, maquette, plan,
portal — is untouched at its frozen count.

## Deferred, and why

| Item | Why not now |
|---|---|
| Materials on site, and their retention treatment | A separate valuation rule; M7 kept to work certified |
| Price adjustment / escalation clauses | Contract-specific formulas; needs its own milestone |
| Retention guarantee (bond in lieu of cash retention) | Changes the instrument, not the register; M8 with the claims work |
| Reversing pre-M7 retention postings | A Finance reclassification, not a module's decision. Still disclosed |
| Certificate PDF / IPC cover sheet | M9 with the registers |

---

# M8 — Claims, EOT, Delay Register, Risk and Issues

> **ATMTA manages claims administration and evidence. ATMTA does not determine
> legal entitlement.** No rule in this milestone concludes that a party is
> liable, that a delay is excusable, that a notice was fatal, or that float
> belongs to anybody. Those are questions of contract, governing law, fact and
> professional judgement, and every one of them is left to a human with a name
> attached to the decision. What the software does is keep the dates, the
> notices, the evidence, the amounts and the audit history straight.

## M8.0 — Audit before building

Searched the whole suite for anything already representing claims, EOT, delay,
notice, prolongation, liquidated damages, risk, issue, incident or completion
dates.

| Concept | Found | Decision |
|---|---|---|
| Claim / EOT / prolongation / LD / time-impact | **nothing** | Built new |
| Risk | **nothing** | Built new |
| Issue | `realestate.snagging.issue` (handover snagging), `realestate.visual.validation.issue` (M8 visual asset check) | Neither is a project issue register. Built new, named distinctly |
| Delay | `realestate.construction.daily.delay` (M6 daily-report line) | **Reused.** A daily delay line now links to a delay event rather than a second delay table existing beside it |
| Notice | nothing contractual | Built new; issuing runs through M5 transmittals |
| Completion dates | `package.original_completion_date` / `current_completion_date` (M2), `project.expected_completion_date` / `actual_completion_date` (frozen `real_estate_developer`) | Extended the package; the project's planning dates were left exactly as they are |
| Contemporary records | M6 daily reports, labour, equipment; M5 documents, transmittals, RFIs; M6 inspections, NCRs | **Referenced, never copied** |

Nothing was duplicated. One M4 selection gained two values (`issue`,
`claim_determination`) so a change event can name where it came from.

## Completion date architecture

The package already snapshotted `original_completion_date` at award. M8 made
the rest explicit:

| Field | Meaning |
|---|---|
| `original_completion_date` | What the parties agreed. Read-only, snapshotted at award, **never moves** |
| `approved_eot_days` | Sum of **implemented** extensions. Determined-but-unimplemented days are excluded |
| `other_time_adjustment_days` (+ reason) | A separately authorised adjustment that is not an EOT |
| `current_completion_date` | Computed: original + approved EOT + other adjustment |
| `claimed_eot_days` | Days asked for and not granted — reported beside the contract date, never inside it |
| `substantial_completion_date`, `actual_completion_date` | Taking-over and actual, where the contract uses them |

Before award there is no contract date to extend, so `current_completion_date`
holds whatever planning date was typed. `project.expected_completion_date` in
the frozen developer module was **not** migrated or reinterpreted: a planning
date is not a contractual one, and guessing which a legacy value meant is
exactly what M8AU forbids.

ATMTA does not schedule. `schedule_activity_ref`, `schedule_reference` and the
analysis-method fields exist so a P6 or MS Project analysis can be pointed at;
no CPM engine, and no forensic delay algorithm, was built.

## Delay events

`realestate.construction.delay.event` is the factual register: what happened,
when, where, who noticed, what it may have cost and how many days it may have
taken.

Two design points carry the whole rule:

- **The event type is a description, not a liability.** `weather`,
  `contractor_delay` and `employer_delay` say what occurred. `cause_category`
  defaults to `undetermined` and `action_assess()` refuses to leave it there —
  somebody has to decide, and the record shows that they did.
- **A delay may run without an end date.** `is_ongoing` is the normal state for
  weeks. Requiring an end date would invent a fact.

One event collects many days of evidence: `daily.delay` lines from M6 point at
it, so a four-week access restriction is one delay with twenty-eight
contemporaneous records — not twenty-eight delays.
`daily.delay.action_create_delay_event()` raises the register entry and stops:
it issues no notice, opens no claim and grants no day.

An event a claim relies on cannot be voided.

## Notice management

`realestate.construction.notice` computes the deadline **the contract
configured** and says whether the notice beat it.

Notice periods live on the package: `notice_required`, `notice_period_days`,
`notice_day_basis` (calendar or business), `notice_reminder_days`,
`detailed_claim_period_days`. Nothing anywhere hard-codes 28 days, 42 days, or
any standard form's period.

`has_deadline` is separate from `deadline_date` on purpose. A contract with no
recorded period has **no deadline** — not a deadline of today — and every
consumer reads the flag rather than treating an empty date as "overdue".

Statuses: `not_required`, `pending`, `due_soon`, `issued`, `late`, `overdue`,
`acknowledged`, `disputed`. When a notice is late the record says:

> Notice issued N day(s) after the deadline configured on this contract
> (date). **The contractual consequence, if any, is a matter for the contract,
> the governing law and the determination.**

It does not say "no entitlement", it does not reject the claim, and
`test_d_a_late_notice_is_flagged_not_judged` pins that the claim still
submits. An issued notice cannot be edited — when a notice actually went out is
the fact a time-bar argument turns on.

Issuing runs through M5 transmittals and document revisions. No second
correspondence engine was built.

## Claim architecture

`realestate.construction.claim` handles both directions (`side` =
contractor / employer) across sixteen commercial types, with the lifecycle
draft → notice → preparing → submitted → under review → assessed →
determination pending → determined → settlement → closed, plus rejected,
withdrawn, disputed and cancelled.

**Four money figures and four time figures, never overwritten:**

| | Cost | Days |
|---|---|---|
| Estimated exposure | `estimated_exposure` | — |
| Claimed | `claimed_cost` | `claimed_days` |
| Assessed (internal) | `assessed_cost` | `assessed_days` |
| Determined | `determined_cost` | `determined_days` |
| Settled | `settled_cost` | `settled_days` |

A claim of 5M assessed at 3.8M, determined at 3.5M and settled at 3.7M keeps
all four. Once determined, `write()` refuses the claimed figures.

**Unknown is not zero.** `cost_claimed_known` distinguishes "no cost claimed"
from "a claimed cost of zero" — M3's rule, applied here. A time-only claim with
no money is entirely ordinary and is not rejected for it; a claim asking for
neither time nor money is refused.

## Claim revisions

`realestate.construction.claim.submission` holds each revision: number, date,
claimed cost, claimed days, narrative, document, whether it is the final
particulars. Issuing revision 1 supersedes revision 0 and **revision 0 still
says what it said** — an issued submission cannot be rewritten. The claim's
current figures follow the latest issued revision.

An ongoing event can be updated monthly without waiting for it to end.

## Claim quantum

`realestate.construction.claim.cost.line` — category, description, WBS, cost
code, period, quantity, UoM, rate, amount, and separately claimed / assessed /
determined amounts. Tax-exclusive, like every control figure in this module.

No entitlement formula is implemented. Prolongation is not "EOT days × a daily
rate": time entitlement and money entitlement are separate questions, and a
line that computed one from the other would be asserting a contractual position
the software has no business holding.

Where quantum cites posted cost, `uses_actual_cost` records it — and actual
cost remains evidence of spend, not proof of recoverability. Citing it changes
nothing: `test_actual_cost_may_be_cited_but_is_not_entitlement`.

## Contemporary records

`realestate.construction.claim.evidence` points at records; it never copies
them. A `Reference` field spans daily reports, labour logs, equipment records,
RFIs, submittals, document revisions, transmittals, inspections, NCRs, change
events, certificates, delay events, notices, journal entries and analytic
lines.

At the moment of citing, the evidence line stamps `reference`, `revision_label`
and `record_date`. That is M5's exact-revision architecture reused: a claim
that cited Drawing Rev B must still say Rev B after Rev D is issued, or the
bundle stops matching the argument built on it. Evidence lines cannot be
re-pointed.

`chronology_html` builds the timeline from the records themselves — delay start
and end, each daily report that recorded it, notices, RFIs, NCRs, change
events, submissions, determinations, EOTs. Nothing in it is authored.

## Determination

`realestate.construction.claim.determination` — authority, role as the contract
names it, date, determined cost, determined days, reasons, conditions,
document.

- Reasons are required. A determination without them is an instruction.
- **Partial determination is the normal case**: 5M / 60 days determined at
  3M / 30 days is neither approval nor rejection, and no boolean is forced.
- An issued determination is **superseded, not edited**; the superseded record
  keeps its figures and the chain shows the change.
- The person who submitted a claim cannot determine it unless
  `allow_self_determination` is explicitly configured.

Dispute records the disputed amount, days, reason and next action. No
adjudication, arbitration or litigation management was built.

## EOT

`realestate.construction.eot` carries claimed / assessed / determined days, the
authority, the analysis method and the reason, plus snapshots of
`original_completion_date`, `completion_date_before` and
`completion_date_after`.

Determining grants days. **Implementing** moves the current completion date —
under an advisory lock, idempotently, with a chatter entry on the package
naming both dates and confirming the original is unchanged.

```
Original 2027-12-31 → EOT-001 +30 → current 2028-01-30
                    → EOT-002 +15 → current 2028-02-14
Original remains 2027-12-31.
```

Corrections are records, not edits: `action_create_correction(-10)` produces a
correcting extension that may carry negative days, while the original still
reads 30. Negative days on a non-correction record are refused.

## Risk register

`realestate.construction.risk` — category, cause, consequence, owner, trigger,
review date, probability and separate cost / schedule / quality impact scores.

- `overall_impact` is the **worst** dimension, not an average: a risk that is
  catastrophic for time and trivial for cost is a severe risk.
- `inherent_score` is probability × impact, documented as a sortable
  convention rather than a quantification, with `management_rating` available
  to override the arithmetic when it misleads.
- The scale maximum is configurable (`risk_scale_maximum`, default 5). No
  company's risk matrix is baked in.
- Cost exposure has min / likely / max, plus schedule exposure in days.

**A risk is not in the forecast until a cost controller puts it there.**
`include_in_forecast` is an explicit act by a manager, chattered with the
amount. `risk_identified` and `risk_only` are reported separately precisely so
nobody mistakes "identified" for "carried".

Mitigation actions are child records with owner, due date, state and
**effectiveness recorded after the fact** — a mitigation nobody reviewed is a
mitigation nobody knows worked. A mitigate-or-avoid strategy with no plan and
no actions is refused.

## Risk materialisation

`action_materialise()` creates an issue, links it both ways, marks the risk
`materialised` and writes `materialised_snapshot` — a JSON record of the state,
probability, impact, score, strategy, mitigation plan, open actions and
exposure **as they stood when it came true**, plus the reason given.

The risk is never deleted or converted. That snapshot is the only honest answer
to "did we see this coming, and what were we doing about it?", and a conversion
would destroy it. A materialised risk cannot be closed — the issue it became is
what closes.

## Issue register

`realestate.construction.issue` — the active problem: design coordination, late
authority approval, utility obstruction, access, supplier failure, commercial
blockers, schedule constraints, site interfaces. Open → actioning → blocked →
ready to close → closed.

Quality non-conformance keeps its own specialised process: an issue may
reference an NCR and never replaces one.

**An issue closes on a resolution, not on a date.** A due date passing makes it
overdue; `action_close()` refuses without a recorded resolution. Escalation
uses Odoo activities rather than a second workflow engine.

## Change integration

Neither a risk, an issue nor a claim moves money.

- `issue.action_create_change_event()` → M4 change event, `source = issue`.
- `claim.action_create_change_order()` → an M4 change order **in draft**. A
  determination is an entitlement decision; moving the budget is an
  authorisation decision, and different people make them at different moments.
  Nothing auto-implements.
- `eot.action_implement()` is the single controlled time-baseline effect.

`test_a_determination_creates_a_draft_change_order_only` holds the whole rule:
budget and commitment are unchanged after the claim is determined and the order
created, and move only once the order is approved and implemented through M4.

## No double counting — and a real defect it found

One problem travels under five names. Reported as a sum, a 2M event becomes 8M.

`realestate.construction.exposure` reports by layer and walks the ladder from
the top: a claim represents its change events, a change event represents the
issue that produced it, an issue represents the risk that became it. Each
underlying event is counted **once, at the furthest stage it has reached**, and
authorised change is excluded entirely — it is baseline, not exposure. `explain()`
returns a sentence that says so on the face of any dashboard.

Building this surfaced a genuine defect in the M2 commitment engine:

> A package with **no purchase order** contributed `current_contract_value` —
> which already includes approved variations — while
> `approved_change_by_cost_code()` counted those same variations again, because
> no order had absorbed them. A 13,000,000 obligation reported as **16,000,000**.

Fixed: an orderless package now contributes its **original** contract value, and
variations arrive through the change path only.
`TestPackageVariationIsCountedOnce` proves both halves, and the
package-with-orders precedence rule is unchanged.

## Forecast integration

`include_in_forecast` is a decision, not a computation, and approved forecasts
stay immutable — a claim determined next month cannot rewrite last month's
approved EAC, because M3 already froze it.

## Certificate integration

A pending claim never appears as certified payable. Determined money reaches
M7 the long way round: claim → change order → M4 approval → the certificate
that certifies authorised work. The link exists; the shortcut does not.

## Security

A new group, **Commercial / Claims**, sits between user and manager. Claims,
claim submissions, quantum, evidence, determinations and EOT are readable only
by it — a site engineer records the facts and never sees the strategy.

Field-level restrictions are enforced **server-side** via `groups=` on
`assessed_cost`, `assessed_days`, `contractual_basis` and `internal_position`.
Hiding a field in a view leaves it readable through a relation, which is
exactly how a negotiating position leaks.

`test_a_site_engineer_cannot_read_claims` and
`test_a_site_engineer_still_records_the_facts` hold both halves.

## Multi-company

Every M8 model carries `company_id`, `_check_company_auto` and `check_company`
on its relations, plus an explicit constraint that the record's company matches
its project's. An EOT cannot extend another project's package; a claim cannot
be created against another company's project.

## Performance

Indexed `project_id`, `state`, dates, `package_id` and severity/priority across
the registers. Exposure and KPI services use `search` + `filtered` over bounded
per-project sets and `_read_group` where aggregation is the whole answer.
Evidence is relational — no daily report content is ever copied into a claim.

## Migration

M8 adds tables and columns only. **No historical claims, EOT records, risks or
issues are manufactured** from notes or comments, and no approved EOT history is
invented to explain a completion date that changed before this milestone
existed. Contract dates are classified rather than reinterpreted:

- deterministic original completion — the package snapshot taken at award;
- current-only legacy date — kept as the current date, with no fabricated
  extension history behind it;
- missing — left missing, which is why `action_implement()` refuses to extend a
  package that has no original completion date.

## Browser verification

`static/tests/tours/claims_tour.js` + `tests/test_m8_browser.py` run headless
Chrome across the delay register and event form (with its "grants no
extension" banner), the late notice and its warning (asserting the text says
*matter for the contract*), the claim register and form showing claimed and
determined side by side, two surviving submission revisions, the generated
chronology, the EOT form with original and current dates, the materialised risk
with its "kept as the record" banner, and the issue register. Every checkpoint
asserts no error dialog and no `NaN`/`[object Object]` in the DOM. Tour
succeeded.

## Tests

`tests/test_m8_claims_invariants.py` — the five invariants, written first:

| | |
|---|---|
| A | A claim moves no money — budget, commitment and actual all unchanged |
| B | Claimed days do not move the completion date |
| C | Only an **implemented** EOT moves the current date; original never moves |
| D | A late notice is flagged, never judged — the claim still submits |
| E | A materialised risk is preserved, the issue is linked, and the exposure is counted once |

`tests/test_m8_claims.py` — delay events, notices (calendar and business days,
no-period, not-required, late, acknowledged, disputed), the full claim walk,
cost-only / time-only / neither, revisions, quantum, evidence and revision
pinning, partial determination, supersession, settlement, dispute, EOT
(single, multiple, correction, idempotency), risk scoring and materialisation,
issues, the exposure ladder, claim → change, governance and security, and
multi-company isolation.

Construction module total: **449 tests, 0 failed, 0 errors** (370 before M8),
including two browser gates.

## Legal / contract boundary

Stated plainly, because it is the most important sentence in this milestone:

- ATMTA records **facts** — what happened, when, who was told, what was sent.
- ATMTA records **requests** — what is claimed, in money and in time.
- ATMTA records **decisions** — what was assessed, determined and settled, by
  whom, with reasons.
- ATMTA **never decides** whether an event is excusable or compensable, whether
  a notice was fatal, who owns float, whether delays were concurrent, or what a
  clause means.

Nothing in the code interprets FIDIC, NEC or any other standard form. Every
period, every threshold and every authority is configuration, because they are
properties of a particular contract and not of construction.

## Release gate

Fourteen modules, fresh install then an in-place upgrade of the same database,
all tests enabled:

| Phase | Result | Construction | Frozen baselines |
|---|---|---|---|
| Fresh install | RC=0 — **1985 tests, 0 failed, 0 errors** | 449 | 299 / 352 / 325 / 247 / 291 / 15 / 7 |
| Upgrade in place | RC=0 — **1985 tests, 0 failed, 0 errors** | 449 | 299 / 352 / 325 / 247 / 291 / 15 / 7 |

Identical counts in both phases — the upgrade is idempotent — and every frozen
module sits exactly on its frozen count. No ERROR or CRITICAL lines in either
phase.

## Deferred, and why

| Item | Why not now |
|---|---|
| Liquidated damages calculation | M8 records the contractual rate, cap and any determined deduction as tracking only. Calculating an entitlement to deduct is a determination |
| Adjudication / arbitration / litigation case management | Explicitly out of scope; the dispute fields are the extension hook |
| CPM scheduling, forensic delay analysis | Out of scope. Methods and conclusions are stored; analysis happens in a scheduling tool |
| Global claims dashboard | M9 owns the integrated dashboard; M8 ships the KPI service and drill-through registers |
| Head-office overhead formulas (Hudson, Emden, Eichleay) | Formula choice is a contractual and legal argument, not a default |

---

# M9 — Control Tower, Integrated Cost Sheet, Reporting and Security Hardening

M9 adds **no financial truth**. Every figure it shows already had an owner
before this milestone, and the whole design problem was to keep it that way: a
reporting layer that recomputes a number becomes a sixth opinion, and a sixth
opinion is how two screens disagree about the same project in front of the same
client.

## M9.0 — Audit of the reporting surfaces

| Finding | What it was | What M9 did |
|---|---|---|
| Legacy dashboard aggregated **globally** — every project, every company — with `search()` + Python `sum()` | `realestate.construction.dashboard.get_data()` | Left in place as the legacy operational dashboard; the Control Tower is the project-scoped, service-backed surface, and the menu now leads with it |
| Legacy "total budget" was the sum of milestone `budget_amount`; "total actual" the sum of `cost.line.amount` | Pre-M1 concepts that contradict M2's authoritative budget and ledger actual | The tower reads `project_totals()` only |
| 30 `sudo()` calls across the module | Mostly analytic plan creation, config parameters and `_read_group` on analytic lines | Documented as the accounting boundary (below) and pinned by tests |
| **Zero record rules** | Phase 0's finding, still open at M8 | Closed: 55 global company rules and 39 project rules |

## The Control Tower

`realestate.construction.control.tower.payload(project)` returns **one**
aggregated payload — not one RPC per KPI, and never "load all records and
aggregate in JavaScript". Sections: header, health, cost, cost sheet, forecast,
change, progress, commercial, certificates, quality, information, claims, risk,
procurement, exceptions.

Each section is built inside its **own savepoint**. A panel may fail without
taking the tower down, and it is reported as `failed` with the error — never as
a zero. A financial figure that fails silently into 0.00 is the single most
dangerous thing a dashboard can do.

### Role-sensitive by payload, not by template

`sections_for_user()` decides what is built. A panel a user may not read is
**absent from the JSON**, not hidden in the DOM — a restricted field that
reaches the browser has already leaked.

| Role | Sections |
|---|---|
| Construction User / Site | header, health, progress, quality, information, risk, exceptions |
| Cost Control / QS | + cost, forecast, cost sheet |
| Commercial / Claims | + change, commercial, claims, certificates |
| Manager | everything, plus procurement signals |

Health itself only consults dimensions the user may read, so a site engineer's
status is built from quality, information and risk rather than from a cost
figure smuggled in through a summary.

## The cost sheet

One row per cost code, plus one for everything that carries none. Columns:
original / approved-change / current budget, pending budget change, original /
approved-change / current commitment, committed remaining (with its *known*
flag), certified, actual, ETC, EAC, forecast variance, previous EAC, EAC
movement, method, status and reason, and potential change exposure.

**The four equations are tested on every row:**

```
Current Budget      = Original Budget + Approved Budget Changes
Current Commitment  = Original Commitment + Approved Commitment Changes
EAC                 = Actual + ETC
Forecast Variance   = Current Budget − EAC
```

Parent totals are aggregated from the rows a reader can open, and
`test_the_rows_add_up_to_the_total` asserts it — nothing separately computed is
persisted.

**Unassigned is not hidden.** Commitment and actual cost with no cost code get
their own row and their own totals, plus a data-control exception with a
drilldown, because dropping them would make the sheet tidy and wrong.

**Unknown is not zero.** A cost code nobody forecast has `etc = None` and
`has_etc = False`; the client renders `N/A` with the server's own reason as a
tooltip. Coverage is measured against the cost codes actually in play rather
than against the forecast's own lines — M3 refuses to approve an incomplete
forecast, so counting only its lines would report 100% coverage on a project
that has since committed money to a code the forecast never saw.

### Drilldowns

Every column opens the records behind it, and the **domain is built in Python
beside the aggregate**. `test_actual_cost_drilldown_equals_the_figure`,
`..._commitment_...` and `..._budget_...` re-run each drilldown and assert the
sum equals the displayed figure. The actual-cost drilldown reuses the same
`UNTAXED` filter and plan columns as `actual_by_cost_code()`; a drilldown with
a different filter from the total it opened is worse than none.

## Project health

Status is `on_track` / `attention` / `at_risk` / `critical` / `no_data`, per
dimension, each with a sentence:

> **AT RISK** — EAC exceeds current budget by 7.2%; 4 high risk(s), 2 overdue
> issue(s); forecast is 41 days old (threshold 35).

There is **no composite score**. Nobody can act on 68 out of 100, and a single
number hides which dimension is in trouble. `test_no_composite_score_is_invented`
pins it.

`no_data` ranks **above** `on_track` in severity. Not knowing is not the same as
being fine, and a green light over an empty project is the most expensive kind
of reassurance.

Every threshold is an `ir.config_parameter`: variance percentages, forecast
staleness, coverage minimum, RFI and NCR counts, change SLA. Tolerance for
overrun is a management policy, not a property of construction.

## Pending is never approved

The tower reports these in their own panel, visually dashed, never folded into a
baseline:

| Reported apart | Meaning |
|---|---|
| Potential cost exposure | Open change events |
| Approved, not implemented | Authorised, baseline does not reflect it yet |
| Claimed | Requested |
| Determined | Decided, not yet authorised |
| Implemented | Reached the baseline through M4 |
| Claimed EOT days | Beside approved EOT days, never inside the contract date |

`TestIntegratedPosition` pins the M9AJ project end to end — budget 105M,
commitment 90M, actual 40M, ETC 55M, EAC 95M, variance +10M, potential change
8M, claimed 5M, determined 3M — and asserts the specific wrong answers
(111M, 113M, 103M, 98M) appear nowhere.

## Progress — four questions, four answers

| | Basis |
|---|---|
| Planned | **N/A**, with a stated reason |
| Physical | Milestone weight × completion |
| Certified | Certified BOQ value ÷ authorised BOQ value |
| Financial | Posted actual ÷ current budget |

Planned progress is deliberately absent. ATMTA holds no schedule baseline, and
interpolating a straight line between a start and an end date would invent the
very number every variance is then measured against. The S-curve waits for the
same reason.

## Data-control exceptions

Twelve deterministic checks in six classes — financial, forecast, commercial,
quality, document, configuration. Each carries a count, a sentence and an
action, and the count and its drilldown are the same query. There is no generic
"8 warnings" badge: a warning nobody can open is decoration.

Examples: uncoded purchase lines, unassigned actual, missing ETC, stale
forecast, no baselined budget, approved-but-unimplemented changes, awarded
packages with no order, packages with no completion baseline, missing control
accounts, BOQ lines with no cost code, overdue NCRs, unclassified documents.

## Security hardening

**Record rules — the last Phase 0 defect, now closed.**

- **55 global company rules.** Multi-company isolation is deliberately *global*
  (no group): a manager of company A has no business reading company B's cost
  sheet either, so it must not be a privilege anybody can be granted past.
- **39 project rules, opt-in per project.** A project with no
  `construction_member_ids` behaves exactly as before — visible to everyone in
  the company. Name one member and it becomes restricted to its team. Enabling
  access control is therefore a decision somebody makes per project, not a
  migration that locks a deployment out on upgrade day. Managers hold an
  unrestricted rule that ORs with the member rule.

**Role matrix.** Four groups with a real hierarchy rather than a dozen
overlapping ones: User → Cost Control / QS and Commercial / Claims → Manager
(which implies both).

**The audit that keeps it honest.** `test_every_model_has_a_deliberate_security
_decision` requires every construction model to appear in `COMPANY_SCOPED`,
`GLOBAL_BY_DESIGN` (with a stated reason) or `LEGACY_WITHOUT_COMPANY`. A new
model added in M10 fails the suite until somebody classifies it. The point is
not that everything needs a company — a work-item catalogue is legitimately
global — but that every omission is a decision.

## Accounting access boundary

Deliberate, and tested both ways:

- **Aggregated project actual is a construction control figure** and is
  available to a cost controller with no accounting rights. It comes from
  analytic lines read with `sudo()` inside `actual_by_cost_code()`.
- **The drilldown is Odoo's question, not ours.** It returns a normal
  `account.analytic.line` action with no elevation, so a user without
  accounting rights gets an `AccessError` when they open it, and an accounting
  user gets the lines.

`test_the_aggregate_is_available_without_accounting_rights` and
`test_an_accounting_user_can_drill` pin both halves. No `sudo()` leaks a
journal entry to a user who may not read one.

## Claim privacy through the tower

`test_a_restricted_payload_does_not_carry_claim_data` builds a payload as a
site engineer and asserts the claim figures are **not in the JSON at all** —
not merely unrendered. `assessed_cost`, `assessed_days`, `internal_position`
and `contractual_basis` remain restricted server-side by `groups=`.

## Multi-company

`test_the_tower_never_reaches_another_company` builds a project in each of two
companies and asserts the payload contains only the active company's figures
and never the other project's id.

## Performance

`test_the_payload_stays_within_a_query_budget` runs the whole payload and fails
if it exceeds a query budget. It is a **budget, not an SLA**: the purpose is to
fail loudly when somebody introduces an N+1 into a panel, not to claim a
production number from a test fixture. `test_the_payload_does_not_grow_with
_record_count` adds forty records and asserts the aggregation still answers
from `search`/`_read_group` rather than per-record work.

## Frontend architecture

Pure Odoo Community: OWL, standard list/pivot/graph, no Spreadsheet, no
Dashboards app, no Enterprise component, no new dependency.

- One `orm.call` for the whole payload.
- **Skeleton while loading, never a flash of zeros** — zero is business
  information and a user may act on it.
- `money()` and `percent()` return `"N/A"` for `null`/`undefined` and a real
  number for `0`. Both are pure functions, and the HOOT suite tests exactly
  that distinction.
- Drilldown actions come from the server; the client just runs them.
- SCSS uses logical properties throughout (`padding-inline`,
  `min-inline-size`, `padding-inline-start`), so RTL flips once in rtlcss and
  never twice. Wide financial tables scroll inside their own region and no
  monetary column is truncated away.

## Browser verification

`static/tests/tours/control_tower_tour.js` + `tests/test_m9_browser.py`, in
headless Chrome: the tower mounts, the project is selected, freshness is
stated, health carries its reasons, the cost cards render, pending money is in
its own visibly-separate panel, the cost sheet renders rows, **an unforecast
cost code shows N/A rather than 0.00**, progress separates its four questions,
data-control exceptions are listed, and a drilldown from actual cost opens a
real list. Every checkpoint asserts no error dialog and no
`NaN`/`Infinity`/`undefined`/`[object Object]`. Tour succeeded.

The gate earned its keep three times over: it caught the tower defaulting to an
arbitrary project, an inline action missing `views` (which fails in
`_preprocessAction` rather than opening anything), and a drilldown that assumed
a recordset where RPC hands it an integer.

`test_an_empty_project_renders_without_breaking` asserts an empty project
produces `no_data` and no failed panel.

## Tests

`tests/test_m9_control_invariants.py` — the five invariants, written first:

| | |
|---|---|
| A | The integrated position, and the four equations that produce it |
| B | A potential change is reported apart from the baseline |
| C | A determined claim moves nothing until M4 implements it |
| D | A missing forecast is missing, not zero; coverage drops below 100% |
| E | One number, one calculation across controls, cost sheet, tower and project |

`tests/test_m9_control_tower.py` — cost sheet equations, row/total agreement,
unassigned rows, empty project, drilldown equality, the M9AJ position, change
implementation, certification in the sheet (retention does **not** shrink
actual), forecast freshness and its configurable threshold, health
transparency, data-control exceptions, report consistency, and the query
budget.

`tests/test_m9_security.py` — the security audit, the role matrix, project
membership, multi-company isolation and the accounting boundary.

Construction module total: **496 tests, 0 failed, 0 errors** (449 before M9),
including three browser gates and a HOOT suite.

## Release gate

Fourteen modules, fresh install then an in-place upgrade of the same database,
all tests enabled:

| Phase | Result | Construction | Frozen baselines |
|---|---|---|---|
| Fresh install | RC=0 — **2032 tests, 0 failed, 0 errors** | 496 | 299 / 352 / 325 / 247 / 291 / 15 / 7 |
| Upgrade in place | RC=0 — **2032 tests, 0 failed, 0 errors** | 496 | 299 / 352 / 325 / 247 / 291 / 15 / 7 |

Identical counts in both phases — the upgrade, including 94 new record rules,
is idempotent — and every frozen module sits exactly on its frozen count. No
ERROR or CRITICAL lines in either phase.

Construction is **not** frozen. M10 owns final migration, closeout and freeze.

## Glossary

| Term | Means, exactly |
|---|---|
| **Original Budget** | The baselined budget as approved. Never edited |
| **Approved Budget Change** | An M4 change order, `impact_side = budget`, **implemented** |
| **Current Budget** | Original + approved budget changes |
| **Original Commitment** | Purchase orders and awarded packages, before any variation |
| **Approved Commitment Change** | An implemented M4 commitment change not already absorbed by an amended order |
| **Current Commitment** | Original + approved commitment changes |
| **Actual** | Posted Odoo ledger cost, gross of retention, via analytic lines, tax-exclusive |
| **Certified** | M7 `certified_amount` on certificates in certified/invoiced/paid |
| **Invoiced** | A posted vendor bill exists |
| **Paid** | Odoo's `payment_state` says so. Construction reads it; it never decides it |
| **ETC** | Estimate to complete, from the **approved** forecast only. `None` when nobody produced one |
| **EAC** | Actual + ETC. `None` when there is no ETC |
| **Potential Change** | An open change event. Possible, not authorised |
| **Approved Change** | An approved change order. Authorised; moves the baseline only once implemented |
| **Claim** | A request for entitlement. Moves nothing |
| **Determined** | A decision on a claim. Still moves nothing until M4 implements it |
| **Retention** | Money withheld and owed back. A liability, never a reduction of cost |
| **Advance** | Money paid before the work. An asset, never cost |
| **Unassigned** | Real money that carries no cost code. Reported, never hidden |

---

# M10 — Enterprise Closeout, Migration, Hardening and Freeze

M10 added almost no business functionality. Its job was to **prove the
system** — and, where proving it exposed a defect, to fix that defect without
using it as an excuse to redesign anything.

## M10.0 — The M9 gate, confirmed

Fresh install and in-place upgrade of fourteen modules, both RC=0, **2032
tests, 0 failed, 0 errors**, frozen baselines exact, no ERROR or CRITICAL
lines. M10 proceeded on that basis.

## What the freeze gates found

Four genuine defects, each fixed at the smallest possible scope:

| Found by | Defect | Fix |
|---|---|---|
| Reconciliation gate | `cost_report.build_for()` raised `KeyError: 'previous_eac'` on **any project with uncoded commitment** — the "Unassigned" row omitted the forecast keys every coded row carries | Added the four missing keys to that one row |
| Timezone gate | `milestone._compute_state` compared against `fields.Date.today()` (UTC), so a milestone due yesterday in the user's calendar was not marked delayed until UTC caught up | `fields.Date.context_today(rec)` |
| Timezone gate | Certificate, owner-billing and retention-release bills fell back to `Date.today()`, dating a document raised at 01:00 local into the **previous period** | `context_today` on every accounting document date |
| Multi-company gate | The integrity audit crashed for a user without purchase rights | Reports the checks it could not run, by name, and still refuses to elevate |

A fifth was found and **deliberately not fixed**; see Known deferred defects.

## M10A — The legacy dashboard, retired

M9's audit established that the old dashboard searched globally across every
project and company, derived "budget" from milestone amounts and "actual" from
manual cost lines, and therefore contradicted M2.

Nothing external depended on it — no other module, no API reference, no tour.
It was **deprecated rather than deleted**, because deleting code at freeze adds
risk without reducing any:

- the action is renamed *Construction Dashboard (deprecated)*;
- its menu is restricted to `base.group_no_one` — developer mode only — and
  moved to the end of the menu;
- the provider's docstring states exactly why its figures are wrong;
- **Control Tower** is the canonical surface and leads the menu.

`test_b_the_legacy_dashboard_is_not_a_user_facing_financial_surface` asserts
that neither an ordinary user nor a construction manager is offered it, and
that the Control Tower is.

## M10B — Glossary, frozen

The full glossary is in the M9 section and is now a product contract. The
terms that must never be interchanged: Budget, Commitment, Actual, Certified,
Invoiced, Paid, ETC, EAC, Potential Change, Approved Change, Claim, Retention,
Advance.

## M10C — The integrity audit

`realestate.construction.integrity.audit.run()` — twenty-four checks across
eleven categories, returning severity, category, finding, expected, actual,
remediation and a drilldown action for each.

Three properties are enforced by test:

- **Read-only.** `test_d_the_integrity_audit_is_read_only_and_repeatable`
  snapshots seven tables around two consecutive runs and asserts nothing moved.
- **Deterministic.** The same database produces the same findings twice.
- **Honest about its own limits.** A check the auditing user cannot run is
  reported by name at `low` severity rather than skipped silently or forced
  through with `sudo()`.

Severity means something: `critical` is reserved for money, company isolation
and authoritative history. An uncoded purchase line is `medium`.

## M10D–F — Migration

Two versioned scripts under `migrations/18.0.1.0.0/`:

**`post-migrate.py`** classifies and logs, using one vocabulary an operator can
grep: `MIGRATED`, `DETERMINISTIC`, `LEGACY_VALID`, `AMBIGUOUS`,
`NEEDS_FINANCE_REVIEW`, `SKIPPED`. It derives a company only where the project
makes it deterministic, reports budget classification per project, quantifies
the pre-M7 retention understatement, marks legacy over-certification, and
confirms that project access remains opt-in.

**`end-migrate.py`** runs the integrity audit read-only after every dependency
has upgraded and logs the findings at their severity. It repairs nothing: an
upgrade that silently corrected an anomaly would destroy the evidence of how it
arose.

**Nothing is invented.** Ambiguous legacy budgets stay ambiguous. Posted
retention entries are never rewritten. Historic over-certification is preserved
and marked. No project team is populated, because doing so would activate
access restriction on every project on upgrade day and lock a live deployment
out of its own work.

Idempotency is proved twice over: `test_m10_migration.py` asserts every
classifier writes nothing and repeats exactly, and the release gate installs
then upgrades the same database, comparing counts.

## M10G/H — Accounting reconciliation

Construction Actual is exactly the posted ledger cost attributable to the
project's analytic dimensions, tax-exclusive, gross of retention. Twelve tests,
one per way that claim could be false: posted bill, draft bill, tax, multi-cost-code
bill, credit note, cancelled bill, unrelated project, manual journal entry with
construction analytics, retention end to end, advance end to end, and full-cycle
agreement across four surfaces.

Retention, pinned exactly:

```
Certified 100,000, retention 5%:
    Actual                100,000     (not 95,000)
    Retention liability     5,000
    Bill total             95,000
Release 5,000:
    Retention outstanding       0
    Actual                100,000     (unchanged)
    Certificate           100,000     (untouched)
```

Advance, pinned exactly: 1,000,000 advanced, 200,000 recovered, 800,000
outstanding, second recovery of 900,000 refused, outstanding never negative.

A deterministic four-case matrix (including negative changes and zero actual)
asserts the four equations hold across combinations hand-written fixtures miss.
Deterministic by construction — no random seeds, no flakiness.

## M10I — The permanent double-count suite

`tests/test_no_double_counting.py` — ten regressions, each corresponding to a
defect this programme actually found:

package + its purchase order · package + its variation · purchase order + its
bill · tax in analytic distribution · retention · advance and its recovery ·
risk → issue → change → claim · approved change leaving potential exposure ·
owner revenue vs contractor cost · multi-plan analytic distribution.

The question each asks is the same: does one economic event appear exactly
once, in the metric where it belongs?

## M10J — Timezone and date semantics

Every deployment this suite targets is ahead of UTC, so for the last hours of
each local day `Date.today()` and the user's date disagree. Eleven tests freeze
time at 22:00 UTC — already tomorrow at UTC+14, still today at UTC-10 — and
assert dates in the user's calendar. Nothing asserts against the wall clock, so
nothing can be flaky.

The audit was targeted, not mechanical: date semantics that belong to a person
(a site report's date, a milestone's lateness, an accounting document's date)
now use `context_today`; the deprecated dashboard was left alone.

## M10K/L — Security and multi-company

M9's rules were re-verified independently, and the torture test runs every
assertion with **both companies active at once** — the configuration where a
rule reading `env.company` instead of `company_ids` looks correct in every
single-company test and leaks the moment somebody ticks two boxes.

Seven tests: a company A user never sees B; both companies active still scopes
each project; switching the active company does not change a project's figures;
a company B user cannot read a company A record; the company rule is global and
not a privilege; the audit stays inside its company; a cross-company record is
refused at creation.

`sudo()` usage was reviewed: it appears where a control service must aggregate
analytic lines and read configuration parameters. The aggregate is a
construction control figure and is deliberately available; the **drilldown is
not elevated**, so a user without accounting rights gets an `AccessError` when
they open it. Both halves are tested.

## M10M — Concurrency

Nine guards re-proved: one baselined budget, one certificate per remaining BOQ
quantity, idempotent change implementation, idempotent EOT implementation, one
active ITP revision, retention releases bounded by what is held, advance
recoveries bounded by what was advanced, certificates bounded by the contract,
and exactly one current document revision.

## M10R — Legacy code

| Item | Decision |
|---|---|
| Legacy dashboard model, JS, template | **Deprecate.** Restricted to developer mode, docstring states why its figures are wrong |
| `milestone.budget_amount` as a budget source | **Compatibility bridge.** Consumed only by the budget classifier |
| Manual `cost.line` as "actual" | **Compatibility bridge.** Never consumed by a control figure |
| `contractor._action_release_retention_legacy()` | **Deprecate.** Renamed with an underscore, nothing calls it |
| `current_certified_pct` and the percentage path | **Keep.** Live data uses it; amounts are authoritative |
| Legacy stored fields | **Keep.** No field removed without migration evidence |

Nothing was deleted. Deletion at freeze adds risk and removes nothing.

## Known deferred defects

**Milestone state does not compute at creation.** `state` carries both a
`default=` and a `compute=` with `readonly=False`, so every `create()` supplies
a value and `_compute_state` never runs until a dependency is next written: a
milestone created already past its expected end date reads *Not Started* until
something touches it.

Removing the default was tried and rejected during freeze — it makes the column
NULL for records created before any dependency exists, which broke thirteen
existing tests. The field carries a comment explaining exactly this, and the
fix belongs in the next feature cycle where it can be staged with a migration.

**`real_estate_checks` custody date defect.** `_open_custody()` stamps the
Datetime `custody.date` with a Date from `context_today`, coercing to midnight
UTC; for users ahead of UTC the receipt row is future-dated and outranks later
movements in `_apply_to_check()`'s ordering, so the cheque's custodian mirror
silently reverts. Reproduced and reported, **not fixed**: it is in a frozen
module, it does not affect Construction correctness, and per M10U it is
recorded as a separate Module 3 maintenance item rather than buried in a
Construction freeze.

## Documentation

- `IMPLEMENTATION_REPORT.md` — this document, M1–M10.
- `UPGRADE_AND_OPERATIONS.md` — pre-upgrade backups and audits, the upgrade
  command and expected log lines, post-upgrade verification in order, rollback
  (restore, not downgrade), and four role checklists: Finance, Commercial,
  Project Controls, Security.
- `PHASE_0_AUDIT_REPORT.md` — the original audit, unchanged.

---

# FREEZE — `real_estate_construction` 18.0.1.0.0

Frozen 2026-08-09. Repository commit `3778f4dc`.

## Final gate — every phase clean

| Gate | Result |
|---|---|
| Fresh install, 14 modules | RC=0 — **2099 tests, 0 failed, 0 errors** |
| Full in-place upgrade, 14 modules | RC=0 — **2099 tests, 0 failed, 0 errors** |
| Construction suite | **563 tests, 0 failed** |
| HOOT (Construction) | **6 tests — suite succeeded** |
| Browser tours (Construction) | **4 tours — all succeeded on minified production assets** |
| Legacy migration | RC=0, classified, nothing invented |
| Migration retry | **IDEMPOTENT=YES** — identical counts and totals |
| Integrity audit | CRITICAL 0 · HIGH 1 · MEDIUM 1 · LOW 0 |

Identical counts across install and upgrade. The only ERROR lines in either
phase are `sql_db: bad query` from the constraint tests themselves, which is
the unique indexes refusing duplicates exactly as designed.

## Frozen baselines

| Module | Tests |
|---|---|
| `real_estate_construction` | **563** |
| `atmta_real_estate` | 299 |
| `real_estate_brokerage` | 352 |
| `real_estate_checks` | 325 |
| `real_estate_developer` | 247 |
| `real_estate_maquette` | 291 |
| `real_estate_plan` | 15 |
| `real_estate_portal` | 7 |
| **Suite total** | **2099** |

## Integrity audit — the two accepted findings

Run against the migrated legacy database, as superuser, with **no skipped
checks**:

- **HIGH · `missing_control_accounts` (1).** The retention and advance control
  accounts are not configured on that test company. This is configuration, not
  corruption: certificates *refuse* to post retention without them, so no
  figure is unreliable. The Finance checklist in
  `UPGRADE_AND_OPERATIONS.md` covers it, and it clears the moment a company is
  configured.
- **MEDIUM · `uncoded_commitment` (1).** The deliberately seeded uncoded
  purchase line. It appears under Unassigned on the cost sheet with a
  drilldown — visible, not hidden.

No CRITICAL findings. No finding is caused by the new architecture.

## Migration proof

Legacy-shaped database, upgraded twice by resetting the recorded module
version — the only way to exercise migration scripts, since neither a fresh
install nor a same-version upgrade runs them.

| Classification | Count |
|---|---|
| `budget_deterministic` | 22 |
| `budget_ambiguous` | **1** — expected 5,000,000 vs milestones 1,250,000, disagreeing by 3,750,000. **No baseline created.** |
| `company_deterministic` | 0 |
| `company_ambiguous` | 0 |
| `retention_needs_finance_review` | 0 |
| `certification_legacy_overcertification` | 0 |
| `projects_restricted` | 0 of 23 — access restriction stays opt-in |

Second run: identical record counts across budgets, budget lines, WBS, cost
codes, packages, document revisions, EOTs and commitment changes; identical
security rows; identical financial totals. `IDEMPOTENT=YES`, no errors.

## Financial proof

Pinned by the freeze invariants and the permanent regression suites:

```
Current Budget      = Original Budget + Approved Budget Changes
Current Commitment  = Original Commitment + Approved Commitment Changes
Actual              = eligible posted Odoo ledger cost, tax-exclusive
EAC                 = Actual + ETC
Forecast Variance   = Current Budget − EAC
```

and the ten double-count regressions, the twelve reconciliation tests and the
nine concurrency guards behind them.

## Post-freeze rule

`real_estate_construction` is frozen at 563 tests. A future change requires
either a reproducible defect with a failing regression test, or a deliberate
next-version feature cycle. Not opportunistic cleanup, not a redesign, and not
a fix for a documented deferred item taken on the side.


---

## Remaining gaps

| Area | Status | Milestone |
|---|---|---|
| WBS / cost codes / analytic | **implemented** | M1 |
| Budget, commitment, cost report | **implemented** | M2 |
| Forecast / ETC / EAC | **implemented** | M3 |
| Change events and change orders | **implemented** | M4 |
| RFI | **implemented** | M5 |
| Submittals and review workflow | **implemented** | M5 |
| Document register and revision control | **implemented** | M5 |
| Transmittals | **implemented** | M5 |
| Transmittal PDF cover sheet | **not built** — registers cover the need today | M9 |
| Due-date reminder crons | **not built** — dates live on the records | M9 |
| Odoo Documents integration | **not used** — Enterprise module absent | — |
| BOQ authorised-quantity variation | **implemented** | M7 |
| Remaining-BOQ forecast method | **implemented** — the input it waited for landed | M7 |
| Retention correct accounting | **fixed** — liability account, register, staged release | M7 |
| Materials on site, escalation, retention bonds | not built | M8 |
| Certificate / IPC cover sheet PDF | not built | M9 |
| ITP, inspections, NCR, daily site reports | **implemented** | M6 |
| Quality dashboards and registers | not built | M9 |
| Inspection notification / reminder crons | not built — dates live on the records | M9 |
| Certificates, advances, owner billing | **implemented** | M7 |
| Claims, EOT, delay register, risk, issues | **implemented** | M8 |
| Liquidated damages calculation | tracking only, by design | — |
| Adjudication / arbitration management | out of scope | — |
| Control Tower, cost sheet, registers | **implemented** | M9 |
| Record rules and role matrix | **implemented** — Phase 0's last defect closed | M9 |
| Planned progress / S-curve | **deferred** — needs a schedule baseline ATMTA does not hold | external |
| Portfolio view and periodic review snapshot | **partial** — the aggregation services exist and are tested; a dedicated portfolio screen and the periodic-review snapshot document were not built | deferred |
| Migration, browser gate, freeze | **implemented** | M10 |
| Integrity audit | **implemented** — 24 checks, read-only | M10 |
| Legacy dashboard | **deprecated** — developer-mode only, Control Tower canonical | M10 |

### Deferred, classified

| Item | Class | Why |
|---|---|---|
| Planned progress and the planned S-curve | **external integration** | Needs an authoritative programme baseline (P6, MS Project or a structured import). Interpolating a straight line between two dates would invent the number every variance is measured against |
| Transmittal PDF cover sheet | **deferred** | The transmittal register and its exact-revision lines carry the evidence today. An operational convenience, not architecture |
| RFI / submittal / inspection reminder crons | **deferred** | Every due date is on its record and every register sorts by it. A notification engine is an operational enhancement |
| Materials on site, price escalation, retention bonds | **deferred** | Separate valuation and instrument rules; each needs its own milestone |
| Liquidated damages calculation | **contract-specific** | The rate, cap and any determined deduction are tracked. Calculating an entitlement to deduct is a determination, not arithmetic |
| FIDIC / NEC / local contract administration logic | **contract-specific** | Every period, threshold and authority is configuration. Nothing interprets a standard form |
| Adjudication, arbitration, litigation management | **out of scope** | The dispute fields are the extension hook |
| HSE, BIM authoring, CPM scheduling | **out of scope** | Never in scope for this module |
| Head-office overhead formulas (Hudson, Emden, Eichleay) | **contract-specific** | Formula choice is a legal argument, not a default |
| Production-scale performance benchmarks | **deferred** | Query budgets are enforced against test fixtures. No production SLA is claimed from a development machine |

### Known deferred defects — next version migration items

**1. Milestone `state` does not compute at creation.**

*Current behaviour:* `realestate.construction.milestone.state` carries both a
`default='not_started'` and `compute='_compute_state'` with `readonly=False`.
Odoo supplies the default on every `create()`, so the compute does not run
until a dependency (`completion_percentage`, `expected_end_date`,
`actual_end_date`) is next written.

*Why it is wrong:* a milestone created already past its expected end date
reads *Not Started* rather than *Delayed* until something happens to touch it.

*Why it was not changed at freeze:* removing the `default=` was attempted and
reverted. The column is `required=True`, and records created before any
dependency exists then fail the NOT NULL constraint — thirteen existing tests
broke immediately. That is a behavioural change with a data-migration
component, which is precisely what a freeze is not the moment for.

*Affected:* every `realestate.construction.milestone`; visible in the
milestone list, the project's progress figure and the legacy dashboard.

*Proposed approach:* remove the default, add a post-migration script that
recomputes `state` for every existing milestone in batches, and add a create
override or an explicit compute trigger so the field is populated for records
created with no dependency values. Ship it as a feature-cycle change with its
own regression test, not as a patch.

**2. `real_estate_checks` custody Date/Datetime defect.**

`_open_custody()` stamps the Datetime field `custody.date` with a Date from
`context_today`, which coerces to midnight UTC. For users ahead of UTC the
receipt row is future-dated and outranks every later movement in
`_apply_to_check()`'s `order='date desc'`, so the cheque's custodian and
location mirror silently revert. Reproduced during M2 and reported.

**Not fixed, deliberately.** It is in a frozen module, it does not affect
Construction correctness, and no Construction gate fails because of it. It is
recorded here only as a cross-reference: it belongs to Module 3 maintenance,
not to a Construction freeze.

**3. Legacy retention reclassification.**

Pre-M7 certificates posted retention as a negative expense line. The exact
understatement is reported by the integrity audit per project. The posted
entries are **not** rewritten by any migration. Reclassifying is a Finance
decision with a period, a journal and an approver — the runbook is in
`UPGRADE_AND_OPERATIONS.md`.
