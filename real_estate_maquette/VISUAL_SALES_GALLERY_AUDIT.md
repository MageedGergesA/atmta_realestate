# ATMTA Interactive Sales Gallery — Phase 0 Audit

**Modules:** `real_estate_maquette` (0.4), `real_estate_plan` (0.3)
**Treated as:** one product domain
**Frozen dependencies:** `atmta_real_estate`, `real_estate_developer`,
`real_estate_checks`, `real_estate_brokerage`

Everything below is answered from the code, not from the module descriptions.

---

## 0. Baseline, captured before any change

Full 14-module fresh install, run before this work started:

```
    1,223 tests · 0 failed · 0 errors · 14 modules installed

    atmta_real_estate        299
    real_estate_developer    247
    real_estate_checks       325
    real_estate_brokerage    352
    real_estate_maquette       0     ← no tests exist
    real_estate_plan           0     ← no tests exist
```

The two visual modules contribute **zero tests**. Every count above is read
from the run log rather than carried forward from an earlier session.

---

## 1. The ten questions, answered from code

### 1. Which exact Three.js version is running?

**r160** — `three.module.js` line 6: `const REVISION = '160';`, and
`maquette_viewer.js:17` pins `const THREEJS_VERSION = "0.160.0"`.

### 2. Who owns and bundles it?

`real_estate_maquette/static/src/lib/threejs/`, vendored **unminified**:

| File | Size |
|---|---|
| `three.module.js` | 1,272,972 bytes (53,044 lines) |
| `GLTFLoader.js` | 108,527 bytes |
| `BufferGeometryUtils.js` | 31,918 bytes |
| `OrbitControls.js` | 29,880 bytes |
| `DRACOLoader.js` | 13,640 bytes |
| `RGBELoader.js` | 11,389 bytes |

There is exactly **one** copy in the suite and no other module vendors Three.js.
No `window.THREE` or `globalThis` assignment anywhere — ES module imports only.

**Absent:** `KTX2Loader`, `MeshoptDecoder`. Only Draco is available, and see §9.

### 3. Is it loaded globally or lazily?

**Lazily, and correctly.** None of the `lib/threejs/` files appear in any asset
bundle. `maquette_viewer.js:66–76` and `glb_viewer.js:10–13` use native dynamic
`import()` against a static URL, resolved only when a viewer mounts.

This is the one part of M28 that is already right.

### 4. How large are real GLBs?

**Unanswerable from this environment, and that is itself a finding.**

`ir_attachment` in a freshly built database holds **0** rows for
`maquette_glb`, `interior_glb` or `maquette_env_hdr`. There is no production
data here and no sample asset committed to the repository.

The fields are plain `fields.Binary` on `realestate.project` /
`realestate.property`, so real GLBs land in the filestore with no size
constraint, no validation and no recorded metadata. Nothing in the codebase
records a triangle count, a texture budget or a file size, so no baseline
exists to compare an optimisation against. M6 and M33 must begin by creating
representative assets — measuring first, as the brief requires.

### 5. How are GPU resources disposed?

**They are not.** `maquette_viewer.js:610–621`:

```js
_teardown() {
    if (this._animationId) cancelAnimationFrame(this._animationId);
    if (this._clickTimer) { clearTimeout(this._clickTimer); ... }
    if (this._onResize) window.removeEventListener("resize", this._onResize);
    if (this._resizeObserver) { this._resizeObserver.disconnect(); ... }
    if (this._onCanvasWheel && ...) { ...removeEventListener("wheel", ...); }
    if (this._renderer) { this._renderer.dispose(); this._renderer = null; }
}
```

Timers, listeners and the renderer are handled. **Nothing else is.** No
geometry, material, texture, environment map, render target, or DRACO decoder
worker is disposed, and the loaded scene graph is never traversed.

GLTFLoader's own documentation warns specifically that image resources need
explicit disposal. Opening a project, navigating away and reopening will
accumulate GPU memory for the lifetime of the tab. Unmeasured, because there
are no assets to measure with — see M8 and M33.

### 6. Can Sales Agents currently mutate mesh mappings?

**Through the controller: no. Through the ORM: yes, and worse than the brief
assumes.**

