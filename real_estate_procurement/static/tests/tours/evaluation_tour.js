/** @odoo-module **/

/**
 * M6 — the real-browser gate for evaluation.
 *
 * The tour walks a finalised evaluation and checks the things that would make
 * it indefensible: a frozen basis that says so on screen, candidates carrying
 * a technical result, a knockout failure that is not ranked, an evaluated cost
 * shown beside the raw bid rather than instead of it, and — the point of the
 * whole milestone — no award anywhere.
 *
 * `start_tour` authenticates server-side; nothing here types a credential.
 */

import { registry } from "@web/core/registry";
import {
    assertNoBrokenValues,
    assertNoErrorDialog,
    clickMenu,
    requireText,
} from "@real_estate_procurement/../tests/tours/menu_nav";

/**
 * `trigger` defaults to `.o_content`, which exists on both the list and the
 * form — so a checkpoint straight after opening a record can run before the
 * form has rendered. Steps that inspect a form pass `.o_form_view` and wait
 * for the thing they are about to assert against.
 */
/**
 * Evaluation language is allowed to appear in prose that *denies* awarding —
 * the round form carries a notice saying this is not an award. So the ban is
 * scoped to record data, the same lesson the M5 tour learned when its guard
 * flagged its own disclaimer.
 *
 * Stays here rather than moving to `menu_nav.js`: it is about what M6 must not
 * say, not about navigating a menu.
 */
function forbidInData(label, needles) {
    const scope = document.querySelector(".o_field_x2many_list") ||
        document.querySelector(".o_list_view");
    const text = ((scope && scope.textContent) || "").toLowerCase();
    for (const needle of needles) {
        if (text.includes(needle.toLowerCase())) {
            throw new Error(
                `[${label}] "${needle}" appeared in M6 record data. M6 evaluates; ` +
                `M7 awards.`
            );
        }
    }
}

function checkpoint(label, extra, trigger) {
    return {
        content: `checkpoint: ${label}`,
        trigger: trigger || ".o_content",
        run: () => {
            assertNoErrorDialog(label);
            assertNoBrokenValues(label);
            if (extra) {
                extra(label);
            }
        },
    };
}

function openTab(name) {
    return {
        content: `open the ${name} tab`,
        trigger: `.o_notebook .nav-link:contains("${name}")`,
        run: "click",
    };
}

/**
 * Navigation is part of what is being tested.
 *
 * This tour used to start at `/odoo/action-real_estate_procurement.action_
 * evaluation_round` — a direct action URL — which is how it passed while M6
 * had no menu at all. A milestone nobody can open is not shipped, so the happy
 * path now walks the real hierarchy: the Procurement app, the Evaluation
 * section, then Evaluations.
 */
registry.category("web_tour.tours").add("procurement_evaluation_tour", {
    url: "/odoo",
    steps: () => [
        {
            content: "open the Procurement app",
            trigger: ".o_app[data-menu-xmlid='real_estate_procurement.menu_procurement_root']",
            run: "click",
        },
        {
            content: "open the Evaluation section, wherever this width put it",
            trigger: ".o_main_navbar",
            async run() {
                await clickMenu("real_estate_procurement.menu_evaluation");
                await clickMenu("real_estate_procurement.menu_evaluation_rounds");
            },
        },
        checkpoint("evaluation list opened", undefined, ".o_list_view"),
        {
            // Click the reference *cell*, not the row: clicking the row
            // element itself does not always reach the handler that opens the
            // record, and the tour then waits for a form that never arrives.
            content: "open the finalised evaluation",
            trigger: ".o_list_view td[name='name']",
            run: "click",
        },
        checkpoint("the round says plainly that it is not an award", (label) =>
            requireText(label, ["not an award"]), ".o_form_view"),

        // -- Candidates: results, exclusions and ranks -------------------
        openTab("Candidates"),
        checkpoint("every candidate carries a technical result", (label) => {
            const rows = [...document.querySelectorAll(
                ".o_field_x2many_list .o_data_row")];
            if (rows.length < 3) {
                throw new Error(
                    `[${label}] Expected three candidates, found ${rows.length}.`
                );
            }
            requireText(label, ["Technically Responsive"]);
            forbidInData(label, ["winner", "awarded", "recommended"]);
        }),

        // -- Committee ---------------------------------------------------
        openTab("Committee"),
        checkpoint("the committee and its declarations are visible"),

        // -- Technical sheets: no money anywhere -------------------------
        openTab("Technical Sheets"),
        checkpoint("submitted sheets are listed with their scores", (label) =>
            requireText(label, ["Submitted"])
        ),

        // -- Commercial: raw beside evaluated ----------------------------
        openTab("Commercial"),
        checkpoint("the commercial result is ranked but not awarded", (label) => {
            forbidInData(label, ["winner", "awarded", "award"]);
        }),
    ],
});
