/** @odoo-module **/

import { registry } from "@web/core/registry";

/**
 * Brokerage 0.3 — the flows a code review cannot vouch for.
 *
 * Every assertion below runs against a live DOM in a real (headless) Chrome.
 * The Python side (tests/test_browser.py) supplies the fixtures and varies the
 * user, because the interesting failures in this module are role-dependent: a
 * field behind `groups=` that a manager sees and an agent does not, referenced
 * from an `invisible=` expression, loads fine at install and throws when the
 * agent opens the form.
 *
 * None of these type credentials — `start_tour` authenticates server-side.
 */

// ---------------------------------------------------------------------------
// Shared assertions
// ---------------------------------------------------------------------------

/** Nothing the UI prints may be a broken number. */
function assertNoBrokenNumbers(scope) {
    const el = document.querySelector(scope || ".o_content");
    const text = (el && el.innerText) || "";
    const broken = text.match(/NaN|undefined|Infinity|\[object Object\]/);
    if (broken) {
        const line = text.split("\n").find((l) => l.includes(broken[0]));
        throw new Error(
            `Rendered a broken value: "${broken[0]}" in line "${line}".`
        );
    }
}

/** The page must never scroll sideways. */
function assertNoHorizontalOverflow(label) {
    const doc = document.documentElement;
    if (doc.scrollWidth > doc.clientWidth + 1) {
        throw new Error(
            `[${label}] Page overflows horizontally: ` +
            `scrollWidth=${doc.scrollWidth} clientWidth=${doc.clientWidth}.`
        );
    }
}

/** A field is absent from the DOM, not merely hidden. */
function assertFieldAbsent(name, why) {
    const el = document.querySelector(`.o_field_widget[name='${name}']`);
    if (el) {
        throw new Error(
            `Field "${name}" is on screen and should not be: ${why}`
        );
    }
}

function assertFieldPresent(name) {
    const el = document.querySelector(`.o_field_widget[name='${name}']`);
    if (!el) {
        throw new Error(`Field "${name}" is missing from the form.`);
    }
    return el;
}

function textOf(selector) {
    const el = document.querySelector(selector);
    return el ? (el.innerText || "").trim() : "";
}

/**
 * The rendered value of a field.
 *
 * In edit mode a monetary or char field renders as an `<input>`, whose
 * `innerText` is the empty string however full the field is. Reading only the
 * text makes an assertion that looks strict and passes on nothing.
 */
function fieldValue(name) {
    const el = document.querySelector(`.o_field_widget[name='${name}']`);
    if (!el) {
        return null;
    }
    const input = el.querySelector("input, textarea");
    if (input) {
        return (input.value || "").trim();
    }
    return (el.innerText || "").trim();
}

/**
 * Switch a kanban-first action to its list.
 *
 * Triggering on the switcher itself rather than on `body`: `body` matches
 * before the view has rendered, so the click found nothing and the tour went
 * on to look for rows on a kanban that has none.
 */
const SWITCH_TO_LIST = {
    content: "Switch to the list view",
    trigger: ".o_switch_view.o_list",
    run: "click",
};