`/maquette/save_mesh_mapping` gates on `write` access to
`realestate.project`, which only `real_estate_developer.group_dev_manager`
holds. A Brokerage Sales Agent has read-only on both projects and properties
and is correctly refused.

But `maquette_mesh_name` is an ordinary writable field on
`realestate.property`, editable directly on the property form
(`real_estate_maquette/views/property_views.xml:29`), and
`atmta_real_estate.group_realestate_user` has **write on
`realestate.property`**. Any such user can remap a production mesh from the
property form without going near the gated route.

The `realestate.maquette.unit.picker` wizard has
`base.group_user,1,1,1,1` and its `action_confirm` writes
`self.unit_id.maquette_mesh_name` — and first does this:

```python
others = self.env['realestate.property'].search([...same mesh...])
if others:
    others.write({'maquette_mesh_name': False})
```

Mapping a mesh **silently unmaps whichever unit held it before**. No warning,
no confirmation, no audit trail. In a live gallery that means a customer
looking at the previous unit now sees an unmapped mesh, and nobody knows who
did it or when.

### 7. Can 2D/3D bypass Developer reservation validation?

**No — the structural answer is sound.** `maquette_viewer.js:679–698`
(`reserveUnit`) opens the Developer model's own form:

```js
res_model: "realestate.unit.reservation",
context: { default_property_id: u.id },
```

Nothing in either visual module writes property status, creates a hold, or
computes a price. A suite-wide grep for reservation or state writes from the
visual modules returns only *computed statistics* (`units_reserved` counters).

Developer's `reservation_v2.create()` takes a `pg_advisory_xact_lock` on the
property and then calls `_check_available_for_sale()`, so atomic locking is
already in place and the visual layer inherits it.

**The defect is in the gate the viewer shows first:**

```js
if (u.state !== "available") { notification "Unit is not available."; return; }
```

That is the **legacy `realestate.property.state`**, not Developer's
authoritative availability. A unit that is `available` in legacy terms but
unreleased, blocked, or outside its selling window will have the Reserve
button offered, the customer will proceed, and Developer's form will refuse.
Wrong at the UX layer, correct at the data layer — which is the right way
round, but still wrong.

### 8. Does public embed expose assets safely?

**Partly, and one route does not.**

Neither visual module has a public route: all 11 controller endpoints across
both are `auth='user'`. The public contract lives in `real_estate_api`:

- `/embed/v1/maquette-3d/<token>` and `/embed/v1/plan-2d/<token>` — token
  gated, with expiry, allowed-origin checks and usage recording. Sound.
- `/api/v1/image/<model>/<id>/<field>` — `auth='public'`, `sudo()`,
  whitelist-gated. **No token required.**

The whitelist includes `maquette_glb`, `maquette_env_hdr`, `interior_glb`,
`plan_image`, `floor_plan_image` and `elevation_sheet`. Visibility is decided
by:

```python
def _public_property(record):
    return record.exists() and record.state in ('available', 'reserved', 'sold')
```

Three consequences:

1. **Predictable unauthenticated URLs.** `/api/v1/image/realestate.project/7/
   maquette_glb` needs only a sequential integer. The embed token protects the
   *page*; it does not protect the *asset*.
2. **Legacy state again.** An unreleased unit whose `property.state` happens to
   be `available` has its floor plan and interior GLB publicly downloadable,
   regardless of what Developer says about its release.
3. **`sudo()` bypasses record rules**, so multi-company isolation does not
   apply to this route at all.

### 9. How many competing representations of building/floor/unit exist?

**Rule 1 is currently honoured — there is no competing property master.**

Models defined by the two visual modules:

| Model | Kind | Role |
|---|---|---|
| `realestate.building.floor` | Model | floor metadata, `building_id` + representative `unit_id` → `realestate.property` |
| `realestate.building.region` | Model | polygon on the project master plan → building property |
| `realestate.plan.region` | Model | polygon on any property's plan image → child property |
| `realestate.building.preview` | **Transient** | click-through dialog |
| `realestate.maquette.unit.picker` | **Transient** | mesh-mapping wizard |
| `realestate.spec.tag` | Model | marketing spec tags |

No `visual.unit`, no `maquette.unit`, no `plan.unit`. Every visual object
resolves to `realestate.property`. Mesh mapping is a `Char` on the property
itself.

