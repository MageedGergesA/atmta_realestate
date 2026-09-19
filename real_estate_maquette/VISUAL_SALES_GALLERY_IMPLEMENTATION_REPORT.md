# ATMTA Interactive Sales Gallery — Implementation Report (FROZEN)

**Modules:** `real_estate_maquette` **0.6**, `real_estate_plan` **0.5**,
`real_estate_portal` **0.2** — frozen
**Treated as:** one product domain
**Frozen dependencies:** `atmta_real_estate`, `real_estate_developer`,
`real_estate_checks`, `real_estate_brokerage` — **no production code
modified**; two test-only clock corrections applied with approval (see
"A frozen-module finding", below)

Phase 0 is `VISUAL_SALES_GALLERY_AUDIT.md`. This report covers what was built
on top of it and, in §26, states plainly what was not.

---

## 1. Audit findings

The audit is a separate document; the four findings that shaped everything
built here:

1. **Commercial truth came from the wrong place.** The viewer coloured units
   from `realestate.property.state` and priced them from `base_price` — a
   legacy field that knows nothing about release batches, blocks or selling
   windows, and the internal figure discounts are measured against.
2. **Failure was not graceful.** WebGL failure, a malformed GLB and a missing
   GLB all ended at a red box containing a Three.js exception message. Rule 4's
   chain did not exist.
3. **No security model.** Zero `ir.rule` records, zero `company_id` fields, and
   `base.group_user,1,1,1,1` on `realestate.plan.region` — every internal user
   could create, edit and delete production polygon mappings.
4. **Nothing was tested.** Both modules together: zero tests.

Two findings were **positive** and shaped what was deliberately left alone:
Rule 1 was already honoured — no competing property master exists — and the
dangerous half of Rule 2 was already right, because the Reserve button opens
Developer's own reservation form, which takes a `pg_advisory_xact_lock` and
re-checks availability inside the transaction.

---

## 2. 2D architecture

Unchanged in shape, which was the correct shape. `realestate.plan.region`
carries a polygon of normalised `[x%, y%]` points from a parent property to a
**direct child** property, with constraints already enforcing ≥3 vertices,
numeric coordinates, the 0–100 range, target ≠ parent, a real parent/child
relationship, and SQL uniqueness per pair.

M4's stated constraint list was therefore already satisfied. What it lacked was
tests and tenancy; both were added. `company_id` is now a stored related on the
parent property, so a region can never disagree with the property it is drawn
on.

---

## 3. 3D architecture

Also unchanged in shape: one GLB per project, `maquette_mesh_name` on the
property as the entire mapping contract, interior GLBs lazy per unit.

What changed is everything around it — validation, publication, capability
detection, disposal and where the commercial data comes from.

---

## 4. Asset ownership and version

**Three.js r160**, one vendored copy in
`real_estate_maquette/static/src/lib/threejs/`, loaded by native dynamic
`import()` only when a viewer mounts. No global `window.THREE`, no second copy
in the suite, no CDN for the library itself. That part was already right and
was left alone.

**The Draco decoder was not.** The viewer did this:

```js
draco.setDecoderPath(`https://unpkg.com/three@0.160.0/examples/jsm/libs/draco/`);
```

Every Draco-compressed model needed the customer's browser to reach unpkg.com —
so on an on-premise install, behind a corporate firewall, or in a GCC network
with restricted egress, the model silently failed.

The decoder binaries are compiled artefacts from Google's Draco project and are
**not in this repository**. Pointing the path at a local directory that does not
exist would have swapped a CDN dependency for a 404, so instead:

- the path is an `ir.config_parameter`
  (`real_estate_maquette.draco_decoder_path`) defaulting to a module-relative
  location;
- the server checks whether it is actually installed;
- a model that needs Draco when the decoder is absent is a **critical**
  validation issue and cannot be published.

A deployment that never uses Draco is unaffected and need do nothing. One that
does now gets told, at upload time, by an administrator rather than by a
customer.

---

## 5. Validation and publishing

Uploading a file is no longer a deployment.

```
    DRAFT ──▶ VALIDATING ──▶ READY ──▶ PUBLISHED ──▶ ARCHIVED
      ▲                                    │
      └────────── asset replaced ──────────┘
