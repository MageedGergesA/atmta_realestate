/** @odoo-module **/

/**
 * M8 — Presentation Mode's component behaviour, in HOOT.
 *
 * Tours prove the whole stack works together; these prove the component
 * behaves when the *server* misbehaves — slow replies, out-of-order replies,
 * refusals, empty results. Those are the cases a showroom actually hits and
 * the ones a tour against a healthy database can never produce.
 *
 * Every server answer here is mocked, so nothing below asserts a price or a
 * schedule: the component's job is to display what the server said and to stay
 * standing when it says nothing. Deciding what a unit costs is Developer's job
 * and is tested in Python.
 *
 * The 3D and 2D children are stubbed out. Mounting the real ones would drag in
 * Three.js and a WebGL context, which is exactly the dependency the fallback
 * architecture exists to avoid — and it would make these tests about the GPU
 * rather than about the gallery.
 */

import { describe, expect, test } from "@odoo/hoot";
import { animationFrame, queryAll, queryFirst, queryText } from "@odoo/hoot-dom";
import { Component, xml } from "@odoo/owl";
import {
    contains,
    defineModels,
    mockService,
    mountWithCleanup,
    onRpc,
    patchWithCleanup,
} from "@web/../tests/web_test_helpers";

import { browser } from "@web/core/browser/browser";

import { mailModels } from "@mail/../tests/mail_test_helpers";

import { VisualGallery } from "@real_estate_maquette/js/visual_gallery";
import { MaquetteViewer } from "@real_estate_maquette/js/maquette_viewer";
import { MasterPlan2D } from "@real_estate_maquette/js/master_plan_2d";

// The gallery mounts real services, and the bus/mail services that come up
// with any backend component ask the mock server for `res.users`,
// `discuss.channel` and friends. `mailModels` is the web set plus those.
//
// `defineModels(mailModels)` rather than `defineMailModels()`: the latter also
// renames the suite to "mail", which would take these tests out of the
// `filter=real_estate_maquette` run that scopes the gate to this module.
defineModels(mailModels);

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const PROJECT = { id: 7, name: "Palm Heights", has_3d: true, has_2d: true, readiness: 100 };

const UNIT = {
    id: 42,
    name: "Unit 42",
    property_code: "PH-42",
    area_sqm: 180,
    base_price: 3500000,
    currency: "EGP",
    visual_state: "available",
};

const SOLD_UNIT = { ...UNIT, id: 43, property_code: "PH-43", visual_state: "sold" };

function context(overrides = {}) {
    return {
        mode: "presentation",
        project_id: PROJECT.id,
        project_name: PROJECT.name,
        audience: "internal",
        crm_lead_id: false,
        crm_lead_name: "",
        partner_name: "",
        shortlist: [],
        shortlist_available: true,
        can_reserve: true,
        fallback: { has_2d: true, min_capability: "low", default_experience: "auto" },
        ...overrides,
    };
}

const PLAN = {
    id: 1,
    name: "5-year plan",
    code: "P5",
    available: true,
    currency: "EGP",
    down_payment: 350000,
    installment_count: 20,
    installment_amount: 157500,
    duration_months: 60,
    schedule: [],
};

/**
 * Stub the children.
 *
 * They are replaced by markers carrying the same class names the real
 * components mount under, so "which experience is showing" stays observable
 * without a GPU. `onUnitSelected` is kept on the stub so selection can be
 * driven exactly the way the real viewer drives it.
 */
function stubChildren() {
    class StubViewer extends Component {
        static template = xml`<div class="o_maquette_root o_stub_viewer"/>`;
        static props = ["*"];
    }
    class StubPlan extends Component {
        static template = xml`<div class="o_master_plan_2d o_stub_plan"/>`;
        static props = ["*"];
    }
    patchWithCleanup(VisualGallery, {
        components: { MaquetteViewer: StubViewer, MasterPlan2D: StubPlan },
    });
}

/** Mount the gallery on a project, with the standard happy-path routes. */
async function mountGallery({ ctx, plans, projects } = {}) {
    stubChildren();
    onRpc("/visual/gallery/projects", () => projects || [PROJECT]);
    onRpc("/visual/gallery/context", () => ctx || context());
    onRpc("/visual/gallery/payment_plans", () => ({ plans: plans || [PLAN] }));
    const gallery = await mountWithCleanup(VisualGallery, {
        props: { action: { params: { project_id: PROJECT.id } } },
    });
    // The real load promise, not a guessed number of frames.
    await gallery.loaded;
    await animationFrame();
    return gallery;
}

