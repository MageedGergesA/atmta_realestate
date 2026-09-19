/** @odoo-module **/

import { beforeEach, describe, expect, test } from "@odoo/hoot";
import { animationFrame, runAllTimers } from "@odoo/hoot-mock";
import { click, press, queryAll, queryAllTexts, queryFirst, queryText } from "@odoo/hoot-dom";
import {
    defineModels,
    makeMockEnv,
    mockService,
    models,
    mountWithCleanup,
    onRpc,
    preloadBundle,
} from "@web/../tests/web_test_helpers";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";

import { RentalDashboard } from "@atmta_real_estate/js/dashboard/rental_dashboard";

describe.current.tags("desktop");

/** `atmta_real_estate` depends on `mail`, so the mail models must be defined. */
defineMailModels();

class RentalDashboardModel extends models.Model {
    _name = "realestate.rental.dashboard";

    /** The map card loads on its own; by default there is nothing to place. */
    get_map() {
        return { units: [], without_coordinates: 0, truncated: false };
    }
}

defineModels({ RentalDashboardModel });

/** DashboardChart lazy-loads Odoo's own Chart.js bundle. */
preloadBundle("web.chartjs_lib");

/** Mount the component alone, without the main components container. */
const MOUNT_OPTIONS = { noMainContainer: true };
const MODEL = "realestate.rental.dashboard";

/** A `get_work` payload with the shape the backend returns. */
function makeWork(overrides = {}) {
    return {
        sections: [
            {
                id: "work",
                title: "My Work",
                tiles: [
                    { key: "leases_to_approve", label: "Leases to Approve", value: 2, format: "integer", hint: "", warning: true, drill: true },
                    { key: "signatures_waiting", label: "Awaiting Signature", value: 1, format: "integer", hint: "", warning: false, drill: true },
                    { key: "overdue_obligations", label: "Overdue Obligations", value: 3, format: "integer", hint: "", warning: true, drill: true },
                    { key: "my_activities", label: "My Activities Due", value: 0, format: "integer", hint: "", warning: false, drill: true },
                ],
            },
            {
                id: "portfolio",
                title: "Portfolio Health",
                tiles: [
                    { key: "available_to_lease", label: "Available to Lease", value: 24, format: "integer", hint: "", warning: false, drill: true },
                    { key: "occupied_units", label: "Occupied Units", value: 96, format: "integer", hint: "", warning: false, drill: true },
                    { key: "occupancy_rate", label: "Occupancy", value: 80.0, format: "percent", hint: "Occupied units as a share of leasable units.", warning: false, drill: false },
                    { key: "outstanding_rent", label: "Outstanding Rent", value: 62500.0, format: "monetary", hint: "", warning: true, drill: true },
                ],
            },
        ],
        scope: "mine",
        quick_actions: [
            { key: "new_lease", label: "New Lease", icon: "fa-plus" },
            { key: "available_units", label: "Find Available Unit", icon: "fa-search" },
        ],
        currency_id: 1,
        company_id: 1,
        company_name: "Test Estate Co",
        as_of: "2026-08-04",
        ...overrides,
    };
}

const TILE_COUNT = 8;

/** A `get_trends` payload with the shape the backend returns. */
function makeTrends() {
    return {
        charts: {
            billed_vs_collected: {
                labels: ["Jan 2026", "Feb 2026", "Mar 2026"],
                billed: [450000, 460000, 465000],
                collected: [430000, 441000, 402500],
                scheduled: [455000, 462000, 470000],
            },
            arrears_aging: {
                labels: ["Current", "1-30 Days", "31-60 Days", "61-90 Days", "91-120 Days", "120+ Days"],
                keys: ["current", "1_30", "31_60", "61_90", "91_120", "120_plus"],
                amounts: [0, 21000, 18500, 12000, 7000, 4000],
                counts: [0, 6, 4, 3, 2, 1],
            },
            expiries_by_month: { labels: ["Aug 2026", "Sep 2026", "Oct 2026"], counts: [4, 5, 6] },
            occupancy_trend: { labels: ["Jan 2026", "Feb 2026", "Mar 2026"], occupancy_pct: [76.0, 78.5, 80.0] },
        },
        currency_id: 1,
    };
}

function makeEmptyTrends() {
    return {
        charts: {
            billed_vs_collected: { labels: [], billed: [], collected: [], scheduled: [] },
            arrears_aging: { labels: [], keys: [], amounts: [], counts: [] },
            expiries_by_month: { labels: [], counts: [] },
            occupancy_trend: { labels: [], occupancy_pct: [] },
        },
        currency_id: 1,
    };
}

/** Serve both calls, recording every request. */
function serve({ work = makeWork, trends = makeTrends } = {}) {
    const calls = [];
    onRpc(MODEL, "get_work", ({ args }) => {
        calls.push(["get_work", ...args]);
        return work(args);
    });
    onRpc(MODEL, "get_trends", () => {
        calls.push(["get_trends"]);
        return trends();
    });
    return calls;
}

