/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, useEffect, useRef, useState } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { localization } from "@web/core/l10n/localization";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
// One implementation of the KPI card, the formatter and the panels, shared by
// every dashboard in the suite. They live in ./components so a module-specific
// dashboard can import them without importing this client action.
import { AtmtaKpiCard, formatDashboardValue } from "./components/kpi_card";
import { AtmtaCard, AtmtaDataTable, AtmtaEmptyState, AtmtaRowList, AtmtaSkeleton } from "./components/panels";
import { AtmtaFilterBar } from "./components/filters";

export { AtmtaKpiCard, formatDashboardValue };

// The dashboard palette, so a chart here and a chart on any other screen in
// the suite are the same colours. Kept in step with `--ad-series-*` in
// atmta_dashboard.scss.
const CHART_COLORS = ["#7645d9", "#0ca678", "#3b82c4", "#e8912d", "#b0589a", "#58a7b0"];

export class AtmtaDashboardChart extends Component {
    static template = "atmta_dashboard.Chart";
    static props = {
        chart: Object,
        currencyId: { type: [Number, Boolean], optional: true },
        onSegment: { type: Function, optional: true },
    };

    setup() {
        this.canvasRef = useRef("canvas");
        this.instance = null;
        onWillStart(() => loadBundle("web.chartjs_lib"));
        useEffect(
            () => {
                this.draw();
                return () => this.destroy();
            },
            () => [this.props.chart, this.props.currencyId]
        );
        onWillUnmount(() => this.destroy());
    }

    get isEmpty() {
        const chart = this.props.chart;
        return !chart.labels.length || chart.series.every((s) => !s.data.some((v) => v));
    }

    destroy() {
        if (this.instance) {
            this.instance.destroy();
            this.instance = null;
        }
    }

    draw() {
        this.destroy();
        const canvas = this.canvasRef.el;
        if (!canvas || this.isEmpty || typeof Chart === "undefined") {
            return;
        }
        const chart = this.props.chart;
        const isRTL = localization.direction === "rtl";
        const circular = ["doughnut", "pie"].includes(chart.type);
        const format = (chart.series[0] && chart.series[0].format) || "integer";
        // Mixed charts. A series may override the chart's own type, so a
        // running total can ride as a line over the bars it is the total of.
        // Drawing a cumulative curve as bars asks the reader to compare
        // heights that are not independent quantities, which is the one thing
        // a bar chart is supposed to mean.
        const datasets = chart.series.map((series, index) => {
            const override = !circular && series.type ? series.type : null;
            const effective = override || chart.type;
            const color = CHART_COLORS[index % CHART_COLORS.length];
            return {
                label: series.label,
                data: series.data,
                ...(override ? { type: override } : {}),
                // Chart.js draws lower `order` last, so the line lands on top
                // of the bars rather than behind them.
                order: effective === "line" ? 0 : 1,
                backgroundColor: circular
                    ? chart.labels.map((_, i) => CHART_COLORS[i % CHART_COLORS.length])
                    : color,
                borderColor: circular ? "#fff" : color,
                borderWidth: effective === "line" ? 2 : circular ? 2 : 0,
                borderRadius: effective === "bar" ? 3 : 0,
                // An area fill under a cumulative curve hides the bars it is
                // drawn over.
                fill: false,
                tension: 0.3,
            };
        });
        const self = this;
        this.instance = new Chart(canvas, {
            type: chart.type,
            data: { labels: chart.labels, datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                resizeDelay: 100,
                animation: { duration: 250 },
                onClick: (ev, elements) => self.onChartClick(elements),
                plugins: {
                    legend: {
                        display: circular || chart.series.length > 1,
                        position: "bottom",
                        rtl: isRTL,
                        labels: { boxWidth: 12, boxHeight: 12, usePointStyle: true },
                    },
                    tooltip: {
                        rtl: isRTL,
                        callbacks: {
                            label(context) {
                                const series = chart.series[context.datasetIndex];
                                const raw = circular ? context.parsed : context.parsed.y;
                                const value = formatDashboardValue(raw, series.format, self.props.currencyId);
                                return `${circular ? context.label : series.label}: ${value}`;
                            },
                        },
                    },
                },
                scales: circular
                    ? {}
                    : {
                          x: { grid: { display: false }, ticks: { autoSkip: true, maxRotation: 45 } },
                          y: {
                              beginAtZero: true,
                              ticks: {
                                  precision: format === "integer" ? 0 : undefined,
                                  callback: (value) => formatDashboardValue(value, format, self.props.currencyId),
                              },
                          },
                      },
            },
        });
    }

