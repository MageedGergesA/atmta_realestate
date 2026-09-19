/** @odoo-module **/

/**
 * M8 — the real-browser gate.
 *
 * Tours exist to verify that server and JavaScript work *together*. Every
 * assertion here runs against a live DOM in a real headless Chrome, and every
 * step waits on observable state rather than on time passing — a sleep that
 * hides a race leaves the race in the product.
 *
 * None of these type credentials: `start_tour` authenticates server-side.
 */

import { registry } from "@web/core/registry";

// ---------------------------------------------------------------------------
// Shared assertions
// ---------------------------------------------------------------------------

function assertNoBrokenNumbers(scope) {
    const el = document.querySelector(scope || ".o_content, .o_visual_gallery");
    const text = (el && el.innerText) || "";
    const broken = text.match(/NaN|undefined|Infinity|\[object Object\]/);
    if (broken) {
        const line = text.split("\n").find((l) => l.includes(broken[0]));
        throw new Error(`Rendered a broken value: "${broken[0]}" in "${line}".`);
    }
}

function assertNoHorizontalOverflow(label) {
    const doc = document.documentElement;
    if (doc.scrollWidth > doc.clientWidth + 1) {
        throw new Error(
            `[${label}] Page overflows horizontally: ${doc.scrollWidth} > ${doc.clientWidth}.`
        );
    }
}

function textOf(selector) {
    const el = document.querySelector(selector);
    return el ? (el.innerText || "").trim() : "";
}

/**
 * No Odoo error dialog may be on screen — in any step, in any tour.
 *
 * This exists because the gate caught the gallery passing four consecutive
 * steps with a red "Access Error" modal covering it: every assertion was about
 * chrome (title size, button height) and none about whether the thing behind
 * the chrome had actually worked. A tour that can pass over a broken screen is
 * decoration. Called from every checkpoint below.
 */
function assertNoErrorDialog(where) {
    const dialog = document.querySelector(
        ".o_error_dialog, .modal .o_dialog_error, .o_notification.border-danger");
    if (dialog) {
        const text = (dialog.innerText || "").split("\n").slice(0, 3).join(" ");
        throw new Error(
            `[${where}] An error dialog is on screen: "${text.trim()}". ` +
            "A customer-facing gallery must degrade, not raise."
        );
    }
    // A modal of any kind blocks the showroom; catching it here names the
    // cause instead of letting the next step time out on "below a modal".
    const modal = document.querySelector(".modal-dialog, .o_dialog");
    if (modal) {
        throw new Error(
            `[${where}] An unexpected modal is covering the gallery: ` +
            `"${(modal.innerText || "").slice(0, 120).trim()}"`
        );
    }
}

/**
 * Wait for an observable condition, with a stated failure.
 *
 * Not a sleep: it resolves the moment the condition holds and fails with the
 * caller's own message if it never does, so a real bug reads as that bug
 * rather than as a timeout. Used where the thing being waited for is a browser
 * state no CSS selector can express.
 */
async function waitUntil(condition, message, timeout = 8000) {
    const deadline = performance.now() + timeout;
    while (performance.now() < deadline) {
        if (condition()) {
            return true;
        }
        await new Promise((r) => requestAnimationFrame(r));
    }
    throw new Error(message);
}

/** No stack trace or loader internal may reach a customer. */
function assertNoInternals(scope) {
    const text = textOf(scope || ".o_visual_gallery");
    for (const leak of ["TypeError", "undefined is not", "at Object.",
                        "GLTFLoader", "THREE.", "Traceback"]) {
        if (text.includes(leak)) {
            throw new Error(`Loader internals reached the screen: "${leak}"`);
        }
    }
}

