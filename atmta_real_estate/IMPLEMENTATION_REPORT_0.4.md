# `atmta_real_estate` 0.3 → 0.4 — Implementation Report

**Scope:** Module 1 only. Thirteen downstream ATMTA modules untouched.
**Verified on:** a clone of the live `atmta_realestate` database (all 14 modules installed).

---

## 1. Audit findings

### 1.1 What existed

`atmta_real_estate` v0.3: 24 models, ~9,400 LOC. Property master with a 5-level
hierarchy delegating to `product.template`; rental contracts in two mutually
exclusive modes (single-unit / multi-unit); a payment-plan + increment-rule
schedule generator; a security-deposit state field; utility lines; maintenance
requests; a Leaflet map and a Chart.js dashboard.

The accounting integration was already sound: `realestate.account.tools`
centralised posting and payment registration through
`account.payment.register`, and rent was correctly an `out_invoice`.

### 1.2 What was weak

| # | Finding | Evidence |
|---|---|---|
| **C1** | **Seven writers fought over one `property.state` field** across three modules | `contract.py:286,303` · `contract_line.py:124,127,162` · `maintenance_request.py:56,68,80` · `_base_property.py:258,263` · `reservation.py:89,140,158` · `sale_contract.py:100,161,174` |
| **C2** | **Maintenance completion evicted sitting tenants.** `action_complete` set `state='available'` on a *rented* unit | `maintenance_request.py:68` |
| **C3** | **Single-property leases had ZERO overlap protection.** The only check was an `@api.constrains` on `realestate.contract.line`; `contract.property_id` was unguarded, and the two shapes were blind to each other | `contract_line.py:96` |
| **C4** | `_update_property_state()` ran on **every** `write()` and could raise — editing an unrelated field failed if the property went to maintenance | `contract_line.py:113,141` |
| **C5** | **No `company_id` anywhere**; `property_code`, `contract.name`, `payment.name` all **globally** unique | `_base_property.py:187` |
| **C6** | Contract `state` mixed lifecycle with billing (`invoiced` was a lifecycle value) | `contract.py:19-27` |
| **C7** | Sale-contract cancel set `available` without checking rental occupancy | `sale_contract.py:174` |
| **C8** | **Zero `ir.rule` records in the entire 14-module suite** — no multi-company isolation existed | `grep -rc ir.rule` → 0 |
| **C9** | `contract.type` had **no ACL row at all** — a non-superuser hit `AccessError` opening the lease form | `security/ir.model.access.csv` |
| **C10** | Availability was a manual field somebody had to remember to reset | `_base_property.py:122` |
| **C11** | Meters were six flat columns — no history, no second meter, no reader, no date | `_base_property.py:174-180` |
| **C12** | Dashboard loaded **every** payment into Python to build a top-tenants list | `rental_dashboard.py` |
| **C13** | No tests at all | `find -path "*/tests/*"` → 0 |

### 1.3 Accounting direction — confirmed correct

The specification flagged "documentation ambiguity around inbound/outbound
rental payments". **The implementation was already right.** Tenant rent is
`out_invoice` → `account.payment.register` → **inbound customer payment**
(`rental_invoicing.py:36`, `contract.py:519`, `account_tools.py:29`). The
`in_invoice` at `contract_utility_line.py:34` is the landlord paying the
utility *provider* — a legitimately separate outbound flow, not a bug.
Regression tests now pin both.

### 1.4 Downstream dependency map

| Consumed | By |
|---|---|
| `realestate.property` | maquette, plan, developer, brokerage, handover, api, portal, procurement, customer_service, checks (10 modules, 60+ references) |
| `property.state` reads | maquette (`building_floor`, `building_preview`), developer (`phase`, `project`, dashboard), brokerage (`lead` domain), api (`catalog`, `map`, `interest` public gates), portal (`public.py`) |
| `realestate.contract` | checks, api (`portal`, `downloads`), contract_template, procurement, partner statement report |
| `realestate.contract.payment` | checks (`rental_payment_id` m2o), api (`portal`, `downloads`), partner statement report |
| `realestate.account.tools` | every money-moving module in the suite |

