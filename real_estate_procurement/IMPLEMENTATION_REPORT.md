# `real_estate_procurement` — Enterprise Upgrade Implementation Report

Module version **18.0.3.0.0** · Milestones **Phase 0**, **M2** and **M3**
complete · Construction frozen baseline **563 tests, unchanged**

Sections 1–10 describe M2 (demand, coding, sourcing). Sections 11–27 describe
M3 (budget control, reservation, approval governance, purchase-order
governance).

This report is written to be read by somebody deciding whether to trust the
module in production. Where something is not finished, it says so and names the
milestone that owns it. Nothing here claims a control that does not exist.

---

## 1. Phase 0 findings

The full audit is in `PHASE_0_AUDIT_REPORT.md`. Condensed, with the M2 outcome
against each: **F** = fixed in M2, **M3/M5/M7** = owned by that milestone, **–**
= deliberately unchanged.

### The five architectural questions

| # | Question | Answer at Phase 0 | M2 |
|---|---|---|---|
| Q1 | Does Procurement duplicate Odoo Purchase? | No model duplication, but the quotation stage was skipped entirely and the vendor came from `seller_ids[0]` | **F** |
| Q2 | Where does Construction Commitment appear? | At purchase-order confirmation, computed by Construction. Correct — but one click reached it from an approved requisition | **F** |
| Q3 | Can one obligation be counted twice? | Not yet, because nothing is reserved. That is also the risk | **M3** |
| Q4 | Does every PO line carry Project + WBS + Cost Code? | No. Project analytic only; every generated line landed under **Unassigned** | **F** |
| Q5 | Can governance be bypassed? | Three ways: urgent priority, self-approval, and direct purchase orders | **M3** |

### Budget and commitment

| # | Finding | M2 |
|---|---|---|
| B1 | No budget check of any kind | M3 |
| B2 | No reservation — several approved requests consume the same capacity | M3 |
| B3 | Generated PO lines carry no cost code → commitment lands under Unassigned | **F** |
| B4 | `estimated_cost` fell through to `standard_price` then to **zero**, and zero cleared every approval bracket | **F** — `estimate_is_known` now states whether an amount is known; unknown is not zero |
| B5 | No procurement type; a service was pushed through material expectations | **F** — `procurement_type` on request and plan line |
| — | Estimates were accidentally tax-exclusive; nothing said so | **F** — stated on every estimate field, per Rule 6 |

### Approval

| # | Finding | M2 |
|---|---|---|
| A1 | Urgent priority bypasses approval completely | **Fixed in M3** — §18 |
| A2 | Requester may approve their own request | **Fixed in M3** — §17 |
| A3 | Rules matched against `self.env.company`, not the request's company | **Fixed in M3** — §25 |
| A4 | No approval on the award or the purchase order | Purchase-order side **governed in M3** (§19–§20); award approval is M5/M7 |
| A5 | Amount basis is the flawed estimate | Partly fixed — M2 made the flaw visible (`estimate_is_known`), M3 converts the basis to company currency, and an unknown estimate still brackets as zero (§57) |
| A6 | Step group resolved through `get_external_id()` | – |
| A7 | Re-submission unlinks unapproved steps | Partly **F** — a revision snapshots the basis first |

### Sourcing, delivery, vendors

| Finding | M2 |
|---|---|
| No sourcing event, RFQ governance, bid record, evaluation, award | M5/M6/M7 — explicitly not built here |
| No required-on-site date per line | **F** |
| No long-lead flag or lead times | **F** on the plan line, threshold configurable |
| `_compute_received()` wrote to another record from inside a compute | **F** — receipts now roll up from `stock.picking._action_done()` |
| No material-inspection link to Construction QA/QC | M8 |
| No vendor prequalification, AVL, or performance | M10+ |

### Security and multi-company

| # | Finding | M2 |
|---|---|---|
| S1 | **No record rules at all** | **F** — global company rules on five models plus a requester/buyer split |
| S2 | No `company_id` anywhere | **F** — on request, line, plan, plan line, revision, approval step |
| S3 | Three groups only | Partly **F** — Requester / Buyer / Approver / Manager. The full M19 matrix is not built |
| S4 | Line ACL grants `unlink` to plain users | – (M3 with the approval hardening) |
| S5 | Approval brackets readable by all | – |
| — | Nothing prevented a request from pointing at another company's project | **F** — constraint plus `check_company` |

### Migration risks

All five carried into the M2 migration design; see §9.

### Cross-module observations

| Finding | M2 |
|---|---|
| Setting `re_cost_code_id` as an ordinary purchase user raises `AccessError` | **F** — proved, then fixed in Construction. See §8 |
| PO confirmation writes to `realestate.project` via lazy stock-location creation | Deferred to the **M7 PO Confirmation Integration Gate**. See §10 |

---

## 2. Procurement Plan — demand, not money

`realestate.procurement.plan` and `realestate.procurement.plan.line`.

A plan answers questions a requisition is far too late to answer: what has to
be ordered this quarter, and what has a nine-month lead time and therefore
needed sourcing to start last month.

It is **not** budget, commitment, actual or a purchase order, and it moves no
Construction figure. That is asserted, not asserted-in-prose:
`test_01_a_plan_is_demand_and_never_money` plans 3,000,000 against a 10,000,000
baselined budget and requires commitment and actual to stay at zero.

**Lifecycle:** draft → review → approved → active → superseded / closed.

**Revisioning.** An approved or active plan cannot be rewritten. `write()`
refuses to change lines, period or project on one, and `action_create_revision()`
copies it forward: the new revision starts in draft at `revision + 1` with
`supersedes_id` pointing back, and the original moves to superseded with
`superseded_by_id` pointing forward. Rev 0 keeps the dates and amounts the
schedule was built from — `test_01b` asserts both directions of the link and
that Rev 0's total is unchanged.

**Lines** carry project, WBS, cost code, procurement type, optional product,
category, free-text scope, quantity, UoM, estimated unit cost and amount,
currency, required-on-site date, lead time, target award and RFQ dates,
responsible buyer, procurement strategy, long-lead flag, state and notes.

A product is **not** required. A subcontract package or a conceptual line has
no catalogue entry, and forcing one would invent a product.

**Dates and unknowns.** `target_award_date` is required-on-site minus the lead
time, and `target_rfq_date` is that minus the allowed enquiry period. When the
lead time is unknown **both stay empty**, and `has_lead_time` says which case a
reader is looking at. Required-date-minus-zero would put every long-lead item
comfortably on schedule. `test_13` pins it.

**Long lead** is either set explicitly or derived from
`real_estate_procurement.long_lead_threshold_days` (default 90). No number is
hard-coded in the logic; `test_13b` changes the threshold and the derivation
follows.

---

## 3. Requisition V2

The existing model keeps its database identity — `realestate.material.request`
— exactly as the brief required. Production records remain valid; nothing was
replaced.

**Header additions:** company, procurement type, source type, plan line and
plan, responsible buyer, business justification, request date, revision,
revision history, migration status, coding coverage, data-quality warnings,
and the header defaults for WBS and cost code.

**Line additions:** project and company (related), estimated unit cost,
`estimate_is_known`, required-on-site date, WBS, cost code, substitution
allowed, preferred brand, specification reference, notes, `po_line_ids`,
ordered and remaining quantity.

`product_id` is no longer required, and `uom_id` no longer is either — a
lump-sum subcontract scope has no unit, and inventing one would put a fictional
quantity on the enquiry. A line must still say *something*: either a product or
a written scope. A line with a product must have a unit of measure, because
Odoo's own quantity semantics require it.

**State machine:** draft → submitted → approved → **sourcing** → ordered →
partially received → received → done, with cancelled alongside. `sourcing` is
the state M2 added, and it is the whole point: there is now room between "the
need is authorised" and "the company is obliged", which is where the enquiry,
the comparison and the award belong.

`ordered` is reached only when somebody confirms a purchase order — Procurement
never confirms one itself. `purchase.order.button_confirm()` notices and tells
the requisition; `remaining_qty` stays readable on the line rather than being
hidden behind a closed state (`test_16b`).

**Controlled edits.** After submission, the fields that make up the approved
basis — quantity, UoM, product, description, estimate, required date, WBS, cost
code, project, procurement type, needed-by, priority — refuse to change. This is
not a permission check: a buyer with every right in the system still should not
be able to turn an approved 100 into 150 invisibly.

**Revision.** `action_revise(reason)` requires a reason, snapshots the basis
into an immutable `realestate.material.request.revision` row (state, total, and
the lines as JSON), bumps the revision, clears the approval and returns the
request to draft. The snapshot's own `write()` refuses edits. Revising is
refused once orders are confirmed against the request — changing ordered demand
is a purchasing change, not a requisition edit. `test_15` walks the whole path.

---

## 4. WBS and cost code — line level is authoritative

WBS says *where in the works*; cost code says *what kind of money*. They stay
separate models, both owned by Construction. Procurement creates neither
`procurement.wbs` nor `procurement.cost.code`.

**The header default is only a default.** `default_wbs_id` and
`default_cost_code_id` seed new lines; the line value is what reaches the
purchase order. One requisition routinely spans several codes, and stamping a
header code onto every line is how money reaches a cost report wearing somebody
else's classification. `test_08` puts concrete and electrical on one request and
requires each RFQ line to keep its own code.

**Validation is server-side**, not a form domain: a request in project A cannot
use a WBS node from project B, and a cost code from another company is refused
(`test_09`, `test_11`). Domains help whoever is typing; the constraint has to
hold for imports, RPC and every other way a record arrives.

### Where the code lives, and why

`real_estate_construction` **depends on** `real_estate_procurement`, so the
foreign keys cannot point from Procurement to Construction. The coding fields
are therefore added by Construction, in
`real_estate_construction/models/procurement_requisition.py` — exactly as that
module already adds `re_wbs_id` and `re_cost_code_id` to `purchase.order.line`.

Procurement defines two extension points, and Construction overrides both:

| Hook | Owner | Purpose |
|---|---|---|
| `MaterialRequest._prepare_rfq_line(line)` | Procurement | Commercial values for one RFQ line |
| ↳ override | Construction | Adds `re_wbs_id` and `re_cost_code_id` |
| `ProcurementPlanLine._prepare_requisition_line_values()` | Procurement | Values for a requisition line raised from a plan |
| ↳ override | Construction | Adds `wbs_id` and `cost_code_id` |

Procurement's own coverage computes read whether the coding fields exist rather
than assuming them, so the module still installs and works without
Construction — `coding_status` then reports `No Coding Installed` rather than
pretending everything is coded.

---

## 5. Analytic integration

Procurement writes **no** analytic distribution of its own for coded lines. It
sets `re_cost_code_id` and `re_wbs_id`, and Construction's
`purchase.order.line.create()` builds the distribution with its own helper.
There is one formula, in one place.

This matters because Construction learned the hard way that two separate plan
keys create two analytic lines *each at the full amount*. The correct
representation is one comma-joined key holding both plans. `test_07` compares a
Procurement-generated RFQ line against a hand-created, correctly coded
Construction PO line and requires the distributions to be **identical**, and the
key count to be exactly one.

`test_06` takes it to the end: requisition → RFQ → confirmed order → cost sheet.
The 3,000,000 appears under the concrete cost code, and **no** Unassigned row
carries any commitment. That is the Phase 0 Q4 defect closed at the place it was
actually visible.

---

## 6. RFQ behaviour — the lifecycle change M2 exists to make

**Old:**

```
approved requisition → action_create_purchase_orders()
                     → purchase.order.create()
                     → button_confirm()          ← Construction Commitment
```

One click, no enquiry, no comparison, no award, no vendor decision.

**New:**

```
approved requisition → action_create_rfqs(vendors=…)
                     → one draft purchase order per named vendor
                     → request state: sourcing
                     → Construction Commitment: 0
```

Nothing calls `button_confirm()` from any requisition action. Confirming is a
separate, deliberate act, and M7 will govern it.

**One requisition → several RFQs** is supported now: name three vendors, get
three quotations. **Several requisitions → one sourcing event** is not
prevented — no relation forces one-to-one — but consolidation UI is M5's.

**Split award** is architecturally possible from today: `po_line_ids` is a
one-to-many, so one requisition line can relate to purchase lines on different
orders (`test_16`). The old `po_line_id` remains as a deprecated read-only
field so existing records and reports keep working.

**Compatibility decision (documented, as the brief asked).**
`action_create_purchase_orders()` is **kept and refuses**. It does not silently
delegate to `action_create_rfqs()`, because a method called "create purchase
orders" that quietly creates enquiries instead would be its own kind of lie.
Calling it raises a `UserError` that explains the new flow. The button in the
form view was replaced with **Create RFQs**, so nothing in the UI presents the
old path as normal sourcing. `test_19` pins the refusal.

---

## 7. Vendor selection

`_get_preferred_supplier()` — which returned `seller_ids[0]` and whose result
became the awarded vendor — is **removed**. In its place,
`_suggested_suppliers()` returns every catalogue vendor and decides nothing.

`action_create_rfqs()` **requires** vendors. Called without them it refuses, and
the error message lists the catalogue suggestions rather than acting on them.
The UI path is a wizard where the buyer picks; suggested vendors are shown
beside the choice, read-only, labelled as a suggestion.

No "cheapest supplier" algorithm was introduced to replace the old one. Vendor
selection is a sourcing decision and belongs to M5/M6. `test_d` and `test_05`
require that two configured suppliers produce two enquiries and no award.

---

## 8. Frozen Construction integration — the cost-code access defect

Phase 0 predicted this. M2 **proved it first**, with a real user, then fixed the
smallest possible thing.

**The proof.** `test_10_a_buyer_may_reference_a_cost_code_when_sourcing` builds
a legitimate buyer — Odoo purchase user, Procurement buyer, read-only on the
developer's projects and read-only on Construction's cost codes — and has them
source an approved, correctly coded requisition. Before the fix:

```
File ".../real_estate_construction/models/cost_structure.py", line 305,
     in _get_or_create_analytic_account
    self.analytic_account_id = account
odoo.exceptions.AccessError: You are not allowed to modify
    'Construction Cost Code' (realestate.construction.cost.code) records.
```

Referencing an existing cost code required permission to **edit the cost-code
catalogue**. The only way to code a purchase line correctly was to be able to
invent the code — so the uncoded path succeeded and the correct one failed.
That is a cross-module contract defect owned by the frozen module, not by
Procurement.

A second instance appeared one level up once the first was fixed, in
`project_analytic.py`: the project's analytic account is also created lazily,
and storing the back-link wrote to `realestate.project`, which a buyer reads but
does not write.

**The fix**, in both places, is one line each:

```python
self.sudo().write({'analytic_account_id': account.id})
```

* The analytic account was **already** created with `sudo()`. Recording which
  account was created is the other half of the same system act.
* It is narrow on purpose: one field, on one record. There is no `sudo()`
  wrapped around the purchase-order create, which would hand away every check
  the document is subject to.
* **No ACL was widened.** A buyer still cannot create, edit or delete a cost
  code or a WBS node — `test_10b` and `test_10c` assert all three refusals.
* Construction's frozen suite is **563 tests, unchanged and green**.

**Why not solve it Procurement-side?** Procurement could have pre-materialised
the analytic account with its own `sudo()` before creating the line. That would
have moved the same escalation into a module that has no business reaching into
Construction's master data, and would have left the defect open for anybody
hand-coding a purchase line. The fix belongs where the write is.

**Deployment note.** Procurement's groups deliberately grant neither project
read nor cost-code read: those are the developer's and Construction's rights to
give. A real Procurement Buyer therefore needs `real_estate_developer` read-only
and `real_estate_construction` user alongside the Procurement Buyer group. The
test personas are built exactly that way.

*(Related, and recorded rather than fixed in M2: Procurement references
`realestate.project` without declaring `real_estate_developer` in its manifest.
It works because the suite installs the developer module first.)*

### What M3 added to Construction, and why

M3 needed Construction to hold three more things, and every one of them is
**additive** — no existing Construction model, record, formula, view or test
changes:

1. `models/procurement_requisition.py` gains two classes: `cost_code_id` /
   `wbs_id` on the reservation, and `cost_code_id` / `change_order_id` on the
   control exception. Same dependency reason as M2 — Construction owns cost
   codes and depends on Procurement, so the foreign key cannot point the other
   way.
2. `views/procurement_coding_views.xml`, a new file of inherited views that
   puts WBS and cost code on the requisition line, the plan line, the
   reservation and the exception. It has to live here for the same reason: a
   Procurement view naming `cost_code_id` would fail validation on a
   Procurement-only install, where the field genuinely does not exist. Without
   it, M3's per-cost-code control would depend on coding nobody could enter
   from the interface.
3. `__manifest__.py`: version `1.0.0` → `1.0.1`, so an existing database
   actually loads the new view file on upgrade. Nothing else in the manifest
   changed, and the `18.0.1.0.0` migration scripts do not re-run.

**The Procurement dependency is now declared.** `real_estate_developer` is in
Procurement's `depends`, closing the M2 finding above. M3 adds fields *to*
`realestate.project` (the two policy overrides), so relying on install order
would no longer merely be untidy — it would break a Procurement-only install
outright.

The frozen Construction suite runs at **563 tests, 0 failed** after all of it
(§32).

---

## 9. Migration

Module version 0.2 → **18.0.2.0.0**, with `migrations/18.0.2.0.0/post-migrate.py`.

It does three things and deliberately no fourth:

1. **Re-links legacy purchase lines.** `po_line_ids` derives from the purchase
   line's back-reference, so a legacy line whose purchase line lost that
   reference would vanish from its request. Restored where unambiguous.
2. **Classifies every existing requisition** into: `valid`, `needs_project`,
   `needs_wbs`, `needs_cost_code`, `legacy_auto_confirmed`, `linked_rfq_po`,
   `ambiguous`. Stored on `legacy_status`, which is a finding, not an
   instruction.
3. **Touches no purchase document at all.**

It does **not** manufacture procurement plans for historical requests, assign a
default cost code to anything, revert a confirmed order to an enquiry, or remove
a commitment Construction recorded. Cost codes are derived only where
deterministic; otherwise the record says `needs_cost_code` and waits for a
human.

`legacy_auto_confirmed` outranks `needs_cost_code` deliberately: `po_line_id`
was only ever written by the old one-click flow, so its presence dates the
record exactly — and that money is already committed under Unassigned, where
coding the requisition now would not move it.

### A defect found in this work, in my own code

The first version of the classifier read the stored `lines_missing_cost_code`
aggregate. During a migration that stored compute is still NULL, so an entirely
uncoded legacy database was classified `valid` — unknown read as zero, the exact
mistake the classification exists to catch. It was caught by running the
migration against a seeded legacy database rather than by trusting the unit
tests, which pass either way because the ORM recomputes.

There is a second, structural reason: Construction owns the coding fields and
loads **after** Procurement, so at Procurement's migration time those fields are
not in the registry at all, and on a first upgrade the columns do not yet exist.
The migration therefore determines the answer in SQL and passes it in
(`uncoded_ids` / `unassigned_wbs_ids`); left as `None`, the classifier reads the
fields normally, which is right everywhere else.

### Verified against a real legacy database

A copy of the Construction freeze database, seeded with three pre-M2
requisitions by raw SQL (one coded-nothing, one with no project, one linked to a
purchase line through the deprecated `po_line_id`), then upgraded:

```
M2: re-linked 1 legacy purchase line(s) to their requisition line.
M2: classified 3 requisition(s):
    legacy_auto_confirmed=1, needs_cost_code=1, needs_project=1
```

Purchase orders after the upgrade: 6 draft, 1 sent, 4 confirmed — **identical**
to before. No order was reverted, no commitment moved, no requisition was
invented.

---

## 10. Project stock location — deferred to M7

Confirming a project purchase order can write to `realestate.project` through
lazy stock-location creation. M2's normal workflow stops at the enquiry, so it
does not require confirmation, and the brief was explicit: do not modify frozen
Construction merely because the defect exists.

Recorded as the **M7 PO Confirmation Integration Gate**. It will be reproduced
and resolved before Award → PO confirmation goes live.

*(The lazy analytic-account writes were a different matter: M2 requires cost-code
propagation, and those fired at RFQ-line creation, which M2 does do. They are
fixed and documented in §8.)*

---

## 11. Reservation architecture — why reservation is not commitment

M2 left one sentence to be true and it was not:

```
    BUDGET 10,000,000 · TEN APPROVED REQUISITIONS OF 3,000,000 · ALL FINE
```

Nothing was wrong with any single approval. What was missing was the control
stage between *authorised* and *ordered* — often weeks long — in which a
project has decided to spend money and no supplier has been told anything.

M3 adds it as `realestate.procurement.reservation`.

```
    PROCUREMENT PLAN     demand, no money
    REQUISITION          demand, no money
    APPROVED REQUISITION authorised demand → PROCUREMENT RESERVATION
    DRAFT RFQ            an enquiry; the reservation is unchanged
    CONFIRMED PO         CONSTRUCTION COMMITMENT; the reservation converts
    POSTED VENDOR BILL   CONSTRUCTION ACTUAL
```

