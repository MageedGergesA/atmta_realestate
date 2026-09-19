# `real_estate_brokerage` — Phase 0 Audit

**Date:** 2026-08-06
**Module version audited:** `0.1`
**Scope:** every file in `real_estate_brokerage`, every consumer of its models
across the 14-module ATMTA suite, the two frozen modules it integrates with
(`atmta_real_estate`, `real_estate_developer`), and the Odoo 18 CRM primitives
it does not currently use.

Everything below is read out of the code as it exists today. Where the manifest
and the code disagree, the code wins and the disagreement is recorded.

---

## 0. Size of the thing being upgraded

| | |
|---|---|
| Python files | 13 (1,340 lines) |
| Models | 9 own + 2 inherits (`realestate.property`, `res.users`) |
| Wizards | **0** |
| Views | 11 XML files (1,224 lines incl. the OWL dashboard template) |
| Cron jobs | 2 |
| Sequences | 5 |
| Reports (QWeb/PDF) | **0** |
| Tests | **0** |
| **Record rules (`ir.rule`)** | **0** |
| **`company_id` on any Brokerage model** | **0** |
| **`_check_company_auto`** | **0** |
| Server-side permission checks | **0** |
| `crm` in `depends` | **no** |

For comparison: `real_estate_developer` ships 19 record rules and
`real_estate_checks` ships 7. Brokerage ships none, and — unlike Checks 0.1,
which at least *had* a `company_id` column — **not one Brokerage model carries a
company at all.**

---

## 1. The architectural question, answered

> **Why does ATMTA have `realestate.lead` when Odoo `crm.lead` already exists
> and the suite already uses it?**

### 1.1 The decisive fact

A suite-wide search for `realestate.lead` returns **14 hits, every one of them
inside `real_estate_brokerage`**:

```
real_estate_brokerage/models/lead.py          (definition, sequence)
real_estate_brokerage/models/offer.py         lead_id
real_estate_brokerage/models/viewing.py       lead_id
real_estate_brokerage/models/brokerage_dashboard.py
real_estate_brokerage/data/sequences.xml
real_estate_brokerage/views/lead_views.xml    (5 views + 1 action)
```

**No other module in the suite reads, writes, or references
`realestate.lead` — not by name, not by id, not through a relation.**

A search for `crm.lead` returns hits in **`real_estate_portal`** and
**`real_estate_api`** — the two modules through which a real customer actually
reaches this system:

* `real_estate_portal/controllers/public.py:311` — every website EOI / visit
  request creates a `crm.lead`.
* `real_estate_api/controllers/api_v1_interest.py:165` — `POST /api/v1/interest`
  creates a `crm.lead`.
* `real_estate_api/controllers/api_v1_portal.py:111` — the customer portal
  *reads back* the interests a customer submitted, from `crm.lead`.
* Both modules already extend `crm.lead` with real-estate fields
  (`re_project_id`, `re_property_id`, `re_visit_requested`, `re_visit_date`,
  `re_source`, `realestate_api_source`, `realestate_api_project_id`,
  `realestate_api_property_id`).

### 1.2 So which object is authoritative today?

**`crm.lead` is.** It is the only one a customer can create, the only one the
customer portal can read, and the only one two other modules extend.

`realestate.lead` is a **brokerage-internal island**: a manually-created record
that nothing outside the module has ever heard of. It exists because Brokerage
0.1 was written as a self-contained vertical without checking whether the suite
already had a lead spine — the same pattern that produced the duplicate Chart.js
in Module 1 and the parallel accounting in Checks 0.1.

### 1.3 The eight questions

> **1. Why does `realestate.lead` exist?**

Because Brokerage 0.1 built its own pipeline rather than extending CRM. It is
not required by any business rule found in the code.

> **2. What business data exists there that `crm.lead` does not currently
> contain?**

