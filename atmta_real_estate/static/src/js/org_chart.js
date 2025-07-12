/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { onWillStart, onWillUpdateProps, useState, useEffect } from "@odoo/owl";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { _t } from "@web/core/l10n/translation";

export class OrgChart extends Component {
    static template = "atmta_real_estate.CustomerChartWidget";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.OrgState = useState({ data: {} });

        const loadDetails = async (id, model) => {
            if (!id || typeof id !== "number" || !model) return;

            const result = await this.orm.call("product.product", "get_child_dept", [id, model]);

            this.OrgState.data = {
                parent: result.parent || null,
                self: result.self,
                child: result.child,
            };
        };

        const getCurrentRecordInfo = (props = this.props) => {
            const id = props.record.evalContextWithVirtualIds.id;
            const model = props.record.resModel;
            return { id, model };
        };

        // Load on widget start
        onWillStart(() => {
            const { id, model } = getCurrentRecordInfo();
            return loadDetails(id, model);
        });

        // Load when the record changes (e.g. user switches records)
        onWillUpdateProps((nextProps) => {
            const { id, model } = getCurrentRecordInfo(nextProps);
            return loadDetails(id, model);
        });

        // Reload when `parent_id` changes
        useEffect(() => {
            const { id, model } = getCurrentRecordInfo();
            loadDetails(id, model);
        }, () => [
            this.props.record.data.parent_id?.res_id,
        ]);

        this.loadDetails = loadDetails;
    }

    onChildClick(id, ev) {
        ev.env.services.action.doAction({
            type: "ir.actions.act_window",
            res_model: ev.props.record.resModel,
            res_id: id,
            views: [[false, "form"]],
            name: "Schedule Log",
            target: "current",
        });
    }
}

export const orgChart = {
    component: OrgChart,
    displayName: _t("Org Chart"),
    supportedTypes: ["many2one"],
    extractProps: () => ({}),
};

registry.category("fields").add("org_chart", orgChart);
