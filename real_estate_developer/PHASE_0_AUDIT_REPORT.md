# `real_estate_developer` — Phase 0 Audit

**Audited:** the code as it actually is, not as the documentation describes it.
**Module size:** 3,759 lines total; 1,183 lines of Python across 11 model files.
**Test coverage:** **zero.** There is no `tests/` directory.

The single most important finding is in §4.1: **nothing in the suite creates
`realestate.sale.installment` records any more**, while three modules still
consume them. The developer installment schedule — the object Checks, the
customer API and the dashboard are all built on — is empty in every database
upgraded past version 0.2.

---

## 1. Current data model

### 1.1 Models owned by this module

| Model | Lines | Purpose | `company_id`? | Constraints |
|---|---|---|---|---|
| `realestate.project` | 184 | Development project | **no** | none |
| `realestate.project.boundary.point` | 34 | Plot polygon vertex | **no** | none |
| `realestate.phase` | 58 | Project phase | **no** | none |
| `realestate.unit.reservation` | 158 | Hold / booking | **no** | none |
| `realestate.sale.contract` | 174 | Sale contract | **no** | none |
| `realestate.sale.installment` | 89 | Commercial obligation | **no** | none |
| `realestate.payment.term.segment` | (in `account_payment_term.py`) | Payment-term schedule segment | **no** | none |

Extensions: `realestate.property` (+9 fields), `account.payment.term` (+14
fields and a line generator), `res.partner`, `sale.order` bridge usage.

**There is not one `_sql_constraints` entry and not one `@api.constrains`
method in the entire module.** Project codes are not unique. Sale prices are
not required to be positive. Nothing prevents two live reservations on one
unit. Nothing checks that a phase belongs to the same project as the unit
(only a view-level `domain`, which is not a constraint).

Only three indexes exist: `project.code` (trigram), `boundary.project_id`,
`partner.developer_project_count`. `reservation.property_id`,
`reservation.state`, `sale_contract.property_id`, `installment.date_due` — all
of them queried by crons and the dashboard — are unindexed.

### 1.2 State machines as they really are

| Model | Real values | Documented as |
|---|---|---|
| `project.state` | `planning, construction, marketing, handover, completed, cancelled` | — |
| `phase.state` | `planning, construction, ready, delivered, cancelled` | — |
| `reservation.state` | `hold, booked, confirmed, expired, cancelled` | matches |
| `sale.contract.state` | `draft, signed, handed_over, cancelled` | **"draft/signed/completed"** — wrong |
| `installment.state` | `pending, invoiced, paid, cancelled` | matches |

`sale.contract.state` has **no `completed` value**. The terminal state is
`handed_over`. Any specification written against `completed` describes a state
that does not exist, and `real_estate_handover` depends on the real one.

`project.state` conflates commercial and construction meaning: `marketing` is a
sales state, `construction` and `handover` are physical states, and they are
mutually exclusive in one field. A project that is both under construction and
selling — the normal case for off-plan — cannot be represented.

---

## 2. Current workflows

```
Project (planning → construction → marketing → handover → completed)
  └─ Phase (planning → construction → ready → delivered)
       └─ Property  [Module 1, frozen]  + project_id / phase_id / base_price
            └─ Reservation  hold → booked → confirmed
                 └─ Sale Contract  draft → signed → handed_over
                      ├─ sale.order (bridge)  → ONE account.move for the full price
                      │                         split by account.payment.term
                      └─ installment_ids ......... never populated
```

**Reservation:** `create()` calls Module 1's `_check_available_for_new_sale()`,
assigns a sequence, and sets `property.state = 'reserved'` — but only if the
property was `available`. `action_confirm_booking` requires a non-zero booking
fee. `action_create_sale_contract` builds the contract and moves to `confirmed`.
`action_extend_hold` adds 24 h to the duration field with no record of who did
it or why. A cron expires holds hourly.

