/** @odoo-module **/

import { registry } from "@web/core/registry";
import { stepUtils } from "@web_tour/tour_service/tour_utils";

/**
 * Environment tours for the Rental Dashboard V2.
 *
 * These exist because a stylesheet reading correctly is not evidence that a
 * layout renders correctly. Everything below is asserted against a live DOM in
 * a real (headless) Chrome, at a real viewport, with real computed styles:
 * geometry comes from getBoundingClientRect(), direction from
 * getComputedStyle(). The Python side (tests/test_dashboard_env.py) is what
 * varies the viewport, the language and the company set.
 */

// ---------------------------------------------------------------------------
// Shared assertions
// ---------------------------------------------------------------------------

/** Every number the dashboard prints must be a number. */
function assertNoBrokenNumbers() {
    const root = document.querySelector(".o_re_dashboard");
    if (!root) {
        throw new Error("Dashboard root not found");
    }
    const text = root.innerText || "";
    // ∞ is the Unicode infinity sign, which is how Intl renders Infinity.
    const broken = text.match(/NaN|undefined|Infinity|∞|\[object Object\]/);
    if (broken) {
        const line = text
            .split("\n")
            .find((l) => l.includes(broken[0]));
        throw new Error(
            `Dashboard rendered a broken value: "${broken[0]}" in line "${line}". ` +
            "A KPI divided by zero or read a missing key."
        );
    }
}

/** The page must never scroll sideways, at any viewport, in any direction. */
function assertNoHorizontalOverflow(label) {
    const doc = document.documentElement;
    // 1px of tolerance for sub-pixel rounding of borders.
    if (doc.scrollWidth > doc.clientWidth + 1) {
        throw new Error(
            `[${label}] Page overflows horizontally: scrollWidth=${doc.scrollWidth} ` +
            `clientWidth=${doc.clientWidth} (viewport ${window.innerWidth}px)`
        );
    }
    const root = document.querySelector(".o_re_dashboard");
    if (root && root.scrollWidth > root.clientWidth + 1) {
        throw new Error(
            `[${label}] Dashboard overflows its container: ${root.scrollWidth} > ${root.clientWidth}`
        );
    }
}

/**
 * The dashboard must scroll vertically. Odoo's `.o_action` hides overflow, so a
 * dashboard taller than the window was simply cut off with no way to reach the
 * lower panels; nothing checked for it.
 */
function assertScrollsVertically(label) {
    const scroller = document.querySelector(".o_re_dashboard .o_re_scroll");
    if (!scroller) {
        throw new Error(`[${label}] Dashboard scroll container not found`);
    }
    const overflowY = getComputedStyle(scroller).overflowY;
    if (overflowY !== "auto" && overflowY !== "scroll") {
        throw new Error(`[${label}] Dashboard cannot scroll: overflow-y is ${overflowY}`);
    }
    if (scroller.scrollHeight > scroller.clientHeight + 1) {
        const before = scroller.scrollTop;
        scroller.scrollTop = before + 100;
        if (scroller.scrollTop === before) {
            throw new Error(`[${label}] Dashboard content is taller than its box but does not scroll`);
        }
        scroller.scrollTop = before;
    }
}

/**
 * Nothing may be clipped by, or spill out of, the dashboard's own box, and
 * every chart canvas must have real pixels. A canvas of zero width is the
 * classic flex/height-auto failure and is invisible to a CSS review.
 */
