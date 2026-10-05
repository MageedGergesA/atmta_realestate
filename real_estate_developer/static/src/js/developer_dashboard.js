/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

// The shared dashboard system, so Development & Sales, Rental and Brokerage
// are visibly the same product rather than three custom screens.
import { AtmtaKpiCard } from "@atmta_dashboard/js/components/kpi_card";
import {
    AtmtaCard, AtmtaDataTable, AtmtaRowList, AtmtaSkeleton,
} from "@atmta_dashboard/js/components/panels";
import { AtmtaFilterBar } from "@atmta_dashboard/js/components/filters";
import { AtmtaDonut } from "@atmta_dashboard/js/components/donut";
import { AtmtaDashboardChart } from "@atmta_dashboard/js/atmta_dashboard";
import { AtmtaMapCard } from "@atmta_dashboard/js/components/map_card";

/**
 * Development & Sales Overview.
 *
 * A developer sells inventory once and collects for years, so the screen
 * answers two questions side by side: are we selling, and are we collecting.
 * Blurring them is how a project looks healthy while the money never arrives,
 * which is why the velocity chart and the collection chart are both here and
 * are different series.
 */
export class DeveloperDashboard extends Component {
    static template = "real_estate_developer.DeveloperDashboard";
    static components = {
        AtmtaKpiCard, AtmtaCard, AtmtaDataTable, AtmtaRowList, AtmtaSkeleton,
        AtmtaFilterBar, AtmtaDonut, AtmtaDashboardChart, AtmtaMapCard,
    };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({
            status: "loading", data: null, filters: {},
            errorMessage: "", refreshing: false, loadedAt: null,
        });
        this.load();
    }

    async load({ silent = false } = {}) {
        if (!silent) {
            this.state.status = "loading";
        }
        try {
            this.state.data = await this.orm.call(
                "realestate.developer.dashboard", "get_overview",
                ["team", { ...this.state.filters }]
            );
            this.state.loadedAt = new Date();
            this.state.errorMessage = "";
            this.state.status = "ready";
        } catch (error) {
            console.error("Developer dashboard failed to load", error);
            this.state.errorMessage =
                (error && error.data && error.data.message) ||
                _t("The dashboard data could not be loaded.");
            this.state.status = "error";
        }
    }

    async onRefresh() {
        if (this.state.refreshing) {
            return;
        }
        this.state.refreshing = true;
        try {
            await this.load({ silent: this.state.status === "ready" });
        } finally {
            this.state.refreshing = false;
        }
    }

    async setFilter(key, value) {
        if (value === "all" || !value) {
            delete this.state.filters[key];
        } else {
            this.state.filters[key] = value;
        }
        await this.load({ silent: this.state.status === "ready" });
    }

    async resetFilters() {
        this.state.filters = {};
        await this.load({ silent: this.state.status === "ready" });
    }

    get isLoading() { return this.state.status === "loading"; }
    get isError() { return this.state.status === "error"; }
    get isReady() { return this.state.status === "ready" && !!this.state.data; }
    get data() { return this.state.data || {}; }
    get currencyId() { return this.data.currency_id || false; }

    get lastUpdatedLabel() {
        if (!this.state.loadedAt) {
            return "";
        }
        const minutes = Math.round((Date.now() - this.state.loadedAt.getTime()) / 60000);
        return minutes < 1 ? _t("Updated just now") : _t("Updated %s min ago", minutes);
    }

    get inventory() { return this.data.inventory || { segments: [], total: 0 }; }

    get velocityChart() {
        const velocity = this.data.velocity;
        if (!velocity || !velocity.value || !velocity.value.some((v) => v)) {
            return null;
        }
        return {
            key: "velocity",
            title: _t("Sales Velocity"),
            subtitle: _t("Value contracted, by the month the contract was signed"),
            icon: "fa-line-chart",
            span: "o_ad_col_5",
            type: "bar",
            labels: velocity.labels,
            series: [{
                name: "value", label: _t("Contracted"),
                data: velocity.value, format: "monetary",
            }],
            drillable: velocity.labels.map(() => false),
        };
    }

    get collectionChart() {
        const collections = this.data.collections;
        if (!collections || !collections.scheduled
            || !collections.scheduled.some((v) => v)) {
            return null;
        }
        return {
            key: "collections",
            title: _t("Scheduled vs Collected"),
            // The GAP between the two bars is the collection problem, which is
            // why they belong on one chart rather than two separate cards.
            subtitle: _t("Collection rate %s%", collections.rate.toFixed(1)),
            icon: "fa-credit-card",
            span: "o_ad_col_4",
            type: "bar",
            labels: collections.labels,
            series: [
                { name: "scheduled", label: _t("Scheduled"), data: collections.scheduled, format: "monetary" },
                { name: "collected", label: _t("Collected"), data: collections.collected, format: "monetary" },
            ],
            drillable: collections.labels.map(() => false),
        };
    }

    get mapData() { return this.data.map || { points: [], legend: [] }; }
    get pipeline() { return this.data.pipeline || { segments: [], total: 0 }; }

    get ageingChart() {
        const ageing = this.data.ageing;
        if (!ageing || !ageing.amounts || !ageing.amounts.some((v) => v)) {
            return null;
        }
        return {
            key: "ageing",
            title: _t("Receivables Ageing"),
            subtitle: _t("Overdue balance by how long it has been overdue"),
            icon: "fa-hourglass-half",
            span: "o_ad_col_4",
            type: "bar",
            labels: ageing.labels,
            series: [{
                name: "amounts", label: _t("Outstanding"),
                data: ageing.amounts, format: "monetary",
            }],
            drillable: ageing.labels.map(() => false),
        };
    }

    get priceColumns() {
        return [
            { key: "type", label: _t("Unit Type") },
            { key: "units", label: _t("Units"), numeric: true },
            { key: "avg_area", label: _t("Avg Area (sqm)"), numeric: true },
            { key: "rate", label: _t("Price / sqm"), numeric: true, format: "monetary" },
            { key: "absorption", label: _t("Absorption"), numeric: true, type: "meter" },
        ];
    }

    get handoverColumns() {
        return [
            { key: "unit", label: _t("Unit") },
            { key: "buyer", label: _t("Buyer") },
            { key: "handover", label: _t("Expected Handover") },
            { key: "outstanding", label: _t("Outstanding"), numeric: true, format: "monetary" },
            { key: "status", label: _t("Status"), type: "badge" },
        ];
    }

    get buyerColumns() {
        return [
            { key: "buyer", label: _t("Buyer") },
            { key: "contracts", label: _t("Contracts"), numeric: true },
            { key: "value", label: _t("Contracted"), numeric: true, format: "monetary" },
            { key: "outstanding", label: _t("Outstanding"), numeric: true, format: "monetary" },
        ];
    }

    async openMapProject(point) {
        if (point && point.id) {
            await this.action.doAction({
                type: "ir.actions.act_window",
                res_model: "realestate.project",
                res_id: point.id,
                views: [[false, "form"]],
            });
        }
    }

    get projectColumns() {
        return [
            { key: "project", label: _t("Project") },
            { key: "units", label: _t("Units"), numeric: true },
            { key: "released", label: _t("Released"), numeric: true },
            { key: "committed", label: _t("Committed"), numeric: true },
            { key: "value", label: _t("Contracted Value"), numeric: true, format: "monetary" },
            { key: "sell_through", label: _t("Sell-Through"), numeric: true, type: "meter" },
        ];
    }

    get planColumns() {
        return [
            { key: "plan", label: _t("Payment Plan") },
            { key: "contracts", label: _t("Contracts"), numeric: true },
            { key: "scheduled", label: _t("Scheduled"), numeric: true, format: "monetary" },
            { key: "collected", label: _t("Collected"), numeric: true, format: "monetary" },
            { key: "rate", label: _t("Collection Rate"), numeric: true, type: "meter" },
        ];
    }

    async runBackendAction(method, args, failureMessage) {
        try {
            const action = await this.orm.call("realestate.developer.dashboard", method, args);
            await this.action.doAction(action);
        } catch (error) {
            console.error("Developer dashboard action failed", method, args, error);
            this.notification.add(failureMessage, { type: "warning" });
        }
    }

    async drill(key) {
        if (key) {
            await this.runBackendAction("action_drill", [key],
                _t("Could not open the records behind this figure."));
        }
    }

    async runQuickAction(key) {
        await this.runBackendAction("action_quick", [key], _t("Could not open this screen."));
    }

    async openProject(row) {
        if (row && row.id) {
            await this.action.doAction({
                type: "ir.actions.act_window",
                res_model: "realestate.project",
                res_id: row.id,
                views: [[false, "form"]],
            });
        }
    }
}

registry.category("actions").add("realestate.developer_dashboard", DeveloperDashboard);
