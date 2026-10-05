/** @odoo-module **/

import { Component } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { formatFloat, formatInteger, formatMonetary, formatPercentage } from "@web/views/fields/formatters";

import { Sparkline } from "./sparkline";

/** What a figure that does not exist is rendered as. Never "0". */
export const NOT_AVAILABLE = _t("N/A");

/**
 * Format a figure with Odoo's own formatters, so separators, currency symbol
 * position and precision follow the user's language and the company currency.
 *
 * `null` and `undefined` are NOT zero, and this is the one place that
 * distinction can be lost for the whole suite. An estimate at completion
 * nobody has produced, a planned percentage the programme never published, a
 * first-pass yield with no inspections behind it -- printing any of those as
 * "$0.00" or "0.0%" states something false about the project, and it is the
 * kind of false statement somebody acts on. They render as N/A instead.
 *
 * A provider that genuinely means zero passes 0, which formats normally.
 */
export function formatDashboardValue(value, format, currencyId) {
    if (value === null || value === undefined || Number.isNaN(value)) {
        return NOT_AVAILABLE;
    }
    const number = Number.isFinite(value) ? value : 0;
    switch (format) {
        case "monetary":
            return formatMonetary(number, { currencyId });
        case "percent":
            // Providers publish percentages as 0-100.
            return formatPercentage(number / 100, { digits: [false, 1] });
        case "decimal":
            return formatFloat(number, { digits: [false, 1] });
        default:
            return formatInteger(number);
    }
}

/**
 * One metric, with the context needed to judge it.
 *
 * A bare number answers "what", never "is that good". The card therefore also
 * carries the change against the comparison period and a sparkline of the
 * recent trend, and it is told by the backend (`higher_is_better`) which
 * direction is the good one. That flag is not cosmetic: occupancy up is green
 * and outstanding rent up is red, and guessing from the sign alone gets the
 * second one exactly backwards.
 */
export class AtmtaKpiCard extends Component {
    static template = "atmta_dashboard.KpiCard";
    static components = { Sparkline };
    static props = {
        tile: Object,
        currencyId: { type: [Number, Boolean], optional: true },
        onDrill: { type: Function, optional: true },
    };

    get tile() {
        return this.props.tile;
    }

    get formattedValue() {
        const value = formatDashboardValue(this.tile.value, this.tile.format, this.props.currencyId);
        // A unit the format cannot express -- "yrs" on a payback period.
        return this.tile.suffix ? `${value}${this.tile.suffix}` : value;
    }

    get tone() {
        return this.tile.tone || "primary";
    }

    /**
     * The root's full class list, built here rather than in the template.
     *
     * OWL applies `t-attf-class` and `t-att-class` to the same node by
     * concatenation, which works but makes a name collision between the two
     * sources invisible -- which is how the `warning` TONE and the
     * needs-attention STATE ended up sharing a class. One getter, one source.
     */
    get rootClass() {
        const classes = ["o_ad_kpi", `o_ad_tone_${this.tone}`];
        if (this.tile.drill) {
            classes.push("o_ad_kpi_clickable");
        }
        if (this.tile.warning) {
            classes.push("o_ad_kpi_warning");
        }
        if (this.tile.pending) {
            // Money that is NOT authorised: an open change event, a claim
            // nobody has determined. It is drawn with a dashed edge so it
            // cannot be mistaken at a glance for a baseline figure, because
            // the one mistake this costs is adding it to the budget.
            classes.push("o_ad_kpi_pending");
        }
        return classes.join(" ");
    }

    get icon() {
        return this.tile.icon || "fa-th-large";
    }

    get hasDelta() {
        return Number.isFinite(this.tile.delta_percent);
    }

    /**
     * The delta's business meaning, not its arithmetic sign.
     *
     * `higher_is_better` defaults to true because most tiles count good things,
     * but any tile that counts a problem -- arrears, overdue items, vacancy --
     * sets it false and gets the opposite colour for the same arrow.
     */
    get deltaClass() {
        if (!this.hasDelta || Math.abs(this.tile.delta_percent) < 0.05) {
            return "o_ad_delta_flat";
        }
        const better = this.tile.higher_is_better !== false;
        const rising = this.tile.delta_percent > 0;
        return rising === better ? "o_ad_delta_good" : "o_ad_delta_bad";
    }

    get deltaIcon() {
        if (!this.hasDelta || Math.abs(this.tile.delta_percent) < 0.05) {
            return "fa-minus";
        }
        return this.tile.delta_percent > 0 ? "fa-arrow-up" : "fa-arrow-down";
    }

    get deltaLabel() {
        if (!this.hasDelta) {
            return "";
        }
        const value = Math.abs(this.tile.delta_percent);
        return `${value.toFixed(1)}%`;
    }

    get comparisonCaption() {
        return this.tile.comparison_label || _t("vs previous period");
    }

    /**
     * An optional qualifier on what the number IS, not how big it is.
     *
     * Treasury uses it to mark every figure as paper or as cash, because a
     * cheque on hand and a cleared one are different kinds of thing and
     * adding them is the usual way a PDC-heavy business overstates itself.
     * Rendered as a word, never as a colour alone.
     */
    get qualifier() {
        const measure = this.tile.qualifier;
        if (!measure) {
            return null;
        }
        return {
            paper: { label: _t("Paper"), tone: "neutral" },
            cash: { label: _t("Cash"), tone: "success" },
            // Investment appraisal. An NPV is the output of a model, not of
            // the ledger, and labelling it the way the suite labels collected
            // cash is how a forecast gets quoted as a fact.
            modelled: { label: _t("Modelled"), tone: "info" },
            // Construction cost control. "Posted" is the counterpart of
            // "Modelled": it has reached the analytic ledger, so it is spent
            // rather than projected.
            posted: { label: _t("Posted"), tone: "neutral" },
        }[measure] || { label: measure, tone: "info" };
    }

    get sparkTone() {
        return this.deltaClass === "o_ad_delta_bad" ? "danger" : this.tone;
    }

    get ariaLabel() {
        const parts = [this.tile.label, this.formattedValue];
        if (this.hasDelta) {
            parts.push(
                this.tile.delta_percent > 0
                    ? _t("up %s %s", this.deltaLabel, this.comparisonCaption)
                    : _t("down %s %s", this.deltaLabel, this.comparisonCaption)
            );
        }
        if (this.tile.warning) {
            parts.push(_t("Needs attention"));
        }
        if (this.tile.drill) {
            parts.push(_t("Opens the underlying records"));
        }
        return parts.join(". ");
    }

    onClick() {
        if (this.tile.drill && this.props.onDrill) {
            this.props.onDrill(this.tile.key);
        }
    }

    onKeydown(ev) {
        if (this.tile.drill && (ev.key === "Enter" || ev.key === " ")) {
            ev.preventDefault();
            this.onClick();
        }
    }
}