/** Drive a selection the way the viewer does. */
async function select(gallery, unit) {
    await gallery.onUnitSelected(unit);
    await animationFrame();
}

// ---------------------------------------------------------------------------

describe("opening the gallery", () => {
    test("shows the project name, not a form", async () => {
        await mountGallery();

        expect(".o_visual_gallery").toHaveCount(1);
        expect(queryText(".o_visual_gallery_title")).toBe("Palm Heights");
        expect(".o_form_view").toHaveCount(0);
    });

    test("renders a splash while the server is still answering", async () => {
        // The showroom screen must change the moment somebody opens the
        // gallery, not when the last RPC lands.
        stubChildren();
        let release;
        const held = new Promise((resolve) => { release = resolve; });
        onRpc("/visual/gallery/projects", () => [PROJECT]);
        onRpc("/visual/gallery/context", async () => { await held; return context(); });
        const gallery = await mountWithCleanup(VisualGallery, {
            props: { action: { params: { project_id: PROJECT.id } } },
        });
        await animationFrame();

        expect(".o_visual_gallery_splash").toHaveCount(1);
        expect(".o_visual_gallery_bar").toHaveCount(0);

        release();
        await gallery.loaded;
        await animationFrame();
        expect(".o_visual_gallery_splash").toHaveCount(0);
        expect(".o_visual_gallery_bar").toHaveCount(1);
    });

    test("a gallery that cannot load at all says so", async () => {
        stubChildren();
        onRpc("/visual/gallery/projects", () => { throw new Error("down"); });
        const gallery = await mountWithCleanup(VisualGallery, {
            props: { action: { params: { project_id: PROJECT.id } } },
        });
        await gallery.loaded;
        await animationFrame();

        expect(queryText(".o_visual_gallery_splash")).toInclude(
            "could not be opened");
    });

    test("a refusal is stated in words, not as a dialog", async () => {
        // Wrong-project access, an unpublished project, a revoked role: the
        // server answers `{error}` and the customer sees a sentence.
        await mountGallery({ ctx: { error: "That project is not available." } });

        expect(queryText(".o_visual_gallery_splash")).toInclude(
            "That project is not available.");
        expect(".o_visual_gallery_bar").toHaveCount(0);
    });

    test("with no customer, no customer name is shown", async () => {
        await mountGallery();

        expect(".o_vg_customer_name").toHaveCount(0);
    });
});

