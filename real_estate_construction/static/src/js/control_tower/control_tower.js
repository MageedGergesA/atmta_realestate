/** @odoo-module **/

/**
 * The Construction Control Tower.
 *
 * One RPC returns one payload. Every figure in it was computed server-side by
 * the milestone that owns it — this component formats and routes, and
 * calculates nothing. Anything it added would be a number that reconciles to
 * no record, which is the specific failure a control tower exists to prevent.
 *
 * Three behaviours are deliberate:
 *
 *  - **Zero is business information.** Until the payload arrives the component
 *    renders a skeleton, never a zero. A flash of 0.00 on a budget card is a
 *    lie that a user may act on.
 *  - **Missing is not zero.** `null` from the server renders as "N/A" with the
 *    server's own reason as a tooltip. It never becomes 0.
 *  - **Drilldowns come from the server.** The action and domain are built in
 *    Python beside the aggregate, so a number and the list it opens cannot
 *    drift apart.
 */

import { registry } from "@web/core/registry";
import { Component, onWillStart, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { formatMonetary } from "@web/views/fields/formatters";

/** Format a monetary figure, or say plainly that there isn't one. */
export function money(value, currencyId) {
    if (value === null || value === undefined) {
        return "N/A";
    }
    return formatMonetary(value, { currencyId });
}

/** Format a percentage, preserving the difference between 0% and unknown. */
export function percent(value, digits = 1) {
    if (value === null || value === undefined) {
        return "N/A";
    }
    return `${Number(value).toFixed(digits)}%`;
}

export const STATUS_LABELS = {
    on_track: "On Track",
    attention: "Attention",
    at_risk: "At Risk",
    critical: "Critical",
    no_data: "No Data",
};

export class ControlTower extends Component {
    static template = "real_estate_construction.ControlTower";
    static props = { action: { type: Object, optional: true },
                     actionId: { type: [Number, String], optional: true },
                     className: { type: String, optional: true },
                     updateActionState: { type: Function, optional: true } };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            loading: true,
            error: null,
            projectId: this._initialProjectId(),
            projects: [],
            payload: null,
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
        this.state.projects = await this.orm.searchRead(
            "realestate.project", [], ["display_name"],
            { limit: 200, order: "name" }
        );
        if (!this.state.projectId && this.state.projects.length) {
            this.state.projectId = this.state.projects[0].id;
        }
    }

    async load() {
        if (!this.state.projectId) {
            this.state.loading = false;
            this.state.payload = null;
            return;
        }
        this.state.loading = true;
        this.state.error = null;
        try {
            // One call. Not one per KPI, and never "load everything and
            // aggregate in JavaScript" — business aggregation is server-side.
            this.state.payload = await this.orm.call(
                "realestate.construction.control.tower",
                "payload",
                [this.state.projectId]
            );
        } catch (error) {
            this.state.error = error.message || String(error);
        } finally {
            this.state.loading = false;
        }
    }

    async onProjectChange(ev) {
        this.state.projectId = parseInt(ev.target.value, 10);
        await this.load();
    }

    has(section) {
        const payload = this.state.payload;
        return Boolean(payload && payload[section] && !payload[section].failed);
    }

    failed(section) {
        const payload = this.state.payload;
        return Boolean(payload && payload[section] && payload[section].failed);
    }

    get currencyId() {
        return this.state.payload ? this.state.payload.currency_id : false;
    }

    money(value) {
        return money(value, this.currencyId);
    }

    percent(value) {
        return percent(value);
    }

    statusLabel(status) {
        return STATUS_LABELS[status] || status;
    }

    /** Open the records behind a cost-sheet cell. The server owns the domain. */
    async drill(column, costCodeId) {
        const action = await this.orm.call(
            "realestate.construction.cost.sheet",
            "drilldown",
            [this.state.projectId, column, costCodeId || false]
        );
        if (action) {
            this.action.doAction(action);
        }
    }

    /** Worklist and exception entries carry their own action. */
    openAction(action) {
        if (action) {
            this.action.doAction(action);
        }
    }

    async refresh() {
        await this.load();
    }
}

registry.category("actions").add("construction_control_tower", ControlTower);