```

The validator parses the GLB **server-side** — container header plus the JSON
chunk — with no new Python dependency: the format is a 12-byte header and
length-prefixed chunks, and reading that much of it is thirty lines. It records
what the asset is (mesh, node, material, texture and image counts; glTF version;
generator; file size; extensions) and cross-checks it against inventory.

Replacing `maquette_glb` on a live project returns it to draft, because the
existing mapping was validated against a different file.

**Severity decides what blocks.** Only `critical` stops publication; a validator
that blocks on everything gets switched off. Unmatched meshes are a warning — a
model legitimately contains lift shafts, landscaping and parked cars.

---

## 6. Mapping QA

The dashboard M3 asked for now has a data source:

| Measure | Severity when wrong |
|---|---|
| GLB meshes / properties / matched | informational |
| Mesh mapped to a name not in the model | **critical** — the click does nothing |
| Two units claiming one mesh | **critical** — the click is ambiguous |
| Unmatched meshes | warning |
| Units without a mesh | warning |
| Units without a price | warning |
| Units with no parent | warning |

Findings are rows, not a blob, so they can be listed, filtered and resolved
individually. A re-validation marks superseded findings `resolved` rather than
deleting them, so "this was broken last Tuesday" stays answerable.

One thing the audit expected to find broken turned out to be defended already:
`property_maquette.py` carries a constraint rejecting a duplicate mesh name
within a project, so ordinary authoring cannot create an ambiguous click at all.
That is pinned by a test, and the validator's duplicate check is retained as the
last line for data that arrives by SQL import or migration, which does not pass
through `@api.constrains`.

---

## 7. Asset optimisation

**Partial, and honestly so.** Delivered: budget checks (file size, mesh,
material and texture counts), extension support checking against what the
bundled r160 loader can actually decode, and the Draco resolution above.
KTX2/Basis and Meshopt are detected and refused rather than silently failing —
neither decoder is bundled, so a model requiring them is critical.

Not delivered: an optimisation *pipeline*. See §26.

---

## 8. Performance results

**Not measured, and this is a real gap.** The audit established that no
production GLB exists in this environment — `ir_attachment` holds zero rows for
`maquette_glb`, `interior_glb` or `maquette_env_hdr`, and no sample asset is
committed. The brief is explicit that arbitrary pass/fail numbers must not be
invented without understanding project size, so none were.

What was done instead is structural and does not need a benchmark to be correct:

- the whole-project payload is bounded (`GALLERY_PAGE_LIMIT`) and reports when
  it truncates;
- cache-busting moved from `write_date` to a `visual_version` that only changes
  on publish — previously, editing a project's phone number invalidated every
  browser's copy of a multi-megabyte model;
- disposal (§22) removes the leak that made repeated opens progressively worse.

Establishing SMALL/MEDIUM/LARGE tiers requires representative assets. See §26.

---

## 9. Property source of truth

**Rule 1 holds and was already holding.** No `visual.unit`, `maquette.unit` or
`plan.unit` exists. Every visual object resolves to `realestate.property`:
regions point at it, mesh names live on it, floors reference it.

One data-quality risk is recorded rather than changed:
`realestate.building.floor` derives its identity from a *representative unit*
(`floor_number` and `property_usage_id` are stored relateds on `unit_id`, with
`ondelete='cascade'`). A floor is not a unit, and modelling it as one is
fragile. Changing it would be a destructive migration of live rows for no
functional gain in this release, so it is documented, not touched.

---

## 10. Developer integration

The single largest correctness change. One service —
`realestate.visual.commercial` — is now the only place the gallery asks what a
unit is worth and whether it is free:

| Concern | Before | Now |
|---|---|---|
| Availability | `property.state` | `is_available_for_sale` + `sale_unavailable_reason` + `commercial_status` |
| Colour | JS table keyed on legacy state | server table keyed on visual state |
| Price (internal) | `base_price` | `list_price_developer` |
| Price (public/broker) | `base_price` | `_public_price()` — 0.0 for unlaunched inventory |
| Reserve button gate | `u.state !== "available"` | `reservable`, from the same service |
| Reservation itself | Developer's form | unchanged — it was already right |

Developer's eleven `sale_unavailable_reason` codes map down to seven states a
customer can actually distinguish. The mapping is a pure function, tested
exhaustively, and an unrecognised reason falls through to `not_for_sale` —
failing towards "you cannot buy this" is the safe direction.

---

## 11. Brokerage / CRM integration

**Rule 3 holds.** No visual-only lead or shortlist model exists.

Module 4 established `realestate.property.match` as the canonical shortlist,
carrying `shortlisted`, `rank`, `customer_response` and rejection reasons. The
gallery writes those rows — a gallery favourite and an agent's shortlist entry
are the same thing.

`real_estate_maquette` does **not** depend on `real_estate_brokerage`, and
making a 3D viewer require the whole brokerage engine would be worse than the
problem it solves. So the integration is late-bound: the service asks whether
the model is in the registry and uses it when it is, the same defensive pattern
Brokerage itself uses to read portal and API fields it does not depend on.

Favouriting an already-scored match keeps the score. An anonymous visitor's
session shortlist merges once, idempotently, and one bad id does not lose the
rest of somebody's list.

---

## 12. Reservation path

Unchanged, because it was already correct: the viewer opens
`realestate.unit.reservation` with `default_property_id`, and Developer's
`create()` takes a `pg_advisory_xact_lock` on the property before re-checking
availability. Two sessions racing for one unit are decided there.

What changed is the button's own gate, which read `property.state` and so
offered Reserve on unreleased and blocked units — the customer committed to a
click and Developer's form then refused them. It now reads `reservable` from
the same service that colours the mesh, so the button and the colour cannot
disagree.

---

## 13. Search and filter

**Delivered (P0).** Thirteen filter criteria — availability, project, building,
type, bedrooms, bathrooms, area, floor, price — each declared as data mapping to
a domain term, so what the UI offers and what the server honours cannot drift.

Filtering is a **domain**, answered by the database. The client is told which
ids matched; it is never handed a second copy of the pricing to filter itself.
Non-matching units are returned and marked, because a customer looking at a
tower wants to see that the floor above is taken.

Facets (price/area/floor ranges, bedroom counts, types present, states present)
are built from the units that exist, so a project with no villas offers no villa
filter. An unknown filter key is ignored rather than fatal: a stale client
should get a broader result set, not an error dialog in front of a customer.

---

## 14. Shortlist

Covered in §11.

---

## 15. Compare

2–4 units, rebuilt from the live service on every call — the brief forbids
comparing stale snapshots after a commercial refresh, and a test asserts that a
unit blocked thirty seconds ago compares as blocked. Rows mark the best value
only where "better" is unambiguous: lower price per square metre is marked, a
higher floor is not.

---

## 16. Presentation mode

**Deferred.** See §26.

---

## 17. Public / embed mode

**Partial.** The service layer is audience-aware and tested: a public caller
gets `_public_price()` (0.0 for unlaunched inventory) and never receives
`unavailable_reason`, `commercial_status` or `is_released` — "Blocked — VIP hold
for Mr X" is exactly what M20 forbids. An unrecognised audience is treated as
public, failing towards the least privileged reading.

`visual_public_enabled` defaults to **off**: a project becomes publicly
embeddable because somebody decided it should, not because a file was attached.

**The unauthenticated asset route is not fixed.** `real_estate_api`'s
`/api/v1/image/<model>/<id>/<field>` serves `maquette_glb`, `interior_glb`,
`plan_image`, `floor_plan_image` and `elevation_sheet` with `auth='public'`,
`sudo()` and sequential integer ids — no token, and gated on legacy
`property.state`. This is a real security finding, it is documented in the
audit, and it is **outside these two modules**. It is not blocked by the frozen
rule (`real_estate_api` is not frozen), but changing a public API contract is a
decision with consequences beyond this domain. See §26.

---

## 18. Broker authorisation

**Deferred.** The service already treats `broker` as an external pricing
audience, so a broker never sees the internal list price. Consuming Brokerage's
agreement/allowed-project authorisation is not built. See §26.

---

## 19. Security

Four roles, because the audit found none and the alternative was one ACL row
making every employee a production visual author:

```
    Viewer     — sees published galleries
    Author     — uploads assets, draws polygons, maps meshes
    Publisher  — validates and makes a gallery customer-visible
    Manager    — configuration and everything above
```

A Sales Agent is deliberately not an Author. Authoring a mapping and putting it
in front of a customer are different responsibilities, and `_check_visual_publisher()`
enforces the second — tested from an Author, who is refused.

The `base.group_user,1,1,1,1` grant on `realestate.plan.region` is gone. Every
internal user retains **read**, which is what a viewer needs; write, create and
unlink now require Author.

---

## 20. Multi-company

`company_id` added as a stored related to `realestate.plan.region` (from the
parent property), `realestate.building.region` and `realestate.building.floor`
(from the project/building), plus the two new validation models. Five global
`ir.rule` records, applying to everyone including managers.

A region can never disagree about its company with the property it is drawn on,
because the field is a related rather than a copy.

---

## 21. Migration

Introducing a lifecycle whose default is `draft` would have switched off every
existing customer's gallery on upgrade — safe and destructive at once, which is
the combination that gets an upgrade rolled back.

So the migration classifies and only publishes what already worked:

| Outcome | Condition | Result |
|---|---|---|
| `legacy_valid` | validates with no critical issue | **published** — it was working, it keeps working |
| `needs_review` | critical issues | draft, with the report attached |
| `no_assets` | no GLB and no master plan | draft |
| `already_migrated` | has been through this | untouched |

**Nothing is remapped, renamed or deleted** — every mesh name, polygon, camera
default, floor row and uploaded file is left exactly as it is, asserted by test.
The run is idempotent, which caught a real bug: with an explicit recordset, a
second run read a *published* project as "not a legacy_valid outcome" and took
it offline — the migration undoing its own work. Same class of error as Module
4's commission double-conversion, caught by the same kind of gate.

---

## 22. Frontend lifecycle and resource cleanup

The audit found `_teardown()` disposing the renderer and nothing else. Three.js
does not garbage-collect GPU resources; a dropped JavaScript reference does not
free a WebGL object, and GLTFLoader's documentation warns about image resources
specifically. Opening a project, navigating away and reopening grew the tab's
memory every time until the context was dropped and the viewer went black.

`visual_dispose.js` walks the scene graph and disposes geometries, materials and
every texture-valued material property (24 of them), then the environment map,
the background, the controls, the PMREM generator and the Draco decoder's
workers, then the renderer with `forceContextLoss()` — without which the browser
never reclaims the context and repeated opens exhaust the per-page limit.

Every disposal is individually guarded: teardown usually runs after something
has already failed, and a disposal routine that throws leaks the rest.

---

## 23. Browser verification

**Not performed for this release, and this module is not being frozen.**

M34's gate — "do not freeze from Python/HOOT alone" — applies at freeze. The two
new frontend modules (`visual_capability.js`, `visual_dispose.js`) are written
as pure functions specifically so their logic is testable without a browser, and
`chooseExperience()` is a pure function of two plain objects for that reason.
Neither has yet been exercised in a real browser, and the viewer changes
(colour source, reserve gate, disposal wiring) have been verified by reading and
by server-side tests of the data they consume, not by rendering.

This is stated as a gap rather than glossed: see §26.

---

## 24. Tests

**111, from zero.** Baseline captured before any change (see §25) and never
hard-coded.

| Area | Tests |
|---|---|
| Visual state mapping (pure function, exhaustive) | 6 |
| Visual state through the ORM against Developer | 6 |
| Audience-aware pricing | 4 |
| Unit payload and role separation | 8 |
| Reservation eligibility | 3 |
| Legend / accessibility vocabulary | 3 |
| GLB container parsing | 6 |
| Extension support and Draco | 5 |
| Mapping QA | 8 |
| Publication lifecycle | 10 |
| Readiness scoring | 5 |
| Publisher authority | 3 |
| Filters and facets | 10 |
| Shortlist via Brokerage | 8 |
| Compare | 7 |
| Migration | 9 |
| Asset bundles | 5 |
| Fixtures/other | 5 |

Every GLB in the suite is **built by the test** (`build_glb()`), so a test can
state exactly which meshes exist and the validator is exercised against the real
container format rather than a mock.

---

## 25. Full-suite compatibility

Baseline captured **before** any change, from a 14-module fresh install:

```
    1,223 tests · 0 failed · 14 modules

    atmta_real_estate        299
    real_estate_developer    247
    real_estate_checks       325
    real_estate_brokerage    352
    real_estate_maquette       0
    real_estate_plan           0
