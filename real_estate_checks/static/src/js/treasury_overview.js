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

const MODEL = "realestate.check.dashboard";

/** Treasury Overview. Paper and cash are never added together. */
export class TreasuryOverview extends Component {
    static template = "real_estate_checks.TreasuryOverview";
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
            console.error("Treasury overview failed to load", error);
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
    get lifecycle() { return this.data.lifecycle || { segments: [], total: 0 }; }

    get lastUpdatedLabel() {
        if (!this.state.loadedAt) {
            return "";
        }
        const minutes = Math.round((Date.now() - this.state.loadedAt.getTime()) / 60000);
        return minutes < 1 ? _t("Updated just now") : _t("Updated %s min ago", minutes);
    }

    get maturityChart() {
        const maturity = this.data.maturity;
        if (!maturity || !maturity.amounts || !maturity.amounts.some((v) => v)) {
            return null;
        }
        return {
            key: "maturity",
            title: _t("Maturity Forecast"),
            // Expected paper, not guaranteed cash: a cheque can still bounce.
            subtitle: _t("Face value maturing each month — expected paper, not guaranteed cash"),
            icon: "fa-calendar",
            span: "o_ad_col_6",
            type: "bar",
            labels: maturity.labels,
            series: [{
                name: "amounts", label: _t("Maturing"),
                data: maturity.amounts, format: "monetary",
            }],
            drillable: maturity.labels.map(() => false),
        };
    }

    get bankColumns() {
        return [
            { key: "bank", label: _t("Bank") },
            { key: "cheques", label: _t("Cheques"), numeric: true },
            { key: "face", label: _t("Face Value"), numeric: true, format: "monetary" },
            { key: "cleared", label: _t("Cleared"), numeric: true, format: "monetary" },
            { key: "status", label: _t("Record"), type: "badge" },
        ];
    }

    get upcomingColumns() {
        return [
            { key: "cheque", label: _t("Cheque") },
            { key: "drawer", label: _t("Drawer") },
            { key: "bank", label: _t("Bank") },
            { key: "amount", label: _t("Amount"), numeric: true, format: "monetary" },
            { key: "due", label: _t("Due") },
            { key: "status", label: _t("Maturity"), type: "badge" },
        ];
    }

    async runBackendAction(method, args, failureMessage) {
        try {
            const action = await this.orm.call(MODEL, method, args);
            await this.action.doAction(action);
        } catch (error) {
            console.error("Treasury overview action failed", method, args, error);
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

registry.category("actions").add("realestate.treasury_overview", TreasuryOverview);
