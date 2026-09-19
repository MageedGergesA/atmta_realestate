# `atmta_real_estate` 0.3 → 0.4 — Enterprise Property Master + Lease Administration

This document is the operational companion to the upgrade. It covers what
changed, what a downstream developer needs to know, and what an administrator
must configure before the new capabilities work.

---

## 1. The one thing to understand

The pre-upgrade module had **two overloaded fields** that everything hung off:

| Field | What it conflated |
|---|---|
| `realestate.property.state` | commercial availability + rental occupancy + maintenance + archival |
| `realestate.contract.state` | lease lifecycle + billing milestone |

Both are now **computed compatibility bridges** over a set of orthogonal
dimensions. They are still readable, still searchable, and writes to them are
translated into the dimension the writer actually owns.

**If you maintain a downstream module: you do not have to change anything.**
`prop.state = 'reserved'` still works. But new code should write the dimension
it owns.

### Property status ownership

| Dimension | Owned by | Rental may write it? |
|---|---|---|
| `commercial_status` | developer / brokerage | ❌ |
| `occupancy_status` | **computed** from lease allocations | ❌ (derived) |
| `construction_status` | construction / developer | ❌ |
| `handover_status` | handover | ❌ |
| `maintenance_status` | facilities / maintenance requests | ✅ |

Legacy `state` writes map as follows — note how narrow each one is, which is
what stops modules clobbering each other:

```
'available'   -> {'commercial_status': 'available'}
'reserved'    -> {'commercial_status': 'reserved'}
'sold'        -> {'commercial_status': 'sold'}
'maintenance' -> {'maintenance_status': 'maintenance'}
'inactive'    -> {'commercial_status': 'blocked'}
'rented'      -> {}   # no-op: occupancy is derived from leases
```

### Lease lifecycle

```
draft → proposal → pending_approval → pending_signature → active → notice → ended
                                                                  ↘ terminated
        ↘ cancelled
```

Carried alongside it, no longer crammed into one field:
`billing_status`, `payment_status`, `signature_status`, `occupancy_status`.

---

## 2. Canonical lease allocations

`realestate.contract.property.line` is now the single record that says "this
lease covers this property for this term at this rent".

The two legacy shapes are **mirrored into it automatically**:

* single-unit (`contract.property_id`) → one allocation, `origin='single'`
* multi-unit (`contract.line_ids`) → one allocation per legacy line,
  `origin='legacy_line'`

Nothing was deleted; both legacy relations still work. The mirror is idempotent
and runs on create, on write, and from the migration.

**Why it matters:** before this, a single-unit lease and a multi-unit lease
could double-book the same property, because their overlap checks were blind to
each other. One constraint now covers every lease shape.

---

## 3. Administrator setup

New capabilities are **off by default**. Nothing in an existing database
changes behaviour until you switch it on.

### Required before deposits work

Settings → Real Estate → Security Deposits:

| Setting | Notes |
|---|---|
| Deposit Liability Account | Must be `liability_current`/`liability_non_current` **and reconcilable**. A deposit is money you owe back — the code refuses a revenue account. |
| Deposit Journal | Bank or cash. |
| Forfeited Deposit Income Account | Only touched at the moment of forfeiture. |

### Required before advanced billing works

1. Settings → Real Estate → Billing → set the **Rent Proration Method**
   (actual days / 30-day month / none / whole periods only).
2. On each lease, tick **Use Advanced Billing**.
3. Optionally enable **Automatic Rent Invoicing** *and* activate the
   `cron_generate_rent_invoices` scheduled action — it ships **inactive**,
   because turning on automatic invoicing changes what lands on customers'
   ledgers and should be a deliberate decision.

### Optional

* **Require Lease Approval** — forces the `pending_approval` step.
* **Allow Self-Approval** — off by default; a user cannot approve a lease they
  created. Override per-user with the "Allow Self-Approval" group.
* **Renewal Reminder Windows** — default `180,120,90,60,30` days.

---

## 4. Security roles

```
Read-only
  └─ Rental User          view properties, leases, obligations
       └─ Leasing Agent   proposals, parties, charges, incentives, renewals, schedules
            └─ Property Manager   activate leases, availability overrides, move-in/out, turns
                 └─ Rental Manager   approve commercials, terminate, refund/forfeit deposits
```

The legacy Read-only / User / Manager groups are untouched and still grant
everything they used to.

**No leasing role grants accounting management.** A Rental Manager can raise an
invoice through the lease workflow; their access to journals, reconciliation
and accounting reports is whatever Odoo Accounting gives them.

Every workflow action is gated **server-side**. A hidden button is not security.

---

## 5. Migration

