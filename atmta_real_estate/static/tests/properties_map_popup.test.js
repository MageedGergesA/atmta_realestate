/** @odoo-module **/

import { beforeEach, describe, expect, test } from "@odoo/hoot";
import { makeMockEnv } from "@web/../tests/web_test_helpers";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";

import { buildPropertyPopup } from "@atmta_real_estate/js/properties_map_dashboard";

describe.current.tags("desktop");

/** `atmta_real_estate` depends on `mail`, so the mail models must be defined. */
defineMailModels();

/** The popup's labels are translated, so translations must be loaded first. */
beforeEach(async () => {
    await makeMockEnv();
});

/**
 * Units → Map popups used to be HTML strings in which the property type and
 * country names were not escaped at all, so a type named with markup ran as
 * HTML in every map user's browser. They are now built from DOM nodes.
 */
describe("properties map popup", () => {
    const hostile = '<img src=x onerror="window.__pmdInjected=1">';

    test("names are shown as text, never interpreted as HTML", async () => {
        const popup = buildPropertyPopup({
            id: 7,
            name: `Unit ${hostile}`,
            property_code: `<b>CODE</b>`,
            city: `<i>City</i>`,
            state: "rented",
            hierarchy_level: "unit",
            property_type_id: [3, `Type ${hostile}`],
            country_id: [4, `<script>window.__pmdInjected=1</script>`],
        }, () => {});

        expect(popup.querySelectorAll("img")).toHaveLength(1);
        expect(popup.querySelector("script")).toBe(null);
        expect(popup.querySelector("b")).toBe(null);
        expect(popup.textContent).toInclude(`Unit ${hostile}`);
        expect(popup.textContent).toInclude(`Type ${hostile}`);
        expect(popup.textContent).toInclude("<b>CODE</b>");
        expect(window.__pmdInjected).toBe(undefined);
    });

    test("Open calls back with the unit id", async () => {
        const opened = [];
        const popup = buildPropertyPopup({ id: 12, name: "Unit 12", state: "available" },
            (id) => opened.push(id));
        popup.querySelector(".o_pmd_open_btn").click();
        expect(opened).toEqual([12]);
    });
});