```

After this work, a 14-module fresh install runs **1,334 tests**:

```
    atmta_real_estate        299   ← frozen, unchanged
    real_estate_developer    247   ← frozen, unchanged
    real_estate_checks       325   ← frozen, unchanged
    real_estate_brokerage    352   ← frozen, unchanged
    real_estate_maquette     111   ← was 0
    real_estate_plan           0
```

The four frozen modules are **unmodified** — no file in any of them was
touched — and every count is identical to the baseline.

### One intermittent failure, and what it is

`real_estate_checks` →
`TestTreasuryDashboardMultiCompany.test_dashboard_multi_company` fails
intermittently. Run in isolation on unchanged code it passed twice and failed
once; the failure is always the same step:

> With A+B active the dashboard should show 10 cheques on hand, shows 3.
> Active companies per the switcher: "ATMTA Treasury A".

The tour clicks to activate the second company and asserts before the switcher
has applied it — Module 3's own tour notes record that Odoo's company switcher
*stages* on toggle and only applies on **Confirm**, which is the interaction
this step depends on.

It is **not** caused by this work: nothing here touches the company switcher,
the Treasury dashboard, or any Checks code path, and the flakiness reproduces on
code with and without these changes.

It is **not fixed here** either. `real_estate_checks` is frozen, and the frozen
rule admits changes only for a cross-module contract defect proven by a failing
test. A timing-sensitive tour step is a test-quality problem, not a contract
defect, and stabilising it is a decision about Module 3 rather than something
this domain should reach in and do.

---

## 25a. A defect the full regression caught

Worth recording, because it is the strongest argument for running the whole
suite rather than the module under change.

`maquette_viewer.js` gained two imports — `./visual_capability` and
`./visual_dispose` — and the new files were **not added to the asset bundles**.
Odoo's module loader then failed to define them, and because
`maquette_viewer.js` is in `web.assets_backend`, *every backend page in the
system broke*. The module's own 106 tests all passed: they exercise Python, and
nothing Python does touches the bundle graph.

The full 14-module run surfaced it as **51 browser failures spread across
`atmta_real_estate` and `real_estate_brokerage`** — modules this work had not
touched. It looked exactly like an environment problem, and was not.

`test_visual_assets_bundle.py` now closes it: every relative import in every JS
file this module ships must appear in each bundle its importer appears in. Also
gated there: Three.js is never bundled (1.27 MB unminified on every backend
page), exactly one copy exists in the repository, and no shipped JavaScript
references a CDN host — the last of which caught its own false positive, since
the comment explaining the unpkg fix contained the word.

## 26. Remaining gaps

### Implemented

Shared visual architecture and commercial-truth service; visual configuration
and publication lifecycle; server-side GLB validation; mapping QA with severity;
readiness scoring; four security roles; company isolation and record rules;
audience-aware payloads and pricing; search/filter with facets; shortlist
through Brokerage; compare; capability probing and the fallback decision;
resource disposal; the Draco/CDN resolution; migration with classification and
idempotency; 106 tests.

### Partial

- **Rule 4 fallback** — the *decision* is built and the server sends the
  fallback descriptor with every payload, but the viewer template still renders
  an error box rather than mounting the 2D or list experience. The pieces exist
  and are not yet wired end to end.
- **Asset optimisation (M6)** — budgets and extension checks yes; an
  optimise/compress pipeline no.
- **Public mode (M20)** — the service layer is audience-correct; the
  unauthenticated `/api/v1/image/...` route in `real_estate_api` is documented
  but unchanged.
- **Unit panel (M13)** — the payload carries the right fields for it; the panel
  template still shows the 0.4 field set.

### Deferred

Presentation mode (M19); broker mode (M21); deep links (M22); unified 2D/3D
navigation (M23); visualisation modes beyond availability (M24); typical-floor
templates (M4); unit-type asset reuse (M5); progressive/LOD loading (M7);
real-time bus updates (M11); payment-plan preview (M14); interaction telemetry
(M17); accessibility keyboard paths (M25); RTL verification (M26); admin
reporting surfaces (M35); performance tiers (M33); browser gate (M34).

### Intentionally out of scope

Everything in the brief's "DO NOT BUILD HERE" list. Also: changing
`realestate.building.floor`'s representative-unit identity (§9) — a destructive
migration of live rows for no functional gain in this release.

---

## 27. What a deployment should do first

1. Run `env['realestate.visual.migration.runner'].audit()` and read it before
   `run()`. Working galleries stay live; broken ones go offline with a report.
2. Assign the four visual roles. Nobody has them by default, and publication
   requires Publisher.
3. If any model uses Draco, install the decoder or set
   `real_estate_maquette.draco_decoder_path`. Until then those models are
   refused at publication rather than failing in a browser.
4. Review `/api/v1/image/...` exposure (§17) before enabling public embeds.

---

# M4.5 — Security & browser closeout

M1–M4 were accepted; this section covers the closeout that had to land before
functional work continued.

## A. Public asset security

### What the second audit found

The first audit named `/api/v1/image/<model>/<id>/<field>`. Looking for
*every* route that serves a visual asset turned up a family it had missed, in
`real_estate_portal`:

```
    /projects/<id>/glb                          → the entire 3D model
    /projects/<id>/units.json                   → every unit
    /projects/<id>/regions.json                 → the polygon map
    /projects/portal/property/<id>/floor_plan   → floor plans
    /projects/portal/property/<id>/images.json  → the gallery