A reservation is **not** an accounting encumbrance, a journal entry, a
Construction commitment, a purchase order or an actual cost. Nothing in
`procurement_reservation.py` writes to the ledger, and
`test_r_reservation_creates_no_accounting` asserts that no `account.move` and no
`account.analytic.line` appears when demand is reserved.

The one rule everything is built to keep:

```
    ONE ECONOMIC OBLIGATION EXISTS IN ONE CONTROL STAGE AT A TIME.
```

A reservation of 3,000,000 sitting beside a commitment of 3,000,000 for the
same demand would report 6,000,000 of exposure on a project that owes
3,000,000 — and it would do it in the direction that looks prudent, which is
how such an error survives review. `test_2_conversion_happens_exactly_once`
asserts the sum, not just the parts.

**Line level, not header.** Construction controls money per cost code, so a
requisition for 2,000,000 of concrete and 1,000,000 of electrical is two
reservations against two positions (`test_o`). `cost_code_id` and `wbs_id` are
added to the reservation model *by Construction*, for the same dependency
reason as in M2: Construction owns cost codes and depends on Procurement, so
the foreign key cannot point the other way.

**Tax-exclusive.** Construction's budget and commitment are both stated net, so
the reservation is too. A 1,000,000 requisition ordered at 1,000,000 + 15% VAT
reserves 1,000,000 and commits 1,000,000 (`test_p`). Reserving the gross figure
would overstate every position by the tax rate, invisibly.

**States.** `draft → reserved → converted`, with `released`, `cancelled` and
`expired` as the alternative endings. Converted and released are deliberately
different words: converted means an authoritative commitment replaced it,
released means the demand stopped consuming capacity without ever becoming one.
A reservation that converts most of itself and releases the remainder is filed
as **converted** — the ending that produced real money is the one that
describes it.

---

## 12. Available to procure

```
    AVAILABLE TO PROCURE = CURRENT BUDGET            (Construction)
                         − CURRENT COMMITMENT        (Construction)
                         − ACTIVE PROCUREMENT RESERVATIONS
```

`realestate.procurement.control.get_procurement_control_position()` is the only
place that computes it. Two of the three inputs are **read** from Construction,
never recomputed: `budget_by_cost_code()` and
`current_commitment_by_cost_code()` already encode rules Procurement has no
business restating — that a package and its purchase orders are one commitment
counted once, that a variation folded into an amended order is not added on
top, that uncoded money is reported under Unassigned rather than dropped. A
second implementation would eventually disagree with the cost report, and then
nobody could say which number was the project's.

`available_to_procure` is **not stored anywhere**. It is the difference between
three numbers that move independently, and a stored copy would be a fourth
number that is wrong for as long as it takes something else to recompute it.

### Unknown is not available

The service returns a status, not only a figure:

| Status | Meaning |
|---|---|
| `ok` | position established, demand fits |
| `over_budget` | position established, demand does not fit |
| `insufficient_data` | the position could not be established at all |

`insufficient_data` is returned when there is no project, no cost code, no
Construction baseline, no budget line for that cost code, or Construction is
not installed. It is never converted to zero (which would refuse legitimate
demand while reading as "the budget is exhausted") and never to unlimited
(which is how a control system becomes decorative). Under BLOCK it refuses;
under WARN it is recorded on the reservation and shown in the register.
`test_unknown_is_not_available` and
`test_a_cost_code_absent_from_the_baseline_is_not_zero_budget` pin both halves.

### Position versus demand

Two different questions, and the code keeps them apart. The *position* is
`over_budget` when availability has already gone negative. The *demand* is
over budget when this requisition exceeds what is left — which is what the
approval matrix has to react to, because a project with 1,000,000 remaining is
perfectly healthy right up to the moment somebody asks it for 3,000,000.
`_demand_status()` answers the second, excluding the request's own reservation
so an already-reserved requisition is not measured against a figure it has
itself reduced.

---

## 13. Concurrency

This is the defect that cannot be found by reading code:

```
    User A                          User B
    reads 10,000,000 available      reads 10,000,000 available
    reserves 8,000,000              reserves 8,000,000
                    → 16,000,000 reserved against 10,000,000
```

Nobody skipped a validation. Both transactions read a true number and then made
it false.

**Lock strategy.** `lock_control_scopes()` takes a PostgreSQL
**transaction-level advisory lock** per control scope before the position is
read, so the read and the write are serialised together. The lock is released
by commit or rollback rather than by anybody remembering to. Scope is
company + project + cost code — locking the whole procurement system would
make two unrelated projects queue behind each other for no reason.

**Lock ordering.** A requisition spanning concrete and electrical takes two
locks, and a second requisition spanning the same two in the other order would
deadlock against it. Tokens are therefore **sorted** before any is acquired, so
every caller in the system takes the same scopes in the same sequence
regardless of the order lines were typed in
(`test_lock_order_does_not_depend_on_the_order_lines_were_typed`).

**Database-level uniqueness.** A Python constraint cannot see a row another
transaction has not committed. A partial unique index can:

```sql
CREATE UNIQUE INDEX proc_reservation_one_active_per_line
    ON realestate_procurement_reservation (request_line_id)
 WHERE state = 'reserved' AND request_line_id IS NOT NULL
```

`test_the_database_refuses_a_second_active_reservation` proves it with a raw
`INSERT`, precisely because a raw insert bypasses every ORM check — which is
what a competing transaction effectively does.

**What is not proved by execution.** Odoo runs a test in one transaction, and
`registry.enter_test_mode()` makes additional cursors share that connection so
uncommitted fixture data is visible. A genuine two-process race therefore
cannot be staged inside the suite: the second "transaction" would be the same
transaction and would already hold the lock. `test_m3_concurrency.py` states
this in its own docstring and proves everything that can be proved
deterministically — that the lock is genuinely held (asserted against
`pg_locks`, not against the existence of a line of code), that ordering is
deterministic, that unrelated scopes do not collide, that the index refuses the
duplicate, and that the sequential shape of the race is refused with the
correct remaining figure.

---

## 14. Budget control policy

Company default with a per-project override. That is the whole precedence
chain; a third level keyed on cost code was considered and rejected, because
cost codes belong to Construction and a policy whose depth changed with the
installed modules would be worse than one nobody can override that finely.

| Policy | Behaviour |
|---|---|
| `none` | no reservation at all |
| `warn` | reserve, record the overage as evidence, continue |
| `approval_required` | reserve only where authority has already been granted |
| `block` | refuse the approval |

**The defaults are `warn` and (for purchasing) `optional`, and neither refuses
anything.** That is a decision about upgrades, not a view about good
governance: existing databases have live requisitions and live purchase orders,
and shipping a version that starts refusing them the moment it is installed
takes an operational decision away from the people whose operation it is.
What the defaults *do* provide from the first minute is that every approval
reserves, every position is computed, and every over-budget approval leaves an
exception record. The numbers are true before anybody is stopped by them.

Nothing in any policy increases a Construction budget. A project that needs
more money needs a change order (`test_f`).

---

## 15. Over-budget exceptions

`realestate.procurement.control.exception` — project, requisition or purchase
order, cost code, requested amount, available amount, overage, the policy in
force **as text** (so a later configuration change cannot rewrite what the rule
was on the day), the control position as the approver read it, the reason,
who asked, who decided, and when.

Four types: `over_budget`, `direct_purchase`, `amount_delta` and
`insufficient_data`. Four states, of which one is deliberately not called
approved:

* `noted` — the policy was WARN and nobody had to agree. Calling this
  "approved" would put a decision in the register that nobody took.
* `requested` / `approved` / `rejected` — a real decision, with a required
  reason on refusal.

A decided exception cannot be deleted. Overrides live in a register that can be
listed, counted, filtered and audited — not in a chatter line saying "approved
by phone", which names neither the amount nor the shortfall.

**Change-order-funded procurement.** Where a requisition comes from an approved
Construction change order, the change order is recorded as evidence and context
on the exception. The authoritative budget remains Construction's, and it moves
when the change order is *implemented* — not when it is cited here. Adding the
change-order value a second time inside Procurement is exactly the
double-count this milestone exists to prevent.

---

## 16. Approval matrix

`realestate.procurement.approval.rule` matches on company, amount bracket,
project, procurement type, priority and budget status. Every dimension is
optional and an unset dimension matches everything, so one row is a working
configuration. The amount compared is the **tax-exclusive control amount in
company currency**, so a USD request is converted before it meets an EGP
threshold.

`realestate.procurement.approval.step` is the evidence, and M3H's requirement
is that it must never change again. Each step snapshots the rule **name**, the
approver group, the named approver, the amount basis and a fingerprint of the
commercial basis. A rule renamed, re-bracketed or archived next quarter cannot
alter the answer to "who approved the 3,000,000 of concrete in March"
(`test_a_step_keeps_the_authority_it_was_raised_under`).

**Basis change invalidates approval.** `_basis_hash()` fingerprints project,
company, currency, procurement type, revision and every line's quantity,
amount and coding. A pending step whose basis has moved refuses the decision
rather than recording agreement to something nobody read. It is a change
detector, not a security device — the alternative, comparing field by field at
decision time, drifts the first time somebody adds a field.

**Decisions.** `pending / approved / rejected / skipped / cancelled`. Rejection
requires a reason, preserves every earlier approval, and puts the requisition
into a real `rejected` state — Phase 0 could only express refusal by returning
the request to draft, which reads exactly like the requester changing their
mind. Resubmission opens a new cycle beside the rejected one rather than
overwriting it.

**Sequence.** A later step cannot decide before an earlier one has
(`test_steps_are_decided_in_sequence`). Rules sharing a sequence are parallel.
No BPM engine was built.

**Activities.** A step that names a person gets exactly one activity, created
once and completed on decision. Group steps get none deliberately: an activity
belongs to a person, so a step approvable by any of twelve buyers would mean
twelve activities and eleven of them stale the moment one is decided. Those are
worked from the register's *Awaiting Approval* filter, which is one list that is
always right.

---

## 17. Maker / checker

Phase 0's check asked whether the user was in an approver group. That is a
question about capability; separation of duties is a question about identity —
and in a company small enough that everybody is in the approver group,
self-approval is both most likely and least visible.

`_check_not_self_approval()` compares `env.user` against the requester **and**
the record's creator, server-side, on every approval path including the
legacy single-approver one. The decision-maker is always `env.user`; nothing
reads an `approved_by` supplied by the caller
(`test_h3_the_decision_maker_is_the_session_user`).

A company may allow self-approval below a stated limit —
`procurement_allow_self_approval` and `procurement_self_approval_limit`, both
**off and zero by default**. That is a decision somebody makes in
configuration, not a default and not an accident. `test_h` proves the refusal,
`test_h2` proves the bounded exception, and the Phase 0 defect test is now the
positive regression.

The same rule applies to exceptions: whoever requested an override cannot
grant it.

---

## 18. Urgent procurement

Phase 0: `action_submit()` promoted an urgent request straight to approved and
stamped the requester as its approver. The requester owns the priority field,
so the requester decided whether their own request needed approving.

That branch is gone. Priority is now a **matching dimension** in the approval
matrix: a company can require an emergency approver for urgent demand, and
there is no rule shape that requires fewer. Urgent demand meets every budget
control, reserves nothing before approval, and confirms nothing
(`test_4_urgent_carries_no_authority`, `test_i`, `test_i2`).

Urgency still does what it should: it sorts to the top of the register, filters
on its own, and feeds the required-on-site dates that drive RFQ deadlines. M24's
full emergency-procurement workflow is not built; M3 removes the bypass.

---

## 19. Direct purchase-order governance

Phase 0's widest bypass: anybody who could confirm a purchase order could
commit a construction budget with no requisition, approval, enquiry or award.
Procurement was, in effect, optional.

| Policy | Behaviour for a **project-coded** order |
|---|---|
| `optional` | confirms exactly as standard Odoo always did |
| `controlled` | needs an approved requisition **or** an authorised direct-purchase exception |
| `required` | needs an approved requisition |

Company default with a project override, exactly as for budget policy.

**What counts as project-coded**: the order names a construction project, or a
line carries a cost code, or a line links to a requisition. Office stationery
bought by the same company through the same Purchase app is nobody's
construction commitment and is never gated (`test_k2`) — gating it because the
module happens to be installed is how governance becomes something people route
around rather than use.

**Direct purchase is not automatic approval.** Purchase Manager rights are
Odoo's permission to confirm an order. They are not ATMTA's authority to commit
a project's budget, and the two are kept apart: the exception is a record a
procurement manager decides, with a reason on it.

**The default is `optional`, and that is a real limitation.** Out of the box the
Phase 0 direct-PO bypass is closed *by configuration*, not by default. The
alternative — shipping `controlled` — would refuse confirmation on every
project purchase order path an existing installation depends on, including the
ones Construction's own suite exercises, on the day of an upgrade. §57 states
the consequence plainly and §27 says which switch to throw.

---

## 20. The purchase confirmation boundary

The gate is in `purchase.order.button_confirm()`, on the server, before
`super()`:

```
    company · project · policy · procurement authorisation · coding
    completeness · approval status · active reservation · amount consistency
    · exception where applicable
```

It is server-side because Phase 0's finding was never that a button was visible
to the wrong people — RPC, imports, scheduled actions and other modules all
reach commitment through this method (`test_j2`).

Standard Odoo purchasing is **not** replaced. `purchase.order`,
`button_confirm()`, receipt generation and vendor-bill behaviour are untouched;
an authorised order confirms exactly as it always did. What M3 adds is the
question asked immediately before — which matters because native confirmation
is the point that turns an enquiry into an operational order and generates
downstream receipts.

M3 itself still confirms nothing. The gate only prevents a bypass when some
other legitimate user or process confirms. Which vendor should win is M5–M7's
question.

---

## 21. Reservation → commitment

The hardest transaction in the milestone, and the one with no correct half.

```
    BEFORE   reservation 3,000,000 · commitment 0
    AFTER    reservation converted · commitment 3,000,000
    NEVER    reservation 3,000,000 · commitment 3,000,000
```

Order of operations: gate → `super().button_confirm()` → convert. Commitment
exists once Odoo says the order is confirmed, and the reservation stops
consuming capacity in the **same transaction**. If the confirmation rolls back
the conversion goes with it; if the conversion raises, the confirmation rolls
back too. Nothing commits manually and there is no intermediate state anybody
can observe.

**The three amounts are routinely different**, and the arithmetic is explicit:

| Case | Reserved | Ordered | Result |
|---|---|---|---|
| Equal | 3,000,000 | 3,000,000 | converted 3,000,000, active 0 |
| Under (`test_n`) | 3,000,000 | 2,700,000 | converted 2,700,000, **300,000 released**, commitment 2,700,000 |
| Partial (`test_d`) | 1,000,000 | 600,000 | converted 600,000, **400,000 still reserved** |
| Over (`test_m`) | 3,000,000 | 3,500,000 | **refused** unless tolerance or exception |

The under-reservation case releases the difference at confirmation rather than
at the next month-end review: that 300,000 belongs back in the project's
availability the moment the order is signed.

**Cancelling reverses it.** Construction reads commitment from confirmed
orders, so a cancelled order stops being one immediately. Without the reversal
the demand would sit in neither control stage — not reserved, not committed,
and absent from availability while still needing to be bought. That is the same
double-count defect with the sign flipped, and it is easier to miss because the
number gets smaller.

---

## 22. Partial conversion and split sourcing

`amount_reserved`, `amount_converted`, `amount_released` and `amount_active`
are separate, and conversions are their own records
(`realestate.procurement.reservation.conversion`) rather than a
`converted_by_po_id` field — a single foreign key could only ever name one of
two orders that consumed the same authorisation.

```
    Reserved            1,000,000
    Converted (PO A)      600,000  → Construction commitment
    Active                400,000  → still consuming capacity
    Combined exposure   1,000,000  (not 1,600,000)
```

`test_d2` confirms a second order for the remaining 40 units and the reservation
closes at exactly 1,000,000 converted across two conversion rows. Cumulative
conversion above the authorised amount is refused by the engine
(`test_d3`), with currency rounding respected.

No tender or split-award interface was built. The architecture supports it; M5–M7
own the decision.

---

## 23. Approval tolerance

`procurement_amount_tolerance_pct` and `procurement_amount_tolerance_amount`,
per company, **both zero by default**; the larger of the two applies. There is
no hard-coded 5% or 10% anywhere in the module — whichever number were chosen
would be somebody's policy adopted silently.

Above tolerance, confirmation is refused until the requisition is revised and
re-approved or an `amount_delta` exception is authorised. An approval covers an
amount, not a document number: letting the extra 500,000 through because the
requisition it came from was approved would make approval a formality attached
to a piece of paper.

---

## 24. Multi-currency

Every control comparison happens in **company currency**, because that is what
Construction's budget and commitment are stated in. The reservation keeps the
source currency, the source amount, the company-currency control amount and the
rate date, so a figure can be reconciled later rather than merely believed.

At confirmation the control amount is re-evaluated from the order's own
currency and date rather than assuming the reservation's company amount is
final — an approval in USD and an order placed a month later are not the same
number. No FX is posted by this module; that is Accounting's.

---

## 25. Multi-company

Strict, and enforced three ways: `company_id` on every control model,
`@api.constrains` on the reservation and the exception, and **global** record
rules on the reservation, the conversion, the exception, the approval step and
the approval rule. A company A requisition cannot reserve company B capacity,
be governed by company B's rules, or be authorised by an exception raised in
company B (`test_a_reservation_cannot_cross_a_company_boundary`,
`test_an_exception_cannot_authorise_across_companies`).

The approval matrix now matches on the **requisition's** company. Phase 0
matched on `self.env.company` — whichever company the approver happened to have
active — and the converted Phase 0 test now asserts the opposite.

---

## 26. Security

| Role | Can | Cannot |
|---|---|---|
| Requester | raise and submit demand | approve it, create a reservation, release one |
| Buyer | source approved demand, raise RFQs | approve demand, grant an exception |
| Approver | decide assigned steps | activate or release reservations |
| Manager | configure policy and rules, decide exceptions, activate/release | — |

None of these grants Accounting, Inventory or Construction administration.

Every check is server-side and reached by calling the method, not by hiding a
button: `action_submit`, `action_approve`, `action_reject`, `action_reserve`,
the release wizard, exception approval and the purchase-confirmation gate all
raise for the wrong user regardless of how they are called
(`test_m3_security.py`).

**Two narrow elevations, both documented in the code where they occur:**

1. `_construction_maps()` reads Construction's budget and commitment with
   `sudo()`. A requester needs the *answer* — does this demand fit — without
   acquiring rights over a project's commercially sensitive baseline. Solved on
   the Procurement side deliberately: widening Construction's access rules
   would hand every procurement user the underlying budget records, which is a
   far bigger door.
2. The reservation reads and writes in the conversion path are elevated per
   record, so a purchase user confirming an authorised order does not need
   rights to hand-edit control records. It is not a `sudo()` around
   `button_confirm()`.

**Project access** still follows Construction's team semantics, unchanged from
M2. Approval authority does not open every project: an approver given no
project rights cannot browse projects sideways through a procurement role
(`test_approval_authority_does_not_open_every_project`).

---

## 27. Migration

```
    HISTORIC CONFIRMED PURCHASE ORDERS ARE ALREADY COMMITMENT.
    THEY NEVER RECEIVE A RESERVATION.
```

A reservation created on top of an existing commitment for the same money is
the 6,000,000-for-3,000,000 error, made during an upgrade where nobody is
watching. Everything else follows from that.

**What the migration does**: classifies every requisition, carries existing
approval steps onto the decision field, copies what can still be known of the
rule authority onto historic steps, and writes the permissive policies where a
column is null. Nothing is reserved, released, confirmed or cancelled.

| Classification | Meaning |
|---|---|
| `legacy_confirmed_po` | already commitment — never reserved |
| `sourcing_draft_rfq` | enquiry exists; may need reservation before confirmation |
| `approved_unordered` | candidate for reservation, if the company activates it |
| `not_applicable` | draft, cancelled or rejected — no live demand |

Two orthogonal evidence flags rather than lifecycle states, because a request
can easily be both: `legacy_urgent_bypass` and `legacy_self_approved`. Both were
legitimate under the rules in force at the time. Neither is reversed —
retroactively un-approving them to flatter the new version would be rewriting
commercial history.

`LEGACY_DIRECT_PO` is carried on the purchase order itself as
`re_governance_status` (`linked` / `direct` / `not_project`), computed rather
than stamped once, so a project order raised outside Procurement is findable at
any time and not only on the day of the upgrade. No historic order was
cancelled.

**Rollout is a decision, not a side effect.**
`action_activate_procurement_reservations()` stages **draft** reservations for
approved, unordered demand so a manager can read the list in the register
before a single unit of capacity moves; activating them is a second, deliberate
action, restricted to a procurement manager. Nothing about an upgrade blocks
budget that was not blocked the day before.