**Sale contract:** `action_sign` requires a payment term and a product variant,
creates a bridge `sale.order`, appends discount and maintenance lines from the
payment term, invoices the whole order once, and posts it.
`action_handover` sets the property owner and `state = 'sold'`.
`action_cancel` refuses to cancel a handed-over contract.

**Installments:** the model has `action_generate_invoice` and
`action_mark_paid`, and a cron that is an explicit no-op. Nothing calls them,
because nothing creates installments.

---

## 3. Downstream dependency map

| Model | Consumed by | What is consumed |
|---|---|---|
| `realestate.project` | construction, api, maquette, plan, portal, procurement, investment, customer_service, checks | `project_id` m2o, `name`, `state`, unit rollups |
| `realestate.phase` | construction | `phase_id` m2o, `state` |
| `realestate.unit.reservation` | maquette (3 JS entry points) | opens the reservation **form** via `act_window` |
| `realestate.sale.contract` | **checks**, **handover**, contract_template, api, portal, construction | `sale_contract_id` m2o, `.state == 'signed'`, `action_handover()`, `partner_id`, `property_id`, `project_id`, `sale_price`, `installment_ids` |
| `realestate.sale.installment` | **checks**, api | `sale_installment_id` m2o, `.move_id`, `.date_due`, `.amount`, `.state` |

**Hard constraints this places on Module 2:**

1. `real_estate_handover.handover.sale_contract_id` is `required=True,
   ondelete='cascade'` and the handover flow calls
   `sale_contract_id.action_handover()` when `state == 'signed'`.
   **`action_handover()` and the `signed` state value must survive.**
2. `real_estate_checks.check` carries both `sale_contract_id` and
   `sale_installment_id`, and reads `sale_installment_id.move_id`.
   **The installment model and its `move_id` must survive.**
3. The bulk-PDC wizard reads `sale_contract_id.installment_ids` and sorts by
   `date_due`. **`installment_ids` and `date_due` must survive.**
4. `real_estate_api` exposes contracts and installments to customers.
5. Maquette/plan open the reservation form directly, so every commercial rule
   must live in `create()`/`write()` or a service both call — never in a
   wizard, or 2D/3D bypasses it. There is currently exactly one creation path,
   which is good news for Phase 39.

**Module 1 (frozen) coupling is safe.** `atmta_real_estate` never imports this
module. It references `project_id`/`phase_id` only through late-bound
`field in self._fields` probes (`property_availability.py:217`) and an
`information_schema` column check in the rent-roll SQL view
(`rent_roll.py:112`). `realestate.project` is stored there as a plain Integer,
deliberately not a Many2one. **No Module 1 change is required for Module 2.**

Module 1's `_check_available_for_new_sale()` is the shared availability API,
used by both this module and `real_estate_brokerage`. It only rejects `sold`,
`maintenance` and `inactive`. It knows nothing about releases, commercial
blocks or live holds — which is exactly the gap Phase 3 must fill **in Module
2**, not by editing the frozen module.

Module 1's legacy-state bridge maps developer writes correctly:
`available → commercial_status='available'`, `reserved → 'reserved'`,
`sold → 'sold'`, `inactive → 'blocked'`. Writing `property.state` still works;
new code should write `commercial_status` directly.

---

## 4. Dangerous bugs

### 4.1 P0 — The installment schedule is dead, and three modules depend on it

`migrations/0.2/pre-migrate.py` records the cause:

```python
"""The legacy realestate.installment.plan model is removed. Drop the inbound
FK columns so Odoo can drop its table without constraint errors."""
```

Version 0.2 deleted the plan engine and moved to a single invoice split by an
`account.payment.term`. The `realestate.sale.installment` model, its views, its
menu, its cron and **all three of its downstream consumers were left in place**.
A repository-wide search finds no code path that creates one.

Consequences in any upgraded database:

* `real_estate_checks` bulk PDC creation for developer sales has nothing to
  select — post-dated cheques, a core Egypt/GCC requirement, cannot be issued
  against a sale contract.
