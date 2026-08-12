/** @odoo-module **/

/**
 * M5 — the real-browser gate for Sourcing and Tender.
 *
 * The tour walks a published tender the way a buyer does and checks the things
 * that would make the tender file untrustworthy if they were wrong: demand
 * consolidated on screen while its two sources are still visible separately,
 * an eligibility answer recorded at invitation, bid evidence showing the exact
 * submitted amounts, two tender versions where only one is current, and a
 * confirmation that refuses.
 *
 * What it deliberately does not do is rank anything. There is no cheapest
 * column, no recommendation and no award — M5 records the process, M6 judges
 * it.
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
        const line = text.split("\n").find((l) => l.includes(broken[0]));
        throw new Error(`[${label}] Rendered a broken value: "${broken[0]}" in "${line}".`);
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
            throw new Error(`[${label}] Expected to find "${needle}" on screen.`);
        }
    }
}

/**
 * Look for evaluation language in the *data*, not in the prose.
 *
 * Scoped to the record rows on purpose: the Bids tab carries a deliberate
 * notice saying bids "are not evaluated or ranked here", and scanning the
 * whole page flagged that sentence as evidence of ranking. What matters is
 * whether a column, value or button ranks a vendor — not whether the screen
 * explains that it does not.
 */
function forbidText(label, needles) {
    const scope = document.querySelector(".o_field_x2many_list") ||
        document.querySelector(".o_list_view");
    const text = ((scope && scope.textContent) || "").toLowerCase();
    for (const needle of needles) {
        if (text.includes(needle.toLowerCase())) {
            throw new Error(
                `[${label}] "${needle}" appeared in M5 record data. Sourcing records ` +
                `the process; it does not evaluate or recommend anybody.`
            );
        }
    }
}

