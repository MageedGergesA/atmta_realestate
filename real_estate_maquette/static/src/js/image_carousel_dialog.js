/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";

/** Modal carousel of unit images. Frontend-safe: depends only on OWL +
 *  @web/core (no view/field imports), so it works on portal pages too.
 *  The <Dialog> supplies the X close button. */
export class CarouselDialog extends Component {
    static template = "real_estate_maquette.CarouselDialog";
    static components = { Dialog };
    static props = {
        images: Array,
        title: { type: String, optional: true },
        close: { type: Function, optional: true },
    };
    setup() {
        this.state = useState({ index: 0 });
    }
    get current() {
        return this.props.images[this.state.index];
    }
    prev() {
        const n = this.props.images.length;
        if (n) this.state.index = (this.state.index - 1 + n) % n;
    }
    next() {
        const n = this.props.images.length;
        if (n) this.state.index = (this.state.index + 1) % n;
    }
}