// ---------------------------------------------------------------------------
// 1. The requirements brief and the matching engine
// ---------------------------------------------------------------------------
registry.category("web_tour.tours").add("re_brokerage_matching_tour", {
    // No `url`: the Python side passes the record's own URL to `start_tour`,
    // which overrides it. Clicking through a list first tested the list.
    steps: () => [
        {
            content: "The Requirements page exists on a real-estate lead",
            trigger: ".o_form_view .o_notebook",
            run: () => {
                const links = [...document.querySelectorAll(
                    ".o_notebook .nav-link"
                )];
                const tabs = links.map(
                    (a) => `${a.getAttribute("name")}:${a.innerText.trim()}`
                );
                const tab = links.find(
                    (a) => a.getAttribute("name") === "realestate"
                );
                if (!tab) {
                    throw new Error(
                        "No Requirements tab on the opportunity form. " +
                        `Tabs present: ${tabs.join(" | ") || "(none)"}`
                    );
                }
                tab.click();
            },
        },
        {
            content: "The brief renders with real values, not placeholders",
            trigger: ".o_field_widget[name='re_budget_max']",
            run: () => {
                assertFieldPresent("re_intent");
                assertFieldPresent("re_budget_min");
                assertFieldPresent("re_bedrooms_min");
                if (!fieldValue("re_budget_max")) {
                    throw new Error("The budget ceiling rendered empty.");
                }
                assertNoBrokenNumbers(".o_form_view");
                assertNoHorizontalOverflow("crm lead requirements");
            },
        },
        {
            content: "Open the Matches page",
            trigger: ".o_notebook .nav-link[name='re_matches']",
            run: "click",
        },
        {
            content: "Run the matching engine from the UI",
            trigger: "button[name='action_run_matching']:not(.oe_stat_button)",
            run: "click",
        },
        {
            content: "Matches came back, scored",
            trigger: ".o_list_view .o_data_row",
            run: () => {
                const rows = document.querySelectorAll(
                    ".o_list_view .o_data_row"
                );
                if (!rows.length) {
                    throw new Error("Matching produced no rows in the UI.");
                }
                if (!document.querySelector(".o_list_view .o_progressbar")) {
                    throw new Error(
                        "No score rendered — an agent cannot see how good a " +
                        "match is."
                    );
                }
                assertNoBrokenNumbers();
                assertNoHorizontalOverflow("match results");
            },
        },
    ],
});

// ---------------------------------------------------------------------------
// 2. A match explains itself
// ---------------------------------------------------------------------------
registry.category("web_tour.tours").add("re_brokerage_explain_tour", {
    steps: () => [
        {
            content: "The explanation is readable prose, not a JSON dump",
            trigger: ".o_field_widget[name='explanation']",
            run: () => {
                const text = fieldValue("explanation");
                if (!text) {
                    throw new Error(
                        "A match with no explanation is a black box."
                    );
                }
                if (text.includes("{") || text.includes("'verdict'")) {
                    throw new Error(
                        `The explanation leaked its raw structure: "${text}"`
                    );
                }
                if (!/[\u2713\u25b3\u2717]/.test(text)) {
                    throw new Error(
                        `No per-criterion verdict rendered: "${text}"`
                    );
                }
                assertNoHorizontalOverflow("match explanation");
            },
        },
    ],
});

// ---------------------------------------------------------------------------
// 3. The confidential floor — what an agent must not see
// ---------------------------------------------------------------------------
registry.category("web_tour.tours").add("re_brokerage_floor_hidden_tour", {
    url: "/odoo/action-real_estate_brokerage.action_realestate_listing",
    steps: () => [
        SWITCH_TO_LIST,
        {
            content: "Open the seeded listing",
            trigger: ".o_data_row .o_data_cell",
            run: "click",
        },
        {
            content: "The owner's floor is not on an agent's screen",
            trigger: ".o_field_widget[name='list_price']",
            run: () => {
                assertFieldAbsent(
                    "minimum_price",
                    "it is the seller's negotiating floor and an agent who " +
                    "knows it can give the whole margin away."
                );
                assertNoHorizontalOverflow("listing form as agent");
            },
        },
    ],
});

registry.category("web_tour.tours").add("re_brokerage_floor_visible_tour", {
    url: "/odoo/action-real_estate_brokerage.action_realestate_listing",
    steps: () => [
        SWITCH_TO_LIST,
        {
            trigger: ".o_data_row .o_data_cell",
            run: "click",
        },
        {
            content: "A manager does see it",
            trigger: ".o_field_widget[name='minimum_price']",
            run: () => {
                assertFieldPresent("minimum_price");
                assertNoBrokenNumbers(".o_form_view");
            },
        },
    ],
});

