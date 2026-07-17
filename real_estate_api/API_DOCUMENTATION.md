# Real Estate Public API — REST Documentation

A versioned REST layer (`/api/v1/...`) and an iframe embed surface
(`/embed/v1/...`) over the ATMTA real-estate suite. This document is
the contract for 3rd-party websites and integrations.

**Module:** `real_estate_api` · **Version:** 0.1 · **API version:** v1

---

## Table of Contents

1. [Quickstart](#1-quickstart)
2. [Authentication & Database Selection](#2-authentication--database-selection)
3. [Common Patterns](#3-common-patterns)
4. [Catalog](#4-catalog)
   1. [Developers](#41-developers)
   2. [Projects](#42-projects)
   3. [Buildings](#43-buildings)
   4. [Units / Properties](#44-units--properties)
   5. [Map data (projects + properties on a map)](#45-map-data-projects--properties-on-a-leaflet--google-map)
5. [2D Plan Drill API](#5-2d-plan-drill-api)
   1. [How 2D data is stored](#51-how-2d-data-is-stored)
   2. [Build your own 2D viewer](#56-build-your-own-2d-viewer)
6. [3D Maquette API](#6-3d-maquette-api)
   1. [How 3D data is stored](#61-how-3d-data-is-stored)
   2. [Build your own 3D viewer](#65-build-your-own-3d-viewer)
7. [Interests (lead capture)](#7-interests-lead-capture)
8. [Customer Contact Sync](#8-customer-contact-sync)
9. [Embed Tokens & Iframe Flow](#9-embed-tokens--iframe-flow)
10. [Customer Portal](#10-customer-portal)
    1. [Interests](#101-interests-get-interests)
    2. [Viewings](#102-viewings-get-viewings)
    3. [Contracts](#103-contracts-get-contracts)
    4. [Installments](#104-installments-get-installments)
    5. [Payments](#105-payments-get-payments)
    6. [Worked example — "My Account" page](#106-worked-example--my-account-page)
11. [Downloads (PDF / ZIP)](#11-downloads-pdf--zip)
    1. [By-ref (per-customer)](#111-by-ref-per-customer)
    2. [Top-level (manager)](#112-top-level-manager)
    3. [Zip bundle per contract](#113-zip-bundle-per-contract)
    4. [Statement per partner](#114-statement-per-partner)
    5. [`?template=` semantics](#115-template-semantics)
12. [Error Reference](#12-error-reference)
13. [Rate Limits](#13-rate-limits)
14. [What we never expose](#14-what-we-never-expose)
15. [Postman Collection](#15-postman-collection)

---

## 1. Quickstart

Assuming the Odoo host is `https://erp.atmta.com` and the database is
`atmta_prod`, this is the smallest working example:

```bash
curl -X GET "https://erp.atmta.com/api/v1/projects?limit=5" \
  -H "X-Odoo-Database: atmta_prod"
```

Response:

```json
{
  "results": [
    {
      "id": 28,
      "code": "DEMO",
      "name": "Demo Compound",
      "project_type": "residential",
      "status": "planned",
      "developer_id": null,
      "developer_name": "",
      "city": "",
      "country": "",
      "cover_image_url": "/api/v1/image/realestate.project/28/master_plan_2d?unique=20260531114240&w=1280&h=720",
      "has_2d_plan": true,
      "has_3d_maquette": true,
      "maquette_status": "partial",
      "maquette_unit_count": 22,
      "maquette_total_units": 38,
      "unit_count": 38,
      "available_unit_count": 22,
      "starting_price": 1000000.0,
      "currency": "USD",
      "currency_symbol": "$"
    }
  ],
  "limit": 5,
  "offset": 0
}
```

Catalog reads are **public** — no key required. To mint embed tokens or
submit leads with `Idempotency-Key`, see [Authentication](#2-authentication--database-selection).

---

## 2. Authentication & Database Selection

### Required headers

| Header | When | Example |
|---|---|---|
| `X-Odoo-Database` | Always (multi-DB host) | `X-Odoo-Database: atmta_prod` |
| `Authorization: Bearer <key>` | Mutating routes (interests, embed-tokens) and any private read | `Authorization: Bearer 96ee21de8...` |
| `Origin` | Cross-origin browser requests | Set automatically by the browser |
| `Content-Type: application/json` | All POST bodies | — |

`X-API-Key: <key>` is accepted as a legacy alias for `Authorization:
Bearer`. If both are present, `Authorization` wins.

### Generating an API key

In the Odoo backend:

1. Log in as the user the key should belong to (must be **active**;
   `__system__` keys won't validate).
2. **Preferences** (top-right) → **Account Security** → **New API
   key** → Scope = `real_estate_api`.
3. Save the secret immediately — it's shown only once.

CLI alternative (admin only):

```bash
echo "from datetime import datetime, timedelta
print(env(user=2)['res.users.apikeys'].sudo()._generate(
    scope='real_estate_api',
    name='website-server',
    expiration_date=datetime.now()+timedelta(days=90),
))
env.cr.commit()" | python3 odoo-bin shell -c odoo.conf -d atmta_prod --no-http
```

### Database selection rules

- The `X-Odoo-Database` header is mandatory on multi-database servers.
- Do **not** send a `session_id` cookie together with the header — the
  server returns **403** on conflict with the message:
  ```
  X-Odoo-Database='X' conflicts with the database already bound to this
  request ('Y'). Send the header without a session cookie, or match the
  bound database.
  ```
- A header naming a database that the server's `dbfilter` rejects does
  **not** silently fall through to a different one — the request goes
  on to whatever the default resolver would have produced (typically a
  404).

### Authentication errors

| HTTP | Code | When |
|---|---|---|
| `401` | `unauthorized` | API key required but absent |
| `401` | `unauthorized` | Key present but invalid / wrong scope / inactive user |
| `403` | `forbidden` | `X-Odoo-Database` header conflicts with a session cookie |
| `429` | `rate_limited` | Too many requests; see [Rate Limits](#11-rate-limits) |

---

## 3. Common Patterns

### URL conventions

- All API paths begin with `/api/v1/`.
- Resource names are plural nouns: `/projects`, `/buildings`, `/units`.
- Drill paths read left-to-right: `/projects/<id>/buildings`,
  `/buildings/<id>/units`.
- `<int:id>` parameters are required and must match an existing
  publicly-visible record. Missing or hidden records return **404**.

### Pagination

List endpoints honour:

| Param | Default | Max | Meaning |
|---|---|---|---|
| `limit` | `20` | `100` | Rows per page |
| `offset` | `0` | — | Rows to skip |

The total count comes back in the **`X-Total-Count`** response header
(not in the body — keeps the body cacheable).

```bash
curl -I "https://erp.atmta.com/api/v1/projects?limit=10&offset=20" \
  -H "X-Odoo-Database: atmta_prod"
# →
# HTTP/1.1 200 OK
# X-Total-Count: 31
# Content-Type: application/json; charset=utf-8
```

### Response envelopes

- **List**: `{ "results": [...], "limit": N, "offset": M }` plus
  `X-Total-Count` header.
- **Detail**: the resource object directly.
- **Error**: `{ "error": { "code": "...", "message": "...", "details": ... } }`.

### Field-level guarantees

- Image URLs are absolute paths under `/api/v1/image/MODEL/ID/FIELD?...`. The optional `?w=&h=` triggers on-the-fly resize; `?unique=` is a cache-buster derived from the record's `write_date`.
  Append `?unique=YYYYMMDDHHMMSS` is already on the URL — browsers cache
  by URL, and the value rolls when the record is updated.
- `null` means "no value" (e.g. a missing developer). Empty strings
  (`""`) mean the field exists but is blank.
- Monetary amounts are floats in the resource's native currency. The
  currency code and symbol are emitted side-by-side; the website is
  expected to convert if it needs to display in a different one.
- Dates are ISO-8601 (`YYYY-MM-DD`). Datetimes likewise (`YYYY-MM-DDTHH:MM:SS`).

### CORS

Cross-origin requests work if the calling origin is in the
`real_estate_api.cors.allowed_origins` config parameter (comma-separated).

Preflight (`OPTIONS`) responses include:

```
Access-Control-Allow-Origin: <echoed origin>
Access-Control-Allow-Methods: GET, POST, OPTIONS
Access-Control-Allow-Headers: Content-Type, X-API-Key, Authorization, X-Odoo-Database, Idempotency-Key
Access-Control-Max-Age: 600
Vary: Origin
```

Requests from origins NOT in the allowlist get a normal 2xx/4xx
response **without** any `Access-Control-Allow-Origin` header — the
browser blocks them, but the server does not signal failure.

### Idempotency

`POST /api/v1/interests` accepts an `Idempotency-Key: <opaque-string>`
header (≤ 64 chars). Retries with the same key inside the lead's
retention window return the original lead with
`"idempotent_replay": true`.

---

## 4. Catalog

### 4.1 Developers

A "developer" is a `res.partner` who is the `developer_id` of at least
one publicly-listed project.

#### `GET /api/v1/developers`

Query params: `limit`, `offset`.

```bash
curl "https://erp.atmta.com/api/v1/developers?limit=5" \
  -H "X-Odoo-Database: atmta_prod"
```

```json
{
  "results": [
    {
      "id": 34,
      "name": "Colleen Diaz",
      "logo_url": "/api/v1/image/res.partner/34/image_256?unique=20260520112514",
      "city": "Fremont",
      "country": "United States",
      "project_count": 1
    }
  ],
  "limit": 5,
  "offset": 0
}
```

#### `GET /api/v1/developers/<id>`

Returns the same fields as the list entry plus `website`, `email`,
`phone`, `mobile`, `description` (free text from `res.partner.comment`).

Errors: `404 not_found` if the partner is not a developer of any
publicly-listed project.

#### `GET /api/v1/developers/<id>/projects`

Listing of the developer's projects, paginated. Same shape as
`/api/v1/projects` (summary depth).

---

### 4.2 Projects

A `realestate.project` represents one development.

#### `GET /api/v1/projects`

Query params:

| Param | Type | Meaning |
|---|---|---|
| `limit` / `offset` | int | Pagination |
| `city` | string | `ilike` filter on `city` (max 60 chars) |
| `country` | string | Two-letter `res.country.code` |
| `developer_id` | int | Exact match |
| `q` | string | `ilike` on `name` (max 80 chars) |

```bash
curl "https://erp.atmta.com/api/v1/projects?city=Cairo&limit=5" \
  -H "X-Odoo-Database: atmta_prod"
```

#### `GET /api/v1/projects/<id>`

Returns the **detail** envelope:

```json
{
  "id": 28,
  "code": "DEMO",
  "name": "Demo Compound",
  "project_type": "residential",
  "status": "planned",
  "developer_id": null,
  "developer_name": "",
  "city": "",
  "district": "",
  "country": "",
  "cover_image_url": "/api/v1/image/realestate.project/28/master_plan_2d?unique=20260531114240&w=1280&h=720",
  "has_2d_plan": true,
  "has_3d_maquette": true,
  "maquette_status": "partial",
  "maquette_unit_count": 22,
  "maquette_total_units": 38,
  "unit_count": 38,
  "available_unit_count": 22,
  "reserved_unit_count": 8,
  "sold_unit_count": 0,
  "starting_price": 1000000.0,
  "price_max": 1600000.0,
  "currency": "USD",
  "currency_symbol": "$",
  "description_html": "",
  "address_line": "",
  "latitude": 30.1993652,
  "longitude": 39.8389433,
  "start_date": null,
  "expected_completion_date": null,
  "total_land_area_sqm": 0.0,
  "total_built_up_area_sqm": 0.0,
  "boundary_polygon": [
    {"sequence": 10, "latitude": 30.19977,   "longitude": 39.8391402},
    {"sequence": 20, "latitude": 30.1997654, "longitude": 39.8396498}
  ]
}
```

**`status`** is a public-facing label, mapped one-to-one from the
internal state:

| Internal `state` | Public `status` |
|---|---|
| `planning` | `planned` |
| `construction` | `under_construction` |
| `marketing` | `selling` |
| `handover` | `handover` |
| `completed` | `completed` |

Projects in any other state (`cancelled`, or future internal states) do
not appear in the catalog at all.

**`has_2d_plan`** flips to `true` whenever the `/api/v1/projects/<id>/plan-2d`
endpoint has anything to show — including *picker mode* (see §5). Concretely:
the project has a `master_plan_2d` image, **or** a `main_property_id`, **or**
at least one top-level property with its own `plan_image`. Gate a "View 2D"
button on this — it will not lie about drillability.

**3D mesh fields** — these three summarize the state of the 3D model
without a second round-trip to `/maquette-3d`:

| Field | Meaning |
|---|---|
| `maquette_status` | `not_uploaded` (no GLB), `uploaded_no_mapping` (GLB but zero clickable units), `partial` (some units mapped), `mapped` (all units mapped) |
| `maquette_unit_count` | Units in this project that have a `maquette_mesh_name` set |
| `maquette_total_units` | Total units (leaf `hierarchy_level='unit'`) in this project |

Use `maquette_status` — not `has_3d_maquette` — when the caller cares
about interactivity. `has_3d_maquette: true` only guarantees the GLB
exists; if `maquette_status == 'uploaded_no_mapping'` the scene renders
but nothing is clickable.

Errors: `404 not_found`.

---

### 4.3 Buildings

A building is a `realestate.property` with
`hierarchy_level == 'building'` attached to a project.

#### `GET /api/v1/projects/<id>/buildings`

Query params: `limit`, `offset`.

```bash
curl "https://erp.atmta.com/api/v1/projects/28/buildings" \
  -H "X-Odoo-Database: atmta_prod"
```

```json
{
  "results": [
    {
      "id": 84,
      "name": "Tower A",
      "property_code": "BLD-TOWERA",
      "hierarchy_level": "building",
      "project_id": 28,
      "parent_id": 83,
      "status": "available",
      "cover_image_url": null,
      "has_2d_plan": true,
      "has_3d_interior": false,
      "maquette_mesh_name": "Tower_A",
      "area_sqm": 0.0,
      "floors_count": 10,
      "units_count": 32,
      "thumb_url": "/api/v1/image/realestate.property/84/plan_image?unique=...&w=400&h=300",
      "currency": "USD",
      "currency_symbol": "$"
    }
  ],
  "limit": 20,
  "offset": 0
}
```

**`maquette_mesh_name`** — string that names this property's geometry
inside the project's `maquette_glb`. Empty string when this row isn't
wired to a mesh, or when the project has no GLB. See §6.1 for the full
mesh-linkage model.

---

### 4.4 Units / Properties

Units (`hierarchy_level == 'unit'`) and rooms (`'room'`) are leaves. To
get to them, drill from a building.

#### `GET /api/v1/buildings/<id>/units`

Query params:

| Param | Type | Meaning |
|---|---|---|
| `limit` / `offset` | int | Pagination |
| `status` | `available` \| `reserved` \| `sold` | Filter on public sale status |
| `min_bedrooms` | int | Inclusive lower bound |

Sale status mapping (internal `sale_status` → public `status`):

| Internal | Public |
|---|---|
| `for_sale` | `available` |
| `reserved` | `reserved` |
| `under_contract` | `reserved` |
| `sold` | `sold` |
| `not_listed` | `hidden` (not exposed in catalog) |

```bash
curl "https://erp.atmta.com/api/v1/buildings/84/units?status=available&min_bedrooms=2" \
  -H "X-Odoo-Database: atmta_prod"
```

#### `GET /api/v1/units/<id>`

Returns the detail envelope for a leaf:

```json
{
  "id": 105,
  "name": "Apartment 304",
  "property_code": "DEMO-001",
  "hierarchy_level": "unit",
  "project_id": 28,
  "parent_id": 87,
  "status": "available",
  "cover_image_url": "/api/v1/image/realestate.property/105/image_1920?unique=...&w=800&h=600",
  "has_2d_plan": true,
  "has_3d_interior": true,
  "maquette_mesh_name": "mesh748927950_1",
  "area_sqm": 210.0,
  "bedrooms": 3,
  "bathrooms": 2,
  "floor_number": 3,
  "price": 1050000.0,
  "furnished_status": "unfurnished",
  "currency": "USD",
  "currency_symbol": "$",
  "description": "Spacious 3-bedroom unit with sea view.",
  "property_type": "Apartment",
  "latitude": 30.20,
  "longitude": 39.84,
  "has_kitchen": true,
  "has_balcony": true,
  "living_room_count": 1,
  "gallery": [
    {
      "id": 12,
      "name": "Living room",
      "url": "/api/v1/image/property.image/12/image_1024?unique=...",
      "thumb_url": "/api/v1/image/property.image/12/image_256?unique=...",
      "video_url": ""
    }
  ],
  "plan_image_url": "/api/v1/image/realestate.property/105/plan_image?unique=...&w=1920&h=1080",
  "floor_plan_image_url": "/api/v1/image/realestate.property/105/floor_plan_image?unique=...&w=1920&h=1080",
  "spec_tags": ["marble floors", "smart-home wiring"],
  "maquette_color_override": "#22c55e"
}
```

**`maquette_mesh_name`** — see §6.1. Empty when the row isn't linked
to a mesh in the project's `maquette_glb`.

**`maquette_color_override`** — hex string that beats the default
state-based color when the 3D viewer paints this unit's mesh. Empty
means "use the state color".

#### `GET /api/v1/properties/<id>`

Generic property detail — works for any node in the hierarchy
(compounds, buildings, floors, units, rooms) as long as the record is
publicly visible (`state in available/reserved/sold`).

### 4.5 Map data (projects + properties on a Leaflet / Google map)

Two read-only endpoints feed a map view of the whole catalog. Same
visibility rules as the rest of §4 (cancelled / draft / inactive
records are never returned).

#### Coordinate rule

A record where both `latitude` and `longitude` are `0` is treated as
**"no coordinates set"** and excluded — we never plot a point at the
prime meridian / equator as a fallback. To make this visible to the
client (so it can show "N hidden from map"), both endpoints return a
`missing_coordinates_count` field alongside the results:

| Endpoint | Counts |
|---|---|
| `/api/v1/map/projects` | Publicly-visible projects with no coordinates set (ignores `bbox`; respects `developer_id`) |
| `/api/v1/map/properties` | Publicly-visible properties matching the same `project_id`/`hierarchy_level`/`state` filters but with no coordinates |

If `missing_coordinates_count > 0` on your dashboard, that's the
catalog team's signal to fill in those records — the API will never
guess a fallback location.

#### `GET /api/v1/map/projects`

```bash
curl "https://erp.atmta.com/api/v1/map/projects?limit=500" \
  -H "X-Odoo-Database: atmta_prod"
```

Query parameters:

| Param | Default | Notes |
|---|---|---|
| `limit` | `500` | Hard max `5000` |
| `offset` | `0` | Standard pagination |
| `developer_id` | — | Filter to one developer |
| `bbox` | — | `lat_min,lng_min,lat_max,lng_max` — viewport filter. Malformed → `400` (strict, never silently widened). |
| `include_boundary` | `false` | When `true`, each entry includes `boundary_points: [{sequence, latitude, longitude, label}, …]` — the polygon outline of the plot. Returns `[]` if no boundary configured. |

Response (200):

```json
{
  "results": [
    {
      "id": 28,
      "code": "DEMO",
      "name": "Demo Compound",
      "status": "selling",
      "latitude": 30.0444,
      "longitude": 31.2357,
      "city": "Cairo",
      "country": "Egypt",
      "country_code": "EG",
      "developer_id": null,
      "developer_name": "",
      "cover_image_url": "/api/v1/image/realestate.project/28/master_plan_2d?unique=...&w=400&h=300",
      "has_2d_plan": true,
      "has_3d_maquette": true,
      "unit_count": 38,
      "available_unit_count": 22
    }
  ],
  "total_count": 27,
  "missing_coordinates_count": 4,
  "limit": 500,
  "offset": 0
}
```

#### `GET /api/v1/map/properties`

```bash
curl "https://erp.atmta.com/api/v1/map/properties?project_id=28&limit=1000" \
  -H "X-Odoo-Database: atmta_prod"
```

Query parameters:

| Param | Default | Notes |
|---|---|---|
| `limit` | `1000` | Hard max `5000` |
| `offset` | `0` | Standard pagination |
| `project_id` | — | Filter to one project |
| `hierarchy_level` | — | `compound \| building \| floor \| unit \| room`. Unknown value → `400`. |
| `state` | — | `available \| reserved \| sold`. Unknown value → `400`. Omit to include all three. |
| `bbox` | — | `lat_min,lng_min,lat_max,lng_max` viewport filter |

Response (200):

```json
{
  "results": [
    {
      "id": 84,
      "property_code": "BLD-TOWERA",
      "name": "Tower A",
      "hierarchy_level": "building",
      "state": "available",
      "latitude": 30.0451,
      "longitude": 31.2364,
      "city": "Cairo",
      "district": "",
      "country": "Egypt",
      "country_code": "EG",
      "property_type": "Residential Tower",
      "project_id": 28,
      "project_name": "Demo Compound",
      "base_price": 0.0,
      "currency": "$",
      "area_sqm": 3000.0,
      "cover_image_url": "/api/v1/image/realestate.property/84/image_1920?unique=...&w=400&h=300"
    }
  ],
  "total_count": 38,
  "missing_coordinates_count": 12,
  "limit": 1000,
  "offset": 0
}
```

`missing_coordinates_count` tells the client how many properties match
the same filters (project / hierarchy / state) but have no
coordinates set — useful for a "12 properties not on the map" hint
under the viewport, mirroring the in-house dashboard's behaviour.

#### Rendering hints for a self-built map

1. **Centroid + boundary** — `latitude`/`longitude` on a project is its
   centroid (auto-recomputed from `boundary_points` when those are set).
   Use the centroid as the marker; only fetch `include_boundary=true`
   when the user zooms in enough to render the polygon.
2. **Color by state** — properties: green `#22c55e` available, amber
   `#f59e0b` reserved, grey `#6b7280` sold. Projects: blue `#0d6efd`
   selling, teal `#10b981` under_construction, neutral grey otherwise.
3. **Cluster at low zoom** — Leaflet's `markercluster` plugin handles
   it. Cluster centroids should not show project info — that needs
   individual markers.
4. **Click a marker → unit detail** — for properties, fire
   `GET /api/v1/units/<id>` for the full record (gallery, floor plan).
   For projects, drill into `GET /api/v1/projects/<id>` and from there
   into buildings/units.
5. **Reload on pan/zoom** — pass the new viewport via `bbox=` so you're
   only fetching what's visible. The endpoint's hard cap of 5000 means
   a worldwide zoom-out query won't dump the whole catalog in one
   response.

#### What the API does NOT expose

| Backend write | Why not exposed |
|---|---|
| Edit coordinates of a project/property | Backend-only — geo data is curated, not crowd-sourced |
| Add/remove boundary points | Backend-only |
| Cluster radius / styling | Pure client concern |

#### Two-stage drill — render a full map of every project AND its plots

The recommended interaction for a Leaflet/Google map page is:

```
 ┌──────────────────────────────────────────────────────────────────┐
 │ STAGE 1: world view                                              │
 │ ─────────────────                                                │
 │ GET /api/v1/map/projects?include_boundary=true                   │
 │   → one centroid marker per project                              │
 │   → one polygon per project that has boundary_points set         │
 │                                                                  │
 │ User clicks a project marker / polygon                           │
 └──────────────────────────────┬───────────────────────────────────┘
                                ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │ STAGE 2: project view (zoom in on the selected project)          │
 │ ───────────────────                                              │
 │ GET /api/v1/map/properties?project_id=<id>                       │
 │   → markers for every sub-property with coordinates set          │
 │     (compounds, buildings, floors, units)                        │
 │                                                                  │
 │ User clicks a property marker                                    │
 └──────────────────────────────┬───────────────────────────────────┘
                                ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │ STAGE 3: detail card                                             │
 │ ─────────────────                                                │
 │ GET /api/v1/units/<id>     (or /api/v1/properties/<id>)          │
 │   → full record: gallery, floor plan, price breakdown, etc.      │
 └──────────────────────────────────────────────────────────────────┘
```

#### Worked example — Leaflet, ~80 lines of JS

```html
<div id="map" style="width:100%; height:80vh;"></div>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>

<script>
const ERP_BASE = "https://erp.atmta.com";
const ERP_DB   = "atmta_prod";

const STATE_COLOR = {
  available: "#22c55e", reserved: "#f59e0b", sold: "#6b7280",
};
const PROJECT_COLOR = "#0d6efd";

async function apiGet(path) {
  const res = await fetch(`${ERP_BASE}${path}${path.includes("?") ? "&" : "?"}db=${ERP_DB}`);
  if (!res.ok) throw new Error(`${path} → ${res.status}`);
  return res.json();
}

const map = L.map("map").setView([24, 32], 4);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "© OpenStreetMap",
}).addTo(map);

const projectLayer  = L.layerGroup().addTo(map);
const propertyLayer = L.layerGroup().addTo(map);

// ─── STAGE 1: project markers + polygons ────────────────────────
async function renderProjects() {
  projectLayer.clearLayers();
  propertyLayer.clearLayers();
  const data = await apiGet("/api/v1/map/projects?include_boundary=true&limit=5000");

  for (const p of data.results) {
    // Centroid marker
    const marker = L.circleMarker([p.latitude, p.longitude], {
      radius: 9, color: PROJECT_COLOR, fillColor: PROJECT_COLOR, fillOpacity: 0.8,
    }).bindTooltip(`<b>${p.name}</b><br>${p.city} · ${p.status}`)
      .on("click", () => renderProjectPlots(p));
    projectLayer.addLayer(marker);

    // Lot boundary polygon (if configured)
    if (p.boundary_points && p.boundary_points.length >= 3) {
      const ring = p.boundary_points
        .sort((a,b) => a.sequence - b.sequence)
        .map(bp => [bp.latitude, bp.longitude]);
      L.polygon(ring, { color: PROJECT_COLOR, weight: 1, fillOpacity: 0.15 })
        .addTo(projectLayer);
    }
  }
  if (data.missing_coordinates_count > 0) {
    console.info(`${data.missing_coordinates_count} projects have no coordinates set`);
  }
}

// ─── STAGE 2: drill into one project, show its sub-properties ───
async function renderProjectPlots(project) {
  propertyLayer.clearLayers();
  const data = await apiGet(`/api/v1/map/properties?project_id=${project.id}&limit=5000`);

  for (const prop of data.results) {
    const color = STATE_COLOR[prop.state] || "#cccccc";
    const marker = L.circleMarker([prop.latitude, prop.longitude], {
      radius: 6, color, fillColor: color, fillOpacity: 0.85,
    }).bindTooltip(
      `<b>${prop.property_code}</b> · ${prop.hierarchy_level}<br>` +
      `${prop.name}<br><i>${prop.state}</i>`
    ).on("click", () => showDetail(prop));
    propertyLayer.addLayer(marker);
  }
  // Zoom to fit project + its plots
  const bounds = L.latLngBounds([[project.latitude, project.longitude]]);
  data.results.forEach(p => bounds.extend([p.latitude, p.longitude]));
  if (bounds.isValid()) map.fitBounds(bounds.pad(0.2));

  if (data.missing_coordinates_count > 0) {
    console.info(
      `${data.missing_coordinates_count} properties in ${project.name} have ` +
      `no coordinates and are not on the map.`
    );
  }
}

// ─── STAGE 3: full record for the side card ─────────────────────
async function showDetail(prop) {
  // Units have richer detail; non-units use the generic /properties/<id>
  const path = prop.hierarchy_level === "unit"
      ? `/api/v1/units/${prop.id}`
      : `/api/v1/properties/${prop.id}`;
  const full = await apiGet(path);
  alert(`${full.name}\nstate: ${full.state}\nprice: ${full.base_price} ${full.currency}`);
  // ↑ replace with your own side card / modal
}

renderProjects();
</script>
```

#### What the user sees

| Action | What the map renders |
|---|---|
| Page load | All projects with coords as blue dots; projects with boundaries get a faint blue polygon over their lot |
| Click a project | Map zooms to that project; plots inside it appear as smaller dots colored by state (green/amber/grey) |
| Click a plot | Side card / modal with full unit detail |
| Console (dev tools) | "N projects/properties have no coordinates" — the gap visible to the integrator without surfacing it to end users |

#### Why three calls, not one?

A single "give me everything for the whole catalog" endpoint would pay
two costs: a 5000+ row payload on every map render, and a viewport
that can't change semantics (project vs. plot zoom) without a full
re-fetch. The two-stage approach keeps the first paint small (just
projects) and pulls per-project plots on demand. With Leaflet
`markercluster` you can also blend stage 1 and stage 2 into one map —
clusters at world zoom decompose into individual project markers
which decompose into per-property markers when zoomed in further.

---

## 5. 2D Plan Drill API

The drill API returns a JSON tree describing one level of the plan
(image URL + clickable polygon regions). Drilling deeper is done by
the client: when a region's `target_has_plan` is `true`, the client
fetches `/api/v1/properties/<target_id>/plan-2d` to load the next
level. When `target_has_plan` is `false`, it's a leaf — the client
should treat the click as a unit selection.

### 5.1 How 2D data is stored

Before the JSON shape makes sense, here's the backing data model.
Everything is plain Odoo records — nothing is in flat files except
the image bytes themselves.

```
┌─────────────────────────────────────────────────────────────────┐
│  realestate.project                                             │
│  ────────────────                                               │
│  master_plan_2d           Binary  ← the top-level site map      │
│  master_plan_2d_filename  Char                                  │
│  main_property_id         M2O     ← optional: delegate to       │
│                                     this property's plan        │
└─────────────────────────────────┬───────────────────────────────┘
                                  │ project_id (M2O)
                                  ▼
┌─────────────────────────────────────────────────────────────────┐
│  realestate.building.region                                     │
│  ───────────────────────────                                    │
│  project_id      M2O  → realestate.project                      │
│  property_id     M2O  → realestate.property  (any building)     │
│  polygon         Char (JSON list of [x%, y%])                   │
│  label, color, sequence                                         │
│                                                                 │
│  Polygons drawn ON the project's master plan; each row points   │
│  at one property (typically a compound/building/villa).         │
└─────────────────────────────────────────────────────────────────┘

                                  │ then drilling deeper into a
                                  │ property that has its own
                                  ▼ plan_image

┌─────────────────────────────────────────────────────────────────┐
│  realestate.property                                            │
│  ────────────────────                                           │
│  plan_image            Binary (Image)  ← this property's own    │
│                                          plan (compound layout, │
│                                          building elevation,    │
│                                          floor plate, …)        │
│  plan_image_filename   Char                                     │
│  has_plan_image        Bool (computed; depends on plan_image)   │
└─────────────────────────────────┬───────────────────────────────┘
                                  │ parent_property_id (M2O)
                                  ▼
┌─────────────────────────────────────────────────────────────────┐
│  realestate.plan.region                                         │
│  ───────────────────────                                        │
│  parent_property_id  M2O  → property whose plan_image carries it│
│  target_property_id  M2O  → child property the region opens    │
│  polygon             Char (JSON list of [x%, y%])               │
│  label, color, sequence                                         │
│  target_state            related (live: available/reserved/…)   │
│  target_hierarchy_level  related                                │
│                                                                 │
│  CHECK: target_property_id must be a DIRECT CHILD of parent     │
│  CHECK: polygon ≥ 3 vertices, each value in [0, 100]            │
│  UNIQUE (parent_property_id, target_property_id)                │
└─────────────────────────────────────────────────────────────────┘
```

#### Two region models, one JSON shape

| Layer | Backing model | Pointer FK | Where polygons are drawn |
|---|---|---|---|
| Project master plan | `realestate.building.region` | `property_id` | On the project's `master_plan_2d` image |
| Property internal plan | `realestate.plan.region` | `target_property_id` | On the property's own `plan_image` |

The two endpoints (`/projects/<id>/plan-2d` and
`/properties/<id>/plan-2d`) merge these into a single response shape so
the client never needs to know which model the row came from. Every
field has the same name and same meaning regardless of source.

#### Example row — what a region actually looks like

A property "Tower A" (`id=84`, hierarchy_level=`building`) carries a
plan image of its elevation. Three polygon regions over that image
point at its three floors:

```
realestate_property                 (the host of the plan)
─────────────────────────────────────────────────────────────────
 id                  84
 property_code       BLD-TOWERA
 hierarchy_level     building
 parent_id           83             (the compound)
 project_id          28
 has_plan_image      true
 plan_image_filename tower_a_elevation.png

realestate_plan_region              (3 rows; one per floor polygon)
─────────────────────────────────────────────────────────────────
 id  parent_property_id  target_property_id  label    polygon
 ──  ──────────────────  ──────────────────  ───────  ─────────────────────────────────
 3   84                  87                  Floor 3  [[10,12],[90,12],[90,30],[10,30]]
 4   84                  86                  Floor 2  [[10,32],[90,32],[90,50],[10,50]]
 5   84                  85                  Floor 1  [[10,52],[90,52],[90,70],[10,70]]
```

And `GET /api/v1/properties/84/plan-2d` turns the rows above into the
JSON shown in §5.3 below.

#### Why polygons are JSON-as-string, not relational

```
   polygon = '[[10.5, 12.0], [40.2, 12.0], [40.2, 35.8], [10.5, 35.8]]'
```

- **Percentages**, not pixels — the same coordinates render correctly
  whether the image is shown 800×600 or 1920×1080.
- **JSON in one Char column** instead of a child vertex table because
  polygons are atomic — you always read them whole, parse, draw.
- **Validation in Python** (`_check_polygon`): valid JSON, ≥3 vertices,
  every `[x, y]` numeric, every value in `[0, 100]`.

#### Where the image bytes live

`master_plan_2d` and `plan_image` are `fields.Image(attachment=True)`,
which means Odoo offloads the bytes from the parent table to a row in
`ir_attachment` (and from there to the filestore on disk when the
filestore is configured). The API never returns base64 of the binary;
it always returns a `/api/v1/image/<model>/<id>/<field>` URL, and the
client fetches the bytes on demand. That URL goes through a whitelist
+ visibility check before streaming (see §12).

#### What the API does NOT expose

| Backend write | Why not exposed |
|---|---|
| Create/edit/delete a polygon | Region authoring is a backend-only task by design — there's no `POST /plan-regions` |
| Upload a plan image | Only via the Odoo form |
| Reorder regions (sequence) | Backend-only |
| Reassign `target_property_id` | Backend-only |

The API is **read-only on this layer**. Catalog + drill payloads + image proxy. Nothing else.

### `GET /api/v1/projects/<id>/plan-2d`

> **Consumer migration note — picker mode.** As of this version the
> endpoint returns three distinct shapes (master-plan, delegate,
> picker). If your renderer previously did the equivalent of
> `drawImage(tree.image_url); drawRegions(tree.regions);` it must now
> add a picker branch at the top:
>
> ```js
> if (tree.is_picker) {
>     renderPickerCards(tree.drillable_children);   // tree.image_url is null here
>     return;
> }
> // otherwise: existing master-plan / delegate render path
> drawImage(tree.image_url);
> drawRegions(tree.regions);
> ```
>
> Same change on the summary side: `has_2d_plan` is now `true` for
> picker-mode projects too, so any UI gating on it will start to surface
> "View 2D" buttons for projects that only have drillable children (no
> master plan of their own). That's the intended fix — the flag no
> longer under-reports drillability — but the click handler must reach
> the branch above or the button will feel broken.

Returns the project's **master plan**. If the project has no
`master_plan_2d` image but has a configured `main_property_id`, the
endpoint transparently returns that property's plan tree instead — the
project simply pre-points at the entry property.

```json
{
  "root_kind": "project",
  "root_id": 28,
  "name": "Demo Compound",
  "image_url": "/api/v1/image/realestate.project/28/master_plan_2d?unique=20260630104242&w=1920&h=1080",
  "regions": [
    {
      "id": 6,
      "target_id": 69,
      "target_name": "Tower A",
      "target_kind": "building",
      "target_has_plan": true,
      "target_status": "available",
      "target_mesh_name": "Tower_A",
      "label": "Tower A",
      "color": "#3b82f6",
      "polygon": "[[17.18, 28.64], [40.38, 27.84], [40.21, 42.67], [17.35, 42.56]]"
    }
  ],
  "drillable_children": [],
  "gallery": [],
  "breadcrumbs": [
    {"id": 28, "kind": "project", "name": "Demo Compound"}
  ]
}
```

If the project has **neither** `master_plan_2d` **nor** a `main_property_id`,
the endpoint switches to **picker mode**: `image_url` becomes `null`,
`regions` is empty, and `drillable_children` lists every top-level
property that has its own `plan_image`. The client renders one card per
entry; clicking a card calls `/api/v1/properties/<id>/plan-2d`.

```json
{
  "root_kind": "project",
  "root_id": 23,
  "name": "New Capital Compound",
  "image_url": null,
  "regions": [],
  "gallery": [],
  "drillable_children": [
    {
      "id": 31,
      "name": "New Capital Compound",
      "hierarchy_level": "compound",
      "thumb_url": "/api/v1/image/realestate.property/31/plan_image?unique=...&w=400&h=300",
      "maquette_mesh_name": ""
    }
  ],
  "is_picker": true,
  "breadcrumbs": [{"id": 23, "kind": "project", "name": "New Capital Compound"}]
}
```

### `GET /api/v1/properties/<id>/plan-2d`

Returns the drill tree rooted at a property. Used for every level
deeper than the project master plan.

```json
{
  "root_kind": "property",
  "root_id": 100,
  "name": "Madrid Compound",
  "hierarchy_level": "compound",
  "image_url": "/api/v1/image/realestate.property/100/plan_image?unique=...&w=1920&h=1080",
  "regions": [
    {
      "id": 20,
      "target_id": 101,
      "target_name": "Santiago Tower",
      "target_kind": "building",
      "target_has_plan": true,
      "target_status": "available",
      "target_mesh_name": "Santiago_Tower",
      "label": "Santiago",
      "color": "#07dd03",
      "polygon": "[[83.51,28.88],[59.86,28.28],[60.08,41.65],[82.99,42.36]]"
    }
  ],
  "drillable_children": [
    {
      "id": 101,
      "name": "Santiago Tower",
      "hierarchy_level": "building",
      "thumb_url": "/api/v1/image/realestate.property/101/plan_image?unique=...&w=240&h=150",
      "maquette_mesh_name": "Santiago_Tower"
    }
  ],
  "gallery": [],
  "breadcrumbs": [
    {"id": 28,  "kind": "project",  "name": "Demo Compound"},
    {"id": 100, "kind": "property", "name": "Madrid Compound", "hierarchy_level": "compound"}
  ]
}
```

`breadcrumbs` is the **absolute** chain from the project down to the
current node (the server walks `parent_id` and prepends the project
itself). Render it as a clickable trail; each entry's `id` + `kind`
tells you which endpoint to call when the user clicks back up.

### Polygon format

`polygon` is a JSON-encoded string (so it round-trips cleanly as part
of a JSON response): a list of `[x%, y%]` vertices in the range
`0..100`. Render with SVG by emitting a `<polygon points="x,y x,y ...">`.

### Region target statuses

| `target_status` | Meaning | Client behaviour |
|---|---|---|
| `available` | Unit is for sale | Render clickable, default color |
| `reserved` | Reserved / under contract | Render clickable, amber tint |
| `sold` | Sold | Render clickable, grey tint, no "Inquire" CTA |
| `hidden` | Not in the public catalog | Don't render the region at all |
| `null` | The region targets a building/floor (no sale state) | Render clickable, default color |

**`target_mesh_name`** — the mesh name inside the project's
`maquette_glb` that visually represents this region's target property.
Empty string when the target isn't wired to 3D (or the project has no
GLB). Use this to cross-highlight 2D and 3D: hover the region → look up
`target_mesh_name` in the 3D scene → paint that mesh red.

Errors: `404 not_found` only when the project has **no** master plan,
**no** `main_property_id`, and **zero** top-level properties with a
`plan_image` (picker mode is empty). A property `/plan-2d` route 404s
when the property has no `plan_image`.

### 5.6 Build your own 2D viewer

If you're rendering the drill in your own UI (no iframe), the JSON
above is all you need. The viewer is just: load image, overlay SVG,
listen for polygon clicks, refetch on drill.

#### Minimal HTML/JS — a working drill viewer in ~80 lines

```html
<div id="re-viewer" style="position:relative; max-width:1100px; margin:0 auto;">
  <div id="re-breadcrumbs"></div>
  <div id="re-canvas" style="position:relative; display:inline-block;">
    <img id="re-plan" style="display:block; max-width:100%; max-height:80vh;
                              user-select:none; -webkit-user-drag:none;"/>
    <svg id="re-overlay" viewBox="0 0 100 100" preserveAspectRatio="none"
         style="position:absolute; inset:0; width:100%; height:100%;
                pointer-events:none;"></svg>
  </div>
  <div id="re-picker" style="display:none;"></div>
</div>

<script>
const ERP_BASE = "https://erp.atmta.com";
const ERP_DB   = "atmta_prod";

const COLOR = {
  available: null,        // use the region's own color
  reserved:  "#f59e0b",
  sold:      "#6b7280",
};

async function loadPlan(kind, id) {
  const url = `${ERP_BASE}/api/v1/${kind === "project" ? "projects" : "properties"}/${id}/plan-2d?db=${ERP_DB}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to load ${kind}:${id} — ${res.status}`);
  return res.json();
}

function renderBreadcrumbs(crumbs) {
  const el = document.getElementById("re-breadcrumbs");
  el.innerHTML = crumbs.map((c, i) => {
    const cur = i === crumbs.length - 1;
    return `<button data-kind="${c.kind}" data-id="${c.id}"
              ${cur ? "disabled" : ""}>${c.name}</button>`;
  }).join(" › ");
  el.querySelectorAll("button[data-id]").forEach(btn => {
    btn.onclick = () => render(btn.dataset.kind, +btn.dataset.id);
  });
}

function renderPicker(tree) {
  document.getElementById("re-canvas").style.display = "none";
  const p = document.getElementById("re-picker");
  p.style.display = "block";
  p.innerHTML = tree.drillable_children.map(c => `
    <button data-id="${c.id}" style="width:240px; margin:8px; padding:0;
                                     border:1px solid #ddd; cursor:pointer;">
      <img src="${ERP_BASE}${c.thumb_url}" style="width:100%; display:block;"/>
      <div style="padding:8px;">${c.name}<br><small>${c.hierarchy_level}</small></div>
    </button>
  `).join("");
  p.querySelectorAll("button[data-id]").forEach(btn => {
    btn.onclick = () => render("property", +btn.dataset.id);
  });
}

function renderPlan(tree) {
  document.getElementById("re-canvas").style.display = "inline-block";
  document.getElementById("re-picker").style.display = "none";

  const img = document.getElementById("re-plan");
  img.src = ERP_BASE + tree.image_url;

  // Wait for natural dimensions, then set aspect-ratio so the SVG and
  // image stay locked together. preserveAspectRatio="none" on the SVG +
  // viewBox 0..100 means polygon coordinates land on the image regardless
  // of display size.
  img.onload = () => {
    document.getElementById("re-canvas").style.aspectRatio =
      `${img.naturalWidth} / ${img.naturalHeight}`;
  };

  const svg = document.getElementById("re-overlay");
  svg.innerHTML = "";
  for (const r of tree.regions) {
    if (r.target_status === "hidden") continue;    // strict: never render
    const pts = JSON.parse(r.polygon);
    const points = pts.map(([x, y]) => `${x},${y}`).join(" ");
    const fill = COLOR[r.target_status] ?? r.color;
    const poly = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
    poly.setAttribute("points", points);
    poly.setAttribute("fill", fill);
    poly.setAttribute("fill-opacity", "0.35");
    poly.setAttribute("stroke", fill);
    poly.style.cursor = "pointer";
    poly.style.pointerEvents = "auto";
    poly.addEventListener("mouseenter", () => poly.setAttribute("fill-opacity", "0.6"));
    poly.addEventListener("mouseleave", () => poly.setAttribute("fill-opacity", "0.35"));
    poly.addEventListener("click", () => {
      if (r.target_has_plan) {
        render("property", r.target_id);              // drill deeper
      } else {
        // Leaf — fetch the unit detail to show price, area, gallery
        fetch(`${ERP_BASE}/api/v1/units/${r.target_id}?db=${ERP_DB}`)
          .then(r => r.json())
          .then(showUnitCard);                        // your own modal
      }
    });
    svg.appendChild(poly);
  }
}

async function render(kind, id) {
  const tree = await loadPlan(kind, id);
  renderBreadcrumbs(tree.breadcrumbs);
  if (tree.is_picker) renderPicker(tree);
  else                renderPlan(tree);
}

// Entry: start at the project
render("project", 28);
</script>
```

#### Key rendering rules

1. **Always wrap the `<img>` + `<svg>` in a container with `aspect-ratio:
   <naturalWidth> / <naturalHeight>`** (set inline once the image loads).
   The SVG fills the container; `viewBox="0 0 100 100"
   preserveAspectRatio="none"` then makes each polygon `[x, y]` land on
   the image at exactly `(x%, y%)` regardless of display size.

2. **Decide the polygon fill from `target_status`, NOT from the region's
   own color**, when the target is a leaf unit — so sold/reserved units
   appear greyed-out / amber automatically. The region's `color` field
   is the developer-chosen base; status overrides it.

3. **Branch on `target_has_plan`**: `true` → drill via
   `/api/v1/properties/<target_id>/plan-2d`; `false` → it's a leaf, fetch
   `/api/v1/units/<target_id>` and show a side card / modal.

4. **Use the server's `breadcrumbs` array directly** — don't try to
   re-derive the chain from a click stack. The server walks `parent_id`
   and prepends the project, so the first crumb is always the project,
   the last is always the current node, and clicking any crumb is just
   `render(crumb.kind, crumb.id)`.

5. **`target_status === "hidden"`**: skip rendering that polygon
   entirely. Strict: never fall back to "show anyway with a warning" — a
   hidden unit must not appear in any visual surface.

#### Same coordinate system everywhere

Polygon coordinates `[x, y]` are percentages in `[0, 100]`. This is
deliberately a string field (not a JSON column) so it round-trips
through every JSON layer untouched. Parse with `JSON.parse(r.polygon)`
on the client.

#### Image URL specifics

Every image URL the API returns is rooted at `/api/v1/image/<model>/<id>/<field>`
with optional `?w=&h=` resize and a `?unique=YYYYMMDDHHMMSS` cache-buster
derived from the source record's `write_date`. The image proxy enforces
a whitelist + visibility check before streaming bytes, so a URL the API
gave you will always serve, and a URL crafted from a field name not in
the whitelist will 404. Pass the URL through verbatim — do not strip
the query string.

---

## 6. 3D Maquette API

### 6.1 How 3D data is stored

The 3D layer takes the **inverse** approach to 2D: where 2D has a row
per polygon that *points at* a property via FK, 3D has the property
*point at* a mesh inside a single GLB file via a string name.

```
┌─────────────────────────────────────────────────────────────────┐
│  realestate.project                                             │
│  ────────────────                                               │
│  maquette_glb              Binary  ← the .glb scene             │
│  maquette_glb_filename     Char                                 │
│  maquette_env_hdr          Binary  (optional .hdr/.exr for IBL) │
│  maquette_default_camera   Char    (optional preset)            │
│  has_maquette              Bool                                 │
│  maquette_unit_count       Int  (computed: properties whose     │
│                                  maquette_mesh_name is set)     │
└─────────────────────────────────┬───────────────────────────────┘
                                  │ project_id (M2O)
                                  ▼
┌─────────────────────────────────────────────────────────────────┐
│  realestate.property                                            │
│  ─────────────────────                                          │
│  maquette_mesh_name        Char  ← THIS is the link to 3D.      │
│                                    Exact mesh name inside the   │
│                                    project's GLB. Set by the    │
│                                    Auto-match wizard or the     │
│                                    unit picker in the backend.  │
│  maquette_color_override   Char  (optional hex, beats the       │
│                                   default state-based color)    │
│  floor_plan_image          Binary (attachment)                  │
│  floor_plan_pdf            Binary (attachment)                  │
│  interior_glb              Binary (optional per-unit interior)  │
│  elevation_sheet           Binary (elevation drawings)          │
└─────────────────────────────────────────────────────────────────┘
```

#### Direction of the link — opposite of 2D

| Layer | What carries the link | What it points at |
|---|---|---|
| **2D** | Region row → has FK to property | Region tells you "this polygon is property X" |
| **3D** | Property row → has Char column `maquette_mesh_name` | Property tells you "I'm the mesh named X inside the GLB" |

Why the asymmetry? In 2D each property can host many regions (one per
child it links to), so a separate row per region is the natural shape.
In 3D each property maps to exactly one mesh inside a single GLB, so
sticking the mesh name on the property itself avoids a whole extra
table.

#### One GLB per project; one mesh name per property

The GLB file holds *all* the geometry for the project — landscape,
buildings, individual units — as named meshes. The property row's
`maquette_mesh_name` is the string the GLB exporter assigned to that
unit's geometry (e.g. `"Villa_07"`, `"mesh748927950_1"`, or any other
string). The viewer raycasts a click → reads `mesh.name` →
looks it up in the `units[*]` array to find which property was clicked.

Properties whose `maquette_mesh_name` is empty are **not in the
maquette** — they exist in the catalog but the 3D viewer doesn't render
or react to them. This is intentional: not every unit needs to be
modeled in 3D, and partial coverage is normal.

#### Example row — what a 3D project actually holds

Project 28 "Demo Compound" has a GLB and 12 of its 38 properties carry
a mesh name:

```
realestate_project
─────────────────────────────────────────────────────────────────
 id                       28
 code                     DEMO
 name                     Demo Compound
 maquette_glb_filename    master_plan_demo.glb
 maquette_glb             (Binary, offloaded to ir_attachment row
                           1178 — file ~780 KB on filestore at
                           dd/dd4d6b492f5f4a167843ea07a8150818b1de8dd4)
 maquette_env_hdr         (null — no HDR uploaded)
 maquette_default_camera  ""

realestate_property        (6 of the 12 mapped units, abridged)
─────────────────────────────────────────────────────────────────
 id  property_code  hierarchy   state      maquette_mesh_name    color_override
 ──  ─────────────  ──────────  ─────────  ────────────────────  ──────────────
 54  DEMO-001       unit        reserved   DEMO-004_1            #000
 55  DEMO-002       unit        reserved   mesh2001573440        
 56  DEMO-003       unit        available  group1334676454       
 57  DEMO-004       unit        available  mesh748927950_1       
 58  DEMO-005       unit        available  DEMO-004_2            
 59  DEMO-006       unit        available  mesh1385259583_1      
```

`GET /api/v1/projects/28/maquette-3d` turns this into the JSON shape
shown in §6.2 below — `glb_url` for the file plus a `units[*]` array
with one entry per mapped property.

#### Where the GLB bytes live

`maquette_glb` is `fields.Binary(attachment=True)`, so the bytes are
in `ir_attachment.datas` (or on the filestore disk under
`<db>/filestore/<2-char-prefix>/<sha>` when filestore is configured).
The API returns `/api/v1/image/realestate.project/<id>/maquette_glb?…`,
which streams the bytes through the proxy with the same whitelist +
visibility check as images. Browsers cache by URL; the `unique=` token
rolls only when the GLB is replaced.

#### What the API does NOT expose

| Backend write | Why not exposed |
|---|---|
| Upload / replace the GLB | Only via the Odoo project form |
| Set/clear `maquette_mesh_name` on a property | Backend-only (the Auto-match wizard and unit picker do this internally) |
| Set `maquette_color_override` | Backend-only |
| Upload `env_hdr` or set `default_camera` | Backend-only |

The 3D API is **read-only**: descriptor + GLB URL + unit↔mesh mapping. Nothing else.

### `GET /api/v1/projects/<id>/maquette-3d`

Returns the descriptor needed to load and interact with the project's
3D model:

```json
{
  "project_id": 28,
  "name": "Demo Compound",
  "glb_url": "/api/v1/image/realestate.project/28/maquette_glb?unique=20260630104242",
  "glb_filename": "master_plan_demo.glb",
  "env_hdr_url": null,
  "default_camera": "{\"position\":[50,40,50],\"target\":[0,0,0]}",
  "units": [
    {
      "id": 54,
      "property_code": "DEMO-001",
      "name": "Villa 001",
      "mesh_name": "group1926126815",
      "state": "reserved",
      "base_price": 1050000.0,
      "currency": "$",
      "area_sqm": 210.0,
      "property_type": "Villa",
      "has_floor_plan": true,
      "color_override": "#FF8800"
    }
  ]
}
```

- **`glb_url`** serves a glTF binary (~MB) via the image proxy. The
  browser caches it by URL; the `unique=` parameter rolls when the file
  is replaced. Pass it through verbatim — do not strip the query string.
- **`env_hdr_url`** is optional; when present it's an environment map
  (`.hdr` / `.exr`) for image-based lighting.
- **`default_camera`** is a JSON string the viewer can `JSON.parse` —
  `{position: [x,y,z], target: [x,y,z]}`. Empty string = "no preset,
  use sensible defaults".
- **`units[].mesh_name`** is the exact mesh name inside the GLB.
  Click handling = raycast the scene, then look up `mesh.name` in this
  list. Meshes that don't map to any unit do nothing (no silent
  fallback).
- **`color_override`** is a hex string that overrides the default
  state-based color for that specific unit. Empty = "no override, use
  the state color".

Errors: `404 not_found` if `maquette_glb` is not uploaded on the
project.

### Unit detail from a 3D click

After a click resolves to a `unit_id`, fetch
`/api/v1/units/<id>` to render a side card with price, area, gallery,
floor plan, etc.

### 6.5 Build your own 3D viewer

If you're not using the embed iframe — same idea as the 2D recipe.
The JSON above is the complete contract: load the GLB, traverse the
scene to find each unit's `mesh_name`, color by state, attach a click
handler.

#### Minimal Three.js loader — a working maquette in ~100 lines

```html
<div id="re-3d" style="position:relative; width:100%; height:80vh;"></div>

<script type="importmap">
{
  "imports": {
    "three": "https://unpkg.com/three@0.160/build/three.module.js",
    "three/addons/": "https://unpkg.com/three@0.160/examples/jsm/"
  }
}
</script>

<script type="module">
import * as THREE from "three";
import { GLTFLoader }    from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RGBELoader }    from "three/addons/loaders/RGBELoader.js";

const ERP_BASE = "https://erp.atmta.com";
const ERP_DB   = "atmta_prod";

const STATE_COLOR = {
  available: 0x22c55e,
  reserved:  0xf59e0b,
  sold:      0x6b7280,
};

async function loadMaquette(projectId) {
  const res = await fetch(
    `${ERP_BASE}/api/v1/projects/${projectId}/maquette-3d?db=${ERP_DB}`
  );
  if (!res.ok) throw new Error(`maquette-3d ${projectId} → ${res.status}`);
  return res.json();
}

async function init(projectId) {
  const desc = await loadMaquette(projectId);
  const host = document.getElementById("re-3d");
  const W = host.clientWidth, H = host.clientHeight;

  // Renderer + scene + camera
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setSize(W, H);
  renderer.setPixelRatio(window.devicePixelRatio);
  host.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0xf3f4f6);

  const camera = new THREE.PerspectiveCamera(45, W / H, 0.1, 2000);
  // default_camera is a JSON string; safe-parse it.
  let camPreset = { position: [50, 40, 50], target: [0, 0, 0] };
  if (desc.default_camera) {
    try { camPreset = JSON.parse(desc.default_camera) || camPreset; }
    catch (_) { /* fall through to defaults */ }
  }
  camera.position.set(...camPreset.position);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.set(...camPreset.target);
  controls.update();

  scene.add(new THREE.AmbientLight(0xffffff, 0.6));
  const sun = new THREE.DirectionalLight(0xffffff, 0.8);
  sun.position.set(50, 80, 30); scene.add(sun);

  // Optional IBL — only when env_hdr_url is set.
  if (desc.env_hdr_url) {
    new RGBELoader().load(ERP_BASE + desc.env_hdr_url, (hdr) => {
      hdr.mapping = THREE.EquirectangularReflectionMapping;
      scene.environment = hdr;
    });
  }

  // Load the GLB and index its meshes by name.
  const gltf = await new GLTFLoader().loadAsync(ERP_BASE + desc.glb_url);
  scene.add(gltf.scene);

  // Build mesh_name → unit map. units that don't carry a mesh_name are
  // ignored on purpose (they exist in the catalog but aren't in the
  // maquette).
  const unitsByMesh = new Map();
  for (const u of desc.units) {
    if (u.mesh_name) unitsByMesh.set(u.mesh_name, u);
  }

  // Color every mapped mesh by state; leave unmapped meshes alone so the
  // ground/landscape geometry keeps its original look.
  gltf.scene.traverse((obj) => {
    if (!obj.isMesh) return;
    const unit = unitsByMesh.get(obj.name);
    if (!unit) return;
    const color = unit.color_override
        ? new THREE.Color(unit.color_override)
        : new THREE.Color(STATE_COLOR[unit.state] ?? 0xcccccc);
    obj.material = obj.material.clone();      // don't mutate shared mats
    obj.material.color = color;
    obj.userData.unit = unit;                  // for click lookup
  });

  // Click handling — raycast against the scene, then look up userData.
  const raycaster = new THREE.Raycaster();
  const mouse = new THREE.Vector2();
  renderer.domElement.addEventListener("click", (ev) => {
    const rect = renderer.domElement.getBoundingClientRect();
    mouse.x =  ((ev.clientX - rect.left) / rect.width)  * 2 - 1;
    mouse.y = -((ev.clientY - rect.top)  / rect.height) * 2 + 1;
    raycaster.setFromCamera(mouse, camera);
    const hits = raycaster.intersectObject(gltf.scene, true);
    for (const h of hits) {
      const u = h.object.userData.unit;
      if (u) { showUnitCard(u); return; }       // your own modal/side card
    }
  });

  // Render loop
  function tick() {
    controls.update();
    renderer.render(scene, camera);
    requestAnimationFrame(tick);
  }
  tick();

  // Resize handling — adjust on host container resize, not just window.
  new ResizeObserver(() => {
    const w = host.clientWidth, h = host.clientHeight;
    renderer.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }).observe(host);
}

function showUnitCard(u) {
  // For real life: fetch /api/v1/units/<u.id> for full detail (gallery,
  // floor plan, description). For demo:
  alert(`${u.name} — ${u.state} — ${u.base_price} ${u.currency}`);
}

init(28);
</script>
```

#### Key rendering rules

1. **Color only meshes you can resolve to a unit.** Iterate
   `units[*].mesh_name`, look each one up in the loaded scene, set the
   material color. Leave un-mapped geometry (ground, trees, landscape)
   untouched.

2. **Use `color_override` when non-empty, otherwise the state color.**
   This lets sales paint a specific unit a custom shade (e.g. "promo
   blue") without changing the catalog state.

3. **`userData.unit` is the click bridge.** Stash the unit object on
   each mesh's `userData` after coloring. On click, raycast → first hit
   with `userData.unit` is the answer. Meshes without `userData.unit`
   are inert by design.

4. **Don't trust the GLB to be small.** The current dev maquette is
   ~780 KB; production ones run 5–50 MB. Show a loader, lazy-load when
   the section scrolls into view, and let the browser cache it (the
   `unique=` cache-buster only rolls on real changes).

5. **Resize via `ResizeObserver`, not just `window.resize`.** The
   maquette is rarely full-screen — it's usually inside a card or panel
   whose size changes independently of the window.

6. **`default_camera` is optional and may be `""`** (empty). Always
   wrap `JSON.parse` in `try`/`catch` and fall back to sensible defaults
   so a bad preset can't break the viewer.

7. **`env_hdr_url` is optional** — only attempt to load it when the
   field is a non-null URL. Loading a missing HDR throws and dumps the
   whole scene if you don't guard.

#### Unit detail follow-up

After a click resolves to a `unit_id`, fetch
`/api/v1/units/<id>` for the full record (gallery, floor plan, interior
GLB if any, description, price breakdowns). The `units[*]` array in the
maquette endpoint deliberately holds **only what the maquette needs to
render** — a side card calls for richer data.

#### Same-domain vs cross-domain

The GLB is served from the Odoo origin. If your viewer page lives on a
different origin, you'll get a CORS preflight on the fetch. Add your
origin to `real_estate_api.cors.allowed_origins` (System Parameters);
no other config is needed — the proxy returns the right
`Access-Control-Allow-Origin` for whitelisted origins automatically.

---

## 7. Interests (lead capture)

### `POST /api/v1/interests`

Creates a `crm.lead` flagged `realestate_api_source=true`. No
authentication required, but the endpoint is rate-limited (default
5/hour per IP).

Request body:

```json
{
  "name": "Jane Doe",
  "email": "jane@example.com",
  "phone": "+966501234567",
  "project_id": 28,
  "unit_id": null,
  "message": "Interested in a 3BR unit.",
  "partner_external_ref": "site:user:1234"
}
```

Field rules:

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | string | yes | Max 120 chars |
| `email` | string | one of `email` or `phone` is required | RFC-ish format check |
| `phone` | string | (see above) | 6-20 digits with optional `+`, spaces, `-`, `()` |
| `message` | string | no | Max 2000 chars |
| `project_id` | int | no | Must reference a publicly-listed project |
| `unit_id` (alias: `property_id`) | int | no | Must be `available` or `reserved` |
| `partner_external_ref` | string | no | If present, links the lead to the partner with that ref. Must already exist (mint via [`POST /api/v1/partners`](#8-customer-contact-sync) first) — strict 404 on miss. |

Cross-checks: if both `project_id` and `unit_id` are given and the unit
belongs to a different project, the request is rejected with `400`.

Response (201):

```json
{
  "id": 49,
  "reference": "Website Interest — Demo Compound — Jane Doe",
  "status": "received"
}
```

### Idempotency

Send `Idempotency-Key: <opaque>` (any string ≤ 64 chars) to make
retries safe. The server treats two requests with the same key + same
email as the same logical submission:

```bash
curl -X POST "https://erp.atmta.com/api/v1/interests" \
  -H "X-Odoo-Database: atmta_prod" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: jane-doe-2026-06-22-001" \
  -d '{"name":"Jane Doe","email":"jane@example.com","project_id":28}'
```

A replay returns `200` (not `201`) with an additional
`"idempotent_replay": true` field.

### Error envelope

```json
{ "error": { "code": "bad_request", "message": "Invalid email format." } }
```

---

## 8. Customer Contact Sync

When the 3rd-party site has its own user accounts (registration, login,
profile pages), it can mirror each user as a `res.partner` in Odoo via
this endpoint. The 3rd-party's own user identifier is stored in
`realestate_api_external_ref` and is the upsert key, so the same call
covers both **create on signup** and **update on profile edit**.

### Why mirror users at all?

- A future `POST /api/v1/interests` can be extended to accept
  `partner_external_ref`, so lead capture attaches to the right Odoo
  contact instead of creating duplicates.
- Sales sees one row per real person rather than one lead per
  interaction.
- Lays the groundwork for a self-service portal later (visitor logs in
  on the 3rd-party site, sees their reservations pulled from Odoo).

### `POST /api/v1/partners`

Bearer auth required (this is a mutating, integration-only endpoint).

```bash
curl -X POST "https://erp.atmta.com/api/v1/partners" \
  -H "Authorization: Bearer 96ee21de8..." \
  -H "X-Odoo-Database: atmta_prod" \
  -H "Content-Type: application/json" \
  -d '{
    "external_ref": "site-user-1234",
    "name":         "Jane Doe",
    "email":        "jane@example.com",
    "phone":        "+201001234567",
    "street":       "12 Tahrir Street",
    "city":         "Cairo",
    "country":      "EG"
  }'
```

Field rules:

| Field | Type | Required | Notes |
|---|---|---|---|
| `external_ref` | string | yes | 1-128 chars, `[A-Za-z0-9._:-]`. The 3rd-party's stable user ID. Same value across update calls. |
| `name` | string | yes | Max 120 chars |
| `email` | string | no | RFC-ish format check |
| `phone` | string | no | 6-20 digits with optional `+`, spaces, `-`, `()` |
| `street` | string | no | Max 255 chars |
| `city` | string | no | Max 96 chars |
| `country` | string | no | ISO-3166 alpha-2 code, e.g. `EG`, `SA`, `US`. Unknown code → 400. |

Responses:

**Create (first time we see `external_ref`)** — HTTP 201:

```json
{
  "id": 4587,
  "external_ref": "site-user-1234",
  "created": true
}
```

**Update (same `external_ref` as a previous call)** — HTTP 200:

```json
{
  "id": 4587,
  "external_ref": "site-user-1234",
  "created": false
}
```

Only the fields you re-send are touched. Omitting `phone` does NOT
clear the existing phone — only `name` always overwrites (it's
required on every call).

**Conflict (`external_ref` is already held by a backend-created
contact)** — HTTP 409:

```json
{
  "error": {
    "code": "conflict",
    "message": "external_ref site-user-1234 is already held by a non-API contact (id=99). Pick a different ref or have an admin clear it."
  }
}
```

We never silently take over a contact we didn't mint via the API — pick
a different `external_ref` scheme (e.g. prefix it: `site:1234`) so it
can't collide with anything sales might create by hand.

#### Concurrency

If two parallel POSTs race past the in-Python upsert check with the
same `external_ref`, the loser hits the partial unique index and is
caught by a savepoint. The loser then returns 200 + `created: false`
with the winner's `id` — i.e. the operation is correct under
concurrency, just one of the two callers learns it didn't actually
create the row.

### `GET /api/v1/partners/by-ref/<external_ref>`

Look up a previously-synced partner by its 3rd-party reference.
Bearer auth required.

```bash
curl "https://erp.atmta.com/api/v1/partners/by-ref/site-user-1234" \
  -H "Authorization: Bearer 96ee21de8..." \
  -H "X-Odoo-Database: atmta_prod"
```

Response (200):

```json
{
  "id": 4587,
  "external_ref": "site-user-1234",
  "name":  "Jane Doe",
  "email": "jane@example.com",
  "phone": "+201001234567",
  "street": "",
  "city": "Cairo",
  "country": "EG"
}
```

Returns **404** if no API-created partner has that ref. Refs held by
backend-created contacts also read as not-found (we never leak the
existence of non-API partners through this lookup).

Use this when your site has lost the Odoo `id` (cache wipe, fresh
deploy, debugging) and needs to recover it from the ref. A 404 means
"not synced yet" — call `POST /api/v1/partners` to mint it.

### Storage model

API-created partners get two flags:

- `realestate_api_source = true` (filter chip in the backend)
- `realestate_api_external_ref = "<your-ref>"` (indexed, unique among API rows)

There is no separate "API contacts" model — they're regular
`res.partner` rows so sales workflows (assigning, merging, converting
leads to opportunities, …) work unchanged.

### Idempotency

The upsert semantics mean **the operation is naturally idempotent** —
re-sending the same body produces the same DB state. You don't need
the `Idempotency-Key` header here (it has no effect on this endpoint).

### Rate limit

Inherits the default authed throttle (300/min per API key). There is
no extra per-IP throttle like Interests has — the assumption is the
3rd-party server, not visitor browsers, calls this endpoint.

### Error envelope

```json
{ "error": { "code": "bad_request", "message": "'country' must be a 2-letter ISO code (e.g. 'EG')." } }
```

| HTTP | `code` | Cause |
|---|---|---|
| `400` | `bad_request` | Missing required field, bad email/phone, malformed `external_ref`, unknown country |
| `401` | `unauthorized` | API key missing / wrong scope |
| `409` | `conflict` | `external_ref` already held by a non-API contact |

---

## 9. Embed Tokens & Iframe Flow

The 2D drill viewer and the 3D maquette are exposed as **chromeless
HTML pages** that 3rd-party websites embed via `<iframe>`. Access is
controlled by a per-resource, time-limited, origin-pinned token.

### What this gives the 3rd-party developer

You drop a single `<iframe>` on your real-estate listing page. Visitors
get a fully-interactive 2D site plan drill-down (compound → building →
unit) **or** a 3D maquette of the project, with no Odoo chrome — just
your branding (colors, language, dark/light mode). Visitors clicking a
unit emit a `postMessage` your page can listen to (e.g. to pre-fill an
"I'm interested" form).

You DO NOT need to:
- Ship any Odoo JS to your site (the iframe brings its own).
- Store secrets in browser code (the API key stays server-side).
- Worry about CORS for the viewer itself (the iframe is same-origin
  with the API — only the token mint is cross-origin).

You DO need:
- An API key (a 40-char hex string) issued for your integration.
- A server-side endpoint that mints a fresh token per page-render and
  hands the URL to the browser.

### High-level flow

```
 STEP 1 — Server-to-server: your backend mints a token using the API key.
 STEP 2 — Page render: your backend injects <iframe src="<embed_url>"> into the HTML.
 STEP 3 — Browser loads the iframe. The iframe's own JS calls /api/v1/... for data.
 STEP 4 — Visitor interacts. The iframe posts events to your page; your page can
           reply with theme/navigation commands.

 ┌────────────────────┐                           ┌────────────────────┐
 │  Your website      │                           │  Real Estate API   │
 │  backend           │  (1) POST /embed-tokens   │  egyptairodoodev   │
 │  (Python/Node/PHP) │ ─── Bearer api_key ─────▶ │  (Odoo server)     │
 │                    │ ◀── {token, embed_url} ── │                    │
 └─────────┬──────────┘                           └─────────┬──────────┘
           │ (2) emits HTML with                            │
           │     <iframe src=embed_url>                     │
           ▼                                                │
 ┌────────────────────┐                                     │
 │  Visitor's browser │  (3) GET <embed_url>  (iframe)      │
 │                    │ ──────────────────────────────────▶ │
 │  ┌──────────────┐  │ ◀── 200 chromeless HTML + JS+CSS ── │
 │  │ Your page    │  │                                     │
 │  └──────┬───────┘  │  (3b) JS in iframe: GET /api/v1/... │
 │         │          │ ──────────────────────────────────▶ │
 │  ┌──────▼───────┐  │ ◀── JSON (regions, plan image) ──── │
 │  │  <iframe>    │  │                                     │
 │  │  drill-down  │  │  (3c) <img src=/api/v1/image/...>   │
 │  │  viewer      │  │ ──────────────────────────────────▶ │
 │  └──────┬───────┘  │ ◀── PNG/JPEG ────────────────────── │
 │         │          │                                     │
 │  (4) postMessage   │                                     │
 │     ⇅              │                                     │
 │  unitSelected,     │                                     │
 │  navigated, …      │                                     │
 └────────────────────┘                                     │
```

### The full cycle in plain English

1. **Visitor opens your page** → your server (Django/Node/PHP/etc.)
   knows which project's viewer to show.
2. **Your server mints a token**. It calls `POST /api/v1/embed-tokens`
   over HTTPS with your **API key**. The body says which kind (2D or 3D)
   and which resource (project or property), plus the list of origins
   allowed to embed it (your website domain). The API returns a `token`
   and a ready-to-use `embed_url`.
3. **Your server renders HTML** with `<iframe src="<embed_url>">`. The
   API key NEVER goes to the browser — only the token.
4. **The browser loads the iframe**. The first request is the chromeless
   HTML shell; the shell triggers the JS bundle and CSS, which boot up
   the viewer.
5. **The viewer fetches its data** from the public `/api/v1/...` JSON
   endpoints (no auth needed for read endpoints; same origin so no
   CORS).
6. **The visitor interacts** — clicks a polygon, drills into a building,
   selects a unit. The iframe posts events (`unitSelected`, `navigated`,
   …) to your parent page. Your page can also send commands back
   (change theme, jump to a specific property, …).
7. **The token expires** after `expires_in` seconds (default 1 hour).
   Past expiry the iframe goes 404. Mint a fresh one for the next page
   render — they're cheap.

### Why tokens (and not the API key) for the iframe?

The API key authenticates the *server* that's authorized to use the
service. If you put the key in the browser, anyone viewing source could
copy it and use it from any site for any resource until you revoke it.

A token authorizes **one resource**, **for a bounded time**, **from a
specific origin**. Stealing the URL out of the page source gives the
thief access to that one resource from that one origin, and only
until the timer runs out. Plus you get a full audit row per token in
the Odoo backend.

### 9.1 Mint an embed token

`POST /api/v1/embed-tokens` (Bearer auth required).

```bash
curl -X POST "https://erp.atmta.com/api/v1/embed-tokens" \
  -H "Authorization: Bearer 96ee21de8..." \
  -H "X-Odoo-Database: atmta_prod" \
  -H "Content-Type: application/json" \
  -d '{
    "kind": "plan-2d",
    "resource_model": "realestate.project",
    "resource_id": 28,
    "allowed_origins": "https://atmta-website.com,https://staging.atmta-website.com",
    "expires_in": 3600,
    "theme": { "primary": "#ff5a00", "mode": "light", "lang": "en" }
  }'
```

Request fields:

| Field | Type | Notes |
|---|---|---|
| `kind` | `plan-2d` \| `maquette-3d` | Required |
| `resource_model` | `realestate.project` \| `realestate.property` | Required. 3D embeds only support `realestate.project` |
| `resource_id` | int | Must be publicly visible AND have the relevant asset |
| `allowed_origins` | string | Comma-separated. The embed page returns 403 to any other origin. Use `*` only for dev tokens. |
| `expires_in` | int (seconds) | 60–2,592,000 (30 days). Default 3600. |
| `theme` | object | Optional. Keys: `primary` (hex), `mode` (`light`/`dark`), `lang` (`ar`/`en`), `hide` (array of `exit`/`breadcrumbs`/`sidebar`) |

Response (201):

```json
{
  "token": "FqdqoDEb_u7VYByB55-nEul69FeEBWizPXQkNDsu8B8",
  "embed_url": "https://erp.atmta.com/embed/v1/plan-2d/FqdqoDEb_u7VYByB55-nEul69FeEBWizPXQkNDsu8B8?lang=en&mode=light",
  "expires_at": "2026-06-22T15:37:57",
  "kind": "plan-2d",
  "resource_model": "realestate.project",
  "resource_id": 28
}
```

Validation rejections (400):

- `plan-2d` on a project that has neither `master_plan_2d` nor
  `main_property_id`.
- `plan-2d` on a property without `plan_image`.
- `maquette-3d` on a project without `maquette_glb`.
- `expires_in` outside 60–2,592,000.
- `allowed_origins` empty.

### 9.2 Render the iframe

```html
<iframe
  src="https://erp.atmta.com/embed/v1/plan-2d/FqdqoDEb_u..."
  style="border:0; width:100%; height:600px"
  allow="fullscreen"
  loading="lazy">
</iframe>
```

The embed page sets the following security headers:

```
Content-Security-Policy: frame-ancestors https://atmta-website.com https://staging.atmta-website.com
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: accelerometer=(), camera=(), microphone=(), geolocation=(), fullscreen=(self)
Cache-Control: private, max-age=60
```

`X-Frame-Options` is NOT set (it would override `frame-ancestors` and
only supports one origin).

### 9.3 postMessage protocol

The iframe and parent communicate via `window.postMessage`. Every
message is shaped `{ source, version, type, payload }`.

**Iframe → host** (`source: "re-embed"`):

| `type` | Payload |
|---|---|
| `ready` | `{ height }` — fired once on mount |
| `resize` | `{ height }` — every layout change |
| `navigated` | `{ level, id, name, breadcrumbs, region_count }` — fired when the user drills |
| `unitSelected` | Full unit object (id, name, price, area_sqm, bedrooms, …) when a leaf region or 3D mesh is clicked |
| `interestRequested` | `{ id, kind }` when the in-iframe "I'm interested" button is clicked |
| `pong` | `{}` in response to `ping` |
| `error` | `{ code, message }` |

**Host → iframe** (`source: "re-embed-host"`):

| `type` | Payload | Effect |
|---|---|---|
| `setTheme` | `{ primary, mode, background, foreground, hide }` | Live re-skin |
| `navigate` | `{ to: "back" \| "home" \| "property:<id>" }` | Drill from outside |
| `highlight` | `{ regionId }` | Flash a polygon |
| `ping` | `{}` | Liveness check |

### 9.4 Host-side example

```html
<iframe id="re-frame" src="..."></iframe>
<script>
const ERP_ORIGIN = "https://erp.atmta.com";
const frame = document.getElementById("re-frame");

window.addEventListener("message", (e) => {
  if (e.origin !== ERP_ORIGIN) return;
  const { type, payload } = e.data || {};
  switch (type) {
    case "ready":
    case "resize":
      frame.style.height = payload.height + "px";
      break;
    case "unitSelected":
      showContactForm(payload);     // your own modal
      break;
    case "interestRequested":
      submitLeadStub(payload.id);   // your own flow
      break;
    case "navigated":
      pushHistory(payload);          // sync URL with drill state if you want
      break;
  }
});

function sendToFrame(type, payload) {
  frame.contentWindow.postMessage(
    { source: "re-embed-host", version: "v1", type, payload },
    ERP_ORIGIN,
  );
}

// Example: change the accent color when the page's dark-mode toggle flips
document.querySelector("#dark-toggle").addEventListener("click", () => {
  sendToFrame("setTheme", { mode: "dark" });
});
```

### 9.5 Embed errors

| HTTP | Reason | Recovery |
|---|---|---|
| `404` | Token not found, expired, or wrong `kind` for the route | Mint a fresh token |
| `403` | Origin not in the token's `allowed_origins` | Pass the right origin at mint time |
| `200` but `error` event from postMessage | API JSON fetch failed inside the iframe | Inspect `event.payload.code` |

### 9.6 Worked end-to-end example (no framework)

Here is the simplest possible integration — a server-rendered HTML page
with a `<form>` that asks the visitor for a project ID and then embeds
the 2D viewer for it. Use it as a template for any backend stack.

**Server side (Python / Flask — equivalent in any language):**

```python
import os
import requests
from flask import Flask, render_template_string, request

app = Flask(__name__)

ERP_BASE     = "https://erp.atmta.com"
ERP_DB       = "atmta_prod"
ERP_API_KEY  = os.environ["ATMTA_API_KEY"]          # NEVER hard-code
SITE_ORIGIN  = "https://atmta-website.com"

def mint_embed_token(project_id: int) -> str:
    r = requests.post(
        f"{ERP_BASE}/api/v1/embed-tokens",
        headers={
            "Authorization":   f"Bearer {ERP_API_KEY}",
            "X-Odoo-Database": ERP_DB,
            "Content-Type":    "application/json",
        },
        json={
            "kind":            "plan-2d",
            "resource_model":  "realestate.project",
            "resource_id":     project_id,
            "allowed_origins": SITE_ORIGIN,
            "expires_in":      3600,
            "theme": {"primary": "#ff5a00", "mode": "light", "lang": "en"},
        },
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["embed_url"]                    # ready-to-use URL

PAGE = """
<!doctype html>
<html><head><title>Project Viewer</title></head>
<body>
  <h1>Project {{ pid }}</h1>
  <iframe
      id="re-frame"
      src="{{ embed_url }}"
      style="border:0; width:100%; height:600px"
      allow="fullscreen"
      loading="lazy"></iframe>
  <div id="status">Loading…</div>
  <script>
    const ERP_ORIGIN = "{{ erp_origin }}";
    const frame = document.getElementById("re-frame");
    window.addEventListener("message", (e) => {
        if (e.origin !== ERP_ORIGIN) return;
        const m = e.data || {};
        if (m.source !== "re-embed") return;
        if (m.type === "ready" || m.type === "resize") {
            frame.style.height = m.payload.height + "px";
            document.getElementById("status").textContent = "OK";
        }
        if (m.type === "unitSelected") {
            alert("Visitor picked unit " + m.payload.name);
        }
    });
  </script>
</body></html>
"""

@app.route("/projects/<int:pid>")
def project_page(pid):
    return render_template_string(
        PAGE,
        pid=pid,
        embed_url=mint_embed_token(pid),
        erp_origin=ERP_BASE,
    )

if __name__ == "__main__":
    app.run(port=5000)
```

That is the whole integration — about 60 lines of code.

When a visitor opens `https://your-site.com/projects/28`:

| Where | Request | Response |
|---|---|---|
| Server | `POST {ERP_BASE}/api/v1/embed-tokens` (Bearer auth) | `201 {token, embed_url}` |
| Server | Renders HTML containing `<iframe src=embed_url>` | `200 text/html` |
| Browser | `GET {ERP_BASE}/embed/v1/plan-2d/<token>?db=...` | `200` chromeless HTML |
| Iframe | `GET {ERP_BASE}/web/assets/<hash>/embed_plan_2d.min.{js,css}?db=...` | `200` |
| Iframe JS | `GET {ERP_BASE}/api/v1/projects/28/plan-2d?db=...` | `200` JSON (regions, image URL) |
| Iframe `<img>` | `GET {ERP_BASE}/api/v1/image/realestate.project/28/master_plan_2d?...&db=...` | `200` PNG |
| Iframe → host | `postMessage { type: "ready", payload: { height } }` | host resizes the iframe |
| Visitor clicks polygon | `GET {ERP_BASE}/api/v1/properties/<id>/plan-2d?db=...` | `200` JSON for the drill |
| Visitor reaches a leaf unit | `postMessage { type: "unitSelected", payload: { id, name, price, … } }` | host shows lead form |

### 9.7 Common pitfalls

| Symptom | Cause | Fix |
|---|---|---|
| `401 unauthorized` on mint | API key missing/wrong scope | Mint a new key with the `real_estate_api` scope from My Profile → API Keys |
| `400 validationerror "Project has no 2D entry"` | Trying to mint 2D on a project that has neither `master_plan_2d` nor any top-level property with `plan_image` | Configure one in the backend before minting |
| `403 forbidden` on the embed URL | Visitor's browser sent an `Origin` not in `allowed_origins` | Pass the exact production origin (scheme + host + port, no trailing slash) at mint time |
| Iframe stays blank, console shows CORS error on `embed-tokens` | Cross-origin POST from your `<script>` blocked | The mint should be **server-to-server**, not browser-side. The browser only sees the `embed_url`. |
| Iframe stays blank, no error | DB not routed (multi-DB host) | The `embed_url` returned by mint already contains `?db=...` — use it verbatim, do not strip the query string |
| Iframe loads but no images | Same as above for image URLs | Use the URL fields as returned by the API verbatim — they include the `?db=` suffix and a cache-busting `unique=` token |
| Token works for 5 minutes then 404s | Expired | Mint a fresh token per page render (cheap — they're audit rows, not heavy compute) |

---

## 10. Customer Portal

Once a partner has been synced via [`POST /api/v1/partners`](#8-customer-contact-sync)
and you have their `external_ref`, you can read that customer's activity
across the whole real-estate stack. Five endpoints, all rooted at
`/api/v1/partners/by-ref/<external_ref>/…`:

| Endpoint | Returns |
|---|---|
| `GET .../interests` | The `crm.lead` rows they submitted via `/api/v1/interests` |
| `GET .../viewings` | Property viewings scheduled for them (brokerage module) |
| `GET .../contracts` | Sale + rental contracts they signed (unified) |
| `GET .../installments` | Sale-contract installment schedule (developer module) |
| `GET .../payments` | Rental-contract payment schedule (rental module) |

**Auth.** All five require a Bearer API key with `real_estate_api` scope —
this is customer PII + financial data, not public catalog.

**Strict lookup.** Unknown `external_ref` → **404**. Malformed ref → **400**.
No empty-array fallback (per the `no_silent_fallbacks` rule).

**Common response envelope**

```json
{
  "external_ref": "site:user:42",
  "partner_id": 812,
  "results":     [ ... ],
  "total_count": 17,
  "limit":       50,
  "offset":      0
}
```

**Common query params (endpoint-dependent)**

| Param | Where | Notes |
|---|---|---|
| `limit` | all | 1–500 (default varies per endpoint) |
| `offset` | all | ≥0 |
| `state` | viewings, installments, payments | Filter by workflow state; unknown state → 400 |
| `kind` | contracts | `sale` \| `rental` (omit for both) |
| `date_from`, `date_to` | interests, installments, payments | `YYYY-MM-DD`, closed range |

### 10.1 Interests (`GET .../interests`)

Interests the 3rd-party site captured on this customer's behalf. Only
interests that were submitted with this partner's `external_ref` (or that
were later linked in the CRM) appear here — interests without a
`partner_id` are anonymous leads and won't leak in.

```bash
curl -H "Authorization: Bearer $API_KEY" \
     "$BASE/api/v1/partners/by-ref/site:user:42/interests?db=$DB&limit=20"
```

Response:

```json
{
  "external_ref": "site:user:42",
  "partner_id":   812,
  "results": [
    {
      "id":           1024,
      "name":         "Interest — Villa 14",
      "contact_name": "Ahmed Youssef",
      "email":        "ahmed@example.com",
      "phone":        "+201001234567",
      "description":  "Called about payment plan options.",
      "stage":        "New",
      "type":         "lead",
      "created_at":   "2026-06-14T09:12:03",
      "project_id":   23,
      "project_name": "NEW CAPITAL COMPOUND",
      "unit_id":      118,
      "unit_name":    "Villa 14"
    }
  ],
  "total_count": 3, "limit": 20, "offset": 0
}
```

### 10.2 Viewings (`GET .../viewings`)

Property viewings booked for this customer. Requires the
`real_estate_brokerage` module — if uninstalled, this endpoint returns
**404** (not an empty list).

```bash
curl -H "Authorization: Bearer $API_KEY" \
     "$BASE/api/v1/partners/by-ref/site:user:42/viewings?db=$DB&state=scheduled"
```

Fields: `reference`, `property_id`, `property_name`, `scheduled_at`,
`end_at`, `duration_hours`, `state`, `feedback_rating`, `feedback`,
`next_action`, `agent_name`.

`state` values: `scheduled` | `completed` | `cancelled` | `no_show`.

### 10.3 Contracts (`GET .../contracts`)

Sale + rental contracts, unified into one array with a `kind` field so
the caller can render them together on an "Agreements" page.

```bash
curl -H "Authorization: Bearer $API_KEY" \
     "$BASE/api/v1/partners/by-ref/site:user:42/contracts?db=$DB"
```

Sale contract entry:

```json
{
  "kind":                    "sale",
  "id":                      45,
  "reference":               "SC-00045",
  "state":                   "signed",
  "contract_date":           "2026-03-01",
  "signing_date":            "2026-03-14",
  "expected_handover_date":  "2027-09-01",
  "handover_date":           null,
  "property_id":             118,
  "property_name":           "Villa 14 — NEW CAPITAL COMPOUND",
  "project_id":              23,
  "project_name":            "NEW CAPITAL COMPOUND",
  "sale_price":              5250000.0,
  "currency":                "EGP",
  "paid_amount":             1050000.0,
  "balance_due":             4200000.0,
  "progress_pct":            20.0
}
```

Rental contract entry:

```json
{
  "kind":              "rental",
  "id":                77,
  "reference":         "RC-00077",
  "state":             "active",
  "start_date":        "2026-01-01",
  "end_date":          "2026-12-31",
  "notes":             "Terms and conditions...",
  "currency":          "EGP",
  "contract_no":       "CTR-2026-77",
  "main_contract_no":  ""
}
```

Add `?kind=sale` or `?kind=rental` to narrow to one flavor.

### 10.4 Installments (`GET .../installments`)

The sale-contract installment schedule. Requires `real_estate_developer`;
uninstalled → **404**.

```bash
curl -H "Authorization: Bearer $API_KEY" \
     "$BASE/api/v1/partners/by-ref/site:user:42/installments?db=$DB&state=pending"
```

Fields: `contract_id`, `contract_ref`, `property_id`, `property_name`,
`sequence`, `kind` (`down`|`installment`|`balloon`), `amount`, `currency`,
`date_due`, `state` (`pending`|`invoiced`|`paid`|`cancelled`),
`invoice_state`, `payment_state`.

### 10.5 Payments (`GET .../payments`)

The rental-contract payment schedule (base rent + any additional charges).
Requires `atmta_real_estate`; uninstalled → **404**.

```bash
curl -H "Authorization: Bearer $API_KEY" \
     "$BASE/api/v1/partners/by-ref/site:user:42/payments?db=$DB&date_from=2026-01-01&date_to=2026-12-31"
```

Fields: `contract_id`, `contract_ref`, `property_id`, `property_name`,
`label`, `amount`, `amount_total` (base + charges), `increase_amount`,
`discount_amount`, `date_due`, `hijri_date_due`, `state`
(`draft`|`invoiced`|`paid`|`cancelled`), `invoice_state`, `payment_state`.

`amount_total` is the number to display — it already includes any utility
lines / charges rolled up to that due date.

### 10.6 Worked example — "My Account" page

```javascript
async function loadCustomerDashboard(externalRef) {
  const base = `${BASE}/api/v1/partners/by-ref/${externalRef}`;
  const opts = { headers: { Authorization: `Bearer ${API_KEY}` } };
  const q    = `?db=${DB}`;

  const [interests, viewings, contracts, installments, payments] =
    await Promise.all([
      fetch(`${base}/interests${q}&limit=5`,     opts).then(r => r.json()),
      fetch(`${base}/viewings${q}&state=scheduled`, opts).then(r => r.json()),
      fetch(`${base}/contracts${q}`,             opts).then(r => r.json()),
      fetch(`${base}/installments${q}&state=pending`, opts).then(r => r.json()),
      fetch(`${base}/payments${q}&state=draft`,  opts).then(r => r.json()),
    ]);

  renderInterests(interests.results);
  renderUpcomingViewings(viewings.results);
  renderContracts(contracts.results);
  renderOutstanding([...installments.results, ...payments.results]);
}
```

### 10.7 Common pitfalls

| Symptom | Cause | Fix |
|---|---|---|
| `404 not_found` on every portal endpoint | `external_ref` isn't in Odoo yet | Sync via `POST /api/v1/partners` first, then use the returned `external_ref` |
| Interests list is empty though the site submitted 5 | Interests weren't linked (submitted without `partner_external_ref`) | Include `partner_external_ref` on every `POST /api/v1/interests` |
| `404` on `/viewings` even for a synced partner | Brokerage module not installed on this DB | Install `real_estate_brokerage` or don't call this endpoint |
| Contract shows `paid_amount: 0` but customer paid | Invoice isn't posted yet | The `paid_amount` reflects **posted invoice** state; posting is done by accounting |

---

## 11. Downloads (PDF / ZIP)

Binary document endpoints for contracts, invoices, payment receipts,
and bundled downloads. All return **`application/pdf`** (or
**`application/zip`** for bundles) with
`Content-Disposition: attachment; filename="…"` and
`Cache-Control: private, no-store` (financial + PII data must not sit
in intermediaries or the browser cache).

Errors use the same JSON envelope as every other endpoint —
`{"error": {"code": "...", "message": "..."}}` — with the appropriate
HTTP status.

Two scopes:

* **By-ref** — per-customer routes rooted at
  `/api/v1/partners/by-ref/<external_ref>/…`. Any valid API key with
  scope `real_estate_api` may call these; the endpoint resolves the
  partner from the ref, then refuses to serve any record whose
  `partner_id` doesn't match (never 403 — always 404, so callers can't
  distinguish "wrong partner" from "record doesn't exist").
* **Top-level** — record-id addressed routes at
  `/api/v1/{contracts,installments,payments}/<id>/…`. Restricted to
  users in **`real_estate_api.group_realestate_api_downloads`**
  (implied by Manager). A compromised customer-integration key
  therefore can't walk the full contract / invoice table.

### 11.1 By-ref (per-customer)

**Contracts + reports**

| Endpoint | Returns |
|---|---|
| `GET .../contracts/<id>/download?kind=sale\|rental&template=custom` | Contract PDF |
| `GET .../contracts/<id>/invoice.pdf?kind=sale\|rental&template=std\|custom` | **Primary** invoice on the contract (`contract.invoice_id`) — the single billing move some flows generate instead of / in addition to installments |
| `GET .../contracts/<id>/payment-schedule.pdf` | Whole-rental-contract payment schedule (uses `atmta_real_estate.action_report_payment_schedule`; **rental only**) |
| `GET .../contracts/<id>/financial-summary.pdf` | Rental contract financial summary (uses `atmta_real_estate.action_report_financial_summary`) |
| `GET .../contracts/<id>/full-summary.pdf` | Rental full contract summary (uses `atmta_real_estate.action_report_contract_full`) |

**Per-installment / per-payment**

| Endpoint | Returns |
|---|---|
| `GET .../installments/<id>/invoice?template=std\|custom` | Sale-installment invoice PDF |
| `GET .../payments/<id>/invoice?template=std\|custom` | Rental-payment invoice PDF |
| `GET .../payments/<id>/receipt` | Payment receipt PDF (once the invoice is paid) |

**Attachments** (signed contracts, IDs, permits, brochures — anything uploaded to the record)

| Endpoint | Returns |
|---|---|
| `GET .../contracts/<id>/attachments?kind=sale\|rental` | JSON list `{results: [{id, name, mimetype, size, create_date}]}` |
| `GET .../contracts/<id>/attachments/<aid>?kind=sale\|rental` | The raw file (whatever `mimetype`) |
| `GET .../properties/<id>/attachments` | JSON list of attachments on the property (title deed, permits, …). Only visible to a partner who has a contract on that property. |
| `GET .../properties/<id>/attachments/<aid>` | The raw file |

**Brokerage** (only if `real_estate_brokerage` is installed)

| Endpoint | Returns |
|---|---|
| `GET .../brokerage/<txid>/invoice.pdf?template=std\|custom` | Commission invoice for a brokerage transaction where the partner is either `seller_id` or `buyer_id` |

**Bundles**

| Endpoint | Returns |
|---|---|
| `GET .../contracts/<id>/documents.zip?kind=sale\|rental` | Contract PDF + primary invoice + every posted per-installment/per-payment invoice + every attachment, zipped |
| `GET .../statement.pdf` | Per-partner customer statement PDF |

Example — download the PDF of sale contract #45 for customer
`site:user:42`:

```bash
curl -H "Authorization: Bearer $API_KEY" \
     -H "X-Odoo-Database: $DB" \
     -o contract-45.pdf \
     "$BASE/api/v1/partners/by-ref/site:user:42/contracts/45/download?kind=sale"
```

Response headers:

```
HTTP/1.1 200 OK
Content-Type: application/pdf
Content-Length: 37342
Content-Disposition: attachment; filename="Contract-SC-00045.pdf"
Cache-Control: private, no-store
```

If the contract belongs to a different partner, or doesn't exist,
or `kind` is wrong: **404 `not_found`**. If the caller omits the key:
**401 `unauthorized`**. If they exceed 300 req/min: **429 `rate_limited`**.

### 11.2 Top-level (manager)

Same document set, addressed by raw record id. All require an API key
whose user is in `group_realestate_api_downloads` (implied by Manager):

| Endpoint | Returns |
|---|---|
| `GET /api/v1/contracts/<id>/download?kind=sale\|rental&template=…` | Contract PDF |
| `GET /api/v1/contracts/<id>/invoice.pdf?kind=sale\|rental&template=…` | Primary contract invoice |
| `GET /api/v1/contracts/<id>/payment-schedule.pdf` | Rental payment schedule |
| `GET /api/v1/contracts/<id>/financial-summary.pdf` | Rental financial summary |
| `GET /api/v1/contracts/<id>/full-summary.pdf` | Rental full summary |
| `GET /api/v1/installments/<id>/invoice?template=…` | Sale-installment invoice PDF |
| `GET /api/v1/payments/<id>/invoice?template=…` | Rental-payment invoice PDF |
| `GET /api/v1/payments/<id>/receipt` | Payment receipt PDF |
| `GET /api/v1/contracts/<id>/attachments?kind=sale\|rental` | Attachment list JSON |
| `GET /api/v1/contracts/<id>/attachments/<aid>?kind=sale\|rental` | Attachment file |
| `GET /api/v1/properties/<id>/attachments` | Attachment list JSON |
| `GET /api/v1/properties/<id>/attachments/<aid>` | Attachment file |
| `GET /api/v1/brokerage/<txid>/invoice.pdf?template=…` | Commission invoice |
| `GET /api/v1/contracts/<id>/documents.zip?kind=sale\|rental` | Contract bundle ZIP |

A key without the downloads group gets **403 `forbidden`** — even if
the key is valid.

### 11.3 Zip bundle per contract

The `.../contracts/<id>/documents.zip` variant returns a single zip
containing (best-effort — individual failures are logged and skipped
rather than aborting the whole archive):

1. The contract PDF (rendered from the custom template — see §11.5).
2. The contract's **primary invoice** (`contract.invoice_id`), if any
   AND `state = 'posted'`.
3. Every per-installment or per-payment child invoice PDF where the
   move is posted.
4. Every downloadable attachment on the contract (signed scan,
   customer IDs, permits — anything uploaded to `attachment_ids` or
   the record's chatter).

Zip entry names:

```
Contract-SC-00045.pdf
Invoice-Primary-INV_2026_00013.pdf
Invoice-Installment-INV_2026_00008.pdf
Invoice-Installment-INV_2026_00019.pdf
attachment-42-signed-contract.pdf
attachment-43-buyer-id.jpg
...
```

Content type is `application/zip` and filename defaults to
`Contract-<name>-documents.zip`.

### 11.4 Statement per partner

`.../statement.pdf` returns one PDF summarising the customer's account:

* All sale contracts (reference, property, date, state, price, balance due)
* All rental contracts (reference, dates, state)
* Every installment with due date + invoice/payment state
* Every rental payment schedule row with the same

Rendered from `real_estate_api.action_report_partner_statement` — a
plain `web.external_layout` template scoped to `res.partner`. The
target `res.partner` is the one resolved from `external_ref`.

### 11.5 `?template=` semantics

Contract endpoints accept `?template=custom` (default). `std` is
rejected with **400** — Odoo ships no standard "real estate contract"
report to fall back to. Custom → the real-estate-specific QWeb
template:

* Sale contract → `real_estate_api.action_report_sale_contract` (this
  module — parties, unit, price, payment plan; overridable via view
  inheritance)
* Rental contract → `atmta_real_estate.action_realestate_contract_report`
  (already shipped with `atmta_real_estate`)

Invoice endpoints accept `?template=std|custom`. Both currently
resolve to Odoo's standard **`account.account_invoices`** — no
real-estate-specific per-invoice template exists yet. The parameter
is accepted for forward compatibility so callers who bake it into
URLs don't break when one is added later.

The payment-receipt endpoint has no template picker — it renders
Odoo's `account.action_report_payment_receipt` against the
`account.payment` records reconciled to the invoice (falling back to
the invoice PDF if no `account.payment` exists — e.g. cash / manual
reconcile flows).

### 11.6 Error codes specific to downloads

| HTTP | `code` | When |
|---|---|---|
| `400` | `bad_request` | Missing/invalid `kind`; `template=std` on a contract endpoint |
| `401` | `unauthorized` | No key on any download endpoint |
| `403` | `forbidden` | Top-level endpoint, key user not in the downloads group |
| `404` | `not_found` | Contract/installment/payment id doesn't exist OR belongs to a different partner (by-ref) |
| `404` | `not_invoiced` | Installment/payment exists but its `move_state != 'posted'` — no PDF to render yet |
| `404` | `not_paid` | Payment receipt requested but the invoice is not yet paid |
| `404` | `report_missing` | Report template's module not installed (e.g. `atmta_real_estate.action_report_contract` when only the API module is installed) |
| `429` | `rate_limited` | Same 300/min throttle as every other authed endpoint |
| `500` | `render_failed` | QWeb template compiled but PDF renderer threw. Details in `ir.logging`. |

### 11.7 Worked example — "download all my paperwork" button

```javascript
async function downloadCustomerBundle(externalRef) {
  const url = `${BASE}/api/v1/partners/by-ref/${externalRef}/statement.pdf`;
  const res = await fetch(url, {
    headers: { Authorization: `Bearer ${API_KEY}`,
               'X-Odoo-Database': DB },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const blob = await res.blob();
  const a = Object.assign(document.createElement('a'), {
    href: URL.createObjectURL(blob),
    download: `statement.pdf`,
  });
  a.click(); URL.revokeObjectURL(a.href);
}
```

For a per-contract zip:

```javascript
const url = `${BASE}/api/v1/partners/by-ref/${externalRef}/contracts/${contractId}/documents.zip?kind=sale`;
// ...same fetch/blob dance, save as `contract-${contractId}-docs.zip`
```

---

## 12. Error Reference

All API errors share the same envelope:

```json
{ "error": { "code": "<machine_code>", "message": "<human readable>", "details": ... } }
```

| HTTP | `code` | Typical cause |
|---|---|---|
| `400` | `bad_request` | Malformed JSON, missing required field, invalid email/phone, out-of-range int |
| `400` | `validationerror` | The Odoo model constraint refused the data (e.g. `expires_in` outside range, project has no plan to mint a 2D embed for) |
| `401` | `unauthorized` | Missing or invalid API key on a key-required route |
| `403` | `accesserror` | Origin not in `allowed_origins` for an embed |
| `403` | `forbidden` | `X-Odoo-Database` conflicts with session cookie |
| `404` | `not_found` | Record missing, hidden, or in a non-public state |
| `405` | `method_not_allowed` | Route only accepts a different HTTP method |
| `429` | `rate_limited` | Throttle hit (see [Rate Limits](#13-rate-limits)) |
| `500` | `internal_error` | Bug — the response body never exposes a traceback. The full stack is in `ir.logging`. |

**Never** does the API return a "best-guess" record or an empty list as
a fallback for a 404. Strict.

---

## 13. Rate Limits

Fixed-window counters in Postgres (sufficient for typical real-estate
website traffic). Limits are configurable via `ir.config_parameter`:

| Parameter | Default | What it limits |
|---|---|---|
| `real_estate_api.rate_limit.public.per_minute` | `60` | Anonymous requests, per IP |
| `real_estate_api.rate_limit.authed.per_minute` | `300` | Authenticated requests, per API key |
| `real_estate_api.rate_limit.interest.per_hour` | `5` | `POST /api/v1/interests`, per IP — applied **in addition** to the per-minute throttle |

A throttled request returns `429` with body
`{"error": {"code": "rate_limited", "message": "Too many requests."}}`.
Counter rows older than 24 hours are pruned hourly by an `ir.cron`.

---

## 14. What we never expose

| Model | Fields hidden from the API |
|---|---|
| `res.partner` (developer) | `vat`, `bank_ids`, `category_id`, `commercial_partner_id`, anything starting with `_` |
| `realestate.project` | `expected_budget`, `expected_revenue`, `notes` (Html), `manager_id`, plus the project entirely if `state in ('cancelled', 'planning')` is mapped to `null` (then dropped from the listing) |
| `realestate.property` | `owner_id`, `is_internal`, `manager_id`, `property_notes`, utility meter readings, `reservation_ids`, `sale_contract_ids`, `cost_*` |
| `realestate.embed.token` | The raw `token` value is only returned by the **mint** call. The `/api/v1/embed-tokens` endpoint has no list/get/delete variants — admins use the backend UI. |
| `crm.lead` | The website only sees its own submission echo (`id`, `reference`, `status`). Probability, expected revenue, internal notes, salesperson assignments, and activity logs are never returned. |

---

## 15. Postman Collection

A ready-to-import collection is shipped with the module:

```
real_estate_api/postman/RealEstate_API.postman_collection.json
```

23 requests across 6 folders (Catalog, 2D/3D Maps, Interests, Embed
tokens, Embed routes, CORS / DB Routing) with built-in tests and a
collection-level pre-request script that injects `X-Odoo-Database` on
every call.

Required collection variables:

| Variable | Example |
|---|---|
| `base_url` | `https://erp.atmta.com` |
| `db_name` | `atmta_prod` |
| `api_key` | the 40-char hex secret from key minting |
| `website_origin` | `https://atmta-website.com` |

The other variables (`first_project_id`, `embed_token_2d`, …) are
filled in automatically by request-level test scripts as you run the
collection top-to-bottom.

### How `{{embed_token_2d}}` gets populated

The "Mint Embed Token (2D Plan)" request has this snippet in its
**Tests** tab:

```js
const data = pm.response.json();
pm.collectionVariables.set("embed_token_2d", data.token);
pm.collectionVariables.set("embed_url_2d",   data.embed_url);
```

So after you fire the mint, both `{{embed_token_2d}}` (the raw token
string) and `{{embed_url_2d}}` (the ready-to-use full URL with
`?db=...` already baked in) are available to every following request.

The "Render 2D Embed" request then uses:

```
GET {{base_url}}/embed/v1/plan-2d/{{embed_token_2d}}?db={{db_name}}
```

— or, equivalently:

```
GET {{embed_url_2d}}
```

If you copy a single request out of the collection (rather than running
the whole thing top-to-bottom), remember to fire the mint first or
`{{embed_token_2d}}` will be empty and the embed GET returns 404.

---

## Appendix · Image URL shape

Every image URL the API returns follows the same template — the
`real_estate_api` image proxy, never Odoo's built-in `/web/image/...`:

```
/api/v1/image/<MODEL>/<ID>/<FIELD>?unique=<YYYYMMDDHHMMSS>[&w=W&h=H]
```

- `<MODEL>` / `<FIELD>` are constrained to a whitelist
  (`master_plan_2d`, `plan_image`, `image_1920`, `maquette_glb`, …).
  Anything else returns **404** even if the field exists on the model.
- `w=W&h=H` triggers Odoo's on-the-fly resize. Common sizes the API
  emits: `400x300`, `800x600`, `1280x720`, `1920x1080`. Omit them for
  the original.
- `unique=` is a cache-busting token derived from the source record's
  `write_date`. It changes when the underlying file is replaced — and
  not before.

You may safely fetch these URLs from a browser cross-origin, provided
the origin is in `real_estate_api.cors.allowed_origins`. The proxy
adds the right `Access-Control-Allow-Origin` for whitelisted origins;
unlisted origins get the same 2xx/4xx response without CORS headers
(so the browser blocks them — but the server does not signal failure).

---

*Last reviewed: against `real_estate_api` v0.1 · DB
`realestate_all_modules` · Odoo 18.0.*