**One data-quality risk.** `realestate.building.floor` derives its identity
from a *representative unit*: `floor_number` and `property_usage_id` are stored
relateds on `unit_id`, and `unit_ids` is recomputed from every sibling with a
matching `floor_number`. Delete that unit or change its floor number and the
floor row silently changes meaning or cascades away (`ondelete='cascade'` on
`unit_id`). A floor is not a unit, and modelling it as one is fragile.

### 10. What breaks if WebGL fails?

**The sale.** `maquette_viewer.js:235` constructs
`new T.WebGLRenderer({...})` with no capability probe. Any failure lands in
the outer `catch` at line 205 and sets `this.state.error`, which the template
renders as:

```xml
<div t-elif="state.error" class="alert alert-danger m-3">
    <strong>Could not load:</strong> <t t-esc="state.error"/>
</div>
```

A red box containing a Three.js exception message. There is **no fallback to
2D and no fallback to property details**, which is precisely what Rule 4
forbids. The same is true for a malformed GLB, a missing GLB and a device
without a usable GPU.

The HDR path is the one place that degrades properly —
`catch (e) { /* HDR is optional */ }` — and a missing DRACOLoader is caught
too. Those two show the intended pattern; it simply was not applied to the
thing that matters.

---

## 2. Current 2D architecture

```
   realestate.property (any hierarchy level)
        │   plan_image  (fields.Image)
        │
        └── realestate.plan.region  (N)
                 parent_property_id  → the property carrying the image
                 target_property_id  → a DIRECT CHILD property
                 polygon             → JSON "[[x%, y%], ...]"
                 label, color, sequence
                 target_state        → stored related on property.state
```

Constraints are already close to what M4 asks for, and are enforced in Python:

- polygon is valid JSON, is a list, and has **≥ 3 vertices**;
- every vertex is `[x, y]` with numeric coordinates;
- every coordinate is a percentage in `[0, 100]` — so regions survive any
  display size;
- target ≠ parent;
- target must be a **direct child** of parent;
- SQL `UNIQUE(parent_property_id, target_property_id)`.

M4's stated constraint list is therefore **already satisfied**. What is missing
is not rules but **tests** — there are none — plus authoring ergonomics
(no undo/redo, no zoom/pan while editing) and typical-floor reuse.

### Duplication

Three separate polygon implementations exist:

| File | Lines | Role |
|---|---|---|
| `real_estate_plan/.../plan_editor.js` | 462 | draw/edit regions on a property plan |
| `real_estate_plan/.../plan_viewer.js` | 448 | render/click regions on a property plan |
| `real_estate_maquette/.../master_plan_2d.js` | 696 | draw **and** render regions on the project master plan |

`master_plan_2d.js` targets `realestate.building.region` through
`/maquette/regions/*`; the other two target `realestate.plan.region` through
`/plan/*`. Two polygon models, two route families, three renderers, and two
different commercial UIs for what a customer experiences as the same gesture.
This is M23's "do not maintain two completely different commercial UI
behaviours", already true.

---

## 3. Current 3D architecture

```
   realestate.project
        maquette_glb        (Binary)   ── /maquette/glb/<project_id>      auth=user
        maquette_env_hdr    (Binary)   ── /maquette/hdr/<project_id>      auth=user
        maquette_default_camera (Char/JSON)
        maquette_mesh_naming_hint
        master_plan_2d      (Image)
             │
             └── get_maquette_units_data()  ─→ /maquette/units/<project_id>
                      per unit: id, property_code, name, mesh_name, state,
                                base_price, currency, area_sqm, property_type,
                                has_floor_plan, color_override

   realestate.property
        maquette_mesh_name  (Char)     ← the entire mapping contract
        interior_glb        (Binary)   ── /maquette/interior/<property_id>
        floor_plan_image    (Binary)   ── /maquette/floor_plan/<property_id>
```

Mesh → property mapping is a **string match on `maquette_mesh_name`**. There is
no mapping model, no validation, no duplicate detection beyond the picker's
silent steal, and no report of unmatched meshes.

The payload the viewer colours from is `u.state` — legacy property state — and
prices come from `u.base_price`, not from Developer's price book / promotion
engine (`property_pricing.py`, `price_book.py`, `price_rule.py` all exist and
are not consulted).

---

## 4. Asset ownership and version

