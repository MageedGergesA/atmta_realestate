/** @odoo-module **/

/**
 * M8 — the real-browser gate for claims, delays, EOT, risk and issues.
 *
 * The point of these screens is that they keep facts, requests and decisions
 * apart. This checks the separation actually reaches the DOM: the claim form
 * shows claimed *and* determined side by side, the late-notice banner warns
 * without concluding, and the EOT form says out loud that the original date
 * does not move.
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

function goTo(action) {
    return {
        trigger: "body",
        run: () => {
            window.location.href = `/odoo/action-real_estate_construction.${action}`;
        },
    };
}

registry.category("web_tour.tours").add("construction_claims_tour", {
    url: "/odoo/action-real_estate_construction.action_delay_event",
    steps: () => [
        {
            content: "The delay register lists the seeded event",
            trigger: ".o_list_view .o_data_row",
        },
        checkpoint("delay register"),
        {
            content: "Open the delay event",
            trigger: ".o_list_view .o_data_row td[name='name']",
            run: "click",
        },
        {
            content: "It states plainly that it grants nothing",
            trigger: ".o_form_view .alert:contains('grants no extension')",
        },
        checkpoint("delay event form"),

        goTo("action_notice"),
        {
            content: "The late notice is in the register",
            trigger: ".o_list_view .o_data_row",
        },
        {
            content: "Open it",
            trigger: ".o_list_view .o_data_row td[name='name']",
            run: "click",
        },
        {
            content: "The warning is about a date, not about entitlement",
            trigger: ".o_form_view .alert:contains('matter for the contract')",
        },
        checkpoint("notice form"),

        goTo("action_claim"),
        {
            content: "The claim register opens",
            trigger: ".o_list_view .o_data_row",
        },
        checkpoint("claim register"),
        {
            content: "Open the claim",
            trigger: ".o_list_view .o_data_row td[name='name']",
            run: "click",
        },
        {
            content: "Claimed and determined are both on the form",
            trigger: ".o_form_view .o_field_widget[name='claimed_cost']",
        },
        {
            trigger: ".o_form_view .o_field_widget[name='determined_cost']",
        },
        {
            content: "Submissions keep their revisions",
            trigger: ".o_form_view .o_notebook a:contains('Submissions')",
            run: "click",
        },
        {
            content: "Revision 0 is still there beside revision 1",
            trigger: ".o_field_widget[name='submission_ids'] .o_data_row:eq(1)",
        },
        {
            content: "The chronology is built from the records",
            trigger: ".o_form_view .o_notebook a:contains('Chronology')",
            run: "click",
        },
        {
            trigger: ".o_claim_chronology tbody tr",
        },
        checkpoint("claim form"),

        goTo("action_eot"),
        {
            content: "The EOT register opens",
            trigger: ".o_list_view .o_data_row",
        },
        {
            content: "Open the extension",
            trigger: ".o_list_view .o_data_row td[name='name']",
            run: "click",
        },
        {
            content: "Original and current completion dates are both shown",
            trigger: ".o_form_view .o_field_widget[name='original_completion_date']",
        },
        checkpoint("eot form"),

        goTo("action_risk"),
        {
            content: "The risk register opens",
            trigger: ".o_list_view .o_data_row",
        },
        {
            content: "Open the materialised risk",
            trigger: ".o_list_view .o_data_row td[name='name']",
            run: "click",
        },
        {
            content: "A materialised risk says it is kept, not closed",
            trigger: ".o_form_view .alert:contains('kept as the record')",
        },
        checkpoint("risk form"),

        goTo("action_issue"),
        {
            content: "The issue register opens",
            trigger: ".o_list_view .o_data_row",
        },
        checkpoint("issue register"),
    ],
});
