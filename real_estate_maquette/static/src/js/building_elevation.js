/** @odoo-module **/

import { Component, onMounted, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { Dialog } from "@web/core/dialog/dialog";
import { CarouselDialog } from "./image_carousel_dialog";

/**
 * Building elevation viewer.
 *
 *  - Zoomable elevation sheet (mouse wheel + drag-pan, same pattern as the
 *    2D master plan).
 *  - Floor schedule sidebar: each row clickable; shows units + state stats.
 *  - When a floor is selected, the bottom strip lists its units; clicking a
 *    unit triggers an action (open form in backend, EOI on portal).
 *
 *  Props:
 *    buildingId  Number          required
 *    mode        "backend"|"portal"   default "backend"
 *    close       Function?       (passed by Dialog when used as one)
 */
export class BuildingElevation extends Component {
    static template = "real_estate_maquette.BuildingElevation";
    static components = { Dialog };
    static props = {
        buildingId: { type: Number },
        mode: { type: String, optional: true },
        close: { type: Function, optional: true },
    };

    get portalMode() {
        return this.props.mode === "portal";
    }

    setup() {
        this.orm = useService("orm");
        try {
            this.action = useService("action");
        } catch (_e) {
            this.action = null;
        }
        this.dialog = useService("dialog");
        this.notification = useService("notification");

        this.state = useState({
            loading: true,
            error: "",
            building: null,
            floors: [],
            specs: [],
            selectedFloorId: null,
            selectedUnit: null,
            // 'elevation' = building's elevation sheet, 'floor' = selected floor's plan
            displayMode: "elevation",
            // Zoom / pan state on the elevation sheet
            zoom: 1,
            panX: 0,
            panY: 0,
            isPanning: false,
        });
        this.sheetWrapperRef = useRef("sheetWrapper");
        this.sheetCanvasRef = useRef("sheetCanvas");
        this._panOrigin = null;

        this._onMouseMove = this._onMouseMove.bind(this);
        this._onMouseUp = this._onMouseUp.bind(this);

        onWillStart(async () => {
            await this._load();
        });
        onMounted(() => {
            window.addEventListener("mousemove", this._onMouseMove);
            window.addEventListener("mouseup", this._onMouseUp);
            // Eagerly fetch the first floor's units so the bottom strip is
            // populated without an extra click.
            if (this.state.selectedFloorId) {
                this.selectFloor(this.state.selectedFloorId);
            }
        });
        onWillUnmount(() => {
            window.removeEventListener("mousemove", this._onMouseMove);
            window.removeEventListener("mouseup", this._onMouseUp);
        });
    }

    // ------------------------------------------------------------------
    // Data
    // ------------------------------------------------------------------
    async _load() {
        try {
            if (this.portalMode) {
                const res = await fetch(`/projects/portal/building/${this.props.buildingId}.json`);
                if (!res.ok) throw new Error("Could not fetch building.");
                const data = await res.json();
                this.state.building = data.building;
                this.state.floors = data.floors || [];
                this.state.specs = data.specs || [];
            } else {
                const recs = await this.orm.searchRead(
                    "realestate.property",
                    [["id", "=", this.props.buildingId]],
                    [
                        "id", "name", "property_code", "number_of_floors", "number_of_units",
                        "has_elevation", "spec_tag_ids", "city", "district",
                        "area_sqm",
                    ],
                );
                if (!recs.length) throw new Error("Building not found.");
                this.state.building = {
                    ...recs[0],
                    sheet_url: recs[0].has_elevation
                        ? `/web/image/realestate.property/${recs[0].id}/elevation_sheet`
                        : null,
                };
                // Use read_group-style metadata; check plan presence via the
                // computed display_name to avoid pulling base64 for every row.
                const floors = await this.orm.searchRead(
                    "realestate.building.floor",
                    [["building_id", "=", this.props.buildingId]],
                    [
                        "id", "floor_number", "property_usage_id",
                        "units_total", "units_available",
                        "units_reserved", "units_sold",
                    ],
                    { order: "floor_number" },
                );
                this.state.floors = floors;
                if (this.state.building.spec_tag_ids?.length) {
                    this.state.specs = await this.orm.searchRead(
                        "realestate.spec.tag",
                        [["id", "in", this.state.building.spec_tag_ids]],
                        ["id", "name", "icon", "color"],
                    );
                }
            }
            if (this.state.floors.length) {
                this.state.selectedFloorId = this.state.floors[0].id;
            }
        } catch (err) {
            this.state.error = err.message || String(err);
        } finally {
            this.state.loading = false;
        }
    }

    get selectedFloor() {
        return this.state.floors.find((f) => f.id === this.state.selectedFloorId) || null;
    }
    get hasElevation() {
        return !!this.state.building?.sheet_url;
    }
    get hasFloorPlan() {
        // Enabled if the selected unit has a plan, OR (no unit selected yet)
        // if any unit on the current floor has one.
        if (this.state.selectedUnit?.has_floor_plan) return true;
        const f = this.selectedFloor;
        return !!(f && f.units && f.units.some((u) => u.has_floor_plan));
    }
    get floorPlanUnit() {
        if (this.state.selectedUnit?.has_floor_plan) return this.state.selectedUnit;
        const f = this.selectedFloor;
        return (f && f.units && f.units.find((u) => u.has_floor_plan)) || null;
    }
    get unitFloorPlanUrl() {
        const u = this.floorPlanUnit;
        if (!u) return null;
        return this.portalMode
            ? `/projects/portal/property/${u.id}/floor_plan`
            : `/maquette/floor_plan/${u.id}`;
    }
    get currentImageUrl() {
        if (this.state.displayMode === "floor" && this.hasFloorPlan) {
            return this.unitFloorPlanUrl;
        }
        return this.state.building?.sheet_url || null;
    }
    showElevation() {
        this.state.displayMode = "elevation";
        this.resetZoom();
    }
    showFloorPlan() {
        if (!this.hasFloorPlan) return;
        // If user hit the toggle before clicking a unit, auto-pick the first
        // unit on this floor that has a plan image so the canvas has
        // something to render.
        if (!this.state.selectedUnit?.has_floor_plan) {
            const fallback = this.floorPlanUnit;
            if (fallback) this.state.selectedUnit = fallback;
        }
        this.state.displayMode = "floor";
        this.resetZoom();
    }
    async loadSelectedFloorUnits() {
        const fid = this.state.selectedFloorId;
        if (!fid) return [];
        if (this.portalMode) {
            const res = await fetch(`/projects/portal/floor/${fid}/units.json`);
            if (!res.ok) return [];
            return await res.json();
        }
        // Look up the floor's building_id + floor_number then search units
        // by match. (We avoid floor_id since the model link is now via the
        // existing unit.floor_number integer.)
        const floor = this.state.floors.find((f) => f.id === fid);
        if (!floor) return [];
        const buildingId = this.props.buildingId;
        const rows = await this.orm.searchRead(
            "realestate.property",
            [
                ["parent_id", "=", buildingId],
                ["hierarchy_level", "=", "unit"],
                ["floor_number", "=", floor.floor_number],
            ],
            [
                "id", "property_code", "name", "state",
                "area_sqm", "base_price", "currency_id",
                "has_floor_plan_effective", "image_count",
            ],
            { order: "property_code" },
        );
        return rows.map((r) => ({ ...r, has_floor_plan: !!r.has_floor_plan_effective }));
    }

    // Re-fetch units when the selected floor changes.
    async selectFloor(floorId) {
        this.state.selectedFloorId = floorId;
        const floor = this.state.floors.find((f) => f.id === floorId);
        if (!floor) {
            this.state.selectedUnit = null;
            return;
        }
        floor.units = await this.loadSelectedFloorUnits();
        // Keep a unit selected so the action buttons (Images, Open form,
        // Reserve) stay visible. Prefer a unit with a floor plan so the
        // Floor-plan toggle is also live without an extra click.
        const units = floor.units || [];
        this.state.selectedUnit =
            units.find((u) => u.has_floor_plan) || units[0] || null;
    }

    onMounted_loadFirstFloorUnits() {
        // Hook called once after first render so the initial floor list shows.
        if (this.state.selectedFloorId) {
            this.selectFloor(this.state.selectedFloorId);
        }
    }

    // ------------------------------------------------------------------
    // Zoom / pan on the elevation sheet
    // ------------------------------------------------------------------
    get sheetTransform() {
        return `translate(${this.state.panX}px, ${this.state.panY}px) scale(${this.state.zoom})`;
    }
    get zoomLabel() {
        return `${Math.round(this.state.zoom * 100)}%`;
    }

    onWheelZoom(ev) {
        ev.preventDefault();
        const rect = this.sheetWrapperRef.el?.getBoundingClientRect();
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
    onSheetMouseDown(ev) {
        if (this.state.zoom <= 1) return;
        ev.preventDefault();
        this.state.isPanning = true;
        this._panOrigin = {
            x: ev.clientX - this.state.panX,
            y: ev.clientY - this.state.panY,
        };
    }
    _onMouseMove(ev) {
        if (!this.state.isPanning || !this._panOrigin) return;
        this.state.panX = ev.clientX - this._panOrigin.x;
        this.state.panY = ev.clientY - this._panOrigin.y;
        this._clampPan();
    }
    _onMouseUp() {
        if (this.state.isPanning) {
            this.state.isPanning = false;
            this._panOrigin = null;
        }
    }
    _clampPan() {
        const rect = this.sheetWrapperRef.el?.getBoundingClientRect();
        if (!rect) return;
        const minX = rect.width - rect.width * this.state.zoom;
        const minY = rect.height - rect.height * this.state.zoom;
        this.state.panX = Math.max(minX, Math.min(0, this.state.panX));
        this.state.panY = Math.max(minY, Math.min(0, this.state.panY));
    }
    _zoomToCenter(newZoom) {
        const oldZoom = this.state.zoom;
        if (newZoom === oldZoom) return;
        const rect = this.sheetWrapperRef.el?.getBoundingClientRect();
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
    // Unit interactions (same shape as 2D plan side panel)
    // ------------------------------------------------------------------
    stateColorFor(state) {
        return {
            available: "#22c55e", reserved: "#eab308", rented: "#3b82f6",
            sold: "#6b7280", maintenance: "#a855f7", inactive: "#94a3b8",
        }[state] || "#94a3b8";
    }
    stateLabelFor(state) {
        return {
            available: "Available", reserved: "Reserved", rented: "Rented",
            sold: "Sold", maintenance: "Maintenance", inactive: "Inactive",
        }[state] || state || "—";
    }
    selectUnit(unit) {
        this.state.selectedUnit = unit;
    }
    async openUnitImages() {
        const u = this.state.selectedUnit;
        if (!u) return;
        let imgs;
        if (this.portalMode) {
            const res = await fetch(`/projects/portal/property/${u.id}/images.json`);
            imgs = res.ok ? await res.json() : [];
        } else {
            const recs = await this.orm.searchRead(
                "property.image",
                [["property_id", "=", u.id]],
                ["id"],
                { order: "sequence, id" },
            );
            imgs = recs.map((r) => ({ id: r.id, src: `/web/image/property.image/${r.id}/image_1920` }));
        }
        if (!imgs.length) {
            this.notification.add("No images for this unit yet.", { type: "info" });
            return;
        }
        this.dialog.add(CarouselDialog, {
            images: imgs,
            title: u.name || u.property_code || "Unit",
            propertyId: u.id,
            canUpload: !this.portalMode,
        });
    }
    async openUnitForm() {
        const u = this.state.selectedUnit;
        if (!u || !this.action) return;
        await this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "realestate.property",
            res_id: u.id,
            view_mode: "form",
            views: [[false, "form"]],
            target: "current",
        });
        this.props.close?.();
    }
    async reserveOrEOI() {
        const u = this.state.selectedUnit;
        if (!u) return;
        if (this.portalMode) {
            window.dispatchEvent(new CustomEvent("re-portal-form", {
                detail: { kind: "eoi", propertyId: u.id },
            }));
            this.props.close?.();
            return;
        }
        if (!this.action) return;
        if (u.state !== "available") {
            this.notification.add("Unit is not available.", { type: "warning" });
            return;
        }
        await this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "realestate.unit.reservation",
            view_mode: "form",
            views: [[false, "form"]],
            target: "current",
            context: { default_property_id: u.id },
        });
        this.props.close?.();
    }
}
