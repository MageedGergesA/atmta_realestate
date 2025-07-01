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

        const buildImageUrl = (id) => `/web/image/product.product/${id}/image_128?ts=${Date.now()}`;
        const addImageUrl = (item) => ({ ...item, image_url: buildImageUrl(item.id) });

        const loadDetails = async (id, model) => {
            if (!id || !model) return;

            const result = await this.orm.call("product.product", "get_child_dept", [id, model]);

            this.OrgState.data = {
                parent: result.parent ? addImageUrl(result.parent) : null,
                self: addImageUrl(result.self),
                child: result.child.map(addImageUrl),
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
                loadDetails(productId, model);
            },
            () => [this.props.record.data.product_id]
        );

        onPatched(async () => {
            const id = this.props.record.evalContextWithVirtualIds.id;
            const model = this.props.record.resModel;
            await loadDetails(id, model);
        });
    }

    async ProductDetails(product_id, model) {
        const result = await this.orm.call("product.product", "get_child_dept", [product_id, model]);

        const buildImageUrl = (id) => `/web/image/product.product/${id}/image_128?ts=${Date.now()}`;
        const addImageUrl = (item) => ({ ...item, image_url: buildImageUrl(item.id) });

        this.OrgState.data = {
            parent: result.parent ? addImageUrl(result.parent) : null,
            self: addImageUrl(result.self),
            child: result.child.map(addImageUrl),
        };
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
    displayName: _t("Widget"),
    supportedTypes: ["many2one"],
    extractProps: ({ attrs }) => ({}),
};

registry.category("fields").add("org_chart", orgChart);