**Idempotency** is by construction — a line that already has an active
reservation is skipped — and verified by re-running the real migration against
an already-migrated database (§32).

---

## 28. Registers and data quality

**Requisition register** — reference, project, requester, buyer, procurement
type, required date, amount, coding status, WBS and cost-code coverage, RFQ
count, ordered count, revision, migration status. Filters: My Requests, Urgent,
Draft, Awaiting Approval, Approved, Sourcing, Ordered, Done, Missing Cost Code,
Missing WBS, Data Quality Warnings, Long Lead, Overdue Required Date.

**Procurement plan register** — project, plan, revision, period, line count,
long-lead count, amount, state; plus a planned-items list and pivot with filters
for long lead, not-yet-requested and no-lead-time-known.

**Data-quality warnings** are deterministic and never block: missing project,
uncoded lines, missing WBS, unknown estimates, missing UoM, no required-on-site
date once past draft, and **lines whose coding failed to reach the purchase
document**. That last one matters most: silence there would mean a correctly
coded requisition whose order quietly lost the code, reported as fine.

`coding_status` shows `Unassigned` explicitly. The row is never dropped from
Construction reporting — an uncoded requisition appears as uncoded, not as an
absence.

### M3 registers

**Approval register** (`realestate.procurement.approval.step`, list + pivot) —
requisition, project, rule, approver group, named approver, amount basis,
requested on, days waiting, decision, decider, decision date, comment. Filters:
Awaiting Approval, My Approvals, Approved, Rejected, Waiting Over A Week,
Urgent, Over Budget, Reservation Missing.

**Reservation register** (list + pivot) — reference, requisition, line
description, project, WBS, cost code, reserved, converted, released, active,
source amount and currency, control status, the orders that consumed it, state.
Every total reconciles to the availability service because `amount_active` is
shown beside the two figures it is derived from; a register that shows only a
net number cannot be checked against anything.

**Exception register** — type, project, requisition or order, requested,
available, overage, requester, approver, state, with filters per type and per
decision.

### M3 data-quality findings

Added to the requisition's existing deterministic warnings, and to a per-record
`anomaly_note` on the reservation. Stated, never auto-corrected — every one of
them is a disagreement between two systems, and the useful response is a person
looking at it rather than this module choosing which system it prefers:

* approved demand with no reservation under a policy that requires one;
* a cancelled requisition still holding capacity;
* a converted reservation with no confirmed purchase line behind it;
* converted above what was ever reserved;
* an expired reservation still active;
* a requisition raised and approved by the same person.

`re_governance_status` on the purchase order surfaces the confirmed-without-a-
requisition case, and `approval_control_status` surfaces demand approved over
budget.

---

## 29. Company and project scoping

Every M2 model carries `company_id`. Project → plan → request → lines → RFQ must
stay inside one company, enforced by constraint and `check_company`, not by view
domains.

Global company record rules now exist on the request, the request line, the
revision, the plan and the plan line — where Phase 0 found **no record rules at
all**. On top of them, a requester sees what they raised and a buyer sees the
queue; the two rules are separate because same-model rules from different groups
are OR-ed, so a buyer is widened by the second rather than narrowed by the first.

M3 adds global company rules on the reservation, the conversion, the exception,
the approval step and the approval rule — see §25.

**Project access follows Construction**, and Procurement invents nothing. There
is no `allowed_procurement_project_ids` on users. A project with a named
construction team belongs to that team; a project without one is open; a
construction manager sees everything — the same answer Construction's own record
rules already give. Enforced server-side (`test_20`, `test_20b`).

---

## 30. Performance

Coverage counts, ordered quantities, RFQ and order counts, plan totals and the
data-quality flag are **stored computes with explicit dependencies** — no
per-line search on read. The Construction extension re-declares the coverage
compute's dependencies so it refreshes when a line is coded, which Procurement
cannot express because it cannot name Construction's fields.

Phase 0's two loose ends are gone: `_compute_purchase_orders` no longer walks a
per-record chain of `po_line_id.order_id`, and the receipt rollup no longer runs
from inside a compute.

**M3.** A requisition with forty lines must not ask Construction for the budget
forty times, so the three source maps are read **once per project** and the
positions assembled in Python (`positions_by_cost_code()`); active reservations
are aggregated with `_read_group` by cost code rather than by reading records.
Approval rules are resolved in one bounded query per requisition and matched in
Python. Reservation amounts, conversion totals, request-level reserved and
converted amounts and the approval-aging fields are stored computes with
explicit dependencies. Locks are per company/project/cost-code, so unrelated
scopes never serialise against each other.

---

## 31. Tests

| Suite | Tests | Notes |
|---|---|---|
| `test_phase0_baseline.py` | 26 | All kept. Fifteen former `test_defect_…` are now positive regressions; two remain defect tests |
| `test_m2_invariants.py` | 4 | The four M2 invariants |
| `test_m2_requisition.py` | 22 | The M2 numbered matrix |
| `test_m2_security.py` | 9 | Buyer, requester, company, project access |
| `test_m3_invariants.py` | 5 | The five M3 invariants, written before the models |
| `test_m3_reservation.py` | 17 | Tests A–D, N, O, P, R, multi-currency and every release path |
| `test_m3_policy.py` | 13 | Tests E, F, G, M and Rule 4 |
| `test_m3_approval.py` | 15 | Tests H, I and the matrix |
| `test_m3_governance.py` | 10 | Tests J, K, L and the confirmation gate |
| `test_m3_concurrency.py` | 9 | Locking, ordering, the unique index |
| `test_m3_migration.py` | 6 | Test Q, classification, idempotency, rollout |
| `test_m3_security.py` | 11 | Roles, server-side authority, multi-company |
| **Total** | **147** | 0 → 26 (Phase 0) → 61 (M2) → **147** (M3) |

No test was deleted. Six Phase 0 / M2 tests were **converted**, each because
M3 actually fixed what they recorded:

| Was | Now | Because |
|---|---|---|
| `test_there_is_no_reservation_layer_to_double_count_yet` | `test_the_reservation_layer_never_double_counts_with_commitment` | The layer exists; the test now pins its invariant |
| `test_defect_marking_a_request_urgent_skips_approval_entirely` | `test_urgency_no_longer_decides_whether_approval_applies` | The urgent bypass is gone |
| `test_defect_a_requester_can_approve_their_own_request` | `test_a_requester_can_no_longer_approve_their_own_request` | Maker/checker is identity-based and server-side |
| `test_defect_approval_rules_are_scoped_to_the_active_company` | `test_approval_rules_are_scoped_to_the_requisition_company` | The matrix matches the requisition's company |
| `test_defect_nothing_stops_two_requests_consuming_the_same_budget` | `test_two_requests_can_no_longer_quietly_plan_the_same_budget` | Demand consumes capacity as it is approved, and BLOCK refuses the second |
| `test_16b_ordered_quantity_follows_confirmation` | same test, `partially_ordered` asserted | M3P distinguishes partly ordered from ordered |

Two Phase 0 defect tests are **kept as defect tests**, unconverted and
unaltered, because what they record is still true:

* `test_defect_a_purchase_user_can_commit_without_any_requisition` — under the
  default `optional` policy that is still exactly what happens. Converting it
  would claim a control the default configuration does not have; `test_j`
  proves the same scenario is refused once governance is switched on.
* `test_defect_a_buyer_coding_a_line_hits_an_access_error` — a purchase user
  with no Construction read still cannot code a purchase line (§57).

### The M3 matrix

| Test | Subject | Where |
|---|---|---|
| A | Reservation is not commitment | `test_a`, `test_1` |
| B | Draft RFQ preserves the reservation | `test_b` |
| C | Conversion on confirmation | `test_c`, `test_2` |
| D | Partial conversion | `test_d`, `test_d2`, `test_d3` |
| E | Two requests, one budget | `test_e`, `test_e2`, `test_3` |
| F | Warn policy | `test_f` |
| G | Approval-required policy | `test_g` |
| H | Self-approval | `test_h`, `test_h2`, `test_h3` |
| I | Urgent has no authority | `test_i`, `test_i2`, `test_4` |
| J | Direct PO bypass blocked | `test_j`, `test_j2`, `test_j3`, `test_5` |
| K | Optional policy leaves Odoo alone | `test_k`, `test_k2` |
| L | Direct purchase exception | `test_l`, `test_l2` |
| M | PO above reservation | `test_m`, `test_m2`, `test_m3` |
| N | PO below reservation | `test_n` |
| O | Multi-code demand | `test_o` |
| P | Tax-exclusive basis | `test_p` |
| Q | Migration leaves history alone | `test_q` |
| R | No accounting | `test_r` |

---

## 32. Gate results

| Gate | Result |
|---|---|
| Procurement suite (Phase 0 + M2 + M3) | **147 tests, 0 failed, 0 errors** |
| Construction frozen suite | **563 tests, 0 failed, 0 errors** — exact |
| Purchase integration (RFQ → confirm → receipt → cancel) | in `test_m3_reservation.py` and `test_m3_governance.py` |
| Budget / reservation concurrency | `test_m3_concurrency.py`, 9 tests — §13 says exactly what is and is not proved |
| Direct-PO governance | `test_m3_governance.py`, 10 tests across all three policies |
| Company and project security | `test_m3_security.py`, 11 tests |
| Legacy migration on a seeded pre-M3 database | correct — see below |
| Migration retry / idempotency | identical result, no duplicates |
| **Fresh 14-module install** | 14/14 modules installed; **2246 tests, 3 failed, 0 errors** |
| **Full 14-module upgrade** | **710 tests, 0 failed, 0 errors** on a database migrated from the pre-M3 schema (563 + 147) |

Per-module counts on the fresh install:

| Module | Tests |
|---|---|
| `atmta_real_estate` | 299 |
| `real_estate_brokerage` | 352 |
| `real_estate_checks` | 325 |
| **`real_estate_construction`** | **563** (frozen, exact) |
| `real_estate_developer` | 247 |
| `real_estate_maquette` | 291 |
| `real_estate_plan` | 15 |
| `real_estate_portal` | 7 |
| **`real_estate_procurement`** | **147** |
| **Total** | **2246** |

Procurement tests: **0 → 26 (Phase 0) → 61 (M2) → 147 (M3)**. Full suite:
2160 → **2246**, which is 2160 + 86 exactly — every new test is a Procurement
test and no other module's count moved.

### The three failures on the fresh install, named

All three are the same RTL tour assertion, in three modules M3 does not touch:

```
    atmta_real_estate     TestDashboardRTL.test_dashboard_rtl
    real_estate_brokerage TestBrowserRTL.test_listings_render_right_to_left
    real_estate_checks    TestTreasuryDashboardRTL.test_dashboard_rtl
```

Each fails with *"Dashboard computed direction is ltr, not rtl"*. They are
**pre-existing in this working tree**, not caused by M3, and that is asserted
rather than assumed: `TestTreasuryDashboardRTL` was re-run in isolation on a
different, older database and fails there identically. Procurement has no RTL
tests, Construction is green at 563, and nothing in M3 touches assets,
languages or dashboards.

Two further points of honesty about this run:

* The install-and-test invocation was killed by its own 90-minute wrapper
  timeout while still inside **core Odoo's** JavaScript suites, before it
  reached any ATMTA module. The install itself had completed — 14 of 14
  modules installed — so the suite's tests were then run as a second
  invocation against that same freshly created database. The 2246 above is
  that second run.
* Two combined-upgrade runs showed five failures in Construction's browser
  and HOOT tests, every one of them `odoo.isTourReady(...) was always falsy`.
  My first explanation — two Chrome-driven suites competing for the machine —
  was **wrong**, and re-running alone disproved it. The real cause was mine:
  the migrated database had been created with `createdb -T`, which copies the
  rows but not Odoo's **filestore**, so the generated asset bundles the tours
  need were absent from disk (77 files against 623 on a normal database).
  With the filestore restored the same five classes pass on that database,
  and the full combined run is 710/710. The lesson is recorded because the
  failure looked exactly like a code regression and was not one.

### Legacy migration, verified against real data

A pre-M3 database was copied, seeded with four legacy requisitions covering
each classification, then migrated:

```
    classified 4 requisition(s):
        approved_unordered=2 · legacy_confirmed_po=1 · sourcing_draft_rfq=1
    2 approved requisition(s) are candidates for reservation. None created.
```

Purchase documents before and after: **6 draft, 4 confirmed, 1 sent** — byte
for byte the same. Reservations created: **0**. Exceptions created: **0**. The
urgent-bypassed legacy record kept its approval and gained a flag.

The migration was then **re-run against the already-migrated database** by
resetting the installed version, and produced identical counts with no
duplicate classification, reservation, exception or approval step.

No run in this table was interrupted, port-conflicted or log-overwritten, and
where a run *was* compromised — the concurrent one — it is named above and its
number is not used.

Construction production code changed in two places in M2 (§8), both one line.
M3 added three additive things to Construction (§8) and changed no formula, no
record and no test. The frozen suite is unchanged in count and green.

---

## 33. Vendor master boundary — why `res.partner` stays authoritative

M4 creates no `realestate.vendor`. Every qualification, restriction and
governance profile points at `res.partner`, and the partner keeps the name,
the address, the tax identity, the bank details and the commercial
relationship exactly as Accounting, Sales and Purchase already use them.

What the governance layer owns is a decision *about* that partner:

```
    res.partner                  who they are            (shared, one record)
    vendor.profile               where they stand with   (per company)
                                 Procurement
    vendor.qualification         what they may be         (per company, trade,
                                 sourced for              project and date)
    vendor.restriction           what they may not be     (dated, approved,
                                                          reversible)
```

`test_01_the_vendor_master_is_not_duplicated` asserts both halves: the
qualification's `partner_id` is a `res.partner`, and the profile carries none
of `street`, `phone`, `email`, `vat`, `bank_ids` or `country_id`. The two
additions to `res.partner` are pointers, not governance —
`procurement_profile_id` (computed, unstored, because the profile is per
company and a stored field on a shared partner would show one company's
answer to another) and `procurement_vendor_class` (what the migration made of
this supplier, never an approval).

Odoo's own vendor commercial data — `product.supplierinfo` prices, minimum
quantities, lead times, vendor references — is untouched and unduplicated.
Those answer *what will it cost*. M4 answers *may we buy at all*.
`test_a_price_data_still_exists_and_is_untouched` checks that answering the
second question does not disturb the first.

---

## 34. Contractor vs vendor — the audit, and the decision

`realestate.contractor` already existed in the frozen Construction module and
the brief asked six questions about it before any code was written.

| Question | Answer |
|---|---|
| Is it backed by `res.partner`? | **Yes.** `partner_id` is required with `ondelete='restrict'`, and `contact_email` / `contact_phone` are `related` fields on it. It is not a second identity master |
| Does it represent construction commercial identity only? | **Yes.** Subcontract PO, payment certificates, retention held and released, milestones, an auto-created service product, and the committed/billed/paid rollups |
| Does it hold qualification information already? | **Fragments, not decisions.** `specialization` (nine hard-coded values), `license_number` (free text) and `rating` (A/B/C). No dates, no evidence, no company scope, no per-trade scope, no approval and no history. That is a label on a card, not a qualification |
| Can one supplier be both? | **Yes**, and nothing prevents it. A contractor's `partner_id` is an ordinary supplier partner who may also appear on material purchase orders |
| Could vendor governance safely link to it? | **It already does, through the partner.** A qualification keyed on `res.partner` covers the contractor's partner automatically |
| Would merging break frozen Construction? | **Yes.** `realestate.contractor` carries 563 tests, `ondelete='restrict'` foreign keys from contract packages, payment certificates and retention, and a stored `related` chain through `partner_id` |

**Decision: leave Construction's contractor architecture entirely alone.** No
merge, no deletion, no field added to it, no foreign key pointing at it. This
is also forced by the dependency direction that M2 and M3 were careful to
preserve — `real_estate_construction` **depends on** `real_estate_procurement`,
so Procurement cannot reference `realestate.contractor` without inverting it.

The integration is therefore the partner itself, and
`test_01_contractor_and_vendor_stay_separate` pins it: a contractor and a
qualification share `partner_id`, neither model references the other, and the
qualification model has no `contractor_id`.

**M4 required no Construction change at all.** `git diff` against the M3
commit shows zero lines changed in `real_estate_construction`, and its frozen
suite is unchanged at 563.

---

## 35. Governance profile

`realestate.procurement.vendor.profile`, one per **partner and company**,
unique-constrained on the pair. Per company because a vendor active in one
company and never assessed in another is two different governance situations
about one supplier, and a single record could only hold one of them.

Status is deliberately separate from qualification:

```
    DRAFT → UNDER_REVIEW → ACTIVE → RESTRICTED → SUSPENDED → INACTIVE
```

`ACTIVE` means onboarded and not suspended. It says nothing whatever about
which trades the vendor may be sourced for — that is per-trade, dated and
evidenced. The form makes this hard to misread: the "Qualified For" list is
computed from approved qualifications valid today and is read-only, because a
tick-box trade list beside an ACTIVE status is exactly how "approved" starts
meaning "approved for everything".

**Prospective / sourcing-eligible / award-eligible** are answered per question
by the eligibility service's `purpose` argument rather than stored as a third
status field. The distinction is real — a vendor mid-assessment may be
considered but not invited; a vendor qualified with a pre-award condition may
be invited but not ordered from — and a stored field would be a fourth number
that is wrong for as long as it takes something to recompute it.

Profiles are created **one at a time**, when somebody starts governing a
vendor (`action_open_vendor_governance`, or raising a qualification or
restriction). The migration makes none: a database with 4,000 legacy suppliers
would gain 4,000 empty records saying nothing.
`test_19_a_profile_is_created_when_somebody_starts_governing` checks that
pressing the button twice still yields one.

M4T's conflict metadata is present and deliberately small —
`potential_related_party`, `conflict_review_required`,
`conflict_review_complete` and a note. No workflow: M26 owns that, and this is
the dimension an award approval will later read.

M4S duplicate detection is a computed warning on exact `vat` or `email`
matches. No fuzzy matching, no automatic merge, and the form says so on its
face — merging partners belongs to whoever owns the contact master.

M4R is respected: no bank or payment verification exists here, and no bank
field is exposed to procurement users. Finance owns payment master controls.

---

## 36. Qualification areas

`realestate.procurement.qualification.area` — configurable headings, shipped
with fourteen generic ones (Legal & Registration, Financial Capacity,
Technical Capability, Experience, References, Quality, HSE, Insurance,
Certifications, Delivery & Logistics, Commercial, Sustainability, Information
Security, Other).

No country's paperwork is baked in. "Legal & Registration" is true everywhere;
"commercial registration certificate issued by GAFI" is true in one place and
meaningless in the next, so the specific documents live on template
requirements that a company writes for itself.

`company_id` is left empty on the shipped areas so every company may use the
vocabulary. Sharing a heading shares nothing about any vendor — decisions are
company-scoped and the record rules keep them so.

Two ship **confidential**: Financial Capacity and References. Those are the
evidence people most often regret having made readable to the whole
procurement team.

---

## 37. Qualification requirements

`realestate.procurement.qualification.requirement`, as template lines.

Seven answer types — yes/no, document, date/expiry, numeric, selection, score,
text evidence — and three obligations that are genuinely different powers:

```
    MANDATORY       must be answered and passed
    SCORED          contributes to the total
    INFORMATIONAL   evidence and nothing else
```

**`blocking` is a fourth field, not a synonym for mandatory.** Mandatory says
the question must be answered; blocking says failing it ends the matter
regardless of the score. Conflating them is how an 85 %-scoring vendor with no
valid insurance gets approved — which is test 5, and it passes.

Document requirements track attachment, issue date, expiry date, issuer,
document number, `verified_by`, `verified_on` and a verification result. Odoo
attachments are used; there is no second document store.

`expiry_sensitive` is opt-in per requirement (M4V). Only requirements marked
there are re-checked at eligibility time. Making every attachment
expiry-sensitive would render the register unusable within a month, and a
company-profile PDF does not go stale the way an insurance certificate does.

A requirement that was not met can be **waived** — with a reason and a named
person, both required, and never by the assessor themselves unless the company
has explicitly allowed self-approval. `test_05_a_waiver_needs_a_reason_and_a_person`
covers all three.

---

## 38. Templates and versioning

`realestate.procurement.qualification.template`, applicable by company, trade
and vendor type (using the partner tags this suite already had).

A qualification is an assertion about a moment: *given these questions, this
vendor passed*. If the questions can be edited afterwards, the assertion
quietly changes meaning — a vendor approved under a template that never asked
for insurance appears a year later to have been approved with insurance on
file.

So **a template used by anything past draft is frozen**, and
`action_new_version()` copies it forward, marks the old one obsolete and links
the two. The guard is enforced in two places, because guarding only the
template left the obvious way round it open: `QualificationTemplate.write()`
refuses changes to what it asks, and `QualificationRequirement.write/unlink`
refuse too. Renaming and retiring stay possible — they change no meaning.
Deleting a template with assessments behind it is refused outright.