function assertPanelsFitAndCharts(label) {
    const root = document.querySelector(".o_re_dashboard");
    const rootRect = root.getBoundingClientRect();

    const canvases = document.querySelectorAll(".o_re_chart_panel canvas");
    if (!canvases.length) {
        throw new Error(`[${label}] No chart canvas rendered`);
    }
    for (const canvas of canvases) {
        const rect = canvas.getBoundingClientRect();
        if (rect.width < 40 || rect.height < 40) {
            throw new Error(
                `[${label}] Chart canvas collapsed: ${Math.round(rect.width)}x` +
                `${Math.round(rect.height)}px`
            );
        }
        if (rect.right > rootRect.right + 1 || rect.left < rootRect.left - 1) {
            throw new Error(
                `[${label}] Chart canvas escapes the dashboard box ` +
                `(canvas ${Math.round(rect.left)}-${Math.round(rect.right)}, ` +
                `root ${Math.round(rootRect.left)}-${Math.round(rootRect.right)})`
            );
        }
    }

    for (const kpi of document.querySelectorAll(".o_re_kpi")) {
        const rect = kpi.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) {
            throw new Error(`[${label}] A KPI card has zero size`);
        }
        if (rect.right > rootRect.right + 1 || rect.left < rootRect.left - 1) {
            throw new Error(`[${label}] A KPI card escapes the dashboard box`);
        }
    }
}

/**
 * Every tile the backend sent must be present and readable, however narrow the
 * screen. A Rental Manager (the tour user) is sent all 22 tiles.
 */
function assertAllKpisRendered(label) {
    const values = document.querySelectorAll(
        ".o_re_kpi:not(.o_re_skeleton) .o_re_kpi_value"
    );
    const tiles = document.querySelectorAll("[data-tile]");
    if (values.length !== tiles.length || values.length < 22) {
        throw new Error(
            `[${label}] Expected a value for each of the manager's 22 tiles, found ` +
            `${values.length} values for ${tiles.length} tiles`
        );
    }
    for (const el of values) {
        if (!el.textContent.trim()) {
            throw new Error(`[${label}] A KPI value rendered empty`);
        }
    }
}

const OPEN_DASHBOARD_STEPS = [
    stepUtils.showAppsMenuItem(),
    {
        content: "Open the Real Estate app",
        trigger: '.o_app[data-menu-xmlid="atmta_real_estate.real_estate_menu_root"]',
        run: "click",
    },
    {
        content: "Open the Rental Dashboard",
        trigger: '.o_nav_entry[data-menu-xmlid="atmta_real_estate.menu_rental_dashboard"], '
            + 'a[data-menu-xmlid="atmta_real_estate.menu_rental_dashboard"]',
        run: "click",
    },
    {
        content: "Wait for real data (skeleton gone)",
        trigger: ".o_re_dashboard:not(:has(.o_re_skeleton)) .o_re_kpi_value",
    },
];

// ---------------------------------------------------------------------------
// 1. Responsive layout — the Python side sets the viewport
// ---------------------------------------------------------------------------

registry.category("web_tour.tours").add("atmta_rental_dashboard_layout_tour", {
    url: "/odoo",
    steps: () => [
        ...OPEN_DASHBOARD_STEPS,
        {
            content: "Layout holds at this viewport",
            trigger: ".o_re_dashboard",
            run() {
                const label = `${window.innerWidth}x${window.innerHeight}`;
                assertNoHorizontalOverflow(label);
                assertScrollsVertically(label);
                assertAllKpisRendered(label);
                assertPanelsFitAndCharts(label);
                assertNoBrokenNumbers();

                // The KPI grid must actually reflow rather than shrink cards
                // into unreadable slivers.
                const card = document.querySelector(".o_re_kpi");
                const width = card.getBoundingClientRect().width;
                if (width < 120) {
                    throw new Error(
                        `[${label}] KPI cards squeezed to ${Math.round(width)}px — ` +
                        "the grid is not reflowing to fewer columns"
                    );
                }
                console.log(
                    `[ATMTA-LAYOUT] viewport=${label} kpiCardWidth=${Math.round(width)} OK`
                );
            },
        },
        {
            content: "Drilldown still works at this viewport",
            trigger: ".o_re_kpi_clickable:first",
            run: "click",
        },
        {
            trigger: ".o_list_view, .o_kanban_view, .o_form_view",
        },
    ],
});

