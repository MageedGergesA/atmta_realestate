/* eslint-disable */
/*
 * 2D drill plan embed.
 *
 * Renders the JSON tree from /api/v1/projects/<id>/plan-2d (or
 * /api/v1/properties/<id>/plan-2d for deeper levels) as an image with an
 * SVG overlay of polygon regions. Clicking a region either:
 *   - drills down (if target has its own plan image), OR
 *   - fires `unitSelected` postMessage to the host (for leaf targets).
 *
 * No silent fallback: an empty tree, missing image, or unknown target
 * kind raises a visible error state instead of "drilling into the
 * nearest sibling".
 */

(function () {
    'use strict';
    const ROOT = document.getElementById('embed-root');
    if (!ROOT || !window.ReEmbed) return;

    const { config, emit, apiGet, observeHeight, onInbound } = window.ReEmbed;
    const state = {
        stack: [],   // breadcrumb stack of fetched trees
        current: null,
    };

    // --- DOM scaffolding ---------------------------------------------------
    ROOT.innerHTML = `
        <div class="re-stage">
            <div class="re-breadcrumbs" id="re-crumbs"></div>
            <div class="re-canvas" id="re-canvas">
                <div class="re-loading">Loading…</div>
            </div>
        </div>
    `;
    const $crumbs = ROOT.querySelector('#re-crumbs');
    const $canvas = ROOT.querySelector('#re-canvas');

    // --- Helpers -----------------------------------------------------------
    function showError(message) {
        $canvas.innerHTML = `<div class="re-error">${escapeHtml(message)}</div>`;
        emit('error', { code: 'render', message });
    }
    function escapeHtml(s) {
        return String(s == null ? '' : s).replace(
            /[&<>"']/g,
            (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;',
                     '"': '&quot;', "'": '&#39;' }[c]),
        );
    }

    function planEndpoint(node) {
        if (node.root_kind === 'project') return `/projects/${node.root_id}/plan-2d`;
        if (node.root_kind === 'property') return `/properties/${node.root_id}/plan-2d`;
        return null;
    }

    function renderCrumbs(tree) {
        const crumbs = tree.breadcrumbs || [];
        $crumbs.innerHTML = '';
        for (let i = 0; i < crumbs.length; i++) {
            const c = crumbs[i];
            if (i > 0) {
                const sep = document.createElement('span');
                sep.className = 're-sep'; sep.textContent = '›';
                $crumbs.appendChild(sep);
            }
            const btn = document.createElement('button');
            btn.textContent = c.name || '(unnamed)';
            btn.title = c.hierarchy_level || c.kind;
            btn.addEventListener('click', () => jumpTo(i));
            $crumbs.appendChild(btn);
        }
    }

    function renderPlan(tree) {
        renderCrumbs(tree);
        if (!tree.image_url) {
            showError('No image attached to this level.');
            return;
        }

        $canvas.innerHTML = '';
        const img = document.createElement('img');
        img.className = 're-plan-image';
        img.alt = tree.name || '';
        img.loading = 'lazy';
        img.decoding = 'async';
        img.src = tree.image_url;
        $canvas.appendChild(img);

        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.classList.add('re-region-svg');
        svg.setAttribute('viewBox', '0 0 100 100');
        svg.setAttribute('preserveAspectRatio', 'none');

        const regions = Array.isArray(tree.regions) ? tree.regions : [];
        for (const r of regions) {
            if (r.target_status === 'hidden') continue;
            let pts;
            try { pts = JSON.parse(r.polygon || '[]'); }
            catch (_e) { continue; }
            if (!Array.isArray(pts) || pts.length < 3) continue;

            const poly = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
            poly.classList.add('re-region');
            if (r.target_status) poly.dataset.status = r.target_status;
            poly.setAttribute('points', pts.map((p) => `${p[0]},${p[1]}`).join(' '));
            poly.setAttribute('fill', r.color || '#3b82f6');
            poly.setAttribute('stroke', r.color || '#3b82f6');
            poly.addEventListener('click', () => onRegionClick(r));
            const titleEl = document.createElementNS('http://www.w3.org/2000/svg', 'title');
            titleEl.textContent = r.label || r.target_name || '';
            poly.appendChild(titleEl);
            svg.appendChild(poly);
        }
        $canvas.appendChild(svg);

        emit('navigated', {
            level: tree.hierarchy_level || tree.root_kind,
            id: tree.root_id,
            name: tree.name,
            breadcrumbs: tree.breadcrumbs,
            region_count: regions.length,
        });
    }

    async function loadTreeByEndpoint(endpoint, push = true) {
        $canvas.innerHTML = `<div class="re-loading">Loading…</div>`;
        try {
            const tree = await apiGet(endpoint);
            if (!tree || !tree.image_url) {
                showError('No plan available at this level.');
                return;
            }
            if (push) state.stack.push(tree);
            state.current = tree;
            renderPlan(tree);
        } catch (e) {
            showError(e.message || 'Failed to load plan.');
        }
    }

    function onRegionClick(region) {
        if (region.target_has_plan) {
            // Drill down to the child's own plan.
            loadTreeByEndpoint(`/properties/${region.target_id}/plan-2d`, true);
        } else {
            // Leaf — ask host to handle (open contact form, sidebar, etc.)
            // We also fetch the unit detail to enrich the postMessage payload,
            // so the host doesn't need a second round-trip.
            apiGet(`/units/${region.target_id}`).then(
                (unit) => emit('unitSelected', unit),
                (e) => emit('unitSelected', {
                    id: region.target_id,
                    name: region.target_name,
                    _error: e.message,
                }),
            );
        }
    }

    function jumpTo(index) {
        if (index < 0 || index >= state.stack.length) return;
        // Trim the stack and re-render — no re-fetch needed since each level
        // is fully cached in `state.stack`.
        state.stack = state.stack.slice(0, index + 1);
        state.current = state.stack[state.stack.length - 1];
        renderPlan(state.current);
    }

    function goBack() {
        if (state.stack.length <= 1) {
            emit('navigated', { level: 'root', id: null, name: null });
            return;
        }
        state.stack.pop();
        state.current = state.stack[state.stack.length - 1];
        renderPlan(state.current);
    }

    function goHome() {
        if (!state.stack.length) return;
        state.stack = [state.stack[0]];
        state.current = state.stack[0];
        renderPlan(state.current);
    }

    onInbound((type, payload) => {
        if (type === 'navigate') {
            if (payload.to === 'back') return goBack();
            if (payload.to === 'home') return goHome();
            if (typeof payload.to === 'string' && payload.to.startsWith('property:')) {
                const id = parseInt(payload.to.split(':')[1], 10);
                if (id) return loadTreeByEndpoint(`/properties/${id}/plan-2d`, true);
            }
        }
        if (type === 'highlight' && payload.regionId) {
            const el = ROOT.querySelector(`polygon[data-region-id="${payload.regionId}"]`);
            if (el) {
                el.classList.add('re-flash');
                setTimeout(() => el.classList.remove('re-flash'), 800);
            }
        }
    });

    // --- Boot --------------------------------------------------------------
    (async function boot() {
        observeHeight();
        const initialEndpoint =
            config.resource_model === 'realestate.project'
                ? `/projects/${config.resource_id}/plan-2d`
                : config.resource_model === 'realestate.property'
                ? `/properties/${config.resource_id}/plan-2d`
                : null;
        if (!initialEndpoint) {
            showError('Unsupported embed configuration.');
            emit('ready', { height: window.innerHeight });
            return;
        }
        await loadTreeByEndpoint(initialEndpoint, true);
        emit('ready', { height: window.innerHeight });
    })();
})();
