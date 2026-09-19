/** @odoo-module **/

import { registry } from "@web/core/registry";

/**
 * Environment tours for the Treasury Dashboard.
 *
 * A stylesheet reading correctly is not evidence that a layout renders
 * correctly, and a payload being right is not evidence that the browser shows
 * it. Everything below is asserted against a live DOM in a real (headless)
 * Chrome, at a real viewport, with real computed styles: geometry comes from
 * getBoundingClientRect(), direction from getComputedStyle().
 *
 * The Python side (tests/test_dashboard_browser.py) varies the viewport, the
 * language, the company set and the user's role.
 */

// ---------------------------------------------------------------------------
// Shared assertions
// ---------------------------------------------------------------------------

const ROOT = ".o_re_check_dashboard";

function root() {
    const el = document.querySelector(ROOT);
    if (!el) {
        throw new Error("Treasury dashboard root not found");
    }
    return el;
}

/** Every number the dashboard prints must be a number. */
function assertNoBrokenNumbers() {
    const text = root().innerText || "";
    const broken = text.match(/NaN|undefined|Infinity|∞|\[object Object\]/);
    if (broken) {
        const line = text.split("\n").find((l) => l.includes(broken[0]));
        throw new Error(
            `Dashboard rendered a broken value: "${broken[0]}" in line "${line}". ` +
            "A KPI divided by zero or read a missing key."
        );
    }
}

/** The page must never scroll sideways, at any viewport, in any direction. */
function assertNoHorizontalOverflow(label) {
    const doc = document.documentElement;
    if (doc.scrollWidth > doc.clientWidth + 1) {
        throw new Error(
            `[${label}] Page overflows horizontally: scrollWidth=${doc.scrollWidth} ` +
            `clientWidth=${doc.clientWidth} (viewport ${window.innerWidth}px)`
        );
    }
    const el = root();
    if (el.scrollWidth > el.clientWidth + 1) {
        throw new Error(
            `[${label}] Dashboard overflows its container: ${el.scrollWidth} > ${el.clientWidth}`
        );
    }
}

/**
 * Charts must have real pixels and must not escape the dashboard box. A canvas
 * of zero width is the classic flex/height-auto failure and is invisible to a
 * CSS review.
 */
function assertChartsRendered(label) {
    const rootRect = root().getBoundingClientRect();
    const canvases = document.querySelectorAll(".o_re_check_chart canvas");
    if (!canvases.length) {
        throw new Error(`[${label}] No chart canvas rendered`);
    }
    for (const canvas of canvases) {
        const rect = canvas.getBoundingClientRect();
        if (rect.width < 40 || rect.height < 40) {
            throw new Error(
                `[${label}] Chart canvas collapsed: ${Math.round(rect.width)}x` +
                `${Math.round(rect.height)}px`
            );
        }
        if (rect.right > rootRect.right + 1 || rect.left < rootRect.left - 1) {
            throw new Error(
                `[${label}] Chart canvas escapes the dashboard box`
            );
        }
    }
}

/** No KPI card may be clipped by, or spill out of, the dashboard's own box. */
function assertCardsFit(label) {
    const rootRect = root().getBoundingClientRect();
    for (const card of document.querySelectorAll(".o_re_check_kpi")) {
        const rect = card.getBoundingClientRect();
        if (rect.width < 40 || rect.height < 20) {
            throw new Error(
                `[${label}] KPI card collapsed: ${Math.round(rect.width)}x` +
                `${Math.round(rect.height)}px`
            );
        }
        if (rect.right > rootRect.right + 1 || rect.left < rootRect.left - 1) {
            throw new Error(`[${label}] KPI card escapes the dashboard box`);
        }
    }
}

/**
 * The terminology gate, asserted in the rendered DOM rather than in source.
 *
 * These are the confusions the brief names explicitly. The dashboard may say
 * "PDC Amount Received (Face Value)"; it may never say "Collected Revenue".
 */
const FORBIDDEN_PHRASES = [
    "collected revenue",
    "revenue collected",
    "cash collected",
    "collections",
    "income received",
    "total revenue",
];

/**
 * A negated use is a disclaimer, not a claim.
 *
 * The dashboard says "PDC Amount Received (Face Value) … NOT cash collected"
 * on purpose — that sentence is the whole point of the legend, and a plain
 * substring test would fail the very wording it exists to require.
 */
