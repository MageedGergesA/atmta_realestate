/** @odoo-module **/

import { App } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { getTemplate } from "@web/core/templates";
import { _t } from "@web/core/l10n/translation";
import { MasterPlan2D } from "@real_estate_maquette/js/master_plan_2d";
import { MaquetteViewer } from "@real_estate_maquette/js/maquette_viewer";

/**
 * Frontend service that mounts the OWL 2D + 3D viewers on portal pages.
 *
 *   <div class="o_re_portal_2d_mount" data-project-id="..."/>
 *   <div class="o_re_portal_3d_mount" data-project-id="..."/>
 *
 * The service receives the standard frontend env (with all the registry
 * services already started) so the OWL Apps work out of the box.
 */
async function mountInto(env, target, ComponentClass, props) {
    target.innerHTML = "";
    const app = new App(ComponentClass, {
        env,
        getTemplate,
        props,
        translatableAttributes: ["data-tooltip"],
        translateFn: _t,
        dev: env.debug,
    });
    await app.mount(target);
}

function wireFormEvent() {
    window.addEventListener("re-portal-form", (ev) => {
        const { propertyId, kind } = ev.detail || {};
        const form = document.querySelector(".o_re_eoi_form");
        if (!form) return;
        const hidden = form.querySelector("input[name='property_id']");
        if (hidden) hidden.value = propertyId || 0;
        if (kind === "visit") {
            form.action = form.action.replace(/\/eoi$/, "/visit");
        } else {
            form.action = form.action.replace(/\/visit$/, "/eoi");
        }
        form.scrollIntoView({ behavior: "smooth", block: "center" });
        form.querySelector("input[name='name']")?.focus();
    });
}

export const portalRealEstateViewersService = {
    dependencies: ["orm", "dialog", "notification"],
    async start(env) {
        const t2d = document.querySelectorAll(".o_re_portal_2d_mount");
        const t3d = document.querySelectorAll(".o_re_portal_3d_mount");
        if (!t2d.length && !t3d.length) return;

        for (const el of t2d) {
            const projectId = parseInt(el.dataset.projectId, 10);
            await mountInto(env, el, MasterPlan2D, { projectId, mode: "portal" });
        }
        for (const el of t3d) {
            const projectId = parseInt(el.dataset.projectId, 10);
            await mountInto(env, el, MaquetteViewer, { projectId, mode: "portal" });
        }
        wireFormEvent();
    },
};

registry.category("services").add("real_estate_portal.viewers", portalRealEstateViewersService);