* `/api/v1/portal` returns an empty installment schedule to every buyer.
* The dashboard's `paid_mtd` KPI is permanently `0`.
* `realestate.sale.installment.cron_auto_invoice_due_installments` runs daily
  and returns immediately.

This is the finding that sets the implementation order: M3 and M5 are the
highest-value work in this module, and Phase 36 (Checks compatibility) is
**currently broken**, not merely at risk.

### 4.2 P0 — Double booking is possible

`reservation.create()` checks availability and then writes
`property.state = 'reserved'`, with no lock of any kind:

```python
self.env['realestate.property'].browse(prop_ids)._check_available_for_new_sale()
...
if rec.property_id.state == 'available':
    rec.property_id.state = 'reserved'
```

Two concurrent transactions both read `available`, both pass the check, and
both commit. The result is two live reservations on one unit. Nothing anywhere
enforces "at most one active hold per property" — no unique index, no
constraint, no advisory lock. The check also lives only in `create()`:
re-pointing an existing reservation at another unit via `write()` is unchecked.

This is inventory that gets sold. Double allocation must be structurally
impossible, per Phase 16.

### 4.3 P0 — Two competing invoicing architectures

`sale_contract._create_sale_and_invoice()` raises **one** invoice for the whole
sale price. `sale_installment.action_generate_invoice()` raises **another**
invoice per installment, against the same property product, on the same
partner. Both post to the ledger. Nothing prevents both from running — the
installment button is on the form and enabled whenever the record is `pending`.
The customer is invoiced twice for the same unit.

### 4.4 P0 — `in_payment` and `reversed` are treated as paid

```python
if rec.move_id.payment_state in ('paid', 'in_payment', 'reversed'):
    rec.state = 'paid'
```

`in_payment` means the payment is registered but not reconciled with the bank.
`reversed` means the invoice was **cancelled by a credit note**. Both mark the
commercial obligation collected. Residual is never consulted. This is the exact
failure Phase 26 describes, and it silently overstates collections.

### 4.5 P0 — Internal pricing is exposed on a public, unauthenticated endpoint

`real_estate_api/controllers/api_v1_map.py:183` is `auth='public'` and returns
`'base_price': p.base_price` for every property. The internal base selling
price — the number every discount is measured against — is readable by anyone
on the internet with no credentials. Phase 40 requires list/public price and
internal base price to be distinguishable; today they are the same field, and
it is public.

### 4.6 Dead state values silently disable features

```python
sold = rec.sale_contract_ids.filtered(lambda c: c.state in ('contract_signed', 'handed_over'))
```

`'contract_signed'` is not a value of `sale.contract.state`. It is a leftover
from a rename. Likewise `_compute_sale_status` tests for `'handed_over'` and
`'signed'` but can never produce `'for_sale'` — that branch does not exist. So:

* a **signed** contract does not mark its unit `is_sold`;
* `sale_status` never reads `for_sale`, so a genuinely sellable unit reports
  `not_listed`;
* `project.sold_unit_count` counts only handed-over units and under-reports
  sales for the entire life of a project.

### 4.7 Payment-term "months" are 30 days

```python
_INTERVAL_DAYS = {'monthly': 30, 'quarterly': 90, 'semiannual': 180, 'annual': 365}
```

Due dates are generated as `days_after` multiples of these. Over a 96-month
plan the schedule drifts 96 × 30 = 2,880 days ≈ 7.9 years instead of 8, and no
instalment ever falls on the same day of the month. Real-estate schedules are
calendar-month schedules; this is a correctness defect for exactly the long
plans this module exists to serve. `offset_unit='months'` multiplies by 30 too.

### 4.8 Regenerating a payment term rewrites history

```python
term.line_ids = [(5, 0, 0)] + [(0, 0, v) for v in line_vals]
```

`action_re_generate_lines` destroys and rebuilds the lines of a payment term
that signed contracts already reference. Posted invoices keep their stored due
dates, so the ledger is safe — but any later invoice on an existing contract
silently uses the new schedule, and the original agreed plan is unrecoverable.
This directly violates Rule 5 and is why Phase 13 (versioning) is mandatory.