const NEGATIONS = ["not ", "never ", "no ", "is not ", "are not ", "rather than "];

function claimsPhrase(text, phrase) {
    let index = text.indexOf(phrase);
    while (index !== -1) {
        const preceding = text.slice(Math.max(0, index - 24), index);
        if (!NEGATIONS.some((neg) => preceding.endsWith(neg))) {
            return true;
        }
        index = text.indexOf(phrase, index + 1);
    }
    return false;
}

function assertHonestTerminology(label) {
    const text = (root().innerText || "").toLowerCase();
    for (const phrase of FORBIDDEN_PHRASES) {
        if (claimsPhrase(text, phrase)) {
            throw new Error(
                `[${label}] Dashboard used forbidden wording "${phrase}". ` +
                "Paper received is not cash collected."
            );
        }
    }
    // The disclaimer must be present, not merely the absence of bad words.
    if (!text.includes("paper is not cash")) {
        throw new Error(`[${label}] The paper-vs-cash legend is missing`);
    }
    // Only the Cleared section may be badged Cash.
    const cashBadges = [...document.querySelectorAll(".o_re_check_kind")].filter(
        (b) => (b.innerText || "").trim().toLowerCase() === "cash"
    );
    for (const badge of cashBadges) {
        const card = badge.closest(".o_re_check_kpi");
        const cardLabel = (
            card.querySelector(".o_re_check_kpi_label")?.innerText || ""
        ).toLowerCase();
        if (!cardLabel.includes("cleared")) {
            throw new Error(
                `[${label}] A non-cleared KPI is badged Cash: "${cardLabel}". ` +
                "Only bank-confirmed money may be labelled Cash."
            );
        }
    }
    // "Deposited"/"At Bank" must never be badged Cash — checked above — and the
    // At-the-Bank section must exist and be badged Paper.
    const headings = [...document.querySelectorAll("h5")].map((h) =>
        (h.innerText || "").trim().toLowerCase()
    );
    if (!headings.includes("at the bank")) {
        throw new Error(`[${label}] The 'At the Bank' section is missing`);
    }
}

/** Wait until the dashboard body (not the spinner) is on screen. */
const waitForBody = {
    content: "The dashboard finished loading",
    trigger: ".o_re_check_body",
    run() {},
};

// ---------------------------------------------------------------------------
// 1. Layout — desktop and tablet
// ---------------------------------------------------------------------------

registry.category("web_tour.tours").add("atmta_treasury_dashboard_layout_tour", {
    url: "/odoo/action-real_estate_checks.action_check_dashboard",
    steps: () => [
        waitForBody,
        {
            content: "Charts, cards and totals all render inside the viewport",
            trigger: ".o_re_check_kpi",
            run() {
                const label = `${window.innerWidth}x${window.innerHeight}`;
                assertNoBrokenNumbers();
                assertNoHorizontalOverflow(label);
                assertCardsFit(label);
                assertChartsRendered(label);
                assertHonestTerminology(label);

                // Every section the brief asks for must be present.
                const text = root().innerText;
                for (const section of [
                    "On Hand",
                    "At the Bank",
                    "Cleared",
                    "Risk",
                    "PDC Coverage",
                    "Maturity Buckets",
                ]) {
                    if (!text.includes(section)) {
                        throw new Error(`[${label}] Missing section: ${section}`);
                    }
                }
                // All nine maturity buckets, always, even when empty.
                const buckets = document.querySelectorAll(
                    ".o_re_check_buckets tbody tr"
                );
                if (buckets.length !== 9) {
                    throw new Error(
                        `[${label}] Expected 9 maturity buckets, found ${buckets.length}`
                    );
                }
            },
        },
        {
            content: "Refresh reloads without breaking anything",
            trigger: ".o_re_check_refresh",
            run: "click",
        },
        {
            content: "The dashboard is still intact after a refresh",
            trigger: ".o_re_check_kpi",
            run() {
                assertNoBrokenNumbers();
                assertChartsRendered("after-refresh");
                assertHonestTerminology("after-refresh");
            },
        },
    ],
});

// ---------------------------------------------------------------------------
// 2. Values — the KPIs must show what the fixture planted
// ---------------------------------------------------------------------------

