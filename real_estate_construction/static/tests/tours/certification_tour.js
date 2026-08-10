/** @odoo-module **/

/**
 * M7 — the real-browser gate for certification.
 *
 * The rules live in the server tests. This checks that the screens those rules
 * are reached through actually open, and that the two numbers M7 exists to
 * keep apart — applied for, and certified — are both on the form where a
 * quantity surveyor can see them.
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

registry.category("web_tour.tours").add("construction_certification_tour", {
    url: "/odoo/action-real_estate_construction.action_payment_certificate",
    steps: () => [
        {
            content: "The certificate register lists the seeded certificate",
            trigger: ".o_list_view .o_data_row",
        },
        checkpoint("certificate list"),
        {
            content: "Open it",
            trigger: ".o_list_view .o_data_row td[name='name']",
            run: "click",
        },
        {
            content: "Applied for and certified are both shown",
            trigger: ".o_form_view .o_field_widget[name='applied_amount']",
        },
        {
            content: "...and so is the difference between them",
            trigger: ".o_form_view .o_field_widget[name='disallowed_amount']",
        },
        {
            content: "Retention is presented as an amount withheld",
            trigger: ".o_form_view .o_field_widget[name='retention_amount']",
        },
        checkpoint("certificate form"),

        goTo("action_retention"),
        {
            content: "The retention register shows the movement",
            trigger: ".o_list_view .o_data_row",
        },
        checkpoint("retention register"),

        goTo("action_advance"),
        {
            content: "The advance register opens",
            trigger: ".o_list_view .o_data_row",
        },
        {
            content: "Open the advance",
            trigger: ".o_list_view .o_data_row td[name='name']",
            run: "click",
        },
        {
            content: "An advance says plainly that it is not cost",
            trigger: ".o_form_view .alert:contains('not project cost')",
        },
        {
            content: "Outstanding is on the form",
            trigger: ".o_form_view .o_field_widget[name='outstanding_amount']",
        },
        checkpoint("advance form"),

        goTo("action_retention_release"),
        {
            content: "The release register opens",
            trigger: ".o_list_view",
        },
        checkpoint("release list"),
    ],
});
