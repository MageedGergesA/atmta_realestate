/** @odoo-module **/

/**
 * M6 — an anonymous visitor's favourites, verified without a visitor.
 *
 * The rule under test is a negative one — *nothing leaves the browser* — so
 * the storage is a plain object here rather than a real `sessionStorage`. If a
 * future change made this file fetch anything, these tests would still pass and
 * the HTTP tests in `test_public_shortlist_conversion.py` would still be the
 * ones that catch it; between them, both halves of the rule are covered.
 */

import { describe, expect, test } from "@odoo/hoot";

import {
    attachShortlistToForm,
    paintShortlist,
    readShortlist,
    toggleShortlist,
} from "@real_estate_portal/js/public_shortlist";

/** The smallest thing that behaves like Storage. */
function fakeStorage(initial) {
    const data = { ...(initial || {}) };
    return {
        getItem: (key) => (key in data ? data[key] : null),
        setItem: (key, value) => { data[key] = String(value); },
        _data: data,
    };
}

/** A storage that refuses everything — private browsing, or a blocked store. */
function hostileStorage() {
    return {
        getItem: () => { throw new Error("blocked"); },
        setItem: () => { throw new Error("blocked"); },
    };
}

const KEY = "re_public_shortlist";

describe("the list", () => {
    test("starts empty", () => {
        expect(readShortlist(fakeStorage())).toEqual([]);
    });

    test("adds and removes the same id", () => {
        const storage = fakeStorage();

        expect(toggleShortlist(storage, 42)).toEqual([42]);
        expect(toggleShortlist(storage, 7)).toEqual([42, 7]);
        expect(toggleShortlist(storage, 42)).toEqual([7]);
    });

    test("survives rubbish somebody else left in storage", () => {
        expect(readShortlist(fakeStorage({ [KEY]: "not json" }))).toEqual([]);
        expect(readShortlist(fakeStorage({ [KEY]: '{"a":1}' }))).toEqual([]);
        expect(readShortlist(fakeStorage({ [KEY]: '[1,"2",0,-3,1,null]' })))
            .toEqual([1, 2]);
    });

    test("ignores an id that is not one", () => {
        const storage = fakeStorage();

        expect(toggleShortlist(storage, "nonsense")).toEqual([]);
        expect(toggleShortlist(storage, 0)).toEqual([]);
    });

    test("a blocked store says so rather than lying", () => {
        // Favourites are a convenience: losing them must never break the page.
        // What it must also never do is *claim* to have saved one. The result
        // is read back from storage, so a heart cannot light up over a list
        // the enquiry form will not carry.
        const storage = hostileStorage();

        expect(readShortlist(storage)).toEqual([]);
        expect(toggleShortlist(storage, 42)).toEqual([]);
    });
});

describe("the enquiry form", () => {
    test("carries the list at the moment send is pressed", () => {
        const storage = fakeStorage();
        toggleShortlist(storage, 11);
        toggleShortlist(storage, 12);
        const form = document.createElement("form");

        const value = attachShortlistToForm(form, storage);

        expect(value).toBe("11,12");
        expect(form.querySelector("input[name='shortlist']").value)
            .toBe("11,12");
    });

    test("does not add a second field when submitted twice", () => {
        const storage = fakeStorage();
        toggleShortlist(storage, 11);
        const form = document.createElement("form");

        attachShortlistToForm(form, storage);
        toggleShortlist(storage, 12);
        attachShortlistToForm(form, storage);

        expect(form.querySelectorAll("input[name='shortlist']").length).toBe(1);
        expect(form.querySelector("input[name='shortlist']").value)
            .toBe("11,12");
    });

    test("an empty list posts an empty field, not a stale one", () => {
        const storage = fakeStorage();
        toggleShortlist(storage, 11);
        const form = document.createElement("form");
        attachShortlistToForm(form, storage);

        toggleShortlist(storage, 11);   // unsaved again
        attachShortlistToForm(form, storage);

        expect(form.querySelector("input[name='shortlist']").value).toBe("");
    });
});

describe("what the visitor sees", () => {
    test("hearts and counters reflect the list", () => {
        const storage = fakeStorage();
        toggleShortlist(storage, 5);
        const root = document.createElement("div");
        root.innerHTML = `
            <button data-re-shortlist="5">
                <span data-re-shortlist-label="1">Save</span>
            </button>
            <button data-re-shortlist="6">
                <span data-re-shortlist-label="1">Save</span>
            </button>
            <span data-re-shortlist-count="1" class="d-none"></span>`;

        paintShortlist(root, storage);

        const [saved, notSaved] = root.querySelectorAll("[data-re-shortlist]");
        expect(saved.classList.contains("is-saved")).toBe(true);
        expect(saved.getAttribute("aria-pressed")).toBe("true");
        expect(saved.textContent.trim()).toBe("Saved");
        expect(notSaved.classList.contains("is-saved")).toBe(false);
        expect(notSaved.textContent.trim()).toBe("Save");

        const counter = root.querySelector("[data-re-shortlist-count]");
        expect(counter.textContent).toBe("1");
        expect(counter.classList.contains("d-none")).toBe(false);
    });

    test("an empty list hides the counter", () => {
        const root = document.createElement("div");
        root.innerHTML = `<span data-re-shortlist-count="1"></span>`;

        paintShortlist(root, fakeStorage());

        expect(root.querySelector("[data-re-shortlist-count]")
            .classList.contains("d-none")).toBe(true);
    });
});
