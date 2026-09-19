/** @odoo-module **/

import { registry } from "@web/core/registry";

/**
 * Workflow tours — the treasury lifecycle driven through the real UI.
 *
 * These are not the server tests re-expressed in JavaScript. They exist because
 * the server suite proves the *engine* is right and proves nothing about
 * whether a treasurer can see what state a cheque is in. Each one clicks the
 * buttons a treasurer clicks and reads the badges a treasurer reads.
 *
 * The fixtures (tests/test_workflow_browser.py) plant exactly one cheque per
 * scenario, so every selector below is unambiguous.
 */

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formText() {
    const form = document.querySelector(".modal .o_form_view")
        || document.querySelector(".o_form_view");
    if (!form) {
        throw new Error("No form view on screen");
    }
    return form.innerText;
}

/** The stage the statusbar is currently sitting on. */
function currentStatus() {
    const active = document.querySelector(
        ".o_statusbar_status .o_arrow_button_current"
    );
    return (active?.innerText || "").trim();
}

function assertStatus(expected, label) {
    const actual = currentStatus();
    if (!actual.toLowerCase().includes(expected.toLowerCase())) {
        throw new Error(
            `[${label}] Statusbar shows "${actual}", expected "${expected}"`
        );
    }
}

function fieldText(name) {
    const el = document.querySelector(`[name="${name}"]`);
    return (el?.innerText || el?.value || "").trim();
}

function assertNothingBroken(label) {
    const text = formText();
    if (/NaN|undefined|\[object Object\]/.test(text)) {
        throw new Error(`[${label}] Form rendered a broken value`);
    }
}

// ---------------------------------------------------------------------------
// 1a. Before presentation — paper received, no accounting
// ---------------------------------------------------------------------------

registry.category("web_tour.tours").add("atmta_treasury_before_deposit_tour", {
    url: "/odoo/action-real_estate_checks.action_realestate_check",
    steps: () => [
        {
            content: "Open the cheque the fixture planted",
            trigger: ".o_list_view .o_data_row:first-child .o_data_cell",
            run: "click",
        },
        {
            content: "It is Registered, on hand, with no payment (Rule 2)",
            trigger: ".o_form_view .o_statusbar_status",
            run() {
                assertStatus("Registered", "before deposit");
                assertNothingBroken("before deposit");

                const accounting = fieldText("accounting_state");
                if (!/no payment/i.test(accounting)) {
                    throw new Error(
                        `Accounting Status reads "${accounting}", expected ` +
                        `"No Payment". A received PDC is not cash.`
                    );
                }
                if (fieldText("payment_id")) {
                    throw new Error("A registered cheque already has a payment");
                }
            },
        },
        {
            content: "Its allocation covers the obligation in full",
            trigger: ".o_notebook .nav-link:contains('Allocations')",
            run: "click",
        },
        {
            content: "Exactly one allocation, nothing unapplied",
            trigger: "[name='allocation_ids'] .o_data_row",
            run() {
                const rows = document.querySelectorAll(
                    "[name='allocation_ids'] .o_data_row"
                );
                if (rows.length !== 1) {
                    throw new Error(`Expected 1 allocation, found ${rows.length}`);
                }
                // The obligation it points at must be visible, so a treasurer
                // can see what the cheque is meant to settle.
                if (!/instal/i.test(rows[0].innerText)) {
                    throw new Error(
                        `The allocation does not name its obligation: ${rows[0].innerText}`
                    );
                }
            },
        },
    ],
});

// ---------------------------------------------------------------------------
// 1b. Presentation — deposit, payment, and NOT cleared
// ---------------------------------------------------------------------------