    onChartClick(elements) {
        if (!elements || !elements.length || !this.props.onSegment) {
            return;
        }
        const index = elements[0].index;
        if (this.props.chart.drillable[index]) {
            this.props.onSegment(this.props.chart.key, index);
        }
    }
}

/**
 * The dashboard screen. The provider model comes from the action's params:
 *
 *     <record model="ir.actions.client">
 *         <field name="tag">atmta_dashboard</field>
 *         <field name="params" eval="{'provider': 'my.dashboard'}"/>
 *     </record>
 */
export class AtmtaDashboard extends Component {
    static template = "atmta_dashboard.Dashboard";
    static components = { AtmtaKpiCard, AtmtaDashboardChart, AtmtaCard,
                          AtmtaRowList, AtmtaDataTable, AtmtaSkeleton,
                          AtmtaEmptyState, AtmtaFilterBar };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.notification = useService("notification");
        const params = (this.props.action && this.props.action.params) || {};
        this.provider = params.provider;
        this.state = useState({
            status: "loading",
            data: null,
            scope: params.scope || "team",
            error: "",
            // Filter values live here, not on the server: changing one
            // re-asks for the payload, so the dashboard and the records a
            // tile opens can never disagree about what is selected.
            filters: {},
            period: params.period || "month",
            loadedAt: null,
        });
        onWillStart(() => this.load());
    }

    async load() {
        if (!this.provider) {
            this.state.status = "error";
            this.state.error = _t("This dashboard has no data provider configured.");
            return;
        }
        try {
            this.state.data = await this.orm.call(this.provider, "get_dashboard", [this.state.scope], {
                filters: { ...this.state.filters, period: this.state.period },
            });
            this.state.loadedAt = new Date();
            this.state.status = "ready";
        } catch (error) {
            this.state.status = "error";
            this.state.error = (error && error.data && error.data.message) || _t("The dashboard could not be loaded.");
        }
    }

    async refresh() {
        this.state.status = this.state.data ? "ready" : "loading";
        await this.load();
    }

    async setScope(scope) {
        if (scope !== this.state.scope) {
            this.state.scope = scope;
            await this.load();
        }
    }

    get data() {
        return this.state.data || {};
    }

    get filterDefs() {
        return this.data.filters || [];
    }

    /**
     * "Updated 2 min ago" rather than a bare timestamp: a dashboard whose age
     * is not obvious gets trusted when it is stale.
     */
    get lastUpdatedLabel() {
        if (!this.state.loadedAt) {
            return "";
        }
        const seconds = Math.round((Date.now() - this.state.loadedAt.getTime()) / 1000);
        if (seconds < 60) {
            return _t("Updated just now");
        }
        const minutes = Math.round(seconds / 60);
        if (minutes < 60) {
            return _t("Updated %s min ago", minutes);
        }
        return _t("Updated at %s", this.state.loadedAt.toLocaleTimeString());
    }

    async setFilter(key, value) {
        if (value === "all") {
            delete this.state.filters[key];
        } else {
            this.state.filters[key] = value;
        }
        await this.load();
    }

    async setPeriod(period) {
        this.state.period = period;
        await this.load();
    }

    async resetFilters() {
        this.state.filters = {};
        await this.load();
    }

    async open(method, args) {
        try {
            const action = await this.orm.call(this.provider, method, args);
            if (action) {
                await this.actionService.doAction(action);
            }
        } catch (error) {
            this.notification.add(
                (error && error.data && error.data.message) || _t("Could not open these records."),
                { type: "warning" }
            );
        }
    }

    drill(key) {
        return this.open("action_drill", [key, this.state.scope]);
    }

    drillChart(chartKey, index) {
        return this.open("action_drill_chart", [chartKey, index, this.state.scope]);
    }

    quick(key) {
        return this.open("action_quick", [key]);
    }
}

registry.category("actions").add("atmta_dashboard", AtmtaDashboard);
