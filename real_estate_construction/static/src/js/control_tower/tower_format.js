/** @odoo-module **/

/**
 * The Control Tower's formatting rules.
 *
 * These live in their own module because they encode a reporting rule, not a
 * rendering detail: **a figure that does not exist is not zero.** An EAC
 * nobody has produced, a planned percentage the programme never published, a
 * pass rate with no inspections behind it -- each renders as N/A, never as
 * 0.00. They are pure functions so that distinction can be tested without a
 * server, a mount or a currency.
 */

import { formatMonetary } from "@web/views/fields/formatters";

/**
 * What a figure that does not exist renders as.
 *
 * Spelled here rather than imported from the dashboard component library on
 * purpose: these are pure functions, tested without a server, a mount or a
 * currency, and pulling in a component module just to borrow a string would
 * mean the unit tests could only run with the whole design system loaded.
 * The shared formatter uses the same word.
 */
const NOT_AVAILABLE = "N/A";

/** Format a monetary figure, or say plainly that there isn't one. */
export function money(value, currencyId) {
    if (value === null || value === undefined) {
        return NOT_AVAILABLE;
    }
    return formatMonetary(value, { currencyId });
}

/** Format a percentage, preserving the difference between 0% and unknown. */
export function percent(value, digits = 1) {
    if (value === null || value === undefined) {
        return NOT_AVAILABLE;
    }
    return `${Number(value).toFixed(digits)}%`;
}

export const STATUS_LABELS = {
    on_track: "On Track",
    attention: "Attention",
    at_risk: "At Risk",
    critical: "Critical",
    no_data: "No Data",
};
