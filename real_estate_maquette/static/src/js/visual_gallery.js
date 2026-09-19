/** @odoo-module **/

/**
 * M6 — Presentation Mode.
 *
 * A showroom experience, not a backend form. Full screen, large type, no Odoo
 * chrome inside the viewer, and language a customer can read.
 *
 * ### What "no chrome" does not mean
 *
 * It does not mean fewer permissions. Every request this component makes goes
 * through the same authenticated session and the same record rules as the
 * backend form it replaces; hiding a breadcrumb changes what a salesperson
 * *sees*, never what the server *allows*. The CRM opportunity is read as the
 * real user, so passing somebody else's lead id into the URL fails the same
 * way it would anywhere else.
 *
 * ### The journey
 *
 * ```
 *   PROJECT → MASTER PLAN / 3D → BUILDING → FLOOR → UNIT
 *                                                    ├── COMPARE
 *                                                    ├── SHORTLIST
 *                                                    ├── PAYMENT PLAN
 *                                                    └── RESERVE
 * ```
 *
 * The opportunity is optional throughout. A salesperson walking a visitor
 * through a tower before anybody has taken a name must be able to open this,
 * and requiring a lead first would either block them or make them create a
 * junk one.
 */

import { registry } from "@web/core/registry";
import { Component, onMounted, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";
import { browser } from "@web/core/browser/browser";
import { MaquetteViewer } from "./maquette_viewer";
import { MasterPlan2D } from "./master_plan_2d";

export class VisualGallery extends Component {
    static template = "real_estate_maquette.VisualGallery";
    static components = { MaquetteViewer, MasterPlan2D };
    static props = { action: { type: Object, optional: true }, "*": true };

    setup() {
        this.action = useService("action");
        this.notification = useService("notification");

        // Where the project comes from, in order of reliability.
        //
        // A client action can be reached three ways — an act_window context, an
        // action `params` dict, and a plain URL a salesperson pasted from a
        // deep link — and only the last is guaranteed for a shared link. All
        // three are read, because a gallery that opens blank when somebody
        // follows a link is the failure mode that matters most here.
        const params = (this.props.action && this.props.action.params) || {};
        const context = (this.props.action && this.props.action.context) || {};
        // `browser.location`, not `window.location`: it is the same object
        // at runtime and the one Odoo's own tests can drive.
        const query = new URLSearchParams(browser.location.search);
        const pick = (name) =>
            params[name] || context["default_" + name] || query.get(name);

        this.state = useState({
            loading: true,
            error: "",
            projectId: parseInt(pick("project_id"), 10) || null,
            leadId: parseInt(pick("crm_lead_id"), 10) || null,
            // Deep-link targets, if the salesperson arrived from a shared URL.
            unitId: parseInt(pick("unit_id"), 10) || null,
            buildingId: parseInt(pick("building_id"), 10) || null,

            experience: "3d",
            projects: [],
            ctx: null,
            shortlist: [],
            compareIds: [],
            comparison: null,
            selectedUnit: null,
            paymentPlans: [],
            plansLoading: false,
            fullscreen: false,
        });

        // Loaded after mounting, not in `onWillStart`.
        //
        // A component whose `onWillStart` is still pending is not in the DOM
        // at all, so the "Preparing the gallery…" splash could never appear:
        // the salesperson stared at whatever was on screen before until the
        // whole context arrived. Mounting first means the showroom screen
        // changes the instant somebody opens the gallery.
        //
        // `this.loaded` is exposed so tests can await the real thing instead
        // of guessing at frames.
        onMounted(() => {
            this.loaded = this._load();
        });
    }

    async _load() {
        try {
            await this.loadProjects();
            // With no project named, open the only one there is. A showroom
            // with a single project should not make somebody choose it.
            if (!this.state.projectId && this.state.projects.length === 1) {
                this.state.projectId = this.state.projects[0].id;
            }
            if (this.state.projectId) {
                await this.openProject(this.state.projectId);
            }
        } catch (err) {
            // Rule 4 again: the gallery states what happened rather than
            // handing the customer Odoo's error dialog.
            console.warn("[gallery] could not open", err);
            this.state.error = "The gallery could not be opened.";
        } finally {
            this.state.loading = false;
        }
    }

    async loadProjects() {
        this.state.projects = await rpc("/visual/gallery/projects", {});
    }

    async openProject(projectId) {
        this.state.projectId = projectId;
        const ctx = await rpc("/visual/gallery/context", {
            project_id: projectId,
            crm_lead_id: this.state.leadId || false,
        });
        if (ctx.error) {
            this.state.error = ctx.error;
            return;
        }
        this.state.ctx = ctx;
        this.state.shortlist = ctx.shortlist || [];
        // "Best available" is decided by the viewer's own capability probe;
        // this only respects an explicit project preference.
        const preferred = (ctx.fallback && ctx.fallback.default_experience) || "auto";
        this.state.experience = preferred === "2d" ? "2d" : "3d";
    }

    // ------------------------------------------------------------------
    // Customer actions
    // ------------------------------------------------------------------
    /** The heading, which must never be empty while a project is open. */
    get projectName() {
        if (this.state.ctx && this.state.ctx.project_name) {
            return this.state.ctx.project_name;
        }
        const known = this.state.projects.find(
            (p) => p.id === this.state.projectId);
        return known ? known.name : "Sales Gallery";
    }

    get hasOpportunity() {
        return Boolean(this.state.leadId);
    }

    async onUnitSelected(unit) {
        this.state.selectedUnit = unit || null;
        this.state.paymentPlans = [];
        this.state.plansLoading = false;
        if (!unit || !unit.id) {
            return;
        }
        // Payment plans come from Developer, fetched at selection time rather
        // than carried in the unit payload: a schedule is a commercial value
        // and must not be older than the panel showing it.
        //
        // The request is tagged with the unit it was made for, because a
        // customer clicking through four units faster than the network answers
        // must not end up reading unit 1's schedule under unit 4's name. A
        // late reply for a unit that is no longer selected is dropped.
        const requestedId = unit.id;
        this.state.plansLoading = true;
        try {
            const plans = await rpc("/visual/gallery/payment_plans", {
                property_id: requestedId,
            });
            if (this.state.selectedUnit
                    && this.state.selectedUnit.id === requestedId) {
                this.state.paymentPlans = plans.plans || [];
            }
        } catch (err) {
            // A schedule that cannot be fetched is stated as absent, not as a
            // dialog on a showroom screen. Rule 4 applies to the panel too.
            console.warn("[gallery] payment plans failed", err);
            if (this.state.selectedUnit
                    && this.state.selectedUnit.id === requestedId) {
                this.state.paymentPlans = [];
            }
        } finally {
            if (this.state.selectedUnit
                    && this.state.selectedUnit.id === requestedId) {
                this.state.plansLoading = false;
            }
        }
    }

    // What the panel displays. Formatting only — never arithmetic on a price.
    get unitPrice() {
        const unit = this.state.selectedUnit;
        if (!unit) {
            return "";
        }
        // Whichever the audience payload carried. `visual_price` is the
        // audience-filtered one; `base_price` is what the 2D component reads
        // for an internal user. Neither is computed here.
        const price = unit.visual_price || unit.base_price || 0;
        return price ? this.formatMoney(price, unit.currency || "") : "";
    }

    get unitStatus() {
        const unit = this.state.selectedUnit || {};
        // Developer's answer, mapped for a customer by the server. Never
        // re-derived from a raw state here.
        const status = unit.visual_state || unit.state || "";
        return status ? status.charAt(0).toUpperCase() + status.slice(1) : "—";
    }

    get canReserve() {
        const unit = this.state.selectedUnit;
        if (!unit || !(this.state.ctx && this.state.ctx.can_reserve)) {
            return false;
        }
        // Reservation is Developer's decision; this only avoids offering a
        // button for a unit the server has already said is not available.
        const status = unit.visual_state || unit.state;
        return !status || status === "available";
    }

    formatMoney(amount, currency) {
        const value = Number(amount || 0).toLocaleString(undefined, {
            maximumFractionDigits: 0,
        });
        return currency ? `${value} ${currency}` : value;
    }

    isComparing(unit) {
        return this.state.compareIds.includes(unit && unit.id);
    }

    async toggleShortlist(unit) {
        if (!this.hasOpportunity) {
            this.notification.add(
                "Open the gallery with a customer's opportunity to save favourites.",
                { type: "info" }
            );
            return;
        }
        const shortlisted = this.state.shortlist.some((u) => u.id === unit.id);
        await rpc("/visual/gallery/shortlist", {
            crm_lead_id: this.state.leadId,
            property_id: unit.id,
            source: this.state.experience,
            remove: shortlisted,
        });
        const refreshed = await rpc("/visual/gallery/context", {
            project_id: this.state.projectId,
            crm_lead_id: this.state.leadId,
        });
        this.state.shortlist = refreshed.shortlist || [];
    }

    isShortlisted(unit) {
        return this.state.shortlist.some((u) => u.id === (unit && unit.id));
    }

    toggleCompare(unit) {
        const ids = this.state.compareIds;
        const at = ids.indexOf(unit.id);
        if (at >= 0) {
            ids.splice(at, 1);
        } else if (ids.length < 4) {
            ids.push(unit.id);
        } else {
            this.notification.add(
                "Compare up to four units at a time.", { type: "info" });
        }
        this.state.comparison = null;
    }

    async openComparison() {
        if (this.state.compareIds.length < 2) {
            this.notification.add(
                "Choose at least two units to compare.", { type: "info" });
            return;
        }
        // Re-read rather than compare what the page already had: a unit
        // reserved since it was selected must compare as reserved.
        const result = await rpc("/visual/gallery/compare", {
            property_ids: this.state.compareIds,
        });
        if (result.error) {
            this.notification.add(result.error, { type: "warning" });
            return;
        }
        this.state.comparison = result;
    }

    closeComparison() {
        this.state.comparison = null;
    }

    async reserveUnit(unit) {
        // Developer's own reservation form, with its advisory lock and its
        // availability re-check. Nothing here writes a status.
        await this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "realestate.unit.reservation",
            view_mode: "form",
            views: [[false, "form"]],
            target: "current",
            context: {
                default_property_id: unit.id,
                ...(this.state.leadId
                    ? { default_crm_lead_id: this.state.leadId } : {}),
            },
        });
    }

    // ------------------------------------------------------------------
    setExperience(experience) {
        this.state.experience = experience;
    }

    toggleFullscreen() {
        const root = document.querySelector(".o_visual_gallery");
        if (!document.fullscreenElement && root && root.requestFullscreen) {
            root.requestFullscreen().catch(() => {
                // Denied by the browser (no user gesture, or a policy). The
                // gallery still fills its container, so this is cosmetic.
                this.state.fullscreen = false;
            });
            this.state.fullscreen = true;
        } else if (document.exitFullscreen && document.fullscreenElement) {
            document.exitFullscreen();
            this.state.fullscreen = false;
        }
    }

    exitGallery() {
        this.action.doAction("real_estate_maquette.action_visual_gallery_exit");
    }
}

registry.category("actions").add("realestate.visual_gallery", VisualGallery);