### 4.9 The discount is booked to the maintenance product

`re_apply_extras_to_order` creates the discount line with
`product_id = self._re_maintenance_product().id` and a negative price, with a
code comment admitting the shortcut. The discount therefore posts to the
maintenance product's income account. Revenue by product is wrong, and so is
any tax treatment that differs between the two.

Related: the extras are applied inside `_create_sale_and_invoice`, guarded only
by `if self.invoice_id: return`. If a bridge order exists but invoicing failed,
the next attempt appends a **second** set of discount and maintenance lines.

### 4.10 Contract balances ignore every invoice but one

`_compute_balances` reads `rec.invoice_id` only. Any additional invoice —
including every installment invoice, if that path is used — is invisible to
`paid_amount`, `invoiced_amount`, `balance_due` and `progress`. Credit notes are
not considered at all.

### 4.11 Silent inconsistency when a unit is not `available`

`reservation.create()` sets the property to `reserved` **only if** it was
`available`. If it was anything else the reservation is still created, and the
unit's status no longer reflects reality. The reservation exists; the inventory
does not know.

---

## 5. Data-integrity risks

| # | Risk |
|---|---|
| 1 | No uniqueness on `project.code`, `phase.code`, or reservation/contract references beyond the sequence |
| 2 | No `@api.constrains` that a property's `phase_id` belongs to its `project_id` — enforced only by a view domain |
| 3 | No validation that `sale_price > 0`, that `hold_expiry_at > hold_started_at`, or that installment amounts sum to the sale price |
| 4 | `hold_expiry_at` is a stored compute with `readonly=False`; a manual override is silently destroyed the next time `hold_duration_hours` changes |
| 5 | `action_extend_hold` mutates the duration with no audit trail — Phase 19 exists because of this |
| 6 | No snapshot anywhere: reservation and contract read `property.base_price` live, so changing a unit's price re-prices the *comparison* on historical reservations (`proposed_vs_base`) |
| 7 | `installment_ids` is `ondelete='cascade'` from the contract, so deleting a contract deletes obligations that cheques point at |

---

## 6. Security gaps

| # | Gap |
|---|---|
| 1 | **Zero `ir.rule` records.** No multi-company isolation, no project scoping, no agent scoping. |
| 2 | **No `company_id` on any model in the module** — there is nothing for a company rule to filter on. |
| 3 | Only three groups (Read-only / Sales Agent / Manager). No sales manager, no commercial manager, no separation of pricing authority from deal-making authority. |
| 4 | **Sales Agents can create and edit projects and phases** (`access_project_agent` grants `perm_write` and `perm_create`). |
| 5 | `access_re_pt_segment_user` grants **every internal user** read access to payment-plan segments. |
| 6 | No discount authority of any kind. `proposed_price` is a free-text money field; `proposed_vs_base` computes the deviation and then does nothing with it. Any agent can sell at any price. |
| 7 | No server-side authorisation check on any workflow action — `action_sign`, `action_handover`, `action_cancel` are callable by anyone with write access. |
| 8 | Public API leaks `base_price` (§4.5). |

---

## 7. Accounting gaps

| # | Gap |
|---|---|
| 1 | Two competing invoice paths (§4.3) |
| 2 | Payment state derived from `payment_state` including `in_payment`/`reversed`, never from residual (§4.4) |
| 3 | Contract balances read a single invoice (§4.10) |
| 4 | Discount posted to the wrong product/account (§4.9) |
| 5 | Installment invoices are created with no explicit `company_id`, `currency_id` or `journal_id` — they inherit whatever the environment happens to be |
| 6 | `invoice_date` is set to the **future** due date; posting an invoice dated in the future distorts every period report between now and then |
| 7 | No credit-note or reversal path for cancellation — `action_cancel` just sets a state; posted invoices are left standing |
| 8 | No booking-fee financial flow: `booking_fee` is a number on the reservation with no invoice, no payment, no application to the down payment and no refund/forfeiture rule |

---

