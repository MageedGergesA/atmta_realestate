/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, onMounted, onWillUnmount, useEffect, useRef, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

const HO_COLORS = {
    scheduled: "#0d6efd", inspection: "#fd7e14", snagging: "#ffc107",
    completed: "#28a745", cancelled: "#6c757d",
};
const HO_LABELS = {
    scheduled: "Scheduled", inspection: "Inspection", snagging: "Snagging",
    completed: "Completed", cancelled: "Cancelled",
};
const SEV_COLORS = { minor: "#0d6efd", major: "#fd7e14", critical: "#dc3545" };
const SEV_LABELS = { minor: "Minor", major: "Major", critical: "Critical" };
const SN_STATE_LABELS = {
    open: "Open", assigned: "Assigned", in_progress: "In Progress",
    resolved: "Resolved", verified: "Verified", rejected: "Rejected",
};
const SN_STATE_COLORS = {
    open: "#dc3545", assigned: "#fd7e14", in_progress: "#0d6efd",
    resolved: "#28a745", verified: "#1b5e20", rejected: "#6c757d",
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

export class HandoverDashboard extends Component {
    static template = "real_estate_handover.HandoverDashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.mapRef = useRef("map");
        this.donutHoRef = useRef("donutHo");
        this.donutSevRef = useRef("donutSev");
        this.donutSnStRef = useRef("donutSnSt");
        this.barReadinessRef = useRef("barReadiness");
        this.lineTrendRef = useRef("lineTrend");
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
            this.state.data = await this.orm.call("realestate.handover.dashboard", "get_data", []);
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
        this._renderHo();
        this._renderSeverity();
        this._renderSnSt();
        this._renderReadiness();
        this._renderTrend();
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
        const props = this.state.data.map_props || [];
        const markers = [];
        for (const p of props) {
            const r = p.readiness_overall || 0;
            const color = r >= 100 ? "#28a745" : r >= 75 ? "#0d6efd" : r >= 50 ? "#fd7e14" : "#dc3545";
            const m = L.circleMarker([p.latitude, p.longitude], {
                radius: 9, color: "#fff", weight: 2, fillColor: color, fillOpacity: 0.9,
            });
            const name = (p.name || "").replace(/</g, "&lt;");
            const status = p.ready_to_move ? "Ready to Move" : (p.ready_to_deliver ? "Ready to Deliver" : "In Progress");
            m.bindPopup(`<div><div class="small text-muted">${p.property_code || ''}</div>
                <div><strong>${name}</strong></div>
                <div class="small">${p.city || ''}</div>
                <div class="mt-1">Readiness: <strong>${Math.round(r)}%</strong></div>
                <div><span class="badge" style="background:${color}">${status}</span></div></div>`);
            markers.push(m);
        }
        this.cluster.addLayers(markers);
        if (markers.length) this.map.fitBounds(this.cluster.getBounds(), { padding: [40, 40], maxZoom: 14 });
        setTimeout(() => this.map && this.map.invalidateSize(), 100);
    }

    _draw(refEl, type, data, options) {
        if (typeof Chart === "undefined" || !refEl) return;
        const key = refEl.getAttribute("data-key") || refEl.id;
        if (this.charts[key]) this.charts[key].destroy();
        this.charts[key] = new Chart(refEl, { type, data, options });
    }

    _renderHo() {
        const el = this.donutHoRef.el; if (!el) return;
        el.setAttribute("data-key", "donutHo");
        const d = this.state.data.ho_states;
        this._draw(el, "doughnut", {
            labels: Object.keys(d).map(k => HO_LABELS[k] || k),
            datasets: [{ data: Object.values(d),
                backgroundColor: Object.keys(d).map(k => HO_COLORS[k] || "#888"), borderWidth: 1 }],
        }, { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "bottom" } } });
    }

    _renderSeverity() {
        const el = this.donutSevRef.el; if (!el) return;
        el.setAttribute("data-key", "donutSev");
        const d = this.state.data.sn_severity;
        this._draw(el, "doughnut", {
            labels: Object.keys(d).map(k => SEV_LABELS[k] || k),
            datasets: [{ data: Object.values(d),
                backgroundColor: Object.keys(d).map(k => SEV_COLORS[k] || "#888"), borderWidth: 1 }],
        }, { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "bottom" } } });
    }

    _renderSnSt() {
        const el = this.donutSnStRef.el; if (!el) return;
        el.setAttribute("data-key", "donutSnSt");
        const d = this.state.data.sn_states;
        this._draw(el, "doughnut", {
            labels: Object.keys(d).map(k => SN_STATE_LABELS[k] || k),
            datasets: [{ data: Object.values(d),
                backgroundColor: Object.keys(d).map(k => SN_STATE_COLORS[k] || "#888"), borderWidth: 1 }],
        }, { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "bottom" } } });
    }

    _renderReadiness() {
        const el = this.barReadinessRef.el; if (!el) return;
        el.setAttribute("data-key", "barReadiness");
        const d = this.state.data.readiness_buckets;
        this._draw(el, "bar", {
            labels: Object.keys(d),
            datasets: [{ label: "Properties", data: Object.values(d),
                backgroundColor: ["#dc3545","#fd7e14","#fbbf24","#0d6efd","#28a745"] }],
        }, { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } });
    }

    _renderTrend() {
        const el = this.lineTrendRef.el; if (!el) return;
        el.setAttribute("data-key", "lineTrend");
        const t = this.state.data.trend;
        this._draw(el, "line", {
            labels: t.labels,
            datasets: [
                { label: "Scheduled", data: t.scheduled, borderColor: "#0d6efd",
                  backgroundColor: "rgba(13,110,253,0.1)", tension: 0.3, fill: true },
                { label: "Completed", data: t.completed, borderColor: "#28a745",
                  backgroundColor: "rgba(40,167,69,0.1)", tension: 0.3, fill: true },
            ],
        }, { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "bottom" } } });
    }

    _teardown() {
        if (this.refreshTimer) clearInterval(this.refreshTimer);
        if (this.map) { this.map.remove(); this.map = null; }
        for (const k in this.charts) { try { this.charts[k].destroy(); } catch {} }
        this.charts = {};
    }

    openHandover(id) { this.action.doAction({ type:"ir.actions.act_window", res_model:"realestate.handover", res_id:id, views:[[false,"form"]] }); }
    openSnagging(id) { this.action.doAction({ type:"ir.actions.act_window", res_model:"realestate.snagging.issue", res_id:id, views:[[false,"form"]] }); }
    openWarranty(id) { this.action.doAction({ type:"ir.actions.act_window", res_model:"realestate.warranty", res_id:id, views:[[false,"form"]] }); }
}

registry.category("actions").add("realestate.handover_dashboard", HandoverDashboard);
