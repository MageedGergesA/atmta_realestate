# `real_estate_developer` — Enterprise Sales & Commercial Engine

**Status:** M1–M7 complete.
**The module's central defect is fixed:** instalments are generated again (§7).
**Baseline before work started:** `atmta_real_estate` 0.4 frozen at 299 tests,
0 failures; `real_estate_developer` at **zero tests**.

Full audit: [`PHASE_0_AUDIT_REPORT.md`](PHASE_0_AUDIT_REPORT.md).

---

## 1. Audit findings

The audit is its own document; the headline is that the module's central
schedule object is dead code with three live consumers.

| # | Finding | Severity |
|---|---|---|
| 4.1 | **Nothing creates `realestate.sale.installment`.** Version 0.2 deleted the plan engine and moved to one invoice split by `account.payment.term`, leaving the installment model, its cron, its views and its three consumers (Checks, the customer API, the dashboard) orphaned. PDC issuance against a sale contract is therefore impossible today. | P0 |
| 4.2 | **Double booking is possible.** `reservation.create()` checks availability then writes the property status with no lock, no unique index and no constraint. Two concurrent agents both win. | P0 |
| 4.3 | **Two competing invoicing paths** — a whole-price invoice on the contract and a per-installment invoice — both reachable, both posting. | P0 |
| 4.4 | `in_payment` and `reversed` are treated as **paid**; residual is never consulted. | P0 |
| 4.5 | The **internal base price is exposed on a public, unauthenticated API endpoint**. | P0 |
| 4.6 | Dead state values (`'contract_signed'`) silently disabled `is_sold`; `sale_status` could never reach `for_sale`. | High |
| 4.7 | Payment-term months are **30 days**, so an 8-year plan drifts to 7.9 years and never lands on a fixed day of the month. | High |
| 4.8 | Regenerating a payment term **destroys and rebuilds** lines that signed contracts reference. | High |
| 4.9 | The discount line is booked against the **maintenance product**. | High |
| — | **Zero** `_sql_constraints`, **zero** `@api.constrains`, **zero** `ir.rule`, **zero** `company_id`, **zero** tests. | P0 |

---

## 2. Architecture — M1

### 2.1 New models

| Model | Purpose |
|---|---|
| `realestate.unit.release.batch` | The record of a commercial decision to put specific units on the market from a specific date. `draft → approved → released → closed`. |
| `realestate.unit.block` | A dated, reasoned, auditable withholding of a unit. Lifted, never deleted. |

### 2.2 Extended models

| Model | Added |
|---|---|
| `realestate.project` | `company_id` (**required**, new), `commercial_state`, commercial calendar (5 dates), `default_hold_duration_hours`, `default_booking_fee`, six inventory counters, `contracted_value`, `UNIQUE(company_id, code)`, two `@api.constrains` |
| `realestate.phase` | `company_id` (related-stored), `commercial_state`, `sales_launch_date`, inheritable `hold_duration_hours` / `booking_fee` overrides, `target_sales_value`, three counters |
| `realestate.property` | `release_batch_ids`, `block_ids`, `is_released_for_sale`, `active_block_id`, **`is_available_for_sale`**, `sale_unavailable_reason`, `is_contracted` |
| `realestate.unit.reservation` | `company_id` (related-stored), `_check_company_auto` |
| `realestate.sale.contract` | `company_id` (related-stored), `_check_company_auto` |
| `realestate.sale.installment` | `company_id` (related-stored), `date_due` indexed |

### 2.3 Commercial vs construction

`realestate.project.state` is read by `real_estate_construction`,
`real_estate_api`, `real_estate_maquette`, `real_estate_plan` and the
dashboard. It mixes physical progress (`construction`, `handover`) with a sales
notion (`marketing`), so a project cannot be both under construction and
selling — the normal off-plan case.

It is **left exactly as it is** and treated as the construction dimension.
`commercial_state` is added alongside it: the same orthogonal-dimension pattern
frozen Module 1 used when it split `property.state`. A regression test asserts
that every value of `state` is still present, because removing one would break
five modules.

```
project.state            planning → construction → marketing → handover → completed   (physical, untouched)
project.commercial_state planning → pre_launch  → selling  → sold_out  → closed       (commercial, new)
```

### 2.4 New models — M2

