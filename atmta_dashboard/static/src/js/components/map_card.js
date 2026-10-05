/** @odoo-module **/

import { Component, onMounted, onWillUnmount, useEffect, useRef, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";

const IMAGE_BASE = "/atmta_dashboard/static/src/lib/leaflet/images";
const TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const ATTRIBUTION =
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

let iconsPatched = false;

/**
 * Leaflet ships its marker icons as relative URLs, which resolve against the
 * CSS file rather than the module. Pointing them at the real path once, the
 * first time any map mounts, stops every marker 404ing.
 */
function patchIcons() {
    if (iconsPatched || typeof L === "undefined") {
        return;
    }
    delete L.Icon.Default.prototype._getIconUrl;
    L.Icon.Default.mergeOptions({
        iconRetinaUrl: `${IMAGE_BASE}/marker-icon-2x.png`,
        iconUrl: `${IMAGE_BASE}/marker-icon.png`,
        shadowUrl: `${IMAGE_BASE}/marker-shadow.png`,
    });
    iconsPatched = true;
}

/**
 * A map of whatever the provider sends: projects, sites, units.
 *
 * Deliberately dumb about the business. It is given points with a tone and an
 * optional boundary polygon and draws them; what a tone *means* is the
 * provider's business, which is why the legend is passed in rather than
 * hard-coded. That is what lets Development, Construction and Investment all
 * use one map instead of three.
 */
export class AtmtaMapCard extends Component {
    static template = "atmta_dashboard.MapCard";
    static props = {
        points: { type: Array },
        legend: { type: Array, optional: true },
        onPoint: { type: Function, optional: true },
        // How far the map may zoom in when fitting its points.
        //
        // A portfolio spread across cities wants to stop at town level; a
        // single project's site wants to go right in, because its structures
        // sit fifty metres apart and at town level they stack into one blob.
        // The default is the portfolio case, since that is the common one.
        fitMaxZoom: { type: Number, optional: true },
        emptyText: { type: String, optional: true },
    };

    setup() {
        this.mapRef = useRef("map");
        this.map = null;
        this.layer = null;
        this.state = useState({ selected: null });

        // Whether the map has ever been framed. Until it has, the next draw
        // fits the points; after that the view belongs to whoever is reading
        // it.
        this.framed = false;

        onMounted(() => {
            this.draw();
            this.watchResize();
        });
        // Redraw when the provider sends a DIFFERENT set -- compared by
        // content, not by array identity. The provider builds a new array on
        // every refresh even when nothing moved, so keying on the array
        // itself redrew the map constantly and threw the reader's view away
        // each time.
        useEffect(() => this.draw(), () => [this.signature]);
        onWillUnmount(() => this.teardown());
    }

    /**
     * What the map is drawing, as a value that can be compared.
     *
     * Position and colour only: a label changing does not move a pin, and a
     * redraw that does not move anything is a redraw worth skipping.
     */
    get signature() {
        return (this.props.points || [])
            .map((p) => `${p.id}:${p.lat}:${p.lng}:${p.tone}:${(p.boundary || []).length}`)
            .join("|");
    }

    get isEmpty() {
        return !(this.props.points || []).length;
    }

    get emptyText() {
        return this.props.emptyText || _t("Nothing on the map yet.");
    }

    tone(point) {
        const value = getComputedStyle(document.documentElement)
            .getPropertyValue(`--ad-${point.tone || "primary"}`);
        return value.trim() || "#7645d9";
    }

    draw() {
        if (typeof L === "undefined" || !this.mapRef.el || this.isEmpty) {
            return;
        }
        patchIcons();
        if (!this.map) {
            this.map = L.map(this.mapRef.el, { preferCanvas: true, scrollWheelZoom: false });
            L.tileLayer(TILES, { attribution: ATTRIBUTION, maxZoom: 19 }).addTo(this.map);
        }
        if (this.layer) {
            this.layer.remove();
        }
        this.layer = L.layerGroup().addTo(this.map);

        const bounds = [];
        for (const point of this.props.points) {
            if (!Number.isFinite(point.lat) || !Number.isFinite(point.lng)) {
                continue;
            }
            const colour = this.tone(point);
            // A boundary says far more than a pin: it shows the plot, not a
            // guess at its centre.
            if (point.boundary && point.boundary.length > 2) {
                L.polygon(point.boundary, {
                    color: colour, weight: 2, fillColor: colour, fillOpacity: 0.15,
                }).addTo(this.layer);
                point.boundary.forEach((pair) => bounds.push(pair));
            }
            const marker = L.circleMarker([point.lat, point.lng], {
                radius: 9, color: "#fff", weight: 2,
                fillColor: colour, fillOpacity: 0.95,
            }).addTo(this.layer);
            marker.bindTooltip(point.label, { direction: "top" });
            marker.on("click", () => {
                this.state.selected = point;
                if (this.props.onPoint) {
                    this.props.onPoint(point);
                }
            });
            bounds.push([point.lat, point.lng]);
        }
        // Frame the map once, then leave it alone.
        //
        // Re-fitting on every draw meant a reader who zoomed into one tower
        // was thrown back out to the whole plot the moment anything refreshed
        // -- and the dashboard refreshes on its own. The view is theirs after
        // the first fit.
        //
        // The exception is points that have moved out of sight: if none of
        // what we are drawing falls inside the current view, keeping that
        // view would show an empty map, which is worse than moving it. That
        // happens when the filter switches to another project, which is
        // exactly when a reader expects the map to travel.
        if (bounds.length) {
            const target = L.latLngBounds(bounds);
            if (!this.framed || !this.map.getBounds().intersects(target)) {
                this.map.fitBounds(target, {
                    padding: [24, 24],
                    maxZoom: this.props.fitMaxZoom || 13,
                });
                this.framed = true;
            }
        }
    }

    /** Re-frame on demand, for a control that offers it. */
    resetView() {
        this.framed = false;
        this.draw();
    }

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

    teardown() {
        if (this.resizeObserver) {
            this.resizeObserver.disconnect();
            this.resizeObserver = null;
        }
        if (this.map) {
            this.map.remove();
            this.map = null;
            this.layer = null;
        }
    }
}
