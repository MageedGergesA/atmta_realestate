# Rental UX Specification — `atmta_real_estate`

Phase 2 deliverable. It turns the findings in `RENTAL_UX_ARCHITECTURE_AUDIT.md` into the
target experience for the Rental application.

Design standard: **Odoo-native simplicity with enterprise rental depth.** Standard Odoo views,
statusbars, smart buttons, search panels and activities first; custom OWL only for the
dashboard and the map, which already exist.

Every field named here exists in `atmta_w26_fresh` unless marked **NEW**, and every new field is
a relational inverse or a label — no invented business data. Decisions confirmed by the business
owner on 13 September 2026 are binding (audit §J).

---

## Contents

1. [Product term and principles](#1-product-term-and-principles)
2. [Personas](#2-personas)
3. [Top tasks](#3-top-tasks)
4. [Menu architecture](#4-menu-architecture)
5. [Dashboard](#5-dashboard)
6. [Lease form](#6-lease-form)
7. [Lease list, search and kanban](#7-lease-list-search-and-kanban)
8. [Availability experience](#8-availability-experience)
9. [Tenants](#9-tenants)
10. [Billing experience](#10-billing-experience)
11. [Occupancy experience](#11-occupancy-experience)
12. [Renewal, amendment and termination](#12-renewal-amendment-and-termination)
13. [Role-based visibility](#13-role-based-visibility)
14. [Empty states](#14-empty-states)
15. [Error states and prevention](#15-error-states-and-prevention)
16. [Terminology](#16-terminology)
17. [Responsive behaviour](#17-responsive-behaviour)
18. [Implementation order](#18-implementation-order)

---

## 1. Product term and principles

**The primary term is *Lease*.** It replaces *Contract*, *Real Estate Contract*, *Rental
Contract* and *Lease Contract* in every visible label. Technical names do not change.

Principles, in priority order:

1. **Work before statistics.** Screens open on what needs attention.
2. **One concept, one name, one place.** No second menu, button or status for the same thing.
3. **Children live inside their parent.** Units, parties, charges, escalations and incentives are
   managed on the lease; deductions on the move-out; readings on the meter or the move.
4. **Only the next sensible action is shown.** Buttons follow `lifecycle_state`.
5. **The server decides.** Hiding a button is convenience; the method still checks role and
   prerequisites.
6. **Nothing that looks broken ships.** Menus to non-functional reports are removed, not labelled.

---

## 2. Personas

| Persona | Group (target) | Daily goal | Must never see |
|---|---|---|---|
| **Leasing Agent** — primary persona | `group_rental_agent` | Fill vacant units: find a unit, prepare and submit a lease, collect signatures, chase renewals on their portfolio | Settings, accounting journals, technical helper models, other apps' menus, legacy screens |
| **Property Manager** | `group_property_manager` | Keep units occupied and in good order: activate leases, run move-ins/outs and unit turns, watch arrears and expiries across all leases | Rental settings, accounting configuration |
| **Rental Manager** | `group_rental_manager` | Approve leases, amendments and terminations; watch portfolio health; configure rental policy | — |
| **Billing user / accountant** | Accounting group + `group_rental_user` | Keep billing obligations invoiced and collected; settle deposits | Lease workflow buttons they cannot use |
| **Rental User** (read-mostly) | `group_rental_user` | Look up leases, units and tenants | Workflow and configuration |

Plus the explicit capability flags *Allow Self-Approval* (`group_rental_self_approval`) and
**NEW** *See All Portfolios* (decision 4).

---

## 3. Top tasks

Ranked by frequency for the Leasing Agent; each must be reachable in at most two clicks from the
Rental app.

| # | Task | Entry point | Target clicks |
|---|---|---|---|
| 1 | See what needs my attention today | Dashboard › My Work | 0 |
| 2 | Find a unit I can lease now | Units › Available Units | 1 |
| 3 | Create and submit a lease | Available Units › *New Lease*, or Leases › New | 2 |
| 4 | Find a tenant's current lease | Global search on Leases (tenant), or Tenants | 1–2 |
| 5 | See leases expiring soon | Dashboard tile, or Leases › *Expiring Soon* filter | 1 |
| 6 | Start a renewal | Expiring lease › *Renew* | 2 |
| 7 | Check who owes rent | Dashboard tile, or Billing › Billing Obligations (default *Outstanding*) | 1 |
| 8 | Record a move-in or move-out | Lease › *Move-In* / *Move-Out* smart button, or Occupancy | 2 |
| 9 | Give notice / terminate | Lease › *Give Notice* / *Terminate* | 1 |

---

## 4. Menu architecture

One tree, owned by Rental (decision 1). The root keeps `atmta_real_estate.real_estate_menu_root`
and is renamed **Rental**. Parents used by other modules keep their xmlids.

```
Rental
├── Overview                                   Dashboard (client action)
├── Leases
│   ├── Leases                                 realestate.contract   ← main entry
│   ├── Expiring & Renewals                    realestate.contract (board)
│   ├── Renewals                               realestate.contract.renewal
│   ├── Amendments                             realestate.contract.amendment
│   └── Terminations                           realestate.contract.termination
├── Units
│   ├── Available Units                        realestate.property (Available to Lease)
│   ├── All Rental Units                       realestate.property
│   └── Map                                    Properties Map (client action)
├── Tenants                                    res.partner (has a lease)
├── Billing
│   ├── Billing Obligations                    realestate.contract.payment
│   └── Security Deposits                      realestate.contract.deposit
├── Occupancy                                  (Property Manager+)
│   ├── Move-Ins                               realestate.move.in
│   ├── Move-Outs                              realestate.move.out
│   ├── Unit Turns                             realestate.unit.turn
│   └── Meters                                 realestate.property.meter (+ readings inside)
├── Reporting                                  (Property Manager+)
│   ├── Rent Roll                              realestate.rent.roll
│   └── Lease Analysis                         realestate.contract (pivot, graph)
└── Configuration                              (Rental Manager)
    ├── Settings                               res.config.settings › Rental
    └── Lease Types                            contract.type
```

Removed from the Rental tree (xmlids kept, menus deactivated or moved): Contract Units, Payment
Schedules, Price Adjustment Rules, Payment Plans, Rental History, Utilities, Maintenance, Property
Types (Rental Manager keeps access from Settings), and the injected Treasury, Customer Service,
Public API and contract-template menus, which already exist in their own apps (audit §D).

`atmta_operations_app` keeps Handover and Service and removes its duplicate lease, billing and
occupancy leaves (decision 1).

**Default filters per action** (so lists open on work, not on everything):

| Action | Default |
|---|---|
| Leases | *Current* (draft through notice) — ended/terminated/cancelled hidden until asked |
| Expiring & Renewals | Active or notice, end date within 90 days |
| Renewals | *Open* |
| Available Units | *Available to lease* |
| Billing Obligations | *Outstanding* |
| Security Deposits | *Needs action* |
| Move-Ins / Move-Outs | *Upcoming* |
| Unit Turns | *Open* |

---

## 5. Dashboard

The dashboard answers, in order: *what needs my attention, what is happening today, what is
happening soon, how is the portfolio doing, what can I do next.* Every tile opens a list whose
domain is **the same domain the tile counts** — one domain builder serves both.

### 5.1 Layout

```
┌ Rental ─────────────────────────────── [New Lease] [Find Available Unit] [Move-In] [Move-Out]
│ Mine ◉ / Team ○                                               Refreshed 09:42  ↻
├ MY WORK ───────────────────────────────────────────────────────────────────────
│ Approvals waiting · Signatures waiting · Move-ins today/7d · Move-outs today/7d
│ Overdue obligations · Expiring ≤30d, no renewal · Renewals in progress
│ Notices to process · Deposits to settle · My activities due
├ PORTFOLIO HEALTH ─────────────────────────────────────────────────────────────
│ Available to lease · Occupied · Occupancy % · Active leases
│ Outstanding rent · Expiring 30 / 60 / 90 · Units in turnaround · Out of service
├ TRENDS (loaded after the tiles) ──────────────────────────────────────────────
│ Billed vs collected · Arrears ageing · Expiries by month · Occupancy over time
```

### 5.2 Tiles and their data

All sources are stored fields; date tests use `end_date`, `date_due` and `scheduled_date` against
the user's *today*, never the stored day counters (audit §G #13).

| Tile | Model | Domain (summary) | Shown to |
|---|---|---|---|
| Approvals waiting | lease, amendment, termination | lease `lifecycle_state = pending_approval`; amendment `state = proposed`; termination awaiting approval | Rental Manager |
| Signatures waiting | lease | `lifecycle_state = pending_signature`, `signature_status = pending` | Agent+ |
| Move-ins today / next 7 days | `realestate.move.in` | open states, `scheduled_date` within the user's local day / week | Property Manager+ |
| Move-outs today / next 7 days | `realestate.move.out` | open states, `scheduled_date` in range | Property Manager+ |
| Overdue obligations | `realestate.contract.payment` | `state = invoiced`, `amount_residual > 0`, `date_due < today` | Agent+ |
| Expiring ≤ 30 days, no renewal | lease | `lifecycle_state in (active, notice)`, `end_date ≤ today + 30`, `renewal_state` empty | Agent+ |
| Renewals in progress | `realestate.contract.renewal` | open renewal states **including `accepted`** | Agent+ |
| Notices to process | lease | `lifecycle_state = notice` | Property Manager+ |
| Deposits to settle | `realestate.contract.deposit` | held/received deposits on ended or terminated leases; requested but not received | Property Manager+ |
| My activities due | `mail.activity` | `user_id = me`, `date_deadline ≤ today`, rental `res_model` | Everyone |
| Available to lease | `realestate.property` | `is_available_for_lease` | Everyone |
| Occupied / Occupancy % | `realestate.property` | `occupancy_status` over leasable units | Everyone |
| Active leases | lease | `lifecycle_state in (active, notice)` | Everyone |
| Outstanding rent | `realestate.contract.payment` | invoiced, `amount_residual > 0` (split *due* vs *not yet due*) | Agent+ |
| Expiring 30 / 60 / 90 | lease | `end_date` ranges | Agent+ |
| Units in turnaround / out of service | unit turn, property | open turns; `maintenance_status != normal` | Property Manager+ |

*Mine / Team*: *Mine* restricts lease-based tiles to `user_id = me`; *Team* shows all leases the
user may see (record rules still apply).

### 5.3 Removed or moved to Reporting

Economic occupancy, all-time arrears rate, average rent per m², rent per m² by usage, contracted
rent run-rate, deposits-held total, average vacant days, completed move-ins/outs this month,
occupancy by building. Each is either non-actionable or currently computed on the wrong basis
(per-period rent summed as monthly; mixed currencies).

### 5.4 Performance

- Two calls: `get_work()` renders the tiles; `get_trends()` loads afterwards and may be cached.
- Occupancy over time: one SQL query over a date series, counting distinct units.
- Money totals are grouped by currency; the tile shows the company currency and flags other currencies.
- **NEW** indexes: `realestate_contract_payment(contract_id)`, `(date_due)`, `(company_id, state, date_due)`.

---

## 6. Lease form

### 6.1 Header

- **Statusbar**: `lifecycle_state` only, visible states
  `draft, proposal, pending_approval, pending_signature, active, notice, ended`; terminated and
  cancelled appear only when current. The legacy `state` is removed from every view.
- **Buttons by state** — primary button highlighted; at most three visible at once.

| Lease status | Primary | Secondary | Method (existing unless marked) |
|---|---|---|---|
| Draft | Submit for Approval | Propose · Cancel | `action_submit_for_approval`, `action_to_proposal`, `action_cancel_lease` |
| Proposal | Submit for Approval | Back to Draft · Cancel | `action_submit_for_approval`, `action_reopen_draft`, `action_cancel_lease` |
| Pending Approval | Approve *(Rental Manager)* | Return to Draft · Cancel | `action_approve_lease`, `action_reopen_draft`, `action_cancel_lease` |
| Pending Signature | Mark Signed → then Activate | Amend · Cancel | `action_mark_signed`, `action_activate_lease`, amendment launcher, `action_cancel_lease` |
| Active | Renew | Give Notice · Amend · Terminate | renewal launcher, `action_give_notice`, amendment launcher, termination launcher |
| Notice | Terminate / Complete Move-Out | Withdraw Notice *(if allowed by the transition map: notice → active)* · Amend | termination launcher, move-out, **NEW** `action_withdraw_notice` wrapper over the existing transition |
| Ended / Terminated / Cancelled | — | Reopen *(Cancelled only, Rental Manager)* | `action_reopen_draft` |

*Generate Billing Schedule* and *Invoice Due* move from the header into the **Billing** section
(§10). *Generate Document* and *Print Lease* move into the gear (action) menu.

### 6.2 Summary block (always visible, above the notebook)

| Left | Right |
|---|---|
| Tenant (`partner_id`) | Rent (`current_rent`) per Billing Frequency (`billing_frequency`) |
| Unit(s) (`property_id` / allocation count) | Balance Due (`balance_due`) with Collection status (`payment_status`) |
| Start – End (`start_date`, `end_date`) with days to expiry, computed live | Deposit Held (`deposit_held_total`) |
| Responsible (`user_id`) | Next Escalation (`next_escalation_date`, `next_escalation_amount`) |

Status chips next to the title: Signature, Billing, Renewal — text plus colour, never colour alone.

### 6.3 Smart buttons

One button box (`div[name='button_box']`), in this order, each hidden when its count is zero
except the first two:

Billing Obligations · Invoices · Deposits · Move-In · Move-Out · Renewals · Amendments · Terminations · Documents.

Removed: Sale Order, Payments (legacy), Properties and Parties (visible on the form already).

### 6.4 Notebook

Six sections, most-used first:

| Tab | Contains | Notes |
|---|---|---|
| **Units** | Allocations (`property_line_ids`): unit, area, allocated rent, start, end, occupies | The only unit concept. Legacy unit lines are not shown. |
| **Parties** | `party_ids` with role (tenant, co-tenant, occupant, company, guarantor, authorised representative, emergency contact, other) | — |
| **Billing** | Billing frequency, billing mode, due rule; *Generate Billing Schedule*, *Invoice Due*; obligations list (period, due, amount, outstanding, status) | Legacy "Payment Schedules" tab removed. |
| **Charges & Adjustments** | Rent escalations, recurring charges, incentives | Legacy increment/discount rules removed from the form. |
| **Deposit & Occupancy** | Deposit records with status; move-in and move-out records | Legacy deposit fields hidden. |
| **Terms & Documents** | Lease type, sealing and issue details, notes, attachments | The legacy "Contract Information" and "Sealing Information" groups move here. |

Chatter and activities below the sheet.

---

## 7. Lease list, search and kanban

### 7.1 List

Columns in scanning order: Lease · Tenant · Unit · Start · End · Rent · Status · Collection ·
Responsible · Company *(multi-company only, optional)*.

Decorations, sparingly: **danger** when Collection is overdue; **warning** when active and
`end_date ≤ today + 30`; **info** for pending approval or pending signature; **muted** for closed
leases. Status and Collection render as badges with text.

### 7.2 Search

- **Search fields**: Lease number (`name`), Tenant (`partner_id`, also matches lease parties), Unit
  (`property_id` and allocations), Responsible (`user_id`).
- **Filters** — status group: My Leases · Draft & Proposal · Awaiting Approval · Awaiting
  Signature · Active · On Notice · Ended · Cancelled. Attention group: Expiring in 30 / 60 / 90
  days (live `end_date` domains) · Overdue (`payment_status = overdue`) · Renewal Not Started
  (`renewal_state` empty). Legacy `state` filters are removed.
- **Group by**: Status · Unit · Tenant · Responsible · End Month (`end_date:month`) · Company.
- **Search panel** on the Leases action: Status (`lifecycle_state`, with counts).

### 7.3 Kanban

Only two workflows get kanban:

- **Proposal pipeline** — leases grouped by `lifecycle_state` (draft, proposal, pending approval,
  pending signature); card: lease, tenant, unit, rent, responsible avatar, activity.
- **Expiring & Renewals board** — grouped by a live end-date range, not the stored bucket; card:
  lease, tenant, unit, end date, renewal status, *Renew* quick action.

Unit turns keep their kanban (work queue). No other kanban is added.

---

## 8. Availability experience

Goal: *"What can I rent to this customer right now?"* in seconds.

- **Available Units** opens a list of `realestate.property` with default filter *Available to
  lease* (`is_available_for_lease`), sorted by `available_from`.
- **Columns**: Unit (`property_code`, name) · Building (`parent_id`) · Type (`property_type_id`) ·
  Usage (`usage_category`) · Bedrooms (`bedroom_count`) · Area (`area_sqm`) · Furnished
  (`furnished_status`) · Available From (`available_from`) · Occupancy (`occupancy_status`).
- **Filters**: Available now · Available within 30 days (`available_from`) · Occupied ·
  Pending signature (blocked by a lease) · Out of service (`maintenance_status`) · Furnished.
- **Search / group by**: building (`parent_id`), city (`city`, `sub_area`), type, usage, bedrooms,
  floor (`floor_number`).
- **Row action**: *New Lease* on an available unit opens a draft lease with the unit and company
  prefilled.
- **Form**: rental-oriented page on the unit with current lease, lease history (from allocations)
  and meters; the property status bar and badge row are reduced to Availability and Occupancy.

**Not available and not invented**: a *rent range* filter — units carry no asking-rent field
(`list_price` is the sales price). Project and phase belong to `real_estate_developer`, which
Rental does not depend on; that module may add those filters to this search view.

---

## 9. Tenants

Tenants are ordinary contacts (`res.partner`); no tenant model is created.

- **NEW** `res.partner.rental_lease_ids` — one2many inverse of `realestate.contract.partner_id`.
- **NEW** `res.partner.is_rental_tenant` — stored boolean, true when the contact has any lease.
- **Tenants** action: contacts with `is_rental_tenant`, list columns Name · Phone · Email · Current
  lease · Balance due.
- **Contact form, "Rental" tab** (shown only to rental groups): current and previous leases,
  outstanding billing obligations, deposits, units occupied. Identity fields Rental already owns
  (`id_type`, `id_number`, `cr_number`, `cr_date`, `unified_number`, `issued_by`,
  `organization_type_id`, `is_lessor`) live here instead of on the main partner page.

---

## 10. Billing experience

One authority: the billing engine (decision 2). One vocabulary: **Billing Obligation** for a
record, **Billing Schedule** for a lease's set of obligations.

- **Billing › Billing Obligations**: default *Outstanding*; filters Due Soon (`date_due ≤ today +
  lead days`), Overdue (`date_due < today` with residual), Not Yet Invoiced, Paid; group by lease,
  tenant, month of `date_due`. Columns: Obligation · Lease · Tenant · Period · Due · Amount ·
  Outstanding · Status. Invoices open from the obligation.
- **Lease › Billing tab**: *Generate Billing Schedule* and *Invoice Due* live here, visible only
  when the lease is billed by the engine and has no whole-term legacy invoice.
- **Leases billed the legacy way** show a read-only notice: *"Billed by a single invoice for the
  whole term (INV/…)."* No per-period invoicing button appears for them.
- **Security Deposits**: default *Needs action*; header buttons follow deposit state; forfeiture,
  refund and applying to arrears stay manager-gated.
- Accounting internals (journals, accounts, payment terms) never appear on lease or obligation
  screens; they live in Settings.

---

## 11. Occupancy experience

- **Move-Ins / Move-Outs**: default *Upcoming* (open states, `scheduled_date` from today); filters
  Today · This Week · Overdue Inspections (open and `scheduled_date < now`) · Completed; calendar
  view on `scheduled_date`. **NEW** search views for both.
- **Unit Turns**: kanban work queue by state; default *Open*; filter *Awaiting Turn* (units vacated
  without a started turn). **NEW** search view so the default filter works.
- **Meters**: list of meters by unit; readings managed inside the meter and captured from
  move-in/move-out.
- Move-in *Complete* requires inspection first; unit turn *Mark Ready* requires a passed
  inspection (server-side, §15).

---

## 12. Renewal, amendment and termination

- **Renewals**: kanban work queue (open states through accepted), list, **NEW** search view with
  My Renewals, Open, Accepted, Rejected; card shows current end date, proposed rent, tenant response.
- **Amendments**: list and form; **NEW** search view (Proposed, Approved, Signed, Applied).
- **Terminations**: list and form; **NEW** search view (Notice Given, Approved, Settled, Completed).
- All three open from the lease smart buttons; the lease header only starts them.

---

## 13. Role-based visibility

| Area | Rental User | Leasing Agent | Property Manager | Rental Manager | Accountant (+ Rental User) |
|---|---|---|---|---|---|
| Overview | ✓ (own tiles) | ✓ | ✓ | ✓ | ✓ billing tiles |
| Leases — read | Own portfolio¹ | Own portfolio¹ | All | All | All (read) |
| Leases — create, submit | — | ✓ | ✓ | ✓ | — |
| Approve lease / amendment / termination | — | — | — | ✓ | — |
| Activate lease | — | — | ✓ | ✓ | — |
| Units | ✓ | ✓ | ✓ | ✓ | ✓ |
| Tenants | ✓ | ✓ | ✓ | ✓ | ✓ |
| Billing | read | ✓ | ✓ | ✓ | ✓ |
| Occupancy | — | — | ✓ | ✓ | — |
| Reporting | — | — | ✓ | ✓ | ✓ Rent Roll |
| Configuration | — | — | — | ✓ | — |

¹ *See All Portfolios* flag lifts the portfolio restriction (decision 4).

Menus carry `groups` matching this table; every workflow method keeps its server-side group
check; record rules keep company isolation and portfolio visibility. Legacy group xmlids remain as
hidden aliases implied by the new roles so other modules keep working.

---

## 14. Empty states

Every Rental action gets a `help` block (`o_view_nocontent_smiling_face` for creatable lists,
`o_view_nocontent_empty_folder` otherwise):

| Screen | Title | Body |
|---|---|---|
| Leases | Create your first lease | "Leases track a tenant's occupancy of one or more units, from proposal through signature, billing and renewal." |
| Available Units | No units are available to lease | "Every leasable unit is occupied, reserved or out of service. Clear the filters to see all units." |
| Expiring & Renewals | Nothing expiring soon | "No active lease ends in the next 90 days." |
| Renewals | No renewals in progress | "Renewals start from a lease that is expiring." |
| Billing Obligations | Everything is collected | "No billing obligation has an outstanding balance." |
| Security Deposits | No deposits need action | "Deposits appear here when they are requested, held on an ended lease, or ready to settle." |
| Move-Ins / Move-Outs | No upcoming move-ins / move-outs | "Scheduled moves appear here from their lease." |
| Unit Turns | No units are being turned | "A turn starts when a tenant moves out." |
| Tenants | No tenants yet | "Contacts appear here once they hold a lease." |

---

## 15. Error states and prevention

Server-side, with messages that say what is wrong and what to do:

| Rule | Enforced in | Message (summary) |
|---|---|---|
| A lease is billed by exactly one path | `_create_invoices`, `action_invoice_due_obligations`, `action_generate_invoices`, both schedule generators | "This lease is billed by a single invoice for its whole term. Credit or cancel that invoice before billing by period." |
| No approval bypass | Legacy *Confirm* routes through the approval rules or is refused | "This lease needs approval by a Rental Manager before it can go to signature." |
| No activation without signature and unit | Legacy *Activate* routes through `action_activate_lease` | "Lease CT-… has not been signed yet." / "…has no unit allocated." |
| No termination bypass | Legacy *Terminate* opens the termination process | "Terminate this lease through a termination so notice, settlement and the deposit are handled." |
| No overlapping occupancy | Allocation overlap engine (existing) | Existing message, unchanged |
| No move-in before the lease is ready | `realestate.move.in` completion | "Complete the move-in after the lease is signed and active." |
| No expiry during a termination | `_cron_expire_leases` (resolved) | — |
| No status set by writing | `realestate.contract.write/create` (existing) | Existing message, unchanged |
| No double invoicing of one obligation | `_create_invoices` skips obligations with an invoice (existing) plus a row lock | — |

---

## 16. Terminology

| Use | Instead of |
|---|---|
| Lease | Contract, Real Estate Contract, Rental Contract, Lease Contract |
| Lease Number | Contract Reference |
| Lease Status | Status (legacy), Lease Status/Status mixes |
| Unit | Contract Unit, Contract Line, Contract Property Line, Property Allocation (on screens) |
| Billing Obligation / Billing Schedule | Payment Schedule, Scheduled Payment, Contract Payment |
| Collection | Payment Status (kept: *Collection*) |
| Rent Escalation | Price Adjustment Rule, Price Increment Rule |
| Recurring Charge | Charge Rule |
| Incentive | Discount Rule (for leases) |
| Security Deposit | Deposit State (legacy), Deposits (tab) |
| Responsible | Property Manager (as the label of `user_id`) |
| Lease Type | Contract Type |
| Give Notice / Terminate / Renew / Amend | Terminate (legacy), Confirm Contract, Reset to Draft |

Arabic labels follow the same single-term rule (عقد الإيجار، الاستحقاق، جدول الاستحقاقات،
التأمين).

---

## 17. Responsive behaviour

- Forms use two-column groups only for the summary; notebook tabs stack on tablets.
- Lists keep at most nine default columns; secondary columns are `optional="hide"`.
- Kanban cards show four lines at most and one avatar.
- The dashboard grid collapses to one column under 768 px; tiles keep label and number on one line.
- No horizontal scrolling on forms at 1280 px.

---

## 18. Implementation order

Phase 3 — safe cleanup (business risk first):

1. **Billing authority guards** and hiding the legacy invoice/schedule buttons (decision 2).
2. **Legacy lifecycle bypasses** — Confirm, Activate, Terminate routed through the enterprise checks; legacy deposit fields read-only.
3. **Payment-plan collision** — dedicated migration (decision 3).
4. **Legacy unit lines → allocations**; **increment rules → escalation rules** for future terms.
5. **Menus and actions** — one Rental tree, operations app de-duplicated, injected menus moved.
6. **Roles** — hierarchy, aliases, *See All Portfolios* flag, migration (decision 4).
7. **Stale date fields and indexes**; availability refresh.
8. **Dead code** — first-generation dashboard, pivots and report models, scaffold, broken Excel controller; decide the three API reports.

Phase 4 — UX implementation, in this order: lease form and search, availability, billing,
occupancy and renewal search views, tenants, dashboard, empty states and terminology sweep.

Phase 5 — full test runs across Rental and every dependent module, the seven user flows, and the
five-second, thirty-second, consistency, noise and safety tests.