function kpiCount(key) {
    const card = document.querySelector(`[data-kpi="${key}"]`);
    if (!card) {
        throw new Error(`KPI card "${key}" not rendered`);
    }
    const el = card.querySelector(".o_re_check_kpi_count");
    return parseInt((el.innerText || "0").replace(/[^\d-]/g, ""), 10);
}

registry.category("web_tour.tours").add("atmta_treasury_dashboard_values_tour", {
    url: "/odoo/action-real_estate_checks.action_check_dashboard",
    steps: () => [
        waitForBody,
        {
            content: "Every KPI shows the value the fixture planted",
            trigger: "[data-kpi='on_hand']",
            run() {
                // The fixture (see test_dashboard_browser.py) plants:
                //   4 on hand — 1 due today, 1 due in 5 days, 2 further out
                //   2 at the bank, of which 1 cleared
                //   1 bounced
                const expectations = {
                    on_hand: 4,
                    due_today: 1,
                    in_clearing: 1,
                    cleared_total: 1,
                    bounced: 1,
                };
                for (const [key, expected] of Object.entries(expectations)) {
                    const actual = kpiCount(key);
                    if (actual !== expected) {
                        throw new Error(
                            `KPI "${key}" shows ${actual}, expected ${expected}`
                        );
                    }
                }

                // PDC coverage must be a real percentage, not a placeholder.
                const coverage = document.querySelector(
                    "[data-kpi='coverage_percent'] .o_re_check_kpi_percent"
                );
                const value = parseFloat(coverage.innerText);
                if (!(value >= 0 && value <= 100)) {
                    throw new Error(`PDC coverage is not a percentage: ${coverage.innerText}`);
                }

                // Bucket counts must sum to the on-hand count: the forecast
                // covers cheques on hand and nothing else.
                const bucketTotal = [
                    ...document.querySelectorAll(".o_re_check_bucket_count"),
                ].reduce((sum, el) => sum + parseInt(el.innerText || "0", 10), 0);
                if (bucketTotal !== 4) {
                    throw new Error(
                        `Maturity buckets total ${bucketTotal}, expected 4 (cheques on hand)`
                    );
                }
            },
        },
        {
            content: "Drill into 'Cheques On Hand'",
            trigger: "[data-kpi='on_hand']",
            run: "click",
        },
        {
            content: "The list holds exactly as many records as the KPI claimed",
            trigger: ".o_list_view .o_data_row",
            run() {
                const rows = document.querySelectorAll(
                    ".o_list_view .o_data_row"
                ).length;
                if (rows !== 4) {
                    throw new Error(
                        `Drill-down shows ${rows} cheques but the KPI said 4. ` +
                        "A count and its drill-down must be the same domain."
                    );
                }
            },
        },
    ],
});

// ---------------------------------------------------------------------------
// 3. RTL — an actually-Arabic session
// ---------------------------------------------------------------------------

registry.category("web_tour.tours").add("atmta_treasury_dashboard_rtl_tour", {
    url: "/odoo/action-real_estate_checks.action_check_dashboard",
    steps: () => [
        waitForBody,
        {
            content: "The session really is RTL and the dashboard is mirrored",
            trigger: ".o_re_check_dashboard",
            run() {
                const el = root();
                const direction = getComputedStyle(el).direction;
                if (direction !== "rtl") {
                    throw new Error(
                        `Dashboard computed direction is "${direction}", not "rtl". ` +
                        "Odoo sets direction on .o_action_manager and rtlcss " +
                        "rewrites it; an explicit dir attribute would break that."
                    );
                }

                // Mirroring, proved by geometry rather than by the property.
                const card = document.querySelector(".o_re_check_kpi");
                const cardRect = card.getBoundingClientRect();
                const rootRect = el.getBoundingClientRect();
                const gapStart = rootRect.right - cardRect.right;
                const gapEnd = cardRect.left - rootRect.left;
                if (gapStart > gapEnd) {
                    throw new Error(
                        "In RTL the first KPI card should hug the RIGHT edge; " +
                        `it is ${Math.round(gapStart)}px from the right and ` +
                        `${Math.round(gapEnd)}px from the left.`
                    );
                }

                assertNoBrokenNumbers();
                assertNoHorizontalOverflow("rtl");
                assertCardsFit("rtl");
                assertChartsRendered("rtl");
            },
        },
    ],
});

// ---------------------------------------------------------------------------
// 4. Empty company — zero data must render as zero
// ---------------------------------------------------------------------------

