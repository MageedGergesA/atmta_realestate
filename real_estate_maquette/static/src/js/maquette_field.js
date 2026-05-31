/** @odoo-module **/

import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component } from "@odoo/owl";
import { MaquetteViewer } from "./maquette_viewer";

/**
 * Odoo field wrapper — embeds the MaquetteViewer inside a form view by
 * declaring widget="maquette_viewer" on any field of the project model.
 *
 *   <field name="id" widget="maquette_viewer"/>
 *
 * The field value itself is irrelevant — the widget reads the record's id and
 * fetches the GLB via the /maquette/glb/<id> endpoint.
 */
export class MaquetteField extends Component {
    static template = "real_estate_maquette.MaquetteField";
    static components = { MaquetteViewer };
    static props = { ...standardFieldProps };

    get projectId() {
        return this.props.record.resId || 0;
    }

    get focusPropertyId() {
        const ctx = (this.env.searchModel && this.env.searchModel.context) || {};
        return ctx.maquette_focus_property_id || 0;
    }

    get focusMeshName() {
        const ctx = (this.env.searchModel && this.env.searchModel.context) || {};
        return ctx.maquette_focus_mesh_name || "";
    }

    get isPersisted() {
        return this.projectId > 0;
    }
}

export const maquetteField = {
    component: MaquetteField,
    supportedTypes: ["binary", "integer"],
};

registry.category("fields").add("maquette_viewer", maquetteField);
