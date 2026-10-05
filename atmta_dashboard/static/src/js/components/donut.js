/** @odoo-module **/

import { Component } from "@odoo/owl";

const TONES = ["success", "warning", "danger", "primary", "info", "neutral"];

/**
 * A status breakdown: ring on one side, counts and shares on the other.
 *
 * Inline SVG rather than Chart.js. The ring carries six segments and no axes,
 * so a library buys nothing here, and the legend beside it is a real list --
 * selectable text, readable by a screen reader, and clickable per row into the
 * records behind that segment. A canvas legend is none of those things.
 *
 * Segments worth 0 are kept in the legend and dropped from the ring. Showing
 * "Pending 0" is useful; drawing a zero-width arc is not.
 */
export class AtmtaDonut extends Component {
    static template = "atmta_dashboard.Donut";
    static props = {
        segments: { type: Array },
        total: { type: [Number, Boolean], optional: true },
        totalLabel: { type: String, optional: true },
        onSegment: { type: Function, optional: true },
        size: { type: Number, optional: true },
        thickness: { type: Number, optional: true },
    };
    static defaultProps = { size: 160, thickness: 26 };

    get arcs() {
        const segments = this.props.segments || [];
        const sum = segments.reduce((acc, s) => acc + (s.value || 0), 0);
        if (!sum) {
            return [];
        }
        const { size, thickness } = this.props;
        const radius = (size - thickness) / 2;
        const centre = size / 2;
        const circumference = 2 * Math.PI * radius;
        let consumed = 0;
        const arcs = [];
        segments.forEach((segment, index) => {
            const value = segment.value || 0;
            if (!value) {
                return;
            }
            const length = (value / sum) * circumference;
            arcs.push({
                key: segment.key,
                tone: segment.tone || TONES[index % TONES.length],
                // A 1px gap between neighbours reads as separate segments
                // without a stroke that would distort the proportions.
                dash: `${Math.max(length - 1.5, 0.5)} ${circumference - Math.max(length - 1.5, 0.5)}`,
                offset: -consumed,
                radius,
                centre,
            });
            consumed += length;
        });
        return arcs;
    }

    get legend() {
        const segments = this.props.segments || [];
        const sum = segments.reduce((acc, s) => acc + (s.value || 0), 0);
        return segments.map((segment, index) => ({
            ...segment,
            tone: segment.tone || TONES[index % TONES.length],
            percent: sum ? Math.round((segment.value / sum) * 100) : 0,
        }));
    }

    get isEmpty() {
        return !this.arcs.length;
    }

    get viewBox() {
        return `0 0 ${this.props.size} ${this.props.size}`;
    }

    onSegment(segment) {
        if (this.props.onSegment && segment.key) {
            this.props.onSegment(segment.key);
        }
    }
}