---

## 2. Architecture changes

### 2.1 Models created (14)

| Model | Phase | Purpose |
|---|---|---|
| `realestate.contract.property.line` | 5, 6 | **Canonical** lease↔property allocation + overlap engine |
| `realestate.contract.party` | 7 | Multi-party leases (8 roles) |
| `realestate.contract.charge.rule` | 10 | Recurring charges (11 categories × 4 bases × 6 frequencies) |
| `realestate.rent.escalation.rule` | 11 | Declarative, per-lease, dated rent escalation |
| `realestate.contract.incentive` | 12 | Rent-free / fit-out / discounts |
| `realestate.contract.deposit` | 14 | Deposit as a real financial record |
| `realestate.contract.renewal` | 17 | Renewal negotiation → next lease |
| `realestate.contract.amendment` | 18 | Idempotent mid-term changes, 10 types |
| `realestate.contract.termination` | 19 | Notice → settlement → completion |
| `realestate.move.in` | 20 | Handover inspection |
| `realestate.move.out` + `.deduction` | 21 | Exit inspection + deposit deductions |
| `realestate.unit.turn` | 22 | The **only** thing that returns a unit to market |
| `realestate.property.meter` + `.reading` | 23 | Normalised meters with history |
| `realestate.rent.roll` | 24 | SQL view: one row per allocation + one per vacant unit |

Plus `res.company` / `res.config.settings` extensions and two pure-Python
helper modules (`lease_states.py`, `proration.py`) with no ORM.

### 2.2 Models extended

`realestate.property` (5 new files), `realestate.contract` (8 mixins),
`realestate.contract.payment` (billing obligation + arrears),
`realestate.contract.payment.line`, `realestate.contract.line`,
`realestate.maintenance.request`, `property.type`.

### 2.3 State machines

**Changed:**

| Field | Before | After |
|---|---|---|
| `property.state` | 6-value writable field, 7 writers | **computed bridge** over 5 dimensions |
| `contract.state` | 7-value writable, lifecycle+billing | **computed bridge** over `lifecycle_state` + `billing_status` |
| `contract.payment.state` | `in_payment` counted as paid | requires `payment_state='paid'` **and** zero residual |

**Added (20 new machines)** — lifecycle (9), billing/payment/signature status,
overdue buckets (6), deposit (9), renewal (8), amendment (6), termination (6),
move-in (4), move-out (5), unit turn (6), commercial/occupancy/construction/
handover/maintenance status, usage categories (11), party roles (8).

### 2.4 Deprecated compatibility fields

| Field | Status |
|---|---|
| `realestate.property.state` | Computed bridge. Reads and writes both work. |
| `realestate.property.occupancy_state` | Mirrors `occupancy_status`. Relabelled "(legacy)". |
| `realestate.property.{electricity,water,gas}_*` | Auto-synced from the newest meter reading. |
| `realestate.contract.state` | Computed bridge. |
| `realestate.contract.deposit_state` + amounts | Synced from `realestate.contract.deposit`. |
| `realestate.contract.{property_id, line_ids}` | Still authoritative for input; mirrored into allocations. |
| `realestate.contract.payment.amount` | Now `Monetary`; still the net base rent. |
| `re_legacy_state_backup` columns | **Kept** post-migration for auditability. |

**Nothing was deleted.**

---

## 3. Business workflows

**Property availability** — derived, never manual:
`active` → `is_leasable` → `commercial_status` → `maintenance_status` →
`construction_status` (only for units in a development) → release window →
blocking allocations → **audited manager override**. Always yields a written
`availability_reason`.

