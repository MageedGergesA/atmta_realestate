/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, onMounted, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";
import { CarouselDialog } from "./image_carousel_dialog";
import { BuildingElevation } from "./building_elevation";

/**
 * 2D master plan viewer + editor.
 *
 * View mode: hover regions for labels, click for the building preview dialog.
 *
 * Edit mode operations:
 *  - "+ Add region": drafting; each overlay click drops a vertex. Toolbar
 *    Undo/Cancel/Finish. Finish opens the building picker dialog with the
 *    drawn polygon as the default.
 *  - Click an existing region: that region becomes "selected" and its
 *    vertices appear as draggable circles. Drag a vertex → polygon updates
 *    live; on mouseup we POST the new polygon. Toolbar Edit info / Delete /
 *    Done.
 */
export class MasterPlan2D extends Component {
    static template = "real_estate_maquette.MasterPlan2D";
    static props = ["*"];

    get embedded() {
        // When used as a field widget on the project form we pin to one
        // project (no picker, no extra chrome).
        return !!this.props.projectId;
    }
    get portalMode() {
        return this.props.mode === "portal";
    }
    get canEditRegions() {
        return !this.portalMode;
    }

    setup() {
        this.orm = useService("orm");
        // action service is backend-only; on portal pages we don't use it.
        try {
            this.action = useService("action");
        } catch (_e) {
            this.action = null;
        }
        this.notification = useService("notification");
        this.dialog = useService("dialog");

        this.state = useState({
            projects: [],
            projectId: 0,
            regions: [],
            editMode: false,
            hoveredRegionId: null,
            // Drafting state
            drafting: false,
            draftPoints: [],
            // Existing-region editing state
            selectedRegionId: null,
            draggingVertex: null, // { regionId, idx }
            // View-mode side panel (mirrors the 3D viewer's selectedUnit)
            selectedProperty: null,
            // Pan/zoom on the 2D plan
            zoom: 1,
            panX: 0,
            panY: 0,
            isPanning: false,
        });
        this.overlayRef = useRef("overlay");
        this.canvasRef = useRef("planCanvas");
        // Non-reactive scratchpad for drag deltas
        this._panOrigin = null;

        onWillStart(async () => {
            if (this.embedded) {
                // Embedded mode: skip the project list, lock to the given one.
                const [proj] = await this.orm.read(
                    "realestate.project",
                    [this.props.projectId],
                    ["id", "name", "code", "has_master_plan_2d"],
                );
                this.state.projects = proj ? [proj] : [];
                this.state.projectId = proj ? proj.id : 0;
                await this._loadRegions();
                return;
            }
            this.state.projects = await this.orm.searchRead(
                "realestate.project",
                [["has_master_plan_2d", "=", true]],
                ["id", "name", "code"],
                { order: "name" },
            );
            if (this.state.projects.length) {
                const requested = this.props.action?.params?.project_id;
                const match = requested && this.state.projects.find((p) => p.id === requested);
                this.state.projectId = match ? match.id : this.state.projects[0].id;
                await this._loadRegions();
            }
        });

        this._onMouseMove = this._onMouseMove.bind(this);
        this._onMouseUp = this._onMouseUp.bind(this);
        this._onKeyDown = this._onKeyDown.bind(this);

        onMounted(() => {
            window.addEventListener("mousemove", this._onMouseMove);
            window.addEventListener("mouseup", this._onMouseUp);
            window.addEventListener("keydown", this._onKeyDown);
        });
        onWillUnmount(() => {
            window.removeEventListener("mousemove", this._onMouseMove);
            window.removeEventListener("mouseup", this._onMouseUp);
            window.removeEventListener("keydown", this._onKeyDown);
        });
    }

    // ------------------------------------------------------------------
    // Data loading
    // ------------------------------------------------------------------
    async _loadRegions() {
        if (!this.state.projectId) {
            this.state.regions = [];
            return;
        }
        if (this.portalMode) {
            // Public JSON endpoint (no auth)
            const res = await fetch(`/projects/${this.state.projectId}/regions.json`);
            if (!res.ok) {
                this.state.regions = [];
                return;
            }
            const data = await res.json();
            this.state.regions = data.regions || [];
            return;
        }
        const data = await rpc(`/maquette/regions/${this.state.projectId}`, {});
        this.state.regions = data.regions || [];
    }

