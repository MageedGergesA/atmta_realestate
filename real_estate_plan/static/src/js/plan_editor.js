/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, onMounted, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

/**
 * 2D Plan editor for any realestate.property.
 *
 * Mirrors the maquette MasterPlan2D editor:
 *  - "Edit regions" toggles edit mode
 *  - "+ Add region": click overlay to drop vertices; Undo / Cancel / Finish
 *  - Finish opens the region form for the new polygon
 *  - Click an existing region in edit mode: vertices show as draggable circles,
 *    right-click vertex to remove, click edge midpoint to insert
 *  - Edit info / Delete / Done toolbar for selected region
 *  - View mode: hover labels; click → open the linked property record
 *  - Pan + zoom in view mode (Ctrl+wheel disabled — uses plain wheel)
 *  - Esc cancels draft / deselects / exits edit mode
 */
class PlanEditor extends Component {
    static template = "real_estate_plan.PlanEditor";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        try {
            this.action = useService("action");
        } catch (_e) {
            this.action = null;
        }
        this.notification = useService("notification");

        this.state = useState({
            regions: [],
            children: [],
            editMode: false,
            hoveredRegionId: null,
            // Drafting
            drafting: false,
            draftPoints: [],
            // Existing-region editing
            selectedRegionId: null,
            draggingVertex: null,
            // Pan/zoom
            zoom: 1,
            panX: 0,
            panY: 0,
            isPanning: false,
        });
        this.overlayRef = useRef("overlay");
        this.canvasRef = useRef("planCanvas");
        this._panOrigin = null;

        onWillStart(async () => {
            await this._loadRegions();
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
    get propertyId() {
        return this.props.record.resId;
    }
    get isUnsaved() {
        return !this.propertyId;
    }
    get hasImage() {
        return !!this.props.record.data.plan_image;
    }
    get planImageUrl() {
        const wd = this.props.record.data.write_date || "";
        return `/web/image/realestate.property/${this.propertyId}/plan_image?v=${wd}`;
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
    async _loadRegions() {
        if (this.isUnsaved) {
            this.state.regions = [];
            this.state.children = [];
            return;
        }
        try {
            const data = await rpc(
                `/real_estate_plan/regions/${this.propertyId}`, {});
            this.state.regions = data.regions || [];
            this.state.children = data.children || [];
        } catch (e) {
            console.error("[plan editor] load failed:", e);
            this.notification.add(
                "Failed to load regions: " + (e.message || e),
                { type: "danger" });
        }
    }

    // ------------------------------------------------------------------
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
    // Drafting
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
            this.notification.add("A region needs at least 3 points.",
                { type: "warning" });
            return;
        }
        const polygon = this.state.draftPoints;
        this.state.drafting = false;
        this.state.draftPoints = [];
        await this._openRegionForm({ polygon });
    }

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
            const p = this._eventToPercent(ev);
            this.state.draftPoints = [...this.state.draftPoints, p];
            return;
        }
        if (this.state.selectedRegionId) {
            this.state.selectedRegionId = null;
        }
    }

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
            if (this.state.drafting) return;
            this.state.selectedRegionId = region.id;
            return;
        }
        // View mode inside the property form is read-only: regions are shown
        // (with hover labels) but clicking does nothing. Navigation belongs
        // in the dedicated drill-down viewer, not the editor widget.
    }

    // ------------------------------------------------------------------
    // Vertex drag
    // ------------------------------------------------------------------
    onVertexMouseDown(region, idx, ev) {
        ev.stopPropagation();
        ev.preventDefault();
        this.state.draggingVertex = { regionId: region.id, idx };
    }
    _onMouseMove(ev) {
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
        const result = await rpc("/real_estate_plan/regions/save", {
            property_id: this.propertyId,
            region_id: regionId,
            target_id: region.target_id,
            polygon: region.polygon,
            color: region.color,
            label: region.label,
        });
        if (result.error) {
            this.notification.add("Save failed: " + result.error,
                { type: "danger" });
            await this._loadRegions();
        }
    }

    async onVertexContextMenu(region, idx, ev) {
        ev.preventDefault();
        ev.stopPropagation();
        if ((region.polygon || []).length <= 3) {
            this.notification.add("A region needs at least 3 vertices.",
                { type: "warning" });
            return;
        }
        const polygon = region.polygon.filter((_, i) => i !== idx);
        region.polygon = polygon;
        await rpc("/real_estate_plan/regions/save", {
            property_id: this.propertyId,
            region_id: region.id,
            target_id: region.target_id,
            polygon,
            color: region.color,
            label: region.label,
        });
    }

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
        await rpc("/real_estate_plan/regions/save", {
            property_id: this.propertyId,
            region_id: region.id,
            target_id: region.target_id,
            polygon,
            color: region.color,
            label: region.label,
        });
    }

    edgeMidpoints(region) {
        const pts = region.polygon || [];
        const out = [];
        for (let i = 0; i < pts.length; i++) {
            const a = pts[i];
            const b = pts[(i + 1) % pts.length];
            out.push({ x: (a[0] + b[0]) / 2, y: (a[1] + b[1]) / 2, edgeIdx: i });
        }
        return out;
    }

    // ------------------------------------------------------------------
    // Region form (new or existing)
    // ------------------------------------------------------------------
    async _openRegionForm({ regionId = null, polygon = null }) {
        if (!this.action) return;
        const context = {
            default_parent_property_id: this.propertyId,
        };
        if (polygon) {
            context.default_polygon = JSON.stringify(polygon);
            context.default_color = "#3b82f6";
        }
        await this.action.doAction(
            {
                type: "ir.actions.act_window",
                res_model: "realestate.plan.region",
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
        await rpc("/real_estate_plan/regions/delete",
            { region_id: this.state.selectedRegionId });
        this.state.selectedRegionId = null;
        await this._loadRegions();
    }
    doneEditingSelected() {
        this.state.selectedRegionId = null;
    }
    selectRegionFromList(region) {
        this.state.selectedRegionId = region.id;
    }
    async deleteRegionFromList(region) {
        const label = region.label || region.target_name || "this region";
        if (!window.confirm(`Delete the region "${label}"?`)) return;
        await rpc("/real_estate_plan/regions/delete", { region_id: region.id });
        if (this.state.selectedRegionId === region.id) {
            this.state.selectedRegionId = null;
        }
        await this._loadRegions();
    }
    async editRegionFromList(region) {
        await this._openRegionForm({ regionId: region.id });
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

    // ------------------------------------------------------------------
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

    // ------------------------------------------------------------------
    // Helpers for templates
    // ------------------------------------------------------------------
    regionFillOpacity(region) {
        if (this.state.selectedRegionId === region.id) return "0.55";
        if (this.state.editMode) return "0.4";
        if (this.state.hoveredRegionId === region.id) return "0.55";
        return "0.2";
    }
    regionStroke(region) {
        return this.state.selectedRegionId === region.id ? "#f59e0b" : "#1e293b";
    }
    regionStrokeWidth(region) {
        return this.state.selectedRegionId === region.id ? "0.6" : "0.3";
    }
}

registry.category("fields").add("real_estate_plan_editor", {
    component: PlanEditor,
    supportedTypes: ["one2many"],
});