registry.category("web_tour.tours").add("atmta_treasury_deposit_flow_tour", {
    url: "/odoo/action-real_estate_checks.action_realestate_check",
    steps: () => [
        {
            content: "Select the matured cheque",
            trigger: ".o_list_view thead .o_list_record_selector input",
            run: "click",
        },
        {
            content: "Open the Actions menu",
            trigger: ".o_cp_action_menus .dropdown-toggle:contains('Actions')",
            run: "click",
        },
        {
            content: "Choose the Deposit Workbench",
            trigger: ".o-dropdown--menu .dropdown-item:contains('Deposit Workbench')",
            run: "click",
        },
        {
            content: "The workbench warns that presenting is not collecting",
            trigger: ".modal .o_form_view",
            run() {
                const text = document.querySelector(".modal").innerText;
                if (!/money on its way/i.test(text)) {
                    throw new Error(
                        "The workbench does not distinguish presenting from collecting"
                    );
                }
                if (!/Cleared.*only when Odoo reconciles|reconciles those\s+payments/is.test(text)) {
                    throw new Error(
                        "The workbench does not say clearance requires reconciliation"
                    );
                }
            },
        },
        {
            content: "A destination bank is already proposed",
            trigger: ".modal [name='journal_id'] input",
            run() {
                const value = document.querySelector(
                    ".modal [name='journal_id'] input"
                ).value;
                if (!value.trim()) {
                    throw new Error(
                        "The workbench proposes no destination bank, so a " +
                        "treasurer must retype it on every slip"
                    );
                }
            },
        },
        {
            content: "Create the deposit",
            trigger: ".modal footer button:contains('Create Deposit')",
            run: "click",
        },
        {
            // Modals are portaled to <body>, outside `.o_action_manager`, so
            // scoping the trigger there means "the form in the main content
            // area" rather than "the wizard that is still open".
            content: "The slip is at the bank and carries exactly one payment",
            trigger: ".o_action_manager .o_form_view .o_statusbar_status",
            run() {
                assertStatus("At Bank", "deposit slip");
                assertNothingBroken("deposit slip");

                // The on-screen guarantee is the banner on the form, not the
                // chatter message — a treasurer reads the form.
                const text = formText();
                if (!/Presented to the bank/i.test(text)) {
                    throw new Error(
                        "The confirmed slip does not say the paper has been " +
                        `presented. Screen: ${text.slice(0, 400)}`
                    );
                }
                if (!/only when Odoo\s+reconciles/is.test(text)) {
                    throw new Error(
                        "The confirmed slip does not say clearance waits for " +
                        `reconciliation. Screen: ${text.slice(0, 400)}`
                    );
                }
                const payments = document.querySelector(
                    "button[name='action_view_payments']"
                );
                if (!payments) {
                    throw new Error("No payments button on the confirmed slip");
                }
                const count = parseInt(payments.innerText.replace(/[^\d]/g, ""), 10);
                if (count !== 1) {
                    throw new Error(
                        `Slip shows ${count} payments, expected exactly 1 ` +
                        "(one physical instrument, one accounting payment)"
                    );
                }
            },
        },
    ],
});

/**
 * The cheque side of the presentation, opened from the Cheques action.
 *
 * Deliberately a separate tour rather than a click-through from the slip: a
 * row inside a one2many opens in a dialog whose form view is chosen by Odoo,
 * so asserting the cheque's own banner there would be testing the dialog's
 * view resolution rather than the cheque.
 */
registry.category("web_tour.tours").add("atmta_treasury_presented_check_tour", {
    url: "/odoo/action-real_estate_checks.action_realestate_check",
    steps: () => [
        {
            content: "Open the cheque",
            trigger: ".o_list_view .o_data_row:first-child .o_data_cell",
            run: "click",
        },
        {
            content: "It is Presented and explicitly NOT Cleared",
            trigger: ".o_form_view .o_statusbar_status",
            run() {
                assertStatus("Presented", "after presentation");
                assertNothingBroken("after presentation");

                const text = formText();
                if (!/at the bank/i.test(text)) {
                    throw new Error(
                        "The cheque does not say it is at the bank awaiting " +
                        `confirmation. Screen: ${text.slice(0, 400)}`
                    );
                }
                if (!/not confirmed until Odoo/is.test(text)) {
                    throw new Error(
                        "The cheque does not say the money is unconfirmed"
                    );
                }
                // The accounting dimension must show registered-not-reconciled.
                const accounting = fieldText("accounting_state");
                if (/reconciled/i.test(accounting)) {
                    throw new Error(
                        `Accounting Status reads "${accounting}" with no bank match`
                    );
                }
                if (!fieldText("payment_id")) {
                    throw new Error(
                        "A presented cheque shows no payment, so a treasurer " +
                        "cannot see that the money was registered"
                    );
                }
            },
        },
    ],
});