**Lease creation → activation:**
`draft → proposal → pending_approval → pending_signature → active`.
Approval is gated on Rental Manager and blocks self-approval. Activation
requires a signature and at least one allocation, and the overlap constraint
fires under a Postgres advisory lock.

**Recurring billing:** periods → gross rent (`_rent_on`, escalation-aware) →
proration against the *natural* period → incentive relief → charge lines →
obligation. Idempotent; never rewrites an invoiced obligation.

**Payment:** obligation → `out_invoice` (posted) → `account.payment.register`
→ reconciliation. Partial payments leave the obligation outstanding.

**Rent increase:** declarative dated rules. `_rent_on(date)` walks them —
side-effect free, so regeneration cannot double-apply.

**Renewal:** `draft → proposed → negotiating → approved → accepted → renewed`,
creating a **new** lease. The historical lease keeps its dates and rent.

**Amendment:** `draft → proposed → approved → signed → applied`. `applied_date`
is the idempotency guard. Before/after snapshotted.

**Termination:** `ACTIVE → NOTICE → TERMINATED` (or `ENDED` at expiry).
Un-invoiced future obligations deleted; **invoiced ones credited, never
unlinked**; terminal period reprorated; final invoice raised; unit turn opened.

**Move-in / move-out:** condition + meters + keys recorded; occupancy stamped;
move-out opens a unit turn and **does not** make the unit available.

**Deposit:** `requested → received/held → refunded | partially_refunded |
forfeited | applied`. Every transition posts through Odoo.

---

## 4. Accounting flow

```
LEASE
  └─ realestate.contract.charge.rule / rent.escalation.rule / contract.incentive
        ↓  (billing_engine — ATMTA owns this)
BILLING OBLIGATION            realestate.contract.payment
        ↓  _create_invoices()
CUSTOMER INVOICE              account.move  (move_type='out_invoice', posted)
        ↓  account.payment.register
ACCOUNT PAYMENT               account.payment (payment_type='inbound')
        ↓
BANK RECONCILIATION           Odoo standard
```

**Partial payments.** `amount_invoiced`, `amount_paid`, `amount_residual` are
read from `account.move` on every recompute — never cached independently. An
obligation is `is_settled` only when Odoo reports `payment_state in ('paid',
'reversed')` **and** `abs(amount_residual) < 0.01`. A registered-but-
unreconciled payment (Odoo 18 `in_process`) leaves it outstanding, which is the
accounting-correct answer and a change from v0.3.

**Deposits are a liability, never revenue:**

| Event | Posting |
|---|---|
| Received | inbound `account.payment`, destination = **deposit liability** account |
| Refunded | outbound `account.payment` clearing the same liability |
| Forfeited | `account.move`: Dr Deposit Liability / Cr Forfeited-Deposit Income — the **only** path to P&L |
| Applied to arrears | Dr Deposit Liability / Cr Receivable, then reconciled against open invoices by Odoo |

The code refuses a non-liability or non-reconcilable account with a specific
error rather than posting somewhere wrong.

**Confirmed: rental payments are inbound customer payments.** Pinned by
`test_billing.test_payment_is_inbound` and
`test_invoice_is_an_outbound_customer_invoice`.

---

## 5. Security matrix

```
Read-only
  └─ Rental User (+ base.group_user)
       └─ Leasing Agent (+ legacy RE User)
            └─ Property Manager
                 └─ Rental Manager (+ legacy RE Manager)
Allow Self-Approval — standalone override flag
```

| Capability | RU | LA | PM | RM |
|---|:--:|:--:|:--:|:--:|
| View properties / leases / obligations | ✅ | ✅ | ✅ | ✅ |
| Create allocations, parties, charges, incentives | | ✅ | ✅ | ✅ |
| Generate billing schedule / invoice due | | ✅ | ✅ | ✅ |
| Prepare renewals, propose amendments | | ✅ | ✅ | ✅ |
| Write rent escalations | | ❌ read-only | ❌ | ✅ |
| Activate lease, give notice, end lease | | | ✅ | ✅ |
| Move-in / move-out / unit turn / meters | | | ✅ | ✅ |
| Availability override (reason + user + timestamp) | | | ✅ | ✅ |
| Approve lease / amendment / renewal | | | | ✅ |
| Approve termination, waive penalty | | | | ✅ |
| Refund / forfeit / apply deposit | | | | ✅ |
| Delete any leasing record | | | | ✅ |
| **Accounting management** | ❌ | ❌ | ❌ | ❌ |

