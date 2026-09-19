/** @odoo-module **/

/**
 * M6 — the favourites an anonymous visitor keeps.
 *
 * ### The rule
 *
 * **Browsing creates no CRM record.** A visitor may favourite eleven
 * apartments and leave, and nothing about that should appear in anybody's
 * pipeline. So the list lives in `sessionStorage` — in their browser, on their
 * device, gone when they close the tab — and no request is made when they tap
 * a heart.
 *
 * ### The one way out of the browser
 *
 * The enquiry form. When the visitor fills it in and presses send, the list
 * rides along in a hidden field, and the server folds it onto the lead that
 * submission creates. That is the explicit act the rule requires; there is no
 * other path, and this file makes no fetch of its own.
 *
 * ```
 *   tap heart  →  sessionStorage           (nothing leaves the browser)
 *   submit EOI →  hidden field  →  lead    (an act, with a name attached)
 * ```
 *
 * `sessionStorage` rather than `localStorage` deliberately: a shared showroom
 * tablet must not offer the next visitor the last one's favourites.
 */

const STORAGE_KEY = "re_public_shortlist";

// ---------------------------------------------------------------------------
// The list itself — pure functions over a storage object, so they can be
// tested without a browser session or a DOM.
// ---------------------------------------------------------------------------

/** Read the list, tolerating anything a user or an extension put there. */
export function readShortlist(storage) {
    let raw = null;
    try {
        raw = storage.getItem(STORAGE_KEY);
    } catch (_e) {
        // Private browsing, or storage disabled. Favourites are a convenience;
        // losing them must never break the page they sit on.
        return [];
    }
    if (!raw) {
        return [];
    }
    let parsed;
    try {
        parsed = JSON.parse(raw);
    } catch (_e) {
        return [];
    }
    if (!Array.isArray(parsed)) {
        return [];
    }
    // Ids only, de-duplicated, order preserved.
    const seen = new Set();
    const out = [];
    for (const value of parsed) {
        const id = parseInt(value, 10);
        if (id > 0 && !seen.has(id)) {
            seen.add(id);
            out.push(id);
        }
    }
    return out;
}

/**
 * Store the list and answer with what is *actually* stored.
 *
 * The read-back is the point. When the store is blocked — private browsing, a
 * quota, an extension — writing silently does nothing, and returning the list
 * we hoped to save would light the heart up while the enquiry form (which
 * re-reads storage) posted nothing. A visitor would be told their favourites
 * were saved and then watch them not arrive. Confirming through a read means
 * the heart stays dark, which is disappointing and true.
 */
export function writeShortlist(storage, ids) {
    try {
        storage.setItem(STORAGE_KEY, JSON.stringify(ids));
    } catch (_e) {
        // Nothing to recover; the visitor does not get favourites this
        // session, and the UI is about to say so.
    }
    return readShortlist(storage);
}

export function toggleShortlist(storage, propertyId) {
    const id = parseInt(propertyId, 10);
    if (!(id > 0)) {
        return readShortlist(storage);
    }
    const ids = readShortlist(storage);
    const at = ids.indexOf(id);
    if (at >= 0) {
        ids.splice(at, 1);
    } else {
        ids.push(id);
    }
    return writeShortlist(storage, ids);
}

export function isShortlisted(storage, propertyId) {
    return readShortlist(storage).includes(parseInt(propertyId, 10));
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

/** Reflect the current list onto every heart and counter on the page. */
export function paintShortlist(root, storage) {
    const ids = readShortlist(storage);
    for (const button of root.querySelectorAll("[data-re-shortlist]")) {
        const saved = ids.includes(
            parseInt(button.dataset.reShortlist, 10));
        button.classList.toggle("is-saved", saved);
        button.setAttribute("aria-pressed", saved ? "true" : "false");
        const label = button.querySelector("[data-re-shortlist-label]");
        if (label) {
            label.textContent = saved ? "Saved" : "Save";
        }
    }
    for (const counter of root.querySelectorAll("[data-re-shortlist-count]")) {
        counter.textContent = String(ids.length);
        counter.classList.toggle("d-none", !ids.length);
    }
    return ids;
}

/**
 * Put the list into every enquiry form on the page.
 *
 * Written at submit time rather than kept in sync as the visitor browses: the
 * value that matters is the one at the moment they pressed send.
 */
export function attachShortlistToForm(form, storage) {
    let field = form.querySelector("input[name='shortlist']");
    if (!field) {
        field = document.createElement("input");
        field.type = "hidden";
        field.name = "shortlist";
        form.appendChild(field);
    }
    field.value = readShortlist(storage).join(",");
    return field.value;
}

export function setupPublicShortlist(root, storage) {
    root.addEventListener("click", (ev) => {
        const button = ev.target.closest("[data-re-shortlist]");
        if (!button) {
            return;
        }
        ev.preventDefault();
        toggleShortlist(storage, button.dataset.reShortlist);
        paintShortlist(root, storage);
    });
    root.addEventListener("submit", (ev) => {
        const form = ev.target.closest(".o_re_eoi_form");
        if (form) {
            attachShortlistToForm(form, storage);
        }
    }, true);
    paintShortlist(root, storage);
}

document.addEventListener("DOMContentLoaded", () => {
    if (document.querySelector(".o_re_public_root")) {
        setupPublicShortlist(document, window.sessionStorage);
    }
});