| `realestate.lead` field | Equivalent on `crm.lead` today |
|---|---|
| `name` (LEAD-00001 sequence) | `name` (free text) — the *sequence* is the only genuinely absent thing, and it is cosmetic |
| `partner_id`, `partner_name`, `phone`, `email` | native, plus `email_from`, `mobile`, `phone_sanitized` |
| `source` (6 hard-coded values) | `source_id` (`utm.source`), richer and extensible |
| `state` (7 values) | `stage_id` (`crm.stage`) + `lost_reason_id` + `active` |
| `agent_id` | `user_id` (+ `team_id`, which Brokerage has no concept of) |
| `budget_min` / `budget_max` | **absent** — genuine gap |
| `area_min` / `area_max` | **absent** — genuine gap |
| `bedroom_count_min` | **absent** — genuine gap |
| `property_type_ids` | **absent** — genuine gap |
| `preferred_country_id` / `state_id` / `city` / `district` | **absent** — genuine gap |
| `matched_listing_ids` | **absent** — genuine gap |
| `viewing_ids`, `offer_ids` | **absent** — genuine gap (relations point at `realestate.lead`) |
| `loss_reason_id` (`realestate.lost.reason`) | `lost_reason_id` (`crm.lost.reason`) — duplicate concept |
| `priority`, `color`, `notes` | native (`priority`, `color`, `description`) |

**Conclusion:** the only data `crm.lead` genuinely lacks is the *real-estate
requirements layer* — budget, area, bedrooms, types, locations — plus the
relations to viewings/offers/matches. That is a set of fields to **add to
`crm.lead`**, not a reason to keep a second pipeline.

> **3. Which modules depend on its database ID?**

None outside Brokerage. Within Brokerage: `realestate.viewing.lead_id` and
`realestate.offer.lead_id` (both nullable, neither `required`), and the
dashboard.

> **4. Does API create `crm.lead`, `realestate.lead`, or both?**

**`crm.lead` only.** `api_v1_interest.py:165`. It has never created a
`realestate.lead`. The same is true of the website controller.

> **5. Can one customer currently exist in two pipelines?**

**Yes, and it is the normal case.** A customer who submits a website EOI gets a
`crm.lead`. If an agent then works them in Brokerage, they get a
`realestate.lead` as well. Nothing links the two, nothing detects the
duplication, and the two records have independent states. The API's idempotency
key and Odoo's `duplicate_lead_ids` both operate only within `crm.lead`, so
neither sees the Brokerage record at all.

> **6. Can a transaction refer to a different lead than CRM?**

**Yes.** `realestate.transaction` has no lead reference of any kind — not to
`crm.lead` and not even to `realestate.lead`. The chain is
`transaction → offer → lead(realestate)`, and `offer.lead_id` is optional. A
transaction can therefore be closed with no attributable lead at all, or with a
`realestate.lead` that has no relationship to the `crm.lead` the customer
actually arrived through.

> **7. Can commission attribution disagree with CRM attribution?**

**Yes, and there is nothing to stop it.** Commission recipients come from
`transaction.lister_agent_id` / `selling_agent_id` (and
`res.users.commission_share_default`). The CRM opportunity's `user_id` and
`team_id` play no part. A lead worked and won by agent A in CRM can pay
commission to agent B because B happened to be on the listing. There is no
external-broker concept at all, so a broker-originated deal cannot be attributed
to the broker even in principle.

> **8. Is there existing production data that prevents deterministic
> migration?**

Nothing in the *schema* prevents it. The risks are in the data, and they are
real:

* `realestate.lead.partner_id` is **not required**, so a legacy lead may carry
  only a free-text `partner_name` with no contact. Those cannot be matched to a
  `crm.lead` by partner and must be classified rather than merged.
* A customer legitimately has **multiple concurrent opportunities** (a villa in
  one project and an apartment in another). Matching on partner alone would
  merge two genuine deals.
* `realestate.lead` has **no company**, so a migration cannot even determine
  which company a legacy lead belongs to without inferring it from the agent or
  the matched listings.

The migration must therefore **classify and report**, never merge on name,
email, or phone alone. See §9.

---

## 2. Architecture map

```
                    ┌───────────────────────────────────────────┐
                    │  CUSTOMER ENTRY POINTS (already CRM)      │
                    │  website EOI ──┐                          │
                    │  /api/v1/interest ──┐                     │
                    └──────────────────────┼────────────────────┘
                                           ▼
                                      ┌──────────┐
                                      │ crm.lead │  ← authoritative today
                                      └──────────┘
                                       (portal reads it back)

    ══════════ no link whatsoever ══════════

    ┌────────────────────┐        ┌────────────────────┐
    │ realestate.lead    │───────▶│ realestate.listing │
    │ (manual only)      │ m2m    │  1 ── property_id ─┼──▶ realestate.property
    └─────────┬──────────┘        └─────────┬──────────┘        (Module 1, frozen)
              │ o2m                          │ o2m
              ▼                              ▼
    ┌────────────────────┐        ┌────────────────────┐
    │ realestate.viewing │◀───────│ realestate.offer   │
    └────────────────────┘ create └─────────┬──────────┘
                                            │ action_create_transaction
                                            ▼
                                  ┌────────────────────┐
                                  │realestate.transact.│──▶ sale.order ──▶ account.move
                                  └─────────┬──────────┘    (in-house only)
                                            │ o2m
                                            ▼
                                  ┌────────────────────┐
                                  │realestate.commission│──▶ account.move (vendor bill)
                                  └────────────────────┘
```