```

All `auth='public'`, all `sudo()`, and all authorised by a single test: *the
project exists and is not cancelled*. Fixing only the API route would have left
five bypasses — precisely the failure the brief warned about.

### The authorisation model

```
   request for (kind, record)
        │
        ├── internal user    → check_access_rights + check_access_rule
        │                      as the REAL user, before any sudo()
        │
        ├── public + grant   → company, project, kind, window, revocation,
        │                      origin — all checked
        │
        └── public, no grant → refused, always
```

`realestate.visual.grant` is a capability, not a second token system.
`real_estate_api`'s `realestate.embed.token` still authorises the embed *page*;
a grant is what that page — or a portal page that passed its own public check —
mints for the resources it legitimately needs. **A page cannot mint a grant for
a project it was not itself authorised for**, and the check is re-done inside
the mint rather than trusted from the caller.

Kinds are whitelisted as `(model, field)` pairs, so a request naming a field
that is not a visual asset is refused before anything else happens.

### Deliberate design choices

**`sudo()` happens once, on the line after authorisation succeeded.** Nothing
relies on the public user's own access rights being suitably narrow; relying on
that is how this became a hole.

**Every failure is 404, never 403.** Distinguishing them would make the route an
existence oracle. A test asserts a real record and a nonexistent one produce
identical responses.

**A grant cannot outlive the decision that created it.** Unpublishing a gallery
stops its assets being served immediately, even for grants still inside their
window.

### Broker mode

Not implemented, and deliberately not shortcut. The service already treats
`broker` as an external pricing audience, so no broker path can inherit an
internal price. M6 will consume Brokerage's authorisation rather than adding a
third access class here.

### A regression this closeout also fixed

`get_maquette_units_data()` defaulted to `audience='internal'` for one release.
The portal's anonymous `units.json` calls it with no argument, so that endpoint
served internal list prices, unavailability reasons and commercial status to the
public. The default is now `public`: a caller that forgets to say who is asking
gets the least-privileged answer.

### Tests

33 tests, every one through a real HTTP request. Asserting that a Python method
raises proves the method; it does not prove that no URL reaches the bytes
another way.

Covered: anonymous with no token · sequential enumeration · the old API URL ·
the old portal URL · portal floor plans · 404/404 indistinguishability · valid
grant · expired · revoked · malformed (five shapes) · project A's grant for
project B · wrong kind · unknown kind · unpublished project · origin match,
mismatch, missing and unrestricted · internal authorised · cross-company
internal · portal user treated as public · `/web/image` and `/web/content`
bypass attempts · interior models by four routes · the public payload leak · and
five asserting the portal still works end to end, because security that breaks
the product is not security.

Two initially failed on `/web/image` returning **200**. That turned out to be
Odoo's placeholder image, not a leak — so the assertion moved from the status
code to the response body, which is the property that matters. The tests now
check that no route returns bytes beginning `glTF`, with a counterpart asserting
the gated route *does*, so they cannot pass vacuously.

## B. The fallback path

```
    FULL 3D ──▶ OPTIMISED 2D ──▶ INVENTORY LIST
```

Each failure the brief enumerates lands in the right place: WebGL unavailable
and below-minimum-tier are decided *before* a scene is built; a missing GLB
short-circuits before any capability work; malformed GLB, network failure,
Draco unavailability and any Three.js exception land in one catch that degrades
rather than throwing a red box.

**HDR failure is explicitly non-fatal** — it affects lighting only. Interior
model failure likewise leaves project navigation intact.

`_degradeTo()` tears the viewer down before switching, so a failed 3D attempt
does not leak a scene graph. The reason shown is one plain sentence; loader
internals and stack traces go to the console.

The list fallback is built from data already fetched rather than re-requested:
by the time the viewer is falling back, another round trip is exactly what may
not work. It is also the accessible navigation path (M25).

14 HOOT assertions cover the decision function, which is pure precisely so the
thing that keeps a customer out of a dead end can be verified without a GPU.

## C. Checks tour — test reliability patch, no production behaviour change

Only `static/tests/tours/treasury_dashboard_tour.js` was touched. No Python, no
dashboard logic, no company rules, no treasury workflow, no production asset.

The race: Confirm triggers a full web-client reload, and the KPI selector
matches the pre-reload DOM just as well as the post-reload one, so the
assertion could read Company A's 3 instead of the combined 10.

Two observables were tried and rejected, and the file records why:

- `.o_switch_company_menu` renders `companyService.currentCompany.name` — the
  *current* company, never the active set. It reads "ATMTA Treasury A"
  throughout.
- Reopening the dropdown to check `aria-checked='true'` races the very reload
  it is waiting for and lands on the old page.

What works is waiting on the **rendered outcome** — the dashboard showing the
combined figure — as a trigger with no `run`, Odoo's own idiom for "wait until
this is true". No sleeps of any length.

### Verification

**20 consecutive isolated runs, 0 failures.** Then the frozen Checks suite:
**325 tests, 0 failed** — identical to its frozen baseline.

A first attempt at the 20-run gate returned 17/20 and was **discarded rather
than reported**: the three gaps produced no result line at all, which is a
crashed process rather than a failed test, and they coincided with other Odoo
instances this session was running concurrently on a memory-constrained host.
A stability measurement taken while competing for the same machine measures the
machine. The gate was re-run with nothing else of this session's running.

## D. Draco — production strategy

The decoder binaries are compiled artefacts from Google's Draco project and are
not in this repository; that cannot be changed by writing code. What changed is
that the strategy around them is enforced:

- **Version pinned and derived, not typed twice.** A test reads `REVISION` out
  of `three.module.js` and asserts the pin matches.
- **The required files are derived from the loader**: a test greps
  `DRACOLoader.js` for each filename it fetches, so a future Three.js changing
  them fails here rather than in a deployment.
- **A version mismatch is critical, and more serious than absence.** A decoder
  from another release does not fail cleanly — it produces corrupt geometry or a
  silent hang. `THREEJS_VERSION` in the decoder directory is checked against the
  pin.
- **Licence notices are checked for**, per Apache-2.0.
- **An external path is taken at its word.** Reaching across the network to
  verify a deployment's own asset host would make validation depend on exactly
  the connectivity this feature exists to avoid needing.
- `README.md` ships in the empty directory with the exact files, the exact
  source path inside the r160 distribution, and the install commands.

One `DRACOLoader` per viewer, created once at load and disposed in `_teardown()`
— its `dispose()` terminates the worker pool, which is what leaks otherwise.

## E. Asset-bundle regression gate — permanent

Beyond the import-graph check added when the defect was found, the gate now:

- compiles the **real production bundle** and asserts it is minified;
- asserts `probeCapability`, `disposeViewer` and `chooseExperience` survive
  minification — a module that is bundled but tree-shaken is still missing;
- compiles the **frontend** bundle, which the viewer is also served from;
- compiles the **debug** bundle and asserts it is *not* minified;
- statically verifies that no `@real_estate_maquette/...` module is `require`d
  without being `define`d — the exact shape of the failure that broke 51 tests;
- asserts Three.js is never bundled, exists exactly once in the repository, and
  that no shipped JavaScript references a CDN host;
- loads a backend page on a **cold cache** with every asset attachment purged.

## F. Deferrals honoured

Telemetry, real-time websocket updates, sunlight, VR/AR and performance numbers
were **not** pulled forward. §8 stands: no benchmark will be claimed until
representative production GLBs exist. The architecture prepared for measurement
— bounded payloads, version-based cache-busting, disposal — is in place and is
described without numbers attached to it.

---

# M5 — CRM, shortlist, compare, payment plans

## Shortlist — Brokerage's rows, with provenance

Covered structurally in §11; M5 added what the brief asked to persist.

A gallery favourite writes `realestate.property.match` — the same row an agent
works from, carrying the match score, the customer's own response and the
viewing feedback loop. There is no second shortlist model, and
`real_estate_maquette` still does not depend on `real_estate_brokerage`: the
integration is late-bound through the registry.

Persisted per the brief: the opportunity, the property, the **visual source**
(3D maquette / 2D plan / unit list / comparison / public embed), and the
timestamp — which is Brokerage's own `shortlisted_on`, stamped by
`action_shortlist()` rather than re-implemented here. Ranking and customer
feedback are Brokerage's existing `rank` and `customer_response` fields,
untouched.

Favouriting an already-scored match **keeps the score and the customer's
response** and only raises the flag; re-favouriting from the same experience
does not repeat the note.

### Anonymous sessions

`merge_anonymous_shortlist()` requires a `crm_lead_id`. That is the only path
from a session list into the database, so there is structurally no way for an
anonymous click to create permanent CRM data — which is what the brief
requires, rather than a policy that has to be remembered.

## Compare

2–4 units, rebuilt from the live service on every call, now including each
unit's payment plans. Nothing is cached from an earlier page: a unit blocked
thirty seconds ago compares as blocked, and its plans are re-quoted against the
price it has now.

## Payment plan preview

Every number comes from Developer:

```
    realestate.payment.plan._available_for(unit)  → which plans apply
    plan._generate_schedule(total_price=…)        → the actual rows
