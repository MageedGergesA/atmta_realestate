/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, useEffect, useRef } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { loadBundle } from "@web/core/assets";
import { localization } from "@web/core/l10n/localization";

import { FORMAT } from "./dashboard_schema";
import { formatKpiValue } from "./kpi_card";

/**
 * Fallback palette, used only when the CSS custom properties are not resolvable
 * (chiefly in unit tests, where no stylesheet is mounted). The real colours
 * live in the SCSS so they follow the Odoo theme and dark mode.
 */
const FALLBACK_COLORS = {
    "--re-chart-1": "#017e84",
    "--re-chart-2": "#71639e",
    "--re-chart-3": "#5b899e",
    "--re-chart-4": "#a5757d",
    "--re-chart-warn": "#b5651d",
    "--re-chart-muted": "#adb5bd",
};

function resolveColor(token, el) {
    if (!token || !token.startsWith("--")) {
        return token;
    }
    if (el && typeof getComputedStyle === "function") {
        const value = getComputedStyle(el).getPropertyValue(token).trim();
        if (value) {
            return value;
        }
    }
    return FALLBACK_COLORS[token] || "#017e84";
}

/**
 * One chart panel.
 *
 * Owns exactly one Chart.js instance and is rigorous about its lifecycle: the
 * instance is destroyed before every re-create and again on unmount, so
 * refreshing or navigating away cannot leave detached canvases accumulating in
 * memory. That was the specific failure mode called out for this phase.
 */
export class DashboardChart extends Component {
    static template = "atmta_real_estate.DashboardChart";
    static props = {
        definition: { type: Object },
        payload: { type: [Object, { value: null }], optional: true },
        currencyId: { type: [Number, { value: false }], optional: true },
        onSegmentClick: { type: Function, optional: true },
    };

    setup() {
        this.canvasRef = useRef("canvas");
        this.chart = null;

        // Use ODOO'S Chart.js, lazily.
        //
        // Odoo ships Chart.js v4.4.1 in the dedicated `web.chartjs_lib`
        // bundle, which its own graph view, gauge field and journal dashboard
        // all load this way. Bundling a second copy into assets_backend would
        // put ~200 KB on every backend page for a library only this dashboard
        // uses, and would leave two `Chart` globals racing to define
        // themselves the moment a user also opens a graph view.
        onWillStart(() => loadBundle("web.chartjs_lib"));

        useEffect(
            () => {
                this.renderChart();
                // The cleanup returned here runs before the next effect pass
                // AND on unmount, which is what guarantees one live instance.
                return () => this.destroyChart();
            },
            () => [this.props.payload, this.props.currencyId]
        );

        onWillUnmount(() => this.destroyChart());
    }

    destroyChart() {
        if (this.chart) {
            this.chart.destroy();
            this.chart = null;
        }
    }

    /** True when there is genuinely nothing to plot (a valid business state). */
    get isEmpty() {
        const payload = this.props.payload;
        if (!payload || !payload.labels || !payload.labels.length) {
            return true;
        }
        // Labels but every series flat at zero is still worth drawing — an
        // all-zero month IS information. Only a missing dataset is "empty".
        return this.props.definition.series.every(
            (series) => !Array.isArray(payload[series.name])
        );
    }

    get emptyText() {
        return _t("No data for this period yet.");
    }

    formatValue(value, format) {
        return formatKpiValue(value, format, { currencyId: this.props.currencyId });
    }

    renderChart() {
        this.destroyChart();
        const canvas = this.canvasRef.el;
        if (!canvas || this.isEmpty || typeof Chart === "undefined") {
            return;
        }

        const def = this.props.definition;
        const payload = this.props.payload;
        const isRTL = localization.direction === "rtl";

        const datasets = def.series.map((series) => {
            const color = resolveColor(series.color, canvas);
            const base = {
                label: series.label,
                data: payload[series.name] || [],
                borderColor: color,
                borderWidth: def.type === "line" ? 2 : 0,
                borderRadius: def.type === "bar" ? 3 : 0,
                // A single-point dataset is invisible on a line chart without
                // a point marker, so points stay on for short series.
                pointRadius: def.type === "line" ? 3 : undefined,
                pointHoverRadius: def.type === "line" ? 5 : undefined,
                tension: def.type === "line" ? 0.3 : undefined,
            };
            if (def.type === "line" && series.fill) {
                base.backgroundColor = `${color}22`;
                base.fill = true;
            } else {
                base.backgroundColor = color;
            }
            return base;
        });

        this.chart = new Chart(canvas, {
            type: def.type,
            data: { labels: payload.labels || [], datasets },
            options: this.buildOptions(def, isRTL),
        });
    }