Belt and braces: **every requirement is additionally snapshotted onto the
response** (§39), so even the frozen template is not load-bearing for reading
history. `test_responses_snapshot_the_requirement` deletes the source
requirement and reads the response back intact.

---

## 39. Assessment workflow

`realestate.procurement.vendor.qualification` — one decision for one
vendor + company + trade + optional project, with an assessment date, an
effective date, an expiry date and an approval date.

```
    DRAFT → SUBMITTED → UNDER_REVIEW → ASSESSED → PENDING_APPROVAL → APPROVED
                                                       ↓
                        REJECTED   CANCELLED   EXPIRED   SUPERSEDED
```

Every transition is a server-side action. There is no way to type a status.

**State and result are two fields**, because they answer different questions.
`state = approved, result = qualified_with_conditions` is an ordinary outcome:
the review is finished and the answer is "yes, but". One field would force
that to be recorded as an approval plus a note, and notes cannot be filtered,
counted or enforced.

Results: `qualified`, `qualified_with_conditions`, `not_qualified`,
`pending_information`. A `not_qualified` result may be approved and become
current — approving means "this conclusion stands", and a recorded, findable
"no" is more useful than an abandoned draft.

**Responses are snapshots.** At creation the template's requirements are
copied onto `realestate.procurement.qualification.response` with their name,
type, obligation, blocking flag, expiry sensitivity, confidentiality, weight,
maximum score, threshold and area. After that, reading the assessment never
consults the template again.

### Suspension is deliberately not a qualification state

The brief lists SUSPENDED among the possible states and this implementation
does not have it, on purpose. Suspending is a management act taken later,
usually for reasons that have nothing to do with the assessment. If it
rewrote the assessment's state, the record of what the assessor concluded
would be gone — and M4P separately requires that the qualification remain
stored as a valid historical assessment. So suspension is its own dated,
approved, reversible record (§44) and the eligibility service lets it outrank
a valid qualification without touching it. Test C asserts exactly this: after
suspension the qualification is still `approved`, still `qualified`, still
current, and still named in the eligibility answer beside the reason it does
not help.

### Reassessment

`action_reassess()` creates a new assessment on the latest active template
version, links it to the previous one, and carries evidence references
forward. **Verification is not carried forward for anything expiry-sensitive.**
An insurance certificate verified two years ago is not verified now, and
copying the tick would be the single most dangerous convenience in this
module. `test_08_reassessment_does_not_carry_forward_expiring_verification`
checks that the document number comes across and the verification does not.

The old assessment is immutable: `_DECIDED_FIELDS` (vendor, company, trade,
project, template, dates, result, score, approver) cannot be written once the
record is approved, expired or superseded, its responses cannot be edited, and
it cannot be deleted.

---

## 40. Scoring

Opt-in per template. Off means pass/fail — mandatory requirements decide and
no percentage is computed, because not every supplier needs a number.

When on, the arithmetic is written down in full on the record:

```
    weighted     = Σ (score × weight)      over scored requirements
    weighted_max = Σ (max_score × weight)
    score        = weighted / weighted_max × 100
```

`score_explanation` lists every contributing line with its numbers, the total,
the minimum required, and the reason for the result. There is no opaque risk
score anywhere in M4 and no model produces one.

The rules are applied in this order, and stated in that order in the
explanation:

1. a **blocking** requirement that failed → `not_qualified`, whatever the
   total says;
2. a **mandatory** requirement with no answer → `pending_information`, because
   nobody has decided anything yet;
3. a mandatory requirement that failed **without** being blocking → downgrade
   to `qualified_with_conditions`, with a structured condition naming it;
4. a scored template below its minimum → `not_qualified`.

Test 5 is rule 1: 90 % score, insurance failed, blocking → **Not Qualified**.

---

## 41. Conditional qualification

`realestate.procurement.qualification.condition`, in fields rather than prose:

```
    max_award_value    a value ceiling, in company currency
    project_limited    valid only on named projects
    category_limited   valid only for named trades
    date_limited       a narrower validity window
    pre_award_action   what must happen before an order
    warning            everything else, including auto-generated ones
```

A condition nobody can query is a condition nobody will honour. The
eligibility service returns them in its answer, so a buyer sees the 5,000,000
ceiling before inviting anybody, and M7's award check will read the same
field rather than parsing a sentence. Test 6 asserts the amount comes back
through the service; enforcing it at award is explicitly M7's and is not
claimed here.

A `max_award_value` of zero is refused by constraint — that is "not qualified"
said badly.

---

## 42. Approved Vendor List

**Derived, not stored.** There is no `is_approved_vendor` boolean anywhere and
no manually maintained membership table.

The AVL is a question with four parameters — company, trade, project, date —
and the honest way to answer a question with four parameters is to ask it.
`realestate.procurement.avl.report` takes all four plus a purpose, calls the
eligibility service, and returns one row per vendor naming **the qualification
that authorises it**. A row with no qualification behind it is visibly a row
with nothing behind it.

Excluded vendors are shown by default with the reason, because a list that
silently drops the suspended vendor cannot answer *why wasn't Vendor X
invited* — which is the question the screen exists for.

It ranks nobody. Qualified is not cheapest, and comparing quotations is M6's.

---

## 43. Eligibility service

One service, `realestate.procurement.vendor.eligibility`, and every screen and
gate asks it:

```python
check_vendor_eligibility(vendor, company, category, project, date, purpose)
    → { eligible, qualified, status, qualification_id, qualification_ref,
        result, score, valid_from, valid_to, conditions, blocking_reasons,
        warnings, restriction_ids, endorsement_id, policy, enforcing, ... }
```

### Eligible is not qualified

Two separate keys, and the distinction is the whole of UNKNOWN IS NOT
ELIGIBLE:

```
    qualified   a current, valid, approved assessment says so
    eligible    company policy allows them to take part today
```

Under OPTIONAL an unassessed vendor comes back `eligible=True,
qualified=False, status='no_qualification'`. Nobody has decided they are good;
the company has decided not to require the decision yet. One flag would turn
"we allow it for now" into "the system says they're approved".

### As-of date

Never implicitly today. Invitation, award and confirmation happen on different
days and the answer legitimately differs between them, so the date is an
argument; `date=None` means today, and that is a default rather than an
assumption baked into the arithmetic.

### Precedence

```
    1  governance status      inactive vendor                  → blocks
    2  restrictions in force  scoped by trade and project      → blocks
    3  general qualification  company + trade                  → policy decides
    4  project endorsement    where the project demands one    → policy decides
    5  document expiry        mandatory, expiry-sensitive      → policy decides
```

A project endorsement can never rescue a vendor from a company suspension: 1
and 2 return before 3 is reached, and
`test_04_an_endorsement_cannot_rescue_a_suspended_vendor` proves it.

### M5's hook

`get_eligible_vendors(...)` is the bulk form and `eligibility_snapshot(...)`
produces the flat, immutable dict M5 will store on a tender invitation —
partner, qualification reference, validity, conditions, status and reasons,
all primitives. M4 stores none of these because there is nothing yet to store
them on; it produces them in the shape that will be kept, so a tender opened
today can still print *this vendor was eligible at invitation, under
qualification QUAL/2026/00007, valid to 2027-03-01* after every one of those
facts has changed.
`test_15_a_snapshot_survives_everything_changing_afterwards` debars the vendor
and rewrites the approval date afterwards, and reads the snapshot back
unchanged.

---

## 44. Suspension and restriction

`realestate.procurement.vendor.restriction` — dated, reasoned, approved,
liftable, and never deleted once it has been in force.

Seven types, and they do not all do the same thing:

| Type | Blocks invitation | Blocks the order |
|---|---|---|
| Sourcing suspension | ✔ | ✔ |
| Award suspension | — | ✔ |
| Trade restriction (within its trade) | ✔ | ✔ |
| Project restriction (within its project) | ✔ | ✔ |
| Temporary hold | ✔ | ✔ |
| Debarment | ✔ | ✔ |
| Probation | — | — |

Probation blocks nothing by design: it is a recorded warning that travels with
the vendor, and a restriction that silently stopped sourcing while being
called "probation" would be worse than not having the type.

**A suspension blocks even under OPTIONAL policy.** Absence of qualification is
tolerated there because nobody has decided yet; a suspension is a decision.
`test_c_suspension_blocks_even_under_a_permissive_policy`.

**It touches nothing else.** No purchase order is cancelled, no bill reversed,
no qualification altered, no history rewritten. The affected open orders are
surfaced as a worklist on the restriction so somebody can decide about them —
deciding is a management act with money attached and is not this module's.
`test_09_a_suspension_does_not_touch_the_orders`.

Whether a restriction applies is answered **from its dates**, not from its
state flag, so a question about March gets March's answer whether or not the
nightly job has run — and a *lifted* restriction still applies to dates before
it was lifted. Lifting requires a reason; deleting one that has been in force
is refused.

---

## 45. Qualification policy

Company default, project override — the same chain M3 uses.

```
    OPTIONAL                qualification available, never required
    WARN                    record the gap and continue
    REQUIRED_FOR_SOURCING   must be eligible to be invited
    REQUIRED_FOR_AWARD      must be eligible to receive the order
```

### Why there is no fifth level

The brief offered REQUIRED_FOR_PO as a possibility. It is not shipped, because
with no Award document in the system until M7 it would enforce at exactly the
same moment as REQUIRED_FOR_AWARD — a setting that changed nothing, which is
worse than a gap somebody can see. REQUIRED_FOR_AWARD is enforced today at
purchase-order confirmation, the only award-like act that exists. When M7
introduces a formal award the check moves earlier and confirmation keeps this
one as the backstop.

### Default

**OPTIONAL**, and the migration confirms rather than assumes it (§49). An
upgrade must not start refusing purchases that were legal the day before.
`test_11_optional_is_the_shipped_default` and
`test_11_optional_policy_changes_nothing_for_a_legacy_vendor` hold the line.

### Where it bites

| Moment | Level that refuses | Method |
|---|---|---|
| Creating RFQs from a requisition | REQUIRED_FOR_SOURCING | `MaterialRequest._check_vendors_may_be_invited()` |
| Confirming a purchase order | REQUIRED_FOR_SOURCING, REQUIRED_FOR_AWARD | `PurchaseOrder._check_vendor_eligibility()` |

Both are server-side, before anything is created or committed. The
confirmation gate sits inside `button_confirm()` alongside M3's, so RPC,
imports, scheduled actions and other modules hit it too —
`test_the_gate_is_server_side_not_a_hidden_button` confirms through three
different entry points, including one carrying a `force_confirm` context that
does nothing.

**Scope is `is_realestate_po` and nothing wider.** Office stationery, IT
subscriptions and everything else this suite has no opinion about confirm
exactly as standard Odoo confirms them, under the strictest policy available —
`test_an_office_purchase_is_not_policed`.

A warning is never the reason something fails. This was found in testing: under
WARN the gate posts a note, Odoo refuses to post on behalf of a user with no
email address, and the note's failure was cancelling the confirmation it was
only commenting on. `post_governance_note()` now falls back to the system
partner and names the acting user in the text;
`test_a_warning_never_blocks_the_thing_it_warns_about` is the regression.

---

## 46. Trade taxonomy

Four classifications already existed and none of them was the one eligibility
needs:

| Existing | What it classifies | Why it does not fit |
|---|---|---|
| `product.category` | goods | A vendor qualified for *HVAC installation* is neither narrower nor wider than the *MEP* product category. Keying approval on the product tree means a vendor approved to supply ducting is approved to install it |
| `res.partner.category` | party type | Seven global tags, no hierarchy, no company, no dates. Useful as vendor type, not as trade eligibility |
| `realestate.contractor.specialization` | one Construction label | Construction-owned, single-valued, per contractor record, no company. Procurement cannot reference it without inverting the dependency |
| `contract.package.package_type` | subcontract scope | Same owner, same problem |

So `realestate.procurement.vendor.category`: a hierarchical trade tree with an
optional company, used only as the axis qualification is keyed on. It carries
an explicit `product_category_ids` mapping used **only to suggest** a trade on
a requisition line — and the suggestion is refused when two trades claim the
same product category, because picking the lower id would answer a
configuration question silently
(`test_an_ambiguous_product_mapping_suggests_nothing`). A buyer's choice is
never recomputed away.

A vendor may be qualified for concrete, qualified for steel and not qualified
for electrical, and each is a separate decision — tests B and
`test_b_the_concrete_decision_is_not_widened_by_a_second_trade`.

---

## 47. Company, trade and project scope

**Company.** `company_id` is required on qualifications and restrictions, and
global record rules scope every governance model. A vendor qualified in
Company A is not qualified in Company B — `test_03_qualification_does_not_cross_companies`.

**Group recognition** is stated, never implied. `is_group_wide` plus an
explicit `shared_company_ids`, with a constraint refusing the first without the
second: leaving `company_id` empty to get the same effect would expose the
decision everywhere by accident.
`test_03_group_recognition_has_to_be_stated`.

**Project.** A qualification with no project is the general company/trade
decision. One with a project is an *endorsement* — the extra sign-off some
developments need, usually because a client or lender insists. It is requested
only where `procurement_vendor_endorsement_required` is set on the project, it
narrows and never widens, and the general questionnaire is not duplicated for
it. Test 4 covers both directions.

Restrictions scope the same way: a project restriction leaves other projects
alone (`test_04_a_project_restriction_leaves_other_projects_alone`) and says
nothing about sourcing that names no project.

---

## 48. Validity, expiry and reassessment

`effective_date`, `expiry_date` and `approved_date` are all real and all
consulted.

`approved_date` exists because of Rule 6's converse: a qualification approved
in June never made anybody eligible in March, however early its effective date
is typed. `test_d_eligibility_never_predates_the_approval`.

Expiry is a **date boundary**, not a flag — valid on the last day, not on the
next (`test_d_expiry_is_a_date_boundary_not_a_flag`), and the eligibility
service accepts `expired` and `superseded` records inside their own validity
window, which is how historical sourcing stays explainable.

`expiring_soon` uses `procurement_qualification_warn_days`, configurable per
company and defaulting to 30 — a starting point, not a rule, and
`test_07_expiring_soon_uses_the_configured_window` changes it and watches the
status move.

The nightly job moves state and nothing else: the record keeps its result, its
evidence and its dates, and it is idempotent
(`test_07_the_expiry_job_is_idempotent`). Nothing depends on the job having
run — the service works from the dates — so a stopped scheduler produces stale
labels, never wrong answers.

**A cron for M3's reservation expiry was added at the same time.** M3 shipped
`expire_due_reservations()` with no scheduler behind it, so a company that
configured `procurement_reservation_expiry_days` got a control that never
fired. The default is still zero — never expire — so this releases nothing
until somebody chooses a number. Recorded here rather than left as a gap: a
setting that silently does nothing is worse than not offering it.

---

## 49. Migration

`migrations/18.0.4.0.0/post-migrate.py`.

The one dishonest thing this script could do is decide that a vendor with
eight years of purchase orders must be qualified. Prior commercial activity is
evidence that somebody once decided to buy; it has no assessor, no date, no
evidence and no expiry, and writing it into the qualification register would
create exactly the fiction the register exists to prevent.

So it:

1. **confirms the permissive policy** rather than assuming it, and logs a
   warning naming any company already configured to require qualification —
   which the upgrade did not do;
2. **creates nothing**: no qualification, no profile, no AVL entry, no
   restriction, and it alters no purchase order, vendor bill or requisition;
3. **classifies the supplier population** so a manager can see the size of the
   job before deciding to start it.

| Classification | Meaning |
|---|---|
| `assessed` | A current, valid, approved qualification exists |
| `needs_qualification` | The company requires qualification and this supplier is actually in use |
| `has_active_purchase_history` | Confirmed orders exist. **Evidence, not a decision** |
| `has_current_vendor_pricelist` | Commercial data exists, nothing ordered |
| `legacy_active_vendor` | An active supplier with neither |
| `existing_vendor_unassessed` | Everything else |

Classification is a written field rather than a stored compute: the inputs are
confirmed purchase orders and price lists across the whole database, and a
compute would either re-read all of that on every partner write or go stale.
It is rerun by hand, by a weekly cron, or by the migration, and rerunning is
idempotent because every branch is decided from current data —
`test_19_classification_is_idempotent` also checks it creates no governance
records as a side effect.

Test 19 is the headline: a vendor with years of confirmed orders comes out
`has_active_purchase_history`, has zero qualifications, is not `qualified`
under a strict policy, and its purchase order is still `purchase`.

**Rollout runbook.** Vendor Governance → *Vendors Not Assessed* lists the
work; assess the vendors that matter; then move the company or the individual
projects from OPTIONAL to WARN, to REQUIRED_FOR_SOURCING, to
REQUIRED_FOR_AWARD. Nothing about the upgrade forces any of those steps.

---

## 50. Suggested suppliers

`_suggested_suppliers()` is unchanged: it still returns every catalogue vendor
and decides nothing. M4 did **not** put back the filter M2 removed —
`test_22_suggested_suppliers_still_lists_everybody`.

Alongside it, `_sourcing_pool()` returns the same vendors each with its
governance answer:

```
    Vendor A   eligible
    Vendor B   qualification expires before the required date
    Vendor C   no qualification for this trade
    Vendor D   suspended
```

Four rows, not one. Ineligible vendors are shown with the reason, never
dropped — `test_22_the_pool_shows_every_vendor_with_its_status`. It ranks
nobody, prices nothing and creates no order
(`test_22_the_pool_confirms_nothing`).

Test 14 states the converse and matters as much: a qualified vendor with no
catalogue entry is eligible, and Procurement does not invent a price to fill
the gap.

Test 21 pins the boundary: a draft RFQ stays a draft RFQ while qualifications
are approved, restrictions imposed and expiry run. M5 owns invitations.

---

## 51. Security and confidential evidence

Two new groups, deliberately beside the spend ladder rather than inside it:

```
    Qualification Assessor    run assessments, record and verify evidence,
                              see confidential documents. Cannot approve
    Qualification Approver    approve or reject qualifications, impose and
                              lift restrictions. No spend authority at all
```

Approving a supplier is not approving a purchase, and a company may reasonably
grant one and not the other. Nothing here implies or is implied by Accounting,
Inventory or Construction — a Construction Manager does not become a vendor
approver by being a Construction Manager.

### Confidential evidence

A requirement (or its whole area) may be marked confidential. Two record rules
on the response model, which Odoo ORs: buyers see the non-confidential
responses, assessors see all of them. Written as a group restriction rather
than a global rule, because a global `confidential = False` would hide the
financial assessment from the person doing the financial assessment.

Requesters have no access to responses at all, and still read the partner —
Rule 1 working.

### Attachments

The classic hole is a `many2many` of `ir.attachment` created through the web
widget: the attachment is left unattached, `res_model` empty, and an
unattached attachment is readable by anybody who can read attachments at all.
A vendor's audited accounts would be one `/web/content/` away from every
requester in the company.

`_bind_attachments()` stamps `res_model` and `res_id` onto every document, so
Odoo's own attachment check asks whether the reader may read *this response* —
which the record rules above already answer. There is no separate attachment
ACL to keep in step and **no `sudo()` in the read path**.
`test_17_a_confidential_document_is_not_a_loose_attachment` uploads one, reads
it as an assessor and is refused as a buyer.

`test_17_a_buyer_still_sees_the_eligibility_answer` closes the loop:
confidentiality must not make the service useless to a buyer. They cannot read
the financial evidence and can still be told whether the vendor may be
invited, which is the point of returning a decision rather than a file.

### The two documented elevations

Both are narrow, both are at the field/record level, and both are commented in
place:

| Where | Why |
|---|---|
| `VendorCategory.suggest_for_product()` and `PurchaseOrder._order_vendor_categories()` | Whoever confirms an order may hold nothing but Odoo's Purchase Manager. Which control question the gate asks about them cannot depend on their being allowed to browse the trade list. A trade is a word; nothing about any vendor is exposed |
| The eligibility service's internal searches over profiles, restrictions and qualifications | It returns a *decision*, not the evidence. A buyer is entitled to be told a vendor is suspended without being entitled to read the investigation |

### Maker / checker

`_check_not_self_approval()` derives the deciding user from `env.user` and
nothing else. `test_16_the_rule_is_derived_from_env_user_not_from_the_client`
passes `approved_by` and `default_approver_id` in the context and is still
refused. Rejection requires a reason and keeps every response
(`test_16_a_rejection_needs_a_reason_and_keeps_the_evidence`).

The switch is separate from M3's spend self-approval, because the product
keeps the two authorities separate.

---

## 52. Multi-company

Covered in §47. Concretely: `company_id` required and indexed on every
governance model, global record rules on all nine, `check_company` semantics
enforced by constraint between qualification, project and template, and group
recognition available only when stated explicitly.

---

## 53. Concurrency