Two entirely separate customer spines, joined nowhere.

---

## 3. CRM duplication map

| Concept | Brokerage 0.1 | Odoo 18 CRM | Verdict |
|---|---|---|---|
| Lead / opportunity | `realestate.lead` | `crm.lead` (+ `type` lead/opportunity) | **duplicate** |
| Pipeline state | `state` selection, 7 values | `crm.stage` records, orderable, per-team | **duplicate** |
| Lost | `state = 'lost'` + `loss_reason_id` | `active = False` + `lost_reason_id` + `date_closed` | **duplicate** |
| Lost reasons | `realestate.lost.reason` | `crm.lost.reason` | **duplicate** |
| Source | `source` selection, 6 values | `source_id` → `utm.source`, plus `medium_id`, `campaign_id` | **duplicate, and weaker** |
| Marketing channel | `realestate.marketing.channel` | `utm.source` / `utm.medium` | **overlapping** |
| Agent | `agent_id` + `res.users.is_realestate_agent` | `user_id` + `crm.team` + `crm.team.member` | **duplicate, and weaker** |
| Sales team | *(absent)* | `crm.team`, multi-team via `crm.team.member` | **missing** |
| Duplicate detection | *(absent)* | `duplicate_lead_ids` — email domain, `phone_sanitized`, commercial entity; **surfaces, never auto-merges** | **missing** |
| Merge | *(absent)* | `_merge_opportunity` | **missing** |
| Lead→opportunity conversion | *(absent)* | `convert_opportunity`, `crm.lead2opportunity.partner` | **missing** |
| Stage durations for funnels | *(absent)* | `duration_tracking` via `mail.tracking.duration.mixin`, plus `date_open`, `date_conversion`, `date_closed`, `date_last_stage_update` | **missing** |
| Activities / chatter | `mail.thread`, `mail.activity.mixin` | same | equivalent |

**Odoo 18 CRM already provides, out of the box, every capability M1, M5, M17,
M18, M19 and M26 ask for.** `crm.lead` inherits
`mail.tracking.duration.mixin`, so funnel elapsed-time analytics (M26) needs no
custom history table — the requirement "prefer actual CRM stage history" is
satisfiable natively.