    buildOptions(def, isRTL) {
        const self = this;
        const yFormat = def.yFormat || FORMAT.INTEGER;
        const showLegend = def.series.length > 1;

        return {
            responsive: true,
            maintainAspectRatio: false,
            // Charts live in flex containers; without this a resize can grow
            // the canvas unboundedly.
            resizeDelay: 100,
            indexAxis: def.horizontal ? "y" : "x",
            animation: { duration: 250 },
            interaction: { mode: "index", intersect: false },
            onClick: (ev, elements) => self.onChartClick(ev, elements),
            plugins: {
                legend: {
                    display: showLegend,
                    position: "bottom",
                    rtl: isRTL,
                    labels: { boxWidth: 12, boxHeight: 12, usePointStyle: true },
                },
                tooltip: {
                    rtl: isRTL,
                    callbacks: {
                        label(context) {
                            const series = def.series[context.datasetIndex];
                            const format = (series && series.format) || yFormat;
                            const value = self.formatValue(context.parsed[def.horizontal ? "x" : "y"], format);
                            let line = `${context.dataset.label}: ${value}`;
                            // The arrears chart carries a parallel count array
                            // that is far more useful in a tooltip than on an axis.
                            if (def.countsSeries && self.props.payload[def.countsSeries]) {
                                const count = self.props.payload[def.countsSeries][context.dataIndex];
                                if (count !== undefined) {
                                    line += ` (${count})`;
                                }
                            }
                            return line;
                        },
                    },
                },
            },
            scales: {
                x: {
                    stacked: !!def.stacked,
                    grid: { display: def.horizontal },
                    ticks: {
                        precision:
                            def.horizontal && yFormat === FORMAT.INTEGER ? 0 : undefined,
                        // Long building names must not push the chart out of
                        // its container.
                        maxRotation: 45,
                        minRotation: 0,
                        autoSkip: true,
                        callback(value, index) {
                            const label = this.getLabelForValue(value);
                            if (def.horizontal) {
                                return self.formatValue(value, yFormat);
                            }
                            return typeof label === "string" && label.length > 16
                                ? `${label.slice(0, 15)}…`
                                : label;
                        },
                    },
                },
                y: {
                    stacked: !!def.stacked,
                    beginAtZero: true,
                    max: def.yMax,
                    ticks: {
                        // Counts are whole things. Without this Chart.js picks
                        // fractional steps (0, 0.5, 1) which the integer
                        // formatter then renders as "0, 1, 1" — duplicate
                        // labels that read as a broken axis.
                        precision: yFormat === FORMAT.INTEGER ? 0 : undefined,
                        callback(value) {
                            if (def.horizontal) {
                                const label = this.getLabelForValue(value);
                                return typeof label === "string" && label.length > 20
                                    ? `${label.slice(0, 19)}…`
                                    : label;
                            }
                            return self.formatValue(value, yFormat);
                        },
                    },
                },
            },
        };
    }

    /**
     * Segment drilldown. Only fires for charts whose payload actually publishes
     * a bucket-key array — no chart invents a drilldown target.
     */
    onChartClick(ev, elements) {
        const def = this.props.definition;
        if (!def.drillBucket || !elements || !elements.length || !this.props.onSegmentClick) {
            return;
        }
        const keys = this.props.payload[def.drillBucket];
        const bucket = keys && keys[elements[0].index];
        if (bucket) {
            this.props.onSegmentClick(bucket);
        }
    }
}
