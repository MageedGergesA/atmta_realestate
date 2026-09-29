# atmta_real_estate — Phase 0 Audit Report

**Scope:** Full inventory of `atmta_real_estate` + dependency map to the 13 downstream ATMTA modules.
**Purpose:** Establish a safe change baseline for the enterprise upgrade (Phases 1–35).
**Rule:** No production code changes until this report is reviewed and phasing is approved.
**Date:** 2026-08-04.

---

## Part A — Module inventory (atmta_real_estate itself)

### A.1 Manifest / structure
- Odoo 18 module, LGPL-3, `application=True`, version 0.3.
- `depends`: `base, product, mail, sale, sale_stock, account, stock, web_hierarchy`
- External python deps: none (the `hijridate` dependency was withdrawn in 0.12
  together with the two Hijri date fields it served).
- `post_init_hook`: `_sync_property_code_sequence` + `_backfill_unit_stock`.
- 28 Python modules (models + wizard + rental_dashboard abstract), 8 OWL/JS assets (Leaflet, Chart.js, rental dashboard, properties map, property_map widget), 20+ QWeb views/actions/reports.

### A.2 Models (canonical)
| Model | Description | State-field? |
|---|---|---|
| `realestate.property` | Property master (product.template inherits) — hierarchy compound→building→floor→unit→room | ✅ single `state` (mixes 4 concepts, see B) |
| `property.type` | Taxonomy | – |
| `property.usage` | Taxonomy | – |
| `property.image` | Media | – |
| `realestate.maintenance.request` | Maintenance workorder | ✅ 5 values |
| `realestate.contract` | Rental contract | ✅ 7 values (includes `invoiced`) |
| `contract.type` | Taxonomy | – |
| `realestate.contract.line` | Multi-property contract line | ✅ mirrors contract |
| `realestate.payment.plan` | Frequency rule (day/week/month/year) | – |
| `realestate.contract.payment` | Scheduled payment obligation | ✅ 4 values (computed from move_id) |
| `realestate.contract.payment.line` | Additional charge on payment | – |
| `realestate.contract.increment.rule` | Rent increase / discount rule | – |
| `realestate.contract.utility.line` | Utility line | ✅ 2 values |
| `realestate.property.rental.history` | Rental history denormalized | – |
| `realestate.report.contract` | SQL view (pivot) | – (auto=False) |
| `realestate.report.contract.line` | SQL view (pivot) | – (auto=False) |
| `realestate.account.tools` | Accounting helper (AbstractModel) | – |
| `realestate.rental.dashboard` | Dashboard data provider (AbstractModel) | – |
| `realestate.contracts.wizard` | PDF wizard | – (TransientModel) |
| `organization.type` | res.partner taxonomy | – |

### A.3 States authoritatively owned by this module

**`realestate.property.state`** (7 values):
`available / reserved / rented / sold / maintenance / inactive`
Mixes: commercial (available/reserved/sold), occupancy (rented), operational (maintenance), lifecycle (inactive). This is the Phase 2 refactor target.

**`realestate.contract.state`** (7 values):
`draft / ready / confirmed / invoiced / active / expired / terminated`
Mixes lease lifecycle with billing status (`invoiced`). This is the Phase 8 refactor target.

**`realestate.contract.deposit_state`** (5 values):
`none / held / refunded / forfeited / partial_refund`
Single-field pseudo-lifecycle. This is the Phase 14 refactor target.

**`realestate.contract.payment.state`** (4 values, computed from `move_id.state`):
`draft / invoiced / paid / cancelled`
Fully derived — direct writes are lost. Only external consumer with a hard-coded whitelist is `real_estate_api/controllers/api_v1_portal.py:361-363` (see C.4).

**`realestate.maintenance.request.state`** (5): `draft / scheduled / in_progress / done / cancelled`.

### A.4 Confirmed bugs (severity-classified after verification)