Covered in §1.1–1.3. One vendored copy of Three.js r160, unminified, lazily
imported, no global leakage, no second copy anywhere in the suite.

**One production blocker:** `maquette_viewer.js:327`

```js
draco.setDecoderPath(`https://unpkg.com/three@${THREEJS_VERSION}/examples/jsm/libs/draco/`);
```

The Draco decoder is fetched **from unpkg.com at runtime**. Any Draco-compressed
GLB therefore requires public internet access from the customer's browser. On an
on-premise deployment, behind a corporate firewall, or in a GCC network with
restricted egress, Draco assets simply fail to decode — and the failure surfaces
as the generic red error box from §1.10. The brief's "no CDN requirement for
production" is currently violated.

---

## 5. Developer integration

| Concern | Developer's authority | What the visual layer uses |
|---|---|---|
| Availability | `is_available_for_sale`, `_check_available_for_sale()` | ❌ legacy `property.state` |
| Release | `realestate.unit.release.batch` | ❌ not consulted |
| Blocks | `realestate.unit.block` | ❌ not consulted |
| Price | price book / price rules / promotions | ❌ raw `base_price` |
| Payment plans | Module 2's payment-plan service | ❌ not surfaced at all |
| Reservation | `realestate.unit.reservation` + `pg_advisory_xact_lock` | ✅ delegated correctly |
| Atomic conflict | advisory lock in `create()` | ✅ inherited |

So the *dangerous* half — creating holds — is right, and the *visible* half —
what a customer is told is available and at what price — is wrong.

---

## 6. CRM integration

**None.** Neither module references `crm.lead`. There is no shortlist, no
favourite, no opportunity association, and no interaction telemetry.

`maquette_viewer.js:702` opens `realestate.listing` — a **Brokerage** model —
but `real_estate_maquette` depends only on `atmta_real_estate`,
`real_estate_developer` and `web`. In a deployment without Brokerage that
button raises. A latent cross-module break.

---

## 7. API / embed integration

Described in §1.8. The embed page contract (`realestate.embed.token`) is
well built: opaque token, absolute expiry, allowed-origin list, usage counter,
last-used IP/origin, GC of expired rows. The asset contract underneath it is
not gated at all.

`real_estate_api/static/lib/embed/maquette_3d_boot.js` is a separate public
bootstrap for the 3D viewer — a fourth frontend entry point.

---

## 8. Performance risks

1. **No asset budget of any kind.** No size limit, no triangle budget, no
   texture-dimension check, at upload or anywhere else.
2. **Everything loads at once.** One GLB per project, fetched whole before
   first interaction. No LOD, no per-building split, no progressive load.
   Interior GLBs *are* lazy, which is the one thing already right.
3. **Unminified library in the served path.** 1.27 MB of `three.module.js`
   before gzip, fetched per cold viewer open.
4. **No compression support beyond Draco**, and Draco depends on a CDN (§4).
   No KTX2/Basis, no Meshopt, no instancing.
5. **`get_maquette_units_data()` builds the full unit list in Python** with no
   limit and no pagination — every unit in the project on every viewer open.
6. **Cache-busting is `write_date`**, so *any* project edit invalidates the
   whole GLB in every browser, re-downloading an unbounded asset.

---

## 9. Security risks

Ranked by what a real deployment would actually suffer.

| # | Finding | Evidence |
|---|---|---|
| 1 | **Every internal user is a 2D visual author.** `base.group_user,1,1,1,1` on `realestate.plan.region` — create, edit and delete production polygon mappings. | `real_estate_plan/security/ir.model.access.csv` |
| 2 | **Zero `ir.rule` records** in either module, and **zero `company_id` fields**. Company A's regions, floors and mappings are readable and writable by Company B. | grep: 0 hits for both |
| 3 | **Unauthenticated asset URLs** with sequential integer IDs (§1.8). | `api_v1_image.py` WHITELIST |
| 4 | **Mesh remapping outside the gated route** via the property form and the `base.group_user` picker wizard (§1.6). | `property_views.xml:29`, `unit_picker.py:73` |
| 5 | **Silent unmapping** of another unit when a mesh is reassigned. | `unit_picker.py:66–73` |
| 6 | **Inconsistent authoring rights**: `plan.region` is `group_user` CRUD, while `building.region` and `building.floor` are `group_system` write. The same gesture on the same screen needs sysadmin in one module and nothing in the other. | both CSVs |

There is no Visual Viewer / Author / Publisher / Manager role separation of any
kind — M29 starts from nothing.

---

## 10. Browser compatibility

- No WebGL capability detection anywhere; no `isWebGLAvailable`, no context
  probe, no `webglcontextlost` handler.
- No device-capability tiering. `setPixelRatio(Math.min(devicePixelRatio, 2))`
  is the only concession to hardware.
- Touch: `OrbitControls` handles gestures, but there is no tablet layout, no
  presentation mode and no full-screen mode.
- RTL: `maquette_viewer.scss` was not written with direction in mind; the 3D
  world and the UI share one DOM tree, so an RTL session risks mirroring the
  canvas container. Untested — there are no tours.

---

## 11. Data-quality risks

1. `realestate.building.floor` identity derives from a representative unit
   (§1.9) and cascades on its deletion.
2. `maquette_mesh_name` is free text with no uniqueness constraint at the
   database level — the picker enforces it procedurally, direct writes do not.
3. No detection of: unmatched meshes, properties without a mesh, properties
   without a floor plan, properties without a price, duplicate mappings, or
   invalid hierarchy links. M3's dashboard has no data source today.
4. `plan_image` and `floor_plan_image` are per-property binaries; identical
   floor plans for identical unit types are stored once **per unit**. A
   400-unit tower with 4 unit types stores 400 copies of 4 images.

---

## 12. Duplicate / dead frontend logic

Four independent frontend entry points render the same commercial concepts:

| Entry point | Module | Bundle |
|---|---|---|
| `maquette_viewer.js` (805 lines) | maquette | backend + frontend |
| `master_plan_2d.js` (696 lines) | maquette | backend + frontend |
| `plan_viewer.js` / `plan_editor.js` (910 lines) | plan | backend only |
| `maquette_3d_boot.js` | api | public embed |

`image_carousel.js` exists solely to re-export `CarouselDialog` from
`image_carousel_dialog.js` because the former imports backend-only modules —
a workaround that is documented in the file and works, but signals the bundle
split was retrofitted.

`glb_viewer.js` (145 lines) is a second, simpler GLB viewer field widget that
duplicates the loading logic in `maquette_viewer.js` with its own dynamic
import block.

---

## 13. Migration risks

Three existing migration scripts (`maquette/0.2`, `0.3`, `0.4`,
`plan/0.2`) show the data has already been reshaped several times. Anything
this upgrade does must preserve, without forcing a remap:

- uploaded `maquette_glb`, `maquette_env_hdr`, `master_plan_2d`
- every `maquette_mesh_name` string
- `realestate.building.floor` rows and their representative-unit links
- `realestate.building.region` and `realestate.plan.region` polygons
- `maquette_default_camera` JSON
- `interior_glb`, `floor_plan_image`, `plan_image`, `elevation_sheet`
- `realestate.embed.token` rows and the URLs already handed out

Because there is **no production data in this environment**, migration
correctness has to be proven against seeded representative fixtures and
classified rather than assumed — the same discipline Module 4's commission
migration used.

---

## 14. What this means for the upgrade

The good news is structural: **Rule 1 and the dangerous half of Rule 2 are
already honoured.** There is one property master, and reservations already go
through Developer's atomically-locked service. Three.js is single-sourced and
already lazy.

The work concentrates in four places:

1. **Commercial truth.** Everything visible — colour, price, availability, the
   Reserve button's own gate — reads legacy `property.state` and `base_price`
   instead of Developer's authority. This is the largest correctness gap and it
   spans 2D, 3D, the API image route and the stored `target_state`.
2. **Failure behaviour.** WebGL failure, malformed GLB and missing GLB all end
   at a red box. Rule 4 requires 3D → 2D → details.
3. **Security and tenancy.** No roles, no record rules, no `company_id`, one
   ACL row that makes every employee a production visual author, and an
   unauthenticated asset route.
4. **Nothing is tested.** Zero tests across both modules, so every constraint
   listed in §2 is currently a claim rather than a guarantee.

None of these requires touching a frozen module. All four are solvable inside
the visual layer by consuming services the frozen modules already expose.

**No destructive architectural decision is required to proceed**, so
implementation begins at M1 without pausing.