`website_crm_partner_assign` (Odoo's **Resellers** module) is present and
**LGPL-3**, so reusing it would not create a licensing problem the way
`account_batch_payment` did in Module 3. Whether it *fits* is a separate
question addressed in §10.

---

## 4. State-machine inventory

| Model | States | Terminal | Guarded transitions? |
|---|---|---|---|
| `realestate.lead` | new, qualified, matched, viewing_scheduled, offer, converted, lost | converted, lost | partly — `action_qualify` checks `new`; `action_mark_lost` checks **nothing** and can fire from `converted` |
| `realestate.listing` | draft, active, under_offer, sold, withdrawn, expired | sold | partly |
| `realestate.viewing` | scheduled, completed, cancelled, no_show | — | **no guards at all** — every action writes the state unconditionally |
| `realestate.offer` | submitted, countered, accepted, rejected, withdrawn, expired | accepted, rejected | partly. **`countered` is unreachable** — no action ever sets it, and there is no counter-offer method |
| `realestate.transaction` | draft, deposit_received, contract_signed, closed, cancelled | closed | yes |
| `realestate.commission` | *(no state)* — only `paid`, derived from the bill | — | n/a |

### Dead / unreachable

* **`offer.state = 'countered'`** — declared, matched in three guards, never
  written. The entire counter-offer half of the negotiation is absent.
* **`lead.state = 'converted'`** — nothing ever sets it. `offer.action_accept`
  sets `lead.state = 'offer'`; closing a transaction does not touch the lead at
  all. **The dashboard's conversion-rate KPI therefore always reads 0.0%.**
* `realestate.viewing.next_action` — captured, never used by anything.
* `realestate.commission.payment_move_id` — declared, never written.
* `realestate.transaction.document_ids` — declared, no view exposes it.

---

## 5. Cross-module dependencies

### 5.1 Module 1 (`atmta_real_estate`, frozen)

* `realestate.property` — extended with `listing_ids`, `active_listing_id`,
  `for_sale`, `transaction_ids`, `last_transaction_id`, `is_sold`, and an
  override of `_compute_sale_status`.
* `_check_available_for_new_sale()` — called by `listing.action_activate`.
* `sale.order._create_re_bridge_order()` — called by `transaction.action_close`.
* `realestate.account.tools.post_moves / register_payment` — called by
  commission billing.
* `atmta_real_estate/tests/test_compatibility.py:53` asserts
  `_check_available_for_new_sale` still works — a frozen contract Brokerage must
  not break.

### 5.2 Module 2 (`real_estate_developer`, frozen)

Declared in `depends`. **Not used anywhere in the code.** Not one Brokerage
model references a project, a phase, a release batch, a reservation, a payment
plan, or the availability engine.

This is the most serious integration finding, and it has two consequences:

**(a) Brokerage can market inventory Developer says is not sellable.**
`listing.action_activate()` calls Module 1's `_check_available_for_new_sale()`,
which only knows `sold` / `maintenance` / `inactive`. Developer's own
`_check_available_for_sale()` — which additionally enforces *archived, not a
unit, no project, project not selling, phase not selling, not released, outside
release window, commercially blocked, already held/reserved/sold* — is never
called. Developer's source even records the gap:

> *"This is the developer-sales counterpart to Module 1's
> `_check_available_for_new_sale()`, which only knows about the legacy states.
> Module 1 is frozen and is also used by Brokerage, so the richer check lives
> here."*

So an unreleased, blocked, or already-reserved Developer unit can be listed,
viewed, offered on and transacted through Brokerage. **This is solvable entirely
inside Brokerage** by calling Developer's check for internal inventory — no
frozen-module change required.

**(b) Brokerage mutates property state directly, bypassing Developer.**
`transaction.action_close()` writes:

```python
rec.property_id.write({'owner_id': rec.buyer_id.id, 'state': 'sold'})
```

with no reservation, no release check, no availability recompute, no advisory
lock, and no Developer contract. For a Developer unit this silently contradicts
`commercial_status`, the release batch, and any live reservation. M24 forbids
exactly this.

### 5.3 Module 3 (`real_estate_checks`, frozen)

No interaction in either direction. Brokerage transactions have no cheque
linkage; Checks' contract-change guards know nothing about Brokerage.

### 5.4 Portal / API

Both already own the `crm.lead` spine (§1). Neither knows Brokerage exists.

---

## 6. Security gaps

### 6.1 No record rules at all

Three groups (Read-only → Agent → Manager), **zero `ir.rule`**. Consequences:

* Every Agent sees **every** lead, listing, viewing, offer, transaction and
  **commission** in the database.
* An Agent can read every other agent's commission amount and every
  confidential internal note.
* There is no team scope, no project scope, no ownership scope.

### 6.2 No company scope, because there are no companies

Not one Brokerage model has `company_id`. In a multi-company deployment every
Brokerage record is global. A listing for Company A's property can be
transacted by Company B's agent and pay commission booked in Company C's
journal, and nothing anywhere objects.

### 6.3 No server-side enforcement

Every action method (`action_activate`, `action_accept`, `action_close`,
`action_create_vendor_bill`, `action_mark_paid`) checks business state and
**never checks the caller's group**. All gating is `groups=` on view buttons —
the same "buttons disappearing is not security" failure Module 3 corrected.

### 6.4 Commission confidentiality

`realestate.commission` is readable by `group_realestate_sales_agent`, so a
junior agent can read the brokerage's entire commission book, including the
company's own share.

---

## 7. Multi-company gaps

| Model | `company_id` | `check_company` | `_check_company_auto` | Record rule |
|---|---|---|---|---|
| `realestate.lead` | ✗ | ✗ | ✗ | ✗ |
| `realestate.listing` | ✗ | ✗ | ✗ | ✗ |
| `realestate.viewing` | ✗ | ✗ | ✗ | ✗ |
| `realestate.offer` | ✗ | ✗ | ✗ | ✗ |
| `realestate.transaction` | ✗ | ✗ | ✗ | ✗ |
| `realestate.commission` | ✗ | ✗ | ✗ | ✗ |
| `realestate.marketing.channel` | ✗ | — | — | ✗ |
| `realestate.lost.reason` | ✗ | — | — | ✗ |

Every `currency_id` defaults to `self.env.company.currency_id` at creation time
and is then free-floating; the five sequences are global, so all companies share
one `LST-` / `TXN-` counter.

---

## 8. Commission and accounting defects

### 8.1 Splits can exceed the entitlement

There is **no gross commission**. Each `realestate.commission` line computes
independently:

```python
rec.amount = (rec.transaction_id.sale_price or 0.0) * (rec.percentage or 0.0) / 100.0
```

so four lines at 25% each of the *sale price* produce 100% of the sale price as
commission. Nothing reconciles the lines against a total, and nothing prevents
the sum from exceeding any entitlement. M13's requirement — *"one authoritative
gross commission, then allocations"* — is entirely absent.

### 8.2 `paid` counts a reversal as payment

```python
rec.paid = rec.bill_id.payment_state in ('paid', 'in_payment', 'reversed')
```

`reversed` means the bill was **cancelled by a credit note**. Money was not
paid. This is the identical defect Module 3's audit found on
`realestate.sale.installment` and Module 1 still has on
`realestate.contract.payment` — a suite-wide idiom that is wrong in all three
places. `in_payment` is also counted, which means "registered, not yet
reconciled".

The dashboard's `commissions_mtd` KPI is built on this flag, so it reports
reversed commissions as paid revenue.

### 8.3 No clawback of any kind

`transaction.action_cancel()` refuses to run on a closed transaction, which
means a deal that collapses **after** closing cannot be recorded at all — and
therefore commission already billed or paid can never be reversed. M14 is
completely unimplemented, and there is no model to hold a reversal.

### 8.4 Hard-coded / missing accounting configuration

`action_create_vendor_bill` creates an `account.move` with:

* no `company_id`;
* no `product_id` — so no account and **no tax** is derived, making every
  commission implicitly tax-free (M15 explicitly forbids assuming this);
* no `currency_id`;
* no journal;
* and it **posts immediately**, with no approval step.

### 8.5 One-shot billing, no approval

There is no entitlement → approval → payable → paid progression. A commission
line goes straight from "typed in" to "posted vendor bill" in one click, by any
Agent.

---

## 9. Duplicate-customer risk

Today a single human being can simultaneously be:

* a `res.partner`;
* one or more `crm.lead` rows (website EOI, API interest, backend);
* one or more `realestate.lead` rows (Brokerage);

with **no detection between the two families**. Odoo's `duplicate_lead_ids`
only searches `crm.lead`.

There is no broker lead registration, so two external brokers can introduce the
same buyer and both claim the commission with nothing in the system to
adjudicate — the dispute M6 exists to prevent.

---

## 10. Recommended CRM unification strategy

### 10.1 Verdict

`crm.lead` becomes canonical. The audit found **no business or technical reason
to keep a second pipeline**: nothing outside Brokerage references
`realestate.lead`, the customer-facing entry points already use `crm.lead`, and
every capability Brokerage's lead model provides is either already in CRM or is
a field that belongs *on* CRM.

### 10.2 The path

```
     legacy realestate.lead
              │
              │  (1) bridge: crm_lead_id link + read-only mirror
              ▼
        canonical crm.lead  ────────────┐
              │                          │  (2) real-estate requirements layer
              │                          │      added TO crm.lead
              │                          │
              │  (3) creation disabled   │
              │      on the legacy model │
              ▼                          ▼
        (4) deprecated, retained    viewings / offers / matches
            for one release          re-pointed at crm.lead
```

Concretely:

1. **Add `crm` to `depends`** and extend `crm.lead` with the real-estate
   requirements layer (normalised child models where a concept is
   multi-valued — preferred projects, locations, property types).
2. **Re-point the relations.** `viewing`, `offer`, and the match/shortlist
   records gain `crm_lead_id`. `lead_id` (legacy) is **kept** and mirrored, so
   no existing row breaks.
3. **Migrate and bridge.** Every legacy lead gets a `crm_lead_id`, classified
   per M30 (MIGRATE / LINK EXISTING / AMBIGUOUS / LEGITIMATE MULTIPLE /
   SKIPPED). Ambiguous and multiple cases are **reported, never merged**.
4. **Disable new creation** on `realestate.lead` with an actionable error
   pointing at the CRM pipeline, and keep the model + table + views for one
   release so a rollback is possible and history stays readable.

### 10.3 What must NOT happen

* Do not merge on name, email, or phone alone — a customer legitimately has
  several opportunities.
* Do not drop the table or the sequence in this release.
* Do not lose `realestate.lead.name` (`LEAD-00001`); it goes onto the migrated
  `crm.lead` as a legacy reference so a printed document from 2024 can still be
  found.
* Do not reproduce `state` as a second selection alongside `stage_id`. Stage is
  the pipeline; `lost_reason_id` + `active` is loss.

### 10.4 Sales teams and the Module 2 deferred gap

Odoo's `crm.team` + `crm.team.member` already provide multi-team membership, so
**no `realestate.sales.team` is needed**. Extending `crm.team` with
`allowed_realestate_project_ids` gives the project scope that Module 2's
implementation report listed as intentionally deferred (*"project/team scoping
needs a project↔user assignment model and `crm` isn't a dependency"*). Adding
`crm` here is what unblocks it, and it can be done **without touching
Developer**: the record rules live in Brokerage and read `crm.team`.

### 10.5 Resellers (`website_crm_partner_assign`)

Present and LGPL-3, so usable. It provides partner grades, activations, lead
assignment to resellers and a partner portal. It does **not** provide time-boxed
customer registration, conflict detection between two brokers claiming the same
buyer, per-project inventory authorisation, or real-estate commission splitting
— which is the entirety of M6. Recommendation: **do not take a hard dependency**;
build the broker layer on `res.partner` (as M7 requires) and leave the reseller
module as an optional, late-bound integration if a deployment wants it.

---

## 11. Dead / obsolete code

| Item | Status |
|---|---|
| `offer.state = 'countered'` | declared, guarded against, **never reachable** |
| `lead.state = 'converted'` | **never set** — the dashboard's conversion KPI is permanently 0.0% |
| `viewing.next_action` | captured, never read |
| `commission.payment_move_id` | declared, never written |
| `transaction.document_ids` | declared, no view exposes it |
| `realestate.lost.reason` | duplicates `crm.lost.reason` |
| `realestate.marketing.channel` | overlaps `utm.source` / `utm.medium`; carries a `base_url` used by nothing |
| `res.users.is_realestate_agent` | a flat boolean doing the job `crm.team.member` does properly |
| `real_estate_developer` in `depends` | declared, **entirely unused** |
| `commission.action_mark_unpaid` | exists only to raise an error |

---

## 12. Data-migration risks

| Risk | Mitigation |
|---|---|
| Legacy lead with no `partner_id` (field is optional) | cannot be matched deterministically → classify `SKIPPED`, report by reference |
| Same customer, several genuine opportunities | never merge on partner alone; classify `LEGITIMATE MULTIPLE` and create one `crm.lead` each |
| Several candidate `crm.lead` rows for one legacy lead | classify `AMBIGUOUS`, link nothing, report both sides |
| Legacy lead has no company | infer nothing; assign the agent's company and record that it was inferred |
| `viewing.lead_id` / `offer.lead_id` are nullable | re-point where possible, leave and report where not; **never delete a viewing or an offer** |
| Legacy `LEAD-` sequence values | preserve on the migrated `crm.lead` as a legacy reference |
| Re-running the migration | must be idempotent — keyed on the bridge link, not on a heuristic |
| Chatter and attachments | `mail.message` / `ir.attachment` are `res_model`+`res_id` addressed; moving them is possible but lossy if done twice → move once, guarded by the bridge link |

---

## 13. Answers the brief asked for, in one place

| Question | Answer |
|---|---|
| Authoritative lead object today | **`crm.lead`** |
| Modules depending on `realestate.lead` ids | **none outside Brokerage** |
| API creates | **`crm.lead` only** |
| One customer in two pipelines | **yes, routinely, undetected** |
| Transaction can disagree with CRM | **yes — it has no lead link at all** |
| Commission can disagree with CRM attribution | **yes — commission comes from the listing, not the opportunity** |
| Production data blocking deterministic migration | **no schema blocker; three data hazards, all handled by classify-and-report** |

---

## Destructive ambiguity check

None found. The migration path in §10.2 is additive: it creates links, adds
fields, and disables new creation. It deletes no table, drops no column, and
merges no record without a deterministic key. Proceeding to M1.
