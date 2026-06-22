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
5. [2D Plan Drill API](#5-2d-plan-drill-api)
6. [3D Maquette API](#6-3d-maquette-api)
7. [Interests (lead capture)](#7-interests-lead-capture)
8. [Embed Tokens & Iframe Flow](#8-embed-tokens--iframe-flow)
9. [Error Reference](#9-error-reference)
10. [Rate Limits](#10-rate-limits)
11. [What we never expose](#11-what-we-never-expose)
12. [Postman Collection](#12-postman-collection)

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
      "cover_image_url": "/web/image/realestate.project/28/master_plan_2d/1280x720?unique=20260531114240",
      "has_2d_plan": true,
      "has_3d_maquette": true,
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
| `429` | `rate_limited` | Too many requests; see [Rate Limits](#10-rate-limits) |

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

- Image URLs are absolute paths under `/web/image/MODEL/ID/FIELD/...`.
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
      "logo_url": "/web/image/res.partner/34/image_256?unique=20260520112514",
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
  "cover_image_url": "/web/image/realestate.project/28/master_plan_2d/1280x720?unique=20260531114240",
  "has_2d_plan": true,
  "has_3d_maquette": true,
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
      "area_sqm": 0.0,
      "floors_count": 10,
      "units_count": 32,
      "thumb_url": "/web/image/realestate.property/84/plan_image/400x300?unique=...",
      "currency": "USD",
      "currency_symbol": "$"
    }
  ],
  "limit": 20,
  "offset": 0
}
```

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
  "cover_image_url": "/web/image/realestate.property/105/image_1920/800x600?unique=...",
  "has_2d_plan": true,
  "has_3d_interior": true,
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
      "url": "/web/image/property.image/12/image_1024?unique=...",
      "thumb_url": "/web/image/property.image/12/image_256?unique=...",
      "video_url": ""
    }
  ],
  "plan_image_url": "/web/image/realestate.property/105/plan_image/1920x1080?unique=...",
  "floor_plan_image_url": "/web/image/realestate.property/105/floor_plan_image/1920x1080?unique=...",
  "spec_tags": ["marble floors", "smart-home wiring"]
}
```

#### `GET /api/v1/properties/<id>`

Generic property detail — works for any node in the hierarchy
(compounds, buildings, floors, units, rooms) as long as the record is
publicly visible (`state in available/reserved/sold`).

---

## 5. 2D Plan Drill API

The drill API returns a JSON tree describing one level of the plan
(image URL + clickable polygon regions). Drilling deeper is done by
the client: when a region's `target_has_plan` is `true`, the client
fetches `/api/v1/properties/<target_id>/plan-2d` to load the next
level. When `target_has_plan` is `false`, it's a leaf — the client
should treat the click as a unit selection.

### `GET /api/v1/projects/<id>/plan-2d`

Returns the project's **master plan**. If the project has no
`master_plan_2d` image but has a configured `main_property_id`, the
endpoint transparently returns that property's plan tree instead — the
project simply pre-points at the entry property.

```json
{
  "root_kind": "project",
  "root_id": 28,
  "name": "Demo Compound",
  "image_url": "/web/image/realestate.project/28/master_plan_2d/1920x1080?unique=...",
  "regions": [
    {
      "id": 6,
      "target_id": 69,
      "target_name": "Tower A",
      "target_kind": "building",
      "target_has_plan": true,
      "target_status": "available",
      "label": "Tower A",
      "color": "#3b82f6",
      "polygon": "[[17.18, 28.64], [40.38, 27.84], [40.21, 42.67], [17.35, 42.56]]"
    }
  ],
  "breadcrumbs": [
    {"id": 28, "kind": "project", "name": "Demo Compound"}
  ]
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
  "image_url": "/web/image/realestate.property/100/plan_image/1920x1080?unique=...",
  "regions": [
    {
      "id": 20,
      "target_id": 101,
      "target_name": "Santiago Tower",
      "target_kind": "building",
      "target_has_plan": true,
      "target_status": "available",
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
      "thumb_url": "/web/image/realestate.property/101/plan_image/240x150?unique=..."
    }
  ],
  "breadcrumbs": [
    {"id": 100, "kind": "property", "name": "Madrid Compound", "hierarchy_level": "compound"}
  ]
}
```

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

Errors: `404 not_found` if the project has no `master_plan_2d` and no
`main_property_id`; or if a property has no `plan_image`.

---

## 6. 3D Maquette API

### `GET /api/v1/projects/<id>/maquette-3d`

Returns the descriptor needed to load and interact with the project's
3D model:

```json
{
  "project_id": 28,
  "name": "Demo Compound",
  "glb_url": "/web/content/realestate.project/28/maquette_glb?download=false&unique=...",
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

- **`glb_url`** serves a glTF binary (~MB). The browser caches it by
  URL; the `unique=` parameter rolls when the file is replaced.
- **`env_hdr_url`** is optional; when present it's an environment map
  (`.hdr` / `.exr`) for IBL reflections.
- **`default_camera`** is a JSON string the viewer can `JSON.parse` —
  `{position: [x,y,z], target: [x,y,z]}`.
- **`units[].mesh_name`** is the exact mesh name inside the GLB.
  Click handling = raycast the scene, then look up `mesh.name` in this
  list. Meshes that don't map to any unit do nothing (no silent
  fallback).

Errors: `404 not_found` if `maquette_glb` is not uploaded on the
project.

### Unit detail from a 3D click

After a click resolves to a `unit_id`, fetch
`/api/v1/units/<id>` to render a side card with price, area, gallery,
floor plan, etc.

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
  "message": "Interested in a 3BR unit."
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

## 8. Embed Tokens & Iframe Flow

The 2D drill viewer and the 3D maquette are exposed as **chromeless
HTML pages** that 3rd-party websites embed via `<iframe>`. Access is
controlled by a per-resource, time-limited, origin-pinned token.

### High-level flow

```
 ┌────────────────┐   1. mint token (server-to-server)   ┌─────────────────┐
 │ Website server │ ───────────────────────────────────▶ │   Real Estate   │
 │                │ ◀──────────  201 + embed_url ─────── │       API       │
 └───────┬────────┘                                       └─────────────────┘
         │ 2. render <iframe src=embed_url>
         ▼
 ┌────────────────┐   3. fetch JSON via /api/v1/...     ┌─────────────────┐
 │  Browser /     │ ──────────────────────────────────▶ │   Real Estate   │
 │  iframe        │ ◀────────  JSON ─────────────────── │       API       │
 │                │ ◀── postMessage events ──────────── │                 │
 └────────────────┘                                      └─────────────────┘
```

### 8.1 Mint an embed token

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

### 8.2 Render the iframe

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

### 8.3 postMessage protocol

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

### 8.4 Host-side example

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

### 8.5 Embed errors

| HTTP | Reason | Recovery |
|---|---|---|
| `404` | Token not found, expired, or wrong `kind` for the route | Mint a fresh token |
| `403` | Origin not in the token's `allowed_origins` | Pass the right origin at mint time |
| `200` but `error` event from postMessage | API JSON fetch failed inside the iframe | Inspect `event.payload.code` |

---

## 9. Error Reference

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
| `429` | `rate_limited` | Throttle hit (see [Rate Limits](#10-rate-limits)) |
| `500` | `internal_error` | Bug — the response body never exposes a traceback. The full stack is in `ir.logging`. |

**Never** does the API return a "best-guess" record or an empty list as
a fallback for a 404. Strict.

---

## 10. Rate Limits

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

## 11. What we never expose

| Model | Fields hidden from the API |
|---|---|
| `res.partner` (developer) | `vat`, `bank_ids`, `category_id`, `commercial_partner_id`, anything starting with `_` |
| `realestate.project` | `expected_budget`, `expected_revenue`, `notes` (Html), `manager_id`, plus the project entirely if `state in ('cancelled', 'planning')` is mapped to `null` (then dropped from the listing) |
| `realestate.property` | `owner_id`, `is_internal`, `manager_id`, `property_notes`, utility meter readings, `reservation_ids`, `sale_contract_ids`, `cost_*` |
| `realestate.embed.token` | The raw `token` value is only returned by the **mint** call. The `/api/v1/embed-tokens` endpoint has no list/get/delete variants — admins use the backend UI. |
| `crm.lead` | The website only sees its own submission echo (`id`, `reference`, `status`). Probability, expected revenue, internal notes, salesperson assignments, and activity logs are never returned. |

---

## 12. Postman Collection

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

---

## Appendix · Image URL shape

Every image URL the API returns follows the same template:

```
/web/image/<MODEL>/<ID>/<FIELD>[/<WIDTH>x<HEIGHT>]?unique=<YYYYMMDDHHMMSS>
```

- `<WIDTH>x<HEIGHT>` triggers Odoo's on-the-fly resize. Common sizes
  the API emits: `400x300`, `800x600`, `1280x720`, `1920x1080`.
- `unique=` is a cache-busting token derived from the record's
  `write_date`. It changes when the image is replaced — and not before.

You may safely fetch these URLs from a browser cross-origin: Odoo's
image handler responds with `Access-Control-Allow-Origin: *`.

---

*Last reviewed: against `real_estate_api` v0.1 · DB
`realestate_all_modules` · Odoo 18.0.*