// ---------------------------------------------------------------------------
// 4. Negotiation history survives to the screen
// ---------------------------------------------------------------------------
registry.category("web_tour.tours").add("re_brokerage_negotiation_tour", {
    url: "/odoo/action-real_estate_brokerage.action_realestate_offer",
    steps: () => [
        {
            trigger: ".o_data_row .o_data_cell",
            run: "click",
        },
        {
            trigger: ".o_notebook .nav-link:contains('Negotiation History')",
            run: "click",
        },
        {
            content: "Every round of the negotiation is on screen",
            trigger: ".o_field_widget[name='revision_ids'] .o_data_row",
            run: () => {
                const rows = document.querySelectorAll(
                    ".o_field_widget[name='revision_ids'] .o_data_row"
                );
                if (rows.length < 3) {
                    throw new Error(
                        `Only ${rows.length} revisions rendered; the fixture ` +
                        "negotiated three times. 0.1 would have shown one row."
                    );
                }
                assertNoBrokenNumbers(".o_form_view");
                assertNoHorizontalOverflow("negotiation history");
            },
        },
    ],
});

// ---------------------------------------------------------------------------
// 5. A duplicate registration refuses without naming the holder
// ---------------------------------------------------------------------------
registry.category("web_tour.tours").add("re_brokerage_registration_tour", {
    url: "/odoo/action-real_estate_brokerage.action_lead_registration",
    steps: () => [
        {
            content: "Open the rejected registration",
            trigger: ".o_data_row:contains('Rejected') .o_data_cell",
            run: "click",
        },
        {
            content: "The refusal is visible and names nobody",
            trigger: ".alert",
            run: () => {
                const page = textOf(".o_form_view");
                if (!/already registered/i.test(page)) {
                    throw new Error(
                        "The broker is not told their claim was refused."
                    );
                }
                if (/Holding Broker/.test(page)) {
                    throw new Error(
                        "The competing broker's name is on screen. That is a " +
                        "competitor's client list, leaked one name at a time."
                    );
                }
                assertNoHorizontalOverflow("lead registration");
            },
        },
    ],
});

// ---------------------------------------------------------------------------
// 6. The commission gross, and the over-allocation warning
// ---------------------------------------------------------------------------
registry.category("web_tour.tours").add("re_brokerage_commission_tour", {
    url: "/odoo/action-real_estate_brokerage.action_realestate_transaction",
    steps: () => [
        {
            trigger: ".o_data_row .o_data_cell",
            run: "click",
        },
        {
            content: "The gross and what is left of it are both shown",
            trigger: ".o_field_widget[name='commission_gross_amount']",
            run: () => {
                assertFieldPresent("commission_gross_amount");
                assertFieldPresent("commission_allocated");
                assertFieldPresent("commission_unallocated");
                const gross = fieldValue("commission_gross_amount");
                if (!gross || parseFloat(gross.replace(/[^0-9.-]/g, "")) === 0) {
                    throw new Error(
                        `Gross commission rendered as "${gross}" — every split ` +
                        "is measured against this number."
                    );
                }
                assertNoBrokenNumbers(".o_form_view");
                assertNoHorizontalOverflow("transaction commissions");
            },
        },
    ],
});

// ---------------------------------------------------------------------------
// 7. The Developer boundary, stated on screen
// ---------------------------------------------------------------------------
registry.category("web_tour.tours").add("re_brokerage_developer_notice_tour", {
    url: "/odoo/action-real_estate_brokerage.action_realestate_transaction",
    steps: () => [
        {
            trigger: ".o_data_row .o_data_cell",
            run: "click",
        },
        {
            content: "An internal deal says whose the unit is",
            trigger: ".alert-info",
            run: () => {
                const text = textOf(".o_form_view");
                if (!/Developer/.test(text)) {
                    throw new Error(
                        "Nothing on screen tells the user that this unit's " +
                        "availability and ownership are the Developer " +
                        "module's, not this transaction's."
                    );
                }
            },
        },
    ],
});

// ---------------------------------------------------------------------------
// 8. Every action the menus point at opens, with nothing broken on it
//
// Reached by URL rather than by clicking the navbar: Odoo collapses menu
// items into an overflow dropdown at narrow viewports, so a click-through
// tour tests the navbar's responsiveness rather than the pages.
// ---------------------------------------------------------------------------
registry.category("web_tour.tours").add("re_brokerage_menus_tour", {
    url: "/odoo/action-real_estate_brokerage.action_crm_lead_realestate",
    steps: () => [
        {
            content: "Opportunities opens",
            trigger: ".o_list_view, .o_kanban_view",
            run: () => {
                assertNoBrokenNumbers();
                assertNoHorizontalOverflow("opportunities");
            },
        },
    ],
});

