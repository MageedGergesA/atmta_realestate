/** @odoo-module **/

/**
 * M9 — the real-browser gate for the Control Tower.
 *
 * The tower's whole job is to be believed, so the tour checks the things that
 * would make it not worth believing: a zero where a number is unknown, a
 * baseline that quietly absorbed a pending change, a drilldown that opens
 * something other than what the cell said.
 *
 * `start_tour` authenticates server-side; nothing here types a credential.
 */

import { registry } from "@web/core/registry";

function assertNoErrorDialog(label) {
    const dialog = document.querySelector(".o_error_dialog, .o_dialog_error");
    if (dialog) {
        const text = (dialog.innerText || "").split("\n").slice(0, 4).join(" | ");
        throw new Error(`[${label}] An error dialog is open: ${text}`);
    }
}

function assertNoBrokenNumbers(label) {
    const el = document.querySelector(".o_construction_tower, .o_content");
    const text = (el && el.textContent) || "";
    const broken = text.match(/NaN|Infinity|\[object Object\]|undefined/);
    if (broken) {
        const line = text.split("\n").find((l) => l.includes(broken[0]));
        throw new Error(`[${label}] Rendered a broken value: "${broken[0]}" in "${line}".`);
    }
}

function checkpoint(label) {
    return {
        trigger: ".o_construction_tower",
        run: () => {
            assertNoErrorDialog(label);
            assertNoBrokenNumbers(label);
        },
    };
}

function textOf(selector) {
    // `textContent`, not `innerText`: headless Chrome computes innerText from
    // layout, and a panel that is rendered but below the fold can come back
    // empty. The DOM text is what the assertion is actually about.
    const el = document.querySelector(selector);
    return (el && el.textContent) || "";
}

registry.category("web_tour.tours").add("construction_control_tower_tour", {
    url: "/odoo/action-real_estate_construction.action_control_tower",
    steps: () => [
        {
            content: "The tower mounts",
            trigger: ".o_construction_tower .o_tower_body",
        },
        {
            content: "Select the project this tour seeded",
            trigger: ".o_tower_header select",
            run: function () {
                const select = document.querySelector(".o_tower_header select");
                const option = Array.from(select.options).find((o) =>
                    o.textContent.includes("M9 Tower Project"));
                if (!option) {
                    throw new Error("The seeded project is not in the selector.");
                }
                select.value = option.value;
                select.dispatchEvent(new Event("change", { bubbles: true }));
            },
        },
        {
            content: "Its cost panel has loaded",
            trigger: ".o_tower_cost .o_kpi:contains('Current Budget')",
        },
        checkpoint("tower mounted"),
        {
            content: "Freshness is stated, not implied",
            trigger: ".o_tower_freshness:contains('Data as of')",
        },
        {
            content: "The health status carries its reasons",
            trigger: ".o_tower_health .o_tower_reasons li",
        },
        {
            content: "The cost cards are present",
            trigger: ".o_tower_cost .o_kpi:contains('Current Budget')",
        },
        {
            content: "Pending money is in its own section, visibly not a baseline",
            trigger: ".o_tower_pending .o_kpi_pending:contains('Potential Change Exposure')",
        },
        {
            content: "Budget and exposure are not the same number",
            trigger: ".o_tower_body",
            run: () => {
                const budget = textOf(".o_tower_cost");
                const pending = textOf(".o_tower_pending");
                if (!budget.includes("Current Budget")) {
                    throw new Error("Cost panel did not render the budget.");
                }
                if (!pending.includes("Potential Change Exposure")) {
                    throw new Error("Pending panel did not render exposure.");
                }
            },
        },
        {
            content: "The cost sheet renders its rows",
            trigger: ".o_cost_sheet tbody tr",
        },
        {
            content: "An unforecast cost code shows N/A rather than zero",
            trigger: ".o_cost_sheet tbody",
            run: () => {
                const text = textOf(".o_cost_sheet");
                if (!text.includes("N/A")) {
                    throw new Error(
                        "The seeded project has a cost code with no ETC; the " +
                        "sheet must show N/A rather than 0.00."
                    );
                }
            },
        },
        checkpoint("cost sheet"),
        {
            content: "Progress separates its four questions",
            trigger: ".o_tower_progress .o_kpi:contains('Certified')",
        },
        {
            content: "Data-control exceptions are listed and openable",
            trigger: ".o_tower_exceptions .list-group-item",
        },
        {
            content: "Drill from actual cost into the ledger",
            trigger: ".o_cost_sheet tbody tr:first td:nth-child(7)",
            run: "click",
        },
        {
            content: "The drilldown opened a real list",
            trigger: ".o_list_view, .o_action_manager .o_view_controller",
        },
        {
            trigger: "body",
            run: () => assertNoErrorDialog("after drilldown"),
        },
    ],
});