// ---------------------------------------------------------------------------
// 1. Presentation Mode — the customer-facing journey
// ---------------------------------------------------------------------------
registry.category("web_tour.tours").add("re_visual_presentation_tour", {
    steps: () => [
        {
            content: "The gallery opens as a showroom, not a form",
            trigger: ".o_visual_gallery",
            run: () => {
                if (document.querySelector(".o_visual_gallery .o_form_view")) {
                    throw new Error("A backend form rendered inside the gallery.");
                }
                assertNoErrorDialog("gallery open");
                assertNoBrokenNumbers(".o_visual_gallery");
                assertNoHorizontalOverflow("presentation mode");
            },
        },
        {
            content: "The project name is readable across a room",
            trigger: ".o_visual_gallery_title",
            run: () => {
                const el = document.querySelector(".o_visual_gallery_title");
                const size = parseFloat(getComputedStyle(el).fontSize);
                if (size < 18) {
                    throw new Error(
                        `Title is ${size}px — too small for a showroom.`
                    );
                }
                if (!textOf(".o_visual_gallery_title")) {
                    throw new Error("The gallery shows no project name.");
                }
                assertNoErrorDialog("title");
            },
        },
        {
            content: "Mode switches are touch-sized",
            trigger: ".o_visual_gallery_modes .o_vg_btn",
            run: () => {
                assertNoErrorDialog("mode switches");
                const buttons = document.querySelectorAll(".o_vg_btn");
                for (const button of buttons) {
                    const box = button.getBoundingClientRect();
                    if (box.height < 40) {
                        throw new Error(
                            `A control is ${Math.round(box.height)}px tall; ` +
                            "a tablet needs at least 44."
                        );
                    }
                }
            },
        },
        {
            content: "Switch to the plan",
            trigger: ".o_visual_gallery_modes .o_vg_btn:contains('Plan')",
            run: "click",
        },
        {
            content: "The stage re-renders without breaking",
            trigger: ".o_visual_gallery_stage",
            run: () => {
                assertNoErrorDialog("presentation 2D");
                assertNoBrokenNumbers(".o_visual_gallery");
                assertNoInternals();
                assertNoHorizontalOverflow("presentation 2D");
            },
        },
    ],
});

// ---------------------------------------------------------------------------
// 2. Presentation Mode with a customer
// ---------------------------------------------------------------------------
registry.category("web_tour.tours").add("re_visual_presentation_crm_tour", {
    steps: () => [
        {
            content: "The customer's name is on screen",
            trigger: ".o_vg_customer_name",
            run: () => {
                assertNoErrorDialog("customer context");
                const name = textOf(".o_vg_customer_name");
                if (!name) {
                    throw new Error(
                        "The gallery was opened with an opportunity but shows " +
                        "no customer."
                    );
                }
            },
        },
    ],
});

// ---------------------------------------------------------------------------
// 3. Fallback — a customer must never reach a dead end
// ---------------------------------------------------------------------------
registry.category("web_tour.tours").add("re_visual_fallback_tour", {
    steps: () => [
        {
            //  The fallback itself is the trigger. An OR with `.o_maquette_root`
            //  matched the still-loading viewer and asserted too early — the
            //  trigger has to *be* the state being waited for, not a superset
            //  of it.
            content: "The viewer degrades rather than showing an error box",
            trigger: ".o_maquette_fallback",
            run: () => {
                // The red box 0.4 showed. Its absence is the whole point.
                if (document.querySelector(".o_maquette_error")) {
                    throw new Error(
                        "The viewer showed a raw error box instead of " +
                        "falling back."
                    );
                }
                assertNoErrorDialog("fallback");
                assertNoInternals(".o_maquette_root");
            },
        },
        {
            content: "And says why, in one plain sentence",
            trigger: ".o_maquette_fallback .alert",
            run: () => {
                const reason = textOf(".o_maquette_fallback .alert");
                if (!reason) {
                    throw new Error("The fallback gives no reason at all.");
                }
                if (reason.length > 200) {
                    throw new Error(
                        `The reason is ${reason.length} characters — that is a ` +
                        "log line, not a sentence for a customer."
                    );
                }
            },
        },
        {
            //  A container is not a navigation path. This asserted only that
            //  the element existed, and passed for months while the list
            //  inside it rendered "No units to show" — `_teardown()` was
            //  clearing the unit data on the way to the fallback that needed
            //  it. Whichever rung the chain lands on must have something in it
            //  the customer can actually use.
            content: "A navigation path still exists, with something in it",
            trigger:
                ".o_maquette_fallback_list, .o_maquette_fallback_2d",
            run: () => {
                const list = document.querySelector(".o_maquette_fallback_list");
                if (list) {
                    const rows = list.querySelectorAll("tbody tr");
                    if (!rows.length) {
                        throw new Error(
                            "The list fallback rendered no units — the last " +
                            "rung of the chain is a dead end: " +
                            `"${(list.innerText || "").trim().slice(0, 80)}"`
                        );
                    }
                } else {
                    const plan = document.querySelector(
                        ".o_maquette_fallback_2d img");
                    if (!plan || !plan.getAttribute("src")) {
                        throw new Error(
                            "The 2D fallback rendered no plan image."
                        );
                    }
                }
                assertNoBrokenNumbers(".o_maquette_root");
                assertNoHorizontalOverflow("fallback");
            },
        },
    ],
});