registry.category("web_tour.tours").add("re_brokerage_matches_menu_tour", {
    url: "/odoo/action-real_estate_brokerage.action_property_match",
    steps: () => [
        {
            trigger: ".o_list_view",
            run: () => {
                assertNoBrokenNumbers();
                assertNoHorizontalOverflow("matches");
            },
        },
    ],
});

registry.category("web_tour.tours").add("re_brokerage_mandates_menu_tour", {
    url: "/odoo/action-real_estate_brokerage.action_mandate",
    steps: () => [
        {
            trigger: ".o_list_view",
            run: () => {
                assertNoBrokenNumbers();
                assertNoHorizontalOverflow("mandates");
            },
        },
    ],
});

registry.category("web_tour.tours").add("re_brokerage_registrations_menu_tour", {
    url: "/odoo/action-real_estate_brokerage.action_lead_registration",
    steps: () => [
        {
            trigger: ".o_list_view",
            run: () => {
                assertNoBrokenNumbers();
                assertNoHorizontalOverflow("registrations");
            },
        },
    ],
});

registry.category("web_tour.tours").add("re_brokerage_agreements_menu_tour", {
    url: "/odoo/action-real_estate_brokerage.action_broker_agreement",
    steps: () => [
        {
            trigger: ".o_list_view",
            run: () => {
                assertNoBrokenNumbers();
                assertNoHorizontalOverflow("agreements");
            },
        },
    ],
});

registry.category("web_tour.tours").add("re_brokerage_sources_menu_tour", {
    url: "/odoo/action-real_estate_brokerage.action_source_performance",
    steps: () => [
        {
            trigger: ".o_list_view",
            run: () => {
                assertNoBrokenNumbers();
                assertNoHorizontalOverflow("source performance");
            },
        },
    ],
});

// ---------------------------------------------------------------------------
// 9. Closeout — the commission semantic migration, on screen
// ---------------------------------------------------------------------------
registry.category("web_tour.tours").add("re_brokerage_share_warning_tour", {
    steps: () => [
        {
            content: "The Real Estate Agent page carries the warning",
            trigger: ".o_notebook .nav-link[name='realestate_agent']",
            run: "click",
        },
        {
            content: "An unresolved default warns, on the record that fixes it",
            trigger: ".alert-warning",
            run: () => {
                const text = textOf(".o_form_view");
                if (!/needs review/i.test(text)) {
                    throw new Error(
                        "Nothing on the agent record says the commission " +
                        "default is unusable."
                    );
                }
                if (!/percentage of the sale price/i.test(text)) {
                    throw new Error(
                        "The warning does not say what the number used to mean."
                    );
                }
                const legacy = fieldValue("commission_share_legacy_value");
                if (!legacy || parseFloat(legacy) === 0) {
                    throw new Error(
                        `The original value is not shown (read "${legacy}"). ` +
                        "It is the evidence a payout dispute is settled from."
                    );
                }
                assertNoBrokenNumbers(".o_form_view");
                assertNoHorizontalOverflow("agent commission warning");
            },
        },
    ],
});

registry.category("web_tour.tours").add("re_brokerage_migration_log_tour", {
    url: "/odoo/action-real_estate_brokerage.action_commission_share_migration",
    steps: () => [
        {
            content: "The evidence is browsable",
            trigger: ".o_list_view",
            run: () => {
                const rows = document.querySelectorAll(".o_data_row");
                if (!rows.length) {
                    throw new Error("The migration produced no evidence rows.");
                }
                const text = textOf(".o_list_view");
                if (!/% of sale/i.test(text) || !/% of gross/i.test(text)) {
                    throw new Error(
                        "The list does not distinguish the old meaning from " +
                        "the new one, which is the only thing it is for."
                    );
                }
                assertNoBrokenNumbers();
                assertNoHorizontalOverflow("migration evidence");
            },
        },
    ],
});

