/** @odoo-module **/

/**
 * HOOT tests for the Control Tower's formatting rules.
 *
 * These cover the one place a reporting UI most easily lies: turning an
 * unknown into a zero. `money()` and `percent()` are pure functions precisely
 * so that distinction can be tested without a server, a mount or a currency.
 *
 * Only the unknown branches are asserted here. The formatted branch delegates
 * to Odoo's own `formatMonetary`, which needs the localization service — and a
 * test that stood up half the web client to prove Odoo can format a number
 * would be testing Odoo, not this module. That a real number is *not* "N/A" is
 * asserted through `percent()`, which formats without a currency.
 */

import { expect, test, describe } from "@odoo/hoot";
import {
    money,
    percent,
    STATUS_LABELS,
} from "@real_estate_construction/js/control_tower/control_tower";

describe("Control Tower formatting", () => {
    test("an unknown amount is N/A, never zero", () => {
        expect(money(null, false)).toBe("N/A");
        expect(money(undefined, false)).toBe("N/A");
    });

    test("an unknown percentage is N/A", () => {
        expect(percent(null)).toBe("N/A");
        expect(percent(undefined)).toBe("N/A");
    });

    test("a genuine zero is rendered as a number, not as unknown", () => {
        // Zero is business information: somebody forecast nothing, which is
        // not the same as nobody forecasting.
        expect(percent(0)).toBe("0.0%");
        expect(percent(0) === "N/A").toBe(false);
    });

    test("a real percentage keeps one decimal", () => {
        expect(percent(12.34)).toBe("12.3%");
        expect(percent(100)).toBe("100.0%");
    });

    test("every health status has a human label", () => {
        for (const key of ["on_track", "attention", "at_risk", "critical", "no_data"]) {
            expect(Boolean(STATUS_LABELS[key])).toBe(true);
        }
    });

    test("no data is labelled as such rather than as fine", () => {
        expect(STATUS_LABELS.no_data).toBe("No Data");
        expect(STATUS_LABELS.no_data === STATUS_LABELS.on_track).toBe(false);
    });
});
