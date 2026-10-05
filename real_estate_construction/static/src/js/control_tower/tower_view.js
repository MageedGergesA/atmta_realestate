/** @odoo-module **/

/**
 * The Construction Control Tower, rendered through the suite design system.
 *
 * The backend is untouched: `payload()` still returns every figure, computed
 * server-side by the milestone that owns it, and this view calculates
 * nothing. What changed is that the tower now speaks the same visual language
 * as every other dashboard -- the shared KPI card, panel, row list and data
 * table -- instead of its own bordered boxes.
 *
 * Three behaviours carry over unchanged, because they are the point of a
 * control tower rather than decoration:
 *
 *  - **Zero is business information.** Until the payload arrives this renders
 *    a skeleton, never a zero. A flash of $0.00 on a budget card is a lie a
 *    user may act on.
 *  - **Missing is not zero.** `null` renders as "N/A" carrying the server's
 *    own reason, and never becomes 0. An EAC nobody has produced is not an
 *    EAC of zero.
 *  - **Drilldowns come from the server**, built in Python beside the
 *    aggregate, so a figure and the list it opens cannot drift apart.
 */

import { Component, onWillStart, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

import { AtmtaKpiCard } from "@atmta_dashboard/js/components/kpi_card";
import { money as formatTowerMoney } from "./tower_format";
import {
    AtmtaCard, AtmtaDataTable, AtmtaRowList, AtmtaSkeleton,
} from "@atmta_dashboard/js/components/panels";
import { AtmtaFilterBar } from "@atmta_dashboard/js/components/filters";
import { AtmtaDonut } from "@atmta_dashboard/js/components/donut";
import { AtmtaMapCard } from "@atmta_dashboard/js/components/map_card";
import { AtmtaDashboardChart } from "@atmta_dashboard/js/atmta_dashboard";

const MODEL = "realestate.construction.control.tower";

/** Health status -> the suite's tone vocabulary. */
const STATUS_TONE = {
    on_track: "success",
    attention: "warning",
    at_risk: "danger",
    critical: "danger",
    no_data: "neutral",
};

const STATUS_LABEL = {
    on_track: _t("On Track"),
    attention: _t("Attention"),
    at_risk: _t("At Risk"),
    critical: _t("Critical"),
    no_data: _t("No Data"),
};

/** Exception class -> severity, so the worst kind sorts and colours first. */
const EXCEPTION_SEVERITY = {
    financial: "critical",
    forecast: "critical",
    commercial: "warning",
    quality: "warning",
    document: null,
    configuration: null,
};

export class ConstructionTower extends Component {
    static template = "real_estate_construction.ConstructionTower";
    static components = {
        AtmtaKpiCard, AtmtaCard, AtmtaDataTable, AtmtaRowList, AtmtaSkeleton,
        AtmtaFilterBar, AtmtaDonut, AtmtaDashboardChart, AtmtaMapCard,
    };
    static props = {
        action: { type: Object, optional: true },
        actionId: { type: [Number, String], optional: true },
        className: { type: String, optional: true },
        updateActionState: { type: Function, optional: true },
    };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({
            status: "loading",
            errorMessage: "",
            projectId: this._initialProjectId(),
            projects: [],
            payload: null,
            refreshing: false,
            loadedAt: null,
        });
        onWillStart(async () => {
            await this.loadProjects();
            await this.load();
        });
    }

    _initialProjectId() {
        const params = (this.props.action && this.props.action.params) || {};
        const context = (this.props.action && this.props.action.context) || {};
        return params.project_id || context.active_id || null;
    }

    async loadProjects() {
        // The backend decides the order, because only it knows which projects
        // carry construction data. Sorting by name here opened the tower on
        // whichever project sorted first -- usually one with no contract and
        // no budget, so every figure read N/A and the screen looked broken
        // rather than empty.
        try {
            this.state.projects = await this.orm.call(
                MODEL, "projects_for_picker", []);
        } catch (error) {
            console.error("Control Tower: project list failed", error);
            this.state.projects = [];
        }
        if (!this.state.projectId && this.state.projects.length) {
            this.state.projectId = this.state.projects[0].id;
        }
    }

    async load({ silent = false } = {}) {
        if (!this.state.projectId) {
            this.state.payload = null;
            this.state.status = "empty";
            return;
        }
        if (!silent) {
            this.state.status = "loading";
        }
        try {
            this.state.payload = await this.orm.call(
                MODEL, "payload", [this.state.projectId]);
            this.state.loadedAt = new Date();
            this.state.errorMessage = "";
            this.state.status = "ready";
        } catch (error) {
            console.error("Control Tower failed to load", error);
            this.state.errorMessage =
                (error && error.data && error.data.message) ||
                _t("The control tower data could not be loaded.");
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
        if (key === "project_id" && value && value !== "all") {
            this.state.projectId = Number(value);
            await this.load({ silent: this.state.status === "ready" });
        }
    }

    // ------------------------------------------------------------------
    get isLoading() { return this.state.status === "loading"; }
    get isError() { return this.state.status === "error"; }
    get isEmpty() { return this.state.status === "empty"; }
    get isReady() { return this.state.status === "ready" && !!this.state.payload; }
    get payload() { return this.state.payload || {}; }
    get currencyId() { return this.payload.currency_id || false; }
    get sections() { return this.payload.sections || []; }

    has(section) { return this.sections.includes(section); }

    get lastUpdatedLabel() {
        if (!this.state.loadedAt) {
            return "";
        }
        const minutes = Math.round((Date.now() - this.state.loadedAt.getTime()) / 60000);
        return minutes < 1 ? _t("Updated just now") : _t("Updated %s min ago", minutes);
    }

    get filters() {
        if (!this.state.projects.length) {
            return [];
        }
        return [{
            key: "project_id",
            label: _t("Project"),
            icon: "fa-cubes",
            // No "All" option: the tower reports on one project at a time,
            // and a combined cost position across projects is a number that
            // reconciles to no contract.
            options: this.state.projects.map((p) => ({
                key: p.id,
                label: p.has_data ? p.display_name : `${p.display_name} —`,
            })),
        }];
    }

    get filterValues() {
        return { project_id: this.state.projectId };
    }

    // ------------------------------------------------------------------
    // Health
    // ------------------------------------------------------------------
    get healthStatus() {
        const health = this.payload.health || {};
        return {
            label: STATUS_LABEL[health.status] || health.status || "",
            tone: STATUS_TONE[health.status] || "neutral",
        };
    }

    get healthRows() {
        const health = this.payload.health || {};
        return (health.dimensions || []).map((d) => ({
            key: `dim_${d.key}`,
            label: d.key.charAt(0).toUpperCase() + d.key.slice(1),
            sublabel: d.reason || "",
            // The chip carries the verdict; the count beside it would be a
            // second, unrelated number in the same row.
            badge: STATUS_LABEL[d.status] || d.status,
            badgeTone: STATUS_TONE[d.status] || "neutral",
            severity: d.status === "at_risk" || d.status === "critical"
                ? "critical"
                : (d.status === "attention" ? "warning" : null),
        }));
    }

    get exceptionRows() {
        const block = this.payload.exceptions || {};
        const rows = (block.exceptions || []).map((e) => ({
            key: e.key,
            label: e.message,
            sublabel: e.class,
            value: e.count,
            severity: EXCEPTION_SEVERITY[e.class] || null,
            icon: "fa-exclamation-circle",
            _action: e.openable ? e.action : null,
        }));
        // Worst class first. A configuration note and a financial exception
        // are not the same kind of problem.
        const rank = { critical: 0, warning: 1 };
        rows.sort((a, b) => (rank[a.severity] ?? 2) - (rank[b.severity] ?? 2));
        return rows;
    }

    // ------------------------------------------------------------------
    // KPI tiles
    // ------------------------------------------------------------------
    tile(key, label, value, options = {}) {
        return {
            key,
            label,
            value,
            format: options.format || "monetary",
            icon: options.icon || "fa-th-large",
            tone: options.tone || "primary",
            hint: options.hint || "",
            qualifier: options.qualifier || null,
            suffix: options.suffix || null,
            higher_is_better: options.higherIsBetter !== false,
            // Not authorised: drawn dashed so it cannot be read as a baseline.
            pending: !!options.pending,
            warning: !!options.warning,
            warning_label: options.warningLabel || _t("Needs attention"),
            // Nothing here opens a list: the tower's own drilldowns are built
            // server-side per figure, and a half-wired one would be worse
            // than none.
            drill: false,
        };
    }

    get costTiles() {
        const cost = this.payload.cost;
        if (!cost) {
            return [];
        }
        // ETC, EAC and the variance all come out of an approved forecast, not
        // the ledger. They carry the same "Modelled" badge Investment uses,
        // so a projection is never read as a posted figure.
        const modelled = { qualifier: "modelled" };
        return [
            this.tile("current_budget", _t("Current Budget"), cost.current_budget, {
                icon: "fa-balance-scale", tone: "primary",
                hint: _t("Original %s plus approved changes.",
                         this.money(cost.original_budget)),
            }),
            this.tile("current_commitment", _t("Current Commitment"), cost.current_commitment, {
                icon: "fa-file-text-o", tone: "info",
                hint: _t("Orders and awarded packages. Money promised, not yet spent."),
            }),
            this.tile("actual_cost", _t("Actual Cost"), cost.actual_cost, {
                icon: "fa-money", tone: "warning", higherIsBetter: false,
                qualifier: "posted",
                hint: _t("Posted to the analytic ledger. This has been spent."),
            }),
            this.tile("etc", _t("Cost to Complete"), cost.etc, {
                icon: "fa-hourglass-half", tone: "info", ...modelled,
                hint: _t("From the approved forecast. Absent, not zero, when no forecast is approved."),
            }),
            this.tile("eac", _t("Estimate at Completion"), cost.eac, {
                icon: "fa-flag-checkered", tone: "primary", ...modelled,
                higherIsBetter: false,
                hint: _t("Actual plus cost to complete."),
            }),
            this.tile("forecast_variance", _t("Forecast Variance"), cost.forecast_variance, {
                icon: "fa-line-chart",
                tone: (cost.forecast_variance ?? 0) < 0 ? "danger" : "success",
                ...modelled, higherIsBetter: true,
                warning: (cost.forecast_variance ?? 0) < 0,
                warningLabel: _t("Over budget"),
                hint: _t("Current budget minus EAC. Negative means the job is forecast to overrun."),
            }),
        ];
    }

    get changeTiles() {
        const change = this.payload.change;
        if (!change) {
            return [];
        }
        return [
            this.tile("potential_exposure", _t("Potential Change Exposure"),
                      change.potential_cost_exposure, {
                icon: "fa-exclamation-triangle", tone: "warning", higherIsBetter: false,
                pending: true,
                hint: _t("%s open change event(s). Not in the budget and not in the EAC.",
                         change.open_change_events),
            }),
            this.tile("approved_unimplemented", _t("Approved, Not Implemented"),
                      change.approved_unimplemented, {
                format: "integer", icon: "fa-clock-o", tone: "warning",
                higherIsBetter: false, pending: true,
                hint: _t("Authorised, but the baseline does not reflect them yet."),
            }),
        ];
    }

    get claimTiles() {
        const claims = this.payload.claims;
        if (!claims) {
            return [];
        }
        return [
            this.tile("claimed_cost", _t("Claimed"), claims.claimed_cost, {
                icon: "fa-gavel", tone: "neutral", higherIsBetter: false, pending: true,
                hint: _t("Requested by the contractor, not granted."),
            }),
            this.tile("determined_cost", _t("Determined"), claims.determined_cost, {
                icon: "fa-balance-scale", tone: "info",
                hint: _t("Assessed. %s implemented.", this.money(claims.implemented_cost)),
            }),
            this.tile("claimed_eot", _t("Extension of Time Claimed"),
                      claims.claimed_eot_days, {
                format: "integer", suffix: _t(" days"), icon: "fa-calendar-plus-o",
                tone: "warning", higherIsBetter: false,
                hint: _t("%s day(s) approved.", claims.approved_eot_days ?? 0),
            }),
        ];
    }

    get certificateTiles() {
        const certs = this.payload.certificates;
        if (!certs) {
            return [];
        }
        return [
            this.tile("certified", _t("Certified"), certs.certified_amount, {
                icon: "fa-check-circle", tone: "success",
                hint: _t("Work the engineer has certified as done."),
            }),
            this.tile("disallowed", _t("Disallowed"), certs.disallowed_amount, {
                icon: "fa-ban", tone: "danger", higherIsBetter: false,
            }),
            this.tile("retention", _t("Retention Outstanding"),
                      certs.retention_outstanding, {
                icon: "fa-lock", tone: "neutral",
                hint: _t("Held back against defects, and still owed."),
            }),
            this.tile("advance", _t("Advance Outstanding"), certs.advance_outstanding, {
                icon: "fa-undo", tone: "warning", higherIsBetter: false,
                hint: _t("Paid up front and not yet recovered."),
            }),
        ];
    }

    /**
     * A reading in a secondary panel.
     *
     * These dimensions are not headline figures. Quality, information, risk,
     * claims and certification are five sets of four readings, and giving
     * each its own row of KPI cards produced five near-identical bands of
     * mostly zeros that filled two thirds of the width and said very little.
     * A row list says the same thing in a quarter of the space and lets three
     * dimensions sit side by side, which is how every other dashboard in the
     * suite presents its secondary detail.
     */
    reading(key, label, value, options = {}) {
        return {
            key,
            label,
            sublabel: options.sublabel || "",
            value,
            format: options.format || "integer",
            icon: options.icon || "fa-circle-o",
            severity: options.severity || null,
        };
    }

    get qualityRows() {
        const q = this.payload.quality;
        if (!q) {
            return [];
        }
        return [
            this.reading("open_ncrs", _t("Open Non-Conformances"), q.open_ncrs, {
                icon: "fa-exclamation-triangle",
                sublabel: _t("Work that did not meet specification"),
                severity: q.open_ncrs ? "critical" : null,
            }),
            this.reading("high_ncrs", _t("High Severity"), q.high_severity_ncrs, {
                icon: "fa-fire",
                severity: q.high_severity_ncrs ? "critical" : null,
            }),
            this.reading("overdue_ncrs", _t("Overdue"), q.overdue_ncrs, {
                icon: "fa-clock-o",
                severity: q.overdue_ncrs ? "warning" : null,
            }),
            this.reading("fpy", _t("First-Pass Yield"), q.first_pass_yield_pct, {
                format: "percent", icon: "fa-check",
                sublabel: _t("Passed without a re-inspection"),
            }),
        ];
    }

    get informationRows() {
        const i = this.payload.information;
        if (!i) {
            return [];
        }
        return [
            this.reading("open_rfis", _t("Open RFIs"), i.open_rfis, {
                icon: "fa-question-circle",
            }),
            this.reading("overdue_rfis", _t("Overdue RFIs"), i.overdue_rfis, {
                icon: "fa-clock-o",
                sublabel: _t("A decision the site is waiting on"),
                severity: i.overdue_rfis ? "warning" : null,
            }),
            this.reading("submittals", _t("Pending Submittals"), i.pending_submittals, {
                icon: "fa-inbox",
            }),
            this.reading("transmittals", _t("Transmittals Unacknowledged"),
                         i.transmittals_awaiting_acknowledgement, {
                icon: "fa-paper-plane-o",
            }),
        ];
    }

    get riskRows() {
        const r = this.payload.risk;
        if (!r) {
            return [];
        }
        return [
            this.reading("open_risks", _t("Open Risks"), r.open_risks, {
                icon: "fa-shield",
            }),
            this.reading("high_risks", _t("High Risks"), r.high_risks, {
                icon: "fa-fire",
                severity: r.high_risks ? "critical" : null,
            }),
            this.reading("unmitigated", _t("Without Mitigation"),
                         r.risks_without_mitigation, {
                icon: "fa-ban",
                sublabel: _t("A risk nobody has planned a response to"),
                severity: r.risks_without_mitigation ? "warning" : null,
            }),
            this.reading("overdue_issues", _t("Overdue Issues"), r.overdue_issues, {
                icon: "fa-clock-o",
                severity: r.overdue_issues ? "warning" : null,
            }),
        ];
    }

    get claimRows() {
        const c = this.payload.claims;
        if (!c) {
            return [];
        }
        return [
            this.reading("claimed_cost", _t("Claimed"), c.claimed_cost, {
                format: "monetary", icon: "fa-gavel",
                sublabel: _t("Requested by the contractor, not granted"),
            }),
            this.reading("determined_cost", _t("Determined"), c.determined_cost, {
                format: "monetary", icon: "fa-balance-scale",
                sublabel: _t("Assessed; %s implemented", this.money(c.implemented_cost)),
            }),
            this.reading("claimed_eot", _t("Extension of Time Claimed"),
                         c.claimed_eot_days, {
                icon: "fa-calendar-plus-o",
                sublabel: _t("%s day(s) approved", c.approved_eot_days ?? 0),
            }),
        ];
    }

    get certificateRows() {
        const c = this.payload.certificates;
        if (!c) {
            return [];
        }
        return [
            this.reading("certified", _t("Certified"), c.certified_amount, {
                format: "monetary", icon: "fa-check-circle",
                sublabel: _t("Work the engineer has certified as done"),
            }),
            this.reading("disallowed", _t("Disallowed"), c.disallowed_amount, {
                format: "monetary", icon: "fa-ban",
                severity: c.disallowed_amount ? "warning" : null,
            }),
            this.reading("retention", _t("Retention Outstanding"),
                         c.retention_outstanding, {
                format: "monetary", icon: "fa-lock",
                sublabel: _t("Held against defects, and still owed"),
            }),
            this.reading("advance", _t("Advance Outstanding"), c.advance_outstanding, {
                format: "monetary", icon: "fa-undo",
                sublabel: _t("Paid up front and not yet recovered"),
            }),
        ];
    }

    // ------------------------------------------------------------------
    // Progress
    // ------------------------------------------------------------------
    get progressRows() {
        const p = this.payload.progress;
        if (!p) {
            return [];
        }
        const rows = [
            ["planned", _t("Planned"), p.planned_pct, p.planned_reason],
            ["physical", _t("Physical"), p.physical_pct, p.physical_basis],
            ["certified", _t("Certified"), p.certified_pct, p.certified_basis],
            ["financial", _t("Financial"), p.financial_pct, p.financial_basis],
        ];
        return rows.map(([key, label, value, basis]) => ({
            key: `progress_${key}`,
            label,
            sublabel: basis || "",
            // `null` stays null so the row says N/A. A planned percentage the
            // programme never produced is not 0% complete.
            value: value === null || value === undefined ? null : Number(value),
            format: "percent",
        }));
    }

    // ------------------------------------------------------------------
    // Cost sheet
    // ------------------------------------------------------------------
    get costSheetColumns() {
        return [
            { key: "code", label: _t("Cost Code") },
            { key: "name", label: _t("Description") },
            { key: "current_budget", label: _t("Budget"), numeric: true, format: "monetary" },
            { key: "current_commitment", label: _t("Committed"), numeric: true, format: "monetary" },
            { key: "actual_cost", label: _t("Actual"), numeric: true, format: "monetary" },
            { key: "etc", label: _t("To Complete"), numeric: true, format: "monetary" },
            { key: "eac", label: _t("EAC"), numeric: true, format: "monetary" },
            { key: "forecast_variance", label: _t("Variance"), numeric: true, format: "monetary" },
            { key: "status", label: _t("Position"), type: "badge" },
        ];
    }

    get costSheetRows() {
        const sheet = this.payload.cost_sheet;
        if (!sheet) {
            return [];
        }
        const rows = (sheet.rows || []).map((row) => {
            const variance = row.forecast_variance;
            let status = _t("No forecast");
            let tone = "neutral";
            if (variance !== null && variance !== undefined) {
                // A rounding tail of a few cents is not an overrun.
                if (variance < -1) {
                    status = _t("Over");
                    tone = "danger";
                } else {
                    status = _t("Within");
                    tone = "success";
                }
            }
            return { ...row, id: row.cost_code_id, status, status_tone: tone };
        });
        // Worst overrun first: a cost sheet earns its place by saying WHICH
        // line is in trouble, which the project total cannot.
        rows.sort((a, b) => (a.forecast_variance ?? 0) - (b.forecast_variance ?? 0));
        return rows;
    }

    get costChart() {
        const rows = this.costSheetRows.filter((r) => r.current_budget);
        if (!rows.length) {
            return null;
        }
        const top = rows.slice(0, 8);
        return {
            key: "cost_by_code",
            title: _t("Budget, Actual and Forecast by Cost Code"),
            subtitle: _t("Where the overrun actually sits — the project total cannot say"),
            icon: "fa-bar-chart",
            span: "o_ad_col_8",
            type: "bar",
            labels: top.map((r) => r.code),
            series: [
                { name: "budget", label: _t("Budget"),
                  data: top.map((r) => r.current_budget || 0), format: "monetary" },
                { name: "actual", label: _t("Actual"),
                  data: top.map((r) => r.actual_cost || 0), format: "monetary" },
                { name: "eac", label: _t("EAC"),
                  data: top.map((r) => r.eac || 0), format: "monetary",
                  type: "line" },
            ],
            drillable: top.map(() => false),
        };
    }

    get siteMap() {
        const map = this.payload.site_map;
        if (!map || map.failed || !(map.points || []).length) {
            return null;
        }
        return map;
    }

    async openSiteProperty(point) {
        // The map hands over the whole point, not an id.
        const id = point && (point.id || point);
        if (!id) {
            return;
        }
        try {
            await this.action.doAction({
                type: "ir.actions.act_window",
                res_model: "realestate.property",
                res_id: id,
                views: [[false, "form"]],
            });
        } catch (error) {
            console.error("Control Tower: could not open the property", error);
            this.notification.add(_t("Could not open this structure."),
                                  { type: "warning" });
        }
    }

    get progressDonut() {
        const p = this.payload.progress;
        if (!p || p.physical_pct === null || p.physical_pct === undefined) {
            return null;
        }
        const done = Number(p.physical_pct);
        return {
            segments: [
                { key: "done", label: _t("Complete"), value: Math.round(done), tone: "success" },
                { key: "remaining", label: _t("Remaining"),
                  value: Math.max(100 - Math.round(done), 0), tone: "neutral" },
            ],
            total: `${done.toFixed(1)}%`,
            totalLabel: _t("Physical"),
        };
    }

    // ------------------------------------------------------------------
    money(value) {
        // The tower's own formatter, so a hint and the card above it say
        // "N/A" in exactly the same words.
        return formatTowerMoney(value, this.currencyId);
    }

    async openException(key) {
        const row = this.exceptionRows.find((r) => r.key === key);
        if (!row || !row._action) {
            return;
        }
        try {
            await this.action.doAction(row._action);
        } catch (error) {
            console.error("Control Tower: exception drilldown failed", error);
            this.notification.add(_t("Could not open these records."),
                                  { type: "warning" });
        }
    }
}

registry.category("actions").add("construction_control_tower", ConstructionTower);