## 8. Concurrency risks

| # | Risk |
|---|---|
| 1 | Double booking (§4.2) — no lock, no unique index, no constraint |
| 2 | `cron_expire_holds` searches without `limit` and loops record-by-record, writing property state inside the loop. Two overlapping cron runs can both expire the same hold and both release the unit — including one that a *new* reservation has just taken |
| 3 | Expiry and a new reservation race: the cron can free a unit between a new reservation's availability check and its write |
| 4 | `active_reservation_id` is a stored compute that takes `[:1]` of whatever matches — it hides a double booking rather than preventing it |
| 5 | No `SELECT … FOR UPDATE` or `pg_advisory_xact_lock` anywhere in the module |

Module 1 already solved the equivalent problem for leases with an advisory lock
in `contract_property_line.py`; that is the pattern to follow.

---

## 9. Migration risks

| # | Risk |
|---|---|
| 1 | Live databases have contracts whose entire schedule exists only as `account.payment.term` lines on one posted invoice. Rebuilding a real installment schedule must **derive from the posted invoice's due dates**, not from the current template — the template may have been regenerated since (§4.8) |
| 2 | Historical price breakdowns do not exist. There is one `base_price` and one `sale_price`. Per Phase 49, premiums and discounts must **not** be invented; the legacy total is stored as-is and marked legacy |
| 3 | `sale.contract.state` must keep `draft/signed/handed_over/cancelled` readable — handover calls `action_handover()` on `signed`. A richer lifecycle must be a bridge over new dimensions, exactly as Module 1 did for `property.state` |
| 4 | Adding `company_id` to six models requires a deterministic backfill. Projects have no company today; the only safe default is `env.company` at migration time, with the ambiguity logged |
| 5 | Adding a unique index for "one active reservation per property" will **fail on existing data** if a double booking already happened. The migration must detect and report duplicates rather than crash |
| 6 | `realestate.payment.term.segment` records and the `re_*` fields on `account.payment.term` must survive until plans are migrated; deleting them would break existing terms |
| 7 | IDs to preserve: project, phase, property, reservation, sale contract, installment, invoice, payment, check, handover — plus chatter, attachments and sequences |

---

## 10. Recommended implementation order

Ordered by risk retired per unit of work, not by phase number.

| Milestone | Why here |
|---|---|
| **M1 — Commercial foundation** | `company_id` + record rules first: every model added later inherits the gap otherwise. Then release batches, blocks, and one authoritative `is_available_for_sale`. Fixes §4.6, §4.11, §6.1, §6.2. |
| **M2 — Pricing** | Price books, premium build-up, history, promotions, and the discount authority matrix. Fixes §4.5, §5.6, §6.6. Must precede reservations, because a reservation snapshots a price. |
| **M3 — Payment plans** | `realestate.payment.plan` replaces the `account.payment.term` misuse. Fixes §4.7, §4.8. Must precede contracts, because a contract snapshots a plan version. |
| **M4 — Reservation V2** | Atomic locking first (§4.2), then snapshots, expiry, booking fee (§7.8), audited extensions, structured cancellation. |
| **M5 — Contract + installments** | **Restores installment generation** (§4.1) and retires the second invoicing path (§4.3). Fixes §4.4, §4.10. Unblocks Checks and the customer API. |
| **M6 — Post-signature changes** | Adjustments, restructuring, early settlement, cancellation, unit swap, buyer transfer — all built on the immutable snapshots M3–M5 establish. |
| **M7 — Enterprise hardening** | Cross-module compatibility proof, roles and scoping, dashboard on DB aggregation, reports, performance, migration, and the full test suite including the concurrency test. |

**Sequencing constraint:** pricing before reservation, plans before contracts.
A snapshot cannot be taken of something that does not yet exist, and taking it
later means back-filling invented history — which Phase 49 forbids.

**No Module 1 change is required.** Nothing in this audit points at a defect in
the frozen contract; §3 shows the coupling is late-bound and the legacy-state
bridge maps developer writes correctly.