    get plan2dUrl() {
        return `/web/image/realestate.project/${this.state.projectId}/master_plan_2d`;
    }
    get selectedRegion() {
        return this.state.regions.find((r) => r.id === this.state.selectedRegionId) || null;
    }
    polygonPoints(region) {
        return (region.polygon || []).map((p) => `${p[0]},${p[1]}`).join(" ");
    }
    draftPathPoints() {
        return this.state.draftPoints.map((p) => `${p[0]},${p[1]}`).join(" ");
    }

    // ------------------------------------------------------------------
    // Top bar
    // ------------------------------------------------------------------
    async onSelectProject(ev) {
        this.state.projectId = parseInt(ev.target.value, 10) || 0;
        this._resetEditing();
        await this._loadRegions();
    }
    toggleEditMode() {
        this.state.editMode = !this.state.editMode;
        this._resetEditing();
    }
    _resetEditing() {
        this.state.drafting = false;
        this.state.draftPoints = [];
        this.state.selectedRegionId = null;
        this.state.draggingVertex = null;
        this.state.hoveredRegionId = null;
    }

    // ------------------------------------------------------------------
    // Drafting (Add region)
    // ------------------------------------------------------------------
    startAddRegion() {
        this.state.selectedRegionId = null;
        this.state.drafting = true;
        this.state.draftPoints = [];
    }
    cancelDraft() {
        this.state.drafting = false;
        this.state.draftPoints = [];
    }
    undoLastPoint() {
        if (this.state.draftPoints.length) {
            this.state.draftPoints = this.state.draftPoints.slice(0, -1);
        }
    }
    async finishDraft() {
        if (this.state.draftPoints.length < 3) {
            this.notification.add("A region needs at least 3 points.", { type: "warning" });
            return;
        }
        const polygon = this.state.draftPoints;
        this.state.drafting = false;
        this.state.draftPoints = [];
        await this._openRegionForm({ polygon });
    }

    // ------------------------------------------------------------------
    // Overlay click — context-dependent
    // ------------------------------------------------------------------
    _eventToPercent(ev) {
        const rect = this.overlayRef.el.getBoundingClientRect();
        const x = Math.max(0, Math.min(100, ((ev.clientX - rect.left) / rect.width) * 100));
        const y = Math.max(0, Math.min(100, ((ev.clientY - rect.top) / rect.height) * 100));
        return [Math.round(x * 100) / 100, Math.round(y * 100) / 100];
    }

    onOverlayClick(ev) {
        if (!this.state.editMode) return;
        if (this.state.drafting) {
            // Add a vertex.
            const p = this._eventToPercent(ev);
            this.state.draftPoints = [...this.state.draftPoints, p];
            return;
        }
        if (this.state.selectedRegionId) {
            // Click outside the polygon body → deselect.
            this.state.selectedRegionId = null;
        }
    }

    // ------------------------------------------------------------------
    // Hover + click on existing regions
    // ------------------------------------------------------------------
    onRegionEnter(region) {
        if (this.state.draggingVertex) return;
        this.state.hoveredRegionId = region.id;
    }
    onRegionLeave() {
        this.state.hoveredRegionId = null;
    }
    async onRegionClick(region, ev) {
        if (ev) {
            ev.stopPropagation();
            ev.preventDefault();
        }
        if (this.state.editMode) {
            if (this.state.drafting) return; // ignore region clicks while drafting
            this.state.selectedRegionId = region.id;
            return;
        }
        // View mode: fetch the property + open a side panel mirroring 3D's.
        await this._selectProperty(region);
    }

    async _fetchHierarchyLevel(propertyId) {
        if (this.portalMode) {
            const res = await fetch(
                `/projects/${this.state.projectId}/property/${propertyId}.json`);
            if (!res.ok) return null;
            const data = await res.json();
            return data.hierarchy_level || null;
        }
        const recs = await this.orm.searchRead(
            "realestate.property",
            [["id", "=", propertyId]],
            ["hierarchy_level"],
        );
        return recs.length ? recs[0].hierarchy_level : null;
    }