| # | Severity | Bug | File:line | Verified? |
|---|---|---|---|---|
| 1 | 🔥 High | `re_is_realestate` written on `account.payment.term` from `atmta_real_estate` without depending on `real_estate_developer` (where the field is defined) — raises `ValueError` when developer isn't installed | `contract.py:246` | ✅ Verified with grep. Field lives at `real_estate_developer/models/account_payment_term.py:16` |
| 2 | 🔥 High | `_on_state_change` `@api.onchange` writes to DB (mutates `property_id.state`) — v18 violation | `contract_line.py:129` → `_update_property_state` | ✅ Confirmed |
| 3 | 🔥 High | `_sync_contract_history` runs on EVERY `write()` and unlinks/recreates all rental_history rows | `contract.py:347` | ✅ Confirmed |
| 4 | 🔥 High | `_compute_totals` swaps basis at invoicing time (pre-invoice: scheduled amount; post-invoice: amount_total incl. tax) — discontinuous dashboards | `contract.py` `_compute_totals` | ✅ Confirmed |
| 5 | ⚠️ Med | `_compute_utilities` is NOT stored but feeds `net_income` = `total_paid` (stored) − `paid_utilities` — stale utility values | `contract.py:164` | ✅ Confirmed |
| 6 | ⚠️ Med | Two crons doing the same job: `_cron_update_line_statuses` + `cron_update_contract_line_states` | `data/contract_line_expiry_cron.xml` | ✅ Confirmed |
| 7 | ⚠️ Med | `cron_auto_invoice_due_payments` is active but the method is a no-op (deprecated) | `rental_invoicing.py` | ✅ Confirmed |
| 8 | ⚠️ Med | Missing ACLs for `contract.type`, `organization.type`, `property.usage` — dropdowns 500 for non-admin | `security/ir.model.access.csv` | ✅ Confirmed |
| 9 | ⚠️ Med | `next_available_date` is a bare `fields.Date` — no compute, no cron. Some downstream reads it as truth. | `_base_property.py` | ✅ Confirmed |
| 10 | ⚠️ Med | Duplicate constraints: SQL `unique(name)` + `@api.constrains` `_check_unique_code` on property, contract, contract_payment | Multiple | ✅ Confirmed |
| 11 | 💡 Low | `_()` wrapped in Selection labels — v18 anti-pattern | `_base_property.py:158-166` (ac_type) | ✅ Confirmed |
| 12 | 💡 Low | `print()` in production code | `wizard/realestate_contracts_wiz.py` `_get_report_values` | ✅ Confirmed |
| 13 | 💡 Low | CamelCase field name `property_Attachment_media_ids` (capital A) | `_base_property.py` | ✅ Confirmed |
| 14 | 💡 Low | 4 orphan report XMLs not in manifest (`report_financial_summary`, `report_payment_schedule`, `report_contract_agreement`, `report_contract_full`) | `reports/*.xml` | ✅ Confirmed — but 3 of these ARE called by `real_estate_api/controllers/api_v1_downloads.py:713-765` — **they are DEAD in the module but ACTIVE consumers via the API** (matches the earlier 404 rental-report issues) |
| 15 | 💡 Low | `reports/__init__.py` intentionally comments out `contract_excel_report` (which has its own xlsxwriter typo bug). File is dormant, not broken today. | `reports/__init__.py` | ✅ Confirmed |

**Retracted findings** (agent report claimed these; verification shows FALSE):
- ❌ "wizard/__init__.py is empty" — file contains `from . import realestate_contracts_wiz`. Verified with cat.
- ❌ "`re_is_realestate` field truly undefined" — reclassified to #1 above (cross-module dependency leak, different bug).

### A.5 Missing enterprise capabilities

- **No `company_id` field on ANY model.** No `check_company=True` on any relational. No multi-company record rules. Phase 28 target.
- **No `@api.constrains`** at all on: `contract_deposit`, `payment_plan`, `increment_rule`, `utility_line`, `maintenance_request`, `rental_history`.
- **No overlap protection** for contracts on the same property (agents 2 and 3 both confirm: no downstream module enforces it either — greenfield).
- **No `mail.thread` chatter** on `contract_payment`, `utility_line`, `payment_line`, `rental_history` (money moves silently).
- **`per-contract`-generated `account.payment.term`** — creates one `account.payment.term` per contract with N `(0,0,{...})` percent lines matching each scheduled payment (`contract.py:_build_rent_payment_term`, ~L246). Elegant but creates schema drift when contract is regenerated.
- **`action_register_payment` on contract.payment** derives inbound/outbound from `move_type` (out_invoice → inbound) — no hard-coded bug. This contradicts the earlier hunch — **rental collection IS correctly modeled as inbound customer payment**.

### A.6 Assets / duplication risks
Module bundles Leaflet + Leaflet.markercluster + Chart.js UMD. Chart.js is already in Odoo 18's `web.assets_backend` (`chart_umd`). **Duplicate-load risk.**

---

## Part B — Downstream references to `realestate.property`