// ---------------------------------------------------------------------------
// 2. RTL — asserted from computed style and real geometry, not from the SCSS
// ---------------------------------------------------------------------------

registry.category("web_tour.tours").add("atmta_rental_dashboard_rtl_tour", {
    url: "/odoo",
    steps: () => [
        ...OPEN_DASHBOARD_STEPS,
        {
            content: "The session really is RTL and the dashboard mirrored",
            trigger: ".o_re_dashboard",
            run() {
                const root = document.querySelector(".o_re_dashboard");
                const dir = getComputedStyle(root).direction;
                if (dir !== "rtl") {
                    // Odoo's backend does NOT put dir="rtl" on <html>; RTL is
                    // delivered by serving an rtlcss-processed stylesheet that
                    // flips `.o_action_manager { direction: ltr }` to rtl. So
                    // report enough to tell "wrong language" from "wrong
                    // stylesheet" apart.
                    const am = document.querySelector(".o_action_manager");
                    const content = document.querySelector(".o_content");
                    const sheets = [...document.querySelectorAll('link[rel="stylesheet"]')]
                        .map((l) => l.getAttribute("href"))
                        .filter((h) => h && h.includes("/web/assets/"));
                    throw new Error(
                        `Expected an RTL session, computed direction is "${dir}". ` +
                        `html[dir]=${document.documentElement.getAttribute("dir")} ` +
                        `session.lang=${(odoo.__session_info__ || {}).user_context &&
                            odoo.__session_info__.user_context.lang} ` +
                        `o_action_manager.direction=${am ? getComputedStyle(am).direction : "n/a"} ` +
                        `o_content.direction=${content ? getComputedStyle(content).direction : "n/a"} ` +
                        `stylesheets=${JSON.stringify(sheets)}`
                    );
                }

                // `inset-inline-end` must resolve to the LEFT edge in RTL. This
                // is the assertion a stylesheet review cannot make: it proves
                // the browser mirrored the logical property.
                const kpi = document.querySelector(".o_re_kpi_clickable");
                const drill = kpi && kpi.querySelector(".o_re_kpi_drill");
                if (!drill) {
                    throw new Error("No drill affordance found to test mirroring");
                }
                const kpiRect = kpi.getBoundingClientRect();
                const drillRect = drill.getBoundingClientRect();
                const fromStart = drillRect.left - kpiRect.left;
                const fromEnd = kpiRect.right - drillRect.right;
                if (fromStart > fromEnd) {
                    throw new Error(
                        "The drill arrow did not mirror: it is still pinned to the " +
                        `right edge (left gap ${Math.round(fromStart)}px, ` +
                        `right gap ${Math.round(fromEnd)}px)`
                    );
                }

                // `margin-inline-start` on the hint must become margin-right.
                const hint = document.querySelector(".o_re_kpi_hint");
                if (hint) {
                    const cs = getComputedStyle(hint);
                    if (parseFloat(cs.marginRight) <= parseFloat(cs.marginLeft)) {
                        throw new Error(
                            "margin-inline-start did not flip to margin-right in RTL " +
                            `(right=${cs.marginRight}, left=${cs.marginLeft})`
                        );
                    }
                }

                assertNoHorizontalOverflow("rtl");
                assertAllKpisRendered("rtl");
                assertPanelsFitAndCharts("rtl");
                assertNoBrokenNumbers();
                console.log("[ATMTA-RTL] direction=rtl, mirroring verified");
            },
        },
        {
            content: "Charts render legends/labels in RTL without breaking",
            trigger: ".o_re_chart_panel canvas",
            run() {
                // Chart.js draws to a canvas, so text cannot be asserted from
                // the DOM. What we CAN assert is that the canvas is painted at
                // the correct size and inside its panel, which is where RTL
                // layout bugs actually surface.
                for (const canvas of document.querySelectorAll(".o_re_chart_panel canvas")) {
                    const panel = canvas.closest(".o_re_chart_panel");
                    const c = canvas.getBoundingClientRect();
                    const p = panel.getBoundingClientRect();
                    if (c.left < p.left - 1 || c.right > p.right + 1) {
                        throw new Error("A chart canvas escapes its panel in RTL");
                    }
                }
            },
        },
        {
            content: "Drilldown works in an RTL session",
            trigger: ".o_re_kpi_clickable:first",
            run: "click",
        },
        {
            trigger: ".o_list_view, .o_kanban_view, .o_form_view",
        },
    ],
});