registry.category("web_tour.tours").add("atmta_treasury_dashboard_empty_tour", {
    url: "/odoo/action-real_estate_checks.action_check_dashboard",
    steps: () => [
        waitForBody,
        {
            content: "An empty company renders zeroes, not errors",
            trigger: "[data-kpi='on_hand']",
            run() {
                assertNoBrokenNumbers();
                assertNoHorizontalOverflow("empty");
                assertChartsRendered("empty");
                assertHonestTerminology("empty");

                for (const key of ["on_hand", "in_clearing", "cleared_total", "bounced"]) {
                    const value = kpiCount(key);
                    if (value !== 0) {
                        throw new Error(`KPI "${key}" is ${value} in an empty company`);
                    }
                }
                // A zero denominator must not produce NaN — this is the whole
                // point of the empty-company case.
                const rate = document.querySelector(
                    "[data-kpi='bounce_rate'] .o_re_check_kpi_percent"
                ).innerText;
                if (!/^0\.0%$/.test(rate.trim())) {
                    throw new Error(`Bounce rate on an empty company reads "${rate}"`);
                }
                // Still nine buckets, all at zero.
                const buckets = document.querySelectorAll(".o_re_check_buckets tbody tr");
                if (buckets.length !== 9) {
                    throw new Error(`Expected 9 buckets, found ${buckets.length}`);
                }
            },
        },
    ],
});

// ---------------------------------------------------------------------------
// 5. Multi-company — the native switcher, and no leakage
// ---------------------------------------------------------------------------

registry.category("web_tour.tours").add("atmta_treasury_dashboard_company_tour", {
    url: "/odoo/action-real_estate_checks.action_check_dashboard",
    steps: () => [
        waitForBody,
        {
            content: "Company A shows only Company A's cheques",
            trigger: "[data-kpi='on_hand']",
            run() {
                const value = kpiCount("on_hand");
                if (value !== 3) {
                    throw new Error(
                        `Company A should show 3 cheques on hand, shows ${value}. ` +
                        "A different number means the dashboard leaked or ignored " +
                        "the active company."
                    );
                }
                window.__atmtaCompanyA = value;
            },
        },
        {
            content: "Open the company switcher",
            trigger: ".o_switch_company_menu button",
            run: "click",
        },
        {
            // Odoo 18 renders dropdown menus in a portal at the end of <body>,
            // not inside the toggle's own element.
            content: "Both companies are offered",
            trigger: ".o-dropdown--menu .o_switch_company_item",
            run() {
                const text = document.querySelector(".o-dropdown--menu").innerText;
                for (const name of ["ATMTA Treasury A", "ATMTA Treasury B"]) {
                    if (!text.includes(name)) {
                        throw new Error(`Company switcher does not offer ${name}`);
                    }
                }
            },
        },
        {
            content: "Add Company B to the active set",
            // The checkbox inside the item toggles a company into the active
            // set; clicking the label would switch to it instead.
            trigger:
                ".o-dropdown--menu .o_switch_company_item:contains('ATMTA Treasury B') [role='menuitemcheckbox']",
            run: "click",
        },
        {
            // Toggling only STAGES the selection in Odoo 18; `apply()` runs on
            // Confirm. Without this the dashboard would be re-read with the
            // old company set and the test would pass or fail for the wrong
            // reason.
            content: "Confirm the new company selection",
            trigger: ".o_switch_company_menu_buttons button:contains('Confirm')",
            run: "click",
        },
        {
            //  TEST RELIABILITY PATCH — no production behaviour change.
            //
            //  Confirm triggers a full web-client reload. The KPI selector
            //  below matches the *old* DOM just as well as the new one, so
            //  without this step the assertion could run against the
            //  pre-reload dashboard and read Company A's 3 instead of the
            //  combined 10. That is what made this tour intermittent: it was
            //  a race, not a wrong number.
            //
            //  The fix is to wait for an observable application state rather
            //  than for time to pass. A step with a trigger and no `run` is
            //  Odoo's own idiom for "wait until this is true".
            //
            //  The synchronisation point is the *rendered outcome*: the
            //  dashboard showing the combined figure. A trigger only matches
            //  once that is true, so the assertion below cannot run against
            //  the pre-reload DOM.
            //
            //  Two other observables were tried first and neither works:
            //
            //    * `.o_switch_company_menu` — shows only the *current*
            //      company (`companyService.currentCompany.name`), never the
            //      active set, so it reads "ATMTA Treasury A" throughout.
            //    * reopening the dropdown and checking B's
            //      `aria-checked='true'` — the reopen click races the reload
            //      it is waiting for, and lands on the old page.
            //
            //  The KPI is unambiguous here: on-hand is 3 for company A alone
            //  and 10 for A+B, and no other number on the card can match.
            content: "Wait until the dashboard has re-rendered for both companies",
            trigger: "[data-kpi='on_hand'] .o_re_check_kpi_count:contains('10')",
        },
        {
            content: "With both companies active the figure is the sum",
            trigger: "[data-kpi='on_hand'] .o_re_check_kpi_count",
            run() {
                const value = kpiCount("on_hand");
                if (value !== 10) {
                    throw new Error(
                        "With A+B active the dashboard should show 10 cheques " +
                        `on hand, shows ${value}. Active companies per the ` +
                        `switcher: "${
                            document.querySelector(".o_switch_company_menu")
                                ?.innerText || "?"
                        }". Legend: "${
                            document.querySelector(".o_re_check_legend")
                                ? "rendered"
                                : "missing"
                        }".`
                    );
                }
            },
        },
    ],
});