/**
 * 1c. After the bank matches.
 *
 * The Python side reconciles a real bank statement line between the two tours;
 * this one only checks that the browser now shows clearance.
 */
registry.category("web_tour.tours").add("atmta_treasury_cleared_flow_tour", {
    url: "/odoo/action-real_estate_checks.action_realestate_check",
    steps: () => [
        {
            content: "Open the cheque",
            trigger: ".o_list_view .o_data_row:first-child .o_data_cell",
            run: "click",
        },
        {
            content: "After bank reconciliation it reads Cleared",
            trigger: ".o_form_view .o_statusbar_status",
            run() {
                assertStatus("Cleared", "after bank match");
                assertNothingBroken("after bank match");

                const accounting = fieldText("accounting_state");
                if (!/bank reconciled/i.test(accounting)) {
                    throw new Error(
                        `Accounting Status reads "${accounting}", expected "Bank Reconciled"`
                    );
                }
                if (!fieldText("cleared_date")) {
                    throw new Error("A cleared cheque has no cleared date");
                }
                const ribbons = [...document.querySelectorAll(".o_widget_web_ribbon")]
                    .map((r) => r.innerText)
                    .join(" ");
                if (!/cleared/i.test(ribbons)) {
                    throw new Error("No Cleared ribbon on the form");
                }
            },
        },
    ],
});

// ---------------------------------------------------------------------------
// 2. Bounce BEFORE any bank match — the ledger is safely restored
// ---------------------------------------------------------------------------

registry.category("web_tour.tours").add("atmta_treasury_bounce_flow_tour", {
    url: "/odoo/action-real_estate_checks.action_realestate_check",
    steps: () => [
        {
            content: "Open the presented cheque",
            trigger: ".o_list_view .o_data_row:first-child .o_data_cell",
            run: "click",
        },
        {
            content: "Register the bounce",
            trigger: "button[name='action_open_bounce_wizard']",
            run: "click",
        },
        {
            content: "The wizard states the ledger effect BEFORE anything happens",
            trigger: ".modal [name='accounting_preview']",
            run() {
                // The wizard renders both branches (safe / manual) and hides
                // one, so take whichever actually has text rather than the
                // first in document order.
                const preview = [
                    ...document.querySelectorAll(".modal [name='accounting_preview']"),
                ]
                    .map((el) => el.innerText || "")
                    .filter((t) => t.trim())
                    .join("\n");
                if (!/unreconciled and then cancelled/i.test(preview)) {
                    throw new Error(
                        "The preview does not describe the unwind.\n" +
                        `PREVIEW FIELD: "${preview}"\n` +
                        `MODAL TEXT: ${document.querySelector(".modal").innerText}`
                    );
                }
                if (!/Nothing is deleted/i.test(preview)) {
                    throw new Error(
                        "The preview does not promise that nothing is deleted"
                    );
                }
                if (!/return to outstanding/i.test(preview)) {
                    throw new Error(
                        "The preview does not say the invoice becomes outstanding"
                    );
                }

                const modal = document.querySelector(".modal").innerText;
                // 0.1 had an "Issue Penalty Invoice Now" tickbox right here.
                if (/issue penalty invoice now/i.test(modal)) {
                    throw new Error(
                        "Invoicing a customer must not be a side effect of a bounce"
                    );
                }
                // M11 — the bank's charge and the customer's penalty are two
                // different amounts owed by two different parties.
                for (const name of ["bank_charge_amount", "penalty_amount"]) {
                    if (!document.querySelector(`.modal [name="${name}"]`)) {
                        throw new Error(`The bounce wizard has no ${name} field`);
                    }
                }
            },
        },
        {
            content: "Confirm the bounce",
            trigger: ".modal footer button:contains('Register Bounce')",
            run: "click",
        },
        {
            // `accounting_note` exists only on the bounce, so this cannot match
            // the cheque form we came from.
            content: "The receivable was restored, and the record says how",
            trigger: ".o_action_manager [name='accounting_note']",
            run() {
                assertNothingBroken("after bounce");
                const text = formText();
                if (!/Unreconciled payment/i.test(text)) {
                    throw new Error(
                        `The bounce does not record what it did to the ledger:\n${text.slice(0, 400)}`
                    );
                }
                if (!/reversed, not deleted/i.test(text)) {
                    throw new Error("The bounce does not confirm the entry survives");
                }
                const ribbons = [...document.querySelectorAll(".o_widget_web_ribbon")]
                    .map((r) => r.innerText)
                    .join(" ");
                if (/manual accounting/i.test(ribbons)) {
                    throw new Error(
                        "A pre-bank-match bounce must NOT need manual accounting"
                    );
                }
            },
        },
    ],
});