```

**No new service was needed in Developer, and none was added.**
`_generate_schedule` is already a pure calculation returning rows and
`_available_for` already answers applicability, so this only shapes them for an
audience. Nothing is recalculated — a plan's residual line, its rounding
absorption and its date rules are the developer's commercial terms, and a
second implementation of them is a second set of numbers to reconcile when they
disagree. A test asserts the schedule sums to the price using Developer's own
residual logic.

Headline figures — down payment, duration, cadence, instalment count and
amount, handover and balloon — are derived from the **generated rows** rather
than from the plan's summary fields, so what is shown is what this price
actually produces. Cadence is read from the gap between generated due dates,
because a plan may mix date rules and what a buyer wants to know is how often
they will pay.

### Two behaviours worth stating

**An unreleased unit gets no public schedule at all.** Its public price is
0.0, and generating a schedule against zero would produce a table of zeroes
that looks like an offer.

**A plan that cannot be quoted says so rather than vanishing.** Developer
refuses a handover-dated line when the project has no expected handover date —
correctly, because inventing one would put due dates years too early. The panel
now shows that plan as unavailable with Developer's own reason attached. An
earlier draft of this code swallowed the exception and dropped the plan
silently, which is a support call nobody can answer.

## Tests

17 further tests: applicable plans and their schedules, the sum-to-price
property, headline figures from generated rows, unreleased/public suppression,
internal visibility, wrong-project and draft-plan exclusion, unknown units,
compare carrying fresh plans, the unavailable-plan path, and shortlist
provenance across every source with score and feedback preservation.

---

# M6–M8 — modes, deep links, migration completion, and the browser gate

## Typical floor templates and unit-type asset reuse

Both landed in M5 and are unchanged here. `realestate.visual.unit.type` holds
visual assets once per type and bumps a shared version when they change;
`realestate.visual.floor.template` copies a floor's polygons rather than asking
somebody to redraw forty identical floors. Neither model owns a commercial
fact: a unit type is a *visual* grouping, and a floor template is a *drawing*.
Asset reference safety is covered by the effective-assets computation being
computed rather than stored, so an unpublished or replaced asset cannot be
served from a stale copy of a reference.

## Presentation Mode — what changed in M6

The mode existed before this milestone; what it lacked was a way to *do*
anything with a unit once a customer pointed at one. `onUnitSelected` was
written, the payment-plan and shortlist state was written, and none of it was
reachable — the viewer kept its selection to itself and the gallery template
rendered no panel.

- `MaquetteViewer` and `MasterPlan2D` now both take an optional
  `onUnitSelected` callback, and both funnel **every** selection path through a
  single setter (`_setSelectedUnit` / `_setSelectedProperty`). A mesh click, a
  plan region, a list row and the close button all report the same way; the
  three-out-of-five that used to be remembered are no longer a category of bug.
- The gallery renders the customer's panel: unit, area, price, status, and the
  four actions — **Save**, **Compare**, **Reserve**, and the payment plans.
- Every number in the panel is displayed, never derived. `formatMoney()` adds
  thousands separators and a currency symbol; there is no arithmetic on a price
  anywhere in the component.
- Reserve is offered only for a unit the server says is available, and it opens
  Developer's own reservation form — the advisory lock and the availability
  re-check are Developer's, not a second implementation here.

### Two defects the panel work exposed

**The loading splash could never appear.** The gallery loaded in
`onWillStart`, and a component with a pending `onWillStart` is not in the DOM —
so a salesperson opening the gallery kept looking at the previous screen until
the last RPC landed, and the "Preparing the gallery…" markup was dead code.
Loading moved to `onMounted`; the splash is now what a showroom actually sees,
and `this.loaded` is exposed so tests await the real thing instead of frames.

**A slow reply could land on the wrong unit.** Payment plans are fetched per
selection. A customer clicking through four units faster than the network
answers could read unit 1's schedule under unit 4's name. Each request is now
tagged with the unit it was made for and a late reply for an abandoned unit is
dropped. Tested by holding two requests open and resolving them out of order.

## Public Mode

`realestate.visual.modes.public_context()` is the single assembly point, and it
authorises before it assembles:

```
    project published for the public?          → no: 404
    grant valid, unexpired, unrevoked,
      right project, right company, origin ok? → no: 404
    ────────────────────────────────────────────────────────
    sudo(), and only the public audience