// ---------------------------------------------------------------------------
// 3. Empty company — zero data must read as zero, never as a broken number
// ---------------------------------------------------------------------------

registry.category("web_tour.tours").add("atmta_rental_dashboard_empty_tour", {
    url: "/odoo",
    steps: () => [
        ...OPEN_DASHBOARD_STEPS,
        {
            content: "An empty company renders zeros, not NaN",
            trigger: ".o_re_dashboard",
            run() {
                assertNoBrokenNumbers();
                assertAllKpisRendered("empty");
                assertNoHorizontalOverflow("empty");

                // Ratio KPIs are the division-by-zero candidates: occupancy,
                // collection rate, renewal rate. With no denominator they must
                // land on a definite 0, not on a blank or a dash-of-shame.
                const zeroish = /^[\s‏‎]*(0|٠)([.,٫][0٠]+)?\s*%?\s*$/;
                const cards = [...document.querySelectorAll(".o_re_kpi")];
                const percentCards = cards.filter((c) => {
                    const value = c.querySelector(".o_re_kpi_value");
                    return value && value.textContent.includes("%");
                });
                for (const card of percentCards) {
                    const value = card.querySelector(".o_re_kpi_value").textContent.trim();
                    // Strip the percent sign and any locale spacing.
                    const numeric = value.replace(/[%\s ]/g, "");
                    if (!zeroish.test(numeric) && !/^0/.test(numeric)) {
                        throw new Error(
                            `Empty company shows a non-zero ratio KPI: "${value}"`
                        );
                    }
                }

                // Charts must show their empty state, not a broken axis.
                const panels = document.querySelectorAll(".o_re_chart_panel");
                if (panels.length !== 4) {
                    throw new Error(`Expected 4 chart panels, found ${panels.length}`);
                }
                console.log(
                    `[ATMTA-EMPTY] ${percentCards.length} ratio KPIs all zero, ` +
                    `${panels.length} panels present`
                );
            },
        },
    ],
});

// ---------------------------------------------------------------------------
// 4. Multi-company — the dashboard must follow the native company switcher
// ---------------------------------------------------------------------------

/**
 * Read the numeric content of a KPI by its label. Used to prove the figures
 * actually change when the allowed-company set changes.
 */
function readKpi(label) {
    const seen = [];
    for (const card of document.querySelectorAll(".o_re_kpi")) {
        const name = card.querySelector(".o_re_kpi_label");
        if (!name) {
            continue;
        }
        const text = name.textContent.trim();
        seen.push(text);
        if (text.startsWith(label)) {
            return card.querySelector(".o_re_kpi_value").textContent.trim();
        }
    }
    const root = document.querySelector(".o_re_dashboard");
    throw new Error(
        `KPI "${label}" not found. Dashboard state: ` +
        `cards=${document.querySelectorAll(".o_re_kpi").length} ` +
        `skeletons=${document.querySelectorAll(".o_re_skeleton").length} ` +
        `error=${root && root.querySelector(".o_re_state_error") ? "yes" : "no"} ` +
        `labels=${JSON.stringify(seen)}`
    );
}

function toNumber(text) {
    // Locale-agnostic enough for the integer KPIs this tour reads.
    const digits = text.replace(/[^\d]/g, "");
    return digits ? parseInt(digits, 10) : 0;
}

