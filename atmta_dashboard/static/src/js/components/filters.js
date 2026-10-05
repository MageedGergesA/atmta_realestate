/** @odoo-module **/

import { Component } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";

/**
 * The global filter bar.
 *
 * Every dashboard in the suite gets the same one, because "why is this number
 * smaller than I expected" is nearly always a filter, and the answer has to be
 * visible without opening anything. Active filters are therefore also rendered
 * as removable chips next to the selectors.
 *
 * The filter *definitions* come from the backend: a dashboard that has no
 * concept of a building does not show a Building selector, and the options are
 * the records this user may actually read.
 */
export class AtmtaFilterBar extends Component {
    static template = "atmta_dashboard.FilterBar";
    static props = {
        filters: { type: Array },
        values: { type: Object },
        periods: { type: Array, optional: true },
        period: { type: String, optional: true },
        onChange: { type: Function },
        onPeriod: { type: Function, optional: true },
        onReset: { type: Function },
    };

    get periods() {
        return this.props.periods || [
            { key: "today", label: _t("Today") },
            { key: "week", label: _t("This Week") },
            { key: "month", label: _t("This Month") },
            { key: "quarter", label: _t("Quarter") },
            { key: "ytd", label: _t("YTD") },
            { key: "12m", label: _t("Last 12 Months") },
        ];
    }

    /** Only the filters that are actually narrowing something. */
    get chips() {
        const chips = [];
        for (const filter of this.props.filters) {
            const value = this.props.values[filter.key];
            if (!value || value === "all") {
                continue;
            }
            const option = (filter.options || []).find(
                (o) => String(o.key) === String(value));
            chips.push({
                key: filter.key,
                label: `${filter.label}: ${option ? option.label : value}`,
            });
        }
        return chips;
    }

    get hasActiveFilters() {
        return this.chips.length > 0;
    }

    /**
     * Whether an option is the selected one.
     *
     * Done here, not in the template: an OWL template is compiled against a
     * restricted context with no JavaScript globals, so a `String(...)` call
     * inside it throws `ctx.String is not a function` at render time and takes
     * the whole screen down with it. The coercion is still needed -- a filter
     * value arrives from a `<select>` as a string while the option key is a
     * database id.
     */
    isSelected(filter, option) {
        const current = this.props.values[filter.key];
        return current !== undefined && `${current}` === `${option.key}`;
    }

    onSelect(filterKey, ev) {
        this.props.onChange(filterKey, ev.target.value);
    }

    clear(filterKey) {
        this.props.onChange(filterKey, "all");
    }
}