```

The `sudo()` is on the line after authorisation succeeded, and it is necessary
in both directions: a public route runs as the shared Public user, who cannot
read a property — so without it the gallery is empty for everybody, and with it
placed before the grant check the gallery is open to everybody. The order is
the entire security property.

`check_public_grant()` was added to `realestate.visual.access` and the deep-link
resolver now calls it instead of keeping its own copy. There is one definition
of "a valid public grant" to expire, revoke and scope; two would drift, and the
one that drifted would be the one still serving bytes.

**What a public visitor is not sent**: internal price basis, premium-rule
internals, minimum acceptable price, discount authority, approval metadata,
commercial status, the reason a unit is blocked, customer data, commission.
These are not stripped at the end — they are never read, because
`unit_payload(audience='public')` is the only source used. The test asserts on
a real `realestate.unit.block` with real internal notes on it, and checks that
neither the note, the person named in it, nor the *kind* of hold appears
anywhere in the payload.

### The anonymous session

`shortlist_available` is **False** and `session_shortlist_only` is True. A
visitor's favourites live in their own browser; this module writes no CRM record
for somebody who has not identified themselves, and there is no route that
would. `convert_public_session()` is the one path into the database, it requires
a lead that an explicit act already produced, and it refuses to invent one.

The explicit act is the enquiry form. `/projects/<id>/eoi` and `.../visit` now
absorb a posted shortlist onto the lead they create, after re-checking every id
against the project it claims to belong to — a hidden field is a value somebody
typed. A failure there never breaks the enquiry: the customer's message is the
thing that must not be lost, and a favourite that did not carry over is logged.

### The client half of the anonymous session

The server half alone would have been a dead path — `convert_public_session()`
reachable by nothing a visitor could actually do. `public_shortlist.js` closes
it, and it is deliberately not an OWL component:

- The list lives in **`sessionStorage`**, not `localStorage`, and not a cookie.
  A shared showroom tablet must not offer the next visitor the last one's
  favourites, and a cookie would send the list to the server on every request —
  which is the thing the rule forbids.
- Tapping a heart makes **no request at all**. The file contains no `fetch`.
- The list is written into the enquiry form's hidden field **at submit time**,
  because the value that matters is the one at the moment the visitor pressed
  send.
- The heart in the portal viewer's panel is a plain button carrying a property
  id and no handler of its own; a single delegated listener on the page owns
  the behaviour. There is one place where a favourite is recorded, not one per
  component that happens to show a unit.
- Storage failures — private browsing, quota, a blocked store — lose the
  favourites and never break the page. They are a convenience.

The list helpers are pure functions of a storage object, which is why the HOOT
suite can verify them without a browser session, including the cases that
matter most: rubbish left in storage by somebody else, an id that is not one, a
store that throws on every call, and a form submitted twice not accumulating
fields or posting a stale value.

## Broker Mode

Unchanged from M6's first pass and still delegating every authorisation
question to Brokerage: `_check_may_transact()`, `active_broker_agreement_id`,
`_check_still_valid()`, `_covers_project()`. A refused broker gets Brokerage's
own wording, not a second description of the same rule, and reserving routes
through lead registration rather than through a "visual broker customer" of our
own invention.

## Deep links

Two routes, one resolver, and an identifier is never an authorisation. The
internal link resolves under Odoo's access rules; the public link carries the
same grant that authorises assets. Resolution walks *upwards* from the unit
rather than trusting a project id in the URL, so a link cannot claim a unit
belongs to a project it does not. No price and no availability travel in the
URL — the page reads those fresh, so a link that was accurate on Tuesday shows
Thursday's truth on Thursday.

## Migration completion — classifying mappings, not projects

The project-level migration answers "may this gallery be live". That is not the
same question as "will *this* click work": a project can be perfectly
publishable while a handful of its mappings point at meshes somebody deleted
three revisions ago. `classify_mappings()` answers the second question, one row
per mapping, for both mesh mappings and plan regions:

| Classification | Meaning |
|---|---|
| `valid` | Matched against the current model, validated since the upgrade |
| `legacy_valid` | Matched, and live before this release |
| `needs_validation` | Resolves, but nothing has checked this project yet |
| `unmatched` | Points at a mesh — or a blank polygon — that is not there |
| `duplicate` | Two records claim one target; a click would be ambiguous |
| `missing_resource` | The asset it depends on does not exist or cannot be read |

It returns rows, never only a summary: "12 unmatched" tells somebody there is
work to do; a list of which twelve tells them how to do it.
`classification_summary()` exists for a report line.

It is **read-only by construction**. Deciding that a mapping pointing at a
deleted mesh should be cleared is a decision about somebody's model, and this
reports it so a human can make it. A test asserts the classifier changes nothing.

Two states the brief anticipated turned out to be unreachable in this schema and
are deliberately *not* handled: a region with no property, and a region pointing
at a deleted one. `property_id` is required, NOT NULL, and `ondelete='cascade'`.
Defensive code for a state the database cannot hold is code that can never run
and can never be tested. The blank-polygon case *is* reachable — NOT NULL still
permits an empty string, which is what a SQL import produces — and is classified.

## The role matrix

Four roles, each a superset of the last, and the line that matters is between
the middle two:

| | *no role* | Viewer | Author | Publisher | Manager |
|---|:--:|:--:|:--:|:--:|:--:|
| Read mappings and regions | ✓ | ✓ | ✓ | ✓ | ✓ |
| Draw a region, map a mesh, upload a model | — | — | ✓ | ✓ | ✓ |
| Produce a validation report (check your own work) | — | — | ✓ | ✓ | ✓ |
| **Advance DRAFT → READY** | — | — | **—** | ✓ | ✓ |
| **Publish — make it customer-visible** | — | — | **—** | ✓ | ✓ |
| Read a public grant | — | — | — | ✓ | ✓ |
| Revoke a public grant | — | — | — | — | ✓ |

Two rows are worth stating plainly, because writing this table is what made
them explicit — the first draft of it asserted the wrong thing twice and the
tests corrected it:

- **Read is deliberately open to any internal user.**
  `access_building_region_base` grants `base.group_user` read and nothing else,
  because a project form has to render its regions for anybody who may see the
  project. The visual roles govern *changing* a mapping, which is the part that
  decides where a customer's click lands.
- **An author may check their work but may not advance it.** Producing a
  validation report is an author's right — they need to know whether the mesh
  names match the model they just uploaded. `action_visual_validate()` is not
  that: it moves DRAFT → READY, a step along the road to a customer seeing it,
  and it is gated on Publisher along with publication itself.

Every row is asserted through the real gate — `with_user()`, real groups, real
ACLs — never a `has_group()` call standing in for the thing it is supposed to
be testing.

Grants are separated from authoring on purpose: a capability that causes bytes
to leave the building is not authoring data, so an Author cannot even read one.

## Multi-company

`company_id` on every visual model is a stored related on the owning property
or project, so a region can never disagree with the property it is drawn on,
and the global record rules apply to everyone rather than to a role. On the
authorisation side a grant carries its company, and `check_public_grant()`
refuses when the project's company and the grant's company differ — a grant
minted in one company cannot serve another's assets even if both published.

## Browser verification — what the gate actually caught

The gate is not decoration; it found four real defects, three of them in code
that had been reviewed and had passing tests.

**1. The list fallback was always empty.** `_teardown()` cleared `_unitData`,
and `_degradeTo()` calls `_teardown()` on the way to the fallback that renders
from it. Every customer sent down the last rung of the chain — the accessible
path, the one a weak device gets — saw "No units to show for this project yet"
while the legend directly above it counted two available. The existing fallback
tour asserted that `.o_maquette_fallback_list` *existed*; it does exist, empty.
The tour now asserts the list has rows in it, and `_unitData` survives teardown
because it is plain JSON, not a GPU resource.

**2. A raw "Access Error" modal in front of a customer.** `MasterPlan2D` had no
error handling on any of its loads, so an authorisation failure became Odoo's
global red dialog on a showroom screen. Rule 4 covers the whole gallery, not
only the 3D viewer: failures now degrade to a plain sentence inside the plan.

**3. The shortlist was advertised and then exploded.** `shortlist_available()`
meant "Brokerage is installed", which is not the question the client asked. A
user without Brokerage's sales role got the button, pressed it, and hit an
Access Error. It now means "installed **and** readable by this user", and
`shortlist_for()` returns empty rather than raising.

**4. A tour that passed over a broken screen.** The presentation tour passed
four consecutive steps — title size, button height, no horizontal overflow —
with a red Access Error modal covering the entire gallery, because every
assertion was about chrome and none about whether the thing behind the chrome
had worked. `assertNoErrorDialog()` now runs at every checkpoint in every tour.

### Two test-side races fixed by waiting on the right thing

- **The fallback tour** triggered on `".o_maquette_fallback, .o_maquette_root"`,
  which matches the still-loading root, so it asserted before the viewer had
  degraded. A trigger has to *be* the state being waited for, not a superset.
- **The RTL tour** asserted `getComputedStyle(...).direction === "rtl"`. Odoo 18
  does not set a `direction` declaration anywhere for the backend: `start.js`
  adds `o_rtl` to `<body>`, and the mirroring comes from the rtlcss-flipped
  bundle. The assertion was testing nothing and failing a perfectly good Arabic
  session; it now asserts `body.o_rtl` plus a loaded `web.assets_web.rtl`
  stylesheet, which is Odoo's actual contract.

### Scenarios covered in a real browser

Showroom open · project name and touch-target sizing · customer context from a
CRM opportunity · 3D → 2D degradation · malformed GLB → degradation ·
degradation with no dead end and a populated navigation path · **unit selection
→ panel → payment terms → save → a real `realestate.property.match` row in
Brokerage** · **two-unit comparison read fresh, with its as-of time** · repeated
open/close without accumulating canvases · RTL session with an unmirrored world
· tablet viewport.

The journey and comparison tours run on the **degraded** experience
deliberately. Headless Chrome renders WebGL here, so the unit list is the only
experience a browser can click deterministically — picking a specific mesh out
of a canvas means guessing at pixels — and it is also the experience that most
needs to work. Selection converges on one setter whichever way it was made, so
the journey exercises the same path a mesh click takes.

## HOOT — component behaviour

37 tests run in the browser's own module system: 14 on the fallback decision
(pure functions, no GPU) and 23 on the gallery component. The HOOT
suite covers what a tour against a healthy database cannot produce — a server
that is slow, out of order, refusing, or empty:

opening and splash · a refusal rendered as a sentence · a gallery that cannot
load at all · unit panel and payment plans · a plan the developer cannot quote,
with its reason · a failed plan request degrading rather than raising · **a
stale reply for an abandoned unit** · reserve offered and withheld by status ·
shortlist hidden when unusable · saving without an opportunity writing nothing ·
saving with one going to the server and coming back saved · comparison
requiring two units, re-read from the server, capped at four, closable ·
experience switching · a project configured for 2D · project id read from a
pasted URL · teardown leaving nothing behind.

The 3D and 2D children are stubbed: mounting the real ones would drag in
Three.js and a WebGL context, which is the dependency the fallback architecture
exists to avoid, and would make these tests about the GPU rather than about the
gallery.

## Three.js and Draco — status

Unchanged from M4.5-D and still correct: r160 vendored once, never bundled,
loaded by native dynamic `import()`; the pin is derived from `three.module.js`'s
own `REVISION` rather than typed twice; the required decoder filenames are
grepped out of `DRACOLoader.js` so a future Three.js changing them fails here
rather than in a deployment; a version mismatch is treated as more serious than
absence, because a decoder from another release produces corrupt geometry or a
silent hang rather than a clean failure. One `DRACOLoader` per viewer, disposed
in `_teardown()`.

The decoder binaries remain absent from this repository — they are compiled
artefacts from Google's Draco project, and no amount of code changes that. The
`README.md` in the empty directory names the exact files, the exact source path
inside the r160 distribution, and the install commands.

## Performance — still not measured, still on purpose

No benchmark is claimed. §8 stands unchanged: `ir_attachment` holds zero
production GLBs in this environment, and the brief forbids inventing pass/fail
numbers without representative assets. What M6–M8 added that will matter when
those assets exist:

- Three.js is no longer downloaded to discover there is nothing to render. The
  units payload is fetched first, and a project with no model — or a device the
  capability probe sends to 2D — never pays 1.27 MB to find that out.
- The canvas-accumulation tour is the closest thing to a memory assertion that
  can be made honestly: it is not a measurement, but an accumulating count is
  unambiguous evidence of a leak.

## A frozen-module finding the final regression turned up

The first full 14-module fresh install of this milestone came back **4 failed,
72 errors of 1,522** — none of them in the visual stack, all of them in frozen
Modules 1–4. It was run between 22:56 and 23:03 **UTC**, and that is the whole
explanation.

The server's users sit in `Europe/Brussels` (UTC+2 in summer). Between 22:00
and 24:00 UTC, `fields.Date.today()` (UTC) and `fields.Date.context_today()`
(the user's timezone) return **different dates**. Any code that mixes the two
is off by one day for two hours out of every twenty-four.

Two failures are exactly that, and in both the *test* holds the wrong clock
while the production code is right:

| Test | Production code | Test asserts against | Result |
|---|---|---|---|
| `atmta_real_estate` · `test_turn_records_vacant_days` | `context_today` (`unit_turn.py:112`) | `self.today` from UTC | `11 != 10` |
| `real_estate_brokerage` · `test_an_eligible_broker_gets_the_protection` | `context_today` (`lead_registration.py:374`) | `fields.Date.today()` | `2026-11-06 != 2026-11-05` |

The other 72 are the same root cause reaching further: in `real_estate_checks`
the deposit's custody transfer does not take effect, so the bounce's transfer
finds the cheque already with the same custodian at the same location and the
`_check_movement_is_a_movement` constraint — correctly — refuses to record a
movement that moves nothing.

**This was proved, not assumed.** Two controlled runs:

- **Frozen modules alone, no visual module installed**: 1,223 tests, **2 failed,
  0 errors** — the same two off-by-one failures, both of which executed at
  23:08–23:09 UTC, inside the window. The visual stack cannot be the cause of a
  failure that reproduces when it is not installed.
- The 72 `real_estate_checks` errors did **not** reproduce in that run, because
  by the time it reached Checks the clock had passed 00:00 UTC and the two
  date functions agreed again.

**Not dismissed as machine load.** The brief is explicit that a reproducible
isolated failure must be investigated rather than waved away, and this one was:
the mechanism is named, the two clocks are named, the window is named, and the
control run isolates it from this work.

**Not fixed here.** These are frozen modules, and the milestone permits only
approved test-only reliability changes to them. The fix is one line per test —
assert against `fields.Date.context_today(self.env.user)`, the clock the
production code actually uses — and it is recommended, not applied. Nothing in
the visual stack mixes the two clocks: every date in this work comes from
`fields.Date.context_today(self)`.

## Remaining gaps, restated honestly

Nothing below is a surprise; each is a deliberate boundary rather than an
oversight, and each is stated so the next person does not have to rediscover it.

1. **No performance numbers.** No representative production GLB exists in this
   environment. §8 and the M4.5-F deferral both stand.
2. **Draco decoder binaries are not in the repository.** They are compiled
   third-party artefacts. The strategy, the version pin, the filename
   derivation and the install instructions are all in place; the files are a
   deployment step.
3. **The 3D selection path is not clicked in a browser.** Picking a specific
   mesh out of a WebGL canvas means guessing at pixels. Selection converges on
   one setter, that setter is covered by HOOT, and the journey tour drives it
   through the list — but no test literally clicks a building in 3D.
4. **Two frozen-module test defects are reported, not fixed** (the UTC vs
   `context_today` clock, above). They belong to Modules 1 and 4.
5. **`convert_public_session` has one caller**: the enquiry form. A visitor who
   never enquires never becomes a record, which is the rule working, but it
   also means the conversion path has exactly one entry point to keep correct.

## Final regression

All fresh installs of all fourteen modules, all run outside the
UTC/user-timezone window described above:

| Run | Scope | Result |
|---|---|---|
| Fresh install | 14 modules | 1,522 · 0 failed · 0 errors |
| Fresh install | + role matrix, + public-shortlist work | 1,535 · 2 failures, both my own new tests asserting the wrong thing |
| Full upgrade on the installed database | same 14 modules | identical 1,535 and identical result — the upgrade path produces the same suite as the install |
| **Final gate, everything included** | 14 modules, fresh install | **1,536 tests · 0 failed · 0 errors** |

Per-module counts in the final gate:

| Module | Tests | Note |
|---|--:|---|
| `atmta_real_estate` | 299 | frozen, unchanged |
| `real_estate_developer` | 247 | frozen, unchanged |
| `real_estate_checks` | 325 | frozen, unchanged |
| `real_estate_brokerage` | 352 | frozen, unchanged |
| `real_estate_maquette` | 291 | 247 at the start of M6 |
| `real_estate_plan` | 15 | zero before this upgrade |
| `real_estate_portal` | 7 | zero before this milestone |

Plus 47 HOOT tests (37 maquette, 10 portal) inside the two suite wrappers, and
11 real-browser tour scenarios.

The four frozen counts are exactly their frozen values. **The two role-matrix
failures in the middle run were mine and were tests, not code**: they asserted
that no-role users cannot read regions (an explicit ACL says they can, by
design) and that an Author may run `action_visual_validate` (it advances
DRAFT → READY, so it is the Publisher's). Both were corrected to assert what
the system actually does, and the matrix in this report was corrected with them.

## Freeze decision

**Recommendation: the visual stack is ready to freeze**, with the two
frozen-module clock defects raised separately as Module 1 / Module 4 work.

Versions at the freeze point: `real_estate_maquette` **0.6**,
`real_estate_plan` **0.5**, `real_estate_portal` **0.2** — the portal is
included because the anonymous-session conversion put code and tests in it.

The gates the milestone set, and where each stands:

| Gate | Status |
|---|---|
| Full 14-module fresh install, all tests | ✅ **1,536 · 0 failed · 0 errors** |
| Full upgrade on an installed database | ✅ same suite, same result as the install |
| Migration retry / idempotency | ✅ second run reports `already_migrated` and changes nothing |
| Mapping classification complete (6 buckets) | ✅ two buckets proved unreachable and are documented as such |
| Role matrix asserted through real ACLs | ✅ and it corrected two wrong assumptions |
| Multi-company isolation | ✅ company on the record, company on the grant |
| Public mode leaks nothing internal | ✅ asserted against a real block with real notes |
| Anonymous browsing creates no CRM record | ✅ server, route and browser-side all asserted |
| Real browser gate | ✅ 11 scenarios; it caught four real defects |
| HOOT component coverage | ✅ 47 tests (37 maquette, 10 portal) |
| Asset-bundle regression gate | ✅ permanent, and it caught the portal bundle omission today |
| Fallback chain has no dead end | ✅ including the empty-list defect this gate found |
| Performance numbers | ⛔ deferred, deliberately, for want of production assets |

The one judgement call worth stating: **the gate found four real defects in
code that already had passing tests**, three of them in paths a customer would
have hit — an empty fallback list, a raw Access Error modal, and a shortlist
button that failed when pressed. That is an argument for freezing *now* rather
than later: the tests that would have caught them exist today and did not exist
last week, and every one of them is a permanent regression gate rather than a
one-off check.

---

# FREEZE — Interactive Sales Gallery

The visual stack is frozen at **`real_estate_maquette` 0.6**,
**`real_estate_plan` 0.5**, **`real_estate_portal` 0.2**.

## The regression gate for anything that follows

```
    atmta_real_estate        299     (frozen)
    real_estate_developer    247     (frozen)
    real_estate_checks       325     (frozen)
    real_estate_brokerage    352     (frozen)
    real_estate_maquette     291     ← frozen here
    real_estate_plan          15     ← frozen here
    real_estate_portal         7     ← frozen here

    full 14-module fresh install   1,536 · 0 failed · 0 errors
    full upgrade, same database    same suite, same result