describe("the unit panel", () => {
    test("a selection shows the unit and its payment plans", async () => {
        const gallery = await mountGallery();

        await select(gallery, UNIT);

        expect(".o_vg_unit_panel").toHaveCount(1);
        expect(queryText(".o_vg_unit_name")).toBe("Unit 42");
        expect(queryText(".o_vg_plans")).toInclude("5-year plan");
        // Displayed, not recomputed: the down payment is the server's number.
        expect(queryText(".o_vg_plan_down")).toInclude("350,000");
    });

    test("closing the panel clears the plans with it", async () => {
        const gallery = await mountGallery();
        await select(gallery, UNIT);

        await select(gallery, null);

        expect(".o_vg_unit_panel").toHaveCount(0);
        expect(gallery.state.paymentPlans).toEqual([]);
    });

    test("a unit with no published plan says so", async () => {
        const gallery = await mountGallery({ plans: [] });

        await select(gallery, UNIT);

        expect(queryText(".o_vg_plans_none")).toInclude("No payment plan");
    });

    test("a plan the developer cannot quote gives its reason", async () => {
        const gallery = await mountGallery({
            plans: [{
                id: 2, name: "Handover plan", code: "H", available: false,
                reason: "This project has no expected handover date yet.",
                schedule: [],
            }],
        });

        await select(gallery, UNIT);

        expect(queryText(".o_vg_plan_unavailable")).toInclude(
            "no expected handover date");
    });

    test("a failed plan request degrades instead of raising", async () => {
        // Rule 4 in the panel: no red dialog on a showroom screen.
        stubChildren();
        onRpc("/visual/gallery/projects", () => [PROJECT]);
        onRpc("/visual/gallery/context", () => context());
        onRpc("/visual/gallery/payment_plans", () => { throw new Error("boom"); });
        const gallery = await mountWithCleanup(VisualGallery, {
            props: { action: { params: { project_id: PROJECT.id } } },
        });
        await gallery.loaded;
        await animationFrame();

        await select(gallery, UNIT);

        expect(".o_vg_unit_panel").toHaveCount(1);
        expect(gallery.state.paymentPlans).toEqual([]);
        expect(gallery.state.plansLoading).toBe(false);
    });

    test("a slow reply for an abandoned unit never lands on the new one", async () => {
        // A customer clicking through units faster than the network answers
        // must not read unit 42's schedule under unit 43's name.
        stubChildren();
        const gate = {};
        for (const id of [UNIT.id, SOLD_UNIT.id]) {
            let resolve;
            const promise = new Promise((r) => { resolve = r; });
            gate[id] = { promise, resolve };
        }
        onRpc("/visual/gallery/projects", () => [PROJECT]);
        onRpc("/visual/gallery/context", () => context());
        onRpc("/visual/gallery/payment_plans", async (request) => {
            const { params } = await request.json();
            return gate[params.property_id].promise;
        });
        const gallery = await mountWithCleanup(VisualGallery, {
            props: { action: { params: { project_id: PROJECT.id } } },
        });
        await gallery.loaded;
        await animationFrame();

        const first = gallery.onUnitSelected(UNIT);        // 42, in flight
        const second = gallery.onUnitSelected(SOLD_UNIT);  // 43, in flight
        // 43 answers first, then 42 arrives late for a unit nobody is on.
        gate[SOLD_UNIT.id].resolve({ plans: [{ ...PLAN, name: "plan-for-43" }] });
        await second;
        gate[UNIT.id].resolve({ plans: [{ ...PLAN, name: "plan-for-42" }] });
        await first;
        await animationFrame();

        expect(gallery.state.selectedUnit.id).toBe(SOLD_UNIT.id);
        expect(gallery.state.paymentPlans.map((p) => p.name)).toEqual(
            ["plan-for-43"],
            { message: "A stale reply overwrote the selected unit's plans." });
    });

    test("reserve is offered for an available unit and withheld for a sold one",
        async () => {
            const gallery = await mountGallery();

            await select(gallery, UNIT);
            expect(".o_vg_reserve").toHaveCount(1);

            await select(gallery, SOLD_UNIT);
            expect(".o_vg_reserve").toHaveCount(0);
        });
});

describe("the shortlist", () => {
    test("is hidden entirely when the user may not use it", async () => {
        // Brokerage absent, or a user without its sales role. Advertising a
        // button that then fails is how the Access Error dialog reached a
        // customer in the first place.
        const gallery = await mountGallery({
            ctx: context({ shortlist_available: false }),
        });

        await select(gallery, UNIT);

        expect(".o_vg_shortlist").toHaveCount(0);
    });

    test("without an opportunity, saving explains itself rather than writing",
        async () => {
            const notifications = [];
            mockService("notification", {
                add: (message) => { notifications.push(message); },
            });
            let wrote = false;
            onRpc("/visual/gallery/shortlist", () => { wrote = true; return {}; });
            const gallery = await mountGallery();
            await select(gallery, UNIT);

            await contains(".o_vg_shortlist").click();

            expect(wrote).toBe(false, {
                message: "An anonymous favourite must not write CRM data.",
            });
            expect(notifications.join(" ")).toInclude("opportunity");
        });

    test("with an opportunity, saving goes to the server and comes back saved",
        async () => {
            const ctx = context({
                crm_lead_id: 99, crm_lead_name: "Ms Nour", partner_name: "Ms Nour",
            });
            let saved = null;
            stubChildren();
            onRpc("/visual/gallery/projects", () => [PROJECT]);
            onRpc("/visual/gallery/context", () => (
                saved ? { ...ctx, shortlist: [UNIT] } : ctx));
            onRpc("/visual/gallery/payment_plans", () => ({ plans: [PLAN] }));
            onRpc("/visual/gallery/shortlist", () => { saved = true; return {}; });
            const gallery = await mountWithCleanup(VisualGallery, {
                props: {
                    action: { params: { project_id: PROJECT.id, crm_lead_id: 99 } },
                },
            });
            await gallery.loaded;
            await animationFrame();
            expect(queryText(".o_vg_customer_name")).toInclude("Ms Nour");

            await select(gallery, UNIT);
            await contains(".o_vg_shortlist").click();
            await animationFrame();

            expect(saved).toBe(true);
            expect(gallery.isShortlisted(UNIT)).toBe(true);
            expect(queryText(".o_vg_shortlist")).toInclude("Saved");
        });
});

