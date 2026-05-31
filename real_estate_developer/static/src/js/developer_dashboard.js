/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, onMounted, onWillUnmount, useEffect, useRef, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

const PROJ_COLORS = {
    planning: "#6c757d", construction: "#fb8c00", marketing: "#0d6efd",
    handover: "#9c27b0", completed: "#28a745", cancelled: "#dc3545",
};
const PROJ_LABELS = {
    planning: "Planning", construction: "Construction", marketing: "Marketing",
    handover: "Handover", completed: "Completed", cancelled: "Cancelled",
};
const UNIT_COLORS = {
    available: "#28a745", reserved: "#fd7e14", rented: "#0d6efd",
    maintenance: "#ffc107", inactive: "#6c757d",
};
const UNIT_LABELS = {
    available: "Available", reserved: "Reserved", rented: "Sold/Rented",
    maintenance: "Maintenance", inactive: "Inactive",
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

export class DeveloperDashboard extends Component {
    static template = "real_estate_developer.DeveloperDashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.mapRef = useRef("map");
        this.donutProjRef = useRef("donutProj");
        this.donutUnitRef = useRef("donutUnit");
        this.barProjectsRef = useRef("barProjects");
        this.lineVelocityRef = useRef("lineVelocity");
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
            this.state.data = await this.orm.call("realestate.developer.dashboard", "get_data", []);
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
        this._renderProjStates();
        this._renderUnitStates();
        this._renderTopProjects();
        this._renderVelocity();
    }

    _renderMap() {
        if (typeof L === "undefined" || !this.mapRef.el) return;
        patchLeaflet();
        if (!this.map) {
            this.map = L.map(this.mapRef.el, { center: [24.7136, 46.6753], zoom: 5, preferCanvas: true });
            const streets = L.tileLayer("https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png", {
                attribution: '&copy; CARTO', maxZoom: 19, subdomains: "abcd",
            });
            const satellite = L.tileLayer(
                "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                { attribution: "Tiles &copy; Esri", maxZoom: 19 }
            );
            const hybridLabels = L.tileLayer(
                "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
                { attribution: "", maxZoom: 19, opacity: 0.9 }
            );
            const hybrid = L.layerGroup([satellite, hybridLabels]);
            streets.addTo(this.map);
            L.control.layers(
                { "Streets": streets, "Satellite": satellite, "Satellite + Labels": hybrid },
                null,
                { position: "topright", collapsed: true }
            ).addTo(this.map);
            this.cluster = L.markerClusterGroup({ chunkedLoading: true });
            this.map.addLayer(this.cluster);
        }
        this.cluster.clearLayers();
        if (this.polygonsLayer) { this.map.removeLayer(this.polygonsLayer); }
        this.polygonsLayer = L.layerGroup().addTo(this.map);
        const projs = this.state.data.map_projs || [];
        const markers = [];
        const allBounds = [];
        for (const p of projs) {
            const color = PROJ_COLORS[p.state] || "#888";
            const name = (p.name || "").replace(/</g, "&lt;");
            const popupHtml = `<div><div class="small text-muted">${p.code || ''}</div>
                <div><strong>${name}</strong></div>
                <div class="small">${p.city || ''} · ${p.project_type}</div>
                <span class="badge" style="background:${color}">${PROJ_LABELS[p.state]}</span></div>`;
            // Polygon boundary (if defined)
            if (p.boundary && p.boundary.length >= 3) {
                const poly = L.polygon(p.boundary, {
                    color: color, weight: 2, fillColor: color, fillOpacity: 0.25,
                });
                poly.bindPopup(popupHtml);
                this.polygonsLayer.addLayer(poly);
                allBounds.push(poly.getBounds());
            }
            // Centroid marker
            const m = L.circleMarker([p.latitude, p.longitude], {
                radius: 10, color: "#fff", weight: 2, fillColor: color, fillOpacity: 0.9,
            });
            m.bindPopup(popupHtml);
            markers.push(m);
        }
        this.cluster.addLayers(markers);
        // Fit to all markers + polygons
        if (markers.length || allBounds.length) {
            let bounds = this.cluster.getBounds();
            for (const b of allBounds) {
                bounds = bounds.isValid() ? bounds.extend(b) : b;
            }
            if (bounds.isValid()) this.map.fitBounds(bounds, { padding: [40, 40], maxZoom: 12 });
        }
        setTimeout(() => this.map && this.map.invalidateSize(), 100);
    }

    _draw(refEl, type, data, options) {
        if (typeof Chart === "undefined" || !refEl) return;
        const key = refEl.getAttribute("data-key") || refEl.id;
        if (this.charts[key]) this.charts[key].destroy();
        this.charts[key] = new Chart(refEl, { type, data, options });
    }

    _renderProjStates() {
        const el = this.donutProjRef.el; if (!el) return;
        el.setAttribute("data-key", "donutProj");
        const d = this.state.data.proj_states;
        this._draw(el, "doughnut", {
            labels: Object.keys(d).map(k => PROJ_LABELS[k] || k),
            datasets: [{ data: Object.values(d),
                backgroundColor: Object.keys(d).map(k => PROJ_COLORS[k] || "#888"), borderWidth: 1 }],
        }, { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "bottom" } } });
    }

    _renderUnitStates() {
        const el = this.donutUnitRef.el; if (!el) return;
        el.setAttribute("data-key", "donutUnit");
        const d = this.state.data.unit_states;
        this._draw(el, "doughnut", {
            labels: Object.keys(d).map(k => UNIT_LABELS[k] || k),
            datasets: [{ data: Object.values(d),
                backgroundColor: Object.keys(d).map(k => UNIT_COLORS[k] || "#888"), borderWidth: 1 }],
        }, { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "bottom" } } });
    }

    _renderTopProjects() {
        const el = this.barProjectsRef.el; if (!el) return;
        el.setAttribute("data-key", "barProjects");
        const t = this.state.data.top_projects || [];
        this._draw(el, "bar", {
            labels: t.map(x => x[0]),
            datasets: [{ label: "Contracted Value", data: t.map(x => x[1]), backgroundColor: "#283593" }],
        }, { responsive: true, maintainAspectRatio: false, indexAxis: "y",
             plugins: { legend: { display: false } } });
    }

    _renderVelocity() {
        const el = this.lineVelocityRef.el; if (!el) return;
        el.setAttribute("data-key", "lineVelocity");
        const v = this.state.data.velocity;
        this._draw(el, "bar", {
            labels: v.labels,
            datasets: [
                { label: "Contracts Signed", data: v.count, type: "bar",
                  backgroundColor: "#0d6efd", yAxisID: "y" },
                { label: "Revenue", data: v.revenue, type: "line",
                  borderColor: "#28a745", yAxisID: "y1", tension: 0.3 },
            ],
        }, {
            responsive: true, maintainAspectRatio: false,
            scales: {
                y: { type: "linear", position: "left" },
                y1: { type: "linear", position: "right", grid: { drawOnChartArea: false } },
            },
        });
    }

    _teardown() {
        if (this.refreshTimer) clearInterval(this.refreshTimer);
        if (this.map) { this.map.remove(); this.map = null; }
        for (const k in this.charts) { try { this.charts[k].destroy(); } catch {} }
        this.charts = {};
    }

    openProject(id) { this.action.doAction({ type:"ir.actions.act_window", res_model:"realestate.project", res_id:id, views:[[false,"form"]] }); }
    openContract(id) { this.action.doAction({ type:"ir.actions.act_window", res_model:"realestate.sale.contract", res_id:id, views:[[false,"form"]] }); }
    openReservation(id) { this.action.doAction({ type:"ir.actions.act_window", res_model:"realestate.unit.reservation", res_id:id, views:[[false,"form"]] }); }
    openInstallment(id) { this.action.doAction({ type:"ir.actions.act_window", res_model:"realestate.sale.installment", res_id:id, views:[[false,"form"]] }); }

    formatMoney(n) {
        if (n === null || n === undefined) return "0";
        return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(n);
    }
}

registry.category("actions").add("realestate.developer_dashboard", DeveloperDashboard);
