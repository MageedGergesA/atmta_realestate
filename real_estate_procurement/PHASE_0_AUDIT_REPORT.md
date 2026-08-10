# Phase 0 — `real_estate_procurement` audit

What the module does today, established by reading every line of it and by
running 26 tests against it. Nothing here is taken from the module's own
description; where the description and the code disagree, the code wins.

Baseline: **1,131 lines**, 6 model files, 6 view files, **no tests before this
audit**, module version `0.1`.

---

## Architecture

| Concern | Model | Verdict |
|---|---|---|
| Request header | `realestate.material.request` | Owns the workflow |
| Request line | `realestate.material.request.line` | Owns quantities |
| Approval rule | `realestate.procurement.approval.rule` | Amount-bracket → group |
| Approval step | `realestate.procurement.approval.step` | Snapshot per request |
| Purchase order | **`purchase.order` (Odoo)** | Correctly not duplicated |
| Receipts | **`stock.picking` (Odoo)** | Correctly not duplicated |
| Vendor | **`res.partner` (Odoo)** | Correctly not duplicated |
| Vendor bill | **`account.move` (Odoo)** | Correctly not duplicated |

Extensions: `purchase.order` gains `re_project_id`, `re_source_model`,
`re_source_id`, `is_realestate_po`; `purchase.order.line` gains
`re_material_request_line_id`; `product.template` gains four real-estate flags;
`res.partner` gains vendor tags.

**The single most important finding is a good one:** this module never built a
competing purchase order, receipt, vendor or bill model. Rules 1–3 of the
milestone brief are already satisfied structurally. The work ahead is
governance and project control, not undoing a parallel ERP.

---

## Existing workflow

```
Material Request (draft)
  → submit  ─┬─ priority = Urgent ──────────────► approved   [no approval at all]
             └─ otherwise → submitted → approve → approved
  → action_create_purchase_orders()
        groups lines by product.seller_ids[0]
        creates one PO per supplier
        **calls button_confirm() immediately**
  → state = ordered
  → receipts roll the state to partial / received
```

There is no plan, no requisition-to-sourcing step, no RFQ stage, no bid, no
comparison, no award and no expediting. The lifecycle in the brief has
seventeen stages; the module implements approximately four of them.

---

## Odoo-native features already used well

- `purchase.order` / `purchase.order.line` as the commercial document.
- `product.supplierinfo` for vendor pricing.
- `stock.picking` for receipts, with `_prepare_stock_moves()` overridden to
  route a project order to the project's stock location — a clean, correctly
  placed override that must survive the redesign.
- `account.move` untouched by Procurement.
- Odoo's `qty_received` read rather than recomputed.

## Duplicated Odoo features

**None material.** The one duplication is conceptual rather than structural:
`action_create_purchase_orders()` re-implements vendor selection
(`seller_ids[0]`) instead of using Odoo's RFQ/alternatives machinery.

---

## Answers to the five architectural questions

### Q1 — Does Procurement duplicate anything Odoo Purchase owns?

**No model duplication.** But it bypasses the quotation stage entirely:
`action_create_purchase_orders()` calls `button_confirm()` on the orders it
creates, so nothing is ever a quotation and nothing can be compared.

Vendor selection is `product.seller_ids[0]` — whichever supplier row sorts
first wins, regardless of price. A cheaper supplier on the same product is
never asked.

*Pinned by* `test_defect_creating_purchase_orders_skips_the_quotation_stage`,
`test_defect_the_vendor_is_chosen_by_list_order_not_by_competition`.

### Q2 — At exactly which event does Construction Commitment appear?

Commitment appears **when a purchase order is confirmed**, and is computed by
Construction's own `commitment` service from confirmed PO lines. Procurement
does not write commitment and must not start.

Confirmed today:

| Event | Commitment |
|---|---|
| Material request created | 0 |
| Request submitted | 0 |
| Request approved | 0 |
| Draft PO | 0 |
| **Confirmed PO** | **the untaxed line total** |

The defect is not *where* commitment appears but *how quickly*: approving a
request and clicking one button produces a confirmed PO, so a project goes
from no obligation to a committed one with no sourcing event in between.

*Pinned by the four tests in* `TestQ2WhereCommitmentAppears`.

### Q3 — Can one obligation be counted more than once?

**Not today, because there is nothing to double-count with.** Construction
already enforces package-versus-PO precedence, and Procurement holds no
reservation, encumbrance or commitment figure of its own.

That is also the risk: because nothing is reserved, **two approved requisitions
can each consume the same remaining budget**. Two requests of 8,000,000 both
reach `approved` against a 10,000,000 budget with nothing checked, nothing
reserved and nothing warned.

The M3 reservation layer must therefore be built knowing it is the *first*
thing capable of double-counting against commitment.

*Pinned by* `TestQ3DoubleCounting` (three tests).