**Record rules:** 19 global multi-company rules (the suite had **zero**), plus a
portfolio-scoping rule for Rental User with a permissive counterpart for
Property Manager and above.

**Every workflow action is gated server-side** via `_require_group()`. Hidden
buttons are not treated as security — `test_security` proves each gate holds
when called directly.

**Self-approval** is blocked by default: a user cannot approve a lease they
created, unless the company allows it or they hold the override group.

---

## 6. Test report

**299 tests, 0 failed, 0 errors** — 284 Python business tests plus 15
browser-driven tests (HOOT unit suite, five tours, bundle and production-asset
gates). See §9.5 for the release-gate table.

| Module | Covers |
|---|---|
| `test_proration` | 16 pure-function tests: day counts, all 4 conventions, boundaries |
| `test_property_status` | Dimensions, legacy bridge, **the maintenance-eviction regression**, occupancy rollup, hierarchy vs usage |
| `test_availability` | Every blocker, `next_available_date`, override + its ACL |
| `test_lease_overlap` | Every boundary the spec lists + single-vs-multi collision + concurrency |
| `test_lease_lifecycle` | Transitions, guards, direct-write validation, approval controls, legacy bridge, allocation mirror |
| `test_parties` | Primary sync, role rules, responsibility totals |
| `test_billing` | Schedule generation, idempotency, all 4 charge types, proration, **inbound payment**, **partial payment**, arrears buckets |
| `test_escalation` | 4 types, chaining, date boundaries, determinism, generator |
| `test_incentives` | Rent-free/fit-out/discounts, **obligation still exists**, stacking cap |
| `test_deposit` | Receipt/refund/forfeit/apply, **liability-not-revenue**, config guards, ACL |
| `test_renewal` | Full workflow, **historical lease not rewritten**, reminders idempotent |
| `test_amendment` | All 10 types, **idempotency**, history, effective-date, invoiced-period immutability |
| `test_termination` | Notice, penalties, **credit-not-delete**, reprorated terminal period, unit turn |
| `test_move_in_out` | Both workflows, meters, **move-out ≠ available**, turn gating |
| `test_meters` | Consumption, history, backwards guard, legacy sync, metered charges |
| `test_security` | Group hierarchy, ACLs, **server-side gates**, no accounting escalation |
| `test_multi_company` | Cross-company rejection, per-company codes, record-rule isolation |
| `test_compatibility` | Every downstream contract: `property.state`, `contract.state`, obligation fields, checks m2o, rent roll, **every dashboard tile drills down**, **the drilldown action contract itself** |
| `test_dashboard_js` | HOOT front-end unit suite + the dashboard integration tour, in headless Chrome |
| `test_dashboard_env` | Real viewports (1024/991/768, touch), Arabic RTL session, empty company, multi-company switching, **plus server-side company scoping** |
| `test_dashboard_assets` | Bundle composition, V1 absence, Chart.js singularity, no CDN, minified production bundle, cold cache |

**Unresolved failures: none.**

### Bugs the suite caught before release

