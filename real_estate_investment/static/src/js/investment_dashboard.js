/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, onMounted, onWillUnmount, useEffect, useRef, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

const FEAS_COLORS = { draft: "#6c757d", approved: "#28a745", rejected: "#dc3545", archived: "#9e9e9e" };
const FEAS_LABELS = { draft: "Draft", approved: "Approved", rejected: "Rejected", archived: "Archived" };

let leafletPatched = false;
function patchLeaflet() {
    if (leafletPatched || typeof L === "undefined") return;
    const base = "/atmta_real_estate/static/src/lib/leaflet/images";
    delete L.Icon.Default.prototype._getIconUrl;
    L.Icon.Default.mergeOptions({
        iconRetinaUrl: `${base}/marker-icon-2x.png`,
        iconUrl: `${base}/marker-icon.png`,
        shadowUrl: `${base}/marker-shadow.png`,
    });
    leafletPatched = true;
}

export class InvestmentDashboard extends Component {
    static template = "real_estate_investment.InvestmentDashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.mapRef = useRef("map");
        this.donutFeasRef = useRef("donutFeas");
        this.barNpvRef = useRef("barNpv");
        this.barIrrRef = useRef("barIrr");
        this.barPbRef = useRef("barPb");
        this.barSensRef = useRef("barSens");
        this.state = useState({
            loading: true, data: null, lastUpdate: null,
            autoRefresh: true, refreshInterval: 30000,
        });
        this.map = null; this.cluster = null; this.charts = {};
        this.refreshTimer = null;
        onMounted(async () => {
            await this.load();
            if (this.state.autoRefresh)
                this.refreshTimer = setInterval(() => this.refresh(), this.state.refreshInterval);
        });
        useEffect(() => {
            if (this.state.data) Promise.resolve().then(() => this.renderAll());
        }, () => [this.state.data]);
        onWillUnmount(() => this._teardown());
    }

    async load() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call("realestate.investment.dashboard", "get_data", []);
            this.state.lastUpdate = new Date().toLocaleTimeString();
        } finally { this.state.loading = false; }
    }
    async refresh() { await this.load(); }
    toggleAutoRefresh() {
        this.state.autoRefresh = !this.state.autoRefresh;
        if (this.state.autoRefresh)
            this.refreshTimer = setInterval(() => this.refresh(), this.state.refreshInterval);
        else { clearInterval(this.refreshTimer); this.refreshTimer = null; }
    }

    renderAll() {
        if (!this.state.data) return;
        this._renderMap();
        this._renderFeasStates();
        this._renderNpv();
        this._renderIrr();
        this._renderPb();
        this._renderSens();
    }

    _renderMap() {
        if (typeof L === "undefined" || !this.mapRef.el) return;
        patchLeaflet();
        if (!this.map) {
            this.map = L.map(this.mapRef.el, { center: [24.7136, 46.6753], zoom: 5, preferCanvas: true });
            L.tileLayer("https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png", {
                attribution: '&copy; CARTO', maxZoom: 19, subdomains: "abcd",
            }).addTo(this.map);
            this.cluster = L.markerClusterGroup({ chunkedLoading: true });
            this.map.addLayer(this.cluster);
        }
        this.cluster.clearLayers();
        const projs = this.state.data.map_projs || [];
        const markers = [];
        for (const p of projs) {
            const color = !p.has_study ? "#9e9e9e" : (p.npv > 0 ? "#28a745" : "#dc3545");
            const m = L.circleMarker([p.latitude, p.longitude], {
                radius: 10, color: "#fff", weight: 2, fillColor: color, fillOpacity: 0.9,
            });
            const name = (p.name || "").replace(/</g, "&lt;");
            const status = !p.has_study ? "No study" : (p.npv > 0 ? "Positive NPV" : "Negative NPV");
            m.bindPopup(`<div><div class="small text-muted">${p.code || ''}</div>
                <div><strong>${name}</strong></div>
                <div class="small">${p.city || ''}</div>
                <div class="mt-1">NPV: <strong>${this.formatMoney(p.npv)}</strong> · IRR: <strong>${Math.round(p.irr * 10) / 10}%</strong></div>
                <span class="badge" style="background:${color}">${status}</span></div>`);
            markers.push(m);
        }
        this.cluster.addLayers(markers);
        if (markers.length) this.map.fitBounds(this.cluster.getBounds(), { padding: [40, 40], maxZoom: 12 });
        setTimeout(() => this.map && this.map.invalidateSize(), 100);
    }

    _draw(refEl, type, data, options) {
        if (typeof Chart === "undefined" || !refEl) return;
        const key = refEl.getAttribute("data-key") || refEl.id;
        if (this.charts[key]) this.charts[key].destroy();
        this.charts[key] = new Chart(refEl, { type, data, options });
    }

    _renderFeasStates() {
        const el = this.donutFeasRef.el; if (!el) return;
        el.setAttribute("data-key", "donutFeas");
        const d = this.state.data.feas_states;
        this._draw(el, "doughnut", {
            labels: Object.keys(d).map(k => FEAS_LABELS[k] || k),
            datasets: [{ data: Object.values(d),
                backgroundColor: Object.keys(d).map(k => FEAS_COLORS[k] || "#888"), borderWidth: 1 }],
        }, { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "bottom" } } });
    }

    _renderNpv() {
        const el = this.barNpvRef.el; if (!el) return;
        el.setAttribute("data-key", "barNpv");
        const d = this.state.data.npv_chart;
        const colors = d.npv.map(v => v > 0 ? "#28a745" : "#dc3545");
        this._draw(el, "bar", {
            labels: d.labels,
            datasets: [{ label: "NPV", data: d.npv, backgroundColor: colors }],
        }, { responsive: true, maintainAspectRatio: false, indexAxis: "y",
             plugins: { legend: { display: false } } });
    }

    _renderIrr() {
        const el = this.barIrrRef.el; if (!el) return;
        el.setAttribute("data-key", "barIrr");
        const d = this.state.data.irr_buckets;
        this._draw(el, "bar", {
            labels: Object.keys(d),
            datasets: [{ label: "Studies", data: Object.values(d),
                backgroundColor: ["#dc3545","#fd7e14","#fbbf24","#0d6efd","#28a745"] }],
        }, { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } });
    }

    _renderPb() {
        const el = this.barPbRef.el; if (!el) return;
        el.setAttribute("data-key", "barPb");
        const d = this.state.data.pb_buckets;
        this._draw(el, "bar", {
            labels: Object.keys(d),
            datasets: [{ label: "Studies", data: Object.values(d),
                backgroundColor: "#283593" }],
        }, { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } });
    }

    _renderSens() {
        const el = this.barSensRef.el; if (!el) return;
        el.setAttribute("data-key", "barSens");
        const d = this.state.data.sens_avg;
        this._draw(el, "bar", {
            labels: ["Best", "Expected", "Worst", "Custom"],
            datasets: [{ label: "Avg NPV",
                data: [d.best, d.expected, d.worst, d.custom],
                backgroundColor: ["#28a745","#0d6efd","#dc3545","#6a1b9a"] }],
        }, { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } });
    }

    _teardown() {
        if (this.refreshTimer) clearInterval(this.refreshTimer);
        if (this.map) { this.map.remove(); this.map = null; }
        for (const k in this.charts) { try { this.charts[k].destroy(); } catch {} }
        this.charts = {};
    }

    openStudy(id) { this.action.doAction({ type:"ir.actions.act_window", res_model:"realestate.investment.feasibility", res_id:id, views:[[false,"form"]] }); }
    openScenario(id) { this.action.doAction({ type:"ir.actions.act_window", res_model:"realestate.investment.scenario", res_id:id, views:[[false,"form"]] }); }

    formatMoney(n) {
        if (n === null || n === undefined) return "0";
        return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(n);
    }
}

registry.category("actions").add("realestate.investment_dashboard", InvestmentDashboard);
