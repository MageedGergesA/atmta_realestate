/** @odoo-module **/

import { Component } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";

import { formatDashboardValue } from "./kpi_card";

/**
 * A titled card. Everything below a KPI row sits in one of these so the page
 * has one card geometry rather than six.
 */
export class AtmtaCard extends Component {
    static template = "atmta_dashboard.Card";
    static props = {
        title: { type: String, optional: true },
        icon: { type: String, optional: true },
        subtitle: { type: String, optional: true },
        linkLabel: { type: String, optional: true },
        onLink: { type: Function, optional: true },
        slots: { type: Object, optional: true },
        className: { type: String, optional: true },
    };
}

/**
 * A compact queue of counted, clickable rows.
 *
 * Replaces a wall of single-number cards: eight rows take roughly a third of
 * the space eight cards do, and read faster because the numbers line up.
 *
 * Severity is applied only where the count is non-zero. "0 overdue payments"
 * painted red is noise that trains people to ignore red.
 */
export class AtmtaRowList extends Component {
    static template = "atmta_dashboard.RowList";
    static props = {
        rows: { type: Array },
        currencyId: { type: [Number, Boolean], optional: true },
        onRow: { type: Function, optional: true },
        emptyText: { type: String, optional: true },
    };

    get visibleRows() {
        return (this.props.rows || []).map((row) => ({
            ...row,
            formatted: row.format
                ? formatDashboardValue(row.value, row.format, this.props.currencyId)
                : row.value,
            // A row whose point is a VERDICT rather than a count renders a
            // chip instead of the count pill -- "At Risk" is not a quantity,
            // and an empty pill beside it reads as a missing number.
            badgeClass: row.badgeTone ? `o_ad_badge o_ad_badge_${row.badgeTone}` : "",
            // Severity shades the row by how bad it is. A verdict row has no
            // count, so it is shaded on the verdict instead of on `value`,
            // which would always be falsy and never shade.
            severityClass: (row.value || row.badge)
                ? this.severityClass(row.severity)
                : "",
        }));
    }

    severityClass(severity) {
        return {
            critical: "o_ad_row_critical",
            warning: "o_ad_row_warning",
        }[severity] || "";
    }

    get emptyText() {
        return this.props.emptyText || _t("Nothing needs your attention.");
    }

    onRow(row) {
        if (this.props.onRow && row.key) {
            this.props.onRow(row.key);
        }
    }

    onKeydown(ev, row) {
        if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            this.onRow(row);
        }
    }
}

/**
 * A ranked table: top properties, top agents, occupancy by unit type.
 *
 * Columns are described by the backend so a provider can add one without a
 * template change. A column may render as a number, a currency, a percentage,
 * a status badge or a meter bar.
 */
export class AtmtaDataTable extends Component {
    static template = "atmta_dashboard.DataTable";
    static props = {
        columns: { type: Array },
        rows: { type: Array },
        currencyId: { type: [Number, Boolean], optional: true },
        onRow: { type: Function, optional: true },
        emptyText: { type: String, optional: true },
    };

    cell(row, column) {
        const raw = row[column.key];
        if (column.type === "meter") {
            const pct = Math.max(0, Math.min(100, Number(raw) || 0));
            return { kind: "meter", pct, text: `${pct.toFixed(0)}%` };
        }
        if (column.type === "badge") {
            return { kind: "badge", text: raw, tone: row[`${column.key}_tone`] || "info" };
        }
        if (column.format) {
            return { kind: "text", text: formatDashboardValue(raw, column.format, this.props.currencyId) };
        }
        return { kind: "text", text: raw === false || raw === undefined ? "—" : raw };
    }

    get emptyText() {
        return this.props.emptyText || _t("No data for this period.");
    }

    onRow(row) {
        if (this.props.onRow && row.id) {
            this.props.onRow(row);
        }
    }
}

/**
 * The shape of the page while it loads.
 *
 * A spinner tells the reader to wait; a skeleton tells them what is coming and
 * stops the whole layout jumping when it lands. The Brokerage dashboard used a
 * white spinner on a white page, which looked exactly like a broken screen.
 */
export class AtmtaSkeleton extends Component {
    static template = "atmta_dashboard.Skeleton";
    static props = {
        kpis: { type: Number, optional: true },
        panels: { type: Number, optional: true },
    };
    static defaultProps = { kpis: 6, panels: 3 };

    get kpiRange() {
        return Array.from({ length: this.props.kpis }, (_v, i) => i);
    }

    get panelRange() {
        return Array.from({ length: this.props.panels }, (_v, i) => i);
    }
}

/**
 * An empty state that offers the way out of itself.
 *
 * "0" is a measurement; "No active leases / Create Lease" is an answer. The
 * difference matters most on a fresh database, which is the first thing a new
 * customer sees.
 */
export class AtmtaEmptyState extends Component {
    static template = "atmta_dashboard.EmptyState";
    static props = {
        icon: { type: String, optional: true },
        text: { type: String },
        actionLabel: { type: String, optional: true },
        onAction: { type: Function, optional: true },
    };
}