### B.1 Modules with ZERO coupling (safe to ignore for property refactor)
- `real_estate_construction`
- `real_estate_contract_template`
- `real_estate_investment`

### B.2 Modules that WRITE `realestate.property.state` (contested writers)

Only two modules ever write to `property.state` today:

| Module | File:Line | Write | New home (Phase 2) |
|---|---|---|---|
| real_estate_developer | `reservation.py:89` | `state='reserved'` | `commercial_status='reserved'` |
| real_estate_developer | `reservation.py:140,158` | `state='available'` | `commercial_status='available'` |
| real_estate_developer | `sale_contract.py:100` | `state='reserved'` | `commercial_status='reserved'` |
| real_estate_developer | `sale_contract.py:161` | `state='sold'` | `commercial_status='sold'` |
| real_estate_developer | `sale_contract.py:174` | `state='available'` | `commercial_status='available'` |
| real_estate_brokerage | `transaction.py:135` | `state='sold'` | `commercial_status='sold'` |

The base module itself writes `state='rented'` in `contract.py:_action_activate` (single-property mode) — that's a Phase 2 candidate for `occupancy_status='occupied'` write.

### B.3 Parallel-status fields already added downstream (collapse targets)

| Module:Field | Purpose | Collapses into |
|---|---|---|
| `real_estate_developer.sale_status` (not_listed/for_sale/reserved/under_contract/sold) | Sales pipeline status | `commercial_status` |
| `real_estate_developer.construction_status` (planning/under_construction/ready/delivered) | Construction milestone | `construction_status` (already named) |
| `real_estate_brokerage.for_sale` (Boolean) | Whether listable | `commercial_status` |
| `real_estate_brokerage.is_sold` (Boolean, from transactions) | Whether sold | `commercial_status` |
| `real_estate_developer.is_sold` (Boolean, from contracts) — **name collision with brokerage** | Whether sold | `commercial_status` — **must unify** |
| `real_estate_handover.handover_ready / ready_to_deliver / ready_to_move` | Handover readiness | `handover_status` |
| `atmta_real_estate.occupancy_state` (available/partial/rented, computed) | Occupancy | `occupancy_status` (verbatim rename) |

**Collision alert:** `is_sold` is declared on `realestate.property` by BOTH developer and brokerage. Whichever module loads last wins the compute. Any refactor must unify these into a single `commercial_status` sourced from both.

### B.4 Stored `related` fields that shadow `property.state`

- `real_estate_plan/models/plan_region.py:38-40` — `target_state = fields.Selection(related='target_property_id.state', store=True)`. **If we change the `state` selection tuple values, every stored `target_state` needs a `_recompute` pass.** This is why we keep `state` as a real field that becomes a computed-store bridge.

### B.5 Code that reads/serializes raw `property.state` (visitor-facing)

Renaming `state` values without a compat layer would silently break these:

| Location | What breaks |
|---|---|
| `real_estate_portal/controllers/public.py:171-172,237` | JSON labels via `_fields['state'].selection` |
| `real_estate_api/controllers/api_v1_map.py:210-217,235` | `?state=` query param echo |
| `real_estate_api/models/realestate_property.py:29`, `api_v1_map.py:45` | Hard-coded `PUBLIC_PROPERTY_STATES=('available','reserved','sold')` |
| `real_estate_api/controllers/api_v1_catalog.py:230,241` + `api_v1_embed_token.py:61` + `api_v1_interest.py:101` | Inline `not in ('available','reserved','sold')` gates |
| `real_estate_maquette/models/unit_picker.py:24` | `unit_state = related='unit_id.state'` — narrowing the base selection breaks existing DB values |
| `real_estate_maquette/models/project_maquette.py:143`, `building_preview.py:119-125`, `building_floor.py:75-81` | Filter unit lists by raw state values |

### B.6 Domain filters using `property.state`

| Location | Domain |
|---|---|
| `real_estate_developer/models/developer_dashboard.py:27,63` | `('state','=','available')`, iterates 5 states |
| `real_estate_brokerage/models/lead.py:109` | `('property_id.state','=','available')` |
| `real_estate_api/controllers/api_v1_map.py:195,217` | `('state','in', PUBLIC_PROPERTY_STATES)` |

Plus the base method `_check_available_for_new_sale()` (`_base_property.py:106-114`) — called by `real_estate_developer.reservation.py:81` and `real_estate_brokerage.listing.py:189`. **The base method is the choke-point** — we keep the method name and semantics, replace the internals to use new dimensions. Zero call-site changes downstream.