```

Plus, inside those counts, **47 HOOT tests** (37 maquette, 10 portal) and
**11 real-browser tour scenarios**.

No further visual-gallery feature work. The deferrals in "Remaining gaps" stay
deferred: performance numbers wait for representative production GLBs, and the
Draco decoder binaries are a deployment step, not a code change.

## The two frozen-module test corrections, applied

Approved as test-only reliability changes, in the same spirit as the M4.5-C
Checks tour patch. Neither touches production code, and neither changes a test
count — 299 and 352 both stand.

| File | Was | Now |
|---|---|---|
| `atmta_real_estate/tests/common.py` | `cls.today = fields.Date.today()` | `fields.Date.context_today(cls.env['res.partner'])` |
| `real_estate_brokerage/tests/test_broker_registration.py` | asserts against `fields.Date.today()` | asserts against `fields.Date.context_today(reg)` |

In both cases the *test* held the wrong clock and the production code was
right: these modules date things in the user's timezone, and the fixtures were
built in UTC.

### Verified deterministically, not by waiting for midnight

The original diagnosis depended on a two-hour nightly window, which is a poor
thing to regression-test against. The window is reproducible on demand instead:
put every user in a timezone whose date already differs from UTC.

```sql
UPDATE res_partner SET tz='Pacific/Kiritimati'      -- UTC+14
 WHERE id IN (SELECT partner_id FROM res_users);
