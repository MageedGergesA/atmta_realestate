/** @odoo-module **/

/**
 * Click a backend menu entry by XML id, wherever this viewport has put it.
 *
 * Shared by the M6 evaluation tour and the M7 award tour. It started life
 * inside the evaluation tour and moved here the moment a second tour needed
 * it: two copies of navigation logic drift, and the copy that drifts is the
 * one whose viewport nobody re-tests.
 *
 * The navbar is responsive. At desktop width the sections sit on the bar; as
 * it narrows they overflow into a "More Menu" dropdown, and narrower still
 * they collapse into the burger. A tour that only knew the desktop selector
 * passed on desktop and failed at 768px — which is the tablet gate doing its
 * job, and the reason this exists rather than a widened selector.
 *
 * It polls rather than sleeping a guessed interval: the section bar renders
 * asynchronously after the app opens, and a fixed delay was long enough for
 * the desktop run and not for the RTL one.
 */
export async function clickMenu(xmlid) {
    const sel = `[data-menu-xmlid='${xmlid}']`;
    const pause = (ms) => new Promise((r) => setTimeout(r, ms));

    const deadline = Date.now() + 15000;
    let item = null;
    while (Date.now() < deadline) {
        item = document.querySelector(sel);
        if (item) {
            break;
        }
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

/** Fail loudly if an error dialog is open — never silently continue past one. */
export function assertNoErrorDialog(label) {
    const dialog = document.querySelector(".o_error_dialog, .o_dialog_error");
    if (dialog) {
        const text = (dialog.innerText || "").split("\n").slice(0, 4).join(" | ");
        throw new Error(`[${label}] An error dialog is open: ${text}`);
    }
}

/** A rendered NaN is a bug that looks like a value. */
export function assertNoBrokenValues(label) {
    const el = document.querySelector(".o_content");
    const text = (el && el.textContent) || "";
    const broken = text.match(/NaN|Infinity|\[object Object\]/);
    if (broken) {
        throw new Error(`[${label}] Rendered a broken value: "${broken[0]}".`);
    }
}

/** Require text on screen, and say what was actually there when it is not. */
export function requireText(label, needles) {
    const el = document.querySelector(".o_content");
    const text = (el && el.textContent) || "";
    for (const needle of needles) {
        if (!text.includes(needle)) {
            const seen = text.replace(/\s+/g, " ").slice(0, 300);
            throw new Error(
                `[${label}] Expected to find "${needle}". Screen showed: ${seen}`
            );
        }
    }
}
