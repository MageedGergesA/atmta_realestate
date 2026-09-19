# Claude Code prompt — ATMTA Real Estate Suite showcase PDF

Copy everything inside the fence below and paste it into a fresh Claude Code session.

---

```markdown
Create a professional PDF showcasing the ATMTA Real Estate Suite running on our Odoo server.
Screenshot-led, headlines only — no paragraphs, no technical explanations.

## Access
- URL: http://egyptairodoodev.isdeg.com:8069/odoo
- Database: real_estate_full
- I already have this open and logged in in my Chrome. Use the claude-in-chrome MCP tools
  against my existing session. Do NOT install Playwright or any browser — reuse my session.
- If a page shows the database selector, append ?db=real_estate_full

## Hard rules
- READ-ONLY. Never create/edit/delete records, never confirm, sign, post, save cameras,
  save mesh mappings, or submit any form. Navigate and screenshot only.
- Do not spend time re-exploring the whole app. The exact targets are listed below.
- Skip anything with no data (verified empty): Construction milestones/contractors,
  Investment studies, Procurement material requests, snagging, warranties,
  unit interior 3D models, specification tags. Do not put empty dashboards in the PDF.

## Screenshot standards
- Browser window ~1920x1080, 100% zoom, desktop layout.
- Wait for full render before every capture: images, fonts, Chart.js canvases,
  Leaflet map tiles, 2D polygon overlays, and the 3D WebGL model.
- No browser chrome/URL bar, no loading spinners, no open dropdowns, no notification
  popups, no chat windows, no debug mode, no mouse cursor over content.
- Save PNGs to docs/re_showcase/screenshots/

## Capture list (in this order)

PRIORITY 1 — the 2D / 3D experience (this is the focus, give it the most space)
1. 3D project maquette — /odoo/action-real_estate_developer.action_realestate_project/28
   (Green Horizon Compound) → open the "3D Maquette" tab. Wait for the model to fully
   render, pick a clean angle, keep the whole model in frame, no clipping.
2. 3D — second project for variety: same steps on project 27 (Palm Hills New Cairo),
   which is fully mesh-mapped. Only keep it if it looks clearly different from #1.
3. 2D master plan — .../action_realestate_project/3 (Al Hamra Marina) → "2D Plan" tab.
   Clickable building regions must be visible.
4. 2D drill-down level 1 — /odoo/action-atmta_real_estate.action_property_view/3026
   (Green Horizon Compound) → "2D Plan" tab. Shows 4 building polygons.
5. 2D drill-down level 2 — .../action_property_view/3027 (Building B01) → "2D Plan" tab.
   Shows the unit polygons inside the building. (#4 + #5 together prove the drill.)
6. Building elevation + floors — .../action_property_view/2 (RH — Tower A)
   → "Elevation & Floors" tab.
7. Unit preview — .../action_property_view/3045 (unit B03-101).
   Capture the "Plans & 3D" tab (floor plan) and the "Media & Files" tab (5 gallery images).

PRIORITY 2 — business proof (only screens that have real data)
8.  Rental dashboard      — /odoo/action-atmta_real_estate.action_rental_dashboard
9.  Developer dashboard   — /odoo/action-real_estate_developer.action_developer_dashboard
10. Brokerage dashboard   — /odoo/action-real_estate_brokerage.action_brokerage_dashboard
11. Properties map        — /odoo/action-atmta_real_estate.action_properties_map_dashboard
    (wait for ALL map tiles to load — grey squares are a reject)
12. Project portfolio     — /odoo/action-real_estate_developer.action_realestate_project
13. Property inventory    — /odoo/action-atmta_real_estate.action_property_view
14. Sale contracts        — /odoo/action-real_estate_developer.action_sale_contract
15. Brokerage pipeline    — /odoo/action-real_estate_brokerage.action_realestate_listing_pipeline

PRIORITY 3 — public-facing
16. Public projects page  — http://egyptairodoodev.isdeg.com:8069/projects
17. Public project page   — .../projects/28 (has the live 2D + 3D viewers and unit counters)

## Sensitive data
Before using each shot, check for customer names, emails, phones, IDs, chatter messages,
internal notes. Crop or blur anything personal. Aggregate figures are fine.

## PDF output
- Landscape 16:9, 1920x1080 proportions.
- One feature per page: a short headline (max 8 words), an optional 3-6 word subline,
  and one large screenshot. At most two screenshots on the drill-down page.
- No paragraphs. No bullet lists of features. No model names, module names, Python,
  URLs, endpoints or database names anywhere in the PDF.
- Cover page: title "ATMTA Real Estate Suite", subtitle
  "A Complete Digital Platform for Real Estate Operations", using the best 3D or
  public project visual as the hero image. Do not invent a logo or contact details.
- Design: light background, deep navy/charcoal text, ONE accent colour, generous
  whitespace, rounded corners + soft shadow on screenshots, consistent margins,
  page numbers. Screenshots are the main content — keep text minimal.
- Suggested headlines: Interactive 3D Project Experience / Availability at a Glance /
  Interactive 2D Master Planning / Navigate from Master Plan to Unit /
  Visual Building and Floor Selection / Everything the Buyer Needs to Decide /
  A Digital Sales Experience for Every Project / Smarter Rental Operations /
  Complete Control of Every Project and Unit / A Connected Sales Pipeline /
  One View Across the Entire Business

## Build method (keep it simple and fast)
Generate a single self-contained HTML file with one .page div per slide, then render:
  google-chrome --headless --disable-gpu --no-pdf-header-footer \
    --print-to-pdf=docs/re_showcase/ATMTA_Real_Estate_Showcase.pdf docs/re_showcase/deck.html
Use @page { size: 1920px 1080px landscape; margin: 0 } and page-break-after on each slide.
Chrome is already installed — do not install any new tooling.

## Finish
Render each PDF page to PNG and visually check: sharp screenshots, readable headlines,
consistent alignment, no clipping, no empty charts, no browser chrome, no personal data.
Fix problems, then report: PDF path, number of screenshots used, number of pages, and
any feature you could not capture with the exact reason.
```

---

## Notes before you run it

**Record IDs** — valid at time of writing (database `real_estate_full`):

| ID | Record | Why it was chosen |
|----|--------|-------------------|
| 28 | Green Horizon Compound (project) | 3D model + 13 units with mixed availability + full 2D drill chain |
| 27 | Palm Hills New Cairo (project) | 3D model, every unit mesh-mapped |
| 3 | Al Hamra Marina (project) | The only project with a master-plan image + clickable building regions |
| 3026 | Green Horizon Compound (property) | Compound level of the 2D drill — 4 building polygons |
| 3027 | Building B01 (property) | Building level of the 2D drill — unit polygons |
| 2 | RH — Tower A (property) | The only building with an elevation sheet + floor records |
| 3045 | Unit B03-101 (property) | Floor plan + 5 gallery images + bedrooms/area filled in |

If any ID 404s, fall back to any project whose form has a populated **3D Maquette** or
**2D Plan** tab.

**Deliberately excluded** — these modules are installed but hold zero records, so any
dashboard built from them would render as all-zeros with empty charts:

- Construction (0 milestones, 0 contractors, 0 cost lines)
- Investment (1 draft study, NPV/IRR/payback all 0)
- Procurement (0 material requests)
- Snagging issues, warranties, unit interior 3D models, specification tags

**Product naming** — the application shows no custom branding (company is the Odoo
default, website is "My Website"). All 12 modules are authored by "Atmta", so
"ATMTA Real Estate Suite" is the correct title and no logo should be used.
