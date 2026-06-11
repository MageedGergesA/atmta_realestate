/** @odoo-module **/

import { Component, onWillStart, useRef, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";
import { CarouselDialog } from "@real_estate_maquette/js/image_carousel_dialog";

const STATE_COLORS = {
    available: "#22c55e",
    reserved: "#f59e0b",
    rented: "#3b82f6",
    sold: "#ef4444",
    maintenance: "#a855f7",
    inactive: "#6b7280",
};

/**
 * Drill-down plan viewer: open at any property → see its plan image with
 * polygon regions → click a region drills into the target (if the target has
 * its own plan image) or opens the target's form (leaf).
 */
export class PropertyPlanViewer extends Component {
    static template = "real_estate_plan.PropertyPlanViewer";
    static components = { Dialog };
    static props = {
        action: { type: Object, optional: true },
        actionId: { type: [String, Boolean], optional: true },
        className: { type: String, optional: true },
        globalState: { type: Object, optional: true },
        resId: { type: [Number, Boolean], optional: true },
    };

    setup() {
        this.orm = useService("orm");
        try {
            this.action = useService("action");
        } catch (_e) {
            this.action = null;
        }
        this.notification = useService("notification");
        this.dialog = useService("dialog");

        this.state = useState({
            stack: [],       // breadcrumb of property nodes
            hoveredRegionId: null,
            loading: true,
            selectedTarget: null,     // leaf preview panel
            lightboxImage: null,      // currently-open full-size image url
        });
        this.overlayRef = useRef("overlay");

        // Entry can be either a property OR a project. The viewer treats
        // both identically once loaded — only the initial endpoint differs.
        const ctx = this.props.action?.context || {};
        const params = this.props.action?.params || {};
        const initialProjectId =
            params.project_id || ctx.default_project_id || ctx.project_id || null;
        const initialPropertyId =
            this.props.resId ||
            params.property_id ||
            ctx.default_property_id ||
            ctx.property_id ||
            null;

        onWillStart(async () => {
            if (initialProjectId) {
                await this._navigateToProject(initialProjectId, /*push=*/true);
            } else if (initialPropertyId) {
                await this._navigateTo(initialPropertyId, /*push=*/true);
            }
            this.state.loading = false;
        });
    }

    get current() {
        return this.state.stack[this.state.stack.length - 1] || null;
    }

    get hasImage() {
        return !!(this.current && this.current.plan_image_url);
    }

    /**
     * Children list shown in the bottom strip — restricted to children that
     * actually have a polygon region drawn on the parent's plan image. A child
     * with no region cannot be reached by clicking the image, so listing it
     * is misleading.
     */
    get assignedChildren() {
        if (!this.current) return [];
        const targetIds = new Set(
            (this.current.regions || []).map((r) => r.target_id),
        );
        return (this.current.children || []).filter((c) => targetIds.has(c.id));
    }

    /**
     * Children that themselves carry a plan image — used as the picker grid
     * when the current node has no plan image of its own. Lets the user open
     * a project (no master_plan_2d set) and still drill into a compound /
     * tower / villa whose owner has fully configured its own plan.
     */
    get drillableChildren() {
        if (!this.current) return [];
        return (this.current.children || []).filter((c) => c.has_plan_image);
    }

    /** All children, drillable or not — for the leaves-only fallback. */
    get allChildren() {
        return (this.current && this.current.children) || [];
    }

    childThumbUrl(child) {
        // Sized thumbnail — Odoo serves a 240×150 variant via /web/image/...
        // instead of the full image, which is the main reason the picker
        // grid was slow to render on the server.
        return `/web/image/realestate.property/${child.id}/plan_image/240x150`;
    }

    /**
     * Close the viewer reliably regardless of internal drill level or entry
     * path. Strategies tried in order:
     *  1) dialog mode (target: 'new')          → call the dialog's close()
     *  2) fullscreen / current action          → restore the previous breadcrumb
     *  3) fullscreen with no prior controller  → browser history back
     *  4) fully orphaned page                  → navigate to webclient root
     *
     * Drilling internally only mutates this.state.stack — it does NOT push
     * onto Odoo's controller stack — so restore() always lands on the form
     * the user came from (project or property), no matter how deep they are.
     */
    async closeViewer() {
        const close = this.env?.dialogData?.close;
        if (typeof close === "function") {
            try { close(); return; } catch (_e) { /* fall through */ }
        }
        if (this.action && typeof this.action.restore === "function") {
            try {
                await this.action.restore();
                return;
            } catch (_e) { /* no prior controller — fall through */ }
        }
        if (typeof window !== "undefined" && window.history && window.history.length > 1) {
            window.history.back();
            return;
        }
        if (typeof window !== "undefined") {
            window.location.assign("/odoo");
        }
    }

    polygonPoints(region) {
        return (region.polygon || []).map((p) => `${p[0]},${p[1]}`).join(" ");
    }

    stateColor(state) {
        return STATE_COLORS[state] || "#94a3b8";
    }

    regionFillOpacity(region) {
        if (this.state.hoveredRegionId === region.id) return "0.55";
        return "0.3";
    }

    async _navigateTo(propertyId, push = true) {
        this.state.loading = true;
        // Clear any stale preview from the previous node so the side panel
        // shows content for the new context.
        this.state.selectedTarget = null;
        try {
            const data = await rpc(
                `/real_estate_plan/property_view/${propertyId}`, {});
            if (data.error) {
                this.notification.add(
                    "Failed to load: " + data.error, { type: "danger" });
                return;
            }
            const node = {
                ...data.property,
                regions: data.regions || [],
                children: data.children || [],
            };
            if (push) {
                this.state.stack.push(node);
            } else {
                this.state.stack[this.state.stack.length - 1] = node;
            }
            // Always populate the side panel with the current node's details
            // and image gallery. Every level (compound / building / floor /
            // apartment / room) can have its own images, so the carousel
            // should be reachable everywhere — not only at the deepest leaf.
            await this._openTargetPreview(propertyId);
        } finally {
            this.state.loading = false;
        }
    }

    async _navigateToProject(projectId, push = true) {
        this.state.loading = true;
        this.state.selectedTarget = null;
        try {
            const data = await rpc(
                `/real_estate_plan/project_view/${projectId}`, {});
            if (data.error) {
                this.notification.add(
                    "Failed to load project: " + data.error, { type: "danger" });
                return;
            }
            const node = {
                ...data.property,
                regions: data.regions || [],
                children: data.children || [],
            };
            if (push) {
                this.state.stack.push(node);
            } else {
                this.state.stack[this.state.stack.length - 1] = node;
            }
        } finally {
            this.state.loading = false;
        }
    }

    async onRegionClick(region) {
        if (!this.current) return;
        const target = (this.current.children || []).find(
            (c) => c.id === region.target_id);
        if (target && target.has_plan_image) {
            // Drill into the next level
            await this._navigateTo(region.target_id, true);
            return;
        }
        // Leaf node — show the image-preview panel instead of jumping to form
        await this._openTargetPreview(region.target_id);
    }

    async _openTargetPreview(targetId) {
        try {
            const recs = await this.orm.read(
                "realestate.property", [targetId],
                ["display_name", "property_code", "hierarchy_level", "state",
                 "area_sqm", "base_price", "bedroom_count", "bathroom_count",
                 "floor_number"],
            );
            if (!recs.length) {
                this.notification.add("Property not found.", { type: "warning" });
                return;
            }
            const rec = recs[0];
            // Image gallery — property.image rows + the property's own floor_plan
            // / hero images, if any. Falls back gracefully if those fields
            // aren't present.
            let images = [];
            try {
                const imgs = await this.orm.searchRead(
                    "property.image",
                    [["property_id", "=", targetId]],
                    ["id"],
                    { order: "sequence, id" },
                );
                for (const i of imgs) {
                    images.push({
                        id: `img-${i.id}`,
                        // image_128 is one of the built-in variants from
                        // image.mixin → tiny payload for the side panel.
                        thumb: `/web/image/property.image/${i.id}/image_128`,
                        full: `/web/image/property.image/${i.id}/image_1024`,
                    });
                }
            } catch (_e) { /* property.image may not exist */ }
            // Floor-plan image on the property itself (maquette field)
            try {
                const ifp = await this.orm.searchRead(
                    "realestate.property",
                    [["id", "=", targetId], ["floor_plan_image", "!=", false]],
                    ["id"],
                );
                if (ifp.length) {
                    images.unshift({
                        id: `fp-${targetId}`,
                        thumb: `/web/image/realestate.property/${targetId}/floor_plan_image/128x128`,
                        full: `/web/image/realestate.property/${targetId}/floor_plan_image/1024x1024`,
                    });
                }
            } catch (_e) { /* field missing */ }
            this.state.selectedTarget = { ...rec, images };
        } catch (e) {
            console.error("[plan viewer] preview load failed:", e);
            this.notification.add("Failed to load preview: " + (e.message || e),
                { type: "danger" });
        }
    }

    closeTargetPreview() {
        this.state.selectedTarget = null;
    }

    openLightbox(url) {
        this.state.lightboxImage = url;
    }
    closeLightbox() {
        this.state.lightboxImage = null;
    }

    async openTargetFormFromPreview() {
        if (!this.state.selectedTarget) return;
        const id = this.state.selectedTarget.id;
        this.state.selectedTarget = null;
        await this.openTargetForm(id);
    }

    stateLabel(state) {
        return ({
            available: "Available",
            reserved: "Reserved",
            rented: "Rented",
            sold: "Sold",
            maintenance: "Maintenance",
            inactive: "Inactive",
        })[state] || state || "—";
    }

    /**
     * Collect every image-bearing source for a property (own gallery rows,
     * its floor-plan image, and — if maquette is set up — the property's
     * `images` field), then return a {id, src} list ready for CarouselDialog.
     */
    async _collectImages(propertyId) {
        const images = [];
        // property.image rows
        try {
            const imgs = await this.orm.searchRead(
                "property.image",
                [["property_id", "=", propertyId]],
                ["id"],
                { order: "sequence, id" },
            );
            for (const i of imgs) {
                images.push({
                    id: i.id,
                    src: `/web/image/property.image/${i.id}/image_1920`,
                });
            }
        } catch (_e) { /* model missing */ }
        // floor_plan_image on the property itself (maquette field)
        try {
            const ifp = await this.orm.searchRead(
                "realestate.property",
                [["id", "=", propertyId], ["floor_plan_image", "!=", false]],
                ["id"],
            );
            if (ifp.length) {
                images.unshift({
                    id: `fp-${propertyId}`,
                    src: `/web/image/realestate.property/${propertyId}/floor_plan_image`,
                });
            }
        } catch (_e) { /* field missing */ }
        return images;
    }

    async openImagesCarousel(propertyId, title) {
        const images = await this._collectImages(propertyId);
        this.dialog.add(CarouselDialog, {
            images,
            title: title || "Images",
            propertyId,
            canUpload: true,
        });
    }

    async viewCurrentImages() {
        if (!this.current || this.current.is_project) return;
        await this.openImagesCarousel(
            this.current.id,
            (this.current.property_code || this.current.name) + " — Images",
        );
    }

    async viewSelectedTargetImages() {
        if (!this.state.selectedTarget) return;
        const t = this.state.selectedTarget;
        await this.openImagesCarousel(
            t.id,
            (t.property_code || t.display_name) + " — Images",
        );
    }

    async openTargetForm(propertyId) {
        if (!this.action) return;
        await this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "realestate.property",
            res_id: propertyId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    async jumpTo(index) {
        if (index < 0 || index >= this.state.stack.length) return;
        this.state.stack = this.state.stack.slice(0, index + 1);
        this.state.selectedTarget = null;
        // Re-populate the side panel for whatever we landed on (skip if
        // we landed on the project root — projects aren't property records).
        const top = this.current;
        if (top && !top.is_project) {
            await this._openTargetPreview(top.id);
        }
    }

    async back() {
        if (this.state.stack.length <= 1) return;
        this.state.stack.pop();
        this.state.selectedTarget = null;
        const top = this.current;
        if (top && !top.is_project) {
            await this._openTargetPreview(top.id);
        }
    }

    onRegionEnter(region) {
        this.state.hoveredRegionId = region.id;
    }
    onRegionLeave() {
        this.state.hoveredRegionId = null;
    }

    async openCurrentForm() {
        if (!this.current || !this.action) return;
        const model = this.current.is_project
            ? "realestate.project"
            : "realestate.property";
        await this.action.doAction({
            type: "ir.actions.act_window",
            res_model: model,
            res_id: this.current.id,
            views: [[false, "form"]],
            target: "current",
        });
    }
}

registry.category("actions").add(
    "real_estate_plan.property_plan_viewer", PropertyPlanViewer);