async function mountDashboard() {
    const dashboard = await mountWithCleanup(RentalDashboard, MOUNT_OPTIONS);
    await animationFrame();
    await animationFrame();
    return dashboard;
}

beforeEach(async () => {
    await makeMockEnv();
});

// ===========================================================================
// Rendering
// ===========================================================================
describe("rendering", () => {
    test("the dashboard mounts with company and date from the payload", async () => {
        serve();
        await mountDashboard();

        expect(".o_re_dashboard").toHaveCount(1);
        expect(".o_re_title").toHaveText("Rental");
        expect(queryText(".o_re_subtitle")).toInclude("Test Estate Co");
        expect(queryText(".o_re_subtitle")).toInclude("2026-08-04");
    });

    test("every tile the backend sends is rendered, and no other", async () => {
        serve();
        await mountDashboard();

        expect("[data-tile]").toHaveCount(TILE_COUNT);
        expect(".o_re_kpi:not(.o_re_skeleton)").toHaveCount(TILE_COUNT);
    });

    test("work comes first, then portfolio health, the map, then trends", async () => {
        serve();
        await mountDashboard();

        const sections = queryAll(".o_re_section[data-section]").map((el) => el.dataset.section);
        expect(sections).toEqual(["work", "portfolio", "map", "trends"]);
        const titles = queryAllTexts(".o_re_section_title").map((t) => t.toLowerCase());
        expect(titles).toEqual(["my work", "portfolio health", "map", "trends"]);
    });

    test("counts, currency and percentages are visually distinguishable", async () => {
        serve();
        await mountDashboard();

        const values = queryAllTexts(".o_re_kpi_value");
        expect(values.some((v) => v === "24")).toBe(true);
        expect(values.some((v) => v.includes("%"))).toBe(true);
        expect(values.some((v) => v.includes("$"))).toBe(true);
    });

    test("tiles needing attention are flagged beyond colour alone", async () => {
        serve();
        await mountDashboard();

        expect(".o_re_kpi_warning .o_re_kpi_status").toHaveCount(3);
        expect(queryAllTexts(".o_re_kpi_warning .o_re_kpi_status")).toInclude("Needs attention");
    });

    test("quick actions are offered, the first one primary", async () => {
        serve();
        await mountDashboard();

        expect(queryAllTexts(".o_re_quick_action")).toEqual(["New Lease", "Find Available Unit"]);
        expect(queryFirst(".o_re_quick_action")).toHaveClass("btn-primary");
    });
});

// ===========================================================================
// Mine / Team
// ===========================================================================
describe("scope", () => {
    test("the dashboard opens on my work", async () => {
        const calls = serve();
        await mountDashboard();

        expect(calls[0]).toEqual(["get_work", "mine"]);
        expect(".o_re_scope [data-scope='mine']").toHaveAttribute("aria-pressed", "true");
    });

    test("switching to team reloads the tiles only", async () => {
        const calls = serve();
        await mountDashboard();

        await click(".o_re_scope [data-scope='team']");
        await animationFrame();
        await animationFrame();

        expect(calls).toEqual([["get_work", "mine"], ["get_trends"], ["get_work", "team"]]);
        expect(".o_re_scope [data-scope='team']").toHaveAttribute("aria-pressed", "true");
        expect(".o_re_skeleton").toHaveCount(0);
    });
});

// ===========================================================================
// Charts
// ===========================================================================
describe("charts", () => {
    test("the four trend charts render", async () => {
        serve();
        await mountDashboard();

        expect(".o_re_chart_panel").toHaveCount(4);
        expect(queryAllTexts(".o_re_chart_panel .o_re_panel_title")).toEqual([
            "Billed vs Collected",
            "Arrears Ageing",
            "Lease Expiries by Month",
            "Occupancy over Time",
        ]);
        expect(".o_re_chart_panel canvas").toHaveCount(4);
    });

    test("empty trend data renders an empty state instead of crashing", async () => {
        serve({ trends: makeEmptyTrends });
        await mountDashboard();

        expect(".o_re_chart_panel canvas").toHaveCount(0);
        expect(".o_re_chart_panel .o_re_empty").toHaveCount(4);
    });

    test("tiles do not wait for the charts", async () => {
        let resolveTrends;
        onRpc(MODEL, "get_work", () => makeWork());
        onRpc(MODEL, "get_trends", () => new Promise((resolve) => { resolveTrends = resolve; }));
        await mountDashboard();

        expect("[data-tile] .o_re_kpi_value").toHaveCount(TILE_COUNT);
        expect(".o_re_chart_skeleton").toHaveCount(4);
        expect(".o_re_chart_panel").toHaveCount(0);

        resolveTrends(makeTrends());
        await animationFrame();
        await animationFrame();
        expect(".o_re_chart_skeleton").toHaveCount(0);
        expect(".o_re_chart_panel canvas").toHaveCount(4);
    });

    test("a trends failure keeps the tiles and says the charts are missing", async () => {
        onRpc(MODEL, "get_work", () => makeWork());
        onRpc(MODEL, "get_trends", () => {
            throw new Error("boom");
        });
        await mountDashboard();

        expect("[data-tile] .o_re_kpi_value").toHaveCount(TILE_COUNT);
        expect(".o_re_state_error").toHaveCount(0);
        expect(queryText("[data-section='trends'] .o_re_empty_text")).toInclude("could not be loaded");
    });

    test("refreshing does not accumulate chart instances", async () => {
        const calls = serve();
        await mountDashboard();
        expect(".o_re_chart_panel canvas").toHaveCount(4);

        await click(".o_re_refresh");
        await animationFrame();
        await animationFrame();

        expect(calls.filter(([method]) => method === "get_trends")).toHaveLength(2);
        expect(".o_re_chart_panel canvas").toHaveCount(4);
    });
});

