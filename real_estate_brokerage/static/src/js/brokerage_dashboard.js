/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

// The shared dashboard system: Brokerage renders through exactly the same
// primitives as Rental, so the two screens are the same product.
import { AtmtaKpiCard } from "@atmta_dashboard/js/components/kpi_card";
import {
    AtmtaCard, AtmtaDataTable, AtmtaRowList, AtmtaSkeleton,
} from "@atmta_dashboard/js/components/panels";
import { AtmtaFilterBar } from "@atmta_dashboard/js/components/filters";
import { AtmtaDonut } from "@atmta_dashboard/js/components/donut";
// The shared chart wrapper, NOT the Rental module's one. Importing that would
// make Brokerage depend on the whole leasing app for a canvas.
import { AtmtaDashboardChart } from "@atmta_dashboard/js/atmta_dashboard";

/**
 * Brokerage Overview.
 *
 * Reading order is the same as Rental and for the same reason: KPIs (what is
 * happening), analysis (why), problems (what is wrong), my work (what I do
 * about it), detail last.
 */
export class BrokerageDashboard extends Component {
    static template = "real_estate_brokerage.BrokerageDashboard";
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
                "realestate.brokerage.dashboard", "get_overview",
                ["team", { ...this.state.filters }]
            );
            this.state.loadedAt = new Date();
            this.state.errorMessage = "";
            this.state.status = "ready";
        } catch (error) {
            console.error("Brokerage dashboard failed to load", error);
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

    get listingStatus() { return this.data.listing_status || { segments: [], total: 0 }; }
    get funnel() { return this.data.funnel; }

    /** Built in the shape the shared chart component expects. */
    get velocityChart() {
        const velocity = this.data.velocity;
        if (!velocity || !velocity.value || !velocity.value.some((v) => v)) {
            return null;
        }
        return {
            key: "velocity",
            title: _t("Sales Velocity"),
            subtitle: _t("Value of deals closed, by closing month"),
            icon: "fa-line-chart",
            span: "o_ad_col_6",
            type: "bar",
            labels: velocity.labels,
            series: [{
                name: "value",
                label: _t("Deal Value"),
                data: velocity.value,
                format: "monetary",
            }],
            drillable: velocity.labels.map(() => false),
        };
    }

    get agentColumns() {
        return [
            { key: "agent", label: _t("Agent") },
            { key: "deals", label: _t("Deals"), numeric: true },
            { key: "commission", label: _t("Commission Paid"), numeric: true, format: "monetary" },
        ];
    }

    get listingColumns() {
        return [
            { key: "listing", label: _t("Property") },
            { key: "agent", label: _t("Agent") },
            { key: "price", label: _t("Asking Price"), numeric: true, format: "monetary" },
            { key: "days", label: _t("Days on Market"), numeric: true },
            { key: "status", label: _t("Status"), type: "badge" },
        ];
    }

    async runBackendAction(method, args, failureMessage) {
        try {
            const action = await this.orm.call("realestate.brokerage.dashboard", method, args);
            await this.action.doAction(action);
        } catch (error) {
            console.error("Brokerage dashboard action failed", method, args, error);
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

    async openListing(row) {
        if (row && row.id) {
            await this.action.doAction({
                type: "ir.actions.act_window",
                res_model: "realestate.listing",
                res_id: row.id,
                views: [[false, "form"]],
            });
        }
    }
}

registry.category("actions").add("realestate.brokerage_dashboard", BrokerageDashboard);