`migrations/0.4/pre-migrate.py` snapshots `state` on properties, leases and
legacy contract lines into `re_legacy_state_backup` columns **before** the
schema changes — because those fields become stored computed fields, and Odoo
would otherwise recompute them from defaults and destroy the originals.

`migrations/0.4/post-migrate.py` then:

1. maps property `state` onto the status dimensions
2. maps lease `state` onto `lifecycle_state`
3. seeds `usage_category` from property-type names (unambiguous matches only)
4. builds canonical allocations from both legacy lease shapes
5. normalises the flat meter columns into meter records
6. creates deposit records for leases already holding a deposit

**The backup columns are deliberately kept.** They cost almost nothing and make
the mapping auditable and re-derivable.

**Nothing is invented.** Where legacy data cannot determine a new value —
`construction_status` and `handover_status` are the clear cases, since the old
field carried no information about either — the record keeps its default and
the migration logs it. Check the upgrade log for `WARNING ... post-migrate` to
see everything that could not be classified.

---

## 5b. Rental Dashboard — V1 rollback and the 0.5 cleanup

The V2 front end replaced V1 in place. The V1 files are **still on disk**:

```
static/src/js/rental_dashboard.js
static/src/xml/rental_dashboard.xml
static/src/scss/rental_dashboard.scss
```

They are **not** in `web.assets_backend` and must not be re-added: V1 registers
the same `realestate.rental_dashboard` client-action key as V2, and reads
payload keys (`property_states`, `revenue_trend`, `top_tenants`,
`properties_by_city`, `map_props`, `hierarchy`) that `get_data()` stopped
returning in 0.4. `TestDashboardBundleComposition` fails the build if any of
them reappears in a bundle, and the production-assets test additionally proves
the V1 fingerprint is absent from the minified bundle a browser receives.

To roll back to V1 you would re-add those three paths to the manifest and
revert `models/rental_dashboard.py` — which is the only reason they are kept.

> **0.5 cleanup item:** delete the three deprecated Rental Dashboard V1 front-end
> files and this section. One release of rollback cover is enough.

Chart.js is **not** vendored by this module. `DashboardChart` lazy-loads Odoo's
own `web.chartjs_lib` bundle, the same way the graph view does. Do not add a
copy back — see the implementation report §9.2.

---

## 6. What is deliberately NOT here

Per the scope boundary, these belong to other modules and only have hooks here:

developer sales pricing · unit sale reservation · brokerage commissions ·
construction · BOQ · procurement · full collections case management ·
facilities management · owner association · Saudi Wafi · Ejar · CRM
replacement · any custom accounting ledger or second payment engine

`realestate.unit.turn` is a minimal operational object with a late-bound hook
(`_optional_model`) so a future FM module can extend it. This module never
imports a downstream module.

---

## 7. Running the tests

Everything at once:

```bash
python3 odoo-bin -c odoo.conf -d <db> -u atmta_real_estate --test-enable \
    --test-tags=atmta_leasing,atmta_dashboard_js,atmta_dashboard_tour,\
atmta_dashboard_env,atmta_dashboard_assets --stop-after-init
```

Or by layer:

| Tag | What it runs | Needs |
|---|---|---|
| `atmta_leasing` | the whole Python business suite | — |
| `atmta_dashboard_js` | the HOOT front-end unit suite | headless Chrome |
| `atmta_dashboard_tour` | the dashboard integration tour | headless Chrome |
| `atmta_dashboard_env` | tablet viewports, Arabic RTL, empty company, multi-company switching | headless Chrome, **`rtlcss` on `PATH`** |
| `atmta_dashboard_assets` | bundle composition, Chart.js ownership, production minified assets, cold cache | headless Chrome |

Everything is tagged `post_install`, so pass `-u atmta_real_estate` (or `-i`).

### `rtlcss` is required for the RTL test

Odoo's backend puts **no `dir` attribute on `<html>`**. Right-to-left is
delivered by serving an rtlcss-processed stylesheet, which rewrites
`.o_action_manager { direction: ltr }` to `rtl`; everything below inherits it.
Without `rtlcss` on `PATH`, Odoo silently serves the LTR bundle and
`TestDashboardRTL` fails — correctly, because that is what an Arabic user would
see.

```bash
npm install --prefix ~/.npm-rtl rtlcss
export PATH=~/.npm-rtl/node_modules/.bin:$PATH
```

**Consequence for anyone writing views here:** never set `dir` on a container.
An explicit `dir="auto"` makes the browser guess the direction from the first
strong directional character in the element, so a Latin label or a digit pins
the whole subtree to LTR inside an otherwise RTL session. Inherit the direction
Odoo establishes, and use CSS *logical* properties (`margin-inline-start`,
`inset-inline-end`) — rtlcss deliberately leaves those alone, so the browser
mirrors them exactly once.
