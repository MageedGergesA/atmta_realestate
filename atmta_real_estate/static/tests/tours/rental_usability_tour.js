/** @odoo-module **/

import { registry } from "@web/core/registry";
import { stepUtils } from "@web_tour/tour_service/tour_utils";

/**
 * Five-second test, run as a Leasing Agent (Phase 5 quality gate).
 *
 * Opening the Rental app must make four things obvious without a click: where
 * the user is, what needs attention, how to create a lease and how to find an
 * available unit. The last step proves the "find" affordance works.
 */
registry.category("web_tour.tours").add("atmta_rental_five_second_tour", {
    url: "/odoo",
    steps: () => [
        stepUtils.showAppsMenuItem(),
        {
            content: "Open the Rental app",
            trigger: '.o_app[data-menu-xmlid="atmta_real_estate.real_estate_menu_root"]',
            run: "click",
        },
        {
            content: "The app opens on the Rental overview",
            trigger: ".o_re_dashboard:not(:has(.o_re_skeleton)) [data-section='work']",
        },
        {
            content: "Where am I, what needs attention, how to create, how to find",
            trigger: ".o_re_dashboard",
            run() {
                const brand = document.querySelector(".o_menu_brand");
                if (!brand || !brand.textContent.includes("Rental")) {
                    throw new Error(`The app name is not shown: "${brand && brand.textContent}"`);
                }
                const firstSection = document.querySelector(".o_re_section[data-section]");
                if (!firstSection || firstSection.dataset.section !== "work") {
                    throw new Error("My Work is not the first thing on screen.");
                }
                const quick = [...document.querySelectorAll(".o_re_quick_action")]
                    .map((el) => el.dataset.quickAction);
                for (const wanted of ["new_lease", "available_units"]) {
                    if (!quick.includes(wanted)) {
                        throw new Error(`Quick action "${wanted}" is missing: ${quick.join(", ")}`);
                    }
                }
                if (document.querySelector(".o_re_quick_action[data-quick-action='move_in']")) {
                    throw new Error("A Leasing Agent is offered a Property Manager action.");
                }
                const navItems = [...document.querySelectorAll(".o_menu_sections .o_nav_entry, .o_menu_sections .dropdown-toggle")]
                    .map((el) => el.textContent.trim());
                if (navItems.some((name) => name === "Configuration")) {
                    throw new Error("A Leasing Agent sees the Configuration menu.");
                }
            },
        },
        {
            content: "Find an available unit",
            trigger: ".o_re_quick_action[data-quick-action='available_units']",
            run: "click",
        },
        {
            content: "The available units open, listing the unit",
            trigger: ".o_list_view .o_data_row:contains('FIVE-U-001')",
        },
    ],
});