| Model | Purpose |
|---|---|
| `realestate.price.book` | A *document*: approved, activated, then frozen. Versioned. |
| `realestate.price.book.line` | One unit's base price — lump sum or rate per m² |
| `realestate.price.rule` | A premium with real-estate conditions (phase, building, floor band, area band, usage) |
| `realestate.property.price.component` | How a unit's current price was built, row by row |
| `realestate.price.history` | Append-only record of every price change |
| `realestate.promotion` | A campaign, distinct from a negotiated discount |
| `realestate.commercial.approval.rule` | Who may authorise how much of what |
| `realestate.commercial.approval.request` | One request, one decision, one audit trail |
| `realestate.commercial.approval.mixin` | `_require_approval()` — the gate any workflow calls |
| `realestate.price.bulk.update` (+ line) | Bulk reprice with a mandatory preview |

---

## 3. Commercial status ownership

| Status | Owned by | Written by |
|---|---|---|
| `property.commercial_status` (Module 1 vocabulary) | **Developer / Brokerage** | the reservation and contract workflows |
| `property.maintenance_status` | Module 1 (facilities) | maintenance requests |
| `property.occupancy_status` | Module 1 (leasing) | lease allocations |
| `property.construction_status` | **Developer** | computed from phase/project state |
| `property.is_available_for_sale` | **Developer** | computed — never written |
| `property.is_released_for_sale` | **Developer** | computed from release batches |
| Commercial blocks | **Developer** | `realestate.unit.block` records |

Module 1's `COMMERCIAL_STATUS` vocabulary — `unreleased / available / held /
reserved / contracted / sold / blocked` — already covers the developer sales
pipeline exactly, and Module 1 explicitly designates it developer-owned. M1
therefore adopts it rather than inventing a parallel status, and the legacy
`property.state` bridge continues to work for older code.

**A block does not change `commercial_status`.** That is deliberate: writing the
status would destroy whatever it was before, record no reason, and leave nothing
to audit once lifted. `is_available_for_sale` consults live blocks instead, so
lifting a block restores the previous state exactly.

### The availability engine

`is_available_for_sale` is the single authoritative answer to "can a buyer take
this unit today?". It is stored and indexed, because it is searched, grouped and
drilled into constantly, and it evaluates, in order:

```
archived → not a leaf unit → no project → project not selling → phase not selling
        → not released → outside release window → commercially blocked
        → under maintenance → held/reserved/contracted/sold
```

Every refusal also publishes `sale_unavailable_reason`, ordered most-structural
first, so the reason a user sees is the one they can act on. `_check_available_
for_sale()` turns each reason into a specific error message.

Because the flag is stored, **time-based transitions need a sweep**: a release
window opening or a block expiring changes nothing in the ORM. Two daily crons
recompute only the affected records, never the whole table.

---

## 4. Pricing architecture

```
        BASE PRICE            lump sum, or rate/m² × area
      + PREMIUMS              floor · view · orientation · corner · garden ·
                              terrace · parking · storage · finishing ·
                              location · custom
      + MANUAL ADJUSTMENT     recorded as its own component, never disguised
                              as a rule outcome
      ─────────────────────
      = LIST PRICE            what a buyer is quoted
      − PROMOTION             a campaign: approved once, applied many times
      − DISCOUNT              a negotiation: unique to one deal, gated by the
                              authority matrix
      ± PAYMENT-PLAN EFFECT   M3
      ─────────────────────
      = NET SELLING PRICE     snapshotted onto the deal (M4/M5)
```

Premium conditions are **named columns**, not a domain expression. A domain
would express strictly more and no sales administrator could ever configure or
audit it; every condition here can be read off a form and explained to a buyer.

Floors are read from the Module 1 hierarchy rather than from an integer on the
unit, and `_floor_of()` returns `None` rather than `0` when it cannot tell —
because 0 is the ground floor, not "unknown".

**Price books are immutable once approved.** Editing `line_ids`, `rule_ids`,
scope, currency or `valid_from` on an approved or active book raises. Repricing
is `action_new_version()`, which copies the book at version + 1 and leaves the
original exactly as it was. That is Rule 5 at the pricing layer: a deal signed
six months ago stays explicable from the book it was signed against.

Activating a book expires the previous one, writes the new prices onto the
units, records a `price.history` row for every change, and regenerates the
component build-up. All batched — a per-record write with a per-record history
insert is the difference between a slow action and an unusable one at 20,000
units.

**Price history is append-only.** `unlink` is blocked outright; `write` is
blocked for every field except the ones Odoo itself maintains (stored computes
and related mirrors), because blocking those would break the ORM's own
recompute pass rather than protect anything.

### Public vs internal price (Phase 40)

The audit found `base_price` — the internal number every discount is measured
against — served from an `auth='public'` endpoint. `_public_price()` is the
method public callers should use: it returns the list price for a unit that is
actually on the market, and **0.0** for one that is not. An unreleased unit has
no public price, and inventing one leaks the pricing of inventory that has not
launched.

---

## 5. Payment-plan architecture

```
TEMPLATE (realestate.payment.plan, active, versioned)
   │  action_new_version()      → v2 draft; v1 untouched forever
   │  action_create_custom_copy() → a negotiated fork for ONE deal
   ▼
