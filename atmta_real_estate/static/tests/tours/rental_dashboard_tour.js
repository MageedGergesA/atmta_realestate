/** @odoo-module **/

import { registry } from "@web/core/registry";
import { stepUtils } from "@web_tour/tour_service/tour_utils";

/**
 * Integration tour: menu → Rental dashboard → tile drilldown → record list.
 *
 * Deliberately small. The HOOT unit suite exercises the presentation logic in
 * depth; this tour proves the pieces are wired together end to end: the client
 * action, the assets, both real RPCs and a real drilldown against real records.
 */
registry.category("web_tour.tours").add("atmta_rental_dashboard_tour", {
    url: "/odoo",
    steps: () => [
        stepUtils.showAppsMenuItem(),
        {
            content: "Open the Rental app",
            trigger: '.o_app[data-menu-xmlid="atmta_real_estate.real_estate_menu_root"]',
            run: "click",
        },
        {
            content: "Open the Overview",
            trigger: '.o_nav_entry[data-menu-xmlid="atmta_real_estate.menu_rental_dashboard"], '
                + 'a[data-menu-xmlid="atmta_real_estate.menu_rental_dashboard"]',
            run: "click",
        },
        {
            content: "The dashboard shell is mounted",
            trigger: ".o_re_dashboard",
        },
        {
            content: "Tiles and charts have replaced every placeholder",
            trigger: ".o_re_dashboard:not(:has(.o_re_skeleton)) .o_re_chart_panel",
        },
        {
            content: "Work comes first, then portfolio health, then trends",
            trigger: ".o_re_dashboard",
            run() {
                const sections = [...document.querySelectorAll(".o_re_section[data-section]")]
                    .map((el) => el.dataset.section);
                const expected = ["work", "portfolio", "map", "trends"];
                if (JSON.stringify(sections) !== JSON.stringify(expected)) {
                    throw new Error(`Sections are ${sections.join(", ")}, expected ${expected.join(", ")}`);
                }
            },
        },
        {
            content: "A Rental Manager sees every tile, each with a value",
            trigger: ".o_re_dashboard",
            run() {
                const tiles = document.querySelectorAll("[data-tile]");
                const values = document.querySelectorAll("[data-tile] .o_re_kpi_value");
                if (tiles.length < 22 || values.length !== tiles.length) {
                    throw new Error(
                        `Expected 22 tiles with values, found ${tiles.length} tiles and ` +
                        `${values.length} values`
                    );
                }
            },
        },
        {
            content: "All four trend charts rendered",
            trigger: ".o_re_dashboard",
            run() {
                const panels = document.querySelectorAll(".o_re_chart_panel");
                if (panels.length !== 4) {
                    throw new Error(`Expected 4 chart panels, found ${panels.length}`);
                }
            },
        },
        {
            content: "The map card placed the located unit",
            trigger: ".o_re_map_card .o_re_map_canvas.leaflet-container",
            run() {
                const located = document.querySelector(".o_re_map_located");
                if (!located || !/[1-9]\d* located/.test(located.textContent)) {
                    throw new Error(`The map card did not count its units: ${located && located.textContent}`);
                }
                if (!document.querySelectorAll(".o_re_map_legend li").length) {
                    throw new Error("The map card has no legend");
                }
            },
        },
        {
            content: "Open the units available to lease",
            trigger: "[data-tile='available_to_lease'] .o_re_kpi_clickable",
            run: "click",
        },
        {
            content: "A real, filtered record list opened",
            trigger: ".o_list_view",
        },
        {
            content: "Go back to the dashboard",
            trigger: ".o_back_button, .breadcrumb-item:first a",
            run: "click",
        },
        {
            content: "The dashboard is still healthy after navigating back",
            trigger: ".o_re_dashboard [data-tile] .o_re_kpi_value",
        },
    ],
});