| # | Bug | Consequence had it shipped |
|---|---|---|
| 1 | `required=True` on stored computed fields | Every property creation failed |
| 2 | Odoo **merges** `_sql_constraints` across inheritance | Old global `UNIQUE(property_code)` survived — multi-company codes still impossible |
| 3 | Proration measured the **truncated** period | Lease ending on the 15th billed a full month |
| 4 | `construction_status` falls back to `'planning'` with no project | Every standalone rental unit permanently unavailable |
| 5 | Occupancy rollup treated an unset child as "mixed" | Empty building reported `partially_occupied` |
| 6 | Termination filtered a recordset snapshot after unlinking | `MissingError` during settlement |
| 7 | 30/360 count lacked the end-of-month rule | February billed 28/28, inconsistent with other months |
| 8 | Party responsibility constraint fired mid-edit | A 40/60 split was impossible to enter |
| 9 | Company config read required `res.company` access | Approval crashed for ordinary managers |
| 10 | KPI tiles were company-scoped, their drilldowns were not | A tile said 31, its drilldown listed 34 — the record rule also admits `company_id = False` |
| 11 | Dashboard root carried `dir="auto"` | The entire dashboard would render **left-to-right for every Arabic user**, inside an otherwise RTL Odoo (§9.3) |
| 12 | Chart.js vendored a second time in `assets_backend` | 201 KB on every backend page, and two `Chart` globals once a graph view was opened (§9.2) |

---

## 7. Compatibility report

Restored a pristine 0.3 clone and upgraded **all 14 modules together**:

```
odoo exit code: 0
errors (excluding pre-existing view warnings): none
```

| Module | Version | State |
|---|---|---|
| atmta_real_estate | 18.0.0.4 | installed |
| real_estate_api / brokerage / checks / construction / contract_template / customer_service / handover / investment / plan / portal / procurement | 18.0.0.1–0.3 | installed |
| real_estate_developer | 18.0.0.2 | installed |
| real_estate_maquette | 18.0.0.4 | installed |

**Data integrity:**

| Property | Before | After | commercial | occupancy | maintenance |
|---|---|---|---|---|---|
| S-U101 | reserved | reserved | reserved | vacant | normal |
| S-COMP | available | available | available | vacant | normal |
| R-U-001 | available | available | available | vacant | normal |

| Lease | Before | After | lifecycle |
|---|---|---|---|
| RC-TEST-001 | draft | draft | draft |

**One cross-module collision found and resolved:** `real_estate_developer`
already declares `construction_status` on `realestate.property` as a stored
computed field. Module 1's selection is now a deliberate **superset** using
identical spellings where they overlap, and the developer module retains
ownership. Odoo logs an "overrides existing selection" warning on a full
install — expected and harmless.

**Remaining warnings are all pre-existing** (missing `alt` on `<img>`, missing
`title` on `<i class="fa">`, deprecated `kanban-box`) in views this upgrade did
not touch.

**Re-run at closeout.** Removing the vendored Chart.js changed five sibling
modules (§9.2), so the 14-module upgrade was repeated on a fresh clone of the
full database:

```
odoo exit code: 0
ERROR / CRITICAL lines: 0
```

| Module | Version | State |
|---|---|---|
| atmta_real_estate | 18.0.0.4 | installed |
| real_estate_maquette | 18.0.0.4 | installed |
| real_estate_plan | 18.0.0.3 | installed |
| real_estate_developer | 18.0.0.2 | installed |
| real_estate_api / brokerage / checks / construction / contract_template / customer_service / handover / investment / portal / procurement | 18.0.0.1 | installed |

---

## 8. Remaining gaps

### ✅ Implemented and tested
Phases 1–14, 16–23, 27–34 in full: status dimensions, usage/hierarchy split,
identifiers with scoped uniqueness, availability engine, overlap protection,
allocations, parties, lifecycle, billing obligations, charge rules, escalation,
incentives, proration, deposits, arrears, renewals, amendments, termination,
move-in/out, unit turn, meters, security, multi-company, auditability,
automation, views, performance, tests, migration.

### ⚠️ Partially implemented