    async _selectProperty(region) {
        // Buildings / blocks → open the full elevation drill-down dialog
        // instead of the simple side panel.
        const hl = await this._fetchHierarchyLevel(region.building_id);
        if (hl === "building" || hl === "block") {
            this.dialog.add(BuildingElevation, {
                buildingId: region.building_id,
                mode: this.portalMode ? "portal" : "backend",
            });
            return;
        }
        // Units / villas → side panel (existing behavior)
        if (this.portalMode) {
            const res = await fetch(
                `/projects/${this.state.projectId}/property/${region.building_id}.json`);
            if (!res.ok) {
                this.notification.add("Property not found.", { type: "warning" });
                return;
            }
            const data = await res.json();
            this.state.selectedProperty = {
                id: data.id,
                name: data.name,
                property_code: data.property_code,
                property_type: data.property_type,
                state: data.state,
                area_sqm: data.area_sqm,
                base_price: data.base_price,
                currency: data.currency,
                has_floor_plan: data.images?.some((i) => i.id === "fp"),
                hierarchy_level: "",
                region_label: region.label || "",
                _portalImages: data.images || [],
            };
            return;
        }
        const recs = await this.orm.searchRead(
            "realestate.property",
            [["id", "=", region.building_id]],
            [
                "id", "name", "property_code", "property_type_id",
                "state", "area_sqm", "base_price", "currency_id",
                "floor_plan_image", "hierarchy_level",
            ],
        );
        if (!recs.length) {
            this.notification.add("Property not found.", { type: "warning" });
            return;
        }
        const r = recs[0];
        this.state.selectedProperty = {
            id: r.id,
            name: r.name || "",
            property_code: r.property_code || "",
            property_type: (r.property_type_id && r.property_type_id[1]) || "",
            state: r.state || "",
            area_sqm: r.area_sqm || 0,
            base_price: r.base_price || 0,
            currency: (r.currency_id && r.currency_id[1]) || "",
            has_floor_plan: !!r.floor_plan_image,
            hierarchy_level: r.hierarchy_level || "",
            region_label: region.label || "",
        };
    }

    closeSidePanel() {
        this.state.selectedProperty = null;
    }

    floorPlanUrl(propertyId) {
        return `/maquette/floor_plan/${propertyId}`;
    }

    stateLabelFor(state) {
        return {
            available: "Available",
            reserved: "Reserved",
            rented: "Rented",
            sold: "Sold",
            maintenance: "Maintenance",
            inactive: "Inactive",
        }[state] || state || "—";
    }
    stateColorFor(state) {
        return {
            available: "#22c55e",
            reserved: "#eab308",
            rented: "#3b82f6",
            sold: "#6b7280",
            maintenance: "#a855f7",
            inactive: "#94a3b8",
        }[state] || "#94a3b8";
    }

    async openImages() {
        const p = this.state.selectedProperty;
        if (!p) return;
        let images = [];
        if (this.portalMode) {
            // Pre-loaded from the public JSON endpoint
            images = (p._portalImages || []).map((i) => ({ id: i.id, src: i.src }));
        } else {
            if (p.has_floor_plan) {
                images.push({
                    id: `fp-${p.id}`,
                    src: `/web/image/realestate.property/${p.id}/floor_plan_image`,
                });
            }
            const imgs = await this.orm.searchRead(
                "property.image",
                [["property_id", "=", p.id]],
                ["id"],
                { order: "sequence, id" },
            );
            for (const r of imgs) {
                images.push({ id: r.id, src: `/web/image/property.image/${r.id}/image_1920` });
            }
        }
        if (!images.length && this.portalMode) {
            this.notification.add("No images for this property yet.", { type: "info" });
            return;
        }
        this.dialog.add(CarouselDialog, {
            images,
            title: p.name || p.property_code || "Property Images",
            propertyId: p.id,
            canUpload: !this.portalMode,
        });
    }

