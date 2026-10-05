/** @odoo-module **/

import { Component } from "@odoo/owl";

/**
 * A KPI's recent history, drawn as inline SVG.
 *
 * Deliberately not Chart.js. Six of these on a KPI row would mean six canvases,
 * six resize observers and a library load before the first number is readable;
 * a sparkline carries no axes, no legend and no tooltip, so a path is the whole
 * widget. It also scales with the page zoom and prints, which a canvas does not.
 */
export class Sparkline extends Component {
    static template = "atmta_dashboard.Sparkline";
    static props = {
        /** Oldest value first. */
        points: { type: Array },
        tone: { type: String, optional: true },
        width: { type: Number, optional: true },
        height: { type: Number, optional: true },
    };
    static defaultProps = { tone: "primary", width: 80, height: 32 };

    get geometry() {
        const { width, height } = this.props;
        const values = (this.props.points || []).filter((v) => Number.isFinite(v));
        if (values.length < 2) {
            return null;
        }
        const pad = 2;
        const min = Math.min(...values);
        const max = Math.max(...values);
        // A flat series has no range to divide by; draw it down the middle
        // rather than dividing by zero and producing NaN path data.
        const span = max - min || 1;
        const stepX = (width - pad * 2) / (values.length - 1);
        const coords = values.map((value, index) => {
            const x = pad + index * stepX;
            const y = max === min
                ? height / 2
                : height - pad - ((value - min) / span) * (height - pad * 2);
            return [x, y];
        });
        const line = coords.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
        const area = `${line} L${coords[coords.length - 1][0].toFixed(1)},${height} L${coords[0][0].toFixed(1)},${height} Z`;
        return { line, area, last: coords[coords.length - 1] };
    }
}
