/** @odoo-module **/

import { registry } from "@web/core/registry";

/**
 * The lease form in a real browser: "Add a line" under Rent Escalations.
 *
 * The lease this tour opens already has an escalation taking effect today. A
 * new row has no date yet, so the rent chain's sort tied it with today's rule
 * and compared two unsaved ids -- the web client showed "TypeError: '<' not
 * supported between instances of 'NewId' and 'NewId'". Any server error on the
 * way fails the tour.
 */
registry.category("web_tour.tours").add("atmta_rental_escalation_line_tour", {
    steps: () => [
        {
            content: "The lease form is open",
            trigger: ".o_form_view .o_notebook",
        },
        {
            content: "Open Charges & Adjustments",
            trigger: ".o_notebook .nav-link:contains('Charges')",
            run: "click",
        },
        {
            content: "Add a rent escalation row",
            trigger: "div[name='escalation_rule_ids'] .o_field_x2many_list_row_add a",
            run: "click",
        },
        {
            content: "The new row renders once its onchange has answered",
            trigger: "div[name='escalation_rule_ids'] .o_data_row.o_selected_row div[name='percentage'] input",
            run: "edit 2",
        },
        {
            content: "No error dialog was raised",
            trigger: ".o_form_view",
            run() {
                const dialog = document.querySelector(".o_error_dialog, .modal .o_error_detail");
                if (dialog) {
                    throw new Error(`An error dialog is open: ${dialog.innerText.slice(0, 300)}`);
                }
            },
        },
        {
            content: "Discard the unsaved row",
            trigger: ".o_form_button_cancel",
            run: "click",
        },
        {
            content: "Back to a clean form",
            trigger: ".o_form_view:not(:has(.o_form_status_indicator_buttons:not(.invisible)))",
        },
    ],
});
