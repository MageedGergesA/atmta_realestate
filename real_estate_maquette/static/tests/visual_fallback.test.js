/** @odoo-module **/

/**
 * M4.5-B — the fallback decision, tested without a browser.
 *
 * `chooseExperience()` is a pure function of two plain objects specifically so
 * this is possible: the decision that keeps a customer out of a dead end
 * should not require a GPU to verify.
 *
 * The chain under test:
 *
 *     FULL 3D → OPTIMISED 2D → STANDARD INVENTORY LIST
 *
 * with the separate failure causes the brief enumerates each landing in the
 * right place.
 */

import { describe, expect, test } from "@odoo/hoot";
import {
    chooseExperience,
    meetsTier,
    TIER_HIGH,
    TIER_LOW,
    TIER_MEDIUM,
    TIER_NONE,
} from "@real_estate_maquette/js/visual_capability";

const CAPABLE = { supported: true, tier: TIER_HIGH, reasons: [], limits: {} };
const WEAK = { supported: true, tier: TIER_LOW, reasons: [], limits: {} };
const NO_WEBGL = { supported: false, tier: TIER_NONE, reasons: ["no_webgl_context"], limits: {} };

const WITH_2D = { has_2d: true, min_capability: TIER_LOW, default_experience: "auto" };
const WITHOUT_2D = { has_2d: false, min_capability: TIER_LOW, default_experience: "auto" };

describe("tier comparison", () => {
    test("a higher tier satisfies a lower minimum", () => {
        expect(meetsTier(TIER_HIGH, TIER_LOW)).toBe(true);
        expect(meetsTier(TIER_MEDIUM, TIER_LOW)).toBe(true);
    });

    test("a lower tier does not satisfy a higher minimum", () => {
        expect(meetsTier(TIER_LOW, TIER_HIGH)).toBe(false);
        expect(meetsTier(TIER_NONE, TIER_LOW)).toBe(false);
    });

    test("a tier satisfies itself", () => {
        expect(meetsTier(TIER_MEDIUM, TIER_MEDIUM)).toBe(true);
    });
});

describe("the fallback chain", () => {
    test("a capable device gets 3D", () => {
        const choice = chooseExperience(CAPABLE, WITH_2D);
        expect(choice.experience).toBe("3d");
        expect(choice.reason).toBe("capable");
    });

    test("no WebGL falls back to 2D when a plan exists", () => {
        const choice = chooseExperience(NO_WEBGL, WITH_2D);
        expect(choice.experience).toBe("2d");
        expect(choice.reason).toBe("no_webgl");
    });

    test("no WebGL and no plan still reaches the unit list", () => {
        // Rule 4's floor: never a dead end.
        const choice = chooseExperience(NO_WEBGL, WITHOUT_2D);
        expect(choice.experience).toBe("list");
        expect(choice.reason).toBe("no_webgl");
    });

    test("a device below the project's minimum tier degrades", () => {
        const strict = { ...WITH_2D, min_capability: TIER_HIGH };
        const choice = chooseExperience(WEAK, strict);
        expect(choice.experience).toBe("2d");
        expect(choice.reason).toBe("below_minimum_tier");
    });

    test("a weak device still gets 3D when the project allows it", () => {
        const choice = chooseExperience(WEAK, WITH_2D);
        expect(choice.experience).toBe("3d");
    });
});

describe("configured experience", () => {
    test("2D configured is honoured on a capable device", () => {
        const choice = chooseExperience(CAPABLE, { ...WITH_2D, default_experience: "2d" });
        expect(choice.experience).toBe("2d");
        expect(choice.reason).toBe("configured");
    });

    test("2D configured with no plan falls through to the list", () => {
        const choice = chooseExperience(CAPABLE, { ...WITHOUT_2D, default_experience: "2d" });
        expect(choice.experience).toBe("list");
        expect(choice.reason).toBe("configured_but_no_plan");
    });

    test("list configured is always honoured", () => {
        const choice = chooseExperience(CAPABLE, { ...WITH_2D, default_experience: "list" });
        expect(choice.experience).toBe("list");
    });

    test("3D configured on an incapable device still degrades", () => {
        // A configuration cannot make a GPU appear.
        const choice = chooseExperience(NO_WEBGL, { ...WITH_2D, default_experience: "3d" });
        expect(choice.experience).toBe("2d");
    });
});

describe("missing descriptor", () => {
    test("an empty descriptor never produces a dead end", () => {
        const choice = chooseExperience(NO_WEBGL, {});
        expect(choice.experience).toBe("list");
    });

    test("a capable device with an empty descriptor still gets 3D", () => {
        const choice = chooseExperience(CAPABLE, {});
        expect(choice.experience).toBe("3d");
    });
});