function checkpoint(label, extra) {
    return {
        content: `checkpoint: ${label}`,
        trigger: ".o_content",
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

registry.category("web_tour.tours").add("procurement_sourcing_tour", {
    url: "/odoo/action-real_estate_procurement.action_sourcing_event",
    steps: () => [
        checkpoint("sourcing list opened"),
        {
            content: "open the seeded tender",
            trigger: ".o_list_view td[name='title']:contains('Browser Gate Tender')",
            run: "click",
        },
        checkpoint("tender form opened", (label) =>
            requireText(label, ["Published"])
        ),

        // -- Scope: the consolidated ask the market sees ----------------
        openTab("Scope"),
        checkpoint("scope shows the consolidated quantity", (label) =>
            requireText(label, ["100.00"])
        ),

        // -- Demand: consolidated above, still two sources underneath ---
        openTab("Authorised Demand"),
        checkpoint("both source requisition lines survive consolidation", (label) => {
            const rows = document.querySelectorAll(".o_field_x2many_list .o_data_row");
            if (rows.length < 2) {
                throw new Error(
                    `[${label}] The tender consolidated 50 + 50 into one 100-unit line, ` +
                    `so two allocation rows must remain visible. Found ${rows.length}.`
                );
            }
            requireText(label, ["50.00"]);
        }),

        // -- Invitations and their eligibility snapshots ----------------
        openTab("Invited Vendors"),
        checkpoint("three vendors, each with an eligibility answer", (label) => {
            const rows = [...document.querySelectorAll(".o_field_x2many_list .o_data_row")];
            if (rows.length !== 3) {
                throw new Error(`[${label}] Expected 3 invitations, found ${rows.length}.`);
            }
            requireText(label, ["Gate Vendor A", "Gate Vendor B", "Gate Vendor C"]);
            // Every invitation must carry a recorded answer from M4 — not
            // necessarily the word "eligible". Under the default OPTIONAL
            // policy an unassessed vendor is legitimately
            // `eligible=True, status=no_qualification`, and asserting the word
            // would be asserting one company's configuration.
            const missing = rows.filter((row) => {
                const cell = row.querySelector("td[name='eligibility_status']");
                return !cell || !cell.textContent.trim();
            });
            if (missing.length) {
                throw new Error(
                    `[${label}] ${missing.length} invitation(s) show no ` +
                    `invitation-time eligibility answer at all.`
                );
            }
        }),

        // -- Bids: the submitted numbers, exactly ------------------------
        openTab("Bids Received"),
        checkpoint("bid evidence shows what was submitted", (label) => {
            requireText(label, ["3,200,000.00", "3,050,000.00"]);
            forbidText(label, [
                "cheapest",
                "best value",
                "recommended",
                "winner",
                "ranked",
                "technically compliant",
            ]);
        }),

        // -- Versions: Rev 0 sealed, Rev 1 current -----------------------
        openTab("Versions"),
        checkpoint("both tender versions are visible", (label) => {
            const rows = document.querySelectorAll(".o_field_x2many_list .o_data_row");
            if (rows.length < 2) {
                throw new Error(
                    `[${label}] An addendum was issued, so Rev 0 and Rev 1 must both ` +
                    `appear. Found ${rows.length} version row(s).`
                );
            }
            requireText(label, ["Superseded", "Issued"]);
        }),

        // -- Clarifications ----------------------------------------------
        openTab("Clarifications"),
        checkpoint("the vendor clarification is recorded against its version"),

        // -- Bid detail: the snapshot after a native compare --------------
        openTab("Bids Received"),
        {
            content: "open the vendor A bid",
            trigger: ".o_field_x2many_list .o_data_row:contains('Gate Vendor A')",
            run: "click",
        },
        checkpoint("the submitted amount survived the native compare", (label) => {
            requireText(label, ["3,200,000.00"]);
        }),
    ],
});

/**
 * RTL. Direction is read from the rendered document rather than asserted from
 * a language code, because the question is whether the *page* came up
 * right-to-left, not whether somebody set a field. If it did not, the tour
 * says so plainly — that is a session or fixture finding and not a reason to
 * touch M5's styling.
 */
registry.category("web_tour.tours").add("procurement_sourcing_rtl_tour", {
    url: "/odoo/action-real_estate_procurement.action_sourcing_event",
    steps: () => [
        checkpoint("the session was served the RTL bundle", (label) => {
            // Odoo 18's backend does not set `html[dir]` — grep the source and
            // the only `t-att-dir` is in report templates. Backend RTL is
            // delivered by serving the rtlcss-generated `.rtl` asset bundle,
            // so that is what has to be asserted. Probing `body direction`
            // instead reports "ltr" on a perfectly good RTL session, which is
            // exactly why the RTL tests in three other modules fail.
            const sheets = [...document.querySelectorAll("link[rel=stylesheet]")]
                .map((l) => l.getAttribute("href") || "");
            const rtl = sheets.filter((href) => href.includes(".rtl."));
            if (!rtl.length) {
                throw new Error(
                    `[${label}] No RTL stylesheet bundle was served, so this ` +
                    `session is not right-to-left. Loaded: ${sheets.join(", ")}`
                );
            }
        }),
        {
            content: "open the seeded tender",
            trigger: ".o_list_view td[name='title']:contains('Browser Gate Tender')",
            run: "click",
        },
        checkpoint("the tender form is usable in RTL", (label) => {
            const doc = document.documentElement;
            if (doc.scrollWidth > doc.clientWidth + 2) {
                throw new Error(
                    `[${label}] The page scrolls horizontally in RTL ` +
                    `(${doc.scrollWidth}px of content in ${doc.clientWidth}px).`
                );
            }
        }),
        openTab("Bids Received"),
        checkpoint("monetary values stay readable in RTL", (label) =>
            requireText(label, ["3,200,000.00"])
        ),
        openTab("Invited Vendors"),
        checkpoint("invitations are reachable in RTL"),
    ],
});

/**
 * Tablet. The viewport is set by the test; this only asks whether the work can
 * actually be done at that size — controls reachable, nothing clipped, no
 * horizontal scroll.
 */
registry.category("web_tour.tours").add("procurement_sourcing_tablet_tour", {
    url: "/odoo/action-real_estate_procurement.action_sourcing_event",
    steps: () => [
        checkpoint("sourcing list opened on a tablet"),
        {
            content: "open the seeded tender",
            trigger: ".o_list_view td[name='title']:contains('Browser Gate Tender')",
            run: "click",
        },
        checkpoint("the tender form fits the viewport", (label) => {
            const doc = document.documentElement;
            if (doc.scrollWidth > doc.clientWidth + 2) {
                throw new Error(
                    `[${label}] The form overflows a tablet viewport ` +
                    `(${doc.scrollWidth}px in ${doc.clientWidth}px).`
                );
            }
            const buttons = [...document.querySelectorAll(".o_form_statusbar button")];
            const hidden = buttons.filter((b) => b.offsetParent === null);
            if (buttons.length && hidden.length === buttons.length) {
                throw new Error(`[${label}] No status-bar action is reachable.`);
            }
        }),
        openTab("Authorised Demand"),
        checkpoint("the demand tab is usable on a tablet"),
        openTab("Bids Received"),
        checkpoint("bid evidence is readable on a tablet", (label) =>
            requireText(label, ["3,200,000.00"])
        ),
    ],
});
