/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, onMounted, onWillUnmount, useEffect, useRef, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

const STATE_COLORS = {
    available: "#28a745",
    reserved: "#fd7e14",
    rented: "#0d6efd",
    maintenance: "#ffc107",
    inactive: "#6c757d",
};

const CONTRACT_COLORS = {
    draft: "#6c757d",
    confirmed: "#fd7e14",
    invoiced: "#9c27b0",
    active: "#0d6efd",
    expired: "#9e9e9e",
    terminated: "#dc3545",
};

const STATE_LABELS = {
    available: "Available", reserved: "Reserved", rented: "Rented",
    maintenance: "Maintenance", inactive: "Inactive",
};
const CONTRACT_LABELS = {
    draft: "Draft", confirmed: "Confirmed", invoiced: "Invoiced",
    active: "Active", expired: "Expired", terminated: "Terminated",
};

export class RentalDashboard extends Component {
    static template = "atmta_real_estate.RentalDashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.mapRef = useRef("map");
        this.donutPropRef = useRef("donutProp");
        this.donutContractRef = useRef("donutContract");
        this.lineRevenueRef = useRef("lineRevenue");
        this.barTenantsRef = useRef("barTenants");
        this.barCitiesRef = useRef("barCities");

        this.state = useState({
            loading: true,
            data: null,
            lastUpdate: null,
            autoRefresh: true,         // enabled by default
            refreshInterval: 30000,    // ms
        });

        this.map = null;
        this.cluster = null;
        this.charts = {};
        this.refreshTimer = null;

        onMounted(async () => {
            await this.load();
            if (this.state.autoRefresh) {
                this.refreshTimer = setInterval(() => this.refresh(), this.state.refreshInterval);
            }
        });

        // Re-render charts/map whenever data updates AND the DOM has caught up
        // (state.data flips from null → object on first load; flips again on every refresh).
        useEffect(
            () => {
                if (this.state.data) {
                    // Defer one tick so refs to canvases inside <t t-if="state.data"> exist.
                    Promise.resolve().then(() => this.renderAll());
                }
            },
            () => [this.state.data]
        );