| Item | State |
|---|---|
| **Phase 15 — accounting** | Complete for the new engine. The **legacy** payment-plan generator still produces one invoice split by a per-contract `account.payment.term`; it is unchanged and untested by this suite. |
| **Phase 24 — rent roll** | All columns and filters present; XLSX via Odoo's built-in list export (`export_xlsx="1"`). No dedicated `report_xlsx` template — the spec said not to add a dependency for it. |
| **Phase 26 — dashboard v2** | Complete. 20 KPIs, 6 charts, 3 lists, drilldowns — server side **and** the OWL front end, verified in a real browser at three tablet widths, in an Arabic RTL session, on an empty company and across a multi-company switch. See §9. Filters are deferred to 0.5 (§9.5). |
| **Phase 30 — billing cron** | Implemented, idempotent, batch/multi-company/retry-safe, but ships **inactive** by design. |
| **Phase 12 — incentives** | Modelled and billed correctly. `total_value` is indicative — it uses the rent at the incentive start, not per-period escalated rent. |

### 📋 Planned / not done

* **Economic occupancy** estimates vacant-unit potential at the portfolio
  average rent/m². Defensible, but an estimate — returns `0.0` rather than
  inventing a denominator when there is no basis.
* **`realestate.contract.end_date` remains required.** Open-ended leases are
  expressible at the *allocation* level only. Relaxing the contract field is a
  downstream-visible change and was left alone.
* **No index-feed integration.** `escalation_type='index'` stores the basis
  string and the negotiated amount; nothing fetches CPI.
* **Legacy `deposit_state` buttons still exist** on the contract form alongside
  the new deposit record. They are synced one-way (record → legacy). Removing
  them is a follow-up once users migrate.
* **`_read_group` in `property_status`** is v18-correct; the deprecated
  `check_access_rights` / `check_access_rule` calls in `real_estate_maquette`
  and `real_estate_plan` (26 sites) are **outside this module's scope** but will
  break on v19.

### 🚫 Intentionally out of scope (Phase 35)

developer sales pricing · unit sale reservation · brokerage commissions ·
construction · BOQ · procurement · full collections case management ·
facilities management · owner association · Saudi Wafi · Ejar · CRM
replacement · custom accounting ledger · duplicate payment engine

`realestate.unit.turn` provides the minimal turn object plus a late-bound
`_optional_model()` hook. **This module imports no downstream module.**

---

## 9. Rental Dashboard V2 — release closeout

### 9.1 Drilldown action contract, fixed on the server

Odoo's action service calls `action.views.map(...)` unconditionally in
`_preprocessAction`, and `views` is only populated server-side when an action is
loaded by database id. An action returned inline from a Python method must
therefore carry `views` itself. Previously the front end patched that up; that
was the wrong layer.

`realestate.rental.dashboard` now builds every action through two helpers:

| Method | Returns |
|---|---|
| `_list_action(name, res_model, domain, context)` | complete `ir.actions.act_window` with `views=[(False,'list'),(False,'form')]` |
| `_form_action(name, res_model, res_id, context)` | complete form action |
| `action_open_record(res_model, res_id, name)` | validated `_form_action`; raises `UserError` for an unknown model |

`view_mode` is still emitted alongside `views` for non-web callers (XML-RPC,
reports) that read it.

The front end's `normalizeAction()` was demoted to a **defensive guard** — with
a comment saying so — and list-row clicks now call `action_open_record` instead
of assembling an action in JavaScript. A browser should not be responsible for
repairing a malformed action.

`TestDashboardActionContract` asserts every returned action carries
`type / name / res_model / views / domain / target`, for list drilldowns,
arrears-bucket drilldowns and row form actions, and that an unknown model is
refused rather than quietly returned.

### 9.2 Chart.js ownership audit

| Question | Answer |
|---|---|
| Version | 4.4.1 |
| Source | `web/static/lib/Chart/Chart.js`, shipped by Odoo |
| Bundle | `web.chartjs_lib` — lazy, never in `assets_backend` |
| Owner | Odoo `web`; also used by the graph view, gauge field and journal dashboard graph |
| CDN | none, anywhere in the module |
| Duplicates | one — **now removed** |