M4AF requires exactly one authoritative current qualification per vendor,
company, trade and project scope.

**A partial unique index**, `proc_qualification_one_current`, on
`(company_id, partner_id, category_id, COALESCE(project_id, 0)) WHERE
is_current`. Partial because uniqueness applies to the *current* record — a
vendor with five years of history has five rows and should. `COALESCE`
because NULLs do not collide in a unique index, and two current general
qualifications for one trade is exactly the collision this exists to prevent.

**A transaction-level advisory lock** taken in `action_approve()` before the
previous current record is read, scoped to that one vendor/company/trade/
project.

**An explicit flush** between superseding the old record and setting the new
one current. Found in testing: the ORM buffers writes and was sending both
UPDATEs together in an order the index rejected — a crash rather than a wrong
answer, but still not an answer.

### What the tests can and cannot prove

Stated as plainly as §13 states it for reservations. Odoo's harness runs each
test inside one transaction on one connection, so a genuine two-process race
cannot be staged. What is tested is the thing that would decide such a race:
the index exists (`test_18_the_database_enforces_one_current_per_scope`), a
second approval supersedes rather than duplicating, a forced duplicate written
straight past the workflow is refused by the database, and project
endorsements do not collide with the general qualification.

---

## 54. Performance

Assumed: thousands of vendors, many trades, years of history.

| Measure | Where |
|---|---|
| Composite index `(partner_id, company_id, category_id, state, effective_date, expiry_date)` on the qualification table — the eligibility service's exact lookup | `VendorQualification.init()` |
| The partial unique index doubles as the `is_current` lookup index | same |
| `_read_group` rather than Python summation in the classification pass and the partner counts | `res_partner.py` |
| Classification batched, with an explicit `batch_size` | `_classify_procurement_vendors()` |
| Bulk eligibility accepts an explicit `partners` recordset; the unbounded branch is documented as the one to avoid | `_candidate_partners()` |
| Restrictions filtered by an indexed `(partner_id, company_id, state, effective_from)` domain before any Python work | `_restrictions()` |

The eligibility service performs a bounded number of queries per vendor and
does not load history into Python to filter it.

---

## 55. Tests

**Procurement: 240 tests, 0 failed, 0 errors** — 147 at M3, +93 for M4.

| File | Tests | Covers |
|---|---|---|
| `test_m4_invariants.py` | 11 | The five invariants A–E, written before any model |
| `test_m4_qualification.py` | 31 | Tests 1, 3–10: scope, templates, scoring, conditions, expiry, reassessment, restrictions |
| `test_m4_eligibility.py` | 28 | Tests 11–15 and 20–22: policy, price, history, the purchase gate, financial isolation |
| `test_m4_security.py` | 23 | Tests 16–19: maker/checker, confidential evidence, concurrency, migration, registers |

Every M1–M3 test was kept. **No test was converted, deleted or weakened for
M4** — none needed to be, because M4 added a control that defaults to
refusing nothing.

### The brief's 22, mapped

| # | Requirement | Test |
|---|---|---|
| 1 | Vendor master not duplicated | `test_01_the_vendor_master_is_not_duplicated`, `test_01_contractor_and_vendor_stay_separate` |
| 2 | Category-specific | `test_b_qualification_is_category_specific` |
| 3 | Company-specific | `test_03_qualification_does_not_cross_companies` |
| 4 | Project-specific | `test_04_project_endorsement_narrows_and_never_widens` |
| 5 | Mandatory/blocking failure | `test_05_a_blocking_failure_outranks_a_high_score` |
| 6 | Conditional, 5M cap exposed | `test_06_a_value_cap_is_exposed_not_enforced_yet` |
| 7 | Expiry | `test_d_expiry_ends_eligibility_without_ending_history`, `test_07_*` |
| 8 | Reassessment | `test_08_reassessment_supersedes_without_rewriting` |
| 9 | Suspension | `test_c_suspension_overrides_a_valid_qualification`, `test_09_*` |
| 10 | Suspension expiry | `test_10_a_lapsed_suspension_stops_biting` |
| 11 | Optional policy | `test_11_optional_policy_changes_nothing_for_a_legacy_vendor` |
| 12 | Required policy | `test_12_required_for_sourcing_refuses_an_invitation` |
| 13 | Price ≠ qualification | `test_13_a_price_list_is_not_a_qualification` |
| 14 | Qualification ≠ price | `test_14_a_qualification_is_not_a_price` |
| 15 | Current vs historical | `test_15_the_answer_depends_on_the_date_it_is_asked_about` |
| 16 | Approval | `test_16_the_assessor_cannot_approve_their_own_assessment` |
| 17 | Attachment security | `test_17_a_confidential_document_is_not_a_loose_attachment` |
| 18 | Concurrency | `TestM4Concurrency` (4 tests) |
| 19 | Migration | `test_19_purchase_history_does_not_become_a_qualification` |
| 20 | M3 financial isolation | `test_e_governing_a_vendor_moves_no_money`, `test_20_*` |
| 21 | RFQ current state | `test_21_a_draft_rfq_stays_a_draft_rfq` |
| 22 | Suggestion | `test_22_the_pool_shows_every_vendor_with_its_status` |

### Three defects the tests found in M4 itself

Recorded because each one would have shipped as a plausible-looking feature
that failed at the worst moment, and each now has a regression test:

| Defect | Symptom | Fix |
|---|---|---|
| **A warning cancelled the thing it was warning about.** Under WARN the gate posts a note; Odoo refuses to post on behalf of a user with no email; the exception propagated out of `button_confirm()` | A user with no email address could not confirm any project purchase order under WARN | `post_governance_note()` falls back to the system partner and names the acting user in the text. `test_a_warning_never_blocks_the_thing_it_warns_about` |
| **A governance refusal arrived as an access error.** The refusal message re-read the qualification and the restriction as the confirming user, who may hold nothing but Purchase Manager | "You are not allowed to access Vendor Restriction" instead of "cannot be confirmed: suspended since 3 August — site safety incident" | The service returns the *decision* elevated and the evidence not. `test_a_bare_purchase_manager_gets_the_refusal_not_an_access_error` |
| **A frozen template could be edited through its rows.** The freeze guarded `template.write()` and nothing guarded the requirement records | Editing or deleting a requirement directly changed what a used questionnaire had asked | The guard moved onto `QualificationRequirement.write/unlink` as well. `test_a_used_template_refuses_to_change_what_it_asks` |

The ORM write-ordering collision on the current-qualification index (§53) was
found the same way and is fixed by an explicit flush.

---

## 56. M4 gate results

Every figure below is from a run that completed. Interrupted and
port-conflicted runs are not reported: three had to be repeated — twice
because running two Odoo instances concurrently starved both, once because a
second process was still holding port 8099. Their output is discarded, not
averaged in.

The figures are from the final run, taken after the last code change rather
than from the first run that happened to be green.

| # | Gate | Result |
|---|---|---|
| 1 | Procurement M1–M4 | **240 tests, 0 failed, 0 errors** |
| 2 | Construction frozen suite | **563 tests, 0 failed, 0 errors** — exact |
| — | Both together, one database | **803 tests, 0 failed, 0 errors** |
| 3 | Vendor / partner integration | Included above (`TestM4Qualification`, `test_01_*`) |
| 4 | Purchase integration | Included above (`TestM4PurchaseGate`, 6 tests) |
| 5 | Security | Included above (`TestM4Approval`, `TestM4ConfidentialEvidence`) |
| 6 | Attachment confidentiality | `test_17_*`, 5 tests |
| 7 | Multi-company | `test_03_*`, `test_04_*` |
| 8 | Concurrency | `TestM4Concurrency`, 4 tests |
| 9 | Fresh 14-module install | Installed clean, exit 0, no parse or critical error. Test run: **802 tests, 1 failed** — see below |
| 10 | Full 14-module upgrade | Upgraded clean, exit 0, no parse or critical error |
| 11 | Legacy Procurement migration | Upgraded 18.0.3.0.0 → 18.0.4.0.0 on `atmta_m3mig`, exit 0, no error — see below |
| 12 | Migration retry / idempotency | Same upgrade rerun, exit 0, identical outcome, nothing duplicated |

### The one failure, and why it is not being fixed here

`real_estate_construction`'s
`TestMultiCompanyIsolation.test_both_companies_active_still_scopes_each_project`
failed on the fresh 14-module database and passed everywhere else, including
the 803-test combined run on `atmta_p4`. It is not an M4 regression, and it is
worth being precise about why rather than asserting it.

The assertion is:

```python
    self.assertNotIn(str(self.project_b.id), str(payload_a['cost']))
```

— the *string* of project B's id, searched inside the *string* of project A's
cost payload. That payload contains no id of any kind. Its keys are
`original_budget`, `current_budget`, `original_commitment`,
`current_commitment`, `actual_cost`, `certified_amount`, `etc`, `eac`,
`forecast_variance`, `has_forecast`, `available_before_commitment`,
`budget_remaining_vs_actual`, `over_committed`, `unassigned_commitment`,
`unassigned_actual` and `budget_state` — sixteen numbers and flags, not one
reference. So the check cannot detect the leak it is named after, and can only
ever fire on a coincidence between an id and a figure.

On this run it did: `project_b.id` came out 60 and company A's commitment was
`60000000.0`, which contains `60`.

What made the ids land differently is that M4 ships fourteen qualification-area
records, so everything created afterwards is numbered higher. M4 *exposed* the
coincidence; it did not create the defect, and `git log` confirms the test file
has not been touched since the freeze commit.

**It has been left alone.** M4AI says to stop and prove a contract defect
before editing Construction, and this is not a contract defect — it is a test
that cannot do its job. The isolation it means to check is covered by its own
neighbouring assertions (`payload_a` reports 100,000,000 and `payload_b`
reports 50,000,000, both passing) and by the other tests in the same class,
which all pass. Rewriting a frozen module's test to make a gate green is
exactly the move the freeze exists to prevent, so it is reported instead.

**Construction was not modified by M4.** `git diff` against the M3 commit
(`f7873a2`) reports zero changed lines in `real_estate_construction`, so the
563 is not a coincidence of the suite passing — there was nothing to break.
M4AI's escape hatch ("if M4 unexpectedly requires Construction edits, stop and
prove a contract defect first") was never needed: the dependency direction
that M2 and M3 preserved meant vendor governance could be keyed on
`res.partner` and `realestate.project`, both of which Procurement already
depends on.

### Migration classifications

Run on a copy of `atmta_m3mig` — the database M3's own migration was proved
against, so this is a genuine 18.0.3.0.0 → 18.0.4.0.0 step over data that
predates vendor governance entirely. (Copied with the filestore, not just the
rows: `createdb -T` does not carry it, which cost M3 half a day of phantom
browser failures.)

The upgrade log, in full:

```
    M4: 2 company(ies) on the permissive default, 0 configured to require
        qualification, 0 had no value and were set to optional.
    M4 vendor classification — has_active_purchase_history: 1
    M4 complete: 1 supplier(s) classified. 0 qualifications created,
        0 governance profiles created, 0 approved-vendor-list entries created,
        0 restrictions created. No purchase order, vendor bill or requisition
        was altered.
```

Rerun immediately afterwards: exit 0, same output, nothing duplicated. The
post-state check confirmed 0 qualifications, 0 profiles, 0 restrictions, both
companies on `optional`, **4 confirmed purchase orders still confirmed and 0
cancelled**, and M3's reservation figures untouched.

That database only had one supplier in it, which proves the migration is safe
but says little about what the classification *reports*. So the same entry
point the migration calls — `_classify_procurement_vendors()` — was then run
over a seeded population of nine legacy vendors on the migrated database:

| Classification | Count |
|---|---|
| `has_active_purchase_history` | 4 |
| `has_current_vendor_pricelist` | 2 |
| `legacy_active_vendor` | 4 |
| `assessed` | 0 |
| `needs_qualification` | 0 (no company requires qualification) |

Run twice, byte-identical both times. Records created by the classification:
**0 qualifications, 0 profiles, 0 restrictions.**

And the test that matters most, on the migrated database rather than in a
fixture — a supplier with confirmed purchase orders, asked under
`required_for_sourcing`:

```
    eligible = False    qualified = False    status = no_qualification
```

Years of trading did not become a governance decision.

---

## 57. Remaining gaps, stated plainly

After M3, a confirmed purchase order carried one guarantee:

```
    IF A PO IS CONFIRMED, IT WAS FINANCIALLY AUTHORISED.
```

M4 adds a second, and only where the policy has been switched on:

```
    IF A PO IS CONFIRMED, THE VENDOR WAS ALLOWED TO RECEIVE IT.
```

Neither is, and together they are still not:

```
    THE BEST VENDOR WAS SELECTED.
```

**M4 does not determine the winning vendor.** It answers *eligible /
ineligible / eligible with conditions*, and nothing in this module should be
read as claiming more. Who was invited, what they quoted, how the bids
compared technically and commercially, and who should win are M5–M7.

### Not built in M4, and not stubbed

| Missing | Owner |
|---|---|
| Competitive tender, alternative-RFQ orchestration, bid submission | M5 |
| Technical evaluation, commercial evaluation, bid levelling, negotiation, BAFO | M6 |
| Formal award recommendation and split-award interface | M7 |
| Vendor portal, supplier self-registration, external registration URLs | M8+ |
| Vendor performance and scorecards, expediting, material inspection, QA/QC | M8+ |
| Procurement dashboard | M9 |
| Full conflict-of-interest workflow (M4 ships the metadata only) | M26 |
| AI supplier risk, sanctions screening, credit-bureau integration | not planned |
| Full emergency-procurement workflow (M3 only removed the urgency bypass) | M24 |

### Known limitations of what *was* built in M4

| Limitation | Why it stands |
|---|---|
| **Vendor qualification defaults to `optional`.** Out of the box no vendor is refused anything | Shipping a required level would refuse existing suppliers on upgrade day across every company. The rollout worklist and runbook are §49; the register, the AVL and every eligibility answer are live from the first minute |
| **A `max_award_value` condition is exposed and not enforced.** M4 tells a buyer the ceiling; nothing stops an order above it | There is no award document to enforce at until M7. The condition is structured precisely so M7 reads a field rather than parsing a note. Stated here rather than implied to work |
| **`REQUIRED_FOR_AWARD` is enforced at purchase-order confirmation**, not at an award decision | The award decision does not exist yet. When M7 introduces one the check moves earlier and confirmation keeps this as the backstop. `REQUIRED_FOR_PO` was deliberately not shipped as a fifth level because today it would be indistinguishable |
| **Governance applies only to `is_realestate_po` orders.** An office purchase confirms under any policy | Policing the whole purchase journal would break ordinary buying that this suite has no opinion about. A company that wants it there should say so, and no such switch exists yet |
| **The trade taxonomy starts empty.** Until trades exist, eligibility is asked without one | A shipped trade tree would be one company's vocabulary imposed on everyone. Qualification areas ship because headings are generic; trades do not because they are not |
| **A genuine two-process race is not exercised.** §53 states exactly what is proved instead | Odoo's harness shares one connection. The index, the lock and the flush are each tested directly |
| **Duplicate detection is exact-match only**, on tax number and email | Fuzzy company matching is a product of its own, and auto-merging partners is explicitly out of scope. The warning is a prompt for a human |
| **Vendor bank and payment data is untouched** | M4R. Finance owns payment master controls, and widening procurement's view of bank details to "verify" them would be a worse outcome than the gap |
| **Restriction has no approval workflow of its own** — imposing one is a single act by a Qualification Approver | A two-step suspension would delay the case where speed is the point. Every restriction records who imposed it, who approved it, when, why, and who lifted it |

### Known limitations carried forward from M3

| Limitation | Why it stands |
|---|---|
| **Purchase governance defaults to `optional`.** Out of the box the direct-PO bypass is closed by configuration, not by default | Shipping `controlled` would refuse confirmation on every existing project purchase-order path on upgrade day, including the ones Construction's own suite exercises. The runbook says which switch to throw |
| **Budget policy defaults to `warn`** — nothing is refused until a company chooses | Same reason. Reservations, positions and exception evidence are all live from the first minute |
| **A genuine two-process race is not exercised by the suite** *(budget reservations)* | Odoo's test harness shares one connection; §13 states exactly what is proved instead. §53 says the same about qualifications |
| **A purchase user with no Construction read still cannot code a PO line** — `test_defect_a_buyer_coding_a_line_hits_an_access_error` remains a defect test | Fixing it means widening Construction's cost-code read, which the frozen-module rule and the M2 brief both forbid. The practical answer is that anybody coding to a cost code has Construction User |
| **An unknown estimate still brackets as zero** in the approval matrix. `estimate_is_known` and the data-quality warning surface it; the matrix does not refuse it | Refusing every requisition with an unpriced line would stop legitimate early demand. Naming the unknown is the honest half that exists today |
| **No cost-code-level policy override** | Cost codes are Construction's; a precedence level that only existed when another module was installed would be worse than not having it |
| **Reservation expiry is off by default** | An expiry nobody chose would release real demand on a date nobody knew about. M4 did fix the half of this that was a defect rather than a choice: `expire_due_reservations()` had no scheduler behind it, so a configured expiry never fired at all. See §48 |
| **PO confirmation still writes to `realestate.project`** via lazy stock-location creation | M7 PO Confirmation Integration Gate, unchanged from M2 |
| **Line-level ACL still grants `unlink` to plain users** | The active-reservation guard now refuses deletion of demand that holds capacity; the ACL row itself is untouched |
| **Requisition-level material-inspection link** unused | M8 |

Nothing in this list is disguised as finished.

---

## 58. Frozen test correction — the control experiment

M4's gate reported one failure in frozen Construction:
`TestMultiCompanyIsolation.test_both_companies_active_still_scopes_each_project`
(`real_estate_construction/tests/test_m10_multicompany.py`). §56 recorded it as
a test that cannot do its job. It has now been corrected — in the test only —
and the correction was put through a control experiment before being accepted,
because a test-only change that coincides with new browser failures has to be
proved innocent rather than assumed innocent.

### The defect, precisely

```python
    self.assertNotIn(str(self.project_b.id), str(payload_a['cost']))
```

`ConstructionControlTower._cost()` returns a fixed dict of eighteen keys, every
value a float, a bool, or the `budget_state` string. `project_totals()` does
return `project_id`; `_cost()` deliberately does not copy it through. **No id of
any kind can appear in the structure being searched.** The assertion could only
pass, or fail by digit collision — which is what happened: the fixture confirms
a Project A order of `60_000_000.0`, Postgres handed out `project_b.id == 60`,
and `"60"` matched inside `"60000000.0"`. The reverse is worse: a real leak of
Project B's money into Project A's totals produces a summed float carrying no id
at all, and the assertion stays green. False positives and false negatives both.

### What replaced it

Project B is first given a contribution of every kind Project A's payload
aggregates — a baselined budget (already in `setUp`), a confirmed commitment of
7,000,000 inside company B, and a posted actual of 3,000,000. An isolation test
whose neighbour contributes nothing has nothing to leak and cannot fail.

Four proofs then run, none of them stringified:

1. **Exact totals.** A's authorised figures: budget 100,000,000, commitment
   60,000,000, actual 20,000,000. A summing leak makes them 150,000,000,
   67,000,000 and 23,000,000.
2. **Row identity.** Every `cost_code_id` on `cost_sheet.rows_for()` compared as
   an integer: `civil_a` present, `civil_b` absent.
3. **The records behind the numbers.** `cost_sheet.drilldown()` returns the
   backend-owned domain for a cell. Both domains are executed **with `sudo()`**
   — deliberately, because a domain that only looks isolated because a record
   rule filtered it afterwards is not isolated, it is lucky — and the resulting
   records are checked field by field: `order_id.re_project_id` and `company_id`
   are A's, B's order line and B's analytic line are absent, and the sum of the
   records equals the figure the tower reported.
4. **B answers as B**, with 50,000,000 / 7,000,000 / 3,000,000, not with A's
   figures and not with both.

Each of the four was mutation-tested — inverted one at a time and re-run — so
none is vacuous:

| Mutation | Failure produced |
|---|---|
| expect the leaked commitment `67M` | `60000000.0 != 67000000.0` |
| expect `civil_b` among A's rows | `1009 not found in {1008}` |
| expect B's order line in A's drilldown | `224 not found in [223]` |
| expect B's analytic line in A's drilldown | `182 not found in [181]` |

### The control experiment

Five Construction browser/HOOT classes failed in the same gate window. To
establish whether the correction caused them, two conditions were run
**interleaved** — A, B, B, A — with the same command, ports, database strategy
(fresh copy of one template per run), tags, timeouts, browser mode and assets
mode. The only difference between conditions was that one test file, verified by
sha256 before every run. Zero Construction production files were involved.

| Run | Condition | Result |
|---|---|---|
| C1 | **A** — original frozen test | all five classes FAIL |
| C2 | **B** — correction | all five classes FAIL |
| C3 | **B** — correction | all five classes FAIL |
| C4 | **A** — original frozen test | all five classes FAIL |

