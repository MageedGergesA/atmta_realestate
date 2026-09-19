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
        this.state = useState({ projects: [], projectId: 0, loadError: "" });
        onWillStart(async () => {
            // Project Plans is shown to every internal user, but only roles
            // that may read development projects can list them. Anyone else
            // got a crashed screen; they now get the reason, as on the 2D plan.
            try {
                this.state.projects = await this.orm.searchRead(
                    "realestate.project",
                    [["has_maquette", "=", true]],
                    ["id", "name", "code"],
                    { order: "name" },
                );
            } catch (err) {
                console.warn("[maquette] Opening the 3D maquette failed", err);
                this.state.projects = [];
                this.state.loadError =
                    "The 3D maquette could not be loaded. You may not have access " +
                    "to development projects.";
                return;
            }
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