// ---------------------------------------------------------------------------
// 3b. The customer's journey — select, read the terms, save
// ---------------------------------------------------------------------------
registry.category("web_tour.tours").add("re_visual_journey_tour", {
    steps: () => [
        {
            //  Headless Chrome has no GPU, so the viewer degrades to the unit
            //  list — which is the point: the journey has to work on the
            //  experience a real weak device gets, not only on the good one.
            content: "Pick a unit from whichever experience rendered",
            trigger: ".o_maquette_fallback_list tbody tr",
            run: "click",
        },
        {
            content: "The unit panel opens with the unit on it",
            trigger: ".o_vg_unit_panel .o_vg_unit_name",
            run: () => {
                assertNoErrorDialog("unit panel");
                if (!textOf(".o_vg_unit_name")) {
                    throw new Error("The panel opened with no unit on it.");
                }
                assertNoBrokenNumbers(".o_vg_unit_panel");
                assertNoInternals(".o_vg_unit_panel");
            },
        },
        {
            content: "The payment terms come from the server, already resolved",
            trigger: ".o_vg_plans .o_vg_plan_name, .o_vg_plans_none",
            run: () => {
                // Either a plan or a plain statement that there is none. What
                // must never appear is a spinner that never resolves or a
                // number the browser worked out for itself.
                if (document.querySelector(".o_vg_plans_loading")) {
                    throw new Error(
                        "The payment panel is still loading after the terms " +
                        "were rendered."
                    );
                }
                assertNoBrokenNumbers(".o_vg_plans");
            },
        },
        {
            content: "Save the unit for this customer",
            trigger: ".o_vg_shortlist",
            run: "click",
        },
        {
            content: "The gallery confirms it is saved",
            trigger: ".o_vg_shortlist:contains('Saved')",
            run: () => {
                assertNoErrorDialog("after saving");
            },
        },
    ],
});

// ---------------------------------------------------------------------------
// 3c. Comparison
// ---------------------------------------------------------------------------
registry.category("web_tour.tours").add("re_visual_compare_tour", {
    steps: () => [
        {
            content: "Pick the first unit",
            trigger: ".o_maquette_fallback_list tbody tr:first-child",
            run: "click",
        },
        {
            content: "Add it to the comparison",
            trigger: ".o_vg_compare_toggle",
            run: "click",
        },
        {
            content: "Pick a second unit",
            trigger: ".o_maquette_fallback_list tbody tr:nth-child(2)",
            run: "click",
        },
        {
            content: "Add that one too",
            trigger: ".o_vg_compare_toggle:not(.active)",
            run: "click",
        },
        {
            content: "Open the comparison",
            trigger: ".o_visual_gallery_customer .o_vg_btn:contains('Compare')",
            run: "click",
        },
        {
            content: "Two units, side by side, read just now",
            trigger: ".o_visual_gallery_compare .o_vg_compare_table",
            run: () => {
                assertNoErrorDialog("comparison");
                const columns = document.querySelectorAll(
                    ".o_vg_compare_table thead th");
                // One label column plus one per unit.
                if (columns.length < 3) {
                    throw new Error(
                        `The comparison rendered ${columns.length - 1} unit ` +
                        "column(s); two were chosen."
                    );
                }
                if (!textOf(".o_vg_compare_asof")) {
                    throw new Error(
                        "The comparison does not say when it was read. A " +
                        "price without a time is a price somebody will argue " +
                        "about."
                    );
                }
                assertNoBrokenNumbers(".o_visual_gallery_compare");
                assertNoHorizontalOverflow("comparison");
            },
        },
    ],
});