Before this closeout the module vendored its own copy at
`static/src/lib/chartjs/chart.umd.js`: the same version, 201 KB, loaded
**eagerly in `web.assets_backend`**, so every backend page in the database paid
for a library one dashboard uses. It also meant a user who opened any graph
view would trigger Odoo's lazy bundle as well, leaving two copies competing to
define the `Chart` global.

The duplicate is deleted, and `DashboardChart` does what Odoo's own graph
renderer does:

```js
onWillStart(() => loadBundle("web.chartjs_lib"));
```

**Collateral, and why it was in scope.** Five sibling modules
(`real_estate_developer`, `brokerage`, `construction`, `investment`,
`handover`) call `new Chart(...)` without declaring the library anywhere. All
five depend on `atmta_real_estate` transitively and were silently consuming the
global its bundle happened to publish. Removing the duplicate without touching
them would have broken five dashboards, so each received the identical one-line
`loadBundle` in `setup()`. Nothing else in those modules was changed.

Global state: each `DashboardChart` owns exactly one Chart.js instance and
destroys it both in the `useEffect` cleanup and in `onWillUnmount`, so no chart
survives a refresh or a navigation.

### 9.3 Real-browser verification

All of the following ran in headless Chrome via `start_tour`, which
authenticates server-side — no credential is typed anywhere.
`tests/test_dashboard_env.py` varies the environment;
`static/tests/tours/rental_dashboard_env_tour.js` asserts against the live DOM
with `getBoundingClientRect()` and `getComputedStyle()`, never against the SCSS.

| Environment | Asserted in the browser |
|---|---|
| 1024×768, 991×768, 768×1024, touch emulation on | no horizontal overflow on `<html>` or on the dashboard; all 20 KPIs present and non-empty; every chart canvas ≥40×40 px and inside the dashboard box; KPI cards ≥120 px wide (proving the grid reflows to fewer columns rather than crushing cards); drilldown still opens a record list |
| Arabic (`ar_001`) session | `getComputedStyle(root).direction === "rtl"`; the `inset-inline-end` drill arrow measured closer to the **left** edge; `margin-inline-start` measured as a computed `margin-right`; charts inside their panels; drilldown works |
| Empty company | no `NaN` / `undefined` / `Infinity` / `∞` / `[object Object]` anywhere in the dashboard text; every ratio KPI reads zero; all 6 chart panels present; all 3 list panels render a real empty state and zero rows |
| Two companies, native switcher | company A's figure recorded, `.log_into` used on company B, gated on the navbar showing "ATMTA Company B" before reading anything, then B's figure asserted non-zero **and different from A's**; drilldown re-checked under B |

**The RTL test found a real bug — and only a browser could have.**

The dashboard root carried `dir="auto"`. `dir="auto"` makes the browser infer
direction from the element's **first strong directional character**; the first
thing this dashboard renders is a Latin word or a digit, so it resolved to
`ltr`. Every logical property below it then resolved LTR, and the whole
dashboard would have rendered left-to-right inside a fully right-to-left Odoo
— for every Arabic user, in production. The stylesheet was correct throughout;
one HTML attribute defeated it. The attribute is removed: direction is now
inherited from `.o_action_manager`, which rtlcss rewrites to `direction: rtl`.

Two supporting facts worth recording, because both are easy to get wrong:

* Odoo's backend puts **no `dir` attribute on `<html>`**. RTL is delivered
  entirely by serving an rtlcss-processed stylesheet. `rtlcss` must be on
  `PATH` or the RTL test fails — correctly, because that is what an Arabic user
  would see.
* rtlcss flips **physical** properties only. Verified directly:
  `margin-left:9px` → `margin-right:9px`, while `margin-inline-start`,
  `inset-inline-end` and `padding-inline` pass through untouched. So logical
  properties are mirrored exactly once, by the browser. There is no double-flip.

