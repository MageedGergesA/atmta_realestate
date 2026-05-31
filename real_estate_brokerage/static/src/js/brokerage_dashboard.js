/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, onMounted, onWillUnmount, useEffect, useRef, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

const LISTING_STATE_COLORS = {
    draft: "#6c757d", active: "#0d6efd", under_offer: "#fd7e14",
    sold: "#28a745", withdrawn: "#9e9e9e", expired: "#6c757d",
};
const LISTING_STATE_LABELS = {
    draft: "Draft", active: "Active", under_offer: "Under Offer",
    sold: "Sold", withdrawn: "Withdrawn", expired: "Expired",
};
const LEAD_STATE_LABELS = {
    new: "New", qualified: "Qualified", matched: "Matched",
    viewing_scheduled: "Viewing", offer: "Offer", converted: "Converted",
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

export class BrokerageDashboard extends Component {
    static template = "real_estate_brokerage.BrokerageDashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.mapRef = useRef("map");
        this.donutListingRef = useRef("donutListing");
        this.barFunnelRef = useRef("barFunnel");
        this.barDomRef = useRef("barDom");
        this.barAgentsRef = useRef("barAgents");
        this.lineVelocityRef = useRef("lineVelocity");

        this.state = useState({
            loading: true, data: null, lastUpdate: null,
            autoRefresh: true, refreshInterval: 30000,
        });
        this.map = null; this.cluster = null; this.charts = {};
        this.refreshTimer = null;

        onMounted(async () => {
            await this.load();
            if (this.state.autoRefresh) {
                this.refreshTimer = setInterval(() => this.refresh(), this.state.refreshInterval);
            }
        });

        useEffect(() => {
            if (this.state.data) Promise.resolve().then(() => this.renderAll());
        }, () => [this.state.data]);

        onWillUnmount(() => this._teardown());
    }

    async load() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call("realestate.brokerage.dashboard", "get_data", []);
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
        this._renderListingStates();
        this._renderFunnel();
        this._renderDom();
        this._renderAgents();
        this._renderVelocity();
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
            const color = LISTING_STATE_COLORS[p.listing_state] || "#888";
            const m = L.circleMarker([p.latitude, p.longitude], {
                radius: 8, color: "#fff", weight: 2, fillColor: color, fillOpacity: 0.9,
            });
            const name = (p.name || "").replace(/</g, "&lt;");
            m.bindPopup(`<div><div class="small text-muted">${p.property_code || ''}</div>
                <div><strong>${name}</strong></div>
                <div class="small">${p.city || ''}</div>
                <span class="badge" style="background:${color}">${LISTING_STATE_LABELS[p.listing_state]}</span></div>`);
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

    _renderListingStates() {
        const el = this.donutListingRef.el; if (!el) return;
        el.setAttribute("data-key", "donutListing");
        const d = this.state.data.listing_states;
        this._draw(el, "doughnut", {
            labels: Object.keys(d).map(k => LISTING_STATE_LABELS[k] || k),
            datasets: [{ data: Object.values(d),
                backgroundColor: Object.keys(d).map(k => LISTING_STATE_COLORS[k] || "#888"), borderWidth: 1 }],
        }, { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "bottom" } } });
    }

    _renderFunnel() {
        const el = this.barFunnelRef.el; if (!el) return;
        el.setAttribute("data-key", "barFunnel");
        const d = this.state.data.lead_funnel;
        this._draw(el, "bar", {
            labels: Object.keys(d).map(k => LEAD_STATE_LABELS[k] || k),
            datasets: [{ label: "Leads", data: Object.values(d), backgroundColor: "#6a1b9a" }],
        }, { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } });
    }

    _renderDom() {
        const el = this.barDomRef.el; if (!el) return;
        el.setAttribute("data-key", "barDom");
        const d = this.state.data.dom_buckets;
        this._draw(el, "bar", {
            labels: Object.keys(d),
            datasets: [{ label: "Active Listings",
                data: Object.values(d),
                backgroundColor: ["#28a745","#fbbf24","#f97316","#dc2626"] }],
        }, { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } });
    }

    _renderAgents() {
        const el = this.barAgentsRef.el; if (!el) return;
        el.setAttribute("data-key", "barAgents");
        const t = this.state.data.top_agents || [];
        this._draw(el, "bar", {
            labels: t.map(x => x[0]),
            datasets: [{ label: "Commission", data: t.map(x => x[1]), backgroundColor: "#1b5e20" }],
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
                { label: "Sales Count", data: v.count, type: "bar",
                  backgroundColor: "#0d6efd", yAxisID: "y" },
                { label: "Revenue", data: v.revenue, type: "line",
                  borderColor: "#28a745", yAxisID: "y1", tension: 0.3 },
            ],
        }, {
            responsive: true, maintainAspectRatio: false,
            scales: {
                y: { type: "linear", position: "left", title: { display: true, text: "Count" } },
                y1: { type: "linear", position: "right", title: { display: true, text: "Revenue" },
                      grid: { drawOnChartArea: false } },
            },
        });
    }

    _teardown() {
        if (this.refreshTimer) clearInterval(this.refreshTimer);
        if (this.map) { this.map.remove(); this.map = null; }
        for (const k in this.charts) { try { this.charts[k].destroy(); } catch {} }
        this.charts = {};
    }

    openListing(id) { this.action.doAction({ type: "ir.actions.act_window", res_model: "realestate.listing", res_id: id, views: [[false,"form"]] }); }
    openLead(id) { this.action.doAction({ type: "ir.actions.act_window", res_model: "realestate.lead", res_id: id, views: [[false,"form"]] }); }
    openViewing(id) { this.action.doAction({ type: "ir.actions.act_window", res_model: "realestate.viewing", res_id: id, views: [[false,"form"]] }); }
    openOffer(id) { this.action.doAction({ type: "ir.actions.act_window", res_model: "realestate.offer", res_id: id, views: [[false,"form"]] }); }

    formatMoney(n) {
        if (n === null || n === undefined) return "0";
        return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(n);
    }
}

registry.category("actions").add("realestate.brokerage_dashboard", BrokerageDashboard);