```

Under that skew, before the fix: **1 + 1 + 74 failures**, exactly matching the
22:00-UTC run. After the fix: `atmta_real_estate` and `real_estate_brokerage`
run **651 tests, 0 failed, 0 errors** with the skew still in place.

This is worth keeping. Any suite that passes only when the server and its users
agree about what day it is has a bug waiting for a deployment in Cairo, Riyadh
or Dubai — which is every deployment this product targets.

## ⚠ A production defect found while doing this — NOT fixed, needs a decision

Chasing the 74 `real_estate_checks` errors found something that is **not** a
test defect. `real_estate_checks/models/check.py:667`:

```python
Custody.create({
    ...
    'date': rec.received_date or fields.Date.context_today(rec),   # ← Datetime field
})
```

`realestate.check.custody.date` is a **Datetime**. It is being given a **Date**
computed in the user's timezone, which Odoo coerces to **midnight UTC** of that
date. For any user ahead of UTC, during the hours between their local midnight
and UTC midnight, the cheque's opening custody row is stamped **in the future**.

`_apply_to_check()` mirrors custody onto the cheque with
`order='date desc, id desc'` — so that future-dated receipt row outranks every
real movement that follows, and the mirror silently reverts.

Observed directly, with users at UTC+14:

```
receipt  date=2026-08-09 00:00:00   to_custodian=1     to_location=245
deposit  date=2026-08-08 11:55:01   to_custodian=False to_location=246  (at bank)

check.custodian_id = 1     check.location_id = 245
```

**The cheque says it is in the office with the clerk while the paper is at the
bank.** Downstream, the bounce's return-from-bank transfer then finds the same
custodian and the same location on both sides and is correctly refused as a
movement that moves nothing — which is what the 74 errors are.

Why it matters beyond the tests: every target deployment is ahead of UTC
(Cairo UTC+2/+3, Riyadh UTC+3, Dubai UTC+4). A cheque registered in the local
morning has a wrong custody mirror until UTC midnight, and anything reading
`check.location_id` in that period — screens, reports, the bounce path — reads
a location the paper is not in.

The fix is one line — stamp the movement with a real instant
(`fields.Datetime.now()`), or convert the date through the user's timezone
rather than letting it coerce to UTC midnight. **It has not been applied**: it
changes production behaviour in a frozen module, and that is a deliberate
decision to be taken rather than slipped into a visual-gallery milestone.
Reproduce it with the `UPDATE res_partner SET tz=...` above.
