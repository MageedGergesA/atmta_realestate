/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

// The shared dashboard system. Rental renders through exactly the same
// primitives as Sales, Brokerage, Construction, Procurement and Treasury, so
// the suite reads as one product rather than nine modules with their own CSS.
import { AtmtaKpiCard } from "@atmta_dashboard/js/components/kpi_card";
import { AtmtaCard, AtmtaDataTable, AtmtaRowList, AtmtaSkeleton } from "@atmta_dashboard/js/components/panels";
import { AtmtaFilterBar } from "@atmta_dashboard/js/components/filters";
import { AtmtaDonut } from "@atmta_dashboard/js/components/donut";

import { OVERVIEW_CHARTS } from "./dashboard_schema";
import { DashboardChart } from "./dashboard_chart";
import { DashboardMap } from "./dashboard_map";

/**
 * Rental Overview.
 *
 * One backend call (`get_overview`) returns the whole screen. The previous
 * version made two -- tiles, then charts -- which was right while the charts
 * were decoration. They are not: the occupancy sparkline on the KPI card and
 * the occupancy trend chart below it are the SAME series, and fetching it
 * twice is both slower and a chance for the headline figure and the chart
 * beside it to disagree.
 *
 * Reading order is deliberate and is the whole point of the redesign:
 * KPIs (what is happening) -> trends (why) -> problems (what is wrong) ->
 * my work (what I do about it) -> detail. Not twelve equal-weight boxes.
 */
export class RentalDashboard extends Component {
    static template = "atmta_real_estate.RentalDashboard";
    static components = {
        AtmtaKpiCard, AtmtaCard, AtmtaDataTable, AtmtaRowList, AtmtaSkeleton, AtmtaFilterBar,
        AtmtaDonut, DashboardChart, DashboardMap,
    };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.charts = OVERVIEW_CHARTS;

        this.state = useState({
            status: "loading",
            data: null,
            scope: "mine",
            filters: {},
            errorMessage: "",
            refreshing: false,
            loadedAt: null,
        });

        this.load();
    }

    // ------------------------------------------------------------------
    // Data
    // ------------------------------------------------------------------
    async load({ silent = false } = {}) {
        if (!silent) {
            this.state.status = "loading";
        }
        try {
            this.state.data = await this.orm.call(
                "realestate.rental.dashboard", "get_overview",
                [this.state.scope, { ...this.state.filters }]
            );
            this.state.loadedAt = new Date();
            this.state.errorMessage = "";
            this.state.status = "ready";
        } catch (error) {
            console.error("Rental dashboard failed to load", error);
            this.state.errorMessage =
                (error && error.data && error.data.message) ||
                (error && error.message) ||
                _t("The dashboard data could not be loaded.");
            this.state.status = "error";
            if (silent) {
                this.notification.add(_t("Could not refresh the dashboard."), { type: "danger" });
            }
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

    async setScope(scope) {
        if (scope !== this.state.scope) {
            this.state.scope = scope;
            await this.load({ silent: this.state.status === "ready" });
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

    // ------------------------------------------------------------------
    // Derived values
    // ------------------------------------------------------------------
    get isLoading() { return this.state.status === "loading"; }
    get isError() { return this.state.status === "error"; }
    get isReady() { return this.state.status === "ready" && !!this.state.data; }
    get data() { return this.state.data || {}; }
    get currencyId() { return this.data.currency_id || false; }

    /**
     * "Updated 2 min ago" rather than a bare clock time: a dashboard whose age
     * is not obvious gets trusted when it is stale.
     */
    get lastUpdatedLabel() {
        if (!this.state.loadedAt) {
            return "";
        }
        const minutes = Math.round((Date.now() - this.state.loadedAt.getTime()) / 60000);
        if (minutes < 1) {
            return _t("Updated just now");
        }
        return _t("Updated %s min ago", minutes);
    }

    get occupancyPayload() {
        const trend = this.data.occupancy_trend;
        return trend ? { labels: trend.labels, occupancy: trend.values } : null;
    }

    get collectionPayload() {
        const collection = this.data.collection;
        return collection
            ? { labels: collection.labels, billed: collection.billed, collected: collection.collected }
            : null;
    }

    get collectionSubtitle() {
        const collection = this.data.collection;
        if (!collection) {
            return "";
        }
        return _t("Collection rate %s%", collection.rate.toFixed(1));
    }

    get leaseStatus() {
        return this.data.lease_status || { segments: [], total: 0 };
    }

    get unitMix() {
        return this.data.unit_mix || { segments: [], total: 0 };
    }

    get arrearsPayload() {
        const arrears = this.data.arrears;
        if (!arrears || !arrears.amounts || !arrears.amounts.some((v) => v)) {
            return null;
        }
        return { labels: arrears.labels, amounts: arrears.amounts };
    }

    /** Columns are declared here so a provider can add one without a template change. */
    get unitTypeColumns() {
        return [
            { key: "type", label: _t("Unit Type") },
            { key: "occupied", label: _t("Occupied"), numeric: true },
            { key: "available", label: _t("Available"), numeric: true },
            { key: "occupancy", label: _t("Occupancy"), numeric: true, type: "meter" },
        ];
    }

    get topPropertyColumns() {
        return [
            { key: "property", label: _t("Property") },
            { key: "units", label: _t("Units"), numeric: true },
            { key: "rent", label: _t("Monthly Rent"), numeric: true, format: "monetary" },
            { key: "arrears", label: _t("Arrears"), numeric: true, format: "monetary" },
            { key: "occupancy", label: _t("Occupancy"), numeric: true, type: "meter" },
        ];
    }

    async openProperty(row) {
        if (row && row.id) {
            await this.action.doAction({
                type: "ir.actions.act_window",
                res_model: "realestate.property",
                res_id: row.id,
                views: [[false, "form"]],
            });
        }
    }

    // ------------------------------------------------------------------
    // Actions: the backend owns every domain
    // ------------------------------------------------------------------
    async runBackendAction(method, args, failureMessage) {
        try {
            const action = await this.orm.call("realestate.rental.dashboard", method, args);
            await this.action.doAction(this.normalizeAction(action));
        } catch (error) {
            console.error("Rental dashboard action failed", method, args, error);
            this.notification.add(failureMessage, { type: "warning" });
        }
    }

    /** Open the records behind a figure, with the domain that figure counted. */
    async drill(key) {
        if (!key) {
            return;
        }
        await this.runBackendAction(
            "action_drill", [key, this.state.scope],
            _t("Could not open the records behind this figure.")
        );
    }

    async runQuickAction(key) {
        await this.runBackendAction("action_quick", [key], _t("Could not open this screen."));
    }

    async openMap() {
        await this.runBackendAction("action_open_map", [], _t("Could not open the map."));
    }

    /**
     * DEFENSIVE ONLY: the backend returns complete act_window dicts. Kept
     * because `doAction` maps over `views` unconditionally, so a downstream
     * override that forgets them degrades instead of breaking the dashboard.
     */
    normalizeAction(action) {
        if (!action || typeof action !== "object") {
            return action;
        }
        if (action.type !== "ir.actions.act_window" || action.views) {
            return action;
        }
        const modes = (action.view_mode || "list,form").split(",").map((m) => m.trim()).filter(Boolean);
        return { ...action, views: modes.map((mode) => [false, mode]) };
    }
}

registry.category("actions").add("realestate.rental_dashboard", RentalDashboard);