// ---------------------------------------------------------------------------
// 10. The commission split and the payable, driven through the UI
// ---------------------------------------------------------------------------
registry.category("web_tour.tours").add("re_brokerage_split_tour", {
    steps: () => [
        {
            content: "The splits are on the transaction",
            trigger: ".o_field_widget[name='commission_ids'] .o_data_row",
            run: () => {
                const rows = document.querySelectorAll(
                    ".o_field_widget[name='commission_ids'] .o_data_row"
                );
                if (rows.length < 2) {
                    throw new Error(
                        `Only ${rows.length} split rendered; the fixture has two.`
                    );
                }
                const gross = fieldValue("commission_gross_amount");
                const allocated = fieldValue("commission_allocated");
                const left = fieldValue("commission_unallocated");
                for (const [label, value] of [["gross", gross],
                                              ["allocated", allocated],
                                              ["unallocated", left]]) {
                    if (value === null || value === "") {
                        throw new Error(`The ${label} figure rendered empty.`);
                    }
                }
                assertNoBrokenNumbers(".o_form_view");
                assertNoHorizontalOverflow("commission splits");
            },
        },
    ],
});

registry.category("web_tour.tours").add("re_brokerage_payable_tour", {
    steps: () => [
        {
            content: "A billed commission shows its payable",
            trigger: ".o_field_widget[name='commission_ids'] .o_data_row",
            run: () => {
                const text = textOf(".o_form_view");
                if (!/billed/i.test(text)) {
                    throw new Error(
                        "The split does not show that it has reached a payable."
                    );
                }
                assertNoBrokenNumbers(".o_form_view");
            },
        },
    ],
});

// ---------------------------------------------------------------------------
// 11. Duplicate detection, and the dashboard
// ---------------------------------------------------------------------------
registry.category("web_tour.tours").add("re_brokerage_duplicate_tour", {
    url: "/odoo/action-real_estate_brokerage.action_lead_registration",
    steps: () => [
        {
            content: "Both claims are listed, one refused",
            trigger: ".o_list_view .o_data_row",
            run: () => {
                const text = textOf(".o_list_view");
                if (!/rejected/i.test(text)) {
                    throw new Error(
                        "The duplicate claim is not shown as refused."
                    );
                }
                if (!/already registered/i.test(text)) {
                    throw new Error(
                        "The refusal reason is not visible in the register."
                    );
                }
                assertNoBrokenNumbers();
                assertNoHorizontalOverflow("registration duplicates");
            },
        },
    ],
});

registry.category("web_tour.tours").add("re_brokerage_dashboard_tour", {
    url: "/odoo/action-real_estate_brokerage.action_brokerage_dashboard",
    steps: () => [
        {
            content: "The dashboard renders without breaking",
            trigger: ".o_action_manager",
            run: () => {
                assertNoBrokenNumbers();
                assertNoHorizontalOverflow("brokerage dashboard");
            },
        },
    ],
});

// ---------------------------------------------------------------------------
// 12. RTL — asserted from computed style and real geometry, not from a class
// ---------------------------------------------------------------------------
registry.category("web_tour.tours").add("re_brokerage_rtl_tour", {
    url: "/odoo/action-real_estate_brokerage.action_realestate_listing",
    steps: () => [
        SWITCH_TO_LIST,
        {
            content: "The session really is right-to-left, and nothing spills",
            trigger: ".o_list_view",
            run: () => {
                // Odoo sets direction on `.o_action_manager`, not on
                // `<html>` — the root element carries no `dir` attribute at
                // all, so asserting there reads "ltr" in a perfectly good
                // Arabic session and proves the opposite of what it looks like.
                const scope = document.querySelector(".o_action_manager");
                if (!scope) {
                    throw new Error("No action manager on the page.");
                }
                const dir = getComputedStyle(scope).direction;
                if (dir !== "rtl") {
                    throw new Error(
                        `Expected an RTL session, got direction="${dir}" on ` +
                        ".o_action_manager. The tour would otherwise be " +
                        "asserting nothing."
                    );
                }
                assertNoBrokenNumbers();
                assertNoHorizontalOverflow("listings in RTL");
            },
        },
    ],
});