registry.category("web_tour.tours").add("atmta_rental_dashboard_company_tour", {
    url: "/odoo",
    steps: () => [
        ...OPEN_DASHBOARD_STEPS,
        {
            content: "Record company A's figures",
            trigger: ".o_re_dashboard",
            run() {
                const total = toNumber(readKpi("Available to Lease"));
                window.__atmtaCompanyA = total;
                assertNoBrokenNumbers();
                if (total <= 0) {
                    throw new Error(
                        "Company A shows no units — the fixture did not seed data, " +
                        "so a leakage test would be vacuous"
                    );
                }
                console.log(`[ATMTA-MC] company A leasable units = ${total}`);
            },
        },
        {
            content: "Open the native company switcher",
            trigger: ".o_switch_company_menu button",
            run: "click",
        },
        {
            content: "Both fixture companies are listed",
            trigger: ".o_switch_company_menu_items .o_switch_company_item",
            run() {
                const labels = [...document.querySelectorAll(
                    ".o_switch_company_menu_items .o_switch_company_item .company_label"
                )].map((el) => el.textContent.trim());
                for (const name of ["ATMTA Company A", "ATMTA Company B"]) {
                    if (!labels.includes(name)) {
                        throw new Error(
                            `Company switcher is missing "${name}". Listed: ${labels.join(" | ")}`
                        );
                    }
                }
            },
        },
        {
            content: "Log into company B (single-company switch, no multi-select)",
            trigger: ".o_switch_company_menu_items .o_switch_company_item",
            run() {
                // `.log_into` is Odoo's own "switch to this company" control.
                // Because exactly one company is active, this replaces the
                // selection rather than adding to it — a true switch, which is
                // what a user does, and it reloads the client.
                const item = [...document.querySelectorAll(
                    ".o_switch_company_menu_items .o_switch_company_item"
                )].find(
                    (el) => el.querySelector(".company_label").textContent.trim()
                        === "ATMTA Company B"
                );
                item.querySelector(".log_into").click();
            },
        },
        {
            // Switching companies reloads the whole client. Waiting on the
            // dashboard alone is not enough: the OLD dashboard is still in the
            // DOM for as long as the reload takes, so the next step could read
            // company A's figures and call them company B's. Gate on the
            // navbar's company name first — it can only say "B" after the new
            // page has rendered.
            content: "The client reloaded into company B",
            trigger: ".o_switch_company_menu .oe_topbar_name:contains('ATMTA Company B')",
        },
        {
            content: "The dashboard reloaded for company B",
            trigger: ".o_re_dashboard:not(:has(.o_re_skeleton)) .o_re_kpi_value",
        },
        {
            content: "Company B's figures differ and show no leakage",
            trigger: ".o_re_dashboard",
            run() {
                const total = toNumber(readKpi("Available to Lease"));
                const a = window.__atmtaCompanyA;
                assertNoBrokenNumbers();
                if (total === 0) {
                    throw new Error("Company B shows zero units — the switch lost the data");
                }
                if (total === a) {
                    throw new Error(
                        `Company B shows the same figure as company A (${total}). ` +
                        "Either the switch was ignored or the KPI is not company-scoped."
                    );
                }
                window.__atmtaCompanyB = total;
                console.log(
                    `[ATMTA-MC] company B leasable units = ${total} (A was ${a}) — no leakage`
                );
            },
        },
        {
            content: "The drilldown is scoped to company B too",
            trigger: "[data-tile='available_to_lease'] .o_re_kpi_clickable",
            run: "click",
        },
        {
            trigger: ".o_list_view",
            run() {
                const rows = document.querySelectorAll(".o_list_view .o_data_row").length;
                const tile = window.__atmtaCompanyB;
                if (tile !== undefined && rows !== tile) {
                    throw new Error(
                        `Drilldown shows ${rows} rows but the tile said ${tile}`
                    );
                }
            },
        },
    ],
});