_generate_schedule(price, booking_date, contract_date, handover_date)
   ▼
SCHEDULE ROWS  kind · % · amount · due date · cumulative · remaining
   ▼
RESERVATION SNAPSHOT (M4) → CONTRACT SNAPSHOT (M5)
   ▼
realestate.sale.installment  → account.move → Odoo payment + reconciliation
```

### Why not `account.payment.term` (Rule 4)

Odoo's payment term describes how **one invoice** splits into due dates. A
developer plan is a 5–10 year agreement producing many independent obligations
and many invoices. The audit found two structural consequences of conflating
them, neither patchable:

* months were approximated as **30 days**, so a 96-month plan ran 2,880 days —
  7.9 years — and no instalment ever landed on a fixed day of the month;
* `action_re_generate_lines()` **destroyed and rebuilt** the term's lines in
  place, silently restating the schedule of every contract referencing it.

Dates now use `relativedelta`, so "3 months after booking" means the same day of
the month three months later, for ten years. A regression test asserts that 96
monthly instalments from 31 Jan 2026 end in **January 2034** — eight years, not
7.9 — and that every instalment lands at month-end rather than drifting.

### Line kinds and calculation

One line can stand for many payments: "48 quarterly instalments of 1.5%" is a
single row with a count of 48, which is how a developer actually describes a
plan.

| Calculation | Meaning |
|---|---|
| `percent` | % of the deal price |
| `fixed_amount` | a flat sum |
| `residual` | whatever is left to reach 100% — what makes a plan work at any price |

| Date rule | Anchor |
|---|---|
| `on_booking` / `days_after_booking` / `months_after_booking` | the booking date |
| `months_after_contract` | the contract date |
| `on_handover` | the handover date — **raises** if the deal has none, rather than inventing a due date years too early |
| `fixed_date` | an absolute date |

### The schedule always adds up

`_generate_schedule()` resolves the residual line once every other amount is
known, sorts by due date, and absorbs **rounding** on the final row.

The rounding absorber is bounded: the largest error per-row rounding can
accumulate is one rounding unit per row, so anything larger is not rounding — it
is a plan that does not reach 100%. Beyond that tolerance the drift is left in
place for `_validate_schedule_total()` to reject. **This bound was added because
a test caught the unbounded version silently topping up the last payment by
900,000 to hide a misconfigured plan** — which would have billed the customer
the difference.

`_validate_schedule_total()` runs on the preview and will run on the deal path,
so a misconfigured plan fails in front of the person configuring it rather than
on a customer's contract.

### Versioning and negotiated plans (Phases 13–14)

Active plans are frozen: `line_ids`, `booking_handling`, currency, scope and
version cannot be edited. Changing "10% down / 8 years" into "15% down / 7
years" is `action_new_version()`, and v1 stays exactly as signed.

A buyer who negotiates gets `action_create_custom_copy()` — a fork flagged
`is_custom` and linked to its template. It never appears in the standard plan
list, the master template is never edited for one buyer, and
`_custom_divergence()` reports the down-payment and duration difference from the
template, which is what a commercial director asks about.

---

## 6. Reservation locking — why double booking is impossible

The audit found `create()` checking availability and then writing the property
status **with no lock of any kind** — no advisory lock, no unique index, no
constraint. Two agents clicking at the same moment both read "available", both
pass the check, and both commit. That is a double-sold unit.

Two independent mechanisms now stand in the way, and both are needed:

### 1. A Postgres partial unique index — the guarantee

```sql
CREATE UNIQUE INDEX realestate_reservation_one_live_per_property
ON realestate_unit_reservation (property_id)
WHERE state IN ('hold', 'pending_payment', 'booked')
```

Created in `init()`, so it exists in every database. This makes two live holds
on one unit **structurally impossible** — not unlikely, impossible. It holds
against concurrent transactions, against a future code path that forgets to
lock, and against a data import, none of which a Python check can promise.

### 2. An advisory transaction lock — the good error message

```python
self.env.cr.execute("SELECT pg_advisory_xact_lock(%s, %s)",
                    (RESERVATION_LOCK_NAMESPACE, property_id))
