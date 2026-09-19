/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

import { CHART_DEFS, SECTION_ICONS } from "./dashboard_schema";
import { DashboardChart } from "./dashboard_chart";
import { DashboardMap } from "./dashboard_map";
import { KpiCard, formatKpiValue } from "./kpi_card";

/**
 * Rental Dashboard.
 *
 * Two calls: `get_work` renders the tiles, then `get_trends` fills in the
 * charts, so the numbers a user acts on never wait for the charts.
 *
 * Business logic lives in the backend: which tiles a user sees, what each one
 * counts and what it opens. This component formats, lays out, and asks the
 * backend for actions by key.
 */
export class RentalDashboard extends Component {
    static template = "atmta_real_estate.RentalDashboard";
    static components = { KpiCard, DashboardChart, DashboardMap };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.chartDefs = CHART_DEFS;

        this.state = useState({
            /** "loading" | "ready" | "error" for the tiles. */
            status: "loading",
            work: null,
            /** "loading" | "ready" | "error" for the charts, independently. */
            trendsStatus: "loading",
            trends: null,
            scope: "mine",
            errorMessage: "",
            refreshing: false,
        });

        // Not onWillStart: the component renders its loading skeleton at once
        // and re-renders when each payload lands.
        this.loadAll();
    }

    // ------------------------------------------------------------------
    // Data
    // ------------------------------------------------------------------
    async loadAll({ silent = false } = {}) {
        await this.loadWork({ silent });
        if (this.state.status === "ready") {
            await this.loadTrends({ silent });
        }
    }

    async loadWork({ silent = false } = {}) {
        if (!silent) {
            this.state.status = "loading";
        }
        try {
            const work = await this.orm.call(
                "realestate.rental.dashboard", "get_work", [this.state.scope]
            );
            this.state.work = work;
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

    async loadTrends({ silent = false } = {}) {
        if (!silent) {
            this.state.trendsStatus = "loading";
        }
        try {
            this.state.trends = await this.orm.call(
                "realestate.rental.dashboard", "get_trends", []
            );
            this.state.trendsStatus = "ready";
        } catch (error) {
            console.error("Rental dashboard trends failed to load", error);
            this.state.trendsStatus = "error";
        }
    }

    async onRefresh() {
        if (this.state.refreshing) {
            return;
        }
        this.state.refreshing = true;
        try {
            await this.loadAll({ silent: this.state.status === "ready" });
        } finally {
            this.state.refreshing = false;
        }
    }

    async onRetry() {
        await this.loadAll();
    }

    /** Mine / Team only changes the tiles; the charts are portfolio-wide. */
    async setScope(scope) {
        if (scope === this.state.scope) {
            return;
        }
        this.state.scope = scope;
        await this.loadWork({ silent: this.state.status === "ready" });
    }

    // ------------------------------------------------------------------
    // Derived presentation values
    // ------------------------------------------------------------------
    get isLoading() {
        return this.state.status === "loading";
    }

    get isError() {
        return this.state.status === "error";
    }

    get isReady() {
        return this.state.status === "ready" && !!this.state.work;
    }

    get currencyId() {
        return (this.state.work && this.state.work.currency_id) || false;
    }

    get companyName() {
        return (this.state.work && this.state.work.company_name) || "";
    }

    get asOf() {
        return (this.state.work && this.state.work.as_of) || "";
    }

    get quickActions() {
        return (this.state.work && this.state.work.quick_actions) || [];
    }

    /** Render-ready sections, straight from the backend's tile list. */
    get sections() {
        const sections = (this.state.work && this.state.work.sections) || [];
        return sections.map((section) => ({
            id: section.id,
            title: section.title,
            icon: SECTION_ICONS[section.id] || "fa-th-large",
            cards: section.tiles.map((tile) => this.buildCard(tile)),
        }));
    }

    buildCard(tile) {
        const value = Number.isFinite(tile.value) ? tile.value : 0;
        return {
            key: tile.key,
            label: tile.label,
            value,
            formattedValue: formatKpiValue(value, tile.format, { currencyId: this.currencyId }),
            hint: tile.hint || "",
            warning: !!tile.warning,
            clickable: !!tile.drill,
        };
    }

    chartPayload(chartKey) {
        const charts = (this.state.trends && this.state.trends.charts) || {};
        return charts[chartKey] || null;
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

    /** Open the records behind a tile, with the domain the tile counted. */
    async drill(key) {
        if (!key) {
            return;
        }
        await this.runBackendAction(
            "action_drill", [key, this.state.scope],
            _t("Could not open the records behind this figure.")
        );
    }

    async drillArrearsBucket(bucket) {
        await this.runBackendAction(
            "action_drill_arrears_bucket", [bucket],
            _t("Could not open the records behind this segment.")
        );
    }

    async runQuickAction(key) {
        await this.runBackendAction(
            "action_quick", [key], _t("Could not open this screen.")
        );
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
        const modes = (action.view_mode || "list,form")
            .split(",")
            .map((mode) => mode.trim())
            .filter(Boolean);
        return { ...action, views: modes.map((mode) => [false, mode]) };
    }
}

registry.category("actions").add("realestate.rental_dashboard", RentalDashboard);