// ---------------------------------------------------------------------------
// 6. Roles — what each Treasury level sees
// ---------------------------------------------------------------------------

/**
 * Roles.
 *
 * Two tours rather than one parameterised tour: a tour receives no arguments,
 * and a URL query string does not survive the web client's own navigation, so
 * the expectation is baked into the tour's name instead.
 */
function roleSteps(expectBankDetails) {
    return [
        waitForBody,
        {
            content: "The dashboard renders for this role without a crash",
            trigger: "[data-kpi='on_hand']",
            run() {
                assertNoBrokenNumbers();
                assertHonestTerminology("role");
                assertChartsRendered("role");
            },
        },
        {
            content: "Drill into the cheque list",
            trigger: "[data-kpi='on_hand']",
            run: "click",
        },
        {
            content: "Open a cheque",
            trigger: ".o_list_view .o_data_row:first-child .o_data_cell",
            run: "click",
        },
        {
            content: "Sensitive fields obey the role (M32)",
            trigger: ".o_form_view",
            run() {
                // `groups=` on a field makes Odoo strip it from the arch
                // entirely for users without the group, so presence in the DOM
                // is a direct test of the access rule rather than of CSS.
                // The three bank references, which sit in the always-visible
                // top group of the form. `notes` is deliberately NOT checked
                // here: it lives on an inactive notebook page, and Odoo 18
                // renders notebook pages lazily, so its absence from the DOM
                // would prove nothing. The served arch is asserted instead by
                // `test_reports_and_access.py`, which is the stronger test.
                const sensitive = [
                    "account_number",
                    "branch",
                    "partner_bank_id",
                ];
                for (const name of sensitive) {
                    // `.o_field_widget` scopes this to the FIELD. A notebook
                    // page also carries a `name` attribute, and one of these
                    // pages is called "notes".
                    const present = !!document.querySelector(
                        `.o_form_view .o_field_widget[name="${name}"]`
                    );
                    if (present !== expectBankDetails) {
                        throw new Error(
                            `Field "${name}" is ${present ? "visible" : "hidden"} ` +
                            `but this role expects it ` +
                            `${expectBankDetails ? "visible" : "hidden"}. Bank ` +
                            "account references and treasury notes are " +
                            "Treasury-only."
                        );
                    }
                }

                // Custody history is equally Treasury's.
                const custodyRows = document.querySelectorAll(
                    "[name='custody_ids'] .o_data_row"
                ).length;
                if (!expectBankDetails && custodyRows) {
                    throw new Error(
                        "A restricted Checks User can read custody movements"
                    );
                }
            },
        },
    ];
}

registry.category("web_tour.tours").add("atmta_treasury_dashboard_role_restricted_tour", {
    url: "/odoo/action-real_estate_checks.action_check_dashboard",
    steps: () => roleSteps(false),
});

registry.category("web_tour.tours").add("atmta_treasury_dashboard_role_treasury_tour", {
    url: "/odoo/action-real_estate_checks.action_check_dashboard",
    steps: () => roleSteps(true),
});