### B.7 Views inheriting `view_property_form` (6 modules)

xpath anchors that MUST survive Phase 31 UI redesign:
- `//div[@name='status_ribbon']` — developer, handover
- `//div[hasclass('oe_button_box')]` — brokerage, maquette
- `//notebook` — developer, brokerage, handover, maquette
- `//field[@name='parent_id']` — developer
- `//page[@name='media']` — plan

### B.8 Base methods called from downstream (public API)

- `_check_available_for_new_sale()` — `_base_property.py:103`
- `action_mark_for_resale()` — `_base_property.py:89`
- `_get_stock_location()` (hasattr-guarded call in `real_estate_procurement/models/purchase_order.py`)

**Keep these method signatures.**

### B.9 Downstream fields materialized on `realestate.property`

All safe (they add fields, don't rename). New dimensions from Phase 2 must coexist:
- developer: `project_id, phase_id, base_price, sale_status, construction_status, is_sold, reservation_ids, sale_contract_ids, active_reservation_id, reservation_count, sale_contract_count`
- brokerage: `for_sale, is_sold, listing_ids, transaction_ids, active_listing_id, last_transaction_id, listing_count, transaction_count`
- handover: `handover_ready, ready_to_move, ready_to_deliver, readiness_*`
- plan: `plan_image, has_plan_image, plan_region_ids`
- maquette: `maquette_mesh_name, floor_plan_image, interior_glb, elevation_sheet, floor_ids, spec_tag_ids, has_elevation, has_floor_plan_effective, maquette_color_override, image_count`

---

## Part C — Downstream references to `realestate.contract` + `contract.payment`

### C.1 Modules with ZERO coupling to rental contract
- `real_estate_developer` — its own `realestate.sale.contract`, no overlap
- `real_estate_brokerage` — its own `realestate.transaction`
- `real_estate_construction` — different entity (`realestate.contractor`)
- `real_estate_handover` — reads only sale_contract, not rental
- `real_estate_portal` — sale-side only
- `real_estate_maquette` — none
- `real_estate_plan` — none
- `real_estate_customer_service` — none
- `real_estate_investment` — none

### C.2 Reference-only coupling (no state read, no writes)

- `real_estate_procurement/models/material_request.py:159` — `('realestate.contract', 'Rental Contract')` in `_get_source_model_selection` (polymorphic Reference field). Reads only `_name, project_id, property_id`. Zero coupling to state.

### C.3 Contract-template coupling (mixin only)

- `real_estate_contract_template/models/contract_template_inherit.py:69-71` — adds `generate_document_count` compute + 2 pure-UI buttons. No state coupling.
- `real_estate_contract_template/views/contract_form_buttons.xml:25-42` — inherits `view_realestate_contract_form`, needs `<header>` + `<div name='button_box'>` slots to stay.

### C.4 real_estate_checks bridge

- `check.py:72-79` — `rental_contract_id` + `rental_payment_id` (both nullable + `ondelete='set null'` → safe against contract deletion).
- `check.py:132-140` — reads `rental_contract_id.property_id` for auto-derive.
- `check.py:250-253` — reads `rental_payment_id.move_id` for reconciliation.
- `check.py:235-246` — creates `account.payment` with `payment_type='inbound'` + `partner_type='customer'` — **correct for tenant collection**.
- `bulk_check_wizard.py:14` — currently **sale-only** (`sale_contract_id` required). **No rental-side bulk wizard yet** — potential Phase 13 or 14 enhancement.

### C.5 real_estate_api coupling (LARGEST consumer)

- **Reads `contract.state`** at `api_v1_portal.py:246-263` — serializes raw `state` string to API clients. **Public JSON contract exposure — any lifecycle rename requires a compat layer or a migration announcement.**
- **Reads `contract.payment.state`** at `api_v1_portal.py:370-391` — full serialization.
- **Hard-coded whitelist** at `api_v1_portal.py:361-363` — accepts only `('draft','invoiced','paid','cancelled')` as query params. Renaming any of the 4 payment state values silently rejects legitimate queries.
- **Ownership checks** at `api_v1_downloads.py:182,213,244,841-848` — always via `contract.partner_id`.
- **Reports called via API** — 3 that were previously 404'd (Section A.4 #14):
  - `atmta_real_estate.report_payment_schedule`
  - `atmta_real_estate.report_financial_summary`
  - `atmta_real_estate.report_contract_full`
  These are orphan report XMLs — the API calls them by xmlid but the manifest doesn't load them. **This IS the earlier 404 root cause.** Fix in Phase 24 (reports section) — either load them or remove the API callers.

### C.6 Reports rendering rental contract data

All internal to `atmta_real_estate` except `real_estate_api/reports/partner_statement_report.xml`. The API report is tolerant (uses `t-field`, not raw string comparison).

### C.7 `partner_id` — no rename possible

The primary tenant field is `partner_id` on `realestate.contract`. Every downstream domain filter uses `partner_id` verbatim. **Renaming would break every API endpoint.** Preserve the name; new party model (Phase 7) adds side-table.

### C.8 `start_date` / `end_date` — greenfield for overlap protection

No downstream module enforces overlap. Only consumer that filters by dates is the dashboard (`rental_dashboard.py:155-166`). **Adding overlap `@api.constrains` in Phase 5 is greenfield** — no compat concerns.

### C.9 Single-property vs multi-property patterns

- Contract has BOTH `property_id` (single) AND `line_ids` (multi), gated by `is_single_property` / `is_multi_property` Booleans.
- Downstream single-property reads: `real_estate_checks/check.py:139-140` and `real_estate_api/api_v1_downloads.py:840-844`.
- **No downstream module reads `line_ids` or iterates `contract_payment_ids[].property_id`.** — Multi-property internal restructure (Phase 6) is safe.

### C.10 `action_register_payment` / `action_generate_payment_lines` / `action_generate_payment_schedule`

- Defined only in `atmta_real_estate/models/contract_payment.py:201` and `contract.py:172,375`.
- Called only from `atmta_real_estate/models/contract.py:174` and `views/contract_views.xml:9` button.
- **Zero downstream callers.** Safe to rename or restructure in Phase 9-15.

### C.11 Modules with their own contract-like `state` (not overlapping)

Each is a distinct model:
- `real_estate_brokerage.transaction.state` (draft/deposit_received/contract_signed/closed/cancelled)
- `real_estate_handover.snagging_issue.state`
- `real_estate_construction.construction_task.state`
- `real_estate_checks.check.state`
Zero coupling to rental contract state.

---

## Part D — Architectural conflict map + safe migration path

### D.1 Compatibility architecture (recommended)

For every state redesign (Phases 2, 8, 14), use the **compat-bridge pattern**:

```
1. NEW dimension field is authoritative (e.g. commercial_status)
2. LEGACY state stays as a stored-compute field:
      state = fields.Selection(..., compute='_compute_legacy_state', store=True, readonly=True)
   Its compute derives from the new dimensions in a well-defined mapping.
3. Setters that used to write state=… are updated once:
      • base module: writes new dimensions directly
      • downstream: gets a single _transition_commercial_status(new) helper that
        internally writes the correct dimension. Legacy write() to state raises
        DeprecationWarning but is still routed to the new dimension.
4. Selection tuple of legacy `state` stays UNCHANGED (all 7 values remain).
   This preserves plan_region.target_state and every API JSON contract.
5. Deprecation period: 12 months. After that, we may remove the compat writer
   (but keep the field forever — it's a stored compute).
```

This is the ONLY approach that preserves:
- `real_estate_plan.plan_region.target_state` (stored related)
- `real_estate_api` JSON contracts on `state` field
- `real_estate_maquette.unit_picker.unit_state`
- All 3 downstream domain-filter modules
- The 6 view-xpath extensions

### D.2 Phase-execution risk-ranked order

Given the audit, this is my recommended re-ordering for **minimum-risk-first**:

**Wave 1 — Zero-downtime bug fixes** (do these FIRST, before any refactor, as a small standalone commit):
- 1a. Fix `re_is_realestate` cross-module leak (add hasattr guard in `contract.py:246`, don't add the field)
- 1b. Remove/deactivate duplicate cron (`ir_cron_contract_line_expiry` OR `ir_cron_update_contract_line_status`)
- 1c. Deactivate deprecated `cron_auto_invoice_due_payments` (`active=False`)
- 1d. Add missing ACLs for `contract.type`, `organization.type`, `property.usage`
- 1e. Move `_on_state_change` DB-write out of `@api.onchange` into `write()` override
- 1f. Delete `print()` statements in wizard
- 1g. Rename `property_Attachment_media_ids` → `property_attachment_media_ids` (deprecate old via `_columns_stored_new_name` bridge)

Total effort: ~1 day. Zero refactor. Ships as `atmta_real_estate v0.3.1`.

**Wave 2 — Compat-bridge foundation** (Phases 1, 2, 3, 28, 29):
- Property master hardening + status dimensions (with legacy state as computed bridge) + IDs + multi-company + auditability.
- No new business models yet.
- Downstream modules re-tested; developer + brokerage writers updated to write new dimensions.
- Effort: ~1 week.

**Wave 3 — Availability + overlap** (Phases 4, 5, 7):
- Availability engine (computed from active contracts + explicit override) + overlap protection + contract parties.
- Effort: ~4 days.

**Wave 4 — Lease lifecycle + contract lines** (Phases 6, 8):
- New `lifecycle_state` on contract with legacy `state` as bridge. Property lines model.
- Effort: ~1 week.

**Wave 5 — Financial engine** (Phases 9-16):
- Charge rules + escalation + incentives + proration + deposits + accounting audit + arrears.
- Effort: ~2 weeks.

**Wave 6 — Workflows** (Phases 17-23):
- Renewals + amendments + termination + move-in/out + meters normalization.
- Effort: ~1.5 weeks.

**Wave 7 — Enterprise polish** (Phases 24-27, 30-35):
- Rent roll + boards + dashboard V2 + security + crons + UI + performance + tests + migration + compat validation.
- Effort: ~2 weeks.

**Total realistic effort: 7-9 weeks of focused senior work.**

### D.3 Risks that must be flagged before Wave 2 starts

- **JSON contract preservation** — the API serializes raw `state` strings on rental contract + rental payment. Any customer using our REST API today will break if these change without a compat layer. **Need to decide: do we version the API (v2) or keep v1 forever?**
- **Multi-company migration** — adding `company_id` to existing tables requires a data-fill script. If existing prod DBs have contracts spanning multiple companies (via partners), we need a mapping decision.
- **`plan_region.target_state` stored-related recompute** — after Phase 2, all existing plan_region rows need `_recompute` triggered.
- **The 3 API-called-but-not-manifest-loaded reports** (`report_payment_schedule`, `report_financial_summary`, `report_contract_full`) are producing 404s in production right now (verified in prior session). Wave 1 should add them to manifest OR remove the API callers.

### D.4 Two decisions I need before Wave 2

**Decision 1 — API compatibility posture**
Options:
- (a) Freeze current `state` values forever — new dimensions live alongside, legacy `state` stays as computed bridge, JSON contract is preserved indefinitely.
- (b) Version the API — introduce `/api/v2/` with new lifecycle_state values, mark v1 deprecated with a 6-month sunset.
- (c) Keep v1 forever, expose dimensions as new fields in the same v1 payload — JSON grows but doesn't break.

**Decision 2 — Existing production DB migration**
Options:
- (a) Non-destructive only — legacy `state` stays authoritative for existing records; new dimensions computed. Old records get their new dimensions computed from `state`.
- (b) Full migration — data-fill script runs once, sets new dimensions from mapping table, then `state` becomes compute-bridge for future.
- (c) Both — new records use new dimensions natively; old records keep legacy `state` as truth via a `_use_legacy_state` flag.

---

## Appendix — files I need to touch in Wave 1 (bug fixes)

| File | Change |
|---|---|
| `atmta_real_estate/models/contract.py:246` | Guard `'re_is_realestate' in AccountPaymentTerm._fields` before adding to vals |
| `atmta_real_estate/models/contract_line.py:129` | Move DB-write out of `@api.onchange` into `write()` override |
| `atmta_real_estate/data/contract_line_expiry_cron.xml` | Delete/deactivate duplicate cron |
| `atmta_real_estate/data/auto_invoice_cron.xml` | Set `active=False` on deprecated cron |
| `atmta_real_estate/security/ir.model.access.csv` | Add ACL rows for contract.type, organization.type, property.usage |
| `atmta_real_estate/wizard/realestate_contracts_wiz.py` | Remove `print()` statements |
| `atmta_real_estate/models/_base_property.py` | Rename `property_Attachment_media_ids` (deprecate old name via compat) |
| `atmta_real_estate/__manifest__.py` | Add the 3 orphan reports to manifest (fixes API 404) |

Approx 40 lines of surgical changes across 8 files. No new models. Zero downstream impact.

---

## Pause point

**Do not proceed to Wave 2 without your approval on:**
1. The two decisions in D.4
2. The wave-ordering in D.2 (vs the user's original 35-phase sequence)
3. Whether Wave 1 quick-wins should ship as a standalone commit BEFORE the refactor waves
