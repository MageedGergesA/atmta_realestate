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
| A5 | Amount basis is the flawed estimate | Partly fixed — M2 made the flaw visible (`estimate_is_known`), M3 converts the basis to company currency, and an unknown estimate still brackets as zero (§33) |
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
ones Construction's own suite exercises, on the day of an upgrade. §33 states
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
  with no Construction read still cannot code a purchase line (§33).

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

## 33. Remaining gaps, stated plainly

M3 guarantees one thing about a confirmed purchase order:

```
    IF A PO IS CONFIRMED, IT WAS FINANCIALLY AUTHORISED.
```

It does **not** guarantee:

```
    THE BEST VENDOR WAS SELECTED.
```

That distinction is the whole of the difference between M3 and M4–M7, and
nothing in this module should be read as claiming the second.

### Not built in M3, and not stubbed

| Missing | Owner |
|---|---|
| Vendor prequalification and approved-vendor list | M4 |
| Competitive tender, alternative-RFQ orchestration | M5 |
| Technical evaluation, commercial evaluation, bid levelling, BAFO | M6 |
| Formal award recommendation and split-award interface | M7 |
| Expediting, material inspection, vendor scorecards, procurement dashboard | M8+ |
| Full emergency-procurement workflow (M3 only removed the urgency bypass) | M24 |

### Known limitations of what *was* built

| Limitation | Why it stands |
|---|---|
| **Purchase governance defaults to `optional`.** Out of the box the direct-PO bypass is closed by configuration, not by default | Shipping `controlled` would refuse confirmation on every existing project purchase-order path on upgrade day, including the ones Construction's own suite exercises. The runbook says which switch to throw |
| **Budget policy defaults to `warn`** — nothing is refused until a company chooses | Same reason. Reservations, positions and exception evidence are all live from the first minute |
| **A genuine two-process race is not exercised by the suite** | Odoo's test harness shares one connection; §13 states exactly what is proved instead |
| **A purchase user with no Construction read still cannot code a PO line** — `test_defect_a_buyer_coding_a_line_hits_an_access_error` remains a defect test | Fixing it means widening Construction's cost-code read, which the frozen-module rule and the M2 brief both forbid. The practical answer is that anybody coding to a cost code has Construction User |
| **An unknown estimate still brackets as zero** in the approval matrix. `estimate_is_known` and the data-quality warning surface it; the matrix does not refuse it | Refusing every requisition with an unpriced line would stop legitimate early demand. Naming the unknown is the honest half that exists today |
| **No cost-code-level policy override** | Cost codes are Construction's; a precedence level that only existed when another module was installed would be worse than not having it |
| **Reservation expiry is off by default** | An expiry nobody chose would release real demand on a date nobody knew about |
| **PO confirmation still writes to `realestate.project`** via lazy stock-location creation | M7 PO Confirmation Integration Gate, unchanged from M2 |
| **Line-level ACL still grants `unlink` to plain users** | The active-reservation guard now refuses deletion of demand that holds capacity; the ACL row itself is untouched |
| **Requisition-level material-inspection link** unused | M8 |

Nothing in this list is disguised as finished.