```

Taken **before** the availability check, in both `create()` and `write()` — the
audit noted the original check lived only in `create()`, so re-pointing an
existing reservation at another unit was unguarded.

The index alone would let the loser hit a raw `IntegrityError`, aborting the
transaction and showing a Postgres message. The lock serialises the two
transactions instead, so the loser re-reads, sees the winner's hold, and gets:

> Unit A-402 has just been held by another transaction (RES-00017). Please
> choose another unit.

The lock is released at commit or rollback, so a crashed request can never
leave a unit locked forever.

### What the tests actually prove (Phase 51)

Odoo forbids committing inside a test, so two fully independent committed
transactions cannot be staged. Each link is therefore proven deterministically,
which is stronger than a timing-dependent race that would show only that one
interleaving happened to behave:

| Test | Proves |
|---|---|
| `test_advisory_lock_serialises_two_transactions` | While one real cursor holds the unit lock, a second cursor's `pg_try_advisory_xact_lock` returns **false** — and succeeds once the first transaction ends, so the lock is not leaked |
| `test_the_loser_of_the_race_gets_a_business_error` | The guard inside the lock refuses the second buyer with a message naming the unit |
| `test_the_database_itself_forbids_two_live_holds` | A raw `INSERT` bypassing every Python check still raises `IntegrityError` |
| `test_expiry_cannot_race_a_fresh_reservation` | The expiry cron re-reads under the lock, so it cannot expire a hold that was booked between its search and its write |

### The cron, rewritten

V1 searched without a limit, looped record-by-record writing property state
inside the loop, and could expire a hold that had just been booked. The
replacement is bounded to 500 records, takes the unit lock per record,
re-reads state and expiry under it, and writes once — idempotent, and a second
run finds nothing to do.

---

## 7. Accounting flow

```
PAYMENT PLAN (template, versioned)
        │
        ▼  snapshot at hold
RESERVATION SCHEDULE (frozen — what the buyer agreed to)
        │
        ▼  action_sign()
realestate.sale.installment          ← the COMMERCIAL obligation
        │                              (Checks maps PDCs 1:1 onto these)
        ▼  action_generate_invoice()
account.move (out_invoice)           ← the ACCOUNTING obligation
        │
        ▼  Odoo payment + reconciliation