Failure families, identical in both conditions: HOOT `Script timeout exceeded`;
tour `_wait_ready` returning falsy — *the web client never became ready*; and a
websocket `concurrent.futures._base.TimeoutError` behind several of them. **Not
one failure is a business assertion.** No tour reached a step; they never
started. The FAIL/ERROR split moved between identical-source runs (C1: 2 failed
+ 3 errors; C3: 5 failed + 0 errors) while the affected test set stayed the
same, and in the fresh-install gate the Construction tours passed while three
unrelated RTL tours in `atmta_real_estate`, `real_estate_brokerage` and
`real_estate_checks` failed instead.

Host state was recorded at every run: load 1.4–4.4, available memory 3.9–5.3 GB,
**swap pinned at its 2 GB ceiling throughout**, 36–50 Chrome processes resident,
and other Odoo servers running.

### Conclusion

```
    THE TEST CORRECTION IS NOT CAUSAL.
```

The failures reproduce identically without it. The cause was then found, and it
was not the environment either — see §59.

### The corrected test

`test(construction): fix multi-company isolation assertion` — commit `47a5a57`,
one file, +86 −4, **zero Construction production lines**. `b70ca5f` was not
amended. 20 of 20 completed stability runs, 0 failed, 0 errors, with the project
id sequence advancing on every run so that ids varied across exactly the axis
the old assertion was fragile to.

---

## 59. The browser failures — root cause

A first pass classified the browser and HOOT failures as environment
instability, on the strength of a saturated 2 GB swap and 36–50 resident Chrome
processes. That was wrong, and the record is corrected here rather than quietly
amended.

### The hypothesis that was rejected

The programme's accepted M3/M4 gates all ran `-u real_estate_procurement`; the
new scoped gates omitted it. That suggested the tests needed a module
install/update lifecycle. It was tested directly — same template, same tags,
same ports, same timeouts, same assets mode, one variable:

| Condition | Invocation | Result |
|---|---|---|
| **N** | no `-i`, no `-u` | 5 fail — 1 JS script timeout, 4 ready timeouts |
| **U** | `-u real_estate_construction` | 5 fail — identical modes |

```
    -u MECHANISM REJECTED.
```

### The actual cause

The gate harness provisioned its databases with `createdb -T`, which clones a
PostgreSQL database and **not** the Odoo filestore. Asset bundles are
`ir.attachment` rows whose `store_fname` points into
`<data_dir>/filestore/<dbname>/`. The clone therefore carried every
`web.assets_*` attachment record while the bytes stayed behind:

```
    atmta_gfresh (template, installed directly)   623 files, 75 MB
    atmta_nu_n   (createdb -T clone)              no filestore at all
    atmta_gbrw   (createdb -T clone)              0 files
```

A web client served bundles that resolve to nothing never finishes booting.
`odoo.isTourReady(...)` stays falsy, the websocket request times out, HOOT hits
`Script timeout exceeded` — every observed signature, and never once a business
assertion, because no tour ever reached a step.

### Direct proof

Third condition, same command as **N**, filestore copied alongside the database:

| Condition | DB clone | Filestore | `-u` | Result |
|---|---|---|---|---|
| N | yes | **missing** | no | 5 fail |
| U | yes | **missing** | yes | 5 fail |
| **FS** | yes | **copied** | no | **0 failed, 0 errors of 6** |

Confirmed twice more from independent clones — U1 and U2, both
`0 failed, 0 errors of 6`.

This retro-explains every observation without appeal to machine load: the fresh
install (`-i`) built its own filestore and passed these same five; the accepted
M3/M4 gates ran against directly installed databases and passed them; the
20 × multi-company runs and Procurement's 240 are pure Python and were never
affected. Condition FS passed at load 1.55 with 40 Chrome processes and swap
still pinned at 2 GB — the resource pressure was real and irrelevant.

```
    CLASSIFICATION: TEST HARNESS DEFECT, in the gate runner, introduced during
    this verification work. Not a product defect, not a defective test, not the
    multi-company correction.
```

### The corrected harness

The gate runner now clones the filestore with the database, and refuses to hand
back an incomplete clone:

```bash
    copy () { ... createdb -T ...; cp -a "$FS/$2" "$FS/$1"; assert_filestore "$1" "$2"; }
```

`assert_filestore()` aborts the run when a clone holds fewer filestore files
than its template. Failing loudly there beats spending a day classifying asset
starvation as a browser flake. Scoped gates also carry the owning module's `-u`,
matching the accepted M3/M4 commands. No product source, no test source, no
timeout, no browser flag and no asset mode was changed for this.

---

## 60. Final M4 release gates

Every run below completed on a correctly provisioned database.

| Gate | Invocation | Result |
|---|---|---|
| **A — Construction** | `-u real_estate_construction`, tag `atmta_construction` | **563 tests, 0 failed, 0 errors** |
| **B — Procurement M1–M4** | `-u real_estate_procurement`, tag `atmta_procurement` | **240 tests, 0 failed, 0 errors** |
| **C — Combined** | `-u` both, both tags | **803 tests, 0 failed, 0 errors** |
| **D — Browser/HOOT subset** | owning modules `-u` | Construction 6/6 pass; `script_timeouts=0`, `ready_timeouts=0` |
| **E — Fresh 14-module install** | `-i` all fourteen | install clean, RC=0; suite 2,339 — failures only in unrelated modules (§61) |
| **F — Full 14-module upgrade** | `-u` all fourteen | upgrade clean, RC=0; suite 2,339 — same unrelated modules (§61) |
| **G — Legacy migration** | `18.0.0.1 → 18.0.4.0.0` | rc=0, see below |
| **H — Migration retry** | re-run | rc=0, byte-identical |

Migration, before and after, on a real legacy database:

```
    qualifications   (table absent) → 0        confirmed POs   5 → 5
    profiles         (table absent) → 0        cancelled POs   0 → 0
    restrictions     (table absent) → 0        PO total        778,406.50 → 778,406.50
    conditions       (table absent) → 0        classification  has_active_purchase_history: 2
```

Two suppliers with confirmed purchase history were classified as exactly that
and qualified as nothing. Purchase history did not become a governance decision.
The retry produced an identical population and an identical classification line:

```
    IDEMPOTENT = YES
```

---

## 61. Unrelated failures, stated separately

The fourteen-module suite is **not** clean, and this report will not print
`0 failed` over a run that failed.

Widening the gate from `atmta_procurement,atmta_construction` to all fourteen
modules ran, for the first time, test suites that no M4 gate had ever executed.
Four to five of them fail, in three modules, none owned by this milestone:

| Module | Test | Character |
|---|---|---|
| `atmta_real_estate` | `TestDashboardRTL.test_dashboard_rtl` | deterministic, 3/3 |
| `real_estate_brokerage` | `TestBrowserRTL.test_listings_render_right_to_left` | deterministic, 3/3 |
| `real_estate_checks` | `TestTreasuryDashboardRTL.test_dashboard_rtl` | deterministic, 3/3 |
| `real_estate_brokerage` | `TestBrowserTablet`, `TestBrowserCommission*` | intermittent, run-dependent |
| `real_estate_checks` | `TestTreasuryDashboardRoleUser` | intermittent, passed under its own `-u` |

The three RTL failures are real product assertions, not timeouts:

```
    Expected an RTL session, computed direction is "ltr".
    html[dir]=null session.lang=undefined
```

They reproduce with a correct filestore, correctly loaded assets, and their own
owning module's `-u`, so §59's root cause does not explain them and they are not
claimed to be fixed by it.

Every one of these tests — and the model code beside them — lives in
**uncommitted, untracked working-tree work from other sessions**
(`atmta_real_estate/tests/`, `real_estate_brokerage/tests/`,
`real_estate_checks/tests/` are all `??` against the branch, as is a body of new
model code). They are outside M4's scope, were not touched, and are recorded
here so nobody reads the fourteen-module number as a clean bill of health for
those modules.

```
    M4 SCOPE (Procurement + frozen Construction):  CLEAN
    WIDENED DIRTY-WORKING-TREE SUITE:              UNRELATED FAILURES PRESENT
```

---

# M5 — Sourcing and Tender

Module version **18.0.5.0.0**.

```
    M5 RECORDS THE COMPETITIVE PROCESS.
    M5 DOES NOT EVALUATE THE WINNER.
```

M6 owns technical and commercial evaluation, bid levelling, currency
normalisation and scoring. M7 owns the award, the purchase order that follows
it, and the Construction commitment that follows that. Nothing in this
milestone ranks a vendor, and the one native helper that would have made
ranking easy (`get_tender_best_lines`) is deliberately left unused.

## 62. M5.0 — the native alternative-RFQ audit

Read from installed source before any model was written
(`addons/purchase_requisition/`), because guessing field names and then
building around the guess is how a module ends up with a parallel
implementation of something Odoo already does.

**Alternatives are a group, not a link.**

```python
    class PurchaseOrderGroup(models.Model):
        _name = 'purchase.order.group'
        order_ids = fields.One2many('purchase.order', 'purchase_group_id')

        def write(self, vals):
            res = super().write(vals)
            self.filtered(lambda g: len(g.order_ids) <= 1).unlink()   # self-implodes
            return res
```

`purchase.order.purchase_group_id` points at it; `alternative_po_ids` is a
*related* one-to-many through it, domain-limited to draft/sent/to-approve and
`check_company=True`. The group is created implicitly — context `origin_po_id`
on create, or writing `alternative_po_ids` — and **deletes itself when it drops
to one order**.

That last property decided the architecture. A tender whose identity vanished
because two of three vendors declined would be a tender nobody could answer
questions about afterwards. The group has no reference, no state, no dates, no
audit and no lifecycle, so:

```
    ATMTA Sourcing Event   identity, authority, governance, history
    purchase.order.group   Odoo's comparison plumbing, used as-is
```

M5 creates the RFQs through the native context hook so the group is built the
way the native compare view expects, and never writes to the group model
directly.

## 63. Native compare is destructive — why bid evidence is sealed

```python
    def action_choose(self):
        order_lines = (self.order_id | self.order_id.alternative_po_ids).mapped('order_line')
        order_lines = order_lines.filtered(lambda l: l.product_qty and ...)
        return order_lines.action_clear_quantities()      # write({'product_qty': 0})
```

Choosing a line in Odoo's compare view writes `product_qty = 0` on every
competing alternative line. Had "what Vendor B submitted" been read back from
`purchase.order.line`, one buyer comparing offers would have rewritten Vendor B
into having bid nothing — and the tender file would have said so afterwards,
with a straight face.

So receipt snapshots into `realestate.procurement.bid.response` and
`.bid.response.line`. Every column there is a plain stored value: not related,
not computed, nothing that could follow the RFQ. The regression is pinned by
`test_d_native_compare_zeroing_cannot_rewrite_the_bid`, which performs the
native mutation and asserts both that it happened and that the snapshot did not
move.

## 64. The sealed-bid engine boundary

Received evidence refuses to be written. The first implementation carried the
bypass on an overridden `sudo()`, and that was a real hole: **every** elevated
read anywhere in the system — a compute, a report, another module, a generic
`sudo()` in core — would have silently acquired the right to rewrite submitted
bids. It was replaced with a named `_engine()`:

```python
    def _engine(self):
        return self.sudo().with_context(re_bid_engine=True)
```

`_record()` also strips the flag from the record it hands back, so a caller
cannot accidentally receive a handle that ignores its own seal. The test that
caught this is `test_d_a_received_bid_refuses_to_be_edited`.

## 65. M5.7 — deadlines

Datetime throughout, and six moments kept apart: `issue_datetime`,
`clarification_deadline`, `original_close_datetime` (never overwritten),
`close_datetime` (effective), `actual_closed_datetime`, and the
`effective_close_datetime` carried by each tender version.

**A bid is judged against the version it answers.** `deadline_applied` is
copied onto the response at receipt from that version, so an addendum that
extends the close does not retrospectively make an earlier submission early,
and one that pulls it in does not make an earlier submission late.

The boundary rule, stated once:

```
    received <= effective close   ON TIME
    received >  effective close   LATE
```

Inclusive, because a tender document closing at 12:00 accepts submissions *up
to* 12:00. Tested at 11:59:59, 12:00:00 and 12:00:01 — at the precision that
survives storage, not an imaginary finer one. A timezone test asserts the same
stored UTC instant classifies identically for users in Cairo, UTC and New York;
no localised string is ever compared.

## 66. M5.8 — late bid governance

| Policy | Recorded | Evaluable | Marked late |
|---|---|---|---|
| `reject` | yes — arrival is a fact | **no** | yes |
| `exception_required` | yes | only after a manager accepts | yes, permanently |
| `allow_with_warning` | yes | yes | yes, permanently |

The policy in force **at receipt** is stamped on the response, along with the
deadline applied, the lateness in seconds, whether an exception was required
and — once granted — who granted it, when and why. Changing company policy
afterwards cannot rewrite how an old submission was handled; a test asserts
exactly that.

Self-approval is off by default: the buyer who recorded a late submission
approving their own exception for it defeats the point of having one.
`procurement_allow_self_late_exception` exists for companies that decide
otherwise, and it is a decision they have to make.

**Late is administrative.** No status in this module says technically
non-compliant, commercially best or recommended, and a test enumerates the
selection to keep it that way.

## 67. M5.9 — decline, no bid, no response

Three different facts, none of them a price:

```
    DECLINED      the vendor told us they will not bid
    NO_BID        the vendor formally returned a no-bid
    NO_RESPONSE   the deadline passed in silence
```

None creates a zero-valued bid. Nought is a commercial statement — it says the
vendor will do the work for nothing — and manufacturing one to make a tender
look complete puts that statement in their mouth. `no_response` is computed and
only becomes true once the deadline has actually passed; before that, silence is
`awaiting`, because a vendor still thinking is not a vendor who declined.
Reason categories are recorded for the tender file and feed nothing: vendor
performance is M9.

## 68. M5.10 — withdrawal

Withdrawing removes the offer, never the evidence: header, lines, attachments,
received timestamp and the whole revision chain stay exactly as they were.

**Withdrawing Rev 1 does not revive Rev 0.** The invitation is left with no
current response, deliberately. A quotation the vendor has already replaced is
not an offer they are still making, and silently promoting it would put a price
back in their mouth that they withdrew. Reinstatement, if a company wants it,
is a new revision — an explicit act, not a side effect.

Whether the withdrawal landed before or after bidding closed is recorded.
Closing the tender settles that outright rather than comparing clocks, because
a withdrawal in the same second as the close is precisely the case somebody
would later argue about.

## 69. M5.11 — clarifications

Three types (vendor question, buyer clarification, general) and three
visibilities (`vendor_only`, `all_invited`, `internal`). Requesters have **no
access row at all** for clarifications, which is a stronger statement than any
domain: project membership does not grant sight of a competitor's commercial
question.

A clarification that would change quantity, scope, specification, commercial
terms, the document basis or the deadline cannot be answered as text. The
answer action refuses until an addendum carries it, so the issued version stays
sealed and vendors are told what changed and when — rather than the basis
moving under the people who already priced it.

## 70. M5.12 — addenda and acknowledgement

Tender versions and bid revisions are different numbers and never mix:

```
    Tender Rev 0  →  Vendor A Bid Rev 0
    Addendum      →  Tender Rev 1
    Vendor resubmits →  Vendor A Bid Rev 1
```

Publishing Rev 1 marks the invitation `resubmission_required` and leaves the
existing bid untouched. **No bid revision is fabricated.** Us changing the
question is not them changing their answer, and a Rev 1 bid nobody submitted
would be a fabrication sitting in the evidence file.

Acknowledgement is its own record — version, moment, method, and who wrote it
down — rather than a boolean that could answer none of those a year later.
Methods include `email_received`, `signed_document`, `buyer_recorded` and
`future_portal`. There is no vendor portal in M5 and nothing here claims the
vendor clicked anything; `future_portal` exists so that portal
acknowledgements stay distinguishable when one arrives.

Policy is `optional` by default and overridable per event. Under
`required_before_response`, a response against an unacknowledged addendum is
recorded but not evaluable, and acknowledging it re-assesses the response
immediately. Two addenda with only the first acknowledged leaves the response
held — tested.

## 71. M5.13 — administrative completeness

`is_evaluable` answers one question: may M6 look at this at all. Everything
behind it is procedural — a bid exists, it answers the current basis, it was in
time or excused, mandatory addenda were acknowledged, required validity and
documents are present. Nothing inspects the offer, and the completeness note on
a passing bid says so in words: *this says nothing about the merits of the
offer.*

## 72. M5.14 — confidentiality

Tested at the ORM, never at the view. A price hidden by `invisible=` in a form
is not hidden, so every assertion goes through `read`, `search`, `search_read`,
`search_count` or a relational traversal as the restricted user — the routes an
export, an RPC client or a browser console actually use.

| Actor | Bid evidence |
|---|---|
| Requester | no access row at all — `read`, `search`, `search_read` and `search_count` all raise `AccessError` |
| Buyer | their own events and the ones they are on the team of |
| Procurement Manager | everything, because somebody must be able to audit a tender they did not run |
| Plain Purchase user | native Purchase rights grant **no** ATMTA bid access |

Bid documents are stamped with `res_model`/`res_id` onto the response at
receipt, so Odoo's own attachment rules put them behind the bid's access rules
instead of leaving them floating and readable by anyone who can guess an id.

Chatter is checked too: a test asserts no competitor amount appears in the
event's messages, because followers of a tender are not automatically people
who may read its prices.

## 73. The tender-RFQ confirmation boundary

Native `button_confirm` returns the alternative-RFQ warning wizard when live
alternatives exist. That is Purchase UX, not governance: it *asks* a question
rather than refusing, it only appears while alternatives are live, and it is
switched off wholesale by `skip_alternative_check` — which the native wizard
sets itself when it confirms.

ATMTA's control is independent of all of it. `_check_tender_authorisation()`
runs first in `button_confirm`, refuses any RFQ belonging to a sourcing event,
and asks `_award_authorisation()` — M7's hook, which returns `False` and is
written as a lookup rather than a switch so that no configuration can open it
early. A test confirms the refusal still holds under
`skip_alternative_check=True`, and that ordinary non-tender project purchases
confirm exactly as they did.

## 74. Concurrency

Odoo's harness shares one cursor, so a genuine two-process race cannot be run
and this suite does not pretend otherwise. Each mechanism is tested directly
instead:

| # | Race | Mechanism | How it is proved |
|---|---|---|---|
| C1 | two publishes | row lock + `unique(event_id, revision)` | duplicate Rev 0 insert raises `IntegrityError`; second publish refused |
| C2 | two addenda | `FOR UPDATE` on the event | the lock statement is asserted in the SQL actually issued |
| C3 | two bid revisions | `unique(invitation_id, revision)` + invitation row lock | duplicate insert raises; lock statement asserted |
| C4 | two closes | idempotence | one `actual_closed_datetime`, one audit entry |
| C5 | receipt near close | stored timestamps | closing after receipt does not make the bid late |
| C6 | addendum during receipt | version captured inside the lock | response carries one version's lines *and* that version's deadline |
| C7 | eligibility change during invitation | one service call | the stored payload matches every summary field |

## 75. Migration and the `purchase_requisition` dependency

Native alternative RFQs live in Odoo's Purchase Agreements module, which was
**uninstalled** in every ATMTA database, so M5 adds it to `depends`. That also
installs native Purchase Agreements — blanket orders and purchase templates.

```
    ATMTA DOES NOT GOVERN NATIVE PURCHASE AGREEMENTS.
```

They are not tenders, M5 makes no claim about them, customises none of them,
and the migration explicitly does not adopt one. The classification labels them
`purchase_agreement_native` and stops there.

The migration creates **nothing**: no sourcing event, version, invitation, bid
response or acknowledgement. A legacy alternative-RFQ group is not a tender —
it has no issue date, no published version, no deadline, no eligibility
decision at invitation and no recorded moment of receipt, and the amounts on
its lines are today's working numbers rather than what anybody submitted. A
tender file assembled out of guesses is worse than an empty one, because
somebody will quote it.

What it does instead is describe the population:

```
    standalone_rfq                  an ordinary legacy RFQ
    native_alternative_group        alternatives exist, no ATMTA evidence
    legacy_direct_po                already handled under M3
    open_approved_demand_with_rfq   a candidate somebody may adopt by hand
    purchase_agreement_native       native, and not ours
    atmta_tender                    raised by a sourcing event
    ambiguous                       cannot be determined
```

Existing companies are moved to `allow_with_warning` for late bids and
`optional` for acknowledgement — recording everything, refusing nothing, on the
same rule as M3 and M4: nothing legal on Friday is refused on Monday because an
upgrade ran.

## 76. Financial isolation

The M5 invariant, asserted at every lifecycle step by
`test_a_no_step_of_a_tender_moves_money`:

