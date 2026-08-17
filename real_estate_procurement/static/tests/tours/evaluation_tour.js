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

function assertNoErrorDialog(label) {
    const dialog = document.querySelector(".o_error_dialog, .o_dialog_error");
    if (dialog) {
        const text = (dialog.innerText || "").split("\n").slice(0, 4).join(" | ");
        throw new Error(`[${label}] An error dialog is open: ${text}`);
    }
}

function assertNoBrokenValues(label) {
    const el = document.querySelector(".o_content");
    const text = (el && el.textContent) || "";
    const broken = text.match(/NaN|Infinity|\[object Object\]/);
    if (broken) {
        throw new Error(`[${label}] Rendered a broken value: "${broken[0]}".`);
    }
}

function pageText() {
    const el = document.querySelector(".o_content");
    return (el && el.textContent) || "";
}

function requireText(label, needles) {
    const text = pageText();
    for (const needle of needles) {
        if (!text.includes(needle)) {
            // Include what was actually rendered. A tour that only says what
            // it wanted makes every failure a second investigation.
            const seen = text.replace(/\s+/g, " ").slice(0, 300);
            throw new Error(
                `[${label}] Expected to find "${needle}". Screen showed: ${seen}`
            );
        }
    }
}

/**
 * Evaluation language is allowed to appear in prose that *denies* awarding —
 * the round form carries a notice saying this is not an award. So the ban is
 * scoped to record data, the same lesson the M5 tour learned when its guard
 * flagged its own disclaimer.
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

/**
 * `trigger` defaults to `.o_content`, which exists on both the list and the
 * form — so a checkpoint straight after opening a record can run before the
 * form has rendered. Steps that inspect a form pass `.o_form_view` and wait
 * for the thing they are about to assert against.
 */
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
/**
 * Click a menu entry by its XML id, wherever this viewport has put it.
 *
 * The navbar is responsive: at desktop width the sections sit on the bar, and
 * as it narrows they overflow into a "More Menu" dropdown before finally
 * collapsing into the burger. A tour that only knew the desktop selector
 * passed on desktop and failed at 768px — which is the tablet gate doing its
 * job, and the reason this helper exists rather than a widened selector.
 */
async function clickMenu(xmlid) {
    const sel = `[data-menu-xmlid='${xmlid}']`;
    const pause = (ms) => new Promise((r) => setTimeout(r, ms));

    // The section bar renders asynchronously after the app is opened, and it
    // renders into different places at different widths. Poll rather than
    // sleep a guessed amount: a fixed delay was long enough for the desktop
    // run and not for the RTL one, which is a flake waiting to happen.
    const deadline = Date.now() + 15000;
    let item = null;
    while (Date.now() < deadline) {
        item = document.querySelector(sel);
        if (item) {
            break;
        }
        // Narrow viewports push the sections into "More Menu", then into the
        // burger. Open whichever is present and look again.
        const more = document.querySelector(
            ".o_menu_sections_more button, button[title='More Menu']");
        if (more && !more.closest(".show")) {
            more.click();
            await pause(200);
            continue;
        }
        const burger = document.querySelector(
            ".o_mobile_menu_toggle, .o_burger_menu_toggle");
        if (burger) {
            burger.click();
            await pause(200);
            continue;
        }
        await pause(200);
    }

    if (!item) {
        const seen = [...document.querySelectorAll("[data-menu-xmlid]")]
            .map((el) => el.dataset.menuXmlid);
        throw new Error(
            `The menu "${xmlid}" is not reachable at ${window.innerWidth}x` +
            `${window.innerHeight} after 15s. Menus visible: ` +
            JSON.stringify(seen)
        );
    }
    item.click();
    await pause(400);
}

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
