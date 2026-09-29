/** @odoo-module **/

/**
 * Rental Dashboard — presentation schema.
 *
 * The backend owns every tile: which ones a user sees, their labels, values,
 * formats and drilldowns (`realestate.rental.dashboard.get_work`). This file
 * only maps section ids to icons and describes the trend charts, whose data
 * arrives separately from `get_trends`.
 */

import { _t } from "@web/core/l10n/translation";

/** Value formats. Resolved by `formatKpiValue` in kpi_card.js. */
export const FORMAT = {
    INTEGER: "integer",
    MONETARY: "monetary",
    PERCENT: "percent",
    DECIMAL: "decimal",
    DATE: "date",
};

/** Icon per backend section id. */
export const SECTION_ICONS = {
    work: "fa-inbox",
    portfolio: "fa-building-o",
};

/** The four trend charts, in display order. */
export const CHART_DEFS = [
    {
        key: "billed_vs_collected",
        // Every series is grouped by the month the obligation FELL DUE
        // (`_billed_vs_collected`), so the third one is "how much of that
        // month's rent has been paid so far", not cash banked in that month.
        // The labels say so: rent received today against an invoice due in
        // March moves March's bar, not this month's.
        get title() { return _t("Billed vs Collected"); },
        get subtitle() { return _t("Rent by the month it fell due: scheduled, invoiced, and paid so far"); },
        type: "bar",
        series: [
            { name: "scheduled", get label() { return _t("Scheduled"); }, color: "--re-chart-muted", format: FORMAT.MONETARY },
            { name: "billed", get label() { return _t("Invoiced"); }, color: "--re-chart-2", format: FORMAT.MONETARY },
            { name: "collected", get label() { return _t("Paid so far"); }, color: "--re-chart-1", format: FORMAT.MONETARY },
        ],
        yFormat: FORMAT.MONETARY,
    },
    {
        key: "arrears_aging",
        get title() { return _t("Arrears Ageing"); },
        get subtitle() { return _t("Outstanding balance by how overdue it is"); },
        type: "bar",
        series: [
            { name: "amounts", get label() { return _t("Outstanding"); }, color: "--re-chart-warn", format: FORMAT.MONETARY },
        ],
        yFormat: FORMAT.MONETARY,
        // Segment drilldown: the payload ships a parallel `keys` array whose
        // values are the bucket ids the backend expects.
        drillBucket: "keys",
        countsSeries: "counts",
    },
    {
        key: "expiries_by_month",
        get title() { return _t("Lease Expiries by Month"); },
        get subtitle() { return _t("Leases reaching their end date over the next year"); },
        type: "bar",
        series: [
            { name: "counts", get label() { return _t("Leases"); }, color: "--re-chart-3", format: FORMAT.INTEGER },
        ],
        yFormat: FORMAT.INTEGER,
    },
    {
        key: "occupancy_trend",
        get title() { return _t("Occupancy over Time"); },
        get subtitle() { return _t("Share of leasable units under a live lease, by month"); },
        type: "line",
        series: [
            {
                name: "occupancy_pct",
                get label() { return _t("Occupancy %"); },
                color: "--re-chart-1",
                format: FORMAT.PERCENT,
                fill: true,
            },
        ],
        yFormat: FORMAT.PERCENT,
        yMax: 100,
    },
];
