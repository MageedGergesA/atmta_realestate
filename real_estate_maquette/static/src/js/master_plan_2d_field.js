/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component } from "@odoo/owl";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { MasterPlan2D } from "./master_plan_2d";

/**
 * Form-field widget that embeds the full 2D Master Plan editor on the
 * project form. Same component as the standalone menu — but pinned to the
 * current project, no picker.
 *
 * Usage:
 *   <field name="id" widget="master_plan_2d_editor" nolabel="1"/>
 */
export class MasterPlan2DField extends Component {
    static template = "real_estate_maquette.MasterPlan2DField";
    static components = { MasterPlan2D };
    static props = { ...standardFieldProps };

    get projectId() {
        return this.props.record.resId;
    }
}

registry.category("fields").add("master_plan_2d_editor", {
    component: MasterPlan2DField,
});
