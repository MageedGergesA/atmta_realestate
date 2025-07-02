/** @odoo-module **/
import { registry } from "@web/core/registry";
import { Component } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { onWillStart, onWillUpdateProps, onPatched, useState, useEffect } from "@odoo/owl";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { _t } from "@web/core/l10n/translation";

export class OrgChart extends Component {
    static template = "atmta_real_estate.CustomerChartWidget";
    static props = { ...standardFieldProps };

    setup() {
        super.setup();
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

        onWillStart(async () => {
            const id = this.props.record.evalContextWithVirtualIds.id;
            const model = this.props.record.resModel;
            await loadDetails(id, model);
        });

        onWillUpdateProps(async (nextProps) => {
            const id = nextProps.record.evalContextWithVirtualIds.id;
            const model = nextProps.record.resModel;
            await loadDetails(id, model);
        });

        useEffect(
            () => {
                const productId = this.props.record.data.product_id?.res_id;
                const model = this.props.record.resModel;
                if (typeof productId === "number") {
                    loadDetails(productId, model);
                }
            },
            () => [this.props.record.data.product_id]
        );

        onPatched(async () => {
            const id = this.props.record.evalContextWithVirtualIds.id;
            const model = this.props.record.resModel;
            await loadDetails(id, model);
        });
    }

    onChildClick(id, ev) {
        const action = {
            type: "ir.actions.act_window",
            res_model: ev.props.record.resModel,
            res_id: id,
            views: [
                [false, "form"],
                [false, "list"],
            ],
            name: "Schedule Log",
            target: "current",
        };
        ev.env.services.action.doAction(action);
    }
}

export const orgChart = {
    component: OrgChart,
    displayName: _t("Org Chart"),
    supportedTypes: ["many2one"],
    extractProps: ({ attrs }) => ({}),
};

registry.category("fields").add("org_chart", orgChart);