### Q4 — Does every PO line carry Project + WBS + Cost Code?

**No — and this is the most consequential defect in the module.**

`action_create_purchase_orders()` writes:

```python
po_line_vals['analytic_distribution'] = {str(self.analytic_account_id.id): 100.0}
```

That is the **project** analytic account only. `re_cost_code_id` and
`re_wbs_id` are never set, so every purchase order line this module generates
lands under **Unassigned** in the Construction cost sheet: real committed
money that cannot be attributed to any part of the works.

Worse, the requisition cannot capture the coding even if somebody wanted to —
`realestate.material.request.line` has no `cost_code_id` and no `wbs_id`
field. There is nowhere to put it.

Hand-coded orders work correctly, which proves the Construction side is
sound and the gap is entirely on the Procurement side.

*Pinned by* `TestQ4CostCodePropagation` (three tests).

### Q5 — Can governance be bypassed?

**Three separate ways, all of them trivial.**

1. **Priority = Urgent skips approval entirely.** `action_submit()` promotes an
   urgent request straight to `approved`, stamps the requester as the
   approver, and generates no approval step. The requester chooses the
   priority, so the requester chooses whether their own request needs
   approving.
2. **A requester can approve their own request.** `action_approve()` checks
   group membership and nothing else. No separation of duties exists anywhere
   in the module.
3. **The entire module is optional.** Any user with purchase rights and
   project access can create and confirm a project purchase order — real
   commitment against a real budget — with no requisition, no approval, no
   enquiry and no award. `re_material_request_line_id` is simply empty.

*Pinned by* `TestQ5GovernanceBypass` (four tests).

---

## Budget / commitment defects

| # | Defect | Severity |
|---|---|---|
| B1 | No budget check of any kind. A request never consults Construction's budget | **critical** |
| B2 | No reservation, so several approved requests consume the same remaining capacity | **critical** |
| B3 | Generated PO lines carry no cost code → commitment lands under Unassigned | **critical** |
| B4 | `estimated_cost` is `seller_price × qty`, silently falling back to `standard_price`, then to **zero** — and zero clears every approval bracket | **high** |
| B5 | No procurement type, so a service request is pushed through material expectations | **medium** |

On tax: `product.supplierinfo.price` and `standard_price` are untaxed, and PO
lines are created without taxes, so the current estimate is accidentally
tax-exclusive. It is right for the wrong reason — nothing states or enforces
it, and Rule 6 needs it stated.

## Approval defects

| # | Defect | Severity |
|---|---|---|
| A1 | Urgent priority bypasses approval completely | **critical** |
| A2 | Requester may approve their own request | **critical** |
| A3 | Approval rules matched against `self.env.company`, not the request's company | **high** |
| A4 | No approval on the **award** or the **purchase order** — only on the request | **high** |
| A5 | Amount basis is the flawed `estimated_total`; a zero estimate matches the lowest bracket | **high** |
| A6 | `ApprovalStep.action_approve()` resolves the group through `get_external_id()` with a fallback — convoluted, though functionally correct | low |
| A7 | Re-submission unlinks unapproved steps, so an approval history can be partially erased before it completes | medium |

## Tender / evaluation gaps

Everything. There is no sourcing event, no RFQ governance, no invited vendor
list, no bid record, no bid revision history, no clarification, no addendum, no
technical evaluation, no commercial evaluation, no normalisation, no BAFO, no
award recommendation and no award approval.

Consequence for Rule 21 (bid confidentiality): there is nothing to protect
yet, and therefore nothing protecting it. The sealed-evaluation requirement
must be designed in from the first tender model, not retrofitted.

## Delivery / inspection gaps

- No expediting, no milestones, no forecast delivery date, no long-lead flag.
- `needed_by` exists on the header only — not per line, where the brief needs
  required-on-site dates.
- Receipts are read from Odoo correctly (`qty_received`), and quantities are
  **not** duplicated — good.
- `_compute_received()` calls `request_id._refresh_state_from_lines()` **from
  inside a compute**, writing to another record. The request's state therefore
  depends on when the cache happened to be invalidated.
- No material inspection link to Construction QA/QC. Construction M6 already
  has `inspection_type = 'material'` with `purchase_order_id` and `picking_id`
  fields waiting — the hook exists and is unused.

## Vendor governance gaps

No prequalification, no approved vendor list, no category or trade scoping, no
validity or expiry, no suspension, no performance measurement of any kind.
Vendor tags on `res.partner` are the whole of it.

## Security gaps

| # | Gap | Severity |
|---|---|---|
| S1 | **No record rules at all** — nothing scopes a request to a company or a project | **critical** |
| S2 | No `company_id` on the request, its lines, or approval steps | **critical** |
| S3 | Three groups only (User / Approver / Manager); no requester, buyer, technical evaluator, commercial evaluator or project-manager separation | **high** |
| S4 | Line-level ACL grants `unlink` to plain users while the header does not — a user may empty an approved request | **high** |
| S5 | Approval rules readable by all users, including their amount brackets | low |

