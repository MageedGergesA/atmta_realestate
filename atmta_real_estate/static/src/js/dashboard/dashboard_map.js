/** @odoo-module **/

import { Component, onMounted, onWillUnmount, status, useEffect, useRef, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";

import { STREET_TILES, statusColor } from "../map_tiles";

const MODEL = "realestate.rental.dashboard";

// Status colours come from map_tiles.js, shared with Units -> Map.
const STATUS_ORDER = ["available", "reserved", "rented", "sold", "maintenance", "inactive"];

/**
 * Map card on the Rental Overview.
 *
 * The backend decides which units are sent (`get_map`) and what opening one
 * does (`action_open_unit`, `action_open_map`); this component only draws them.
 * It loads on its own after the tiles, so the numbers never wait for the map.
 */
export class DashboardMap extends Component {
    static template = "atmta_real_estate.DashboardMap";
    static props = {};

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.mapRef = useRef("map");
        this.map = null;

        this.state = useState({
            /** "loading" | "ready" | "error" */
            status: "loading",
            units: [],
            withoutCoordinates: 0,
            truncated: false,
            noLibrary: false,
        });

        onMounted(() => {
            this.load();
            this.watchResize();
        });
        useEffect(
            (ready, count) => {
                if (ready && count) {
                    this.renderMap();
                }
            },
            () => [this.state.status === "ready", this.state.units.length]
        );
        onWillUnmount(() => this.teardown());
    }

    async load() {
        try {
            const payload = await this.orm.call(MODEL, "get_map", []);
            if (status(this) === "destroyed") {
                return;
            }
            this.state.units = payload.units || [];
            this.state.withoutCoordinates = payload.without_coordinates || 0;
            this.state.truncated = !!payload.truncated;
            this.state.status = "ready";
        } catch (error) {
            if (status(this) === "destroyed") {
                return;
            }
            console.warn("Rental dashboard map failed to load", error);
            this.state.status = "error";
        }
    }

    // ------------------------------------------------------------------
    // Leaflet
    // ------------------------------------------------------------------
    renderMap() {
        if (!this.mapRef.el) {
            return;
        }
        if (typeof L === "undefined") {
            this.state.noLibrary = true;
            return;
        }
        this.teardown();
        this.map = L.map(this.mapRef.el, {
            scrollWheelZoom: false,
            preferCanvas: true,
            worldCopyJump: true,
        });
        L.tileLayer(STREET_TILES.url, STREET_TILES.options).addTo(this.map);

        const markers = this.state.units.map((unit) => {
            const marker = L.circleMarker([unit.lat, unit.lng], {
                radius: 7,
                color: "#ffffff",
                weight: 2,
                fillColor: statusColor(unit.status),
                fillOpacity: 0.9,
            });
            marker.bindPopup(() => this.popupElement(unit), { minWidth: 200, maxWidth: 280 });
            return marker;
        });
        if (typeof L.markerClusterGroup === "function") {
            const cluster = L.markerClusterGroup({
                maxClusterRadius: 50,
                showCoverageOnHover: false,
                spiderfyOnMaxZoom: true,
            });
            cluster.addLayers(markers);
            this.map.addLayer(cluster);
        } else {
            L.featureGroup(markers).addTo(this.map);
        }
        const bounds = L.latLngBounds(this.state.units.map((unit) => [unit.lat, unit.lng]));
        this.map.fitBounds(bounds, { padding: [30, 30], maxZoom: 16 });
        // The card may have been laid out after Leaflet measured it.
        setTimeout(() => this.map && this.map.invalidateSize({ animate: false }), 50);
    }

    /** Built from DOM nodes, never HTML strings: unit names are user input. */
    popupElement(unit) {
        const root = document.createElement("div");
        root.className = "o_re_map_popup";
        const code = document.createElement("div");
        code.className = "small text-muted";
        code.textContent = unit.code || "";
        const name = document.createElement("div");
        name.className = "fw-bold";
        name.textContent = unit.name || "";
        const badge = document.createElement("span");
        badge.className = "badge mt-1";
        badge.style.background = statusColor(unit.status);
        badge.textContent = unit.status_label || "";
        const open = document.createElement("button");
        open.type = "button";
        open.className = "btn btn-sm btn-primary w-100 mt-2 o_re_map_open_unit";
        open.textContent = _t("Open unit");
        open.addEventListener("click", () => this.openUnit(unit.id));
        root.append(code, name, badge, open);
        return root;
    }

    teardown() {
        if (this.resizeObserver) {
            this.resizeObserver.disconnect();
            this.resizeObserver = null;
        }
        if (this.map) {
            this.map.remove();
            this.map = null;
        }
    }

    // ------------------------------------------------------------------
    // Presentation
    // ------------------------------------------------------------------
    get legend() {
        const counts = new Map();
        const labels = new Map();
        for (const unit of this.state.units) {
            counts.set(unit.status, (counts.get(unit.status) || 0) + 1);
            labels.set(unit.status, unit.status_label);
        }
        const keys = [
            ...STATUS_ORDER.filter((key) => counts.has(key)),
            ...[...counts.keys()].filter((key) => !STATUS_ORDER.includes(key)),
        ];
        return keys.map((key) => ({
            key: key || "none",
            label: labels.get(key) || _t("No status"),
            count: counts.get(key),
            color: statusColor(key),
        }));
    }

    // ------------------------------------------------------------------
    // Actions: the backend returns them
    // ------------------------------------------------------------------
    /**
     * Keep Leaflet in step with its container, without looping.
     *
     * `invalidateSize()` itself changes layout, so an unguarded observer
     * re-fires on its own effect and the renderer locks up -- which is
     * exactly what it did. Two guards: the callback only acts when the box
     * has actually changed by more than a pixel, and the call is deferred to
     * the next frame so it never runs inside the observer's own delivery.
     */
    watchResize() {
        const element = this.mapRef.el;
        if (!element || typeof ResizeObserver === "undefined") {
            return;
        }
        let frame = null;
        this.resizeObserver = new ResizeObserver(() => {
            if (frame) {
                return;
            }
            frame = requestAnimationFrame(() => {
                frame = null;
                if (!this.map || !this.resizeObserver) {
                    return;
                }
                // Stop observing across the call. `invalidateSize()` relays
                // the map, which changes the card height, which changes the
                // grid row, which resizes the map again -- a genuine
                // oscillation that a "did it change by more than a pixel"
                // test cannot break, because it really does keep changing.
                // Detaching for the duration ends it in one pass.
                this.resizeObserver.unobserve(element);
                this.map.invalidateSize({ animate: false });
                requestAnimationFrame(() => {
                    if (this.resizeObserver) {
                        this.resizeObserver.observe(element);
                    }
                });
            });
        });
        this.resizeObserver.observe(element);
    }

    async openUnit(unitId) {
        await this.runBackendAction("action_open_unit", [unitId], _t("Could not open this unit."));
    }

    async openFullMap() {
        await this.runBackendAction("action_open_map", [], _t("Could not open the map."));
    }

    async runBackendAction(method, args, failureMessage) {
        try {
            const action = await this.orm.call(MODEL, method, args);
            await this.action.doAction(action);
        } catch (error) {
            console.warn("Rental dashboard map action failed", method, args, error);
            this.notification.add(failureMessage, { type: "warning" });
        }
    }
}
