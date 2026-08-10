/** @odoo-module **/

/**
 * M6 — the real-browser gate for the quality and site records.
 *
 * The server tests prove the rules. This proves the screens a site engineer
 * actually uses open, render the record, and expose the buttons those rules
 * are reached through — a form that raises on load enforces nothing.
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
    const el = document.querySelector(".o_content");
    const text = (el && el.innerText) || "";
    const broken = text.match(/NaN|\[object Object\]/);
    if (broken) {
        const line = text.split("\n").find((l) => l.includes(broken[0]));
        throw new Error(`[${label}] Rendered a broken value: "${broken[0]}" in "${line}".`);
    }
}

function checkpoint(label) {
    return {
        trigger: ".o_content",
        run: () => {
            assertNoErrorDialog(label);
            assertNoBrokenNumbers(label);
        },
    };
}

/** Open a menu by its visible label, from anywhere in the backend. */
function openMenu(label) {
    return [
        {
            trigger: `.o_menu_sections a:contains("${label}"), .dropdown-item:contains("${label}")`,
            run: "click",
        },
    ];
}

registry.category("web_tour.tours").add("construction_quality_tour", {
    url: "/odoo/action-real_estate_construction.action_itp",
    steps: () => [
        {
            content: "The ITP register lists the plan seeded for this tour",
            trigger: ".o_list_view td:contains('Tour ITP')",
        },
        checkpoint("itp list"),
        {
            content: "Open the plan",
            trigger: ".o_list_view td:contains('Tour ITP')",
            run: "click",
        },
        {
            content: "The checkpoints are on the form",
            trigger: ".o_form_view .o_field_widget[name='item_ids'] td:contains('Concrete pour')",
        },
        {
            content: "An active plan says so, and offers a revision rather than an edit",
            trigger: ".o_form_view button[name='action_create_revision']",
        },
        checkpoint("itp form"),

        {
            trigger: "nav .o_menu_brand, .o_navbar_apps_menu",
            run: () => {},
        },
        {
            content: "Back out to the inspection register",
            trigger: "body",
            run: () => {
                window.location.href =
                    "/odoo/action-real_estate_construction.action_inspection";
            },
        },
        {
            content: "The inspection is listed with its result",
            trigger: ".o_list_view td:contains('Rejected')",
        },
        checkpoint("inspection list"),
        {
            content: "Open the inspection",
            trigger: ".o_list_view td:contains('Rejected')",
            run: "click",
        },
        {
            content: "The checklist came across from the plan",
            trigger: ".o_form_view .o_field_widget[name='checklist_line_ids'] td:contains('Slump')",
        },
        {
            content: "A recorded inspection offers a reinspection, not a rewrite",
            trigger: ".o_form_view button[name='action_create_reinspection']",
        },
        checkpoint("inspection form"),

        {
            content: "Go to the NCR register",
            trigger: "body",
            run: () => {
                window.location.href =
                    "/odoo/action-real_estate_construction.action_ncr";
            },
        },
        {
            content: "The NCR is listed",
            trigger: ".o_list_view td:contains('Tour NCR')",
        },
        checkpoint("ncr list"),
        {
            content: "Open it",
            trigger: ".o_list_view td:contains('Tour NCR')",
            run: "click",
        },
        {
            content: "Rework cost is presented as exposure, not as cost",
            trigger: ".o_form_view .alert:contains('quality exposure')",
        },
        checkpoint("ncr form"),

        {
            content: "Go to the daily site reports",
            trigger: "body",
            run: () => {
                window.location.href =
                    "/odoo/action-real_estate_construction.action_daily_report";
            },
        },
        {
            content: "The report is listed",
            trigger: ".o_list_view .o_data_row",
        },
        checkpoint("daily report list"),
        {
            content: "Open it",
            trigger: ".o_list_view .o_data_row td[name='report_date']",
            run: "click",
        },
        {
            content: "Manpower is the labour logs, not a second table",
            trigger: ".o_form_view .o_notebook a:contains('Manpower')",
            run: "click",
        },
        {
            content: "The tour's labour log is shown on the report",
            trigger: ".o_field_widget[name='labor_log_ids'] td:contains('40')",
        },
        checkpoint("daily report form"),
    ],
});
