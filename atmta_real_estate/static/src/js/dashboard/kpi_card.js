/** @odoo-module **/

import { Component } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { formatFloat, formatInteger, formatMonetary, formatPercentage } from "@web/views/fields/formatters";

import { FORMAT } from "./dashboard_schema";

/**
 * Format a dashboard value using Odoo's own formatters.
 *
 * Deliberately NOT `toLocaleString()`: Odoo's formatters honour the user's
 * language, the company's decimal/thousands separators, and — for monetary
 * values — the currency's own precision and symbol position. Hand-rolling that
 * is how a dashboard ends up showing a rounded figure that disagrees with the
 * invoice behind it.
 *
 * @param {number} value
 * @param {string} format one of FORMAT.*
 * @param {object} [options]
 * @param {number} [options.currencyId] required for FORMAT.MONETARY
 * @returns {string}
 */
export function formatKpiValue(value, format, options = {}) {
    const number = Number.isFinite(value) ? value : 0;
    switch (format) {
        case FORMAT.MONETARY:
            // `noSymbol` is not used: the symbol carries meaning on a
            // multi-currency dashboard.
            return formatMonetary(number, { currencyId: options.currencyId });
        case FORMAT.PERCENT:
            // The backend already publishes percentages as 0-100, whereas
            // Odoo's formatPercentage expects a 0-1 ratio.
            return formatPercentage(number / 100, { digits: [false, 1] });
        case FORMAT.DECIMAL:
            return formatFloat(number, { digits: [false, 1] });
        case FORMAT.DATE:
            return value || "";
        case FORMAT.INTEGER:
        default:
            return formatInteger(number);
    }
}

/**
 * One KPI tile.
 *
 * Presentation only — it receives an already-normalised descriptor and never
 * reaches into the payload or recomputes a business figure.
 */
export class KpiCard extends Component {
    static template = "atmta_real_estate.KpiCard";
    static props = {
        label: { type: String },
        value: { type: [Number, String] },
        formattedValue: { type: String },
        hint: { type: String, optional: true },
        tone: { type: String, optional: true },
        warning: { type: Boolean, optional: true },
        progress: { type: Number, optional: true },
        clickable: { type: Boolean, optional: true },
        onDrill: { type: Function, optional: true },
    };

    /**
     * Click handler.
     *
     * Deliberately a named method rather than an inline template expression:
     * the guard is logic, and logic belongs in JS where it can be read and
     * tested. An inline `t-on-click` arrow also proved fragile in practice.
     */
    onCardClick() {
        if (this.props.clickable && this.props.onDrill) {
            this.props.onDrill();
        }
    }

    /**
     * Interactive cards must behave like buttons for keyboard users, not just
     * for the mouse.
     */
    onKeydown(ev) {
        if (!this.props.clickable) {
            return;
        }
        if (ev.key === "Enter" || ev.key === " " || ev.key === "Spacebar") {
            ev.preventDefault();
            this.onCardClick();
        }
    }

    /**
     * Status is never communicated by colour alone — a warning card also
     * carries an icon and an accessible label.
     */
    get statusLabel() {
        if (this.props.warning) {
            return _t("Needs attention");
        }
        return "";
    }

    get ariaLabel() {
        const parts = [this.props.label, this.props.formattedValue];
        if (this.props.warning) {
            parts.push(this.statusLabel);
        }
        if (this.props.clickable) {
            parts.push(_t("Opens the underlying records"));
        }
        return parts.join(". ");
    }
}
