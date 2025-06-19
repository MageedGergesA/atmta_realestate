/** @odoo-module **/

import { useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { CharField, charField } from "@web/views/fields/char/char_field";


export function getGoogleMapUrl(value) {
    if (!value || typeof value !== "string") {
        return false; // Invalid input
    }

    // 1️⃣ Extract `src` from an iframe tag (if the input is raw HTML)
    const iframeRegex = /<iframe[^>]+src=["']([^"']+)["']/;
    const iframeMatch = value.match(iframeRegex);

    if (iframeMatch && iframeMatch[1]) {
        if (iframeMatch[1].includes("https://www.google.com/maps/embed")) {
            return iframeMatch[1]; // Return extracted embed URL
        }
    }

    // 2️⃣ If the input is already an embed URL, return it as is
    if (value.includes("https://www.google.com/maps/embed")) {
        return value;
    }

    // 3️⃣ Extract latitude & longitude from standard Google Maps URL
    const latLngRegex = /@([-0-9.]+),([-0-9.]+)/;
    const latLngMatch = value.match(latLngRegex);

    if (latLngMatch) {
        const lat = latLngMatch[1];
        const lng = latLngMatch[2];

        // Convert standard Google Maps URL into an embeddable URL
        return `https://www.google.com/maps/embed/v1/place?key=YOUR_API_KEY&q=${lat},${lng}`;
    }

    return false; // Return false if it's not a valid Google Maps link
}

export class GoogleMapViewer extends CharField {
    static template = "web.GoogleMapViewer";

    setup() {
        super.setup();
        this.notification = useService("notification");
        this.state = useState({
            isValid: true,
        });
    }

    get url() {
        let url = this.props.value;
        if (this.props.record.data[this.props.name]) {
            url = getGoogleMapUrl(this.props.record.data[this.props.name]);
        }
        return url;
    }

    onLoadFailed() {
        this.state.isValid = false;
        this.notification.add(_t("Could not display the selected map"), { type: "danger" });
    }
}

export const googleMapViewer = {
    ...charField,
    component: GoogleMapViewer,
    displayName: _t("Google Map Viewer"),
};

registry.category("fields").add("embed_map_viewer", googleMapViewer);