/**
 * The cheque side of the same bounce, opened the way a treasurer would: from
 * the Cheques list rather than by clicking through a relational field, which
 * renders differently depending on whether the field is editable.
 */
registry.category("web_tour.tours").add("atmta_treasury_bounced_check_tour", {
    url: "/odoo/action-real_estate_checks.action_realestate_check",
    steps: () => [
        {
            content: "Open the cheque",
            trigger: ".o_list_view .o_data_row:first-child .o_data_cell",
            run: "click",
        },
        {
            content: "It reads Bounced",
            trigger: ".o_form_view .o_statusbar_status",
            run() {
                assertStatus("Bounced", "after bounce");
                assertNothingBroken("after bounce");
            },
        },
        {
            content: "The failed presentation is still on record",
            trigger: ".o_notebook .nav-link:contains('Presentations')",
            run: "click",
        },
        {
            content: "Attempt 1 is preserved, marked bounced",
            trigger: "[name='presentation_ids'] .o_data_row",
            run() {
                const rows = document.querySelectorAll(
                    "[name='presentation_ids'] .o_data_row"
                );
                if (rows.length !== 1) {
                    throw new Error(
                        `Expected 1 presentation attempt on record, found ${rows.length}`
                    );
                }
                if (!/bounced/i.test(rows[0].innerText)) {
                    throw new Error("The presentation attempt is not marked bounced");
                }
            },
        },
    ],
});

// ---------------------------------------------------------------------------
// 3. Bounce AFTER a bank match — the guard, and the instructions
// ---------------------------------------------------------------------------

registry.category("web_tour.tours").add("atmta_treasury_matched_bounce_tour", {
    url: "/odoo/action-real_estate_checks.action_check_bounce",
    steps: () => [
        {
            content: "Open the bounce the fixture recorded",
            trigger: ".o_list_view .o_data_row:first-child .o_data_cell",
            run: "click",
        },
        {
            content: "It is flagged as needing manual accounting, with reasons",
            trigger: ".o_form_view .alert-danger",
            run() {
                assertNothingBroken("matched bounce");
                const ribbons = [...document.querySelectorAll(".o_widget_web_ribbon")]
                    .map((r) => r.innerText)
                    .join(" ");
                if (!/manual accounting/i.test(ribbons)) {
                    throw new Error(
                        "An already-bank-matched bounce is not flagged for manual accounting"
                    );
                }
                const text = document.querySelector(
                    ".o_form_view .alert-danger"
                ).innerText;
                for (const phrase of [
                    "already been matched",
                    "will not silently unwind",
                    "returned-cheque debit",
                    "Reconcile it against a reversal",
                    "ledger is untouched",
                ]) {
                    if (!text.includes(phrase)) {
                        throw new Error(
                            `The instructions omit "${phrase}":\n${text.slice(0, 600)}`
                        );
                    }
                }
            },
        },
        {
            content: "Attempt to re-present",
            trigger: "button[name='action_authorize_representation']",
            run: "click",
        },
        {
            content: "It is blocked, and the refusal explains why",
            trigger: ".o_dialog .modal-body, .o_notification_content",
            run() {
                const text = (
                    document.querySelector(".o_dialog .modal-body") ||
                    document.querySelector(".o_notification_content")
                ).innerText;
                if (!/awaiting manual accounting/i.test(text)) {
                    throw new Error(
                        `Re-presentation was not blocked for the right reason:\n${text}`
                    );
                }
                if (!/same money twice/i.test(text)) {
                    throw new Error(
                        "The refusal does not explain the double-registration risk"
                    );
                }
            },
        },
    ],
});