Three earlier "failures" in this batch were **fixture** bugs, not product bugs,
and are recorded here so the distinction is not lost: the tests configured
`self.env.user`, which in a `TransactionCase` is OdooBot (uid 1), while
`start_tour(login="admin")` authenticates as `base.user_admin` (uid 2). The
language, the groups and the allowed companies were all being set on a user the
browser never became. The empty-company test's "54.2% occupancy in an empty
company" was this, not a division-by-zero.

### 9.4 Bundle and production-asset gates

`TestDashboardBundleComposition` (introspects `ir.asset._get_asset_paths`):

* the three deprecated V1 files are **not** in `web.assets_backend`
* the six V2 files **are** — so removing V1 cannot silently remove V2
* no Chart.js in `assets_backend`; exactly one copy in `web.chartjs_lib`, at
  `/web/static/lib/Chart/Chart.js`
* no CDN reference in any dashboard asset, so production renders with no
  outbound internet access

`TestDashboardProductionAssets`:

* the non-debug backend bundle URL is `.min.js`, serves 200, and contains
  `RentalDashboard`, `DashboardChart`, `KpiCard` and the client-action key
  after minification
* the V1 fingerprint (`properties_by_city`) is **absent** from that bundle
* the dashboard tour passes after deleting the backend bundle attachments —
  the first-request-after-deploy path, in a browser with an empty profile

### 9.5 Release gates

| # | Gate | Proved by | Result |
|---|---|---|---|
| 1 | Backend regression | `atmta_leasing` | ✅ |
| 2 | Front-end unit suite (HOOT) | `TestRentalDashboardUnits` | ✅ |
| 3 | Dashboard integration tour | `TestRentalDashboardTour` | ✅ |
| 4 | 14-module upgrade | all modules upgraded together | ✅ |
| 5 | Drilldown action contract | `TestDashboardActionContract` | ✅ |
| 6 | Tablet 1024 / 991 / 768 | `TestDashboardTablet*`, real viewports | ✅ |
| 7 | RTL, Arabic session | `TestDashboardRTL` | ✅ |
| 8 | Empty company | `TestDashboardEmptyCompany` | ✅ |
| 9 | Multi-company + native switcher | `TestDashboardMultiCompany` | ✅ |
| 10 | Chart.js / bundle composition | `TestDashboardBundleComposition` | ✅ |
| 11 | Production minified assets, cold cache | `TestDashboardProductionAssets` | ✅ |

```
299 tests, 0 failed, 0 errors
--test-tags=atmta_leasing,atmta_dashboard_js,atmta_dashboard_tour,
            atmta_dashboard_env,atmta_dashboard_assets
```

### 9.6 Deferred to 0.5

| Item | Why not in 0.4 |
|---|---|
| Dashboard filters (date range, property type, building, manager) | Filters change the meaning of every KPI **and** every drilldown domain at once. `get_data()` takes no arguments and no partial filter surface was left behind — there is no half-built control to finish, which is the point. |
| Delete the deprecated V1 front end | `static/src/{js,xml,scss}/rental_dashboard.*` stay on disk for one release so 0.4 can be rolled back. They are proven absent from every bundle and from the minified production asset. **Delete in 0.5**, along with §5b of `UPGRADE_0.4.md`. |

---

## Files

```
models/     22 new + 5 modified      ~11,100 LOC
views/       6 new                    ~4,100 LOC
security/    2 new + 1 modified       49 new ACL rows, 19 record rules, 5 groups
data/        2 new                    7 sequences, 4 scheduled actions
migrations/  0.4/pre + 0.4/post       non-destructive, ambiguity-logging
tests/      22 modules                299 tests, ~4,000 LOC
static/     dashboard V2 front end    4 JS + 1 XML + 1 SCSS; HOOT suite + 5 tours
docs/       UPGRADE_0.4.md            operator + developer guide
```
