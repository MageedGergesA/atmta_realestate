/** @odoo-module **/

import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { CarouselDialog } from "./image_carousel_dialog";

/** Backend field widget — a "View Images (N)" button that opens the carousel.
 *  Lives in this file (not image_carousel_dialog.js) because it imports
 *  @web/views/fields, which is backend-only. */
export class ImageCarouselField extends Component {
    static template = "real_estate_maquette.ImageCarouselField";
    static props = { ...standardFieldProps };
    setup() {
        this.dialog = useService("dialog");
    }
    get images() {
        const val = this.props.record.data[this.props.name];
        let ids = [];
        if (val && val.records) {
            ids = val.records.map((r) => r.resId);
        } else if (Array.isArray(val)) {
            ids = val.map((v) => (typeof v === "number" ? v : v.id || v.resId)).filter(Boolean);
        }
        return ids.map((id) => ({
            id,
            src: `/web/image/property.image/${id}/image_1920`,
        }));
    }
    get count() {
        return this.images.length;
    }
    openCarousel() {
        // Open even when empty so the upload affordance is reachable.
        this.dialog.add(CarouselDialog, {
            images: this.images,
            title: "Unit Images",
            propertyId: this.props.record.resId || undefined,
            canUpload: !!this.props.record.resId,
        });
    }
}

export const imageCarouselField = {
    component: ImageCarouselField,
    supportedTypes: ["many2many", "one2many"],
};

registry.category("fields").add("image_carousel", imageCarouselField);

// Re-export so existing code that does `import { CarouselDialog } from "./image_carousel"`
// (backend code) keeps working.
export { CarouselDialog };
