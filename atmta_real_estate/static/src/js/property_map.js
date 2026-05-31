/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, onMounted, onWillUpdateProps, onWillUnmount, useRef, useState } from "@odoo/owl";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { _t } from "@web/core/l10n/translation";

const LEAFLET_IMAGE_BASE = "/atmta_real_estate/static/src/lib/leaflet/images";
const DEFAULT_CENTER = [24.7136, 46.6753]; // Riyadh
const DEFAULT_ZOOM = 12;   // City level (was 5 = country)
const LOCATED_ZOOM = 18;   // Street / building level

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

export class PropertyMap extends Component {
    static template = "atmta_real_estate.PropertyMap";
    static props = {
        ...standardFieldProps,
        longitudeField: { type: String, optional: true },
    };
    static defaultProps = {
        longitudeField: "longitude",
    };

    setup() {
        this.mapRef = useRef("map");
        this.searchState = useState({ query: "", results: [], searching: false, error: null });
        this.map = null;
        this.marker = null;
        this.resizeObserver = null;

        onMounted(() => {
            // Defer init to next paint so the container has its real dimensions
            // (form tabs are present in the DOM but may have 0 size before layout settles)
            requestAnimationFrame(() => this._initMap());
        });
        onWillUpdateProps((nextProps) => this._syncMarker(nextProps));
        onWillUnmount(() => this._teardown());
    }

    get latitude() {
        return this.props.record.data[this.props.name];
    }

    get longitude() {
        return this.props.record.data[this.props.longitudeField];
    }

    get hasCoordinates() {
        return Boolean(this.latitude || this.longitude);
    }

    get readonly() {
        return this.props.readonly;
    }

    _initMap() {
        if (typeof L === "undefined") {
            console.error("Leaflet (L) is not loaded.");
            return;
        }
        if (!this.mapRef.el) return;
        patchLeafletDefaults();

        const center = this.hasCoordinates ? [this.latitude, this.longitude] : DEFAULT_CENTER;
        const zoom = this.hasCoordinates ? LOCATED_ZOOM : DEFAULT_ZOOM;

        this.map = L.map(this.mapRef.el, {
            center,
            zoom,
            scrollWheelZoom: true,
            preferCanvas: true,
        });

        // Streets layer (default) — Carto Voyager: richer street/building detail
        // than OSM-standard and typically faster from MENA region.
        const streetsLayer = L.tileLayer(
            "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
            {
                attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
                maxZoom: 20,
                subdomains: "abcd",
                crossOrigin: true,
            }
        );

        // Satellite layer — Esri World Imagery (no API key required)
        const satelliteLayer = L.tileLayer(
            "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            {
                attribution: 'Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics',
                maxZoom: 19,
                crossOrigin: true,
            }
        );

        streetsLayer.addTo(this.map);

        L.control.layers(
            { "Streets": streetsLayer, "Satellite": satelliteLayer },
            {},
            { position: "topright", collapsed: true }
        ).addTo(this.map);

        if (this.hasCoordinates) {
            this._placeMarker(this.latitude, this.longitude, false);
        }

        if (!this.readonly) {
            this.map.on("click", (ev) => {
                this._placeMarker(ev.latlng.lat, ev.latlng.lng, true);
            });
        }

        // Recompute tiles whenever the container resizes (handles tab switch,
        // form re-layout, sidebar collapse, window resize, etc.)
        if (typeof ResizeObserver !== "undefined") {
            this.resizeObserver = new ResizeObserver(() => {
                if (this.map) this.map.invalidateSize({ animate: false });
            });
            this.resizeObserver.observe(this.mapRef.el);
        }

        // One initial invalidate after a tick — covers the case where the page
        // becomes visible after a parent flex/grid finishes laying out.
        setTimeout(() => this.map && this.map.invalidateSize({ animate: false }), 50);
    }

    _placeMarker(lat, lng, persist) {
        if (!this.map) return;
        const latlng = [lat, lng];
        if (this.marker) {
            this.marker.setLatLng(latlng);
        } else {
            this.marker = L.marker(latlng, { draggable: !this.readonly }).addTo(this.map);
            if (!this.readonly) {
                this.marker.on("dragend", (ev) => {
                    const pos = ev.target.getLatLng();
                    this._writeCoords(pos.lat, pos.lng);
                });
            }
        }
        if (persist) {
            this._writeCoords(lat, lng);
        }
    }

    _writeCoords(lat, lng) {
        const update = {
            [this.props.name]: Number(lat.toFixed(7)),
            [this.props.longitudeField]: Number(lng.toFixed(7)),
        };
        this.props.record.update(update);
    }

    _syncMarker(nextProps) {
        if (!this.map) return;
        const lat = nextProps.record.data[nextProps.name];
        const lng = nextProps.record.data[nextProps.longitudeField];
        if (lat || lng) {
            if (this.marker) {
                this.marker.setLatLng([lat, lng]);
            } else {
                this._placeMarker(lat, lng, false);
            }
            this.map.setView([lat, lng], LOCATED_ZOOM);
        } else if (this.marker) {
            this.map.removeLayer(this.marker);
            this.marker = null;
        }
    }

    _teardown() {
        if (this.resizeObserver) {
            this.resizeObserver.disconnect();
            this.resizeObserver = null;
        }
        if (this.map) {
            this.map.remove();
            this.map = null;
            this.marker = null;
        }
    }

    async onSearch() {
        const query = (this.searchState.query || "").trim();
        if (!query) return;
        this.searchState.searching = true;
        this.searchState.error = null;
        this.searchState.results = [];
        try {
            const url = `https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(query)}&format=json&limit=5&addressdetails=0`;
            const response = await fetch(url, { headers: { "Accept-Language": "en" } });
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            const data = await response.json();
            this.searchState.results = data.map((r) => ({
                lat: parseFloat(r.lat),
                lng: parseFloat(r.lon),
                label: r.display_name,
            }));
            if (!this.searchState.results.length) {
                this.searchState.error = _t("No results found.");
            }
        } catch (err) {
            this.searchState.error = _t("Search failed: ") + err.message;
        } finally {
            this.searchState.searching = false;
        }
    }

    onSearchKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.onSearch();
        }
    }

    onSelectResult(result) {
        if (!this.map) return;
        this.map.setView([result.lat, result.lng], LOCATED_ZOOM);
        this._placeMarker(result.lat, result.lng, true);
        this.searchState.results = [];
        this.searchState.query = result.label;
    }

    onClearLocation() {
        if (this.marker) {
            this.map.removeLayer(this.marker);
            this.marker = null;
        }
        this.props.record.update({
            [this.props.name]: 0,
            [this.props.longitudeField]: 0,
        });
    }
}

export const propertyMap = {
    component: PropertyMap,
    displayName: _t("Property Map"),
    supportedTypes: ["float"],
    extractProps: ({ options }) => ({
        longitudeField: options.longitude_field || "longitude",
    }),
};

registry.category("fields").add("property_map", propertyMap);
