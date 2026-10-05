/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, onMounted, onWillUnmount, useRef, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

import { SATELLITE_TILES, STREET_TILES, statusColor } from "./map_tiles";

const LEAFLET_IMAGE_BASE = "/atmta_dashboard/static/src/lib/leaflet/images";
const WORLD_CENTER = [20, 0];
const WORLD_ZOOM = 2;

const STATE_LABELS = {
    available: _t("Available"),
    reserved: _t("Reserved"),
    rented: _t("Rented"),
    maintenance: _t("Under Maintenance"),
    inactive: _t("Inactive"),
};
// Status colours come from map_tiles.js, shared with the Overview map card.
const STATE_ORDER = ["available", "reserved", "rented", "maintenance", "inactive"];

const LEVEL_LABELS = {
    compound: _t("Compound"),
    building: _t("Building"),
    floor: _t("Floor"),
    unit: _t("Unit"),
    room: _t("Room"),
};
const LEVEL_ORDER = ["compound", "building", "floor", "unit", "room"];

let leafletDefaultsPatched = false;
function patchLeafletDefaults() {
    if (leafletDefaultsPatched || typeof L === "undefined") return;
    delete L.Icon.Default.prototype._getIconUrl;
    L.Icon.Default.mergeOptions({
        iconRetinaUrl: `${LEAFLET_IMAGE_BASE}/marker-icon-2x.png`,
        iconUrl: `${LEAFLET_IMAGE_BASE}/marker-icon.png`,
        shadowUrl: `${LEAFLET_IMAGE_BASE}/marker-shadow.png`,
    });
    leafletDefaultsPatched = true;
}

/**
 * A marker's popup, built from DOM nodes.
 *
 * It used to be an HTML string: the unit name, code and city escaped only
 * `<`, and the property type and country names were not escaped at all, so a
 * property type named with markup was interpreted as HTML in every map user's
 * browser. Every value is now set as text.
 */
export function buildPropertyPopup(p, onOpen) {
    const el = (tag, className, text) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    };
    const root = el("div", "o_pmd_popup");

    const head = el("div", "d-flex align-items-start gap-2");
    const img = el("img", "o_pmd_popup_img");
    img.src = `/web/image/realestate.property/${Number(p.id)}/image_128`;
    img.alt = "";
    img.addEventListener("error", () => { img.style.display = "none"; });
    const info = el("div", "flex-grow-1");
    const typeName = p.property_type_id ? p.property_type_id[1] : "";
    const countryName = p.country_id ? p.country_id[1] : "";
    const place = [typeName, countryName, p.city || ""].filter(Boolean).join(" · ");
    info.append(
        el("div", "small text-muted", p.property_code || ""),
        el("div", "fw-bold", p.name || ""),
        el("div", "small text-muted", place),
    );
    head.append(img, info);

    const badges = el("div", "mt-2 d-flex align-items-center gap-2");
    const state = el("span", "badge", STATE_LABELS[p.state] || p.state || "");
    state.style.background = statusColor(p.state);
    badges.append(state);
    const levelLabel = LEVEL_LABELS[p.hierarchy_level] || p.hierarchy_level || "";
    if (levelLabel) {
        badges.append(el("span", "badge bg-secondary", levelLabel));
    }

    const open = el("button", "btn btn-sm btn-primary w-100 mt-2 o_pmd_open_btn", _t("Open"));
    open.type = "button";
    open.addEventListener("click", () => onOpen(p.id));

    root.append(head, badges, open);
    return root;
}

export class PropertiesMapDashboard extends Component {
    static template = "atmta_real_estate.PropertiesMapDashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.mapRef = useRef("map");
        this.map = null;
        this.cluster = null;

        this.state = useState({
            loading: true,
            error: null,
            properties: [],          // all loaded properties
            // visible filter dropdown
            openDropdown: null,      // 'country' | 'level' | 'state' | 'type' | null
            filters: {
                countries: new Set(),   // selected country ids (or '__none__' for blank)
                levels: new Set(),
                states: new Set(),
                types: new Set(),
            },
            // derived
            options: {
                countries: [],   // [{id, name, count}]
                types: [],
            },
            // counters
            totalCount: 0,
            shownCount: 0,
            withoutCoordsCount: 0,
        });

        onMounted(() => this._load());
        onWillUnmount(() => this._teardown());

