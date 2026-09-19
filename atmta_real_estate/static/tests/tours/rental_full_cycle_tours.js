/** @odoo-module **/

import { registry } from "@web/core/registry";

/**
 * The whole rental cycle, clicked through the web client by the role that owns
 * each step. `tests/test_full_cycle_browser.py` opens each tour on the right
 * record as the right user and checks the server state between tours.
 */
const tours = registry.category("web_tour.tours");

const formReady = {
    content: "The form is loaded",
    trigger: ".o_form_view .o_form_statusbar",
};

/**
 * Click a header button, then wait until the action has run.
 *
 * While a button's action runs the web client disables the header buttons and
 * re-renders them, so "the button is gone" alone can be true before the server
 * has answered, and the next click lands on a form that ignores it. The click
 * waits for an enabled button; "done" also requires that no action button is
 * still disabled. Only the action buttons count: the status bar's stage arrows
 * are always rendered as disabled buttons.
 */
const ACTIONS = ".o_form_statusbar .o_statusbar_buttons";

function clickHeader(name, content) {
    return [
        {
            content,
            trigger: `.o_form_view ${ACTIONS} button[name='${name}']:not([disabled])`,
            run: "click",
        },
        {
            content: `${content}: done`,
            trigger: `.o_form_view:not(:has(${ACTIONS} button[name='${name}'])):not(:has(${ACTIONS} button[disabled]))`,
        },
    ];
}

/** Like clickHeader, for a button that asks for confirmation first (`confirm=`). */
function clickHeaderConfirmed(name, content) {
    const [click, done] = clickHeader(name, content);
    return [
        click,
        {
            content: `${content}: confirm`,
            trigger: ".modal .modal-footer button.btn-primary",
            run: "click",
        },
        done,
    ];
}

function openTab(label) {
    return {
        content: `Open the ${label} tab`,
        trigger: `.o_form_view .o_notebook .nav-link:contains('${label}')`,
        run: "click",
    };
}

function pickRecord(field, text) {
    return [
        {
            content: `Type "${text}" into ${field}`,
            trigger: `.o_form_view .o_field_widget[name='${field}'] input`,
            run: `edit ${text}`,
        },
        {
            content: `Choose "${text}"`,
            trigger: `.o-autocomplete--dropdown-item:contains('${text}')`,
            run: "click",
        },
    ];
}

// ---------------------------------------------------------------- Lease A
tours.add("atmta_cycle_1_agent_create_submit", {
    steps: () => [
        formReady,
        ...pickRecord("partner_id", "Cycle Tenant"),
        ...pickRecord("property_id", "Cycle Unit A"),
        openTab("Billing"),
        {
            content: "Type the base rent",
            trigger: ".o_form_view .o_field_widget[name='price'] input",
            run: "edit 1000",
        },
        ...clickHeader("action_submit_for_approval", "Submit the lease for approval"),
    ],
});

tours.add("atmta_cycle_2_manager_approve", {
    steps: () => [formReady, ...clickHeader("action_approve_lease", "Approve the lease")],
});

tours.add("atmta_cycle_3_agent_mark_signed", {
    steps: () => [formReady, ...clickHeader("action_mark_signed", "Mark the lease signed")],
});

tours.add("atmta_cycle_4_pm_activate_move_in", {
    steps: () => [
        formReady,
        ...clickHeader("action_activate_lease", "Activate the lease"),
        openTab("Occupancy"),
        {
            content: "Schedule the move-in",
            trigger: ".o_form_view .o_notebook button[name='action_schedule_move_in']",
            run: "click",
        },
        {
            content: "The move-in form opens",
            trigger: ".o_form_view .o_form_statusbar button[name='action_complete']",
        },
        {
            content: "The tenant acknowledges the condition report",
            trigger: ".o_form_view .o_field_widget[name='tenant_acknowledged'] input",
            run: "click",
        },
        ...clickHeader("action_complete", "Complete the move-in"),
    ],
});

tours.add("atmta_cycle_5_billing", {
    steps: () => [
        formReady,
        openTab("Billing"),
        {
            content: "Generate the billing schedule",
            trigger: ".o_form_view button[name='action_generate_billing_schedule']",
            run: "click",
        },
        {
            content: "The obligations are listed",
            trigger: ".o_form_view div[name='contract_payment_ids'] .o_data_row",
        },
        {
            content: "Invoice what is due",
            trigger: ".o_form_view button[name='action_invoice_due_obligations']",
            run: "click",
        },
        {
            content: "At least one obligation is invoiced",
            trigger: ".o_form_view div[name='contract_payment_ids'] .o_data_row:contains('Invoiced')",
        },
    ],
});

tours.add("atmta_cycle_6_agent_renew_propose", {
    steps: () => [
        formReady,
        ...clickHeader("action_start_renewal", "Start the renewal"),
        {
            content: "The renewal form opens, seeded from the lease",
            trigger: ".o_form_view .o_form_statusbar button[name='action_propose']",
        },
        ...clickHeader("action_propose", "Propose the renewal"),
    ],
});

tours.add("atmta_cycle_7_manager_approve_renewal", {
    steps: () => [formReady, ...clickHeader("action_approve", "Approve the renewal terms")],
});

tours.add("atmta_cycle_8_agent_accept_renewal", {
    steps: () => [formReady, ...clickHeader("action_accept", "Record the tenant's acceptance")],
});

tours.add("atmta_cycle_9_manager_create_renewal_lease", {
    steps: () => [formReady, ...clickHeader("action_create_renewal_lease", "Create the renewal lease")],
});

// ---------------------------------------------------------------- Lease B
tours.add("atmta_cycle_10_pm_terminate_notice", {
    steps: () => [
        formReady,
        ...clickHeader("action_start_termination", "Start the termination"),
        {
            content: "The termination form opens with its dates filled in",
            trigger: ".o_form_view .o_form_statusbar button[name='action_give_notice']",
        },
        ...clickHeader("action_give_notice", "Record the notice"),
    ],
});

tours.add("atmta_cycle_11_manager_approve_settle", {
    steps: () => [
        formReady,
        ...clickHeader("action_approve", "Approve the termination"),
        ...clickHeaderConfirmed("action_settle", "Settle the termination"),
    ],
});

tours.add("atmta_cycle_12_pm_move_out", {
    steps: () => [
        formReady,
        {
            content: "Schedule the move-out",
            trigger: ".o_form_view .o_form_statusbar button[name='action_schedule_move_out']",
            run: "click",
        },
        {
            content: "The move-out form opens",
            trigger: ".o_form_view .o_form_statusbar button[name='action_start_inspection']",
        },
        ...clickHeader("action_start_inspection", "Start the move-out inspection"),
        ...clickHeader("action_complete", "Complete the move-out"),
    ],
});

tours.add("atmta_cycle_13_pm_complete_termination", {
    steps: () => [formReady, ...clickHeader("action_complete", "Complete the termination")],
});
