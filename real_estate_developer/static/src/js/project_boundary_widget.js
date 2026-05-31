/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, onMounted, onWillUnmount, useRef, useState, useEffect } from "@odoo/owl";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { useService } from "@web/core/utils/hooks";

let leafletPatched = false;
function patchLeaflet() {
    if (leafletPatched || typeof L === "undefined") return;
    const base = "/atmta_real_estate/static/src/lib/leaflet/images";
    delete L.Icon.Default.prototype._getIconUrl;
    L.Icon.Default.mergeOptions({
        iconRetinaUrl: `${base}/marker-icon-2x.png`,
        iconUrl: `${base}/marker-icon.png`,
        shadowUrl: `${base}/marker-shadow.png`,
    });
    leafletPatched = true;
}

function numberedIcon(num) {
    return L.divIcon({
        className: "o_boundary_marker",
        html: `<div class="o_boundary_marker_pin">${num}</div>`,
        iconSize: [28, 28],
        iconAnchor: [14, 14],
    });
}

export class BoundaryPickerField extends Component {
    static template = "real_estate_developer.BoundaryPickerField";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.mapRef = useRef("map");
        this.state = useState({ points: [], busy: false });
        this.map = null;
        this.markersLayer = null;
        this.polygonLayer = null;

        onMounted(async () => {
            await this._initMap();
            await this._loadPoints();
            await this._loadPlanFlag();
            this._render();
        });

        // Re-render whenever the points change
        useEffect(
            () => {
                if (this.map) this._render();
            },
            () => [this._pointsHash()]
        );

