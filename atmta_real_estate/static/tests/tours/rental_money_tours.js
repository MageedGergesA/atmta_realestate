/** @odoo-module **/

import { registry } from "@web/core/registry";

/**
 * The money side of a live lease, clicked through the web client:
 * a security deposit requested, received and refunded, and a rent amendment
 * proposed, approved, signed and applied. Driven by
 * `tests/test_money_cycle_browser.py`, which checks the server between tours.
 */
const tours = registry.category("web_tour.tours");
const ACTIONS = ".o_form_statusbar .o_statusbar_buttons";

const formReady = { content: "The form is loaded", trigger: ".o_form_view .o_form_statusbar" };

/**
 * Click a header button and wait for its action. ``scope`` is the form: the
 * page's form by default, or ``.modal .o_form_view`` for a form opened in a
 * dialog -- otherwise the wait would look at the form behind the dialog.
 */
function clickHeader(name, content, scope = ".o_form_view") {
    return [
        {
            content,
            trigger: `${scope} ${ACTIONS} button[name='${name}']:not([disabled])`,
            run: "click",
        },
        {
            content: `${content}: done`,
            trigger: `${scope}:not(:has(${ACTIONS} button[name='${name}'])):not(:has(${ACTIONS} button[disabled]))`,
        },
    ];
}

function clickHeaderConfirmed(name, content, scope = ".o_form_view") {
    const [click, done] = clickHeader(name, content, scope);
    return [
        click,
        { content: `${content}: confirm`, trigger: ".modal .modal-footer button.btn-primary", run: "click" },
        done,
    ];
}

tours.add("atmta_money_1_agent_request_deposit", {
    steps: () => [
        formReady,
        {
            content: "Open the Deposit & Occupancy tab",
            trigger: ".o_form_view .o_notebook .nav-link:contains('Occupancy')",
            run: "click",
        },
        {
            content: "Create a security deposit for the lease",
            trigger: ".o_form_view .o_notebook button[name='action_create_deposit']",
            run: "click",
        },
        {
            content: "The deposit form opens in a dialog",
            trigger: `.modal .o_form_view ${ACTIONS} button[name='action_request']`,
        },
        {
            content: "Set the amount",
            trigger: ".modal .o_form_view .o_field_widget[name='requested_amount'] input",
            run: "edit 2000",
        },
        {
            // A dialog form closes once its button's action has run.
            content: "Request the deposit",
            trigger: `.modal .o_form_view ${ACTIONS} button[name='action_request']:not([disabled])`,
            run: "click",
        },
        {
            content: "The dialog closed and the lease lists the requested deposit",
            trigger: "body:not(:has(.modal)) .o_form_view div[name='deposit_record_ids'] .o_data_row:contains('Requested')",
        },
    ],
});

tours.add("atmta_money_2_manager_receive_refund", {
    steps: () => [
        formReady,
        ...clickHeader("action_register_receipt", "Register the receipt"),
        ...clickHeaderConfirmed("action_refund", "Refund the deposit"),
    ],
});

tours.add("atmta_money_3_manager_amend_rent", {
    steps: () => [
        formReady,
        {
            content: "Open the gear menu",
            trigger: ".o_control_panel .o_cp_action_menus button:has(.fa-cog)",
            run: "click",
        },
        {
            content: "Choose Amend Lease",
            trigger: ".o-dropdown--menu .o-dropdown-item:contains('Amend Lease')",
            run: "click",
        },
        {
            content: "The amendment form opens",
            trigger: `.o_form_view ${ACTIONS} button[name='action_propose']`,
        },
        {
            content: "Type the new rent",
            trigger: ".o_form_view .o_field_widget[name='new_rent'] input",
            run: "edit 1100",
        },
        {
            content: "Give the reason",
            trigger: ".o_form_view .o_field_widget[name='reason'] textarea",
            run: "edit Mid-term rent review",
        },
        ...clickHeader("action_propose", "Propose the amendment"),
        ...clickHeader("action_approve", "Approve the amendment"),
        ...clickHeader("action_sign", "Mark the amendment signed"),
        ...clickHeaderConfirmed("action_apply", "Apply the amendment"),
    ],
});
