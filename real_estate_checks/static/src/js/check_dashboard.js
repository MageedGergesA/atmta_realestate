/** @odoo-module **/

/**
 * M20 — the treasury dashboard.
 *
 * Three things this component deliberately does NOT do:
 *
 * 1. It never computes a figure. Every number is produced by
 *    `realestate.check.dashboard.get_dashboard_data` with `_read_group`, so a
 *    100,000-cheque database does not ship 100,000 records to a browser.
 *
 * 2. It never invents a drill-down domain. Each KPI arrives carrying the
 *    server-side domain that produced it, and clicking it re-opens exactly
 *    that. A count and its list therefore cannot disagree — the discipline the
 *    Rental Dashboard V2 established.
 *
 * 3. It never re-labels a figure. `label` and `kind` come from the server, so
 *    the front end cannot restate "PDC Amount Received (Face Value)" as though
 *    it were money. `KIND_LABEL` below is the only mapping it owns, and
 *    `test_terminology.py` asserts every rendered string.
 *
 * Chart.js is loaded from Odoo's own lazy bundle, the same pattern Module 1
 * established. This module ships no vendored copy.
 */

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadBundle } from "@web/core/assets";
import { formatMonetary } from "@web/views/fields/formatters";
import {
    Component,
    onMounted,
    onWillStart,
    onWillUnmount,
    useRef,
    useState,
} from "@odoo/owl";

/**
 * The only label mapping the browser owns.
 *
 * `cash` is money Odoo has confirmed. `paper` is the face value of an
 * instrument. `obligation` is what is owed. None of the three uses the
 * vocabulary of income, and `test_terminology.py` asserts that.
 */
const KIND_LABEL = {
    cash: "Cash",
    paper: "Paper",
    obligation: "Owed",
};

export class CheckTreasuryDashboard extends Component {
    static template = "real_estate_checks.TreasuryDashboard";
    // Client actions receive a props bag Odoo controls; accept it wholesale
    // rather than enumerate keys that may differ between action types.
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ data: null, loading: true, error: null });
        this.maturityChartRef = useRef("maturityChart");
        this.stateChartRef = useRef("stateChart");
        this._charts = [];

        onWillStart(async () => {
            // Odoo's own Chart.js, lazily. Never a vendored copy, never a CDN.
            await loadBundle("web.chartjs_lib");
            await this.load();
        });
        onMounted(() => this.renderCharts());
        onWillUnmount(() => this.destroyCharts());
    }

    async load() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call(
                "realestate.check.dashboard",
                "get_dashboard_data",
                [],
                {}
            );
            this.state.error = null;
        } catch (error) {
            // Say what went wrong rather than render an empty dashboard, which
            // would look exactly like "you have no cheques".
            this.state.error =
                error.data?.message || error.message?.data?.message ||
                error.message || String(error);
            this.state.data = null;
        } finally {
            this.state.loading = false;
        }
    }

    async refresh() {
        this.destroyCharts();
        await this.load();
        this.renderCharts();
    }

    // ------------------------------------------------------------------
    // Sections — flattened here so the template is one loop, not three
    // ------------------------------------------------------------------
    get sections() {
        const data = this.state.data;
        if (!data) {
            return [];
        }
        return [
            { key: "on_hand", title: "On Hand", kpis: data.on_hand },
            { key: "deposit", title: "At the Bank", kpis: data.deposit },
            { key: "cleared", title: "Cleared", kpis: data.cleared },
        ];
    }

    get riskCards() {
        const risk = this.state.data?.risk;
        if (!risk) {
            return [];
        }
        return [
            "bounced",
            "unresolved_bounces",
            "manual_accounting",
            "replacements_outstanding",
        ].map((key) => ({ key, ...risk[key] }));
    }

    get coverageCards() {
        const coverage = this.state.data?.coverage;
        if (!coverage) {
            return [];
        }
        return [
            { key: "future_obligations", ...coverage.future_obligations },
            { key: "pdc_received", ...coverage.pdc_received },
            { key: "unsecured", ...coverage.unsecured },
        ];
    }

    // ------------------------------------------------------------------
    // Formatting
    // ------------------------------------------------------------------
    formatAmount(value) {
        return formatMonetary(value || 0, {
            currencyId: this.state.data?.currency_id,
            digits: [69, 0],
        });
    }

    formatPercent(value) {
        return `${(value || 0).toFixed(1)}%`;
    }

    kindLabel(kind) {
        return KIND_LABEL[kind] || "";
    }

    kindClass(kind) {
        return kind === "cash" ? "text-bg-success" : "text-bg-secondary";
    }

    // ------------------------------------------------------------------
    // Drill-down — the server owns the domain
    // ------------------------------------------------------------------
    openKpi(kpi) {
        if (!kpi || !kpi.domain || !kpi.model) {
            return;
        }
        return this.action.doAction({
            type: "ir.actions.act_window",
            name: kpi.label,
            res_model: kpi.model,
            views: [
                [false, "list"],
                [false, "form"],
            ],
            domain: kpi.domain,
            target: "current",
        });
    }

    openBucket(bucket) {
        return this.action.doAction({
            type: "ir.actions.act_window",
            name: bucket.label,
            res_model: "realestate.check",
            views: [
                [false, "list"],
                [false, "form"],
            ],
            domain: bucket.domain,
            target: "current",
        });
    }

    // ------------------------------------------------------------------
    // Charts
    // ------------------------------------------------------------------
    destroyCharts() {
        this._charts.forEach((chart) => chart.destroy());
        this._charts = [];
    }

    renderCharts() {
        const data = this.state.data;
        if (!data || typeof Chart === "undefined") {
            return;
        }
        this.destroyCharts();

        const forecast = data.charts.maturity_forecast;
        if (this.maturityChartRef.el && forecast) {
            this._charts.push(
                new Chart(this.maturityChartRef.el, {
                    type: "bar",
                    data: {
                        labels: forecast.buckets.map((b) => b.label),
                        datasets: [
                            {
                                label: "PDC face value maturing",
                                data: forecast.buckets.map((b) => b.amount),
                                backgroundColor: "#6b7fd7",
                            },
                        ],
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: { legend: { display: false } },
                        onClick: (evt, elements) => {
                            if (elements.length) {
                                this.openBucket(forecast.buckets[elements[0].index]);
                            }
                        },
                    },
                })
            );
        }

        const byState = data.charts.by_state;
        if (this.stateChartRef.el && byState && byState.labels.length) {
            this._charts.push(
                new Chart(this.stateChartRef.el, {
                    type: "doughnut",
                    data: {
                        labels: byState.labels,
                        datasets: [{ data: byState.amounts }],
                    },
                    options: { responsive: true, maintainAspectRatio: false },
                })
            );
        }
    }
}

registry.category("actions").add("realestate_check_dashboard", CheckTreasuryDashboard);