        // Close dropdowns on outside click
        this._onDocClick = (ev) => {
            if (!ev.target.closest(".o_pmd_filter")) {
                this.state.openDropdown = null;
            }
        };
        document.addEventListener("click", this._onDocClick);
    }

    async _load() {
        try {
            const [withCoords, missing] = await Promise.all([
                this.orm.searchRead(
                    "realestate.property",
                    ["|", ["latitude", "!=", 0], ["longitude", "!=", 0]],
                    [
                        "id", "name", "property_code", "latitude", "longitude",
                        "state", "occupancy_state", "hierarchy_level",
                        "country_id", "property_type_id", "city",
                    ],
                    { limit: 5000 }
                ),
                this.orm.searchCount(
                    "realestate.property",
                    [["latitude", "=", 0], ["longitude", "=", 0]],
                ),
            ]);
            this.state.properties = withCoords;
            this.state.totalCount = withCoords.length + missing;
            this.state.withoutCoordsCount = missing;
            this._buildOptions(withCoords);
            this.state.loading = false;
            await Promise.resolve();
            this._initMap();
            this._renderMarkers();
        } catch (err) {
            this.state.error = err.message || String(err);
            this.state.loading = false;
        }
    }

    _buildOptions(properties) {
        const countryMap = new Map();
        const typeMap = new Map();
        for (const p of properties) {
            const c = p.country_id;
            const key = c ? c[0] : "__none__";
            const name = c ? c[1] : _t("(no country)");
            const entry = countryMap.get(key) || { id: key, name, count: 0 };
            entry.count += 1;
            countryMap.set(key, entry);

            const t = p.property_type_id;
            if (t) {
                const tk = t[0];
                const te = typeMap.get(tk) || { id: tk, name: t[1], count: 0 };
                te.count += 1;
                typeMap.set(tk, te);
            }
        }
        this.state.options.countries = [...countryMap.values()].sort((a, b) => a.name.localeCompare(b.name));
        this.state.options.types = [...typeMap.values()].sort((a, b) => a.name.localeCompare(b.name));
    }

    _initMap() {
        if (typeof L === "undefined" || !this.mapRef.el) return;
        patchLeafletDefaults();

        this.map = L.map(this.mapRef.el, {
            center: WORLD_CENTER,
            zoom: WORLD_ZOOM,
            scrollWheelZoom: true,
            preferCanvas: true,
            worldCopyJump: true,
        });

        const streets = L.tileLayer(STREET_TILES.url, STREET_TILES.options);
        const satellite = L.tileLayer(SATELLITE_TILES.url, SATELLITE_TILES.options);
        streets.addTo(this.map);
        L.control.layers(
            { "Streets": streets, "Satellite": satellite },
            {},
            { position: "topright", collapsed: true }
        ).addTo(this.map);

        this.cluster = L.markerClusterGroup({
            chunkedLoading: true,
            maxClusterRadius: 60,
            spiderfyOnMaxZoom: true,
            showCoverageOnHover: false,
        });
        this.map.addLayer(this.cluster);

        setTimeout(() => this.map && this.map.invalidateSize({ animate: false }), 50);
    }

    _filterProperties() {
        const f = this.state.filters;
        return this.state.properties.filter((p) => {
            if (f.countries.size) {
                const key = p.country_id ? p.country_id[0] : "__none__";
                if (!f.countries.has(key)) return false;
            }
            if (f.levels.size && !f.levels.has(p.hierarchy_level)) return false;
            if (f.states.size && !f.states.has(p.state)) return false;
            if (f.types.size) {
                const tid = p.property_type_id ? p.property_type_id[0] : null;
                if (tid === null || !f.types.has(tid)) return false;
            }
            return true;
        });
    }

    _renderMarkers(fit = true) {
        if (!this.map || !this.cluster) return;
        this.cluster.clearLayers();
        const filtered = this._filterProperties();
        const markers = [];
        for (const p of filtered) {
            const color = statusColor(p.state);
            const marker = L.circleMarker([p.latitude, p.longitude], {
                radius: 8,
                color: "#ffffff",
                weight: 2,
                fillColor: color,
                fillOpacity: 0.9,
            });
            marker.bindPopup(() => this._popupHtml(p), { maxWidth: 320, minWidth: 240 });
            markers.push(marker);
        }
        this.cluster.addLayers(markers);
        this.state.shownCount = filtered.length;
        if (fit && filtered.length) {
            const bounds = L.latLngBounds(filtered.map((p) => [p.latitude, p.longitude]));
            this.map.fitBounds(bounds, { padding: [40, 40], maxZoom: 16 });
        }
    }

    _popupHtml(p) {
        return buildPropertyPopup(p, (id) => this._openProperty(id));
    }

    _openProperty(id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "realestate.property",
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    _teardown() {
        document.removeEventListener("click", this._onDocClick);
        if (this.map) {
            this.map.remove();
            this.map = null;
            this.cluster = null;
        }
    }

    // --- UI handlers ---

    onToggleDropdown(name, ev) {
        ev.stopPropagation();
        this.state.openDropdown = this.state.openDropdown === name ? null : name;
    }

    onToggleFilter(category, value, ev) {
        ev.stopPropagation();
        const set = this.state.filters[category];
        if (set.has(value)) set.delete(value); else set.add(value);
        this._renderMarkers(false);
    }

    onClearCategory(category, ev) {
        ev.stopPropagation();
        this.state.filters[category].clear();
        this._renderMarkers(false);
    }

    onResetFilters() {
        for (const k of Object.keys(this.state.filters)) {
            this.state.filters[k].clear();
        }
        this._renderMarkers(true);
    }

    onFitToData() {
        this._renderMarkers(true);
    }

    onResetWorld() {
        if (this.map) this.map.setView(WORLD_CENTER, WORLD_ZOOM);
    }

    isFilterActive(category, value) {
        return this.state.filters[category].has(value);
    }

    activeCount(category) {
        return this.state.filters[category].size;
    }

    // --- Computed getters used in template ---

    get stateOptions() {
        return STATE_ORDER.map((k) => ({ id: k, name: STATE_LABELS[k], color: statusColor(k) }));
    }

    get levelOptions() {
        return LEVEL_ORDER.map((k) => ({ id: k, name: LEVEL_LABELS[k] }));
    }

    get legendItems() {
        return STATE_ORDER.map((k) => ({ label: STATE_LABELS[k], color: statusColor(k) }));
    }
}

registry.category("actions").add("realestate.properties_map", PropertiesMapDashboard);
