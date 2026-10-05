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
import { AtmtaMapCard } from "@atmta_dashboard/js/components/map_card";
import { AtmtaDashboardChart } from "@atmta_dashboard/js/atmta_dashboard";

const MODEL = "realestate.construction.overview";

/**
 * Construction Overview.
 *
 * A NEW screen under its own action and tag, deliberately not a replacement
 * for `realestate.construction_dashboard`. That action is frozen: M10A
 * documented the legacy dashboard as contradicting the authoritative budget
 * and actual-cost definitions, and `test_m10_freeze_invariants` asserts it
 * stays out of reach of ordinary construction users. Repointing it here would
 * have made that test pass while quietly reversing the decision behind it.
 *
 * This is the operational surface: programme, packages, quality, information
 * flow. Money stays with the Control Tower, which remains canonical.
 */
export class ConstructionOverview extends Component {
    static template = "real_estate_construction.ConstructionOverview";
    static components = {
        AtmtaKpiCard, AtmtaCard, AtmtaDataTable, AtmtaRowList, AtmtaSkeleton,
        AtmtaFilterBar, AtmtaDonut, AtmtaMapCard, AtmtaDashboardChart,
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
            console.error("Construction overview failed to load", error);
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
    get programme() { return this.data.programme || { segments: [], total: 0 }; }
    get quality() { return this.data.quality || { segments: [], total: 0 }; }
    get mapData() { return this.data.map || { points: [], legend: [] }; }

    get lastUpdatedLabel() {
        if (!this.state.loadedAt) {
            return "";
        }
        const minutes = Math.round((Date.now() - this.state.loadedAt.getTime()) / 60000);
        return minutes < 1 ? _t("Updated just now") : _t("Updated %s min ago", minutes);
    }

    get activityChart() {
        const activity = this.data.site_activity;
        if (!activity || !activity.filed || !activity.filed.some((v) => v)) {
            return null;
        }
        return {
            key: "site_activity",
            title: _t("Site Activity"),
            // A missing daily report is a day nobody can reconstruct later,
            // which is why the count matters, not only the stoppages.
            subtitle: _t("Daily reports filed each week, and days work stopped"),
            icon: "fa-calendar-check-o",
            span: "o_ad_col_6",
            type: "bar",
            labels: activity.labels,
            series: [
                { name: "filed", label: _t("Reports Filed"), data: activity.filed, format: "integer" },
                { name: "stopped", label: _t("Work Stopped"), data: activity.stopped, format: "integer" },
            ],
            drillable: activity.labels.map(() => false),
        };
    }

    get packageColumns() {
        return [
            { key: "package", label: _t("Package") },
            { key: "contractor", label: _t("Contractor") },
            { key: "value", label: _t("Tender Value"), numeric: true, format: "monetary" },
            { key: "retention", label: _t("Retention %"), numeric: true },
            { key: "status", label: _t("Status"), type: "badge" },
        ];
    }

    get milestoneColumns() {
        return [
            { key: "milestone", label: _t("Milestone") },
            { key: "contractor", label: _t("Contractor") },
            { key: "due", label: _t("Due") },
            { key: "progress", label: _t("Complete"), numeric: true, type: "meter" },
            { key: "status", label: _t("Programme"), type: "badge" },
        ];
    }

    async runBackendAction(method, args, failureMessage) {
        try {
            const action = await this.orm.call(MODEL, method, args);
            await this.action.doAction(action);
        } catch (error) {
            console.error("Construction overview action failed", method, args, error);
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

    async openProject(point) {
        if (point && point.id) {
            await this.action.doAction({
                type: "ir.actions.act_window",
                res_model: "realestate.project",
                res_id: point.id,
                views: [[false, "form"]],
            });
        }
    }
}

registry.category("actions").add("realestate.construction_overview", ConstructionOverview);