amount_residual                      ← the ONLY source of "is it paid?"
```

### The central defect, fixed

Version 0.2 deleted the plan engine that created instalments and moved to a
single invoice split by an `account.payment.term` — but left the model, its
views, its cron and **all three of its consumers** in place. Since that release
the table has been empty in every upgraded database:

* `real_estate_checks` bulk-PDC creation had nothing to select, so post-dated
  cheques — a core Egypt/GCC requirement — could not be issued against a sale
  contract;
* `real_estate_api` served every buyer an empty schedule;
* the dashboard's revenue KPI was permanently zero.

`action_sign()` now raises the schedule, from the reservation's frozen rows
where one exists (that is what the buyer agreed to; the plan may have been
versioned since) or from the plan otherwise. Generation is idempotent — a
second call cannot double the schedule.

### One invoicing path, not two

V1 raised **one invoice for the whole price** on signing while the installment
model offered a **second, per-installment invoice** for the same money. Both
posted. Nothing prevented both from running, and the customer was billed twice.

`_create_sale_and_invoice()` is now a documented no-op — kept rather than
deleted because V1's `action_sign` and any customisation still reference it —
and each instalment is invoiced individually as it falls due.

Two smaller fixes went with it: the invoice is dated **today** with the schedule
date as its *due* date (V1 dated it on the due date, so posting an instalment
due in 2034 distorted every period report between now and then), and it is
created with an explicit company and currency instead of inheriting whatever the
environment happened to be.

### Phase 26 — what "paid" means

The audit found `payment_state in ('paid', 'in_payment', 'reversed')` treated as
paid. `in_payment` means registered but not reconciled; `reversed` means the
invoice was **cancelled by a credit note**. Every figure is now derived from the
invoice itself:

| Field | Source | Why |
|---|---|---|
| `invoiced_amount` | `amount_untaxed` | comparable with the commercial obligation — a 1,000,000 plan does not become 1,150,000 because VAT applies |
| `paid_amount` | `amount_total − amount_residual` | gross, because that is what the customer actually paid |
| `residual_amount` | `amount_residual` | the only figure reflecting reconciliation, partial settlement and credit notes together |

**A test caught the same class of bug in my own implementation.** Reversing an
invoice with `cancel=True` reconciles the credit note against it, so
`amount_residual` drops to zero — and reading the residual alone called that
*paid*. Arriving at V1's error by a different route. A reversed invoice now
returns the obligation to **pending**: it still stands and still has to be
billed. `move_id` is deliberately kept so the cancelled invoice stays visible on
the obligation it failed to settle.

State precedence is `cancelled → pending → paid → partially_paid → overdue →
invoiced`. Overdue-ness is also carried separately as `days_overdue` and
`aging_bucket`, so a partly-paid arrear is not lost to a single-valued state.

### Collections hand-off (Phase 35)

Every field a future Collections module needs is present and populated —
`current_amount`, `invoiced_amount`, `paid_amount`, `residual_amount`,
`date_due`, `days_overdue`, `aging_bucket` — plus `collector_id` and
`promise_to_pay_date` as unused extension points. No case management is built
here.

---

## 8. Contract-change workflows

One rule runs through all five: **paid history is never rewritten.** A paid
instalment is not edited, a posted invoice is not deleted, and a unit is not
blindly returned to the market. What changes is the *open balance*, and what
changed stays legible afterwards.

`_open_installments()` defines what "open" means, and every workflow uses it:
not cancelled, no money against it, and **not already invoiced** — an invoiced
obligation is an accounting document and is Odoo's to reverse, not ours to
rewrite.

### The spine

`realestate.sale.contract.amendment` is one record per change to a signed deal:
typed, approved, applied exactly once (the state guard is the idempotence
mechanism), and never deletable once applied. Each type maps to an approval
action, so **one authority matrix governs discounts, swaps, settlements,
transfers and cancellations alike** — Phase 34's question answered in the
affirmative without a BPM engine.

`realestate.sale.installment.adjustment` records what happened to one
obligation, capturing the "before" **from the record itself** rather than
trusting the caller. Applying it writes to `adjustment_amount`, never to
`original_amount`, so the schedule as signed stays readable next to the
schedule as it now stands. It refuses to touch an already-invoiced obligation
and tells the user to credit the invoice instead.

### The five workflows

| Phase | Workflow | What it preserves |
|---|---|---|
| 28 | **Restructuring** | Paid instalments untouched; open ones are *cancelled with a reason* and replaced, never deleted. The new schedule covers exactly the open balance. Preview is mandatory. |
| 29 | **Early settlement** | Outstanding, discount and fee shown before anything moves; the remaining schedule collapses into one settlement obligation. Policy varies by developer, so the discount is entered rather than calculated. |
| 30 | **Cancellation / termination** | Posted invoices left standing for accounting to reverse; paid instalments kept; future obligations cancelled individually with a reason; the unit returns to the market **only if nothing else commits it**, via the availability engine. |
| 31 | **Unit swap** | Payments carry forward, the open balance is re-cut to the new price, and the amendment holds **both** unit references — the original is never overwritten silently. |
| 32 | **Buyer transfer** | The outgoing buyer stays on the contract as an assignee, so who held the deal before is answerable years later. A transfer fee raises its own obligation. |

### Phase 36 — not corrupting financial instruments

`_assert_no_blocking_checks()` refuses to restructure or settle underneath a
cheque that has been **deposited or cleared**, naming the cheques. The reference
to `realestate.check` is **late-bound** (`if 'realestate.check' in self.env`),
because `real_estate_checks` is not a dependency of this module and Developer
must keep working without it.

---

## 9. Approval matrix

Phase 34 asks whether one architecture can serve discounts, custom plans,
extensions, cancellations, refunds, settlements, swaps and transfers without
becoming a BPM platform. It can, because they share one shape: *somebody wants
to do X, and the size of X decides who must say yes.*

So there are exactly two models plus a mixin, and the action type is a plain
selection. Adding an approvable action means adding one value to
`APPROVAL_ACTION` and calling `_require_approval()`. There is no routing DSL, no
delegation matrix, no escalation timer — Phase 34 rules those out.

| Concept | Behaviour |
|---|---|
| Band | `percent`, `amount` or `any`; `max_value = 0` means no ceiling, for the top tier |
| Resolution | Project-specific rules beat company-wide ones; `sequence` decides within each |
| Ungoverned value | **Permitted**, and treated as "the company has not configured this" — blocking every unconfigured action would make the module unusable out of the box |
| Holding authority | If the acting user *is* the named approver or in the approver group, they act directly; no paperwork |
| Self-approval | **Refused by default.** A salesperson approving their own discount is not an approval. Configurable per rule. |
| Partial grant | An approver may authorise less than was asked; a 3% grant does not cover a 5% ask |
| Enforcement | Server-side in `_require_approval()`. Buttons are not security. |

Example chain, entirely configuration — no threshold is a constant in code:

```
Sales Agent          ≤ 2%
Sales Manager   2.01 – 5%
Director            > 5%   (max_value = 0)
```

---

## 10. Security

| Before | After |
|---|---|
| No `company_id` on any model | `company_id` on all seven |
| **Zero** `ir.rule` records | 7 global company rules + 4 agent/manager scoping rules |
| Sales Agents could create and edit **projects and phases** | read-only on both |
| Payment-plan segments readable by **every internal user** | Developer read-only group |

The company rules deliberately do **not** admit `company_id = False` — a
company-less record would otherwise be visible to everyone, which is the leak
the rules exist to close. Every model has a required or related-stored company,
so no record is stranded.

Agent scoping is the first step of Phase 46: a non-global rule on the agent
group restricts agents to their own deals, and a permissive manager rule
re-opens the full company view (non-global rules of different groups are OR-ed).

### Roles (Phase 45)

The module had three groups. It now has five, and the split that matters is
between selling and setting the terms of sale:

```
Read-only
  └─ Sales Agent          own deals; read-only on projects, phases, plans, price books
       └─ Sales Manager   the team's deals, approves within authority
            └─ Commercial Manager   price books · payment plans · promotions · the
                                    discount authority matrix
                 └─ Developer Manager   everything above