## Multi-company gaps

Not merely incomplete — absent. A request has no company, so nothing prevents
a request in one company from pointing at another company's project, vendor
or purchase order. `_generate_approval_steps()` compounds it by matching rules
against whichever company the approver has active.

## Performance risks

Nothing aggregates today, so there is nothing slow — but nothing to build on
either. Two patterns to avoid carrying forward: `_compute_line_count` is not
stored and not `@api.depends`-decorated (recomputed per read), and
`_compute_purchase_orders` walks `line_ids.po_line_id.order_id` per record.

Both are trivial at current data volumes and must not be replicated in the
dashboard work, where the brief assumes tens of thousands of requisitions.

## Migration risks

| Risk | Detail |
|---|---|
| Existing requests | `realestate.material.request` records exist in production and must survive with their references |
| Existing POs | Purchase orders created outside the module are ordinary Odoo POs — **legacy direct purchases**. They must not have requisitions manufactured for them |
| Missing cost codes | Every historic generated PO line lacks a cost code. It cannot be derived from a header analytic that only carries the project — this is genuinely ambiguous and must be classified, not guessed |
| Approval history | Existing `approval.step` rows reference rules that may since have changed. The snapshot design is sound; the data must not be re-derived |
| No company | Assigning a company retrospectively is deterministic only where the project or PO says so |

## Cross-module observations (not defects in this module)

- Confirming a project purchase order **writes to `realestate.project`**,
  because `_get_re_project_location()` lazily creates and stores the project's
  stock location. A user with read-only project access therefore cannot
  confirm a project PO. Noted, not changed: it belongs to whoever owns
  `_get_stock_location()`.
- Setting `re_cost_code_id` as a plain purchase user raises an `AccessError`
  from inside Construction's `create()` override, which reads the cost-code
  model. So the *uncoded* path succeeds and the *correctly coded* path fails.
  Procurement's M2 work must give buyers the read access the coding requires.

---

## Recommended architecture

Keep the module's one good instinct — Odoo owns the documents — and build
governance around it.

1. **Do not touch** `purchase.order`, `stock.picking`, `account.move` or any
   Construction service. Extend and consume only.
2. **Add the missing dimension to the requisition line**: WBS and cost code,
   with the same optionality Construction uses (uncoded is visible as
   Unassigned, never hidden).
3. **Split the one button into the lifecycle it collapsed**: requisition →
   sourcing event → RFQ (Odoo alternatives) → evaluation → award → *then* a
   purchase order that somebody confirms deliberately.
4. **Add a reservation layer** that is explicitly neither commitment nor
   actual, released when the PO confirms, with the double-count test pinned
   from the first commit.
5. **Replace priority-based approval bypass** with configured emergency
   procurement that records who authorised the exception.
6. **Enforce separation of duties server-side**, following the pattern
   Construction already uses for maker/checker.
7. **Add company and project scoping** to every model, with global company
   rules, exactly as Construction M9 did.

The financial invariant to build around, from the brief:

```
Budget 10M → Approved requisition 3M → Reservation 3M, Commitment 0
           → RFQ                     → Commitment 0
           → Award approved          → Commitment 0
           → PO confirmed 3M         → Reservation released, Commitment 3M
           → Vendor bill 1M          → Actual 1M
           → Payment                 → Actual still 1M
```

At no point may the same 3,000,000 become 6,000,000 because it exists in two
workflow layers.

---

## Phase 0 test baseline

`tests/test_phase0_baseline.py` — **26 tests, 0 failed**, run against the full
fourteen-module suite.

| Class | Tests | Records |
|---|---|---|
| `TestQ1PurchaseOwnership` | 3 | Odoo owns the PO; quotation stage skipped; vendor picked by list order |
| `TestQ2WhereCommitmentAppears` | 4 | Commitment appears exactly at PO confirmation |
| `TestQ3DoubleCounting` | 3 | Package/PO counted once; no reservation exists; two requests can overspend |
| `TestQ4CostCodePropagation` | 3 | Hand-coded works; generated lines uncoded; line cannot hold a code |
| `TestQ5GovernanceBypass` | 5 | Urgent bypass; self-approval; direct PO bypass; coded direct PO raises AccessError; company-scoped rules |
| `TestPhase0StructuralGaps` | 5 | No company, no record rules, no type, estimate-is-a-hint, compute writes |
| `TestPhase0WorthKeeping` | 3 | Source linkage, cancel guard, project stock routing |

Eleven of these are `test_defect_…` and are expected to be **converted into
positive regressions** as each milestone fixes the behaviour they pin — the
same discipline used throughout the Construction programme.
