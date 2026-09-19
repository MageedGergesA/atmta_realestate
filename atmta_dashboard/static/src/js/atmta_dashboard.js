/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, useEffect, useRef, useState } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { localization } from "@web/core/l10n/localization";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { formatFloat, formatInteger, formatMonetary, formatPercentage } from "@web/views/fields/formatters";

/**
 * Format a figure with Odoo's own formatters, so separators, currency symbol
 * position and precision follow the user's language and the company currency.
 */
export function formatDashboardValue(value, format, currencyId) {
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

const CHART_COLORS = ["#017e84", "#71639e", "#5b899e", "#a5757d", "#b5651d", "#adb5bd"];

export class AtmtaKpiCard extends Component {
    static template = "atmta_dashboard.KpiCard";
    static props = {
        tile: Object,
        currencyId: { type: [Number, Boolean], optional: true },
        onDrill: { type: Function, optional: true },
    };

    get formattedValue() {
        return formatDashboardValue(this.props.tile.value, this.props.tile.format, this.props.currencyId);
    }

    get ariaLabel() {
        const parts = [this.props.tile.label, this.formattedValue];
        if (this.props.tile.warning) {
            parts.push(_t("Needs attention"));
        }
        if (this.props.tile.drill) {
            parts.push(_t("Opens the underlying records"));
        }
        return parts.join(". ");
    }

    onClick() {
        if (this.props.tile.drill && this.props.onDrill) {
            this.props.onDrill(this.props.tile.key);
        }
    }

    onKeydown(ev) {
        if (this.props.tile.drill && (ev.key === "Enter" || ev.key === " ")) {
            ev.preventDefault();
            this.onClick();
        }
    }
}

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
        const datasets = chart.series.map((series, index) => ({
            label: series.label,
            data: series.data,
            backgroundColor: circular
                ? chart.labels.map((_, i) => CHART_COLORS[i % CHART_COLORS.length])
                : CHART_COLORS[index % CHART_COLORS.length],
            borderColor: circular ? "#fff" : CHART_COLORS[index % CHART_COLORS.length],
            borderWidth: chart.type === "line" ? 2 : circular ? 2 : 0,
            borderRadius: chart.type === "bar" ? 3 : 0,
            tension: 0.3,
        }));
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
    static components = { AtmtaKpiCard, AtmtaDashboardChart };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.notification = useService("notification");
        const params = (this.props.action && this.props.action.params) || {};
        this.provider = params.provider;
        this.state = useState({ status: "loading", data: null, scope: params.scope || "team", error: "" });
        onWillStart(() => this.load());
    }

    async load() {
        if (!this.provider) {
            this.state.status = "error";
            this.state.error = _t("This dashboard has no data provider configured.");
            return;
        }
        try {
            this.state.data = await this.orm.call(this.provider, "get_dashboard", [this.state.scope]);
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