    async openUnitForm() {
        const p = this.state.selectedProperty;
        if (!p) return;
        if (this.portalMode) {
            // No backend form for public visitors — just keep them on the
            // project page; the EOI form on the side is the main CTA.
            return;
        }
        await this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "realestate.property",
            res_id: p.id,
            view_mode: "form",
            views: [[false, "form"]],
            target: "current",
        });
    }

    async reserveUnit() {
        const p = this.state.selectedProperty;
        if (!p) return;
        if (this.portalMode) {
            // In portal mode "Reserve" is replaced by Express Interest.
            this._openPortalForm("eoi", p.id);
            return;
        }
        if (p.state !== "available") {
            this.notification.add("Property is not available.", { type: "warning" });
            return;
        }
        await this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "realestate.unit.reservation",
            view_mode: "form",
            views: [[false, "form"]],
            target: "current",
            context: { default_property_id: p.id },
        });
    }

    async createListing() {
        const p = this.state.selectedProperty;
        if (!p) return;
        if (this.portalMode) {
            this._openPortalForm("visit", p.id);
            return;
        }
        await this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "realestate.listing",
            view_mode: "form",
            views: [[false, "form"]],
            target: "current",
            context: { default_property_id: p.id },
        });
    }

    // ------------------------------------------------------------------
    // Zoom + pan
    // ------------------------------------------------------------------
    get canvasTransform() {
        return `translate(${this.state.panX}px, ${this.state.panY}px) scale(${this.state.zoom})`;
    }
    get zoomLevelDisplay() {
        return `${Math.round(this.state.zoom * 100)}%`;
    }

    onWheelZoom(ev) {
        if (this.state.editMode) return;
        ev.preventDefault();
        const rect = this.canvasRef.el?.getBoundingClientRect();
        if (!rect) return;
        const oldZoom = this.state.zoom;
        const step = ev.deltaY > 0 ? -0.15 : 0.15;
        const newZoom = Math.max(1, Math.min(6, oldZoom * (1 + step)));
        if (newZoom === oldZoom) return;
        const cx = ev.clientX - rect.left;
        const cy = ev.clientY - rect.top;
        const ratio = newZoom / oldZoom;
        this.state.panX = cx - ratio * (cx - this.state.panX);
        this.state.panY = cy - ratio * (cy - this.state.panY);
        this.state.zoom = newZoom;
        this._clampPan();
    }

    onCanvasMouseDown(ev) {
        if (this.state.editMode) return;
        if (this.state.zoom <= 1) return;
        if (ev.target && ev.target.tagName === "polygon") return;
        ev.preventDefault();
        this.state.isPanning = true;
        this._panOrigin = {
            x: ev.clientX - this.state.panX,
            y: ev.clientY - this.state.panY,
        };
    }

    _clampPan() {
        const rect = this.canvasRef.el?.getBoundingClientRect();
        if (!rect) return;
        const minX = rect.width - rect.width * this.state.zoom;
        const minY = rect.height - rect.height * this.state.zoom;
        this.state.panX = Math.max(minX, Math.min(0, this.state.panX));
        this.state.panY = Math.max(minY, Math.min(0, this.state.panY));
    }

    _zoomToCenter(newZoom) {
        const oldZoom = this.state.zoom;
        if (newZoom === oldZoom) return;
        const rect = this.canvasRef.el?.getBoundingClientRect();
        if (!rect) return;
        const cx = rect.width / 2;
        const cy = rect.height / 2;
        const ratio = newZoom / oldZoom;
        this.state.panX = cx - ratio * (cx - this.state.panX);
        this.state.panY = cy - ratio * (cy - this.state.panY);
        this.state.zoom = newZoom;
        this._clampPan();
    }
    zoomIn() { this._zoomToCenter(Math.min(6, this.state.zoom + 0.5)); }
    zoomOut() { this._zoomToCenter(Math.max(1, this.state.zoom - 0.5)); }
    resetZoom() {
        this.state.zoom = 1;
        this.state.panX = 0;
        this.state.panY = 0;
    }

    _openPortalForm(kind, propertyId) {
        // Dispatch a window-level event the portal page listens to so it
        // can open the EOI / visit modal pre-filled with this property.
        window.dispatchEvent(new CustomEvent("re-portal-form", {
            detail: { kind, propertyId, projectId: this.state.projectId },
        }));
    }

    // ------------------------------------------------------------------
    // Vertex drag (reshape a selected region)
    // ------------------------------------------------------------------
    onVertexMouseDown(region, idx, ev) {
        ev.stopPropagation();
        ev.preventDefault();
        this.state.draggingVertex = { regionId: region.id, idx };
    }

    _onMouseMove(ev) {
        // Pan takes priority when active.
        if (this.state.isPanning && this._panOrigin) {
            this.state.panX = ev.clientX - this._panOrigin.x;
            this.state.panY = ev.clientY - this._panOrigin.y;
            this._clampPan();
            return;
        }
        if (!this.state.draggingVertex) return;
        if (!this.overlayRef.el) return;
        const { regionId, idx } = this.state.draggingVertex;
        const region = this.state.regions.find((r) => r.id === regionId);
        if (!region) return;
        const [x, y] = this._eventToPercent(ev);
        // Mutate the polygon in-place via reactive replace.
        region.polygon = region.polygon.map((p, i) => (i === idx ? [x, y] : p));
    }

    async _onMouseUp() {
        if (this.state.isPanning) {
            this.state.isPanning = false;
            this._panOrigin = null;
            return;
        }
        if (!this.state.draggingVertex) return;
        const { regionId } = this.state.draggingVertex;
        this.state.draggingVertex = null;
        const region = this.state.regions.find((r) => r.id === regionId);
        if (!region) return;
        const result = await rpc("/maquette/regions/save", {
            project_id: this.state.projectId,
            region_id: regionId,
            building_id: region.building_id,
            polygon: region.polygon,
            color: region.color,
            label: region.label,
        });
        if (result.error) {
            this.notification.add("Save failed: " + result.error, { type: "danger" });
            await this._loadRegions(); // revert local state from server
        }
    }

    // Right-click on a vertex → remove it (must keep ≥3 vertices).
    async onVertexContextMenu(region, idx, ev) {
        ev.preventDefault();
        ev.stopPropagation();
        if ((region.polygon || []).length <= 3) {
            this.notification.add("A region needs at least 3 vertices.", { type: "warning" });
            return;
        }
        const polygon = region.polygon.filter((_, i) => i !== idx);
        region.polygon = polygon;
        await rpc("/maquette/regions/save", {
            project_id: this.state.projectId,
            region_id: region.id,
            building_id: region.building_id,
            polygon,
            color: region.color,
            label: region.label,
        });
    }

    // Click an edge midpoint → insert a new vertex there.
    async onEdgeMidpointClick(region, edgeIdx, ev) {
        ev.stopPropagation();
        ev.preventDefault();
        const pts = region.polygon || [];
        if (edgeIdx < 0 || edgeIdx >= pts.length) return;
        const a = pts[edgeIdx];
        const b = pts[(edgeIdx + 1) % pts.length];
        const mid = [
            Math.round(((a[0] + b[0]) / 2) * 100) / 100,
            Math.round(((a[1] + b[1]) / 2) * 100) / 100,
        ];
        const polygon = [...pts.slice(0, edgeIdx + 1), mid, ...pts.slice(edgeIdx + 1)];
        region.polygon = polygon;
        await rpc("/maquette/regions/save", {
            project_id: this.state.projectId,
            region_id: region.id,
            building_id: region.building_id,
            polygon,
            color: region.color,
            label: region.label,
        });
    }

    edgeMidpoints(region) {
        // Return [{x, y, edgeIdx}, ...] for click-to-insert handles.
        const pts = region.polygon || [];
        const out = [];
        for (let i = 0; i < pts.length; i++) {
            const a = pts[i];
            const b = pts[(i + 1) % pts.length];
            out.push({
                x: (a[0] + b[0]) / 2,
                y: (a[1] + b[1]) / 2,
                edgeIdx: i,
            });
        }
        return out;
    }

    // ------------------------------------------------------------------
    // Open the building picker dialog (new or existing)
    // ------------------------------------------------------------------
    async _openRegionForm({ regionId = null, polygon = null }) {
        const context = {
            default_project_id: this.state.projectId,
        };
        if (polygon) {
            context.default_polygon = JSON.stringify(polygon);
            context.default_color = "#3b82f6";
        }
        await this.action.doAction(
            {
                type: "ir.actions.act_window",
                res_model: "realestate.building.region",
                views: [[false, "form"]],
                res_id: regionId || undefined,
                target: "new",
                context,
            },
            { onClose: async () => this._loadRegions() },
        );
    }

    async editSelectedRegionInfo() {
        if (!this.state.selectedRegionId) return;
        await this._openRegionForm({ regionId: this.state.selectedRegionId });
    }
    async deleteSelectedRegion() {
        if (!this.state.selectedRegionId) return;
        if (!window.confirm("Delete this region?")) return;
        await rpc("/maquette/regions/delete", { region_id: this.state.selectedRegionId });
        this.state.selectedRegionId = null;
        await this._loadRegions();
    }
    doneEditingSelected() {
        this.state.selectedRegionId = null;
    }

    // Escape → cancel draft / deselect
    _onKeyDown(ev) {
        if (ev.key !== "Escape") return;
        if (this.state.draftPoints.length) {
            this.cancelDraft();
        } else if (this.state.selectedRegionId) {
            this.state.selectedRegionId = null;
        } else if (this.state.editMode) {
            this.state.editMode = false;
        }
    }
}

registry.category("actions").add("real_estate_maquette.master_plan_2d", MasterPlan2D);