// ===========================================================================
// Loading and errors
// ===========================================================================
describe("loading and errors", () => {
    test("skeletons show while loading and never a fake zero", async () => {
        let resolveWork;
        onRpc(MODEL, "get_work", () => new Promise((resolve) => { resolveWork = resolve; }));
        onRpc(MODEL, "get_trends", () => makeTrends());
        mountWithCleanup(RentalDashboard, MOUNT_OPTIONS);
        await animationFrame();

        expect(".o_re_skeleton").toHaveCount(8);
        expect(".o_re_kpi_value").toHaveCount(0);

        resolveWork(makeWork());
        await animationFrame();
        await animationFrame();
        await animationFrame();

        expect(".o_re_skeleton").toHaveCount(0);
        expect(".o_re_kpi_value").toHaveCount(TILE_COUNT);
    });

    test("a failure shows an error state, not a blank page", async () => {
        onRpc(MODEL, "get_work", () => {
            throw new Error("boom");
        });
        await mountDashboard();

        expect(".o_re_state_error").toHaveCount(1);
        expect(queryText(".o_re_state_title")).toInclude("could not be loaded");
        expect(".o_re_kpi_value").toHaveCount(0);
    });

    test("Retry issues another request and recovers", async () => {
        let attempt = 0;
        onRpc(MODEL, "get_work", () => {
            attempt++;
            if (attempt === 1) {
                throw new Error("transient");
            }
            return makeWork();
        });
        onRpc(MODEL, "get_trends", () => makeTrends());
        await mountDashboard();
        expect(".o_re_state_error").toHaveCount(1);

        await click(".o_re_state_error .btn-primary");
        await animationFrame();
        await animationFrame();

        expect(attempt).toBe(2);
        expect(".o_re_state_error").toHaveCount(0);
        expect(".o_re_kpi_value").toHaveCount(TILE_COUNT);
    });

    test("one request for the tiles and one for the charts per load", async () => {
        const calls = serve();
        await mountDashboard();
        await runAllTimers();

        expect(calls).toEqual([["get_work", "mine"], ["get_trends"]]);
    });
});

