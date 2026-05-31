/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, onWillStart, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { MaquetteViewer } from "./maquette_viewer";

/**
 * 3D-only client action: pick a project, view its .glb model. The 2D side
 * lives in its own action (real_estate_maquette.master_plan_2d).
 */
export class MaquettePreview3D extends Component {
    static template = "real_estate_maquette.MaquettePreview3D";
    static components = { MaquetteViewer };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.state = useState({ projects: [], projectId: 0 });
        onWillStart(async () => {
            this.state.projects = await this.orm.searchRead(
                "realestate.project",
                [["has_maquette", "=", true]],
                ["id", "name", "code"],
                { order: "name" },
            );
            if (this.state.projects.length) {
                this.state.projectId = this.state.projects[0].id;
            }
        });
    }

    onSelect(ev) {
        this.state.projectId = parseInt(ev.target.value, 10) || 0;
    }
}

registry.category("actions").add("real_estate_maquette.preview_3d", MaquettePreview3D);
