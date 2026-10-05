/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

import { AtmtaKpiCard } from "@atmta_dashboard/js/components/kpi_card";
import {
    AtmtaCard, AtmtaDataTable, AtmtaRowList, AtmtaSkeleton,
} from "@atmta_dashboard/js/components/panels";
import { AtmtaFilterBar } from "@atmta_dashboard/js/components/filters";
import { AtmtaDonut } from "@atmta_dashboard/js/components/donut";
import { AtmtaDashboardChart } from "@atmta_dashboard/js/atmta_dashboard";

const MODEL = "realestate.procurement.dashboard";

/**
 * Procurement Overview.
 *
 * Replaces a grid of nineteen equal-weight tiles. The counts all survive, but
 * only six are headline figures; the rest are a problem list and a personal
 * work queue, because a counted row reads faster than a card and takes a
 * fifth of the space.
 */
export class ProcurementDashboard extends Component {
    static template = "atmta_procurement_app.ProcurementDashboard";
    static components = {
        AtmtaKpiCard, AtmtaCard, AtmtaDataTable, AtmtaRowList, AtmtaSkeleton,
        AtmtaFilterBar, AtmtaDonut, AtmtaDashboardChart,
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
                MODEL, "get_overview", ["team", { ...this.state.filters }]);
            this.state.loadedAt = new Date();
            this.state.errorMessage = "";
            this.state.status = "ready";
        } catch (error) {
            console.error("Procurement dashboard failed to load", error);
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
    get demandStages() { return this.data.demand_stages || { segments: [], total: 0 }; }
    get sourcingFunnel() { return this.data.sourcing_funnel || { segments: [], total: 0 }; }

    get lastUpdatedLabel() {
        if (!this.state.loadedAt) {
            return "";
        }
        const minutes = Math.round((Date.now() - this.state.loadedAt.getTime()) / 60000);
        return minutes < 1 ? _t("Updated just now") : _t("Updated %s min ago", minutes);
    }

    get leadTimeChart() {
        const lead = this.data.lead_time;
        if (!lead || !lead.raised || !lead.raised.some((v) => v)) {
            return null;
        }
        return {
            key: "lead_time",
            title: _t("Demand Raised vs Cleared"),
            // The cheapest honest measure of whether the function is keeping
            // up: if raised stays above cleared the backlog is growing, and no
            // single tile shows that.
            subtitle: _t("Requisitions by the month they were raised"),
            icon: "fa-line-chart",
            span: "o_ad_col_6",
            type: "bar",
            labels: lead.labels,
            series: [
                { name: "raised", label: _t("Raised"), data: lead.raised, format: "integer" },
                { name: "closed", label: _t("Cleared"), data: lead.closed, format: "integer" },
            ],
            drillable: lead.labels.map(() => false),
        };
    }

    async runBackendAction(method, args, failureMessage) {
        try {
            const action = await this.orm.call(MODEL, method, args);
            await this.action.doAction(action);
        } catch (error) {
            console.error("Procurement dashboard action failed", method, args, error);
            this.notification.add(failureMessage, { type: "warning" });
        }
    }

    async drill(key) {
        if (key) {
            await this.runBackendAction("action_overview_drill", [key],
                _t("Could not open the records behind this figure."));
        }
    }

    async runQuickAction(key) {
        await this.runBackendAction("action_overview_quick", [key],
            _t("Could not open this screen."));
    }
}

registry.category("actions").add("realestate.procurement_dashboard", ProcurementDashboard);
