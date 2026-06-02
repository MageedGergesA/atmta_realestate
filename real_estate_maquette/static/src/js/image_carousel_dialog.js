/** @odoo-module **/

import { Component, useRef, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";

/** Modal carousel of unit images. Frontend-safe: depends only on OWL +
 *  @web/core (no view/field imports), so it works on portal pages too.
 *
 *  Optional backend mode: pass `propertyId` + `canUpload=true` to expose an
 *  Upload button that creates a property.image record on the unit and
 *  appends it to the carousel inline. Portal pages should leave both off.
 */
export class CarouselDialog extends Component {
    static template = "real_estate_maquette.CarouselDialog";
    static components = { Dialog };
    static props = {
        images: Array,
        title: { type: String, optional: true },
        propertyId: { type: Number, optional: true },
        canUpload: { type: Boolean, optional: true },
        close: { type: Function, optional: true },
    };
    setup() {
        this.state = useState({
            index: 0,
            // local copy so we can mutate after uploads
            images: [...this.props.images],
            uploading: false,
        });
        this.fileInputRef = useRef("fileInput");
        try {
            this.orm = useService("orm");
            this.notification = useService("notification");
        } catch (_e) {
            this.orm = null;
            this.notification = null;
        }
    }
    get current() {
        return this.state.images[this.state.index];
    }
    get canUpload() {
        return !!(this.props.canUpload && this.props.propertyId && this.orm);
    }
    prev() {
        const n = this.state.images.length;
        if (n) this.state.index = (this.state.index - 1 + n) % n;
    }
    next() {
        const n = this.state.images.length;
        if (n) this.state.index = (this.state.index + 1) % n;
    }
    triggerUpload() {
        this.fileInputRef.el?.click();
    }
    async onFilesPicked(ev) {
        const files = Array.from(ev.target.files || []);
        if (!files.length || !this.canUpload) return;
        this.state.uploading = true;
        try {
            const created = [];
            for (const file of files) {
                if (!file.type.startsWith("image/")) continue;
                const b64 = await this._fileToBase64(file);
                const [id] = await this.orm.create("property.image", [{
                    name: file.name || "Image",
                    image_1920: b64,
                    property_id: this.props.propertyId,
                }]);
                created.push({
                    id,
                    src: `/web/image/property.image/${id}/image_1920`,
                    name: file.name || "",
                });
            }
            if (created.length) {
                this.state.images = [...this.state.images, ...created];
                this.state.index = this.state.images.length - created.length;
                this.notification?.add(
                    `${created.length} image${created.length > 1 ? "s" : ""} uploaded.`,
                    { type: "success" },
                );
            }
        } catch (err) {
            this.notification?.add(err.message || String(err), { type: "danger" });
        } finally {
            this.state.uploading = false;
            if (this.fileInputRef.el) this.fileInputRef.el.value = "";
        }
    }
    _fileToBase64(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => {
                // FileReader result is "data:<mime>;base64,<payload>" — strip the prefix.
                const result = reader.result || "";
                const comma = result.indexOf(",");
                resolve(comma >= 0 ? result.slice(comma + 1) : result);
            };
            reader.onerror = () => reject(reader.error);
            reader.readAsDataURL(file);
        });
    }
}