```
    Budget 10M · Approved requisition 3M · Reservation 3M

    create tender      →  3M reserved / 0 committed / 0 actual
    publish            →  3M / 0 / 0
    invite three       →  3M / 0 / 0
    bids 2.8M 3.1M 2.9M →  3M / 0 / 0
    close              →  3M / 0 / 0
```

A cheap bid does not release authorisation and an expensive one does not expand
it; the market answering a question is not the company changing its mind. A bid
above the authorised amount is flagged `above_authorisation` and accommodated
in no other way. M7 revalidates at award.

## 77. The M5 integrity audit

`realestate.procurement.sourcing.audit` reports and never repairs — fifteen
checks covering a writable seal, duplicate current bids, duplicate versions, a
draft version on a published tender, responses against superseded bases,
unacknowledged mandatory addenda, late bids with no policy recorded, a withdrawn
bid still current, a confirmed tender RFQ, allocations exceeding authorised
demand, lost requisition lineage, cross-company events and invitations with no
eligibility snapshot.

One finding is deliberately informational: an RFQ that no longer matches the
bid recorded from it is *normal* after a native compare, and is reported as
`low` with the remediation "no action" — because that divergence is the system
working, not failing.

---

# M6 — Technical and Commercial Evaluation

Module version **18.0.6.0.0**.

```
    M6 EVALUATES THE BIDS.
    M6 DOES NOT AWARD THE CONTRACT.
```

M7 owns the award, the purchase order that follows it and the Construction
commitment that follows that. Nothing in this milestone awards anything, and an
audit check fails the build if an award interface ever appears on an M6 model.

## 78. M6.0 — the source audit, and two findings that shaped the design

Read from installed source before any model was written.

**`price_total_cc` cannot be evaluation evidence.** It computes
`price_subtotal / order_id.currency_rate`, and `purchase.order.currency_rate`
is a stored compute keyed on `date_order`
(`purchase/models/purchase_order.py:193`). Edit the order date and every
historical "company currency" figure moves. Convenient, live, and useless for
an evaluation that must still be reproducible in a dispute two years later.

**Odoo's rate lookup silently returns 1.0.** `res_currency._get_rates` builds:

```sql
    COALESCE( (rate on or before date), (earliest rate), 1.0 )
```

A currency with no published rate converts one-for-one and *looks* converted.
For procurement that is the worst available failure: a USD offer would be
compared against EGP offers at par and would win the tender on an arithmetic
accident. M6 therefore verifies a real rate exists for the declared date and
**refuses to normalise** otherwise, with a message naming both currencies and
the date. The engine itself is Odoo's — `_convert()`, `_get_conversion_rate()`
and `currency.round()` are the right tools and are used; what M6 adds is the
refusal and the frozen record of what was used.

## 79. Evaluation architecture

```
    EVALUATION PLAN     the basis: method, criteria, weights, currency, rate date
    EVALUATION ROUND    one staged evaluation of one tender
    CANDIDATE           one bid inside one round, with its results and rank
    TECHNICAL SHEET     one evaluator's scores for one bid
    COMMERCIAL ANALYSIS one bid's evaluated cost, with its frozen FX evidence
    DEVIATION           where an offer departs from what was asked
```

Results live on the **candidate**, not on the bid: the same offer evaluated
under a revised basis is a different judgement and must not overwrite the
first. M5's bid stays exactly as submitted.

## 80. The plan, and why it freezes

Freezing is the point of the model. A weight moved from 20% to 35% after the
scores are visible is not a methodology correction — it is choosing the winner
and writing the rule afterwards.

Once frozen, the method, criteria, weights, thresholds, evaluation currency,
rate basis, financial formula, committee mode, tie rule and BAFO policy all
refuse to be written. A genuine change produces a **new revision** with its own
reason and author while the old one is superseded, and a revision is refused
outright once a round is under way.

Nothing is silently normalised. Rated criteria that come to 97% are refused at
freeze rather than scaled, because a criterion nobody meant to weight at 12.5%
would then decide the tender. Technical and commercial weights must come to
100%. There is **no shipped 70/30, no default threshold and no default
financial formula** — those are procurement policy, they differ per company and
per tender, and shipping one as a default hands every buyer a methodology they
never chose.

## 81. Mandatory and rated criteria

A mandatory criterion is pass or fail and carries no weight: passing it earns
nothing and failing it ends the bid. A rated criterion contributes its weighted
score, may carry a per-criterion minimum, and states its own score bands in the
plan's words — no band set ships as a default, because *5 = meets requirement*
is one organisation's convention, not a fact.

A knockout failure is not rescuable by any score. Vendor B scoring 10/10 on
everything rated and failing one mandatory criterion is `non_responsive`, is
given no rank, and never reaches commercial evaluation. A failure also requires
a written reason before the sheet can be submitted.

## 82. Committee, declarations and price blindness

Roles are split from the buying roles: a buyer running a tender is not
automatically entitled to score it. Committee membership is **recorded**, not
derived — `user_name` is copied at assignment, so who evaluated survives that
person later leaving the group.

Scoring requires a conflict-of-interest declaration, and a declared conflict
blocks scoring until somebody else clears it. Self-clearing is refused.

**Price blindness is structural.** `technical.evaluation` and its lines carry
no monetary field, no currency field and no commercial name — asserted by a
test that walks the field definitions by *type*, not by spelling. The
commercial models carry **no ACL row for the technical role at all**; that
absence is the control, because a missing access right cannot be widened by
another group's record rule the way a domain can.

**The honest limit, stated plainly:** M5 keeps commercial data in native Odoo
purchase structures, so this is **controlled evaluation-role segregation, not
cryptographic sealed bidding**. A user with sweeping native Purchase
administration rights can still read a `purchase.order`. What M6 guarantees is
that nothing it builds hands price to a technical evaluator, and that its own
commercial records refuse them outright — verified over real RPC, not by
hiding fields in a view.

## 83. Sheets, consolidation and disagreement

An evaluator's submitted sheet is their opinion on the record and refuses
ordinary writes — including from an Evaluation Manager. Corrections go through
a reopen carrying a reason and an author. Criteria are **snapshotted onto the
sheet** at creation, so a historical score is never restated by today's weight.

Consolidation keeps every individual sheet. Where evaluators disagree the
spread is published rather than averaged away: 9 and 3 are not a 6, they are a
disagreement the committee should see. Consensus mode records a separate
consensus result beside the individual sheets rather than pretending consensus
is an arithmetic mean.

## 84. Staging, and what commercial opening means

```
    DRAFT → TECHNICAL_OPEN → TECHNICAL_FINAL → COMMERCIAL_OPEN
          → COMMERCIAL_FINAL → FINALISED
```

Forward only. Stepping backwards would let somebody reopen the technical stage
after seeing prices, which is the single thing staged evaluation exists to
prevent. Commercial opening is refused until the technical result is final, and
only technically responsive offers pass through; the excluded ones are named in
the record rather than quietly dropped.

The candidate set is **frozen at technical opening**, with a reason recorded
for every exclusion. Asking "which bids are eligible now?" a year later would
answer with today's data, and a bid withdrawn since would vanish from an
evaluation it took part in.

## 85. Frozen exchange rates

Every conversion stores the source currency, the target currency, the date the
plan declared, the rate in force on that date and the converted amount. A rate
published afterwards changes none of them — proved by a test that finalises an
evaluation, publishes a new rate, and asserts both the amount and the ranking
are unmoved.

One rate date for all offers, declared by the plan (tender close, issue date,
or a fixed date). Converting each bid at the date it happened to arrive would
rank vendors on the currency market rather than on their offers.

Currency normalisation is evaluation arithmetic and nothing else: no journal
entry, no vendor bill, no FX gain or loss, no analytic line, no commitment.

## 86. Raw bid, adjustments, evaluated cost

```
    RAW BID          2,800,000   what the vendor submitted   (M5, immutable)
    + freight           50,000   itemised, with a rationale  (M6)
    = EVALUATED COST 2,850,000   what the offer is worth     (M6, frozen)
```

All three are kept and all three are shown. Adjustments are typed, and an
adjustment without a rationale is refused — an unexplained figure moving a
vendor up or down the ranking is the one thing an evaluation file cannot
survive. Approving one's own adjustment is refused. A discount is entered as a
positive number and subtracts, so nobody has to remember a minus sign.

Bid leveling normalises line by line on the same frozen basis and preserves the
source allocation lineage M5 recorded.

## 87. Scoring, ranking and ties

The financial formula is the plan's, not the module's. Lowest-evaluated-cost
ratio is offered because it is common — not because it is a law of
procurement — and `lowest_cost_only` is the default so that no score is
invented where none was asked for.

Ranking uses the frozen method's own number at a stated precision. **Ties are
reported, never broken.** Two offers that evaluate identically both hold rank 1,
both are flagged, and the round's result becomes `tie_requires_decision`.
Breaking a tie by database id, vendor name or creation order would invent a
winner nobody chose.

A technically non-responsive bid is never ranked, and the audit fails the build
if one ever is.

## 88. Deviations and BAFO

Deviations are recorded against the bid and the criterion they depart from, so
a material departure is visible on the report rather than buried in an
evaluator's note. Recording one never edits the offer.

A best-and-final round is **off by default** (`bafo_policy = not_allowed`) and
requires a recorded reason and an explicit shortlist; inviting only the current
cheapest bidder without saying why is a preference with no evidence behind it.
A vendor who failed technical evaluation cannot be invited to re-offer. The
BAFO uses M5's bid-revision machinery, so the initial offer survives as Rev 0
and the new one is Rev 1 — an audit check fails if an initial offer was ever
overwritten.

## 89. Financial isolation

Asserted at every step by `test_evaluating_and_finalising_moves_no_money`:

```
    Budget 10M · Reservation 3M · Commitment 0 · Actual 0

    open evaluation      →  3M / 0 / 0
    finalise technical   →  3M / 0 / 0
    open commercial      →  3M / 0 / 0
    normalise + adjust   →  3M / 0 / 0
    finalise + rank      →  3M / 0 / 0
```

And finalising authorises nothing: immediately afterwards a tender RFQ still
refuses `button_confirm()`, including under `skip_alternative_check`, with
Construction commitment at zero. That is a mandatory regression and it runs in
both the unit suite and the browser gate.

## 90. Migration

The `18.0.6.0.0` script creates **no plan, no round, no criterion, no
assignment, no sheet, no score, no analysis and no ranking**. A closed tender
with three bids looks exactly like something that wants an evaluation, and
manufacturing one would fabricate the very things an evaluation file exists to
prove: that criteria were frozen before the offers were seen, that named
evaluators scored them, and that a technical decision preceded the commercial
one. None of that happened, and a migration cannot manufacture judgement.

What it does is label each tender — `open_not_ready`, `closed_unevaluated`,
`evaluation_candidate`, `legacy_external_evaluation`, `evaluated`, `ambiguous`
— so a buyer can see what is waiting for an evaluation somebody has to
actually perform. Idempotent, and tested to alter no bid amount and no
financial position.

## 91. Concurrency

Same harness limit as M4 and M5, stated rather than papered over: one cursor,
so a genuine two-process race cannot run here. Each mechanism is tested
directly instead — unique indexes proved by inserting the duplicate (plan
revision, candidate, evaluator sheet, commercial analysis), and idempotence or
refusal proved for double freeze, double technical open, double submit, double
commercial open, double finalise, and normalisation after finalisation.

**A defect this found:** the sheet's unique index
`(candidate_id, evaluator_id, is_consensus)` never worked. `is_consensus` had
no default, Odoo stored it as `NULL`, and PostgreSQL does not consider two
NULLs equal — the index permitted exactly the duplicate sheets it was written
to prevent. Fixed with an explicit `default=False`, and the field's help text
now records why that default is load-bearing.

## 92. Integrity audit

`realestate.procurement.evaluation.audit` reports and never repairs.
**Twenty** checks — seventeen at first release, three added by the hardening
pass (§96): a submitted sheet edited after submission, a candidate edited after
its round was finalised, and a structural check that the commercial outcome is
still restricted, so removing a `groups=` or putting `rank` back into `_order`
fails the build instead of quietly reopening the leak.

The full set: plan weights that do not reconcile, a criterion
modified after freeze, scoring against an unfrozen basis, duplicate candidates,
an evaluator who never declared, a submitted score from an unresolved conflict,
a knockout failure marked responsive, commercial opened before technical final,
a non-responsive bid holding a rank, a missing FX snapshot, an adjustment with
no rationale, an evaluated cost that differs from the bid with nothing to
explain it, duplicate rank 1, an unsurfaced tie, a BAFO that overwrote an
initial offer, a cross-company evaluation, and any award interface appearing on
an M6 model — plus the three named above.

The freeze check is deliberately data-driven — it compares `write_date` against
`frozen_on` with a one-second tolerance, because `fields.Datetime.now()`
truncates microseconds while the ORM does not, and an assertion that the guard
merely *exists* would be a check that cannot fail.

## 93. The browser gate, RTL and tablet

The tour drives a finalised evaluation end to end: the round form, the notice
that says in plain words this is not an award, the candidate list with a real
knockout exclusion in it, the committee and its declarations, the technical
sheets, and the commercial page where the evaluated cost sits beside the raw
bid. Two guarantees a browser cannot express are asserted in Python around it —
that a technical evaluator is refused commercial data over real RPC, and that a
finalised evaluation still authorises no purchase.

The same tour runs three times, under three conditions, and nothing is relaxed
for the harder two: default desktop, an Arabic (RTL) session, and a 768×1024
tablet viewport with touch emulation.

**The RTL assertion is not the obvious one, and the obvious one is wrong.**
Odoo 18 does not set `html[dir="rtl"]` on the backend. Direction is delivered
by serving a *different stylesheet* — the `.rtl` bundle, produced by running
the compiled CSS through `rtlcss`. Only report templates use `t-att-dir`. A
test asserting `dir="rtl"` on the document fails against a perfectly correct
Odoo, which is precisely how three unrelated failures in this worktree were
misread before the mechanism was traced. So the gate asserts what actually
exists: `ar_001` activates with `direction == 'rtl'`, an Arabic session is
served a `.rtl` stylesheet, and the evaluation screens then complete the full
tour with no error dialog and no broken value.

**The limit that used to stand here has been closed.** `rtlcss` is a Node tool
and it was not installed on this host. Where it is absent Odoo logs a warning
and serves the *unflipped* stylesheet under the `.rtl` name, so the URL
assertion alone could not tell a working flip from a missing one, and
`test_the_rtl_bundle_is_actually_flipped` took an early return that recorded
the shortfall rather than pretending to have checked it.

`rtlcss 4.3.0` is now installed and Odoo's own `find_in_path('rtlcss')`
resolves it in the environment the tests run in, so that assertion takes its
real branch: it fetches both bundles and fails if they are byte-identical. It
passes. The RTL evidence is therefore no longer "the RTL path is selected" — it
is that the stylesheet served to an Arabic session has genuinely been through
the transform. §97 records the installation and what it changed.

## 94. Remaining gaps

| Not built in M6 | Owner |
|---|---|
| Award recommendation, award approval, split award | M7 |
| Purchase-order confirmation and reservation conversion | M7 |
| Construction commitment from an award | M7 |
| Vendor performance and scorecards | later |
| Expediting, material inspection | later |
| Vendor portal and supplier self-service submission | deferred |
| Cryptographic sealed bidding | not planned — see §82 |

| Known limitation of what *was* built | Why it stands |
|---|---|
| **Technical price blindness is role segregation, not sealing.** Native Purchase administration can still read an RFQ | M5 stores commercial data in native structures; claiming more would be claiming a guarantee the architecture does not provide |
| **Committee consolidation offers average or consensus only.** No weighted-evaluator or moderated-scoring mode | Neither has been asked for, and inventing a third method nobody chose is how a methodology stops meaning anything |
| **Abnormally-low detection is a manual flag**, not an automatic test | An arithmetic rule that disqualified a vendor would be an accusation the data cannot support |
| **A genuine two-process race is not exercised** | §91 states exactly what is proved instead |
| ~~Visual RTL mirroring is unverified on this host~~ | **Closed.** `rtlcss 4.3.0` installed; the flip is asserted on the served bytes and passes — §97 |

## 95. M6 completion report

**M6 evaluates the bids. M6 does not award the contract.** Every gate below was
run to confirm that, and the structural audit check `award_surface_in_m6` fails
the build if an award interface ever appears on an M6 model.

### What was added

| | |
|---|---|
| New model files | 6 — `evaluation_plan`, `evaluation_round`, `evaluation_candidate`, `commercial_analysis`, `evaluation_deviation`, `evaluation_audit` (2,112 lines) |
| New models | 12 — plan, criterion, round, assignment, candidate, technical evaluation + line, commercial analysis + adjustment, leveling line, deviation, and the audit (abstract) |
| New test files | 4 — evaluation, governance, browser, browser RTL/tablet (1,082 lines) |
| New test methods | 58 |
| Security | 3 groups (Technical Evaluator, Commercial Evaluator, Evaluation Manager), 35 ACL rows, 11 record rules |
| Other | 1 view file, 1 tour, 1 migration script, sequences |

### Regression gates — all on filestore-verified clones

| Gate | Result |
|---|---|
| Procurement M1–M6 | **386 tests, 0 failed, 0 errors** |
| Construction frozen suite | **563 tests, 0 failed, 0 errors** — the frozen count, exact |
| Both together, one database | **949 tests, 0 failed, 0 errors** (386 + 563) |

Procurement went 328 → 386; the 58 added are M6's.

### Release gates

| Gate | Result |
|---|---|
| Fresh 14-module install | 2,485 tests, **3 failed** — all three unrelated, see below |
| Full 14-module upgrade | 2,485 tests, **3 failed** — the same three |
| M5 → M6 migration, populated legacy database | 5 tenders classified, **0** M6 records created, every M5 figure unchanged |
| Migration retry | **Idempotent** — second run changed nothing |
| Browser | The tour completes under three conditions: desktop, Arabic RTL session, 768×1024 tablet with touch |

> **CORRECTION — this paragraph was wrong, and §98 replaces it.**
>
> It read that the three failures came from tests asserting `html[dir="rtl"]`,
> "a mechanism Odoo 18's backend does not use", and that the assertion was
> therefore looking in the wrong place. A read-only audit of this module went
> and read those three tours. **None of them asserts `html[dir]`.** All three
> assert `getComputedStyle(...).direction === "rtl"` — the correct mechanism —
> and the rental one goes further than anything M6 shipped, checking real
> mirrored geometry through `getBoundingClientRect()` on `inset-inline-end` and
> `margin-inline-start`.
>
> The string `html[dir]=null` came from the *diagnostic message those tours
> print when they fail*, alongside `session.lang`, the computed directions and
> the stylesheet list. It was what the test **reported**, not what it
> **asserted** — and reading a failure diagnostic as the assertion turned three
> correct probes into an imagined defect in somebody else's code.
>
> The real cause was the one this report disclosed two sections earlier and did
> not connect: `rtlcss` was absent, so the `.rtl` bundle was served unflipped
> and `direction` never became `rtl`. One missing build dependency, three red
> tests, and one M6 assertion quietly waiving itself. §98 records what happened
> when the tool was installed.

Procurement: 0 failures. Construction: 0 failures. No dependency regression.

### The migration gate, and why it was re-run

The first migration run passed against a database holding **zero** sourcing
events — it proved only that the script survives an empty table. A genuine
legacy fixture was therefore built by checking out the M5 commit into a
separate git worktree, loading *only* M5's `real_estate_procurement` ahead of
the working tree, and seeding five tenders that reach every branch of
`_classify_evaluation_readiness`. The migration was then run against that.

| Legacy tender | State | Classified |
|---|---|---|
| Open tender | published | `open_not_ready` |
| Closed, three complete offers | closed | `closed_unevaluated` |
| Closed, offers received but none administratively evaluable | closed | `legacy_external_evaluation` |
| Closed, nobody bid | closed | `ambiguous` |
| Cancelled | cancelled | `ambiguous` |

Every label is the expected one. Created: 0 plans, 0 criteria, 0 rounds, 0
candidates, 0 assignments, 0 sheets, 0 analyses, 0 deviations. Unchanged to the
cent: 5 events, 9 invitations, 5 bids totalling 13,800,000.00, 24 purchase
orders totalling 14,658,641.50, 1 requisition, 5 reservations holding
7,500,000.00. The retry reproduced all of it exactly.

### Defects M6's own tests caught

Three real, in the module:

1. **`_()` reserves the keyword `source`.** The missing-rate refusal raised
   `TypeError: get_text_alias() got multiple values for argument 'source'` — the
   safety net would have crashed the first time it fired instead of explaining
   itself. Arguments renamed.
2. **A unique index that never worked.** `is_consensus` defaulted to `NULL`, and
   PostgreSQL does not consider two NULLs equal, so the index silently permitted
   the duplicate evaluator sheets it existed to prevent. `default=False`.
3. **An authority check on the wrong group.** Reopening a submitted sheet
   demanded the *buying* manager rather than the Evaluation Manager.

