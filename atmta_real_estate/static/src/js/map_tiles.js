/** @odoo-module **/

/**
 * The street map background, shared by Units → Map and the Overview's map card.
 *
 * Map backgrounds come from an online tile server; everything else the maps
 * draw (units, clusters, legends) is local. Without internet access both maps
 * still show their units, on a blank background (decision 15 Sep 2026). This is
 * the one online address the dashboard is allowed to use, and
 * `tests/test_dashboard_assets.py` holds it to exactly this host.
 */
export const STREET_TILES = {
    // OpenStreetMap's own tile server: free, no API key. CARTO's Voyager tiles,
    // used until 16 Sep 2026, started stamping "API KEY REQUIRED" across the map.
    url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    options: {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        maxZoom: 19,
        crossOrigin: true,
    },
};


// The satellite layer lives in `atmta_dashboard`, which owns the Leaflet
// assets and which Developer also depends on -- Developer cannot import from
// here. Re-exported so this module's own widgets keep one import path.
export { SATELLITE_TILES } from "@atmta_dashboard/js/map_tiles";

/**
 * One set of unit-status colours for every map, marker, popup and legend.
 *
 * The maps used raw Bootstrap colours (bright green, orange, blue, yellow) while
 * the dashboard's charts and tiles use a muted palette, and Leaflet's clusters
 * added their own yellow on top -- so one status looked different in each place.
 * They live here, beside the tiles, because both maps already import this
 * file. The colours are CSS custom properties (`--re-status-*`, declared in
 * rental_dashboard.scss beside the chart palette); this reads them, so a theme
 * change is one edit. The fallbacks are the same values, for a page where the
 * stylesheet has not been applied yet.
 */
const FALLBACK = {
    available: "#0f7b6c",
    reserved: "#c7892d",
    rented: "#71639e",
    sold: "#5b899e",
    maintenance: "#a5757d",
    inactive: "#adb5bd",
};

export const UNKNOWN_STATUS_COLOR = "#adb5bd";

export function statusColor(status) {
    const value = getComputedStyle(document.documentElement)
        .getPropertyValue(`--re-status-${status}`)
        .trim();
    return value || FALLBACK[status] || UNKNOWN_STATUS_COLOR;
}