```

**Commercial Manager is deliberately separate from Sales Manager.** The person
who sets the discount ceiling should not be the person selling against it.

No developer group grants any accounting right. Raising an invoice through the
instalment workflow uses `realestate.account.tools`; access to journals,
reconciliation and accounting reports remains whatever Odoo Accounting gives the
user.

### Still open (Phase 46)

Project-level and sales-team scoping need a project↔user assignment model that
does not exist yet, and `crm` is not a dependency. Recorded as a gap rather
than half-built.

---

## 11. Migration

### 0.2 → 0.3, in order

`migrations/0.3/pre-migrate.py` runs **before** the schema changes because two
of them cannot survive existing data on their own:

1. `project.company_id` is `required`. Odoo would add the column and apply NOT
   NULL against existing rows and fail the upgrade. The column is created and
   back-filled with the main company, and the count is logged for review.
2. `UNIQUE(company_id, code)` cannot be created if duplicate codes exist.
   Duplicates are detected first and reported **by code and record id**, then
   the upgrade is stopped with an explanation — instead of a Postgres error that
   says nothing about what to fix.

Deal companies are seeded from the unit's product template rather than left for
the ORM to compute row by row; a unit whose template has no company falls back
to the main company rather than becoming invisible to the new record rules.

**Duplicate live reservations** are detected before the Phase 16 unique index
is created, and reported by unit and reservation reference. Without that the
upgrade would abort on a Postgres error naming neither — and a production
database written by the old, unlocked code may genuinely contain the situation
the index forbids.

### What existing data keeps

Project, phase, property, reservation, sale-contract and instalment IDs are all
preserved; every model was **extended, never replaced**. Linked invoices,
payments, cheques, handovers, attachments, chatter and sequences are untouched.

Historical contracts signed before 0.3 have no reservation and no payment plan,
so they have no schedule to reconstruct. `_take_snapshot()` falls back to the
unit's own price and records what it can; **no premium breakdown, discount or
promotion is invented** where the data never existed. Those contracts keep
their legacy totals and simply carry no instalments until one is raised
deliberately.

Nothing is deleted and nothing is invented.

---

## 12. Cross-module compatibility

### `real_estate_checks` — proven working end to end

The audit's headline finding was that PDC issuance against a developer sale
contract has been impossible since 0.2, because nothing created the instalments
the bulk-cheque wizard reads. Driven against a live database, through the
wizard's own `action_generate()`:

```
INSTALLMENTS:             2     (were 0 in every upgraded database)
WIZARD SEES INSTALLMENTS: 2
CHECKS CREATED:           2
MAPPED 1:1:            True     each cheque on its own instalment
AMOUNTS MATCH:         True
```

`test_contract.TestChecksCompatibility` additionally pins the field names the
wizard and the check model read — `installment_ids`, `date_due`, `amount`,
`move_id`, `state`, `kind` — so restoring generation cannot later be undone by
a rename.

### `real_estate_handover`

`action_handover()` and the `signed` state both survive, and are asserted by
`test_handover_still_works_for_the_handover_module`. The accepted source states
were **widened** (a contract may now also be handed over from `active` or
`financially_cleared`), never narrowed.

### The rest

The full 14-module upgrade runs together with zero ERROR/CRITICAL lines, and
Module 1's frozen suite is unaffected. `atmta_real_estate`, `real_estate_checks`
and `real_estate_handover` have now been upgraded and tested together —
**479 tests, 0 failed**. Contract Template, Portal, API, Plan and Maquette get
their behavioural proof in M7.

---

## 13. Tests

`real_estate_developer` had **no tests at all**. M1–M7 add 247.

| Gate | Result |
|---|---|
| Module 1 frozen baseline, before any Module 2 work | 299 tests, 0 failed |
| Developer suite alone (M1–M7) | **247 tests, 0 failed** |
| Module 1 + Developer + Checks + Handover | **531 tests, 0 failed, 0 errors** |
| 14-module upgrade together | exit 0, **0 ERROR/CRITICAL**, all 14 installed |

| Module | Covers |
|---|---|
| `test_commercial_master` | Commercial vs construction independence, the untouched `state` vocabulary, project code uniqueness, booking-window and hold-duration constraints, grouped counters, phase→project inheritance including "zero is a real answer", release batch scope enforcement and freezing, **the dead-state regressions** |
| `test_availability` | Unreleased ≠ available, approval before release, release windows (future / current / past), closing a release, project and phase gating, phase cannot out-rank its project, committed statuses, maintenance, leaf-units-only, reason ordering, block/lift/audit/expiry |
| `test_multi_company` | Every model carries a company, cross-company reads, rules reject NULL companies, `check_company` on releases, deal company derives from the unit, agent scoping, agents can no longer edit projects |
| `test_pricing` | Lump-sum and per-m² bases, percent/fixed/per-m² premiums, condition bands, manual adjustment as its own component, **approved books cannot be edited**, **a new version does not restate the old one**, activation expires the predecessor, prices and history written on activation, history is append-only, unpriced units report their own price, **public price withheld for unreleased units** |
| `test_dashboard_and_compat` | Dashboard scoped to the active companies, aggregation across companies, collections reading real payments, unchanged payload shape, availability from the engine rather than legacy state, the fields API/Portal/Contract-Template/Checks read, 3D reservations going through the same guarded path, index coverage, and a query-count proxy proving the dashboard does not loop |
| `test_contract_changes` | Amendment workflow and **idempotence**, adjustments capturing their own "before", `original_amount` never overwritten, invoiced obligations protected, restructuring covering exactly the open balance, mandatory preview, settlement, cancellation keeping posted invoices and paid instalments, unit swap carrying payments and recording both sides, buyer transfer keeping the outgoing party, and the late-bound cheque guard |
| `test_contract` | Lifecycle extension without redefinition, `action_handover()` still working for the handover module, signing raises the schedule, idempotence, snapshot from the reservation, booking credit carried so money is not charged twice, parties, multi-asset lines, **Phase 26's partial-payment example**, credit notes, aging buckets, invoice dating, and the fields `real_estate_checks` reads |
| `test_reservation` | Snapshot frozen against re-pricing, net price arithmetic, schedule materialisation, **locking and concurrency (4 tests)**, expiry idempotence and boundaries, booking-fee gating, audited extensions, structured cancellation and the wizard's refusal to leave money unallocated |
| `test_payment_plan` | 100% completeness, single residual, fixed amounts needing a residual, over-allocation, **schedules summing exactly for awkward prices**, **calendar months not 30 days**, quarterly spacing, handover with no date refused, ordering, cumulative figures, active-plan immutability, versioning, negotiated forks and divergence, preview balancing |
| `test_approval` | Band resolution, project rules beating company rules, unbounded top tier, ungoverned actions permitted, a rule must name an approver, within-authority acts directly, **beyond authority refused server-side**, an approved request unblocks, **self-approval refused by default**, partial grants do not cover bigger asks, rejections unblock nothing, promotion scope/dates/caps |

**Bugs the M1 suite caught during development**

| # | Bug | Consequence |
|---|---|---|
| 1 | The release-window test tried to re-date a live batch | Product behaved correctly — the *test* was wrong, and it proved the scope freeze works |
| 2 | `@api.constrains` on the two approver fields never fired when **both** were omitted — exactly the mistake it guards | Odoo only validates constrained fields present in `vals`. A rule naming nobody could be saved, and nothing under it could ever be approved. Fixed by adding the always-present `name` to the trigger list. |
| 6 | The amendment constraint fired **after** a cancellation did its job | `_check_contract_is_amendable` read `contract.state`, so recording that a correctly-executed cancellation had been applied was rejected — judging an amendment by the state it produces is circular. Cancellation-type and already-applied amendments are now exempt. |
| 5 | A reversed invoice read as **paid** in my own implementation | Reversing with `cancel=True` reconciles the credit note, dropping `amount_residual` to zero — the same error the audit found in V1, reached from the opposite direction. Money never arrived, yet the obligation showed settled. |
| 4 | `_require_approval` was called on the reservation, but the approval mixin was never mixed into it | Extending a hold, or booking a deal carrying a discount, would have raised `AttributeError` in production — the discount gate did not exist on the model that needed it most. |
| 3 | The schedule generator's rounding absorber was **unbounded** | A plan allocating only 10% silently had its single payment topped up from 100,000 to 1,000,000 — the generator would have billed the customer the whole shortfall as if it were a rounding cent. Now bounded to one rounding unit per row; anything larger is rejected as a misconfigured plan. |

---

## 14. Remaining gaps

### ✅ Implemented (M1)
Phases 1–4 and the Phase 44 baseline: project and phase commercial masters,
inheritance, release engine, commercial blocks, authoritative sale availability,
`company_id` everywhere, record rules, ACL tightening, migration, views, menus,
crons, 48 tests.

### ✅ Implemented (M7)
Phases 41, 45, 48 and the nine required reports: the dashboard recomputed in
the database and scoped to the active companies, the two missing commercial
roles, index coverage for every hot column, and the report suite as filterable
list/pivot actions.

### ✅ Implemented (M6)
Phases 27–33: the amendment spine, instalment adjustments, restructuring, early
settlement, cancellation and termination, unit swap, buyer transfer — each with
a preview, each routed through the single approval architecture, and each
refusing to rewrite paid history.

### ✅ Implemented (M5)
Phases 21–26: the lifecycle extended (never redefined) with signature, billing
and collection as their own dimensions; the commercial snapshot; parties and
co-buyers; multi-asset contract lines; **installment generation restored**; one
invoicing path; and payment state derived from reconciliation.

### ✅ Implemented (M4)
Phases 15–20: reservation snapshots (price, promotion, discount, plan version,
materialised schedule), the locking described in §6, configurable hold expiry
resolved through phase→project, the booking amount as a real gated flow,
audited hold extensions, and structured cancellation with refund/forfeiture.

### ✅ Implemented (M3)
Phases 11–14: `realestate.payment.plan` replacing the `account.payment.term`
misuse, calendar-month date rules, residual lines, bounded rounding absorption,
schedule preview through the real generator, versioning, and negotiated
per-deal forks.

### ✅ Implemented (M2)
Phases 5–10 and the Phase 34 approval architecture: price books with
versioning and immutability, the premium engine, price components, append-only
price history, bulk repricing with mandatory preview, promotions, and the
discount authority matrix with server-side enforcement and no self-approval.

### 📋 Next
| Milestone | Scope |
|---|---|
| M3 | `realestate.payment.plan` replacing the `account.payment.term` misuse; preview, versioning, custom-plan approval |
| M4 | Reservation V2: atomic locking, snapshots, configurable expiry, real booking-fee flow, audited extensions, structured cancellation |
| M5 | Contract V2 and the installment engine — **restores the schedule Checks and the API need** |
| M6 | Adjustments, restructuring, early settlement, cancellation, unit swap, buyer transfer, unified approval engine |
| M7 | Cross-module compatibility proof, roles and scoping, dashboard on DB aggregation, reports, performance, full regression |

### 🚫 Intentionally out of scope (Phase 52)
Brokerage listing and commission engines · CRM replacement · 2D/3D viewer ·
construction BOQ · procurement · snagging/handover workflow · PDC lifecycle ·
full collections case management · facilities management · Wafi/REGA · Oqood ·
DOCX rendering · any custom accounting ledger.

---