        onWillUnmount(() => this._teardown());
    }

    async load() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call(
                "realestate.rental.dashboard", "get_data", []
            );
            this.state.lastUpdate = new Date().toLocaleTimeString();
        } finally {
            this.state.loading = false;
        }
    }

    renderAll() {
        if (!this.state.data) return;
        // Map disabled — re-enable in template (t-if="true") and uncomment below.
        // this._renderMap();
        this._renderDonutPropertyStates();
        this._renderDonutContractStates();
        this._renderRevenueLine();
        this._renderTopTenants();
        this._renderCities();
    }

    async refresh() {
        await this.load();
        this.renderAll();
    }

    toggleAutoRefresh() {
        this.state.autoRefresh = !this.state.autoRefresh;
        if (this.state.autoRefresh) {
            this.refreshTimer = setInterval(() => this.refresh(), 30000);
        } else {
            clearInterval(this.refreshTimer);
            this.refreshTimer = null;
        }
    }

    _patchLeafletDefaults() {
        if (this._leafletPatched || typeof L === "undefined") return;
        const base = "/atmta_real_estate/static/src/lib/leaflet/images";
        delete L.Icon.Default.prototype._getIconUrl;
        L.Icon.Default.mergeOptions({
            iconRetinaUrl: `${base}/marker-icon-2x.png`,
            iconUrl: `${base}/marker-icon.png`,
            shadowUrl: `${base}/marker-shadow.png`,
        });
        this._leafletPatched = true;
    }

    _renderMap() {
        if (typeof L === "undefined" || !this.mapRef.el) return;
        this._patchLeafletDefaults();
        if (!this.map) {
            this.map = L.map(this.mapRef.el, {
                center: [24.7136, 46.6753], zoom: 5,
                scrollWheelZoom: true, preferCanvas: true,
            });
            L.tileLayer(
                "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
                {
                    attribution: '&copy; <a href="https://carto.com/attributions">CARTO</a>',
                    maxZoom: 19, subdomains: "abcd",
                }
            ).addTo(this.map);
            this.cluster = L.markerClusterGroup({
                chunkedLoading: true, maxClusterRadius: 50,
            });
            this.map.addLayer(this.cluster);
        }
        this.cluster.clearLayers();
        const props = this.state.data.map_props || [];
        const markers = [];
        for (const p of props) {
            const color = STATE_COLORS[p.state] || "#888";
            const m = L.circleMarker([p.latitude, p.longitude], {
                radius: 8, color: "#fff", weight: 2,
                fillColor: color, fillOpacity: 0.9,
            });
            const code = (p.property_code || "").replace(/</g, "&lt;");
            const name = (p.name || "").replace(/</g, "&lt;");
            const city = (p.city || "").replace(/</g, "&lt;");
            m.bindPopup(
                `<div><div class="small text-muted">${code}</div>
                <div><strong>${name}</strong></div>
                <div class="small">${city}</div>
                <div class="mt-1"><span class="badge" style="background:${color}">${p.state}</span></div></div>`
            );
            markers.push(m);
        }
        this.cluster.addLayers(markers);
        if (markers.length) {
            this.map.fitBounds(this.cluster.getBounds(), { padding: [40, 40], maxZoom: 14 });
        }
        setTimeout(() => this.map && this.map.invalidateSize(), 100);
    }

    _drawChart(refEl, type, data, options) {
        if (typeof Chart === "undefined" || !refEl) return;
        const key = refEl.id || refEl.getAttribute("data-key");
        if (this.charts[key]) {
            this.charts[key].destroy();
        }
        this.charts[key] = new Chart(refEl, { type, data, options });
    }

    _renderDonutPropertyStates() {
        const el = this.donutPropRef.el; if (!el) return;
        el.setAttribute("data-key", "donutProp");
        const data = this.state.data.property_states;
        this._drawChart(el, "doughnut", {
            labels: Object.keys(data).map(k => STATE_LABELS[k] || k),
            datasets: [{
                data: Object.values(data),
                backgroundColor: Object.keys(data).map(k => STATE_COLORS[k] || "#888"),
                borderWidth: 1,
            }],
        }, { responsive: true, maintainAspectRatio: false,
             plugins: { legend: { position: "bottom" } } });
    }

    _renderDonutContractStates() {
        const el = this.donutContractRef.el; if (!el) return;
        el.setAttribute("data-key", "donutContract");
        const data = this.state.data.contract_states;
        this._drawChart(el, "doughnut", {
            labels: Object.keys(data).map(k => CONTRACT_LABELS[k] || k),
            datasets: [{
                data: Object.values(data),
                backgroundColor: Object.keys(data).map(k => CONTRACT_COLORS[k] || "#888"),
                borderWidth: 1,
            }],
        }, { responsive: true, maintainAspectRatio: false,
             plugins: { legend: { position: "bottom" } } });
    }

    _renderRevenueLine() {
        const el = this.lineRevenueRef.el; if (!el) return;
        el.setAttribute("data-key", "lineRevenue");
        const t = this.state.data.revenue_trend;
        this._drawChart(el, "bar", {
            labels: t.labels,
            datasets: [
                { label: "Expected", data: t.expected, backgroundColor: "#e3f2fd", borderColor: "#1976d2", borderWidth: 1 },
                { label: "Collected", data: t.collected, backgroundColor: "#0d6efd" },
            ],
        }, { responsive: true, maintainAspectRatio: false,
             plugins: { legend: { position: "bottom" } } });
    }

    _renderTopTenants() {
        const el = this.barTenantsRef.el; if (!el) return;
        el.setAttribute("data-key", "barTenants");
        const t = this.state.data.top_tenants || [];
        this._drawChart(el, "bar", {
            labels: t.map(x => x[0]),
            datasets: [{ label: "Total Paid", data: t.map(x => x[1]), backgroundColor: "#1b5e20" }],
        }, { responsive: true, maintainAspectRatio: false,
             indexAxis: "y", plugins: { legend: { display: false } } });
    }

    _renderCities() {
        const el = this.barCitiesRef.el; if (!el) return;
        el.setAttribute("data-key", "barCities");
        const c = this.state.data.properties_by_city || [];
        this._drawChart(el, "bar", {
            labels: c.map(x => x[0]),
            datasets: [{ label: "Properties", data: c.map(x => x[1]), backgroundColor: "#6a1b9a" }],
        }, { responsive: true, maintainAspectRatio: false,
             plugins: { legend: { display: false } } });
    }

    _teardown() {
        if (this.refreshTimer) clearInterval(this.refreshTimer);
        if (this.map) { this.map.remove(); this.map = null; }
        for (const k in this.charts) { try { this.charts[k].destroy(); } catch (e) {} }
        this.charts = {};
    }

    openProperty(id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "realestate.property",
            res_id: id, views: [[false, "form"]],
        });
    }

    openContract(id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "realestate.contract",
            res_id: id, views: [[false, "form"]],
        });
    }

    openMaintenance(id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "realestate.maintenance.request",
            res_id: id, views: [[false, "form"]],
        });
    }

    openHierarchy() {
        this.action.doAction("atmta_real_estate.action_property_view");
    }

    formatMoney(n) {
        if (n === null || n === undefined) return "0";
        return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(n);
    }
}

registry.category("actions").add("realestate.rental_dashboard", RentalDashboard);
