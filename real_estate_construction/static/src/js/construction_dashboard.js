/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, onMounted, onWillStart, onWillUnmount, useEffect, useRef, useState } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { useService } from "@web/core/utils/hooks";

const MS_COLORS = {
    not_started: "#9e9e9e", in_progress: "#0d6efd", completed: "#28a745",
    delayed: "#dc3545", cancelled: "#6c757d",
};
const MS_LABELS = {
    not_started: "Not Started", in_progress: "In Progress", completed: "Completed",
    delayed: "Delayed", cancelled: "Cancelled",
};

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

export class ConstructionDashboard extends Component {
    static template = "real_estate_construction.ConstructionDashboard";
    static props = ["*"];

    setup() {
        // Chart.js is loaded on demand from Odoo's own `web.chartjs_lib`
        // bundle. This dashboard previously relied on a copy that
        // atmta_real_estate pushed into web.assets_backend for every page;
        // that duplicate was removed in atmta_real_estate 0.4, so each
        // consumer now loads the library itself, as Odoo's graph view does.
        onWillStart(() => loadBundle("web.chartjs_lib"));

        this.orm = useService("orm");
        this.action = useService("action");
        this.mapRef = useRef("map");
        this.donutMsRef = useRef("donutMs");
        this.barCostRef = useRef("barCost");
        this.barContractorsRef = useRef("barContractors");
        this.lineSpendRef = useRef("lineSpend");
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
            this.state.data = await this.orm.call("realestate.construction.dashboard", "get_data", []);
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
        this._renderMsStates();
        this._renderCostProj();
        this._renderContractors();
        this._renderSpend();
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
            const prog = p.construction_progress || 0;
            const color = prog >= 90 ? "#28a745" : prog >= 50 ? "#0d6efd" : prog >= 20 ? "#fb8c00" : "#dc3545";
            const m = L.circleMarker([p.latitude, p.longitude], {
                radius: 10, color: "#fff", weight: 2, fillColor: color, fillOpacity: 0.9,
            });
            const name = (p.name || "").replace(/</g, "&lt;");
            m.bindPopup(`<div><div class="small text-muted">${p.code || ''}</div>
                <div><strong>${name}</strong></div>
                <div class="small">${p.city || ''}</div>
                <div class="mt-1"><strong>Progress:</strong> ${Math.round(prog)}%</div></div>`);
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

    _renderMsStates() {
        const el = this.donutMsRef.el; if (!el) return;
        el.setAttribute("data-key", "donutMs");
        const d = this.state.data.ms_states;
        this._draw(el, "doughnut", {
            labels: Object.keys(d).map(k => MS_LABELS[k] || k),
            datasets: [{ data: Object.values(d),
                backgroundColor: Object.keys(d).map(k => MS_COLORS[k] || "#888"), borderWidth: 1 }],
        }, { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "bottom" } } });
    }

    _renderCostProj() {
        const el = this.barCostRef.el; if (!el) return;
        el.setAttribute("data-key", "barCost");
        const d = this.state.data.cost_proj;
        this._draw(el, "bar", {
            labels: d.labels,
            datasets: [
                { label: "Budget", data: d.budget, backgroundColor: "#bbdefb" },
                { label: "Actual", data: d.actual, backgroundColor: "#1976d2" },
            ],
        }, { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "bottom" } } });
    }

    _renderContractors() {
        const el = this.barContractorsRef.el; if (!el) return;
        el.setAttribute("data-key", "barContractors");
        const t = this.state.data.top_contractors || [];
        this._draw(el, "bar", {
            labels: t.map(x => x[0]),
            datasets: [{ label: "Active Milestones", data: t.map(x => x[1]), backgroundColor: "#fb8c00" }],
        }, { responsive: true, maintainAspectRatio: false, indexAxis: "y",
             plugins: { legend: { display: false } } });
    }

    _renderSpend() {
        const el = this.lineSpendRef.el; if (!el) return;
        el.setAttribute("data-key", "lineSpend");
        const d = this.state.data.spend_trend;
        this._draw(el, "line", {
            labels: d.labels,
            datasets: [{ label: "Spend", data: d.amounts,
                borderColor: "#dc3545", backgroundColor: "rgba(220,53,69,0.1)",
                tension: 0.3, fill: true }],
        }, { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } });
    }

    _teardown() {
        if (this.refreshTimer) clearInterval(this.refreshTimer);
        if (this.map) { this.map.remove(); this.map = null; }
        for (const k in this.charts) { try { this.charts[k].destroy(); } catch {} }
        this.charts = {};
    }

    openMilestone(id) { this.action.doAction({ type:"ir.actions.act_window", res_model:"realestate.construction.milestone", res_id:id, views:[[false,"form"]] }); }
    openCostLine(id) { this.action.doAction({ type:"ir.actions.act_window", res_model:"realestate.construction.cost.line", res_id:id, views:[[false,"form"]] }); }
    openProject(id) { this.action.doAction({ type:"ir.actions.act_window", res_model:"realestate.project", res_id:id, views:[[false,"form"]] }); }

    formatMoney(n) {
        if (n === null || n === undefined) return "0";
        return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(n);
    }
}

registry.category("actions").add("realestate.construction_dashboard", ConstructionDashboard);