// ===========================================================================
// Drilldowns and actions
// ===========================================================================
describe("actions", () => {
    function captureActions() {
        const done = [];
        mockService("action", {
            doAction: (action) => {
                done.push(action);
            },
        });
        return done;
    }

    test("clicking a tile asks the backend for its records, in the current scope", async () => {
        serve();
        const drills = [];
        onRpc(MODEL, "action_drill", ({ args }) => {
            drills.push(args);
            return {
                type: "ir.actions.act_window",
                name: "Available to Lease",
                res_model: "realestate.property",
                domain: [["is_available_for_lease", "=", true]],
                views: [[false, "list"]],
            };
        });
        const done = captureActions();
        await mountDashboard();

        await click("[data-tile='available_to_lease'] .o_re_kpi");
        await animationFrame();
        await click(".o_re_scope [data-scope='team']");
        await animationFrame();
        await animationFrame();
        await click("[data-tile='available_to_lease'] .o_re_kpi");
        await animationFrame();

        expect(drills).toEqual([["available_to_lease", "mine"], ["available_to_lease", "team"]]);
        expect(done).toHaveLength(2);
        expect(done[0].res_model).toBe("realestate.property");
    });

    test("a ratio tile is not clickable: it has no record set", async () => {
        serve();
        await mountDashboard();

        expect("[data-tile='occupancy_rate'] .o_re_kpi_clickable").toHaveCount(0);
        expect("[data-tile='occupied_units'] .o_re_kpi_clickable").toHaveCount(1);
    });

    test("a clickable tile is keyboard operable", async () => {
        serve();
        const drills = [];
        onRpc(MODEL, "action_drill", ({ args }) => {
            drills.push(args[0]);
            return { type: "ir.actions.act_window", res_model: "realestate.contract", views: [[false, "list"]] };
        });
        captureActions();
        await mountDashboard();

        queryFirst("[data-tile='signatures_waiting'] .o_re_kpi").focus();
        await press("Enter");
        await animationFrame();

        expect(drills).toEqual(["signatures_waiting"]);
    });

    test("a quick action asks the backend for its screen", async () => {
        serve();
        const quick = [];
        onRpc(MODEL, "action_quick", ({ args }) => {
            quick.push(args[0]);
            return { type: "ir.actions.act_window", res_model: "realestate.contract", views: [[false, "form"]] };
        });
        const done = captureActions();
        await mountDashboard();

        await click(".o_re_quick_action[data-quick-action='new_lease']");
        await animationFrame();

        expect(quick).toEqual(["new_lease"]);
        expect(done[0].res_model).toBe("realestate.contract");
    });

    test("a malformed action is still repaired (defensive fallback)", async () => {
        serve();
        onRpc(MODEL, "action_drill", () => ({
            type: "ir.actions.act_window",
            res_model: "realestate.contract",
            view_mode: "list,form",
        }));
        const done = captureActions();
        await mountDashboard();

        await click("[data-tile='leases_to_approve'] .o_re_kpi");
        await animationFrame();

        expect(done[0].views).toEqual([[false, "list"], [false, "form"]]);
    });

    test("a failed drilldown notifies rather than breaking the page", async () => {
        serve();
        onRpc(MODEL, "action_drill", () => {
            throw new Error("nope");
        });
        const notes = [];
        mockService("notification", {
            add: (message) => {
                notes.push(message);
            },
        });
        await mountDashboard();

        await click("[data-tile='overdue_obligations'] .o_re_kpi");
        await animationFrame();

        expect(notes).toHaveLength(1);
        expect(".o_re_dashboard").toHaveCount(1);
    });
});

// ===========================================================================
// Lifecycle
// ===========================================================================
describe("lifecycle", () => {
    test("unmounting destroys every chart instance", async () => {
        serve();
        const dashboard = await mountDashboard();
        expect(".o_re_chart_panel canvas").toHaveCount(4);

        dashboard.__owl__.app.destroy();
        await animationFrame();

        expect(".o_re_chart_panel canvas").toHaveCount(0);
    });
});

// ===========================================================================
// Map card
// ===========================================================================
describe("map", () => {
    test("with no located unit, the card explains how to place one", async () => {
        serve();
        onRpc(MODEL, "get_map", () => ({ units: [], without_coordinates: 3, truncated: false }));
        await mountDashboard();

        expect(".o_re_map_card").toHaveCount(1);
        expect(".o_re_map_empty").toHaveCount(1);
        expect(queryText(".o_re_map_card .o_re_panel_subtitle")).toInclude("3 without coordinates");
    });

    test("located units are counted by status", async () => {
        serve();
        onRpc(MODEL, "get_map", () => ({
            units: [
                { id: 1, name: "Unit 1", code: "U-1", lat: 24.71, lng: 46.67, status: "rented", status_label: "Rented" },
                { id: 2, name: "Unit 2", code: "U-2", lat: 24.72, lng: 46.68, status: "available", status_label: "Available" },
                { id: 3, name: "Unit 3", code: "U-3", lat: 24.73, lng: 46.69, status: "rented", status_label: "Rented" },
            ],
            without_coordinates: 0,
            truncated: false,
        }));
        await mountDashboard();

        expect(queryText(".o_re_map_located")).toInclude("3 located");
        expect(".o_re_map_empty").toHaveCount(0);
        // Label and count are separate elements, so collapse the whitespace between them.
        expect(queryAllTexts(".o_re_map_legend li").map((text) => text.replace(/\s+/g, " ")))
            .toEqual(["Available 1", "Rented 2"]);
    });

    test("a failed map load does not take the dashboard down", async () => {
        serve();
        onRpc(MODEL, "get_map", () => {
            throw new Error("map unavailable");
        });
        await mountDashboard();

        expect("[data-tile]").toHaveCount(TILE_COUNT);
        expect(queryText(".o_re_map_card")).toInclude("The map could not be loaded.");
    });

    test("open full map asks the backend for the action", async () => {
        const opened = [];
        mockService("action", {
            doAction(action) {
                opened.push(action);
            },
        });
        serve();
        onRpc(MODEL, "action_open_map", () => ({ type: "ir.actions.client", tag: "realestate.properties_map" }));
        await mountDashboard();

        await click(".o_re_map_open_full");
        await animationFrame();
        expect(opened.map((action) => action.tag)).toEqual(["realestate.properties_map"]);
    });
});
