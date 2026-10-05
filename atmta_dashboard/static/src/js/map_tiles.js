/** @odoo-module **/

/**
 * The satellite background, shared by every map in the suite.
 *
 * It lives here because this module owns the Leaflet assets, and because the
 * three widgets that offer a satellite layer sit in modules that cannot
 * import from each other -- Developer does not depend on Rental. Each kept
 * its own copy of the URL and its own zoom ceiling, which is how the bug
 * below came to be fixed in none of them.
 *
 * `maxNativeZoom` is the point of this file. Esri's World Imagery does not
 * cover the globe to the same depth, and where it runs out it does NOT return
 * a 404 that Leaflet could fall back from -- it returns a perfectly valid
 * grey tile reading "Map data not yet available". Declaring `maxZoom: 19`
 * therefore requested tiles that do not exist and painted that grey grid over
 * the property, which is what a user saw instead of their site.
 *
 * Measured against the tile server for a north-coast Egypt location: zoom 12
 * to 18 return real imagery (12-21 KB); zoom 19 and 20 return an identical
 * 2,521-byte placeholder. 18 is the safe ceiling, and Leaflet upscales the
 * last real tile beyond it -- slightly soft, but an image of the actual place
 * rather than a notice that there isn't one.
 *
 * `maxZoom` stays at 19, so the reader can still zoom that far; only the tile
 * REQUESTS stop at 18.
 */
export const SATELLITE_TILES = {
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    options: {
        attribution: 'Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics',
        maxZoom: 19,
        maxNativeZoom: 18,
        crossOrigin: true,
    },
};

/** Place names and boundaries, drawn over the imagery in a hybrid view. */
export const SATELLITE_LABEL_TILES = {
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
    options: {
        attribution: "",
        maxZoom: 19,
        maxNativeZoom: 18,
        opacity: 0.9,
        crossOrigin: true,
    },
};