        onWillUnmount(() => {
            if (this.map) {
                try { this.map.remove(); } catch {}
                this.map = null;
            }
        });
    }

    get isUnsaved() {
        return !this.props.record.resId;
    }

    get projectId() {
        return this.props.record.resId;
    }

    _pointsHash() {
        return this.state.points
            .map((p) => `${p.id}:${p.latitude.toFixed(6)},${p.longitude.toFixed(6)}`)
            .join("|");
    }

    async _loadPlanFlag() {
        // Does this project have a 2D plan to overlay? (field lives in the
        // maquette module — guard in case it isn't installed.)
        this._has2dPlan = false;
        if (this.isUnsaved) return;
        try {
            const recs = await this.orm.read(
                "realestate.project", [this.projectId], ["has_master_plan_2d"]);
            this._has2dPlan = !!(recs && recs[0] && recs[0].has_master_plan_2d);
        } catch (e) {
            this._has2dPlan = false;
        }
    }

    async _loadPoints() {
        if (this.isUnsaved) {
            this.state.points = [];
            return;
        }
        try {
            const points = await this.orm.searchRead(
                "realestate.project.boundary.point",
                [["project_id", "=", this.projectId]],
                ["id", "sequence", "latitude", "longitude", "label"],
                { order: "sequence, id" }
            );
            this.state.points = points;
        } catch (e) {
            console.error("[boundary] load failed:", e);
            this.notification.add("Failed to load boundary points: " + (e.message || e), { type: "danger" });
        }
    }

    async _initMap() {
        if (typeof L === "undefined" || !this.mapRef.el) {
            console.warn("[boundary] Leaflet not available");
            return;
        }
        patchLeaflet();
        this.map = L.map(this.mapRef.el, {
            center: [24.7136, 46.6753],
            zoom: 6,
            preferCanvas: true,
        });

        const streets = L.tileLayer(
            "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
            { attribution: "&copy; CARTO", maxZoom: 19, subdomains: "abcd" }
        );
        const satellite = L.tileLayer(
            "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            { attribution: "Tiles &copy; Esri", maxZoom: 19 }
        );
        const hybridLabels = L.tileLayer(
            "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
            { attribution: "", maxZoom: 19, opacity: 0.9 }
        );
        const hybrid = L.layerGroup([satellite, hybridLabels]);

        streets.addTo(this.map);
        L.control.layers(
            { "Streets": streets, "Satellite": satellite, "Satellite + Labels": hybrid },
            null,
            { position: "topright", collapsed: false }
        ).addTo(this.map);

        // Bottom layer so the 2D-plan overlay sits under the polygon + markers.
        this.overlayLayer = L.layerGroup().addTo(this.map);
        this.markersLayer = L.layerGroup().addTo(this.map);
        this.polygonLayer = L.layerGroup().addTo(this.map);

        this.map.on("click", async (e) => {
            if (this.state.busy) return;
            if (this.isUnsaved) {
                this.notification.add("Save the project first, then click the map.", { type: "warning" });
                return;
            }
            await this._addPoint(e.latlng.lat, e.latlng.lng);
        });

        setTimeout(() => this.map && this.map.invalidateSize(), 100);
    }

    _render() {
        if (!this.map) return;
        this.markersLayer.clearLayers();
        this.polygonLayer.clearLayers();
        if (this.overlayLayer) this.overlayLayer.clearLayers();
        const pts = this.state.points;
        if (pts.length === 0) return;

        const latlngs = pts.map((p) => [p.latitude, p.longitude]);

        if (pts.length >= 3) {
            L.polygon(latlngs, {
                color: "#2e5cdc",
                weight: 2,
                fillColor: "#2e5cdc",
                fillOpacity: 0.2,
            }).addTo(this.polygonLayer);
        } else if (pts.length === 2) {
            L.polyline(latlngs, { color: "#2e5cdc", weight: 2 }).addTo(this.polygonLayer);
        }

        // 2D master-plan overlay, georeferenced to the polygon's bounding box.
        if (this._has2dPlan && this.overlayLayer) {
            const planUrl = `/web/image/realestate.project/${this.projectId}/master_plan_2d`;
            if (pts.length >= 3) {
                L.imageOverlay(planUrl, L.latLngBounds(latlngs),
                    { opacity: 0.75, interactive: false }).addTo(this.overlayLayer);
            } else {
                L.marker(latlngs[0]).bindPopup(
                    `<div><b>2D Plan</b><br/><img src="${planUrl}" ` +
                    `style="max-width:260px;max-height:260px;"/></div>`
                ).addTo(this.overlayLayer);
            }
        }

        pts.forEach((p, i) => {
            const marker = L.marker([p.latitude, p.longitude], {
                icon: numberedIcon(i + 1),
                draggable: !this.isUnsaved,
                autoPan: true,
            });
            marker.bindTooltip(
                `<b>${i + 1}${p.label ? ` · ${p.label}` : ""}</b><br/>` +
                `<small>${p.latitude.toFixed(6)}, ${p.longitude.toFixed(6)}</small><br/>` +
                `<small><i>Drag = move · Shift+click = remove</i></small>`,
                { direction: "top" }
            );
            marker.on("click", async (e) => {
                L.DomEvent.stopPropagation(e);
                if (e.originalEvent && e.originalEvent.shiftKey) {
                    await this._removePoint(p.id);
                }
            });
            marker.on("dragend", async (e) => {
                const { lat, lng } = e.target.getLatLng();
                await this._movePoint(p.id, lat, lng);
            });
            this.markersLayer.addLayer(marker);
        });

        // Auto-fit on first load when there are points
        if (!this._fittedOnce && pts.length >= 1) {
            this._fittedOnce = true;
            this.fitView();
        }
    }

    async _addPoint(lat, lng) {
        if (this.isUnsaved) return;
        this.state.busy = true;
        try {
            await this.orm.call(
                "realestate.project",
                "action_add_boundary_point",
                [this.projectId, lat, lng],
            );
            await this._loadPoints();
        } catch (e) {
            console.error("[boundary] add failed:", e);
            this.notification.add("Failed to add point: " + (e.message || e), { type: "danger" });
        } finally {
            this.state.busy = false;
        }
    }

    async _movePoint(pointId, lat, lng) {
        if (this.isUnsaved || !pointId) return;
        this.state.busy = true;
        try {
            await this.orm.call(
                "realestate.project",
                "action_move_boundary_point",
                [this.projectId, pointId, lat, lng],
            );
            await this._loadPoints();
        } catch (e) {
            console.error("[boundary] move failed:", e);
            this.notification.add("Failed to move point: " + (e.message || e), { type: "danger" });
        } finally {
            this.state.busy = false;
        }
    }

    async _removePoint(pointId) {
        if (this.isUnsaved || !pointId) return;
        this.state.busy = true;
        try {
            await this.orm.call(
                "realestate.project",
                "action_remove_boundary_point",
                [this.projectId, pointId],
            );
            await this._loadPoints();
        } catch (e) {
            console.error("[boundary] remove failed:", e);
            this.notification.add("Failed to remove point: " + (e.message || e), { type: "danger" });
        } finally {
            this.state.busy = false;
        }
    }

    async undoLast() {
        if (this.state.points.length === 0) return;
        const last = this.state.points[this.state.points.length - 1];
        await this._removePoint(last.id);
    }

    async clearAll() {
        if (this.isUnsaved || this.state.points.length === 0) return;
        this.state.busy = true;
        try {
            await this.orm.call(
                "realestate.project",
                "action_clear_boundary",
                [this.projectId],
            );
            await this._loadPoints();
        } catch (e) {
            console.error("[boundary] clear failed:", e);
            this.notification.add("Failed to clear: " + (e.message || e), { type: "danger" });
        } finally {
            this.state.busy = false;
        }
    }

    fitView() {
        if (!this.state.points.length || !this.map) return;
        const bounds = L.latLngBounds(this.state.points.map((p) => [p.latitude, p.longitude]));
        this.map.fitBounds(bounds, { padding: [30, 30], maxZoom: 18 });
    }
}

export const boundaryPickerField = {
    component: BoundaryPickerField,
    supportedTypes: ["one2many"],
    relatedFields: () => [],
};

registry.category("fields").add("boundary_picker", boundaryPickerField);