Four more in the instrumentation, each fixed at the probe rather than by
weakening what it checks: a lexical price-blindness ban that flagged
`weighted_total` (a score, not money) — replaced with a type-based probe; an FX
assertion comparing recordset *order*, which `_order` includes rank in;
a tour checkpoint asserting against the list before the form had rendered; and
a row click that never opened the record. A fifth, in this session: the RTL
gate's `rtlcss` probe assumed `find_in_path` returns `None` when Odoo's own
`which` in fact raises.

### Financial isolation

M6 moves no money and holds no position. Reservations, commitments and actuals
are identical before and after every evaluation in the suite, a finalised
evaluation still cannot confirm a purchase order (`UserError`, including with
`skip_alternative_check`), and the technical evaluation model carries no
monetary field and no `res.currency` relation at all — asserted structurally,
not by naming convention.

## 96. The hardening pass — what a read-only audit found in a finished milestone

M6 was reported complete. It was then audited from the outside, against the
repository rather than against the completion message, and the audit found five
things wrong with it. Four were real defects and one was a wrong diagnosis in
this report. All five are recorded here rather than quietly corrected, because
a report that only describes the version of the work that succeeded is not
evidence of anything.

| # | Found | Severity | Section |
|---|---|---|---|
| 1 | The commercial outcome was readable by a Technical Evaluator | **release blocker** | §96.1 |
| 2 | A public, RPC-callable method auto-scored every candidate as a pass | **release blocker** | §96.2 |
| 3 | M6 had no menu entry — every screen was unreachable | **release blocker** | §96.3 |
| 4 | There was no Evaluation Report, and no way to run the integrity audit | **release blocker** | §96.4 |
| 5 | The three unrelated RTL failures were misdiagnosed in §95 | correction | §98 |

### 96.1 The commercial leak, and the three vectors underneath it

The audit's finding was that `financial_score`, `combined_score` and `rank` on
the candidate carried no field-level restriction while the ACL grants a
Technical Evaluator read on the record. Under the `lowest_ratio` formula the
financial score is a monotonic function of evaluated cost, so those three
fields disclose the **complete commercial ordering** without ever showing a
price. Zero during a first technical evaluation; live the moment a BAFO or a
second round is scored by the same committee, which is a scenario M6 supports.

That was correct, and it was not the whole defect. Closing it properly meant
finding three more ways to ask the same question:

**The `analysis_id` pointer and `is_tied`.** Both belong to the same set. A tie
at rank 1 names which vendors evaluated identically, which is a commercial fact
about specific bidders.

**The domain.** Odoo's expression engine does not consult field groups —
`osv/expression.py` has no such check. So `search([('rank', '=', 1)])` would
have answered honestly however the fields were restricted.

**The order, and this is the one that was invisible.** `_order` was
`'round_id, rank, id'`. A Technical Evaluator calling `search([])` would have
been handed the vendors already arranged in the order the money put them in,
having read no restricted field at all. A field restriction cannot see that
coming, because nothing was read.

The fix is `groups=` on the five fields plus guards on `_search` and
`_read_group` that refuse a restricted field appearing in a domain, an order,
a groupby, an aggregate or a `having` clause — and `_order` no longer names
`rank`.

**And a fourth thing, discovered by the fix rather than by the audit.** Odoo 18
resolves ORDER BY through `_order_field_to_sql`, which calls
`check_field_access_rights`; ordering chains through many2ones. With `rank`
restricted and still named in `_order`, reading an entirely unrelated
`sheet.line_ids` raised `AccessError` three models away — line → sheet →
candidate → rank. Restricting the field without taking it out of `_order` would
have shipped a module that broke technical evaluation for technical evaluators.

The gate is `group_procurement_user`, and it is chosen rather than convenient.
Every commercially entitled role implies it — Commercial Evaluator directly,
Evaluation Manager and Procurement Manager transitively — while
`group_evaluation_technical` implies nothing at all, which is precisely what
keeps a technical evaluator away from the bid register.

One consequence is stated rather than hidden: **a Technical Evaluator who is
also a Buyer can see the ranking.** That is correct. A Buyer reads the bid
register and every price in it natively; hiding a derived score from somebody
holding the raw numbers would be theatre.
`test_a_technical_evaluator_who_is_also_a_buyer_may_see_it` asserts it on
purpose, so nobody later reads it as a hole.

The BAFO shortlist went with them. Under a `selective` policy,
`invited_partner_ids` and `shortlist_reason` name the vendors the buyer wants a
better number from and say why — a commercial judgement, restricted on the same
gate.

### 96.2 The public method audit

Every `action_*` on every M6 model was read against six questions: role,
company, project, state, plan freeze, and commercial stage. The ACL answers
"may this account touch the table" and cannot answer "may this person open the
prices", and M6 had been relying on the first to mean the second.

The worst finding was `action_open_commercial_after_technical`. It was a test
helper living on the production model, justified by the browser gate needing
the same sequence — and it was **public, therefore callable over RPC by anybody
the ACL let write on a round**. One call gave every bid in a live tender a
submitted passing sheet that no evaluator had written, then drove the round to
the commercial stage. It has moved to `tests/common.py::M6Common._advance_to_
commercial`, which the browser classes inherit, so the sequence is still shared
and production no longer ships it.

| Method | Was | Now |
|---|---|---|
| `round.action_open_technical` / `_finalise_technical` / `_open_commercial` / `_finalise` / `_open_bafo` | ACL + state only | + Evaluation Manager or Procurement Manager |
| `round.action_normalise` | ACL + state only | + those two or Commercial Evaluator |
| `round.action_cancel` | **no state check, no reason** — a finalised evaluation could be cancelled silently | refuses a finalised round; reason required |
| `plan.action_freeze` / `action_new_revision` | ACL only | + Evaluation Manager or Procurement Manager |
| `assignment.action_declare` | anybody with write could declare "no conflict" **for somebody else** | the member themselves, or whoever runs the round |
| `assignment.action_clear_conflict` | self-clear refused; anyone else allowed | + management authority |
| `sheet.action_submit` | creation was guarded, submission was not | the sheet's own evaluator, or a manager |
| `analysis.action_flag_for_review` | no state check | refuses a finalised round |
| `adjustment.action_approve` | self-approval refused; no state check | + refuses a finalised round |
| `audit.run` | **no check at all** — an AbstractModel method naming every bid and vendor in the company | Evaluation Manager or Procurement Manager |

### 96.3 Navigation

M6 defined three actions and **zero menu items**. The screens were reachable
only by typing an action URL, which is exactly how the browser tour had been
navigating — so the gate passed while the milestone was unreachable.

An Evaluation section now sits under the existing Procurement root, holding
Evaluations, Evaluation Plans, Technical Evaluations and the Integrity Audit.
It reuses all three existing actions rather than duplicating them.

One thing had to change to make it work. `menu_procurement_root` was gated on
`group_procurement_user`, and `group_evaluation_technical` implies nothing — so
a Technical Evaluator could not see the Procurement menu at all and therefore
could not reach the screens the role exists to use. The root now admits the
technical group as well; Odoo prunes entries whose action a user cannot access
and then prunes the empty parents, so a Technical Evaluator sees the root with
Evaluation under it and nothing else. Six tests assert exactly who sees what,
including that a Requester and a generic Odoo Purchase user see no M6 menu.

The tour now walks that hierarchy instead of jumping to an action URL — and
doing so immediately caught something the direct URL had hidden: at 768×1024
the section bar collapses into a "More Menu" dropdown, so the tablet run failed
where desktop passed. The tour opens whichever container this viewport put the
menu in, and polls rather than sleeping a guessed interval, because a fixed
delay was long enough for the desktop run and not for the RTL one.

### 96.4 The Evaluation Report, and the audit's user interface

**The report.** M6 produced no document. Everything a tender file needs to
prove existed in the database and none of it could be printed, signed or filed.
`report/evaluation_report.xml` renders the frozen basis, the committee and
their conflict declarations, the frozen candidate set with its exclusions, the
technical criteria and results, the commercial analysis with its FX snapshot
and itemised adjustments, the ranking with ties, deviations, and the audit
trail. It states **EVALUATION RESULT ONLY — NO AWARD IS CREATED BY THIS
REPORT** at the top and the bottom, and a test fails the build if the words
*winner*, *awarded*, *award recommendation*, *approved vendor*, *contract
award* or *selected supplier* ever appear in the rendered output.

Reproducibility was audited field by field rather than assumed, and it turned
up one genuine gap. Every commercial figure was already snapshotted — raw
amount, raw currency, rate, rate date, adjustments, evaluated cost — and so
were the criteria on each sheet and the evaluator names. **The vendor's name
was not.** `bid_response.partner_id` is a related field all the way back to
`res.partner`, so a vendor renamed after the event — a merger, a change of
legal name — would silently restate who the committee had evaluated, on the one
document whose purpose is to say exactly that. `candidate.partner_name` is now
snapshotted when the candidate is created, with no fallback to the live partner
anywhere: a blank must read as blank rather than quietly borrowing today's
answer. `test_the_report_does_not_drift_when_the_world_moves` renames a vendor
and publishes a new exchange rate after finalisation, then asserts the two
renderings are byte-identical.

Two independent guards keep the commercial half of it away from technical
evaluators, and the test names both. `action_print_evaluation_report` refuses
to issue it. Rendering the template directly — the path the `/report/…` HTTP
route takes, which performs no group check of its own — fails as well, because
the header alone reads the sourcing event and a technical evaluator has no
access to the tender. Underneath both, `groups=` on the commercial sections
means QWeb never compiles those figures into the output.

**The audit.** `evaluation.audit.run()` was complete, tested, and callable only
from Python. A `TransientModel` wizard now runs it and shows the scope, the
timestamp, the critical/high/medium/low counts and every finding with its
references and remediation. Deliberately not a dashboard, and deliberately
without a "fix" button: the audit reports and never repairs, and
`test_it_repairs_nothing` holds that line. An empty scope says *there was
nothing to audit* rather than reporting clean, because those are not the same
statement.

## 97. `rtlcss`, and what installing it changed

`rtlcss` was absent from this host. Odoo 18 does not mark the backend with
`html[dir="rtl"]`; direction is delivered by serving a different stylesheet —
the `.rtl` bundle, produced by running the compiled CSS through `rtlcss`. Where
the tool is missing Odoo logs a warning and serves the **unflipped** stylesheet
under the `.rtl` name, so the bundle is selected and the pixels are not
mirrored.

`npm install -g rtlcss` installed 4.3.0. `sudo` was neither required nor
correct: Node here is nvm-managed and `npm root -g` is
`~/.nvm/versions/node/v24.19.0/lib/node_modules`, owned by the user — a
`sudo npm install -g` would have installed into the wrong environment. Odoo's
own `find_in_path('rtlcss')` resolves it in the environment the tests run in,
which is the check that matters and is recorded in the evidence directory.

What changed: `test_the_rtl_bundle_is_actually_flipped` no longer takes its
early return. It fetches the `.rtl` bundle and the LTR bundle and fails if they
are byte-identical, and it passes. The RTL claim in this report is therefore no
longer "the RTL path is selected" — it is that the stylesheet served to an
Arabic session has genuinely been through the transform.

## 98. The three RTL failures — the misdiagnosis, and the truth

§95 said the three failures in `atmta_real_estate`, `real_estate_brokerage` and
`real_estate_checks` came from tests asserting `html[dir="rtl"]`, "a mechanism
Odoo 18's backend does not use", and concluded the assertions were looking in
the wrong place.

That was wrong, and it was wrong in a way worth recording. **None of the three
asserts `html[dir]`.** All three assert `getComputedStyle(...).direction ===
"rtl"` — the correct mechanism — and each carries a comment explaining that
Odoo sets direction on `.o_action_manager` rather than on the root element. The
rental one goes further than anything M6 shipped: after the direction check it
asserts real mirrored geometry through `getBoundingClientRect()`, proving that
`inset-inline-end` resolves to the left edge and `margin-inline-start` became
`margin-right`.

The string `html[dir]=null` came from the **diagnostic message those tours
print when they fail** — one of five diagnostics alongside `session.lang`, the
computed directions and the stylesheet list. Reading a failure diagnostic as
though it were the assertion turned three correct probes, written by other
sessions, into an imagined defect in their code. The lesson is narrow and
practical: when a test reports what it saw, that is not what it demanded.

The actual cause was the one disclosed two sections earlier in this same report
and never connected to it — `rtlcss` was absent, the `.rtl` bundle was served
unflipped, `direction` never became `rtl`, and the geometry could not mirror.
One missing build dependency, three red tests in other modules, and one M6
assertion quietly waiving itself.

Not one line of those three tests was modified.

## 99. M6 release freeze

**M6 evaluates the bids. M6 does not award the contract.** Every gate below was
re-run against one identical source hash after the hardening in §96, and every
log is retained outside the repository — which is the other thing this freeze
had to fix.

### The evidence problem, and what was done about it

The first M6 completion report quoted `386 / 0 / 0`, `563 / 0 / 0` and
`949 / 0 / 0`. Those runs happened. Their logs did not survive: the harness and
every gate log lived in a session scratchpad, and the scratchpad was emptied.
A later read-only audit could re-derive the *suite sizes* by counting test
methods, and could re-verify the migration by querying the two databases that
happened to still exist, but the pass/fail lines themselves were gone.

A number nobody can reproduce is not release evidence, whatever it said. So
those figures are treated here as historical claims and not as gates, and every
gate in this section was run again from scratch with its log written to

```
~/atmta_release_evidence/m6_freeze_<timestamp>/
```

Each log records the timestamp, the branch, HEAD, a hash of the M6 source it
tested, the database, the filestore file count, the exact command, the module
flags, the test tags, the exit code and the result line. `SHA256SUMS.txt` covers
the directory. The harness itself now lives in
`~/atmta_release_evidence/bin/` rather than in a scratch directory that gets
deleted.

### Release gates — every one re-run at source hash `103f5981d7ef3f9b`

| Gate | Result |
|---|---|
| A — M6 focused (`atmta_m6`) | **118 tests, 0 failed, 0 errors** |
| B — Procurement M1–M6 | **446 tests, 0 failed, 0 errors** |
| C — Construction (frozen suite) | **563 tests, 0 failed, 0 errors** |
| D — Combined, one database | **1009 tests, 0 failed, 0 errors** |
| E — Security | **76 tests, 0 failed, 0 errors** |
| F — Multi-company | **10 tests, 0 failed, 0 errors** |
| H — Concurrency | **34 tests, 0 failed, 0 errors** |
| I — Currency / FX normalisation | **4 tests, 0 failed, 0 errors** |
| K — Browser, desktop, via the real menu | **3 tests, 0 failed, 0 errors** |
| L — Browser, Arabic RTL | **4 tests, 0 failed, 0 errors** |
| M — Browser, 768×1024 touch | **1 tests, 0 failed, 0 errors** |
| N — Evaluation Report | **10 tests, 0 failed, 0 errors** |
| N — Evaluation Report, real PDF | **1 tests, 0 failed, 0 errors** |
| O — Integrity audit + wizard | **21 tests, 0 failed, 0 errors** |
| P — Fresh 14-module install | **2545 tests, 0 failed, 0 errors** |
| Q — Full 14-module upgrade | **2545 tests, 0 failed, 0 errors** |
| Phase 9 — the three legacy RTL tests, unchanged | **3 tests, 0 failed, 0 errors** |
| G — Project isolation | **not applicable** — see below |
| J — Native purchase boundary | asserted in gates A, B, E: `button_confirm()` refused, commitment 0.00 |
| R — M5→M6 populated migration | see `18_migration_first.log` |
| S — Migration retry | see `19_migration_retry.log` |


### The M5 → M6 migration, on a populated legacy database

The legacy database was built with the M5 commit's own procurement code
prepended to the addons path — genuinely M5 at `18.0.5.0.0`, with no evaluation
models and no `evaluation_readiness` column — while the other thirteen modules
stayed at working-tree state, which is what a real upgrade looks like. Five
tenders were seeded through the M5 public API, one per branch of
`_classify_evaluation_readiness`.

| Legacy tender | State | Classified |
|---|---|---|
| Open tender | published | `open_not_ready` |
| Closed, three complete offers | closed | `closed_unevaluated` |
| Closed, offers received, none administratively evaluable | closed | `legacy_external_evaluation` |
| Closed, nobody bid | closed | `ambiguous` |
| Cancelled | cancelled | `ambiguous` |

Five for five. **Created by the migration: 0 plans, 0 criteria, 0 rounds, 0
candidates, 0 assignments, 0 sheets, 0 sheet lines, 0 analyses, 0 adjustments,
0 leveling lines, 0 deviations.**

Unchanged to the cent, before the migration, after it, and after the retry:

| | Before | After | Retry |
|---|---|---|---|
| Sourcing events | 5 | 5 | 5 |
| Invitations | 9 | 9 | 9 |
| Bid responses | 5 | 5 | 5 |
| Bid total | 12,899,995.00 | 12,899,995.00 | 12,899,995.00 |
| Purchase orders | 24 | 24 | 24 |
| Purchase order total | 13,683,635.75 | 13,683,635.75 | 13,683,635.75 |
| Requisitions | 5 | 5 | 5 |
| Reservations | 5 | 5 | 5 |
| Amount reserved | 8,700,000.00 | 8,700,000.00 | 8,700,000.00 |

The retry rewound `ir_module_module.latest_version` to `18.0.5.0.0` and ran the
upgrade again, so `post-migrate.py` genuinely executed a second time.
**IDEMPOTENT = YES**, exit 0, nothing moved.

### The three RTL tests in other modules

Run unchanged after `rtlcss` was installed: `TestDashboardRTL`
(atmta_real_estate), `TestBrowserRTL` (real_estate_brokerage) and
`TestTreasuryDashboardRTL` (real_estate_checks) — **3 tests, 0 failed, 0
errors**. They had been failing for one reason and it was never their own: the
`.rtl` bundle was served unflipped. Not one line of them was modified, and the
full 14-module suite is green for the first time in this programme.

### Test inventory

```
atmta_real_estate                  299
real_estate_api                    0
real_estate_brokerage              352
real_estate_checks                 325
real_estate_construction           563
real_estate_contract_template      0
real_estate_customer_service       0
real_estate_developer              247
real_estate_handover               0
real_estate_investment             0
real_estate_maquette               291
real_estate_plan                   15
real_estate_portal                 7
real_estate_procurement            446
-----------------------------------------
TOTAL (all 14 modules)             2545
```

Procurement went 386 → 446; the 60 added are the hardening pass. Construction is 563, exact and unchanged.

### What the browser gate proves now

The tour walks the real menu — Procurement → Evaluation → Evaluations — instead
of jumping to an action URL, and it does that at three viewport and locale
combinations with nothing relaxed for the harder two. Making it navigate
properly immediately caught something the direct URL had hidden: at 768×1024
the section bar collapses into a "More Menu" dropdown, so the tablet run failed
where desktop passed.

RTL is no longer a partial claim. `rtlcss 4.3.0` is installed,
`test_the_rtl_bundle_is_actually_flipped` compares the served bytes rather than
logging a waiver, and it passes.

### Financial isolation, re-proved

Reservation 3,000,000.00 held throughout; Construction commitment 0.00;
Construction actual 0.00 — asserted at five points across a full evaluation
(before the plan, after the round opens, after technical finalisation, after
commercial normalisation, after finalisation) and again after a BAFO round. A
finalised evaluation still refuses `button_confirm()` on the winning RFQ,
including with `skip_alternative_check=True`, and the order stays `draft`.

None of that rests on `_check_award_surface` alone. The audit check is
structural and it is one of four independent guarantees; the other three are
behavioural assertions against the Construction position service.

### What this freeze does not claim

**Project-level access control does not exist, in M6 or anywhere else in this
suite.** A release gate asked whether a "Project A-only user" can read Project
B's evaluation evidence. No such user can be constructed: there is no
project-membership model and no project-scoped `ir.rule` in M6, and none in M2
through M5 either. Isolation is drawn at the company, and that is enforced and
tested — eleven global company rules, and a company-B manager who sees nothing
of company A across five models including the commercial analysis.

Building project-level access control inside M6 would give evaluation a
security model no other milestone has, and it would be an architectural change
smuggled in as hardening. It is recorded as a suite-wide gap that M6 neither
introduced nor closed. `TestM6ProjectScope` pins what is true today —
`project_id` stored and indexed on every M6 model, so the rule is cheap to add
— and fails if anyone adds a project rule without updating this claim.

**Technical price blindness remains role segregation, not sealed bidding.** M5
keeps commercial data in native Odoo purchase structures, so a user with
sweeping native Purchase administration can still read an RFQ. What M6
guarantees is that nothing it builds hands price, or the ordering that implies
price, to a technical evaluator — now enforced at the field, the domain, the
sort order, the aggregate, the report and the menu.

**A genuine two-process race is still not exercised.** Odoo's harness shares one
cursor. §91 states what is proved instead, and the new lifecycle authority
checks did not change that.