// ---------------------------------------------------------------------------
// 4. Three.js lifecycle — repeated open/close must not accumulate
// ---------------------------------------------------------------------------
registry.category("web_tour.tours").add("re_visual_lifecycle_tour", {
    steps: () => [
        {
            content: "Open, close and reopen the gallery several times",
            trigger: ".o_visual_gallery",
            run: async () => {
                const before = {
                    canvases: document.querySelectorAll("canvas").length,
                };
                for (let i = 0; i < 3; i++) {
                    const plan = [...document.querySelectorAll(".o_vg_btn")]
                        .find((b) => b.innerText.trim() === "Plan");
                    const three = [...document.querySelectorAll(".o_vg_btn")]
                        .find((b) => b.innerText.trim() === "3D");
                    if (plan) { plan.click(); }
                    await new Promise((r) => requestAnimationFrame(r));
                    if (three) { three.click(); }
                    await new Promise((r) => requestAnimationFrame(r));
                }
                // Canvas elements are the visible proxy for a leaked renderer:
                // each un-disposed viewer leaves one behind. Counting them is
                // not a memory measurement — the brief forbids claiming those
                // without tools — but an accumulating count is unambiguous
                // evidence of a leak.
                const after = document.querySelectorAll("canvas").length;
                if (after > before.canvases + 1) {
                    throw new Error(
                        `Canvas elements grew from ${before.canvases} to ` +
                        `${after} across three open/close cycles — a viewer ` +
                        "is not being torn down."
                    );
                }
            },
        },
    ],
});

// ---------------------------------------------------------------------------
// 5. RTL — the UI mirrors, the 3D world does not
// ---------------------------------------------------------------------------
registry.category("web_tour.tours").add("re_visual_rtl_tour", {
    steps: () => [
        {
            //  How Odoo 18 actually signals RTL: `start.js` adds `o_rtl` to
            //  <body> when the session's localization is right-to-left, and
            //  the mirroring itself comes from the rtlcss-flipped bundle.
            //  There is no `direction: rtl` declaration anywhere to read —
            //  asserting computed direction tested nothing and failed on a
            //  perfectly good Arabic session. Both halves are checked: the
            //  client knows it is RTL, and the mirrored stylesheet is loaded.
            content: "The session is genuinely right-to-left",
            trigger: "body.o_rtl .o_visual_gallery",
            run: async () => {
                await waitUntil(
                    () => [...document.querySelectorAll("link[href]")].some(
                        (l) => l.href.includes("web.assets_web.rtl")
                               && l.sheet),
                    "The session is flagged RTL but the mirrored stylesheet " +
                    "never loaded — the layout would still be left-to-right."
                );
                assertNoErrorDialog("RTL session");
                assertNoHorizontalOverflow("gallery in RTL");
            },
        },
        {
            content: "The 3D canvas is not mirrored",
            trigger: ".o_visual_gallery_stage",
            run: () => {
                // World coordinates are not a reading order. A transform that
                // flips the canvas would put the north side of a compound on
                // the wrong side of the screen for Arabic customers only.
                const stage = document.querySelector(".o_visual_gallery_stage");
                const transform = getComputedStyle(stage).transform;
                if (transform && transform.includes("matrix(-1")) {
                    throw new Error(
                        "The 3D stage is horizontally mirrored in RTL. UI " +
                        "direction and world coordinates are different things."
                    );
                }
            },
        },
    ],
});