describe("comparison", () => {
    test("needs two units before it will open", async () => {
        const notifications = [];
        mockService("notification", { add: (m) => notifications.push(m) });
        let asked = false;
        onRpc("/visual/gallery/compare", () => { asked = true; return {}; });
        const gallery = await mountGallery();
        await select(gallery, UNIT);

        await contains(".o_vg_compare_toggle").click();
        await gallery.openComparison();
        await animationFrame();

        expect(asked).toBe(false);
        expect(notifications.join(" ")).toInclude("two units");
    });

    test("re-reads from the server rather than comparing stale rows", async () => {
        // A unit reserved since it was selected must compare as reserved.
        let requested = null;
        onRpc("/visual/gallery/compare", async (request) => {
            const body = await request.json();
            requested = body.params.property_ids;
            return {
                units: [UNIT, SOLD_UNIT],
                rows: [{ key: "visual_state", label: "Status", best: UNIT.id }],
                as_of: "2026-08-08 09:00:00",
            };
        });
        const gallery = await mountGallery();

        gallery.toggleCompare(UNIT);
        gallery.toggleCompare(SOLD_UNIT);
        await gallery.openComparison();
        await animationFrame();

        expect(requested).toEqual([UNIT.id, SOLD_UNIT.id]);
        expect(".o_visual_gallery_compare").toHaveCount(1);
        expect(queryText(".o_vg_compare_asof")).toInclude("2026-08-08");
        expect(queryAll(".o_vg_compare_table thead th").length).toBe(3);
    });

    test("caps a comparison at four units", async () => {
        mockService("notification", { add: () => {} });
        const gallery = await mountGallery();

        for (let id = 1; id <= 6; id++) {
            gallery.toggleCompare({ ...UNIT, id });
        }

        expect(gallery.state.compareIds.length).toBe(4);
    });

    test("closing the comparison returns to the gallery", async () => {
        onRpc("/visual/gallery/compare", () => ({
            units: [UNIT], rows: [], as_of: "2026-08-08 09:00:00",
        }));
        const gallery = await mountGallery();
        gallery.toggleCompare(UNIT);
        gallery.toggleCompare(SOLD_UNIT);
        await gallery.openComparison();
        await animationFrame();

        gallery.closeComparison();
        await animationFrame();

        expect(".o_visual_gallery_compare").toHaveCount(0);
        expect(".o_visual_gallery_bar").toHaveCount(1);
    });
});

describe("experiences", () => {
    test("switching to the plan swaps the child, and back again", async () => {
        const gallery = await mountGallery();

        expect(".o_stub_viewer").toHaveCount(1);

        gallery.setExperience("2d");
        await animationFrame();
        expect(".o_stub_plan").toHaveCount(1);
        expect(".o_stub_viewer").toHaveCount(0);

        gallery.setExperience("3d");
        await animationFrame();
        expect(".o_stub_viewer").toHaveCount(1);
        expect(".o_stub_plan").toHaveCount(0);
    });

    test("a project configured for 2D opens on the plan", async () => {
        const gallery = await mountGallery({
            ctx: context({
                fallback: {
                    has_2d: true, min_capability: "low", default_experience: "2d",
                },
            }),
        });

        expect(gallery.state.experience).toBe("2d");
        expect(".o_stub_plan").toHaveCount(1);
    });

    test("the project id is read from the URL when the action carries none",
        async () => {
            // A salesperson pasting a shared deep link. Opening blank is the
            // failure mode that matters most for links.
            stubChildren();
            browser.location.search = `?project_id=${PROJECT.id}`;
            let askedFor = null;
            onRpc("/visual/gallery/projects", () => [PROJECT]);
            onRpc("/visual/gallery/context", async (request) => {
                const body = await request.json();
                askedFor = body.params.project_id;
                return context();
            });
            const gallery = await mountWithCleanup(VisualGallery, { props: {} });
            await gallery.loaded;
            await animationFrame();

            expect(askedFor).toBe(PROJECT.id);
        });
});

describe("teardown", () => {
    test("unmounting leaves no gallery behind", async () => {
        const gallery = await mountGallery();
        await select(gallery, UNIT);

        gallery.__owl__.app.destroy();
        await animationFrame();

        expect(".o_visual_gallery").toHaveCount(0);
        expect(queryFirst(".o_vg_unit_panel")).toBe(null);
    });
});
