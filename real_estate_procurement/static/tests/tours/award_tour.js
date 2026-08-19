/** @odoo-module **/

/**
 * M7 — the award screens, driven through the real menu.
 *
 * The tour walks an **approved** award and checks the things that would make it
 * indefensible on screen: that the document says approving is not committing,
 * that the awarded scope is visible rather than implied, that the authorisation
 * trail names two different people, and — the point of the milestone — that
 * nothing on the award screen presents a commitment figure of its own.
 *
 * That last one matters more than it looks. Construction computes commitment
 * from confirmed purchase orders. An award screen showing its own commitment
 * number would be a second figure that disagrees with the first under exactly
 * the conditions nobody tests, and somebody would eventually believe it.
 */

import { registry } from "@web/core/registry";
import {
    assertNoBrokenValues,
    assertNoErrorDialog,
    clickMenu,
    requireText,
} from "@real_estate_procurement/../tests/tours/menu_nav";

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

registry.category("web_tour.tours").add("procurement_award_tour", {
    url: "/odoo",
    steps: () => [
        {
            content: "open the Procurement app",
            trigger: ".o_app[data-menu-xmlid='real_estate_procurement.menu_procurement_root']",
            run: "click",
        },
        {
            content: "reach Awards through the menu, at whatever width this is",
            trigger: ".o_main_navbar",
            async run() {
                await clickMenu("real_estate_procurement.menu_evaluation");
                await clickMenu("real_estate_procurement.menu_procurement_awards");
            },
        },
        checkpoint("the award list opened", undefined, ".o_list_view"),
        {
            // The reference cell, not the row: clicking the row element does
            // not always reach the handler that opens the record.
            content: "open the award",
            trigger: ".o_list_view td[name='name']",
            run: "click",
        },
        checkpoint(
            "the screen says plainly that approving is not committing",
            (label) =>
                requireText(label, [
                    "Approving it authorises the named purchase",
                    "Construction computes the commitment",
                ]),
            ".o_form_view"
        ),

        // -- The awarded scope has to be visible, not implied ---------------
        checkpoint("the vendor and the rank it held are on screen", (label) => {
            requireText(label, ["Gulf Ready-Mix LLC"]);
            const rows = [...document.querySelectorAll(
                ".o_field_x2many_list .o_data_row")];
            if (!rows.length) {
                throw new Error(`[${label}] The award names no vendor.`);
            }
        }),

        // -- Authorisation: two people, and the record says which ----------
        openTab("Approval"),
        checkpoint("the maker and the checker are both named", (label) => {
            requireText(label, ["Maker", "Checker"]);
        }),

        // -- The thing that must NOT be here -------------------------------
        checkpoint("no commitment figure is presented on the award", (label) => {
            const el = document.querySelector(".o_content");
            const text = ((el && el.textContent) || "").toLowerCase();
            // "Construction computes the commitment" is the notice explaining
            // that this screen does not hold one, so the ban is on a labelled
            // *field*, not on the word appearing in prose.
            const labels = [...document.querySelectorAll(
                ".o_form_label, th, .o_horizontal_separator")]
                .map((n) => (n.textContent || "").trim().toLowerCase());
            const offending = labels.filter(
                (l) => l === "commitment" || l === "committed amount");
            if (offending.length) {
                throw new Error(
                    `[${label}] The award screen presents a commitment field ` +
                    `(${JSON.stringify(offending)}). Construction computes ` +
                    `commitment from confirmed orders; a second figure here ` +
                    `would eventually disagree with it.`
                );
            }
            if (!text.includes("construction computes the commitment")) {
                throw new Error(
                    `[${label}] The notice explaining where commitment comes ` +
                    `from is missing.`
                );
            }
        }),
    ],
});
