# Rental UX & Architecture Audit — `atmta_real_estate`

Phase 1 deliverable of the Rental / Leasing re-architecture.
Audited 13 September 2026 against the working tree (including uncommitted work) and
`atmta_w26_fresh`, a fresh install of every module in this repository with demo data.

Paths are relative to the repository root; `AR/` is `atmta_real_estate/`.
**[V]** = verified in code, the Odoo 18 source or the installed database.
**[I]** = inference from code paths, not executed.

Nothing structural has been changed. The only code change made so far is the P0 expiry
fix recorded in §B, which is covered by tests and an upgrade check.

---

## Contents

- [0. Summary](#0-summary)
- [A. Domain ownership matrix](#a-domain-ownership-matrix)
- [B. Duplicate-authority matrix](#b-duplicate-authority-matrix)
- [C. Current menu tree](#c-current-menu-tree)
- [D. Proposed menu tree](#d-proposed-menu-tree)
- [E. Current lease workflow](#e-current-lease-workflow)
- [F. Proposed user journey](#f-proposed-user-journey)
- [G. UX problems](#g-ux-problems)
- [H. Screens that remain after cleanup](#h-screens-that-remain-after-cleanup)
- [I. Compatibility risks](#i-compatibility-risks)
- [J. Decisions needed before Phase 3](#j-decisions-needed-before-phase-3)
- [K. Corrections to the earlier module map](#k-corrections-to-the-earlier-module-map)

---

## 0. Summary

`atmta_real_estate` (manifest name "Real Estate — Rental (Standalone)", v0.6) holds two
generations of the same product. The enterprise leasing layer — `lifecycle_state`,
allocations, the billing engine, renewals, amendments, terminations, deposits, move-in/out,
unit turns, rent roll — is sound and well tested (309 tests). The original rental layer
still runs beside it, and in several places **the original layer can bypass the enterprise
layer's controls from the lease form**.

Size **[V]** at the audit: 46 Python files (9,122 code lines), 29 stored models defined, 14 models
extended, 42 data files, 80 views, 33 menus, 8 scheduled jobs (now 7), 8 security groups,
20 record rules, 309 tests.

Size **[V]** after the dead-code batch (14 Sep 2026, v0.10): 46 Python files outside tests and
migrations (9,465 code lines), 25 stored models defined (plus 4 abstract, 1 transient), 14
models extended, 38 data files, 75 views, 30 menus, 5 scheduled jobs, 9 security groups,
21 record rules, 87 ACL rows, 27 test files, 372 tests. See §L for each change.

The most important findings, in order:

1. **Lease expiry had two authorities — resolved.** See §B.
2. **A lease can be invoiced twice from the screen today.** Three invoicing paths exist;
   no code prevents a lease invoiced for its whole term the legacy way from being invoiced
   again per period. The engine's automatic invoicing must stay off until guards ship.
3. **Legacy lease buttons bypass the enterprise controls.** *Confirm Contract* skips manager
   approval and the self-approval rule; the legacy *Activate* skips the signature and
   allocation checks; the legacy *Terminate* skips settlement, credit notes, the deposit and
   move-out. They sit beside the correct buttons, often with the same label.
4. **`realestate.payment.plan` is declared by two modules.** The developer module's class
   replaces rental's in the registry; both Payment Plans menus error today.
5. **Rental owns things other modules depend on.** The accounting helper used by four
   modules, the sales-side `commercial_status`, and the property `state` bridge read by the
   public API all live here. They can move only through a shared lower module.
6. **Several date-driven stored fields never refresh.** Expiry buckets, days overdue and a
   lease line's "occupies the unit" flag drift, so expiry filters, arrears ageing and
   future-start occupancy are wrong over time.
7. **The lease form is overloaded.** 15 tabs, two status bars in every state, two smart-button
   rows, up to nine header buttons, and the summary rendered below the notebook.

---

## A. Domain ownership matrix

Action key — **Keep**: stays in Rental as is · **Merge**: fold into the authoritative
implementation · **Move**: change owning module, keeping technical names · **Bridge**: keep a
read-only/compatibility shim while consumers migrate · **Remove**: delete after checks.

### A.1 Lease core and changes

| Feature / Model | Current owner | Target owner | User-facing? | Action | Dependencies | Migration risk | Recommendation |
|---|---|---|---|---|---|---|---|
| `realestate.contract` (lease) | Rental | Rental | Yes | Keep | `real_estate_checks` `_inherit` + stored FKs (`models/sale_contract.py:131`, `models/check.py:166`); `real_estate_contract_template` `_inherit` (`models/contract_template_inherit.py:70`); `real_estate_api` portal JSON (`controllers/api_v1_portal.py:246-259`) | High | Keep the technical name and every field the API and Checks read. |
| `lifecycle_state` engine (`AR/models/contract_lifecycle.py`, `AR/models/lease_states.py`) | Rental | Rental | Yes | Keep | All lease workflows | — | The only lease status. Every path that changes it must run the same role and prerequisite checks (see §B). |
| Legacy lease `state` | Rental (computed bridge) | Rental | No | Bridge | `real_estate_api` portal JSON and document ownership (`controllers/api_v1_downloads.py:169,830-849`); `AR/tests/test_compatibility.py` | Medium | Keep computed for readers; remove its status bar and filters from the UI. |
| `realestate.contract.property.line` (allocation) | Rental | Rental | Inside the lease | Keep | Availability, rent roll, billing engine | — | The only unit-allocation concept. |
| `realestate.contract.line` (legacy unit line) | Rental | — | Yes (menu "Contract Units") | Merge → allocations | `contract.payment.contract_line_id`; rental history; two crons; two pivot SQL views; `atmta_operations_app/views/menus.xml:44-45` | Low on this DB (0 rows) / Medium on production | Migrate rows to allocations; hide the menu now; remove after one release. Per-line state, plans and rules have no equivalent **[V]**. |
| `realestate.contract.party` | Rental | Rental | Inside the lease | Keep | — | — | — |
| `contract.type` | Rental | Rental | Configuration | Keep | Legacy lease form | Low | Move to rental configuration. |
| `organization.type` | Rental | Rental | Partner form, printed contract | Keep | `AR/views/res_partner.xml:12,20`; `AR/reports/real_estate_contract.xml:217` | Low | In use — not dead. |
| `realestate.contract.renewal` / `.amendment` / `.termination` | Rental | Rental | Yes | Keep | `atmta_operations_app` actions | — | Add search views (none today). |

### A.2 Pricing, billing and money

| Feature / Model | Current owner | Target owner | User-facing? | Action | Dependencies | Migration risk | Recommendation |
|---|---|---|---|---|---|---|---|
| `realestate.rent.escalation.rule` | Rental | Rental | Inside the lease | Keep | Billing engine, renewal pricing | — | Fix: with `billing_mode='property'` the engine reads mirrored `allocated_rent` and escalations never apply (`AR/models/billing_engine.py:258-260`) **[V]**. |
| `realestate.contract.increment.rule` | Rental | — | Yes (menu "Price Adjustment Rules") | Merge → escalation (discount rules → incentives) | Legacy schedule generator; many2many on lease and line; operations menu; demo data | Medium | Convert per lease; `duration_months` (temporary increase) has no equivalent; hide the menu. |
| `realestate.contract.charge.rule` / `.incentive` | Rental | Rental | Inside the lease | Keep | Billing engine | — | — |
| `realestate.payment.plan` | Rental **and** `real_estate_developer` (same `_name`) | `real_estate_developer` | Yes | Remove from Rental | 10 leases linked; `contract.payment.payment_plan_id`; ACL rows; demo; two actions | **High** | Developer's class replaces Rental's in the registry (`odoo/models.py:733-750`) **[V]**. Convert lease plans to billing frequency, then remove Rental's declaration and its `ir_model_data`. |
| `realestate.contract.payment` (billing obligation) | Rental | Rental | Yes | Keep | `real_estate_checks` FK with cascade and stored computes (`models/allocation.py:54,368`); `real_estate_api` exposes field names and the four state values (`controllers/api_v1_portal.py:350-391`) | **Critical** | Freeze field names and state values. One user-facing name: *Billing Obligations*. |
| `realestate.contract.payment.line` (charge line) | Rental | Rental | Inside the obligation | Keep | Billing engine; maintenance charge-back | — | In use — not dead. |
| Billing engine (Path B, `AR/models/billing_engine.py`) | Rental | Rental | Yes | Keep — the one invoicing authority | — | Medium (migration of legacy leases) | Only path handling company/currency, proration, charge taxes, incentives, escalation and termination credits **[V]**. |
| Legacy per-payment invoicing (Path A, `action_create_invoices`) | Rental | — | No (no button; RPC only) | Remove | None external | Low | Raise a deprecation error first. Its cron exits immediately and invoices nothing (`AR/models/rental_invoicing.py:14`) **[V]**. |
| Legacy whole-lease invoice (Path C, `action_generate_invoices`) | Rental | — | Yes (button) | Bridge → Remove | Leases already invoiced this way | Medium | Hide for new leases; keep `invoice_id` as read-only history until those leases end; add a termination credit for them. |
| Legacy schedule generator (`action_generate_payment_schedule`) | Rental | — | Yes (button) | Remove from UI, then retire | `payment.plan` fields | Medium | Deletes engine rows on engine leases (`AR/models/contract.py:431-432`) **[V]**; probably crashes when the developer module is installed **[I]**. |
| `realestate.contract.deposit` (deposit record) | Rental | Rental | Yes | Keep | — | — | The one deposit concept. |
| Legacy deposit fields and tab (`AR/models/contract_deposit.py`) | Rental | — | Yes (tab with its own header) | Bridge → Remove | Migration 0.6 seeded deposit records from them | Low–Medium | "Deposit Held" counts only records (`AR/models/deposit_record.py:567-574`) **[V]**. Make read-only, then remove. |
| `realestate.account.tools` | Rental | A shared lower module (later) | No | Keep in place | 17 call sites in brokerage, developer, construction, construction certification | **Critical** | Do not move until a common lower module exists. |

### A.3 Occupancy, property and operations

| Feature / Model | Current owner | Target owner | User-facing? | Action | Dependencies | Migration risk | Recommendation |
|---|---|---|---|---|---|---|---|
| `realestate.move.in` / `.move.out` / `.move.out.deduction` | Rental | Rental | Yes | Keep | — | — | Add search views (none today). |
| `realestate.unit.turn` | Rental | Rental | Yes | Keep | Maintenance request | — | Add a search view; its default filter does nothing today. |
| `realestate.property.meter` / `.meter.reading` | Rental | A property-operations layer (later) | Yes | Keep now, Move later | Move-in/out, metered charge rules, legacy meter columns in `atmta_property_core` | Medium | Needs a groups owner below Rental first. |
| `realestate.contract.utility.line` (landlord utility cost) | Rental | Rental, retire later | Managers | Keep; remove from daily menus | `net_income`; operations menu | Low | Not tenant billing — a payables register. |
| `realestate.maintenance.request` | Rental | A facilities module (later) | Yes | Keep now, Move + bridge later | Unit turn; tenant charge-back; `atmta_operations_app` "My Maintenance" action on `assigned_to` | Medium | Generic facilities **[I]**. |
| Rental property extension (availability, `occupancy_status`) | Rental | Rental | Yes | Keep | Allocations | — | Fix the never-refreshed `occupies_property` **[V]**. |
| `commercial_status` | Rental | `atmta_property_core` | Yes | Move (same name) | Developer sales engine; maquette; brokerage via legacy `state` | Medium–High | Sales modules write it; Rental only reads it. |
| `construction_status` | Rental (plain field) + developer (stored compute override) | Developer / project | Yes | Move | Availability | Medium | Two definitions of one field **[V]**. |
| `handover_status` | Rental | `real_estate_handover`, or remove | Yes | Move or Remove | Nothing writes it anywhere **[V]** | Low | — |
| `maintenance_status` | Rental | Facilities, with maintenance | Yes | Move later | Maintenance, unit turn, developer availability | Medium | — |
| Property `state` bridge | `atmta_property_core` (field) + Rental (compute override) | `atmta_property_core` | Yes | Move the bridge | About 30 readers in API, portal, maquette, brokerage; developer writes it | **Critical** | Keep values and behaviour exactly. |
| `rental_status` / `for_rent` | Rental | — | Form only | Bridge → Remove | One view, one test | Low | Computed from legacy lines; contradicts `occupancy_status` on the demo DB **[V]**. |
| `usage_category` | Rental | `atmta_property_core` | Yes | Move | Developer pricing; brokerage and maquette test fixtures | Medium | — |
| `realestate.property.rental.history` | Rental | — | Yes (menu) | Bridge → Remove | Rent statistics; two menus | Low | Rewritten on every lease write and goes stale (`AR/models/contract.py:570-594`) **[V]**. Replace with a read-only view over allocations. |

### A.4 Reporting, UI, security, automation

| Feature / Model | Current owner | Target owner | User-facing? | Action | Dependencies | Migration risk | Recommendation |
|---|---|---|---|---|---|---|---|
| `realestate.rent.roll` | Rental | Rental | Yes | Keep | — | — | Fix per-query SQL rebuild and missing indexes. |
| Rental dashboard (current, `AR/static/src/js/dashboard/`) | Rental | Rental | Yes | Keep, redesign | — | — | See §G and the spec. |
| First-generation dashboard assets (`AR/static/src/js/rental_dashboard.js` + xml + scss) | Rental | — | No | Remove | `AR/tests/test_dashboard_assets.py:19-23`; `UPGRADE_0.4.md` | Low | Not in any bundle; registers the same action tag. |
| `realestate.report.contract` / `.line` + unloaded pivot views | Rental | — | No | Remove | ACL rows 20–25 | Low | SQL views over the legacy line table. |
| Unloaded reports called by the API (`contract_payments_report.xml`, `contract_financial_summary.xml`, `report_contract_summary.xml`) | Rental | Rental | Through the API | Bridge | `real_estate_api/controllers/api_v1_downloads.py:88-90` — those endpoints return 404 today **[V]** | Medium | Load after checking their fields, or retire the endpoints. |
| `AR/reports/contract_report.xml` | Rental | — | No | Remove | An API docstring only | Low | — |
| `AR/controllers/controllers.py`; `AR/reports/contract_excel_report.py` | Rental | — | No | Remove | Scaffold fully commented; Excel controller never imported and broken **[V]** | Low | — |
| Legacy groups (`group_realestate_readonly/user/manager`) | Rental | Rental, as hidden aliases | Yes | Bridge | 14 references in `atmta_operations_app` menus; `real_estate_contract_template` ACL; `atmta_roles`; ~13 tests | Medium | Both group files are `noupdate="1"` **[V]**: changes need a migration. |
| Leasing groups (`group_rental_*`) | Rental | Rental | Yes | Keep | Lifecycle checks | Medium | The one role hierarchy. |
| Lease expiry job | Rental | Rental | No | **Resolved** | — | — | See §B. |
| Contract-line status jobs (two) | Rental | — | No | Remove with `contract.line` | — | Low | Redundant with each other. |
| Availability recompute job | Rental | Rental | No | Keep, fix | — | Medium | Does not refresh allocation `occupies_property`; docstring promises batch commits the code does not do **[V]**. |
| Renewal reminder job | Rental | Rental | No | Keep | — | — | Creates activities no screen shows. |
| Engine invoicing job (inactive) | Rental | Rental | No | Keep inactive until guards and migration ship | — | — | — |
| Legacy auto-invoice job (active, does nothing) | Rental | — | No | Remove the record | — | Low | — |

---

## B. Duplicate-authority matrix

Every area where two implementations can change the same business concept.

| # | Concept | Implementation 1 | Implementation 2 (+3) | Can both act on the same lease? | Consequence | Authority | Status |
|---|---|---|---|---|---|---|---|
| 1 | Lease expiry | `check_contract_expiry` + cron "Check Contract Expiry" | `_cron_expire_leases` + cron "Rental: end expired leases" | Yes, daily | Legacy job ended leases with arrears, on notice, or in companies that switched expiry off | `_cron_expire_leases` | **Resolved 13 Sep 2026.** Legacy method now delegates (`AR/models/contract.py:313`); cron record removed from `AR/data/schedule_actions.xml` and deleted on upgrade (verified on a copy of a pre-change database); 5 tests in `AR/tests/test_lease_expiry.py`; leasing suite 309/309. |
| 2 | Invoicing | Path A `action_create_invoices` | Path B billing engine; Path C whole-lease invoice via sale order | Yes (C then B, C then A, engine flag flipped on a legacy lease) | **Double billing** — nothing blocks it **[V code path]** | Path B | **Open — P0** |
| 3 | Billing schedule generation | Legacy `action_generate_payment_schedule` | Engine `action_generate_billing_schedule` | Yes | Legacy deletes uninvoiced engine rows (`AR/models/contract.py:431-432`); engine duplicates legacy rows because they lack `period_start` (`AR/models/billing_engine.py:191-196`) | Engine | **Open — P0** |
| 4 | Approval | Legacy *Confirm Contract* (`AR/models/contract.py:191-197`) | *Approve* (manager group + self-approval check, `AR/models/contract_lifecycle.py:329-340`) | Yes | Confirm moves proposal/pending approval straight to pending signature; only the company approval switch is checked, from draft/proposal | *Approve* | **Open — P0** |
| 5 | Activation | Legacy *Activate* (`AR/models/contract.py:276-280`) | *Activate* (signature + allocation + property manager, `AR/models/contract_lifecycle.py:351-361`) | Yes — both visible at once | Unsigned lease can go active (demo lease CT-00001 shows both buttons) | Lifecycle *Activate* | **Open — P0** |
| 6 | Termination | Legacy *Terminate* (`AR/models/contract.py:295-307`) | Termination record (notice → approve → settle → complete) | Yes — both labelled "Terminate" | No settlement, penalty, credit note, deposit handling or move-out | Termination record | **Open — P0** |
| 7 | Deposit | Legacy deposit fields and tab | `realestate.contract.deposit` | Yes | Legacy-recorded deposits missing from "Deposit Held" | Deposit record | **Open — P0** (money visibility) |
| 8 | Payment plan | Rental `realestate.payment.plan` (4 fields) | Developer `realestate.payment.plan` (~20 fields, same `_name`) | Registry keeps one class | Both Payment Plans menus error; legacy generator reads a field that no longer exists **[I]** | Developer | **Open — P0** |
| 9 | Lease status display | Legacy `state` status bar | `lifecycle_state` status bar | Both render in every state | Cancelled lease shows "Terminated" on the legacy bar | `lifecycle_state` | Open — P1 |
| 10 | Unit allocation | `realestate.contract.line` | `realestate.contract.property.line` (mirrored) | Mirror | Two user-facing unit concepts | Allocation | Open — P1 |
| 11 | Rent increase | `contract.increment.rule` (legacy generator) | `rent.escalation.rule` (engine) | Different generators | Two rent-increase screens; escalations skipped in property billing mode | Escalation | Open — P1 |
| 12 | Property status | Legacy `state` bridge; `rental_status` | Five status dimensions | Writes translated | `rental_status` contradicts `occupancy_status` | Dimensions | Open — P1 |
| 13 | Navigation | "Real Estate" root | "Property Operations" app | — | Two trees for the same screens | See §J decision 1 | Open — P1 |
| 14 | Billing screens | "Payment Schedules" menu/action | "Billing Obligations" menu/action | Same records | Both open the obligations list; legacy list unreachable | *Billing Obligations* | Open — P1 |
| 15 | Security roles | `group_realestate_*` | `group_rental_*` | ACLs split by family | A user with only the legacy User group cannot save a lease with a property — the allocation sync writes allocations they have no ACL for **[V code path]** | Rental groups | Open — P1 |
| 16 | Rental history | `realestate.property.rental.history` | Allocations / rent roll | — | History goes stale | Allocations | Open — P2 |
| 17 | Contract-line expiry | `_cron_update_line_statuses` | `cron_update_contract_line_states` | Yes | Redundant | Retire with the legacy line | Open — P2 |

---

## C. Current menu tree

Installed menus, from `atmta_w26_fresh` **[V]**. Items owned by other modules are marked.

**Real Estate** (`atmta_real_estate.real_estate_menu_root`)

- Dashboard → Rental Dashboard (client action)
- Properties Map (client action)
- Properties
  - All Properties → `realestate.property`
  - Property Types → `property.type`
  - Rental History → `realestate.property.rental.history`
- Leasing
  - Expiry & Renewal Board → `realestate.contract`
  - Renewals · Amendments · Terminations
- Contracts
  - All Contracts → `realestate.contract` *(opens the expiry-board kanban, which cannot create a lease)*
  - Contract Units → `realestate.contract.line`
  - Payment Schedules → `realestate.contract.payment`
  - Generated Documents *(real_estate_contract_template)*
- Operations
  - Move-Ins · Move-Outs · Unit Turns · Utility Meters · Meter Readings
- Billing
  - Billing Obligations → `realestate.contract.payment`
  - Security Deposits → `realestate.contract.deposit`
- Utilities → `realestate.contract.utility.line`
- Treasury *(real_estate_checks — 21 items)*
- Maintenance
  - Requests → `realestate.maintenance.request`
- Customer Service *(real_estate_customer_service)*
- Reporting
  - Rent Roll → `realestate.rent.roll`
- Public API *(real_estate_api)* — Embed Tokens
- Configuration
  - Payment Plans *(errors — see §B #8)*
  - Price Adjustment Rules → `realestate.contract.increment.rule`
  - Contract Templates *(real_estate_contract_template)*

**Property Operations** (`atmta_operations_app`, visible to the V2 pilot group)

- Overview · Properties Map · My Work (maintenance assigned to me)
- Properties — All Properties · Rental History
- Leasing — Lease Contracts · Contract Units · Expiry & Renewal Board · Renewals · Amendments · Terminations
- Billing — Billing Obligations · Security Deposits
- Occupancy — Move-Ins · Move-Outs · Unit Turns · Utility Meters · Meter Readings · Utility Lines
- Handover *(real_estate_handover)* — Handover Dashboard · Handovers · Snagging Issues · Warranties
- Service — Tickets · Maintenance Requests
- Reporting — Rent Roll
- Configuration — Property Types · Price Increment Rules · Handover Checklist Templates · Ticket Categories · Embed Tokens

The operations app references 22 rental action xmlids and 14 rental group bindings **[V]**.

---

## D. Proposed menu tree

One canonical Rental navigation. Items move out of the tree, not out of the product: each
removed entry has a home listed underneath.

**Rental** (keeps the xmlid `atmta_real_estate.real_estate_menu_root`; renamed)

- **Overview** — Rental Dashboard (task-first; see the spec)
- **Leases** *(Rental User and above)*
  - Leases — the main entry. Filters replace sub-menus: My Leases, Draft & Proposals, Awaiting Approval, Awaiting Signature, Active, Expiring Soon, On Notice, Ended
  - Expiring & Renewals — the expiry board, grouped by live end-date ranges
  - Renewals
  - Amendments
  - Terminations
- **Units** *(availability, rental-oriented)*
  - Available Units — `realestate.property`, default filter *Available to lease*
  - All Rental Units
  - Properties Map
- **Tenants** — `res.partner` filtered to partners with leases *(needs investigation of the existing tenant fields before building; see §J)*
- **Billing** *(Leasing Agent and above; accountants)*
  - Billing Obligations — default filter *Outstanding*
  - Security Deposits
- **Occupancy** *(Property Manager and above)*
  - Move-Ins
  - Move-Outs
  - Unit Turns
  - Meters & Readings *(transitional, until a property-operations module owns meters)*
- **Reporting** *(Property Manager and above)*
  - Rent Roll
  - Lease Analysis *(pivot/graph over leases)*
- **Configuration** *(Rental Manager only)*
  - Settings — rental section of General Settings
  - Contract Types

**Leaves the Rental tree, and where it goes**

| Current item | Destination |
|---|---|
| Contract Units | Removed; units are managed inside the lease (allocations) |
| Payment Schedules | Removed; one entry, *Billing Obligations* |
| Price Adjustment Rules | Hidden, then migrated into escalation rules |
| Payment Plans | Removed from Rental; owned by Development & Sales |
| Rental History | Removed; lease history on the unit form |
| Utilities (landlord costs) | Manager-only, reached from Reporting or the unit |
| Maintenance › Requests | Reached from the unit and the unit turn; owned by Operations/Service |
| Treasury | `atmta_treasury_app` *(its own menus need verifying first)* |
| Customer Service | `atmta_operations_app` › Service (already present) |
| Public API › Embed Tokens | `atmta_operations_app` › Configuration (already present) |
| Contract Templates · Generated Documents | `atmta_development_app` (already present) and a smart button on the lease |
| Property Types | Property core configuration *(still reachable to Rental Managers)* |

Menu xmlids that other modules use as parents are kept, not deleted (§I).

---

## E. Current lease workflow

### E.1 Lifecycle and transitions [V]

States (`AR/models/lease_states.py`): `draft`, `proposal`, `pending_approval`,
`pending_signature`, `active`, `notice`, `ended`, `terminated`, `cancelled`.

| From | Allowed to |
|---|---|
| draft | proposal, pending_approval, pending_signature\*, cancelled |
| proposal | draft, pending_approval, pending_signature\*, cancelled |
| pending_approval | draft, proposal, pending_signature, cancelled |
| pending_signature | proposal, pending_approval, active, cancelled, terminated |
| active | notice, ended, terminated |
| notice | active, ended, terminated |
| ended | active (reopened by an approved amendment) |
| terminated | — |
| cancelled | draft |

\* Requires approval when the company setting *Require Lease Approval* is on.

Blocks other leases: pending_signature, active, notice. Occupies the unit: active, notice.
Closed: ended, terminated, cancelled.

Server-side controls already in place **[V]**: a bare write of `lifecycle_state` is refused for
non-superusers; creating a lease in any status other than draft is refused; each lifecycle
action checks its group; approval checks self-approval; activation checks signature and
allocation.

### E.2 What the lease form shows today [V]

Legacy header buttons (`AR/views/contract_views.xml:9-58`) and lifecycle buttons
(`AR/views/contract_enterprise_views.xml:20-48`) render together. The legacy `state` is
computed from `lifecycle_state`, so both sets react to the same lease.

| Lifecycle (→ legacy state) | Buttons visible together |
|---|---|
| draft (→ draft) | Generate Payment Schedule, Reset to Draft, Print, Propose, Submit for Approval, (Generate Billing Schedule), Generate Document — two "generate schedule" buttons when advanced billing is on |
| proposal (→ ready) | **Confirm Contract** + Submit for Approval, Reset, Print, (Generate Billing Schedule), Generate Document |
| pending_approval (→ ready) | **Confirm Contract** + Approve, Reset, Print, (Generate Billing Schedule), Generate Document |
| pending_signature, not billed (→ confirmed) | Create Invoices, Mark Signed, Activate, Amend, Reset, Print, (Generate Billing Schedule), Generate Document |
| pending_signature, billed (→ invoiced) | **Activate (legacy) + Activate**, Mark Signed, Amend, Reset, Print, (Generate Billing Schedule), Generate Document |
| active / notice (→ active) | **Terminate (legacy) + Terminate**, Invoice Due, Renew, Amend, Reset, Print, (Generate Billing Schedule), Generate Document — up to nine buttons |
| ended / terminated / cancelled | Reset (errors from these states), Print, Generate Document |

Missing from the form **[V]**: no button calls `action_give_notice`, `action_end_lease`,
`action_cancel_lease` or `action_reopen_draft` — notice and cancellation cannot be reached from
the lease itself.

### E.3 Around the lease

- Renewal: Propose → Negotiating → Approve Terms → Tenant Accepted → Create Renewal Lease (or Reject).
- Amendment: Propose → Approve → Mark Signed → Apply (or Cancel).
- Termination: Record Notice → Approve → Settle → Complete (or Cancel).
- Move-in: Start Inspection → Capture Meter Readings → Complete (Complete is allowed straight from Scheduled).
- Move-out: Start Inspection → Capture Final Readings → Assess → Complete → Settle Deposit.
- Unit turn: Start → Cleaning Done → Repairs Done → Pass Inspection → Mark Ready (Mark Ready shows before inspection passes).

---

## F. Proposed user journey

The seven flows the redesign must support, with the screens that serve them. Gaps are what
Phase 3–4 must close.

| # | Flow | Screens and actions | Gaps today |
|---|---|---|---|
| 1 | Find an available unit → create lease → submit → approve → sign → activate | Units › Available Units (filter by type, building, area, rent) → *New Lease* from the unit → lease form: Submit for Approval → Approve (manager) → Mark Signed → Activate | No "Available Units" entry; legacy buttons bypass approval/signature (§B #4–5); All Contracts opens a kanban that cannot create |
| 2 | Active lease → billing schedule → invoice | Lease › Billing Schedule tab → Generate Billing Schedule → Invoice Due (or the engine job, once enabled) → Billing Obligations list | Three invoicing paths; no guard against double billing (§B #2–3) |
| 3 | Active lease → renewal | Leases › Expiring Soon → *Renew* → renewal record → Create Renewal Lease | Expiry filters use a stored bucket that goes stale; renewal has no search view |
| 4 | Active lease → notice → move-out → ended | Lease › *Give Notice* → Move-Out (inspection, assessment, deductions) → Settle Deposit → lease ends | *Give Notice* is not on the lease form; move-out has no search view |
| 5 | Active lease → early termination | Lease › *Terminate* → termination record (notice, approve, settle, complete) | A second "Terminate" skips settlement (§B #6) |
| 6 | Manager → upcoming expiries and overdue obligations | Dashboard "My work" and "Portfolio health" blocks → filtered lists | Dashboard tiles open lists that do not match their numbers; no "My work" section |
| 7 | Leasing agent → no admin configuration | Menus and actions gated by role; Configuration for Rental Manager only | Root and most parents have no groups; configuration visible to anyone with menu access |

---

## G. UX problems

**P0 — business risk**

1. Lease expiry had two authorities. *Resolved.*
2. Double invoicing is reachable from the lease form (§B #2).
3. Two billing-schedule generators destroy or duplicate each other's rows (§B #3).
4. *Confirm Contract* bypasses manager approval and self-approval (§B #4).
5. The legacy *Activate* bypasses signature and allocation checks (§B #5).
6. The legacy *Terminate* bypasses the termination settlement (§B #6).
7. Deposits recorded the legacy way are excluded from "Deposit Held" (§B #7).
8. `realestate.payment.plan` is declared by two modules; its menus error (§B #8).
9. Three tenant PDF downloads in the public API return 404 because their report files are not loaded **[V]**.

**P1 — major UX problem**

10. Two status bars on every lease; notice and cancellation unreachable from the form; *Reset to Draft* always visible and failing from most states.
11. Lease form overload: 15 tabs, summary rendered after the notebook (`AR/views/contract_enterprise_views.xml:92`), two smart-button rows (the second is not a real button box, `AR/views/contract_enterprise_views.xml:52-53`).
12. "All Contracts" opens the expiry kanban (view chosen by name at equal priority) and cannot create a lease; both payment menus open the same list.
13. Date-driven stored fields never refresh: `days_to_expiry` / `expiry_bucket` (`AR/models/contract_renewal.py:469`), `days_overdue` / `overdue_bucket` (`AR/models/billing_obligation.py:224`), allocation `occupies_property` (`AR/models/contract_property_line.py:149`), `vacant_days` (`AR/models/unit_turn.py:110`).
14. The lease list and search are built on the legacy `state`; no lifecycle, unit or rent column.
15. Seven workflow models have no search view (deposit, move-in, move-out, unit turn, renewal, amendment, termination); fourteen menu actions have no empty-state help.
16. Two navigation trees for the same screens; other apps injected into the rental root.
17. Two role families; a legacy User cannot save a lease with a property (§B #15).
18. Dashboard tiles open lists that disagree with the tile (invoiced/collected this month, move-ins/outs, expiring 60/90); renewals tile omits accepted renewals; contracted rent sums per-period amounts as if monthly; money totals mix currencies.
19. Escalations are ignored when a lease bills per property (`AR/models/billing_engine.py:258-260`).
20. No indexes on `realestate_contract_payment.contract_id`, `date_due` or `state`; lease status computes, the rent roll and the dashboard scan the table **[V]**.

**P2 — cleanup**

21. Terminology: *Real Estate Contract* / *Contract* / *Lease* / *Rental contract*; one list named *Payment Schedules*, *Scheduled Payments*, *Payment Schedule*, *Contract Payments*, *Billing Obligations*; *Contract Units* / *Contract Lines* / *Properties*; two different buttons named *Terminate*.
22. Property screens repeat themselves: a status bar plus five badges in the header, two history tabs, two group-bys labelled "Occupancy".
23. Search mistakes: "Due This Month" has no end date (`AR/views/lease_operations_views.xml:245-246`); "In Arrears" means *overdue* on leases and *any outstanding* on the rent roll; two Group By blocks on lease search.
24. The Invoices smart button hides on `payment_count` instead of `invoice_count` (`AR/views/contract_views.xml:74`).
25. Dead code: first-generation dashboard, unloaded pivot views and report models, controller scaffold, broken Excel controller.
26. `rental_status` contradicts `occupancy_status`; rental history rewritten on every lease write.
27. Rent-roll SQL rebuilt on every query; dashboard trend chart runs 13 queries in a loop.
28. Availability job does not commit per batch although its docstring says it does.

**P3 — polish**

29. `optional="hide"` on a form field has no effect (`AR/views/property_enterprise_views.xml:27`).
30. Properties empty state uses an outdated CSS class.
31. Inconsistent badge colours across status fields.
32. Move-out *Settle Deposit* appears both as a header button and a smart button.

---

## H. Screens that remain after cleanup

| Area | Screen | Views |
|---|---|---|
| Overview | Rental Dashboard | Client action (redesigned) |
| Leases | Leases | List (primary), form, search, pivot, graph; kanban only for the proposal pipeline |
| Leases | Expiring & Renewals | Kanban grouped by live end-date range, list |
| Leases | Renewals | List, form, search *(new)*, kanban as the renewal work queue |
| Leases | Amendments | List, form, search *(new)* |
| Leases | Terminations | List, form, search *(new)* |
| Units | Available Units / All Rental Units | List, kanban, form (rental-oriented), search |
| Units | Properties Map | Client action |
| Tenants | Tenants | Filtered partner list and form section |
| Billing | Billing Obligations | List, form, search, pivot, graph |
| Billing | Security Deposits | List, form, search *(new)* |
| Occupancy | Move-Ins / Move-Outs | List, form, search *(new)*, calendar |
| Occupancy | Unit Turns | Kanban (work queue), list, form, search *(new)* |
| Occupancy | Meters & Readings | List, form |
| Reporting | Rent Roll | List, pivot, graph |
| Reporting | Lease Analysis | Pivot, graph |
| Configuration | Rental Settings | Settings section |
| Configuration | Contract Types | List |
| Printouts | Lease PDF · Unit Status PDF · report actions the API needs | QWeb |

---

## I. Compatibility risks

Ranked. Each constrains what the cleanup may rename, move or remove.

1. **`realestate.account.tools` lives in Rental** and has 17 call sites in `real_estate_brokerage`, `real_estate_developer`, `real_estate_construction` and `atmta_construction_certification`. Moving it without a new common dependency breaks those modules at runtime, not at install.
2. **Non-rental property fields live in Rental** — `commercial_status`, `maintenance_status`, `usage_category`, the base `construction_status` and the property `state` bridge. The developer sales engine, the public API and the portal depend on them; they move only to a lower module, never out of existence.
3. **`view_property_form` and `view_property_list`** are inherited by five modules; their xpath anchors (`oe_button_box`, `status_ribbon`, `//notebook`, `page[@name='media']`, the first `parent_id`) must stay stable.
4. **`realestate.contract.payment` field names and state values** are a public API contract and a Checks foreign key with cascade and stored computes.
5. **22 action xmlids and 14 group bindings** are referenced by `atmta_operations_app`; renaming any one breaks that module's install.
6. **Three parent menu xmlids** (`real_estate_menu_root`, `menu_contract_management`, `menu_configuration`) parent five menus in four modules.
7. **`realestate.payment.plan`** — resolving the collision changes which class owns the table and needs an `ir_model_data` migration.
8. **Lease `state` and legacy `property_id`** are read by the API's partner endpoints and document-ownership check; a lease without `property_id` fails that check today **[I]**.
9. **Runtime-only references**: `stock_location_re_root` (developer), `action_realestate_contract_report` (API), the Leaflet static path (handover dashboard).
10. **Both security group files are `noupdate="1"`**: group changes need an explicit migration, or installed databases keep the old groups silently.
11. **Other modules' tests create leases** with `name`, `partner_id`, `property_id`, `start_date`, `end_date`, `currency_id` (`real_estate_checks/tests/common.py:222-232`) and assert legacy property `state` values; these are part of the compatibility contract.

**Safe to hide** (menus deactivated or re-grouped, xmlids kept): the whole legacy menu tree,
Payment Plans, and every rental leaf menu — the operations app links to actions, not menus.

**Unsafe to remove or rename**: the models and fields in risks 1–4 and 8; the view xmlids and
anchors in risk 3; the menu, action and group xmlids in risks 5, 6 and 10; the runtime
references in risk 9.

---

## J. Decisions needed before Phase 3

These change production behaviour or ownership, so they are the business owner's call.

**Decided 13 September 2026** — decisions 1–4 were confirmed by the business owner as recommended:

| # | Decision | Outcome |
|---|---|---|
| 1 | Navigation ownership | **(a)** The Rental root is the one rental navigation; Property Operations keeps Handover and Service only. |
| 2 | Invoicing transition | Guards first; whole-term legacy invoices stay until those leases end; new leases use the engine only; automatic engine invoicing waits for backfill and a pilot company run. |
| 3 | Payment plans | Remove Rental's declaration in its own tested migration step, converting lease plans into a billing frequency. |
| 4 | Role migration | Map legacy groups onto the rental hierarchy and grant an explicit *See All Portfolios* flag to migrated users. |

Decisions 5–7 remain as recommended below unless the owner says otherwise.

1. **Navigation ownership.** Either (a) the Rental root becomes the one rental navigation and `atmta_operations_app` keeps only Handover and Service, or (b) the Property Operations app becomes the one rental navigation and the legacy root is hidden. *Recommendation: (a)* — it keeps Rental self-contained for installs without the V2 pilot.
2. **Invoicing transition.** Leases already invoiced for their whole term the legacy way stay on that invoice until they end (engine blocked for them), and new leases use the engine only. *Recommendation: yes.* Automatic engine invoicing stays off until the guards, the backfill of legacy schedules and a pilot company run are done.
3. **Payment plans.** Remove Rental's declaration and convert each lease's plan into a billing frequency. *High risk; recommendation: yes, as its own migration step.*
4. **Role migration.** Legacy User → Leasing Agent, legacy Manager → Rental Manager, read-only → Rental User; add an explicit *See All Portfolios* flag so migrated users keep seeing every lease. *Recommendation: yes.*
5. **Maintenance, meters and the sales-side status fields.** Move them to lower modules (property core / a facilities module) in a later phase, not during the UX rebuild. *Recommendation: defer; keep them working where they are.*
6. **Legacy rent-increase rules.** Convert to escalation rules for future terms only, keeping invoiced periods untouched. *Recommendation: yes.*
7. **Tenants screen.** Confirm that tenants are ordinary contacts with at least one lease, before a Tenants entry is built.

---

## K. Corrections to the earlier module map

- The legacy auto-invoice job is active but exits immediately and invoices nothing (`AR/models/rental_invoicing.py:14`). The duplicate-invoicing risk comes from buttons and the engine flag, not from two jobs running.
- `demo/demo.xml` is loaded — through the manifest's `demo` key.
- `realestate.contract.payment.line` and `organization.type` are in use.
- Three of the "unloaded" report files are called by `real_estate_api`; their endpoints return 404 today.
- `atmta_procurement_core`, `atmta_procurement_receipt` and `atmta_procurement_vendor` do not depend on Rental; `atmta_construction_certification` does, through the accounting helper.

---

## L. Progress log

Each entry records what changed, how it was verified, and what it deliberately leaves for later.

### 13 Sep 2026 — P0: one lease-expiry authority (§B #1)

- `check_contract_expiry` now delegates to `_cron_expire_leases`; the legacy cron record is removed from `AR/data/schedule_actions.xml`.
- Verified: 5 new tests (`AR/tests/test_lease_expiry.py`); leasing suite 309/309; upgrading a copy of a pre-change database deleted the legacy cron and left one lease-expiry job.
- Behaviour change: leases on notice past their end date are no longer ended by the job; their notice process closes them.

### 13 Sep 2026 — P0 safety batch 1: one billing path per lease; legacy buttons follow the lifecycle (§B #2–7)

- **Billing authority** — `realestate.contract._billing_authority()` (`AR/models/billing_engine.py`) returns `whole_lease`, `engine` or `legacy`.
  - `_create_invoices` (and therefore *Invoice Due* and the engine job) refuses leases billed by a whole-term invoice.
  - The engine schedule generator refuses whole-term leases and leases that still hold legacy rows.
  - `action_generate_invoices` refuses engine leases and leases with any obligation already invoiced.
  - The legacy schedule generator refuses engine leases.
  - `action_create_invoices` (per-payment invoicing) is retired with an explanatory error.
- **Legacy lease buttons** (`AR/models/contract.py`) route through the lifecycle actions:
  - *Confirm* approves through `action_approve_lease` or submits for approval when the company requires it.
  - *Activate* runs `action_activate_lease` (signature, allocation, role).
  - *Terminate* opens the termination process for live leases; ending a lease that never went live requires a Rental Manager.
  - *Reset to Draft* uses `action_reopen_draft`.
  - The four buttons are hidden on the form; *Invoice Due* is hidden on whole-term leases.
- **Legacy deposits** — the five legacy deposit actions refuse server-side; the legacy deposit tab is read-only history.
- Verified: 13 new tests (`AR/tests/test_legacy_paths.py`), one existing test updated to sign before activating; leasing suite 322/322; `real_estate_checks` suite 325/325.
- Left for later: the no-op legacy auto-invoice cron record is `noupdate` and invoices nothing, so it stays until a migration removes it; the engine default and payment-plan removal follow as batch 2.

### 13 Sep 2026 — Batch 2: payment plans leave Rental; new leases bill through the engine (§B #8, decision 3)

- Rental no longer declares `realestate.payment.plan`: `AR/models/payment_plans.py`, `AR/views/payment_plan_views.xml`, the Payment Plans menu, three ACL rows, the plan fields on lease, legacy unit line and billing obligation, five demo plans and ten demo lease links are removed. `real_estate_developer` is the only owner.
- The legacy schedule generator (`action_generate_payment_schedule`, `action_generate_payment_lines`) is retired with an explanatory error; it depended on Rental's plans.
- `use_billing_engine` defaults to on for new leases (decision 2); existing leases keep their value.
- Migration `AR/migrations/0.7/` records each lease's plan as a billing frequency before the schema changes and applies it afterwards. Plans billing every 1, 3, 6 or 12 months, or every year, translate; anything else is left unchanged and logged with the lease id. The snapshot column `re_legacy_plan_frequency` is kept for audit.
- Verified:
  - Upgrade of a database holding both declarations (6 plans, 10 lease links): 7 monthly, 1 quarterly and 1 semi-annual recorded and applied; 1 weekly plan flagged for manual setup; model now owned only by `real_estate_developer`; all 6 plans preserved; Rental's plan columns, fields and XML IDs gone; no errors.
  - Leasing suite 324/324 (2 new tests; the batch 1 generator test now expects the retirement message); `real_estate_developer` 248/248; `real_estate_checks` 325/325.
- Left for later: the two unloaded report templates that still print plan fields are handled with the API report decision (§G #9).

### 13 Sep 2026 — Batch 4: legacy price rules carried over to escalations and incentives (§B #11, decision 6)

- Only the retired legacy generator applied `realestate.contract.increment.rule`, so without a conversion a lease's agreed increases and discounts would silently stop existing.
- `realestate.contract._convert_legacy_price_rules()` (`AR/models/contract_increment_rule.py`) translates, per open lease:
  - permanent increases → dated rent escalations, only when the escalation chain reproduces the rent the generator billed at every rule's start month (to the cent);
  - discounts → percentage or fixed-discount incentives over the matching window (to the lease end when the rule had no duration).
- **Flagged, never guessed:** increases from the lease start, temporary increases, two increases in the same month, and increase sets whose rent would differ because the generator applied them by priority. Increases and discounts each convert all-or-nothing per lease. Rules that would start after the lease ends are skipped. Every created record carries a marker, so re-running creates nothing; closed leases are left alone; a chatter note on each lease lists what was created and what needs attention.
- Migration `AR/migrations/0.7/post-migrate.py` runs the conversion after applying payment-plan frequencies; the two steps run independently.
- Verified:
  - Upgrade of a copy with 5 rules on 10 leases: 8 escalations and 2 incentives created exactly as predicted; CT-00003 rent 18,000 → 18,900 → 20,223 and CT-00008 8,500 → 8,925 → 9,549.75 → 10,049.75, both equal to the legacy formula; CT-00002 and CT-00009 flagged for a temporary increase; one after-term rule skipped; 8 chatter notes; no errors.
  - Leasing suite 336/336 (12 new conversion tests).
- Left for later: legacy rule fields and the Price Adjustment Rules menu are hidden with the navigation batch; the rules themselves stay as history.

### 13 Sep 2026 — Batch 5: one Rental navigation (§B #15–18, decision 1)

- `AR/views/menus.xml` is one tree under a root renamed **Rental**:
  - Overview
  - Leases: Leases, Expiring & Renewals, Renewals, Amendments, Terminations
  - Units: Available Units, All Rental Units, Map
  - Billing: Billing Obligations, Security Deposits
  - Occupancy: Move-Ins, Move-Outs, Unit Turns, Maintenance Requests, Meters, Meter Readings
  - Reporting: Rent Roll, Lease Analysis, Utility Costs
  - Configuration: Settings, Lease Types, Property Types, Contract Templates
- Removed from navigation (the records stay reachable from their lease): Contract Units, Payment Schedules, Price Adjustment Rules, Rental History, the separate Maintenance and Leasing sections (`AR/views/leasing_menus.xml` deleted) and Generated Documents.
- The Leases action is named *Leases* and opens on the list, filtered to current leases, with an empty-state message. Explicit view bindings fix Odoo picking the expiry kanban by name, which could not create a lease. New actions: Available Units, Lease Analysis, Lease Types, Rental Settings.
- Treasury, Customer Service and Public API are their own apps. Property Operations (V2 pilot) keeps only Handover and Service.
- Menu groups name each section's Rental role and the legacy group that reaches it; access is still enforced by ACLs, record rules and server-side role checks.
- Verified: fresh install of Rental, Property Operations, Treasury and Contract Templates gives the tree above with no legacy Rental menus, no Generated Documents under Rental and no errors; leasing suite 336/336.
- Verified on upgrade: upgrading only Rental on a copy of the pre-change database removes the old menus with no errors. Odoo moves child menus of a deleted menu to the top level, and the ones moved are Treasury, Customer Service and Public API, which batch 5 makes top-level apps anyway. Upgrading those three modules afterwards changes nothing and leaves no stray top-level menus.

### 13 Sep 2026 — Batch 6: Rental roles replace the legacy groups (§B #19, decision 4)

- **One role choice.** The Rental category (renamed from *Real Estate*) now holds only Rental User → Leasing Agent → Property Manager → Rental Manager, so the user form offers them as one dropdown. *Allow Self-Approval* and the new *See All Portfolios* flag sit in a new *Rental Permissions* category.
- **Legacy groups are hidden aliases.** `group_realestate_readonly/user/manager` keep their XML IDs and implications, but move to the hidden Technical category and are renamed "Real Estate … (legacy)". Every role still implies one, so ACL rows, menus and other modules that name them keep working.
- **See All Portfolios** (`group_rental_all_portfolios`, rule `rule_contract_all_portfolios`): lifts the own-or-unassigned narrowing for Rental Users and Leasing Agents. Company isolation still applies, and the flag grants nothing without a role.
- **Mapping** (`res.users._map_legacy_rental_roles`, `AR/models/rental_roles.py`):
  - legacy Manager → Rental Manager; legacy User → Leasing Agent; Read-only → Rental User;
  - users the portfolio rule did not narrow before also get See All Portfolios, so nobody loses sight of a lease;
  - portal and public users are logged and left unchanged, because a role would make them internal;
  - re-running it changes nothing.
- **Demotion.** Odoo keeps implied groups when a group is removed, so a demoted Rental Manager would have kept the hidden legacy Manager rights. `res.users.write` now removes legacy aliases that none of the user's remaining groups imply, but only when a Rental role was removed. Legacy groups that other modules grant directly survive unrelated group changes.
- **Fixes:** a legacy User can now save a lease with a unit, because as a Leasing Agent they can create its allocation.
- **Migration** `AR/migrations/0.8/post-migrate.py`: both group files are `noupdate`, so it renames and re-categorises the existing records, then maps users. The new category, flag and rule arrive with the module data. Manifest version 0.8.
- Verified:
  - Upgrade of a copy at 0.6 with five seeded users (legacy Read-only, User and Manager; a Rental User also given legacy User; a portal user given Read-only) ran 0.7 and 0.8 with no errors. Results: Manager → Rental Manager with no flag; Read-only → Rental User with the flag; User → Leasing Agent with the flag; the already-narrowed user → Leasing Agent without the flag; the portal user unchanged and logged. Groups, categories, the four lease rules and the Rental menu tree were all as designed.
  - Leasing suite 349/349 (13 new tests in `AR/tests/test_rental_roles.py`). They cover each mapping, the portfolio flag including company isolation, the dropdown, demotion through the user form, and a legacy group granted directly surviving other group changes.
- Left for later:
  - Suites of other modules that create users with a legacy group directly (procurement, maquette, construction) should keep passing, because those users are created, not demoted, but they have not been re-run yet; they run in Phase 5.
  - Menus still list both a role and a legacy group per section.

### 13 Sep 2026 — Batch 3: units are allocations (§B #10, §A.1)

- **One unit concept.** Units are entered on the lease's *Properties* tab as allocations (`realestate.contract.property.line`). The legacy *Contract Lines* tab is hidden. `realestate.contract.line` stays installed as compatibility: its rows still mirror into allocations, and nothing is deleted. It holds 0 rows on every local database checked (nine), and no other module references it.
- **Readers moved to allocations:**
  - a unit's *Rental Status* is *Rented* while any allocation blocks it (lease awaiting signature, active or on notice). These are exactly the lifecycle states the legacy check read as confirmed, invoiced or active, so single-unit leases read the same; multi-unit leases were previously read from legacy line states that nothing sets any more (`AR/models/rental_property.py`);
  - the unit status report lists the unit's allocations with their lease status (`AR/reports/property_report.xml`);
  - rental history for a multi-unit lease is built from its allocations (`_sync_contract_history`).
- **Renewal** of a multi-unit lease copies the allocations the lease still holds at its end, with their rent and deposit, onto the new term. Units an amendment removed or substituted ended early and are not renewed. Legacy lines are no longer created (`AR/models/contract_renewal.py`).
- **Retired:** the two legacy line-status jobs (*Update Contract Line Status*, *Contract Line Auto Expiry*; `AR/data/contract_line_expiry_cron.xml` deleted).
- **Migration** `AR/migrations/0.9/post-migrate.py` recomputes rental status for every unit, because Odoo does not recompute a stored field when only its dependencies change. Manifest version 0.9.
- Verified:
  - Upgrade of a copy at 0.6 to 0.9 with no errors: rental status recomputed for 32 units, 0 changed (2 rented, matching the 2 units with blocking allocations); all three retired legacy jobs deleted, the five enterprise jobs kept.
  - Leasing suite 356/356 (7 new tests in `AR/tests/test_lease_units.py`): a multi-unit lease entered only as allocations goes live and blocks other leases; rental status follows the lifecycle; renewal carries over only the units still held; history and the unit report read allocations; the jobs are gone.
  - `real_estate_checks` suite 325/325 with batches 3 and 6 in place.
- Left for later:
  - The legacy line model, its views, its ACL rows, `contract_payment.contract_line_id` and the two pivot reports that read legacy lines are removed with the dead-code batch (§B #9) after one release.
  - Legacy buttons still write legacy line states, which nothing reads.

### 14 Sep 2026 — Dead-code batch: legacy unit lines, pivot reports, V1 dashboard, Excel export; date-state that no longer goes stale (§B #9, §G)

Codex adversarial review: **pending due to usage limit** (also pending for batches 3–6).

**Removed**

| Item | What went | Replaced by | Compatibility decision |
|---|---|---|---|
| `realestate.contract.line` | model (`AR/models/contract_line.py`), its list/form/pivot views and *Contract Lines* action (`AR/views/contract_line_views.xml`), the hidden *Contract Lines* tab, 3 ACL rows, `realestate.contract.line_ids`, `realestate.contract.payment.contract_line_id`, `realestate.contract.property.line.legacy_line_id` and origin `legacy_line`, `realestate.property.rental.history.contract_line_id`, `realestate.property.contract_history_ids`, every legacy line-state write in the legacy lease buttons | allocations (`realestate.contract.property.line`), entered on the lease's *Properties* tab | No other module referenced it (repository-wide search). Data carried over by migration; see below. |
| `realestate.report.contract`, `realestate.report.contract.line` | two SQL-view models, 6 ACL rows, their never-loaded view/action files (`contract_pivot_report.xml`, `contract_line_pivot.xml`) | *Lease Analysis* and the rent roll (already in the menu) | No menu, action or loaded view ever reached them; both queried the legacy table. |
| First-generation dashboard | `static/src/js/rental_dashboard.js`, `static/src/xml/rental_dashboard.xml`, `static/src/scss/rental_dashboard.scss` | the active dashboard (`static/src/*/dashboard/`) — the only implementation of `realestate.rental_dashboard` | Never bundled; no addon referenced the files. |
| Excel export | `AR/reports/contract_excel_report.py` (never imported; malformed route `contract/excel/report`, `xlsxwriter.workbook`), `AR/reports/__init__.py` | the standard list export and the rent roll's XLSX export | No caller of the URL anywhere in the repository. |
| Controller scaffold | `AR/controllers/` (all commented out) and its import | — | Nothing routed. |

The three report templates the Public API names but Rental never loaded (`report_contract_full`, `report_payment_schedule`, `report_contract_agreement`) now read allocations instead of legacy lines (and `report_contract_full` reads `contract_payment_ids`, the field that exists). Whether to load them is still the API report decision (§G #9).

**Migrated** (`AR/migrations/0.10/`, manifest 0.10). `odoo.upgrade.util` is not installed on this server, so `util.remove_model` was not available; the removal is explicit and verified:

- *pre-migrate*: every legacy line recorded in `re_legacy_contract_line_backup` with the allocation that mirrored it; line-level price rules and payment plans in `re_legacy_contract_line_rule_backup` (logged as not carried over — only the retired generator applied them); obligations that named a line in `re_legacy_payment_line_backup`, and those obligations given the line's unit directly; mirroring allocations turned into ordinary ones; the two pivot SQL views dropped. Backup tables are kept for audit.
- *post-migrate*: `realestate.contract._restore_legacy_unit_lines` carries each unmirrored line on a multi-unit lease over as an allocation (unit, dates, rent, notes); already-allocated lines and duplicates create nothing; a line on a single-unit lease is reported and kept in the backup; a line that would double-book is reported, not forced. Then the legacy table and its three relation tables are dropped (Odoo does not drop the table of a model missing from the registry) with their `ir_model_relation` and foreign-key `ir_model_constraint` rows, and the columns of the date fields that are no longer stored.
- *end-migrate*: one full recompute of every stored date-derived value (below). It runs at the end of the update because availability reads fields that later-loading modules add (`project_id`, `phase_id`); the first upgrade test ran it in post-migrate and wrongly marked two development units in planning available — caught by the per-record comparison and fixed.

**Date-state fixes.** Each field was classified; none is solved by a job that rewrites every record.

| Field | Class | Now |
|---|---|---|
| obligation `days_overdue`, `overdue_bucket` | A/B | computed for today, not stored; searched through `date_due` (`_overdue_since_domain`, `_overdue_bucket_domain`). Obligation search groups-by-ageing became per-bucket filters. |
| lease `days_to_expiry` | A/B | computed for today, not stored; searched through `end_date`. |
| lease `expiry_bucket` | C | stored (the expiry board groups by it); refreshed on the day a lease crosses 180/90/60/30 days or its end. |
| unit turn `vacant_days` | A | computed for today, not stored. New stored `turnaround_days` (fixed once the turn is ready) feeds the average-vacant-days KPI. |
| party `is_active_party` | A/B | computed for today, not stored; searchable. |
| allocation occupancy → property occupancy, legacy property state, lease occupancy | C | stored; refreshed on start date, the day after the end date, and move-out date. |
| property availability | C | stored; refreshed when an allocation starts/ends or the availability window opens/closes. |
| lease `payment_status` | C | stored; refreshed the day after an outstanding obligation's due date (dependency on the stored arrears field replaced by `date_due`). |
| lease `next_escalation_*`, `current_rent` | C | stored; refreshed on an escalation's effective date. |
| meter reading stats, escalation base/resulting amounts | — | not calendar-dependent (today is only a sort fallback for a missing date); unchanged. |

- `realestate.calendar.refresh` (`AR/models/calendar_refresh.py`) finds only records whose boundary fell since the last run, through the date columns, and remembers the run date (`atmta_real_estate.calendar_refresh_date`), so a gap is covered. The existing daily job (`cron_recompute_availability`, `noupdate`) keeps its XML ID and method, now named *Rental: refresh date-based status*.
- Rent roll: days to expiry, expiry bucket and "in arrears" are derived from the dates in the SQL view when read (`CURRENT_DATE` is the database server's date).
- Dashboard: occupied/vacant tiles, economic occupancy, the 30/60/90-day expiry tiles, the arrears-ageing chart and the overdue worklist read the dates for today, with the same domains their drilldowns open; the average-vacant-days KPI reads `turnaround_days`. Lease search filter *Expiring ≤ 90 Days* reads the end date (it now includes a lease ending today, as the dashboard tile does).

**Tests.** 16 new; 2 overlap tests rewritten to allocations; the dashboard asset test now asserts the first-generation files are gone from disk and bundle; the compatibility test no longer lists the removed model.

- `AR/tests/test_lease_units.py` (+5): removed models, fields and origin are absent; an unmirrored line becomes an allocation with its values; carry-over never duplicates (mirrored, same unit and dates, two lines for one unit, run twice); a line on a single-unit lease is reported, not added; a double-booking line is reported while the others carry over.
- `AR/tests/test_calendar_state.py` (11, frozen clock): days to expiry and bucket as the date moves, expired the day after the end; days overdue, bucket and collection status as the date moves, with searches; a future lease occupies only from its start date (dashboard correct before the refresh, stored status after); occupancy stops the day after the end; vacant days count up until ready and turnaround stays fixed; party activity; an escalation taking effect; expiry tiles equal their drilldowns on two days; arrears-chart buckets equal their drilldowns on two days; the daily job runs once per day.
- Totals: leasing suite **372/372**; `real_estate_checks` **325/325**.

**Upgrade verification** (copy of the pre-cleanup database at 0.6 → 0.10, no errors):

- Seeded legacy data: an unmirrored line → allocation (rent 800, notes kept); a mirrored line → ordinary allocation, no duplicate; a duplicate line → nothing; a line on a single-unit lease → skipped and kept in the backup; an obligation naming its unit only through a line → now names unit 10; a line-level increase rule → recorded and logged.
- Legacy table, its three relation tables and both pivot SQL views dropped. Stale metadata for the removed models — `ir_model`, `ir_model_fields` (on or pointing to them, and the removed field names), `ir_model_fields_selection`, `ir_model_data`, `ir_model_access`, `ir_act_window`, `ir_ui_view`, `ir_act_server`/`ir_cron`, `ir_model_relation`, `ir_model_constraint`, views mentioning removed fields, `ir_filters` — all **0**.
- Per-record comparison of unit availability, occupancy, legacy state and rental status, allocation occupancy, and lease expiry bucket, collection, occupancy and current rent: no difference except five leases gaining a next escalation date, which the 0.7 price-rule conversion in the same upgrade created. The two development units in planning stay unavailable.
- Jobs: the legacy line jobs stay removed; *Rental: refresh date-based status* renamed; refresh date recorded.

**Fresh install** of Rental, Property Operations, Treasury, Contract Templates, Public API and Customer Service: no errors; the removed models absent from metadata; one `realestate.rental_dashboard` client action; 31 Rental menus, none with a broken action; no window action or view on a missing model; no warning naming a removed model, field or view (the only warnings are missing `license` keys in unrelated add-ons and a procurement view naming a group that does not exist). The asset-bundle tests in the leasing suite build and serve the minified backend bundle with the dashboard in it.

**Remaining risks**

- Stored date-derived values are current from the daily refresh; between midnight and its run they reflect yesterday. Everything a user reads as a day count, and every dashboard tile, is computed for today.
- "Today" follows the user's timezone for computed values, the cron user's for stored refreshes, and the database server's in the rent-roll SQL view.
- The no-op legacy auto-invoice job (*Auto-invoice payments due soon*) is still installed (`noupdate`).
- The three API report templates are still not loaded.

### 14 Sep 2026 — Phase 4: UX implementation (RENTAL_UX_SPEC.md §5–§16)

Codex adversarial review: **pending due to usage limit**.

**4a — Lease screens** (`AR/views/contract_views.xml`; the inherited enterprise form and search were folded in and their records removed)
- One statusbar on `lifecycle_state`; the legacy `state` appears in no view.
- Header shows only the next actions, at most three, following spec §6.1. The legacy Confirm, Activate, Terminate and Reset buttons are gone from the form; their methods remain and route through the lifecycle checks.
- New `action_withdraw_notice` (Property Manager; refused while a termination owns the notice) and `action_amend_lease` (refuses leases that cannot be amended).
- Print Lease, Amend Lease and Generate Document (Contract Templates) moved to the gear menu.
- One smart-button box in spec order: Billing Obligations and Invoices always, the rest only when non-zero.
- Status chips beside the lease number, a summary block, and six tabs: Units, Parties, Billing, Charges & Adjustments, Deposit & Occupancy, Terms & Documents. The Units tab is read-only on single-unit leases. Legacy increment and discount rules, the utilities tab and the manual-payment toggle are no longer on the form. The legacy deposit tab shows only when a lease holds legacy deposit data.
- Billing buttons live on the Billing tab; a lease billed by one whole-term invoice says so and offers no per-period invoicing.
- List with the spec columns and decorations; proposal-pipeline kanban by lease status (cards not draggable); search with the spec fields, status and attention filters (live end-date domains), group-bys and a status search panel. The expiry board has its own search view without the panel, and each card shows the unit and a Renew button.

**4b — Available Units**
- A dedicated list (the shared unit list other modules extend is untouched): unit, building, type, usage, bedrooms, area, furnishing, available from, occupancy; sorted by availability date; a New Lease button on each available row and on the unit form (`realestate.property.action_new_lease`).
- The action lists leasable units only. New filters: Available Within 30 Days, Awaiting Signature, Furnished; group by building, sub-area, bedrooms and floor.
- The legacy Contract History tab (rental-history copy) is no longer on the unit form; Lease History reads allocations.

**4c — Work screens**
- Search views where none existed: move-ins, move-outs, unit turns, renewals, amendments, terminations, deposits. The Unit Turns action named a default filter that had no search view, so it opened unfiltered.
- Move-ins and move-outs: Upcoming (default), Today, Next 7 Days, Overdue, Completed, computed from the user's day; calendar views on the scheduled date.
- Kanban work queues for unit turns and renewals; renewals default to Open, deposits to Needs Action.
- Billing obligations: Due in 14 Days and Paid filters, eight default columns, ageing filters.
- Empty-state help (spec §14) on every one of these screens.

**4d — Tenants**
- `res.partner.rental_lease_ids`, stored `is_rental_tenant`, and computed current lease, balance due, outstanding obligations and deposits (one query per related model for any number of contacts).
- Tenants menu and list. Rental's contact details moved from the contact form's main group — shown to every user — to a Rental tab visible to rental roles only, with the contact's leases, outstanding obligations and deposits.

**4e — Dashboard** (`AR/models/rental_dashboard.py`, `AR/static/src/{js,xml,scss}/dashboard/`)
- Two calls: `get_work(scope)` returns My Work and Portfolio Health tiles and quick actions; `get_trends()` returns four charts after the tiles are on screen.
- One definition per tile (model, domain, measure, format, role, *Mine* path) feeds both the number and `action_drill(key, scope)`, so a tile and its list cannot disagree.
- Tiles are sent only to the roles that act on them. The spec's single *Approvals waiting* tile is three (leases, amendments, terminations), because one drilldown list cannot mix record types.
- *Mine* restricts lease-based tiles to leases the user is responsible for; *Team* shows every lease the user may see.
- Quick actions: New Lease, Find Available Unit, Move-In, Move-Out.
- Removed per spec §5.3: economic occupancy, arrears rate, average rent per m², contracted run rate, deposits held, average vacant days, completed moves this month, occupancy by building, rent per m² by usage, and the worklists.
- Deviations: occupancy over time still uses 12 ORM counts rather than one SQL query, so record rules keep applying. Outstanding rent is summed in the company currency only.

**4f — Terminology** (visible labels only; no technical names changed)
- Lease Number, Lease Type, Lease No, Renewal Lease, Renews Lease, Several Units, Responsible (for `user_id`); Lease instead of Contract on obligations, rental history, utility lines, invoices and maintenance requests; Recurring Charge; model descriptions Lease and Billing Obligation; billing obligation views and action; report and report-wizard names; the settings app is Rental.
- Rental has no translation files, so no Arabic labels exist to realign.

**Tests.** Leasing suite **414/414** after 4a–4f. New test files: `test_lease_form.py` (12), `test_available_units.py` (8), `test_workflow_screens.py` (8), `test_tenants.py` (6), `test_dashboard_work.py` (14). Rewritten for the new dashboard: the HOOT unit suite (`static/tests/rental_dashboard.test.js`), both tours, and the dashboard parts of `test_compatibility.py`, `test_calendar_state.py` and `test_dashboard_env.py`.

**Browser tests.** Every earlier run in this environment had skipped the HOOT suite, the tours and the viewport/RTL/company tests, because `websocket-client` was not installed. It was installed into the Odoo virtualenv (approved 14 Sep 2026), and the browser tests then ran in Chrome 151: **15/15** passed, none skipped. That covers the HOOT suite (24 tests, 61 assertions), the integration tour, layouts at 1024, 991 and 768 px, an Arabic right-to-left session, an empty company, the multi-company switcher with no leakage, and the minified production bundle containing the dashboard.

**Fresh install** of Rental, Property Operations, Treasury, Contract Templates, Public API and Customer Service after 4f: no errors; the renamed field labels, model descriptions and report names are in place.

**Upgrade verification** (copy of the pre-cleanup database, 0.6 → 0.10, no errors): the 10 lease tenants flagged as tenants; the removed lease views gone; the Tenants menu present; the dashboard sends a Rental Manager 22 tiles, every count tile equals its drilldown in both scopes, four trend charts, four quick actions.

**Remaining risks**
- Units without a company are counted by no dashboard tile (unchanged rule). The upgraded review database has 32 such units, so its portfolio tiles read 0.
- Dashboard tile labels come from the server; users see them in English until the module has translations.

### 14 Sep 2026 — Phase 5: validation

Codex adversarial review: **pending due to usage limit**.

**Modules that depend on Rental** (each installed into a scratch database with its own tests run; frozen counts from the enterprise-upgrade programme)

| Module | Installed with | Result | Frozen |
|---|---|---|---|
| real_estate_developer | — | 248/248, 1 skipped | 247 |
| real_estate_checks | — | 325/325 | 325 |
| real_estate_brokerage | — | 352/352 | 352 |
| real_estate_plan | — | 15/15 | 15 |
| real_estate_portal | brokerage | 7/7 | 7 |
| real_estate_maquette | brokerage, plan, portal | 291/291 | 291 |
| real_estate_procurement + real_estate_construction | — | 1158 run: 12 failed, 8 errors, 7 skipped | — |

- Developer runs one test more than its frozen count; the module carries uncommitted work that is not part of this programme.
- Maquette's tests assume brokerage and CRM are installed. Installed with plan only, 38 of 291 fail on `crm.lead`, `is_realestate_broker` and `realestate.property.match`; with brokerage, plan and portal, all pass. Portal likewise needs brokerage.
- Procurement installed alone skips 493 of 595 tests, which wait for construction. Construction depends on procurement, so both suites ran in one database.
- **The 20 procurement and construction failures predate this programme.** Every one looks up an ID under its old module name after the committed capability-extraction refactors (`d8af706`, `aa05a16`, `a2ad5df`) moved it: `action_sourcing_event` (now `atmta_procurement_sourcing`), `menu_evaluation` (now `atmta_procurement_app`), `action_payment_certificate` (now `atmta_construction_certification`), `action_itp` (now `atmta_construction_quality`); `menu_procurement_root`, `menu_evaluation_integrity_audit` and `menu_evaluation_rounds` exist in no module at HEAD. The failing tests are the sourcing, evaluation, award, quality and certification tours and the M6 navigation and evaluation-report tests. Procurement and construction have no uncommitted changes, and nothing in the Rental diff touches these IDs. Left unfixed: outside the Rental scope.
- `atmta_procurement_*` and the core construction modules do not depend on Rental. Public API, Customer Service, Contract Templates, Handover and the Operations app have no tests.

**User flows** (`AR/tests/test_user_flows.py`). Each flow runs as the users who do the work, not as the superuser: a Leasing Agent, a billing user (agent, See All Portfolios and invoicing rights), a Property Manager and a Rental Manager. Every role check and record rule on the way therefore applies.
1. Find an available unit in the Available Units action, open New Lease from the unit, submit (agent), approve (Rental Manager), mark signed (agent), activate (Property Manager). The allocation holds the unit, which is then rented and no longer available.
2. Generate the billing schedule of an active lease and invoice the due obligations: one posted invoice per due obligation, linked back to it; a second run creates nothing.
3. Start a renewal, propose, approve, accept and create the renewal lease: it starts the day after the current lease ends, at the proposed rent, linked to the old lease, which is not rewritten.
4. Complete a move-in; give notice through a termination at the lease end; approve and settle; complete the move-out, which vacates the allocation and opens a unit turn; complete the termination. The lease ends.
5. Early termination: the lease is terminated at the cut-off, and no obligation due after it stays billable.
6. A Rental Manager's dashboard counts the lease expiring without a renewal and the overdue invoiced obligation, and each tile's drilldown lists that record.
7. A Leasing Agent's navigation shows Leases, Units, Tenants, Billing and Overview but not Configuration, Occupancy, Reporting or their children. A Property Manager gets Occupancy and Reporting; a Rental Manager gets Configuration.

**Quality gates** (`AR/tests/test_usability_gates.py`, `AR/static/tests/tours/rental_usability_tour.js`)
- *5 seconds* (browser tour, logged in as a Leasing Agent): the Rental app opens on the dashboard with the app name in the navbar; My Work is the first section; New Lease and Find Available Unit are quick actions; no Property Manager quick action and no Configuration menu; Find Available Unit lists the seeded unit.
- *30 seconds*: an active lease, a tenant, an available unit, an expiring lease and an overdue obligation are each one menu away for a Leasing Agent, on the expected model, and open without an access error. Move-ins and move-outs are buttons on the lease, and menus for a Property Manager.
- *Consistency*: one statusbar (`lifecycle_state`); no `realestate.contract.line` model and no line field; escalation rules on the form and no legacy increment or discount rules; none of the retired menus; one billing obligations menu; the retired per-payment invoicing refuses; one lease-expiry scheduled job.
- *Noise*: a Leasing Agent's Rental menus hold no Configuration, Settings, Lease Types, Property Types, Occupancy, Reporting, Meters, Meter Readings or Utility Costs; no two menus open the same action; the lease form carries no journal, account, payment term or sale order field.
- *Safety*: an agent cannot approve, write the status directly, create a lease already active, or activate; an unsigned lease cannot go live even for a Property Manager; a dashboard tile hidden from the role cannot be drilled into. A Rental Manager of another company finds no lease and counts 0 active leases and 0 available units.

**Found and fixed: Leasing Agents saw Occupancy and Reporting.**
- The Occupancy and Reporting sections named Property Manager *or* the legacy `group_realestate_user`. Every Leasing Agent implies that legacy group (batch 6 keeps it as an alias), so agents got Move-Ins, Move-Outs, Unit Turns, Maintenance Requests, Meters, Meter Readings, Rent Roll, Lease Analysis and Utility Costs in their navigation, against spec §4 (Property Manager+). Batch 6 had left "menus still list both a role and a legacy group" for later; the new flow 7 and noise-gate tests caught it.
- Other sections were not affected: on Leases, Units, Tenants, Billing and Configuration the legacy group sits at or above the role's level.
- Fix (`AR/views/menus.xml`): both sections now name Property Manager only. Odoo adds menu groups on update and never removes them, so the legacy group is written as `-atmta_real_estate.group_realestate_user` to unlink it on installed databases. Server-side access is unchanged: ACLs, record rules and role checks never relied on the menu.
- A user given the legacy User group directly, without a role, no longer sees these two sections. Migration 0.8 moved every such user to a role, and no module grants the legacy group through implied groups.
- Test correction: `ir.ui.menu._visible_menu_ids` keeps a menu that has an action even when its parent section is hidden, so the first noise run reported Configuration children, and CRM, Inventory and Invoicing configuration menus, that no agent is ever shown. The tests and the noise script now count a menu only when every ancestor is visible (`AR/tests/common.py` `shown_menu_ids`), which is how the web client builds the tree.
- Verified on upgrade: a database installed with the old menu groups (Rental plus every module that depends on it), upgraded with only Rental, showed no errors, and a Leasing Agent no longer reached Occupancy, Reporting or any of their menus.
- Verified on a fresh install of the same modules: both sections carry only Property Manager. A Leasing Agent's Rental tree is Overview, Leases, Units, Tenants and Billing; a Property Manager's adds Occupancy and Reporting; a Rental Manager's adds Configuration. No role has two menus opening the same action. (The upgrade check could not list the section menus themselves: its script searched menus as OdooBot, which hides every menu that names a group. The fresh-install check searches with `ir.ui.menu.full_list`, and `_rental_menus` in the gates test now does the same.)

**Remaining noise from other modules (not changed: outside the Rental scope).** In the full-stack install a Leasing Agent also sees three apps:
- *Construction* (`real_estate_construction`, installed because Contract Templates depends on it): root menu with no group, and *Control Tower*, a client action.
- *Developer*: root menu with no group, and the Developer Dashboard, a client action.
- *Project Plans* (maquette): root menu with no group, and *3D Maquette* and *2D Master Plan*, client actions.

Odoo checks a menu's model access only for window, report and server actions, so client actions under an ungrouped root are shown to every internal user.

**Rental suite** (fresh install, `--test-tags /atmta_real_estate`): **436/436**, 2 skipped (they need Developer and Checks installed; both paths pass in those modules' own suites), on the final tree including every fix below. History: 427/427 after the menu-leak fix (the first run failed flow 7 and the noise gate); 433/433 with the chatter tests; 436/436 with the migration-guard tests. New: `test_user_flows.py` (7), `test_usability_gates.py` (5 plus the five-second browser tour), `test_chatter_bodies.py` (6), `test_migration_guards.py` (3), `shown_menu_ids` in `tests/common.py`.

**Found and fixed: chatter notes showed raw HTML tags.**
- `mail.thread.message_post` escapes a plain `str` body. Six Rental notes built HTML inside `_()` and posted it as `str`, so users saw literal `<b>`, `<br/>`, `<ul>` and `<li>`: the lease status note on every lifecycle step, incentive approved, amendment applied, availability override, primary tenant changed, and the legacy price-rule conversion note (batch 4). Found on the review copy, which held 7 status notes and 8 conversion notes stored escaped.
- Fix (`AR/models/contract_lifecycle.py`, `contract_incentive.py`, `contract_amendment.py`, `property_availability.py`, `contract_party.py`, `contract_increment_rule.py`): each note is `Markup(_("… <b>%(x)s</b> …")) % {…}`, and the conversion list is joined with `Markup`. The translated template is the only trusted part; every inserted value (names, reasons, snapshots) is escaped by `Markup.__mod__`. The renewal reminder is an activity note on an Html field and was already correct. Other `message_post` bodies in Rental contain no markup.
- Tests: `AR/tests/test_chatter_bodies.py` covers all six notes. Each must store real tags and no escaped tags, and a `<script>` value in a tenant name, override reason or incentive reason must be stored escaped.
- Repair of existing notes, review copy only (`atmta_w26_review`, decision 14 Sep 2026): a script un-escaped only `&lt;b&gt;`, `&lt;ul&gt;` and `&lt;li&gt;` (and their closing tags) in the two known note shapes. It required exactly 7 status and 8 conversion notes; checked each repaired body against the exact shape the fixed code produces; checked that re-escaping the restored tags reproduced the original byte for byte; and read every body back after writing. Any mismatch rolled everything back. Result: 15 notes repaired. A before/after `write_date` snapshot of all 1,161 messages shows exactly those 15 changed. No lease note still holds an escaped tag, and no other message in the database had one.

**Found and fixed: the billing obligation form still presented legacy price rules.**
- `realestate.contract.payment` has one form view, opened from Billing Obligations and from the lease. It showed a "Pricing Rules & Schedule" group with the retired increment and discount rules for every obligation, which contradicts batch 4 (one rent-escalation concept).
- Fix (`AR/views/contract_payment_views.xml`): the group is now "Legacy Price Rules", read-only, and invisible unless the obligation still records a legacy rule. Obligations billed by the engine never show it; migrated history stays readable. The list columns were already hidden by default.
- Test: the consistency gate (`test_one_concept_one_implementation`) asserts the group, its label, its visibility condition, and that both fields appear only inside it.

**Legacy references re-searched** (24 XML IDs removed from Rental, plus the removed models, fields, methods, crons and assets)
- Expected references only: the payment-plan IDs belong to `real_estate_developer`; the removed cron IDs appear in tests asserting they are gone; `legacy_line_id` appears only in the 0.10 migration and a test; `action_generate_payment_schedule`/`_lines` survive only as retired methods that raise, and in their tests; `realestate.contract.pivot` is only the name string of a pivot view record; the manifest's `rental_dashboard.*` paths point at the current dashboard files.
- Documentation only: the Development and Operations app manifests still describe `atmta_real_estate.action_payment_plan` as an action they chose not to surface. The action no longer exists; the text is outdated but does nothing.
- The payment schedule report template (`AR/reports/contract_payments_report.xml`) still prints plan and legacy rule fields but is not in the manifest, so it never loads (known since batch 2).

**Codex review (working tree, 14 Sep 2026)** — native `/codex:review`, run by the user. Four findings: P1 Rental 0.10 pre-migration unguarded on 0.5 databases (fixed below); P1 installment invoicing without a savepoint, P2 unit swap with no open installments, and P2 stored installment aging, all in uncommitted `real_estate_developer` changes that are not part of this programme (not changed; reported). Codex adversarial review: pending due to usage limit.

**Found and fixed: upgrading from 0.5 aborted in `0.10/pre-migrate.py`** (Codex P1)
- Reproduced on a copy of `atmta_ent_demo` (Rental 0.5): `UndefinedTable: relation "realestate_contract_property_line" does not exist` in `_release_mirrors`, registry failed to load, database left at 0.5. Odoo runs every applicable pre-migration before creating new tables, and the allocation table only arrives with 0.6; all earlier upgrade checks had started from 0.6.
- Fix: `_release_mirrors` returns when the allocation table has no `origin` column (no allocation can mirror a legacy line there). The other steps in the script were already guarded; the 0.6 and 0.7 pre-migrations were checked and are guarded too.
- Test: `AR/tests/test_migration_guards.py` runs the real script against a stub cursor shaped like a 0.5 database (no statement touches the allocation table; legacy lines are still backed up), a 0.9 database (mirrors are still released) and a database without legacy lines (nothing but reads).

**Found and fixed: Leasing Agents still saw Occupancy and Reporting on databases upgraded from 0.6**
- The upgrade check on a 0.6 copy showed both sections still linked to `group_rental_user`, which 0.6 had put on them; every Leasing Agent implies it. The earlier fix only unlinked the legacy User group, and `atmta_w26_review` (upgraded from 0.6) had the same two stale links and no others.
- Fix: both menus now also unlink `group_rental_user` (`-atmta_real_estate.group_rental_user`); the file header explains why.

**Upgrade verification after both fixes** (copies; the source databases are unchanged)

| From | Source | Result | Allocations | Duplicates | Menus (agent) | Notes |
|---|---|---|---|---|---|---|
| 0.5 | `atmta_ent_demo` | 0.10, no errors | 10 created for 10 leases | 0 | Occupancy/Reporting hidden | guard logged; 8 escalations, 2 incentives, conversion notes as lists; 0 legacy lines in this database, so the legacy-line restore path was not exercised by real data |
| 0.6 | `atmta_w26_fresh` | 0.10, no errors | 10 | 0 | hidden | 7 status notes still escaped on this unrepaired copy (posted before the fix) |
| 0.4 | `atmta_demo` | **failed outside Rental** | 10 | 0 | not applicable | Rental's migrations completed; `real_estate_procurement` 18.0.16.0.0 pre-migration (committed `aa05a16`, unchanged) then failed on its `ir_model_data` module rename when upgrading procurement from 18.0.7.0.0 (`UniqueViolation` on `ir_model_data_module_name_uniq_index`: `(atmta_procurement_award, action_procurement_award)` already exists), so the end-of-update cleanup never ran |

All three: no legacy tables left, no leases with a unit but no allocation; the same three data warnings each time (one weekly plan with no billing-frequency equivalent, two temporary increases flagged for manual review).

**Dependent modules re-run on the current tree** (each on its own fresh database)

| Module | Result | Frozen |
|---|---|---|
| real_estate_developer | 248/248, 1 skipped | 247 |
| real_estate_checks (Treasury) | 322/325 on the full run; the 3 browser tests timed out opening Chrome under memory pressure and pass 3/3 on rerun | 325 |
| real_estate_brokerage | 351/352 on the full run; the confidential-floor tour timed out (`onWillStart` > 3 s, `get_views` 3.3 s) and passes 2/2 on rerun | 352 |
| real_estate_plan | 15/15 | 15 |
| real_estate_portal (with brokerage) | 7/7 | 7 |
| real_estate_maquette (with brokerage, plan, portal) | 291/291 | 291 |
| real_estate_procurement + real_estate_construction | 1158 run: the same 12 failures and 8 errors (IDs moved by the committed refactors) | — |

**Fresh install** of Rental, Operations app, Public API, Brokerage, Treasury, Contract Templates, Customer Service, Developer, Maquette, Plan, Portal, Procurement and Construction: no errors; Rental 0.10; no demo property without a company; `web.assets_backend`, `web.assets_frontend`, `web.assets_tests` and `web.assets_unit_tests` compile; Occupancy and Reporting carry only Property Manager; per-role Rental menus as designed, no duplicate actions.

**Log review** (every run above): no registry failures, missing XML IDs, missing models or fields, invalid views, asset errors or migration errors from Rental. Rental test logs show 5 INFO "Access Denied" lines, each raised inside an `assertRaises(AccessError)`. Warnings that recur predate this programme: `parent_path` `unaccent` parameter, inconsistent `compute_sudo`/`store` on lease counters and meters, `occupancy_*` recursion, `_check_recursion` deprecation in `atmta_property_core` (present at HEAD), "models have no access rules" while intermediate modules install, and translation stack-info warnings from `_()` in the 0.7 conversion when no language is set. The procurement and construction logs keep the 16 known missing-XML-ID tracebacks.

**Codex developer findings fixed** (user request, 14 Sep 2026; `real_estate_developer` is frozen, so each fix has a regression test shown to fail on the pre-fix code first: the new tests were run against a scratch copy of the module carrying the original files, where 4 of 5 failed)
- P1, `models/sale_installment_v2.py` `cron_auto_invoice_due_installments`: each instalment is invoiced inside its own savepoint. The old `cr.rollback()` in the loop discarded every invoice created earlier in the same run while still counting it (in the test it tripped Odoo's "cannot rollback a cursor from inside a test" guard).
- P2, `models/contract_changes.py` `_apply_unit_swap`: when nothing open is left to re-cut (every instalment invoiced or paid, or the open ones total zero) and the new price leaves a positive balance, a balancing instalment for that balance is raised, due today. Before, the sale price rose and nothing billed the difference. Cheaper and equal swaps are unchanged.
- P2, stored ageing: new `cron_refresh_installment_aging` (daily cron `cron_refresh_installment_aging`, `data/cron.xml`) recomputes every unpaid, uncancelled instalment past its due date by flagging `date_due` as modified, which also refreshes the contract's collection state. The fields stay stored because the views group and filter on them.
- Tests: `real_estate_developer/tests/test_codex_findings.py` (5). Developer suite 253/253 (1 skipped) and Treasury 325/325 with the fixes.

**Second Codex review fixed** (native `/codex:review` of the working tree, 15 Sep 2026; user request "fix the Codex findings"; all in `real_estate_developer`. Every behaviour test below was run against a scratch copy of the module with these fixes reverted: 12 of the 17 new tests failed there, the 2 that guard unchanged behaviour passed, and the 3 migration tests could not run because the migration did not exist)
- P1, instalment amounts on upgrade. Up to 0.4 (HEAD) `realestate.sale.installment.amount` was the only amount. The new code adds empty `original_amount` and `adjustment_amount` columns and a stored `current_amount` compute that also writes `amount`, and the manifest was still 0.4, so no migration would run. Control upgrade (HEAD 0.4 with three instalments of 200,000, 300,000 and 500,000, upgraded with the reviewed code): all three became 0.00. Fix: version 0.5 and `migrations/0.5/pre-migrate.py`, which creates the three columns before the ORM sees the table and fills only empty values (`original_amount = amount`, `adjustment_amount = 0`, `current_amount = original + adjustment`), so nothing is recomputed. Same upgrade with the fix: 18.0.0.5, amounts, originals and current amounts all equal to the 0.4 amounts, no errors, nothing pending. The review databases were installed from the new code and already carry the columns (6 instalments, 1,000,000 original, 1,200,000 current); the migration leaves them as they are.
- P1, price-change amendments wrote `sale_price` only; the instalments kept their amounts, so a 1,000,000 contract raised to 1,200,000 still billed 1,000,000. New `_apply_price_change` rebalances the schedule through the same helper as the unit swap (`_rebalance_schedule`): what is invoiced or paid is kept, the open instalments are scaled through `correction` adjustments with the rounding remainder on the last one, and when nothing open is left an increase becomes one balancing instalment due today.
- P1, unit swaps below what is already billed. Clamping the open balance to zero left the buyer owing more than the new price. `_rebalance_schedule` now refuses a total below what is invoiced or paid ("Credit the excess in Accounting first"), for swaps and price changes alike. Swaps within the open balance are unchanged. A contract with no live instalments (V1 billed the whole price at once) gets no balancing instalment.
- P2, amendments that changed nothing. `_apply_payload` now refuses early-settlement and cancellation amendments, which only their contract wizards can carry out (the wizards never called it), and the form no longer shows Apply for them. Schedule and payment-plan changes restructure the open schedule under the new plan (`_apply_restructure`, as the Restructure wizard does) instead of only relabelling the plan. A swap, transfer, price or plan change missing its target is refused instead of being marked applied. Fee changes and property additions or removals need attached instalment adjustments. Term changes and "other" still apply as records.
- Tests: `real_estate_developer/tests/test_codex_findings_amendments.py` (17): the migration on a table shaped like 0.4, on an adjusted table and on a fresh install; price increases, decreases, a balance after full invoicing, a price below what is billed and a missing price; the refused and routed amendment types; a swap below and within the billed amount. Developer suite 276/276 (1 skipped; the two ERROR lines are the unique-constraint tests' expected messages); Treasury (which overrides the swap) 325/325.

**Found and fixed: adding a rent escalation on the lease form crashed** (reported from the review server, 14 Sep 2026)
- `TypeError: '<' not supported between instances of 'NewId' and 'NewId'` in `rent_escalation.py` `_compute_amounts`. The rent chain sorted a lease's rules by `(effective_date or today, sequence, id)`. On the form every line is an unsaved record, and a row just added has no date yet, so it tied with a rule effective today and the sort compared two NewIds. No existing test built the lease through its form.
- The same pattern crashed the developer price book form: `price_book.py` `_matching_premiums` sorted the book's rules by `(sequence, id)` while computing a line's price, so adding premium rules on the form raised the same error.
- Fix: sort keys use `_origin.id or 0` (the saved id, or 0 for an unsaved line; Python's sort is stable), and an escalation still missing its date sorts last instead of at "today". Also applied to `payment_plan.py` `_generate_schedule` and the three legacy increment-rule sorts in `contract_increment_rule.py`. The one remaining id-based sort (`commercial_approval._find_rule`) only sorts records it has just searched, so it cannot see unsaved records.
- Tests that drive the real forms with `odoo.tests.Form` (the same onchange calls the web client makes): `atmta_real_estate/tests/test_form_cycle.py` (10) covers escalations on a new lease, on a saved lease, and the undated-row case that reproduced the crash; charges, incentives and parties added on the lease form; a several-unit lease built on the form; and the amendment, renewal, termination, move-in, move-out and deposit forms. `real_estate_developer/tests/test_price_book_form.py` (2) adds premium rules on a new and a saved price book. On the unfixed code the undated-row escalation test and both price-book tests failed with the reported error; the other nine form tests passed.

**Found and fixed: forms that a Leasing Agent could not open**
- Found by opening each form as a Leasing Agent, reading every field with the specification the web client sends: first on the review database (read-only, rolled back), then in new tests on records with real invoices, reservations, listings and broker agreements. Rental roles deliberately grant no accounting, development-sales, brokerage or handover rights, but these forms showed data from those models with no `groups` restriction, so the web client read it and the whole form failed with an access error.
  - Rental lease form: the Invoices button (`invoice_count` over `account.move`). Every lease failed to open for an agent.
  - Rental termination form: `credit_note_ids` (`account.move`).
  - Unit form, from `real_estate_developer`: Reservations and Sale Contracts buttons and the Development tab (`realestate.unit.reservation`, `realestate.sale.contract`).
  - Unit form, from `real_estate_brokerage`: Listings and Sales buttons and the Sales tab (`realestate.listing`, `realestate.transaction`).
  - Unit form, from `real_estate_handover`: the Handovers button (`realestate.handover`).
  - Contact form, from `real_estate_brokerage`: the Broker page (`realestate.broker.agreement`), which is hidden for non-brokers but was still read.
- Fix: each element carries the group that may read its model: `account.group_account_invoice,account.group_account_readonly` for the invoice and credit-note elements; `real_estate_developer.group_dev_readonly`; `real_estate_brokerage.group_realestate_sales_readonly`; `real_estate_handover.group_handover_user`. Access rules and record rules are unchanged; users with those groups still see everything.
- Tests (each failed on the unfixed views with the errors above): `atmta_real_estate/tests/test_agent_reads_forms.py` (lease, obligation, deposit, renewal, amendment, move-in, settled termination, unit, tenant); `real_estate_developer/tests/test_rental_agent_reads_unit.py` (a unit with a sale contract and one with a reservation, plus a Developer read-only user who still sees them); `real_estate_brokerage/tests/test_rental_agent_reads_unit.py` (a listed unit and a broker contact, plus a Brokerage reader who still sees them). Handover has no tests; it is covered by the same field-by-field read on a full install.
- Also added: `atmta_real_estate/tests/test_forms_open.py` (every Rental screen opens a blank form, as the default user and as a Leasing Agent), `test_lease_form_browser.py` with `static/tests/tours/rental_escalation_tour.js` (Chrome: "Add a line" under Rent Escalations on a lease with an escalation effective today), and `real_estate_developer/tests/test_developer_forms_open.py` (every Developer screen opens a blank form, as a Developer Manager and as a Sales Agent).

**Found by the new form tests, and fixed**
- *The escalation browser tour could not open a lease as the administrator.* On a fresh install no Rental group included `base.user_admin` or `base.user_root`, so the administrator had no Rental role: the Rental app was hidden and every lease raised "You are not allowed to access 'Lease' records" until someone assigned a role. Existing databases were not affected (migration 0.8 gave admin a role from the legacy groups, as on the review database). Fix (`AR/security/leasing_groups.xml`): Rental Manager includes root and admin, as Odoo's own applications do for their manager group. The file is `noupdate`, so only new installs change.
- *Developer users could not open a phase.* `real_estate_developer` shows a phase's units and unit counters (`phase_units.py`) and its phase action opens them, but no Developer role could read `realestate.property`; a Developer Manager or Sales Agent without a Rental role got an access error on every phase. Brokerage grants its read-only group read access to units for the same reason. Fix (`real_estate_developer/security/ir.model.access.csv`): `access_realestate_property_dev_readonly`, read only, for `group_dev_readonly` (implied by every Developer role). Record rules on units still apply. Caught by `test_developer_forms_open.py`.
- Property and Rental Managers: a field-by-field read of the move-in, move-out, unit turn, meter, maintenance request, deposit (after a receipt and a refund), settled termination, lease and invoiced obligation forms found nothing they cannot read (`AR/tests/test_managers_read_forms.py`, 2/2).

**Sweeps for bugs away from forms** (user request, 14 Sep 2026: "continue and fix any other bugs you find")
- *Broken references:* every `atmta_real_estate.<id>` named in Python, XML, JavaScript or CSV anywhere in the repository resolves. The 12 names without a record are dashboard and map template names, system-parameter keys, the removed cron IDs that tests assert are gone, and the outdated `action_payment_plan` mention in two manifest descriptions.
- *Actions sweep* (`AR/tests/test_actions_sweep.py`, 3/3): every report Rental defines renders over a matching record; every server action Rental binds to a model runs with that record active; every scheduled job Rental defines runs. The records are a billed lease with an invoiced obligation, a deposit, a move-in, a renewal, an amendment and a termination. Nothing failed.
- *Screen sweep* (`AR/tests/test_screen_sweep.py`): for every Rental menu screen each of administrator, Leasing Agent, Property Manager and Rental Manager can see, it loads the views, reads the first list page with the list's own columns, groups the kanban by its default, runs the pivot and graph groupings and measures, searches the calendar's date range, and applies every search filter, group-by, searchable field and default filter. Its first runs flagged the Move-In and Move-Out date filters; those domains call `.to_utc()`, which exists only in the web client's domain evaluator, so the test now evaluates them with a client-style `datetime` module. No finding pointed at a real defect.

**Found and fixed: meter readings added on the meter form saved the wrong previous reading**
- A reading's previous value is a stored compute that searches for the meter's reading before it. On the meter form the new reading's meter is the form's own (unsaved) record, so the search by `meter_id.id` matched nothing and fell back to the opening reading. The list sends that value on save and it was stored, so the reading kept a previous value of 0: consumption became the whole meter value (140 instead of 40 in the test), which overstates every charge billed on meter consumption. Readings added on the move-in and move-out forms were not affected, because there the user picks an existing meter.
- Found by `AR/tests/test_form_lines.py`, which adds rows through the real forms: readings on a new and a saved meter, readings on a move-in, readings and deductions on a move-out, and a charge on a billing obligation. On the unfixed code only the saved-meter reading failed (`0.0 != 100.0`); nothing crashed.
- Fix (`AR/models/property_meter.py` `_compute_previous_reading`): the search uses the saved meter and reading ids (`_origin`); a meter not yet saved has no readings to follow, so its opening reading is used; a reading with no date yet is compared with the user's date rather than UTC's.
- Neither review database (`atmta_w26_review`, `atmta_w26_fresh`) has any meter readings, so no stored value needed repair.

**The whole rental cycle in a real browser** (user request, 14 Sep 2026: "test all rental cycle"; `AR/tests/test_full_cycle_browser.py`, `AR/static/tests/tours/rental_full_cycle_tours.js`, 1/1)
- Every step is clicked in Chrome by the role that owns it, and the server is checked after each of the 13 tours:
  - Lease A: a Leasing Agent picks the tenant and unit on the lease form, types the rent and submits (pending approval, dates and rent as entered); a Rental Manager approves (pending signature); the agent marks it signed; a Property Manager activates it and completes the move-in with the tenant's acknowledgement; the billing user generates the schedule and invoices what is due (posted invoices); the agent starts and proposes a renewal seeded from the lease, the manager approves the terms, the agent records acceptance and the manager creates the renewal lease (starting the day after Lease A ends, linked to it).
  - Lease B: a Property Manager starts the termination from the lease and records notice (lease on notice); a Rental Manager approves and settles it, confirming the settlement dialog; the Property Manager schedules the move-out, inspects and completes it (a unit turn opens), then completes the termination (lease closed).
- No application defect was found. The first three runs failed on the tour script itself: the Settle button asks for confirmation (`confirm=`), which the tour first did not answer; and waiting for "no disabled button in the header" never succeeded because the status bar's stage arrows are disabled buttons, so the wait is now limited to the action buttons.

**Found in the browser and fixed: money buttons errored for Rental roles without accounting rights**
- The money side of a lease in Chrome (`AR/tests/test_money_cycle_browser.py`, `AR/static/tests/tours/rental_money_tours.js`): a Leasing Agent creates and requests a security deposit from the lease; a user with Invoicing rights registers its receipt and refunds it; a Rental Manager amends the rent through the gear menu's Amend Lease, applying it with the confirmation a user sees.
- A Rental Manager without Odoo's Invoicing group, clicking Register Receipt, got "You are not allowed to create 'Payments' (account.payment) records". Rental roles grant no accounting rights by design (`AR/security/leasing_groups.xml`), and no posting method elevates them, yet every button that posts an invoice, credit note or payment was shown to such users: the lease's Invoice Due and Invoice Whole Term, the deposit's Register Receipt, Refund, Forfeit and Apply to Arrears, the termination's Settle, and the utility line's Confirm and Create Bill and Register Payment. Earlier tests ran those steps as a superuser or administrator.
- Decision (14 Sep 2026): keep the design and hide these buttons from users without Invoicing rights. `groups` means "any of", so it cannot require a Rental role *and* Invoicing; `AR/models/accounting_access.py` adds `can_post_accounting` (true when the user holds `account.group_account_invoice`, recomputed per user) to lease, deposit, termination and utility line, and each money button's `invisible` adds `or not can_post_accounting`. Access rules and server-side role checks are unchanged: such a user calling the method directly is still refused.
- Consequence: a Rental Manager without Invoicing rights no longer sees Settle on a termination; a manager with Invoicing rights settles it. The full-cycle browser test now settles as that user.
- Tests: `AR/tests/test_money_buttons.py` (every money button carries the condition on its form; the flag is false without Invoicing and true with it; the server still refuses a manager without Invoicing rights and accepts one with them).
- Earlier failures of the money browser test came from the tour script, not the application: New Security Deposit opens its form in a dialog, which closes by itself once Request has run.

**Found in the browser and fixed: Rent Roll crashed for everyone once a unit had a building**
- Found by a browser crawl (`AR/tests/test_browser_crawl.py`): for each of Leasing Agent, Property Manager and Rental Manager with Invoicing, Chrome opens every Rental menu screen the role can see, switches through each view, opens the first record and clicks its tabs; any client error fails the test. The agent's 12 screens were clean. Both manager roles crashed on Rent Roll: "OwlError ... Caused by: TypeError: this.child.mount is not a function", after which the page was destroyed and the crawl timed out. A scratch reproduction opening only Rent Roll as a Property Manager crashed the same way, so it was not caused by the screen before it.
- Cause: `realestate.rent.roll` is a SQL view, and its compound, building and floor names were read straight from `product_template.name`. That column is translatable and stored as JSON, so the Char fields held `{"en_US": "RH — Tower A"}` (seen on `atmta_w26_review`). Python accepted the object, so the server-side screen sweep passed, but the list renderer cannot display an object in a cell.
- Fix (`AR/models/rent_roll.py`): the names are read in the reader's language, falling back to English, as plain text (`COALESCE(name->>lang, name->>'en_US')`). The view query is already built per read (`_table_query`), so each user gets their own language.
- Tests: `AR/tests/test_rent_roll_names.py` (a unit under a floor, building and compound: the names come back as text, follow an Arabic translation, fall back to English for an untranslated language, and group as text). After the fix: the crawl passes for all three roles (Leasing Agent 12 screens, Property Manager 21, Rental Manager with Invoicing 23, no client error), and the scratch reproduction opens Rent Roll's list, pivot and graph views, alone and after the Move-Ins calendar, without an error.

**Found and fixed: two user-flow tests failed every night for about two hours** (test fixture, not application code)
- The Rental suite run at 22:10 UTC failed `test_flow_1_find_a_unit_lease_it_and_take_it_live` (the unit was still available after its lease went live) and `test_flow_2_bill_an_active_lease_and_invoice_it_once` (0 invoices for 1 due obligation). Both passed on every earlier run.
- Cause: `LeaseCase.today` is the fixture user's date, and the superuser is on Europe/Brussels (UTC+2), so from 22:00 UTC it is already tomorrow. The flow tests' role users had no timezone, so the code under test dated things in UTC. A lease starting "today" had not started for them, and its first obligation was not yet due. This is the same clock split `LeaseCase` already documents; production reads each user's own timezone, as intended.
- Fix (`AR/tests/test_user_flows.py`): the role users take the fixture user's timezone. No assertion changed. Rental suite afterwards, started at 22:18 UTC inside the same window: **477/477** (2 skipped), including the crawl (3) and the Rent Roll name tests (4), clean log (the 6 "Access Denied" INFO lines are each inside an `assertRaises`).

**Sweep for more bugs across the suite** (user request, 15 Sep 2026: "continue and fix any other bugs you find")
- *Browser crawl of every app* on a copy of `atmta_w26_review` (scratch module, not part of the suite): for each app root, a user with the administrator's groups opens every screen, switches views, opens the first record and clicks its tabs. Rental (25 screens), Property Operations (4), Developer (4), Brokerage (4), both Treasury roots (15 each), Handover (1), Investment (1) and Customer Service (2) had no client error. Two screens crashed; both findings are below. The administrator holds no Developer, Brokerage, Construction or Procurement role, so a second pass runs as a user holding every suite role.
- *Static sweeps* for the defect classes already found in Rental: chatter HTML built without `Markup` (none left), id-based sorts that can see unsaved form rows (two, below; the Treasury ones sort saved records only), SQL views reading translated names as JSON (Rent Roll only, fixed above), and `fields.Date.today()` where the user's date is meant (below).

**Found and fixed: two more form crashes on unsaved rows** (Rental)
- Meter form: "Add a line" twice gives two readings both dated today; the meter's reading count, current reading and last reading date re-sorted them by `(date, id)` and compared unsaved ids: `TypeError: '<' not supported between instances of 'NewId' and 'NewId'`. Lease form: two escalations typed with the same date (before one is corrected) crashed the same way in `_rent_on`, which the form's Current Rent uses. The earlier escalation fix covered `_compute_amounts` only.
- Fix: both sorts break ties by the saved id (`_origin.id or 0`); an undated escalation sorts last in `_rent_on`, as in the rent chain.
- Tests: `AR/tests/test_form_ties.py` (2), both failing with the reported `TypeError` on the unfixed code.

**Found and fixed: Brokerage days on market stopped counting**
- `realestate.listing.days_on_market` is stored (the list, kanban and dashboard average read it) and computed from today's date, but nothing refreshed it, so a listing nobody edited kept the count from its last save.
- Fix (`real_estate_brokerage/models/listing.py`): the daily Expire Listings job also recomputes every listing still counting (draft, active or under offer, or closed without its end date). Closed listings keep their final count.
- Tests: `real_estate_brokerage/tests/test_days_on_market_refresh.py` (2); on the unfixed code the active listing read 0 instead of 21, and the closed-listing guard passed.

**Found and fixed: opening the Treasury cheque list wrote to the database**
- The stored `maturity_bucket` shared `_compute_maturity` with four unstored live figures that the cheque list shows, so reading the list recomputed the bucket and wrote it inside a read-only request: "cannot execute UPDATE in a read-only transaction", then Odoo ran the request again with write access (seen in the crawl on Treasury → Cheques).
- Fix (`real_estate_checks/models/check.py`): the bucket has its own compute (`_compute_maturity_bucket`), and the nightly maturity cron calls it. The live figures are unchanged.
- Tests: `real_estate_checks/tests/test_maturity_read_only.py` (2): reading the live figures queues no write and leaves the bucket for the cron; the cron still moves it.

**Found and fixed: Project Plans and Construction screens crashed for users without project access**
- Crawl: Project Plans → 3D Maquette and Construction → Control Tower crashed on opening ("The following error occurred in onWillStart: Odoo Server Error"; server: "You are not allowed to access 'Real Estate Development Project' records"). Both read development projects, which only Developer and Brokerage roles may read, and both root menus had no group. Construction User itself could not read projects either, so Control Tower also crashed for the users it is meant for, in the new Construction app too.
- Decisions (15 Sep 2026):
  - legacy Construction root: Construction User only (`real_estate_construction/views/menus.xml`);
  - Construction User: read-only access to development projects (`real_estate_construction/security/ir.model.access.csv`, as Developer and Brokerage read-only have; record rules still apply);
  - Project Plans: stays visible (Brokerage read-only does not imply Developer read-only, and no module depends on both Maquette and Brokerage), and the 3D Maquette screen says "The 3D maquette could not be loaded. You may not have access to development projects." instead of crashing, as the 2D plan already did (`real_estate_maquette/static/src/js/maquette_preview.js`, `xml/maquette_viewer.xml`).
- Tests: `real_estate_construction/tests/test_menu_access.py` (only Construction users see the app; they can read projects and cannot write, create or delete them); `real_estate_maquette/tests/test_preview_without_project_access.py` (Chrome: a user without project access opens 3D Maquette and sees the message).

**Findings from a code review of the working tree** (`/code-review`, 15 Sep 2026; each verified against the code and reproduced by a test before it was fixed)
- *Accountants could not open payment terms* (`real_estate_developer`). The working tree narrowed read access to schedule segments from every internal user to Developer read-only, but the module adds the segment list and its total to Odoo's own payment-term form with no group. An Invoicing user without a Developer role opening any payment term read them and got an access error. Fix (`views/account_payment_term_views.xml`): the Real Estate Schedule Builder block carries `groups="real_estate_developer.group_dev_readonly"`; the stricter access rule stays. Tests: `real_estate_developer/tests/test_payment_term_form_access.py` (an accountant's form has neither field and reads cleanly; a Developer user still sees and reads both). On the unfixed code the accountant test failed with the segment list on the form.
- *Anonymous API requests wrote a grant row per URL* (`real_estate_maquette`, used by `real_estate_api`). `grant_for_public_project` always created a new `realestate.visual.grant`, and the public API calls it for every gated image and model URL it serialises, so one unauthenticated catalogue request for a project with a few hundred units wrote hundreds of rows, repeatable by anyone. Fix (`models/visual_access.py`): a live grant for the same project, kinds, source and origin with at least half its lifetime left is returned instead; revoked, deactivated and nearly expired grants are never reused. Tests: `real_estate_maquette/tests/test_visual_grant_reuse.py` (4); on the unfixed code the two reuse tests failed (every call minted a new grant) and the two guards (a revoked or nearly expired grant is replaced) passed.
- *A tenant's balance due added up currencies* (`atmta_real_estate`). `rental_balance_due` summed each invoiced obligation's outstanding amount in its lease's currency and showed the total in the company currency. Fix (`models/res_partner.py`): each amount is converted at today's rate first. Tests: `AR/tests/test_tenant_balance_currency.py`; on the unfixed code a tenant with a 575 local and a 345 foreign balance (rate 0.5) showed 920 instead of 1,265.

**Crawl as a user holding every suite role, after the fixes** (a copy of `atmta_w26_review` brought to the current code, nothing pending): every app root opened every screen it shows with no client error, no failed request and no read-only-transaction retry. Rental 25, Property Operations 10, Development & Sales 33, Developer 27, Brokerage 16 and 18, Treasury 18 and 18, Handover 5, Investment 3, Customer Service 2, Project Plans 8, Public API 1, Executive 7, Construction 41 and 40, Procurement 32: 304 screens, 17/17.

**Suites after this sweep** (fresh databases, current tree): Rental **480/480** (2 skipped), clean log; Developer **278/278** (1 skipped; the two ERROR lines are the unique-constraint tests' expected messages); Brokerage **356/356**; Treasury **327/327**; Maquette with Plan, Brokerage and Portal **296/296** (its one "asset error" line is the new 3D test's own message, which contains "could not be loaded"); Portal **7/7**; Construction menu and project access **2/2** (the full Construction suite was not rerun; it carries the known stale-ID failures).

**Dates taken from UTC where the user's date is meant** (Rental): a utility vendor bill and a maintenance charge's due date used `fields.Date.today()`, so for a few hours after local midnight east of UTC they were dated the previous day. Both now use the user's date (`contract_utility_line.py`, `maintenance_request.py`). Other `fields.Date.today()` uses in Brokerage, Handover and the Developer dashboard only shift a count or a default by a day in that window and were not changed.

**PDF printing** (review database, read-only, rolled back): wkhtmltopdf 0.12.6.1 is installed and Odoo reports it ready. Rental registers two PDF reports; both render as PDFs over a real record: the lease document (`report_realestate_contract_template`, 64,892 bytes) and the unit status report (`report_unit_status_template`, 30,852 bytes). The actions sweep already renders every Rental report as HTML in the test suite.

**Suites after these fixes** (fresh databases, current tree): Rental **453/453** (2 skipped), including the escalation browser tour, blank-form, agent-read and manager-read tests, with a clean log and the administrator in Rental Manager; **460/460** (2 skipped) once the screen sweep (4) and actions sweep (3) were added, still with a clean log; **465/465** (2 skipped) with the form-line tests (5) and the meter-reading fix, clean log, and the review server restarted on that code; **470/470** (2 skipped) with the full-cycle and money browser tests (16 browser tours) and the money-button fix and its tests, clean log; Developer **259/259** (1 skipped; the two ERROR lines are the unique-constraint tests' expected messages); Brokerage **354/354**; Treasury **325/325** (its one ERROR line is Odoo's read-only-transaction retry message, present on every run).

**Review database updated again** (`atmta_w26_review`, same versions): review server stopped; `-u atmta_real_estate,real_estate_developer,real_estate_brokerage,real_estate_handover`, no errors, nothing left pending. A Leasing Agent reading every field of the lease, billing obligation, termination, unit and tenant forms now hits no access error (12 failures before the fixes, 0 after). Server restarted on the fixed code: port 8091, login page 200, no errors in its log.

**Review database after the fixes** (`atmta_w26_review`, decision 14 Sep 2026: update Rental there): review server stopped; `-u atmta_real_estate` at the same version 0.10, so no migrations ran and data and views were reloaded; no errors, no module left pending. Occupancy and Reporting went from Property Manager + Rental User to Property Manager only. Per-role Rental menus as designed, no duplicate actions; no company-less unit, no escaped lease note; all 22 dashboard tiles equal their drilldowns (Available to Lease 20, Occupied Units 1). The review server was restarted on the fixed code: port 8091, login page 200, database list `atmta_w26_review` only, no errors in its log.

**Review database after the second Codex fixes and the Rent Roll fix** (`atmta_w26_review`, decision 15 Sep 2026: update and restart): review server stopped; `-u atmta_real_estate,real_estate_developer`, Developer 18.0.0.4 → 18.0.0.5, Rental stays 0.10, no errors, nothing pending. The 0.5 migration found nothing to fill (0 original, 0 current amounts); the 6 instalments keep their amounts (1,200,000 in total), and the one instalment with an empty adjustment now reads 0, as the ORM already showed it. Rent Roll's building name is text again (`jsonb` before, `character varying` after). Server restarted on the new code, port 8091.

**Review database after the bug sweep** (`atmta_w26_review`, decision 15 Sep 2026: update and restart once the suites and crawl pass): review server stopped; `-u atmta_real_estate,real_estate_developer,real_estate_brokerage,real_estate_checks,real_estate_construction,real_estate_maquette`, no errors, nothing pending. Instalment amounts unchanged. The legacy Construction root now carries Construction User, Construction User reads development projects, the payment-term schedule builder carries Developer read-only, and `maturity_bucket` has its own compute. One operational slip: the script's stop step matched processes by command line (`pgrep -f`) and caught the shell running it, which ended the session's command but not the update; the update ran to completion and was then verified as above. The stop step now uses the exact server PID. Server restarted on the new code, port 8091.

**Arabic crawl** (user request, 15 Sep 2026: "continue and fix any other bugs you find"): the same browser crawl, as a user holding every suite role, in Arabic (`ar_001`, right to left), on a copy of `atmta_w26_review`. All 17 app roots and 304 screens opened, switched views, opened a record and clicked its tabs with no client error, no failed request and no read-only-transaction retry. No suite module ships an Arabic translation file, so the labels are Odoo's own Arabic terms plus English; the crawl checks that the screens work in Arabic, not that they are translated.

**Button sweep** (same request): on a copy of `atmta_w26_review`, a user holding every suite role and Accounting Manager pressed every `type="object"` button on the form view of every model a suite module defines (301 models), on up to three real records each, each call in its own savepoint rolled back. 284 calls: 205 ran, 79 were refused with a business message (UserError, ValidationError, AccessError), none crashed. The five buttons reported as naming a method the model lacks are all inside embedded line lists (lease party and incentive lines, requisition approval steps, sale contract instalments), where the method belongs to the line's model and exists. A first run that also swept core models the suite only extends was discarded: CRM's IAP enrichment button manages its own savepoints and broke the transaction for every later call.

**Found and fixed: overdue flags and day counts that never moved in Construction and Procurement** (decision 15 Sep 2026: fix with a daily refresh)
- Found by a static scan for stored computes that read today's date without a refresher. These are stored so the registers can filter, group and sort on them, but a stored compute only reruns when its record changes, so they froze at the last edit: RFIs, submittals and transmittals (overdue days, overdue), issues (overdue), risks (overdue actions), quality observations and NCRs (overdue), notices (status: due soon, overdue), delay events (duration while ongoing), milestones (delayed once past the expected end date) and, in Procurement Control, a requisition's days waiting, an approval step's waiting days and a reservation's expiry anomaly. The Rental, Treasury, Developer, Handover, Customer Service and Brokerage equivalents already had refreshers; change events needed none (their only stored value does not depend on the date).
- Fix: each of `atmta_construction_documents`, `_quality`, `_cost`, `_claims`, `_site` and `atmta_procurement_control` gets `models/date_refresh.py` (a refresher that recomputes only open records whose date has passed) and a daily cron `cron_refresh_dates` in `data/date_refresh_cron.xml`. No field or data-model change.
- Tests: `real_estate_construction/tests/test_date_refresh.py` (11) and `real_estate_procurement/tests/test_date_refresh_control.py` (2). Each moves a date into the past in the database only, shows the stored value has not moved, then runs the refresh. On the unfixed code every staleness check held and the tests stopped at the missing refresher (12 errors); the milestone test's own fixture assumed "in progress" for a new milestone and was corrected (a new milestone is "not started", which also becomes delayed).
- With the fix: the 13 tests pass and all six `cron_refresh_dates` jobs are installed. The full Construction and Procurement suites (Construction, Procurement and Procurement Control installed together; 1,182 tests) show 12 failures and 8 errors, exactly the 20 known stale-XML-ID failures of the baseline run: no new failure and none fixed. A first verification run did not load: the generated cron files had broken model references (the script that wrote them split each job name on its colon); they were rewritten and checked against each refresher's model name before the run above.

**Scheduled jobs, reports and wizards sweep** (same request): on a copy of `atmta_w26_review`, every scheduled job a suite module defines was run (35, each in its own transaction rolled back), every suite report rendered as HTML on real records (6; 2 more had no records to render), and every suite wizard opened with its defaults and form view (44). No failure. The suite defines no mail templates.

**Web route sweep** (same request): a scratch test on a copy of `atmta_w26_review` requested every GET route a suite module defines (76), as an anonymous visitor, a portal user and an employee holding every suite role, filling integer and record ids with real ids: 147 requests, no server error. 81 requests were skipped because the route needs a free-text value (external references, tokens, file names). Server errors are what this finds; it does not judge whether a 200 should have been a 404, which is what the review below did.

**Found and fixed: the public portal served any building and any floor, with internal prices**
- Found by reviewing every `auth='public'` route in the suite. `/projects/portal/building/<id>.json` (`real_estate_portal/controllers/public.py`) checked only that the record was a building, and `/projects/portal/floor/<id>/units.json` only that the floor existed. Both read under `sudo()`, so an anonymous visitor could walk the ids and read every building (name, code, city, district, floor list with available, reserved and sold counts) and every floor's units of every company, published or not; the floor route also returned each unit's internal base price and raw status, which `units.json` had already been changed to withhold (`get_maquette_units_data(audience='public')`). The other portal JSON routes (regions, property details, images) already required a published project.
- Fix: both routes answer 404 unless the building's project is published (`_public_project`: `visual_public_enabled` and `visual_is_live`), and the floor route builds its units with `realestate.visual.commercial.unit_payload(audience='public')`: the public price (0 for a unit not on the market) and the visual state, without the commercial status or the reason a unit is unavailable. The response keys are unchanged.
- Test: `real_estate_portal/tests/test_public_building_floor.py` (anonymous requests: an unpublished project's building and floor are 404; once published, the building is served and an unreleased unit's price reads 0, not its base price). Run against a scratch copy of the module with the two routes restored, both tests failed on the leak itself (an unpublished project's building answered 200 instead of 404; the floor route returned the unit's base price, 1,000,000, instead of 0); with the fix both pass. Portal suite 9/9, Maquette 296/296. A first proof run had failed on both copies alike in the test's own set-up (a floor record is identified by a unit, not a floor number) and was discarded.
- Reviewed and not changed: the public API's download routes all require a valid API key, the top-level ones also the downloads group, and the by-reference ones serve only documents of the resolved partner. API-created partners, however, share one `external_ref` namespace that is not tied to the key that created them: with more than one integration key, a key holder could look up another integration's partners by reference. That matches the documented single-site design; it is a risk to decide on, not a defect.

**Map on the Rental Overview** (user question 15 Sep 2026: "where is the map in the rental dashboard?"; decision: add a map card)
- Before: the map existed only as its own screen, Rental → Units → Map (`realestate.properties_map`). The first-generation dashboard had a Properties Map card, but it was switched off (`t-if="false"`, "Map hidden for now") and that dashboard was never bundled; the redesigned dashboard had no map.
- Now: the Overview has a Map section between Portfolio Health and Trends. `realestate.rental.dashboard.get_map` sends the leasable units that have coordinates (read as the user, so record rules and company isolation apply; units without a company are left out, as from every tile), with their status and how many units have no coordinates. `DashboardMap` (`static/src/js/dashboard/dashboard_map.js`) draws them clustered and coloured as on Units → Map, with a legend and counts; a marker's popup opens the unit (`action_open_unit`, which checks read access) and "Open full map" opens Units → Map (`action_open_map`). It loads on its own after the tiles, so the numbers never wait for it, and a failure shows a message without affecting the rest of the dashboard. Popups are built from DOM nodes, so unit names are never interpreted as HTML.
- Street background (decision 15 Sep 2026: shared online tiles): the card uses the same tile server as Units → Map, now defined once in `static/src/js/map_tiles.js`. Without internet access both maps still show their units, counts and legend, on a blank background. The dashboard's no-CDN test flagged the card's tile address; rather than move the address out of the scanned folder, the test now also scans `map_tiles.js` and allows exactly that one host there (any other CDN reference still fails).
- Found and fixed on Units → Map: marker popups were built as an HTML string in which the property type and country names were not escaped at all (the unit name, code and city escaped only `<`). Users who may edit property types (Rental User and above) could put markup in a type name and have it run in every map user's browser. Popups are now built from DOM nodes (`buildPropertyPopup`), with every value set as text. Test: `AR/static/tests/properties_map_popup.test.js` (hostile type, country, name and code render as text, no element or script is created; Open still calls back with the unit).
- Also fixed on Units → Map: the Streets/Satellite switch drew its icon from Leaflet's `images/layers.png`, which the bundled Leaflet does not ship, so the button was blank and every map load requested a missing file (seen as a 404 in the browser crawl). The icon is now drawn with Font Awesome.
- Tests: `AR/tests/test_dashboard_map.py` (located leasable units only, another company's units left out, a Leasing Agent opens a unit from the card, the full map opens, a missing or forbidden unit is refused); the HOOT suite gains the section order with the map and four map tests (explanation when nothing is located, counts by status, a failed map load leaves the tiles, Open full map); the dashboard tour checks that the card draws the located unit. Rental suite with these changes: 485 tests, 484 passed on the full run; the one failure was the HOOT suite, where the new popup test ran without loaded translations ("translation error"). With that test's setup corrected, the HOOT suite passes on its own, 30/30 (dashboard 28, popup 2). The dashboard tour, the new server tests and the updated asset tests passed in the full run.

**Review database after the date refreshers** (`atmta_w26_review`, decision 15 Sep 2026: update and restart, running each refresh once): review server stopped by its exact PID; `-u` on the five Construction capability modules and Procurement Control, no errors, nothing pending; all six daily jobs active. Each refresh ran once and found nothing to correct (the review data has no open Construction or Procurement records past a date). The map card was already live there (Rental updated earlier the same day, 24 located units). Server restarted on port 8091.

**Review database after the portal fix** (`atmta_w26_review`, decision 15 Sep 2026: update and restart once proven): review server stopped by its exact PID; `-u real_estate_portal`, no errors, nothing pending. Server restarted on port 8091.

**Making the apps installable on their own** (user request, 16 Sep 2026: "we need to make all these modules independent as much as possible"; decisions: do the unblockers first, and free Developer, Brokerage and Construction together)

What the dependencies on Rental actually are, measured rather than assumed (references counted across Python, XML, CSV and JS, excluding tests):
- `realestate.account.tools` — an abstract model with two methods (post draft moves; register and reconcile a payment). Called by Developer (3 files), Brokerage (3), Construction (1) and Construction Certification (3). This was the single largest reason those apps depended on the Rental app.
- The base property views (`view_property_form`, `view_property_list`) live in Rental and are inherited by Brokerage, Developer, Handover, Maquette and Plan. Of the fields they show, 47 belong to `atmta_property_core` and 2 to Rental; of the buttons, 2 are core and 3 are Rental's.
- One shared stock-location tree (`stock_location_re_root` and three children), defined in Rental's data and needed by Developer to hang each project's location under.
- Construction's dependency on Developer is two view identifiers (`view_project_form`, `view_phase_form`); the models it extends (`realestate.project`, `realestate.phase`) already belong to `atmta_project_core`. Of the fields those views show, 27 and 9 are core against 5 and 3 Developer's.
- Procurement references nothing at all from Rental or Developer (one stale manifest comment and old migration scripts aside); Customer Service uses only `realestate.property`.

Planned sequence, each step with its own migration and tests:
1. `atmta_account_tools` — a new module owning the accounting helper, the smallest thing that may depend on `account` (`atmta_base` documents why the floor must not). Rental, Developer, Brokerage, Construction and Certification depend on it; a `pre_init_hook` hands the model identifier over before the module's own definition loads, so an upgraded database keeps one identifier and no duplicate.
2. `atmta_property_stock` — a new module owning the four locations, adopted the same way so existing locations (and the quants in them) are reused, not duplicated. Rental keeps the lifecycle that moves quants; Developer keeps per-project locations.
3. The base property views move into `atmta_property_core`, with Rental keeping an inherited extension for its 3 buttons, `next_available_date` and the map field (the `property_map` widget is Rental's own asset).
4. The base project and phase views move into `atmta_project_core`, with Developer keeping its 5 fields, 2 stat buttons and the plot-boundary page (the `boundary_picker` widget is Developer's).
5. Dependencies dropped: Developer, Brokerage and Construction stop depending on Rental (Construction on Developer); Procurement drops both; Customer Service moves to `atmta_property_core`. Each app gains its own Properties or Projects menu and action, since today those come from Rental and the Development app, and the core modules stay menu-free by design.

Measured before step 5 starts (a sweep of every cross-module `env.ref` in the suite, 16 Sep 2026). Dropping those dependencies breaks a handful of tests that reach for a group belonging to the module being dropped, without guarding for its absence:
- `real_estate_procurement/tests/test_m4_eligibility.py` (lines 389, 437) asks for `real_estate_developer.group_dev_readonly`; `test_m6_browser.py:45` and `test_m7_browser.py:39` ask for `atmta_real_estate.group_realestate_user`. Procurement is meant to drop both modules.
- `real_estate_construction/tests/test_m10_freeze_invariants.py:208` and `test_m9_browser.py:75` ask for `atmta_real_estate.group_realestate_user`. Construction is meant to drop Rental.
- The guarded pattern already exists in the same files -- `test_m9_browser.py:76` and `test_m10_multicompany.py:70` use `raise_if_not_found=False` for the developer group -- so the fix is to follow what those lines already do, not to invent anything.
- Separately, `real_estate_maquette` depends on neither Brokerage nor Portal, yet five of its test files build `crm.lead` records and one asks for `real_estate_brokerage.group_realestate_sales_agent` by name. Those tests only pass when Brokerage and Portal are installed alongside; a run without them reports 38 failures that look like a regression and are not one.
No product code depends on a group from a module it does not declare; every one of these is in a test.

Step 1 done — **the accounting helper leaves Rental** (16 Sep 2026)
- `atmta_account_tools` (depends `base`, `account`) now owns `realestate.account.tools`, moved file-for-file out of `atmta_real_estate/models/`. Rental, Developer, Brokerage, Construction and Certification depend on it; Rental no longer imports the model.
- An installed database keeps one identifier: the `pre_init_hook` re-points `ir_model_data` row `model_realestate_account_tools` from `atmta_real_estate` to `atmta_account_tools` before the new module's own definition loads, and only if the new module does not already have one. `atmta_base`'s description records why the floor still must not host it (it would drag `account` under every ATMTA module).
- Proof: on a database installed at the old layout, the upgrade moved the identifier (before: `atmta_real_estate`, after: `atmta_account_tools`), the module reports installed, the model is still registered once, nothing is left pending, and the log has no error. Fresh installs afterwards: Rental 485/485, Brokerage 356/356.

Step 2 done — **the stock-location tree leaves Rental** (16 Sep 2026)
- `atmta_property_stock` (depends `base`, `stock`) owns the four locations a unit's quant moves between: the `Real Estate` root under warehouse stock, `Unassigned Units`, `Reserved Units` and `Sold Units`. They were Rental data records, which is why Developer needed the whole leasing application just to find the root to hang each project's location under. Rental keeps the lifecycle that relocates quants; Developer keeps its per-project locations.
- The new module's data file is byte-identical to the one Rental shipped, `noupdate="1"` included, and Rental's now-dead copy was deleted, so the four identifiers are declared exactly once in the suite. Five `env.ref` call sites moved (`atmta_real_estate/models/property_stock.py` ×4, `real_estate_developer/models/project_stock.py` ×1); a sweep found nothing else in the suite referring to them — no test, no demo record, no security rule.
- An installed database keeps its locations *and* what sits in them: a `pre_init_hook` hands the four identifiers over before the module's own data loads. Odoo runs that hook only when a module is newly installed (`loading.py`: `if new_install: ... getattr(py_module, pre_init)(env)`), which is what an upgrade of an existing database does, so the records are updated in place rather than a second tree being created beside the one holding the quants.
- Proof: a database installed at the old layout (the four identifiers owned by `atmta_real_estate`, the new module absent) was upgraded. The locations kept their ids — 34, 35, 36, 37 — their owner became `atmta_property_stock`, and the 24 quants in them (19 unassigned, 5 reserved) did not move. Exactly one `Real Estate` location exists, with its three children. No error, no invalid view.
- Rental's manifest no longer describes the module as "self-contained, no dependency on other real-estate modules": it now depends on `atmta_property_core`, `atmta_property_stock` and `atmta_account_tools`, which is the point of the exercise.
- The quant lifecycle itself had no test coverage anywhere in the suite: a sweep for `stock.quant` or `stock_location_re_` across every test file in every module found nothing, so a passing Rental suite said nothing about whether units still move between these locations. `atmta_real_estate/tests/test_unit_stock_locations.py` (6 tests) now covers it, resolving every expected location through `atmta_property_stock`: the four locations are owned by that module and hang off its root; a new unit holds one quant in Unassigned Units; reserving moves it to Reserved Units and leaves nothing behind; selling moves it to Sold Units; returning to available brings it home; a building holds no stock. All six pass.
- Rental suite after the move: 485 tests, one error, `TestMoneyCycleBrowser.test_deposit_and_amendment_in_the_browser`, which died connecting to Chrome (`Network.setCookie` timed out before the tour started) in a run that began with 172 MB of free memory. Re-run with memory available, together with the six new tests: 7 of 7 pass, with no browser timeout. The Rental suite is now 491 tests.

Step 3 done — **the base property views move to the module that owns the property** (16 Sep 2026)
- `view_property_form`, `view_property_list`, `view_property_search`, `view_property_kanban` and `view_property_hierarchy` now live in `atmta_property_core/views/property_views.xml`, with their local names unchanged. Brokerage, Developer, Handover, Maquette and Plan inherit from there instead of from the Rental app, and each now declares `atmta_property_core` rather than reaching it through Rental. Rental keeps `views/property_views_rental.xml` (the unit-stock button and the map, whose `property_map` widget is a Rental asset) and the `action_property_view` action, which menus and other modules reference by name.
- Corrected during the work: the two availability buttons and `next_available_date` were moved out of core on the assumption they were Rental's. `atmta_property_core/models/_base_property.py` defines all three and Rental only extends them, so they belong in the core view and were put back. Only two things are genuinely Rental's.
- `web_hierarchy` had to be added to Property Core's dependencies. Nothing in the field-ownership check predicted it: `view_property_hierarchy` is a `<hierarchy>` view, and without that module the view type does not exist and the module cannot load at all ("Invalid view type: 'hierarchy'", registry failed). Only installing the module on its own found it.
- An installed database keeps its records: `atmta_property_core` 18.0.1.0.0 → 18.0.1.1.0 with a pre-migration that hands the five identifiers over before Rental's data reloads. Without it, Rental's `_process_end` would delete identifiers it no longer declares and take the view records with them.
- Proofs. Property Core installed **alone**: 38 modules, `atmta_real_estate` absent, no error, no invalid view, and no Rental-only name (`property_map`, `action_view_unit_stock`) anywhere in the core screens. Upgrade on a database holding the old row state: the five identifiers came back to `atmta_property_core` keeping the same `ir_ui_view` ids (567–571 — records reused, not recreated), all five inheriting views still resolve to the form, the action still carries its four `view_ids`, and nothing is orphaned. Suites: Rental 485/485, Developer 278/278, Brokerage 356/356, Maquette + Plan 311/311; Handover has no test suite, so only its clean install is claimed.
- A first Maquette run reported 38 failures. They were mine, not the code's: the runner installed only Maquette and Plan, while five of Maquette's test files call `env['crm.lead']` with no guard and one asks for a Brokerage group by name, so they need Brokerage and Portal installed. No view failed to load in that run. With the four modules installed the same 311 tests pass.

Step 4 done — **the base project and phase views move to the module that owns them** (16 Sep 2026)
- `view_project_form`, `view_project_list`, `view_project_kanban`, `view_phase_form` and `view_phase_list` now live in `atmta_project_core/views/`, with their local names unchanged. Construction (project *and* phase), Procurement, Maquette and Plan's drill-down tab inherit from there instead of from the developer application. Developer keeps five extensions (`view_project_form_developer` and friends) carrying the unit counts, the stock navigator, the Inventory Summary page and the Plot Boundary page, whose `boundary_picker` widget is a Developer asset. Both actions stay in Developer: `atmta_executive_app` and `atmta_development_app` reference them by name.
- Plan's boundary overlay deliberately still inherits Developer's extension, because it injects the 2D master plan *inside* the Plot Boundary page. Caught before any install was spent: it still referenced `real_estate_developer.view_project_form`, an identifier Developer no longer declares and which the migration hands to Project Core, so it would have resolved to a form with no such page. A suite-wide sweep afterwards found no reference anywhere resolving to an undeclared identifier.
- `atmta_project_core` 18.0.1.0.0 → 18.0.1.1.0 with a pre-migration that hands the five identifiers over before Developer's data reloads — the same contract the Wave 11 procurement migrations use, and for the same reason: `_process_end` deletes only identifiers belonging to the module being loaded.
- Proofs. Project Core installed **alone**: 34 modules, `real_estate_developer` absent, no error, no invalid view, no Developer-only name in the core screens, and the three anchors other modules inherit (`header`, `button_box`, `notebook`) all present. Upgrade on a database holding the old row state: the five identifiers came back to `atmta_project_core` keeping the same `ir_ui_view` ids (445–449 — records reused, not recreated), Developer's five extensions exist and inherit them, and nothing is orphaned. Suites: Developer 278/278, Maquette + Plan 311/311, Construction 574/576, Procurement 579/597. Every one of those 20 failures appears verbatim in the 14 Sep pre-change baseline (the known stale-identifier set, plus the two Construction browser tours), so none is caused by this move; the two tours were also re-run alone on a quiet machine and failed the same way.
- Two false alarms, both in the verification rather than the code, recorded because they cost real time. A reported "leak" of the Plot Boundary page into the core form was SQL `LIKE` treating `_` as a single-character wildcard: `'%plot_boundary%'` matched the core form's help text "Auto-computed from plot boundary". `grep`, `position()` and `strpos()` agree the page exists only in Developer's extension, and the scripts now use `strpos()`. Separately, a Maquette run reporting 38 failures and a Procurement run reporting one were both caused by install lists that omitted a module the tests need (Brokerage/Portal for Maquette's `crm.lead` fixtures, Construction for one unguarded Procurement test); with the right modules installed both are clean.

**Review database after steps 1–4** (`atmta_w26_review`, decision 16 Sep 2026: update and restart; the server had been stopped by the system for low memory)
- Before touching it, the facts the four migrations must preserve were recorded: the ten view records (2458–2462, 2553–2557), the four locations (34–37) with their 24 quants and 26 across the whole tree, the accounting helper (model 789), 32 properties and 23 projects.
- The first `-u` failed and the registry did not load: "`//page[@name='plot_boundary']/field[@name='boundary_point_ids']` cannot be located in parent view", in `real_estate_plan/views/project_views.xml`. Plan's boundary overlay (view 2714) still had the project form (2555) as its parent in the database. Its XML had been re-pointed to Developer's extension, but on an upgrade that is written only when Plan's loader reaches the record, and the record before it in the same file is written first; writing it validates the whole tree under 2555, which still held the overlay looking for a page the project form no longer has. None of the rehearsals caught it: the project upgrade proof installed Developer without Plan, and the Maquette + Plan suite installs fresh, where the record is created with the right parent. It is the only moved view whose *numeric* parent changes; every other re-pointed view kept the same parent record, because the identifiers were re-owned rather than recreated. Nothing was lost in the failed attempt — ids, quants and counts were unchanged, and the failing module rolled back.
- Fix: `real_estate_plan` 0.5 → 0.6 with a pre-migration that re-parents the overlay to `real_estate_developer.view_project_form_developer` before Plan's data loads, and only while it still hangs off the project form. Developer is a dependency of Plan, so that extension exists by then.
- Second `-u`: no error, no invalid view, nothing pending. The migration logged the overlay moving from view 2555 to view 2866. Against the snapshot taken before the *first* attempt, every view id, location id and quant is where it was and no property or project is missing; the identifiers now belong to `atmta_property_core`, `atmta_project_core`, `atmta_property_stock` and `atmta_account_tools`, with one Real Estate root. Server restarted on port 8091; login page 200, no error in the log.

**Remaining risks after Phase 5**
- Codex adversarial review: pending due to usage limit. The stop-time review was attempted four times on 16 Sep 2026 and every run ended without a review ("You've hit your usage limit... try again at Sep 19th, 2026 5:22 PM"). Nothing in the accounting-helper, property-view or project-view work has been through Codex; the evidence recorded for those steps is from installs, upgrades and the test suites only.
- Upgrading a 0.4 database (`atmta_demo`) fails in `real_estate_procurement` 18.0.16.0.0 pre-migration (committed, outside Rental) with a duplicate `ir_model_data` key. Rental's own steps complete, but the update as a whole does not.
- Both Codex reviews' `real_estate_developer` findings are fixed (above). Not changed: a price-change amendment is approved against its `financial_effect`, which the user types, so one entered as 0 passes every authority band.
- None of the upgrade sources had legacy unit lines, so the 0.10 restore of unmirrored legacy lines is covered by tests only, not by a real upgrade.
- Browser tests in Treasury and Brokerage time out when the machine is short of memory; they pass when rerun alone.
- `atmta_demo` and `atmta_ent_demo` still have company-less units and were not changed.
- The review server on port 8091 runs as a background task of this session and stops with it.
- Units without a company are still counted by no dashboard tile (unchanged rule). Resolved for the review data (decision, 14 Sep 2026: assign the company, on an upgraded copy):
  - Source: all 30 company-less properties in `atmta_w26_fresh` (0.6) were Rental demo records created at install by `__system__` (company 1). Every lease, allocation, obligation, invoice and project in that database is company 1, and no record points to the second company. The earlier figure of 32 counted all properties, including the two units that already had a company.
  - `atmta_w26_fresh` was copied to `atmta_w26_review` (database and filestore), and the copy was upgraded 0.6 → 0.10 with no errors. The original, its `_bak` and the review server on port 8091 are unchanged.
  - The 24 company-less units were given My Company (San Francisco) through the ORM, which also sets their product templates. The script refused to write if any lease on those units belonged to another company; none did. The 4 buildings and 2 compounds keep no company.
  - Rental Manager dashboard in the copy, before → after: Available to Lease 0 → 20, Occupied Units 0 → 1, Occupancy 0% → 3.8% (1 of 26 units), Active Leases 1 → 1. Afterwards every one of the 22 tiles equals its drilldown count, in both Mine and Team.
  - Cause fixed for new installs: `AR/demo/demo.xml` now sets `company_id` to `base.main_company` on its 30 demo properties. The file is `noupdate`, so installed databases are unaffected. Fresh install with demo data afterwards: no demo property without a company; Rental suite 427/427, 2 skipped.
  - `atmta_demo` (0.4, 24 units) and `atmta_ent_demo` (0.5, 332 units) have the same gap and were not changed.
- Developer, Project Plans and Construction apps are visible to every internal user (above); their owners should give those root menus groups.
- 20 procurement and construction tests still use IDs from before the capability-extraction refactors (above).
- Dashboard tile labels are English until Rental has translation files.
- The 5- and 30-second tests check that each thing is on the first screen or one menu away. They do not measure how long a real user takes. The other Developer entries an agent sees (Developers, Reports) were not traced. Dashboards and Discuss configuration menus come from core Odoo. Server-side access to these modules' records is governed by their own ACLs and was not changed.
