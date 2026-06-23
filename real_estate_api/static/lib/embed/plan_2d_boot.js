/* eslint-disable */
/*
 * 2D drill plan viewer.
 *
 * Two callers share this file:
 *
 *   1. The iframe embed at /embed/v1/plan-2d/<token>. The auto-boot
 *      tail at the bottom of the file reads the bridge-config <script>
 *      tag, mounts on #embed-root, and wires drill events to the
 *      postMessage bridge in ``embed_bridge.js``.
 *
 *   2. The portal page at /projects/<id>. ``portal_drill_2d.js`` calls
 *      ``window.RealEstateDrillViewer.mount(container, opts)``
 *      directly with the project id; drill events become normal
 *      callbacks invoked on the host page (no iframe boundary).
 *
 * Renders the JSON tree from /api/v1/projects/<id>/plan-2d (or
 * /api/v1/properties/<id>/plan-2d for deeper levels) as an image with
 * an SVG overlay of polygon regions. Clicking a region either drills
 * down (target_has_plan) or fires ``onUnitSelected`` (leaf).
 *
 * No silent fallback: an empty tree, missing image, or unknown target
 * kind raises a visible error state instead of "drilling into the
 * nearest sibling".
 */

(function () {
    'use strict';

    if (window.RealEstateDrillViewer) return;

    function escapeHtml(s) {
        return String(s == null ? '' : s).replace(
            /[&<>"']/g,
            (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;',
                     '"': '&quot;', "'": '&#39;' }[c]),
        );
    }

    /**
     * Default fetcher: hits the public JSON API.
     * Can be overridden via opts.apiGet (the embed uses ReEmbed's
     * own fetcher so it can prepend the api_base baked into the token).
     */
    async function defaultApiGet(path) {
        const res = await fetch(path, {
            credentials: 'omit',
            headers: { 'Accept': 'application/json' },
        });
        if (!res.ok) {
            let err = { code: `http_${res.status}`, message: res.statusText };
            try {
                const parsed = await res.json();
                if (parsed && parsed.error) err = parsed.error;
            } catch (_e) { /* keep default */ }
            const e = new Error(err.message || res.statusText);
            e.code = err.code; e.status = res.status;
            throw e;
        }
        return res.json();
    }

    /**
     * Mount a drill viewer inside ``container``.
     *
     * opts:
     *   apiBase           — string, prefix for relative paths (default '/api/v1')
     *   apiGet            — optional, function(path) → Promise<json> override
     *   projectId         — entry-point project id (one of projectId/propertyId required)
     *   propertyId        — or entry-point property id (e.g. drill straight into a building)
     *   initialEndpoint   — string, takes precedence over projectId/propertyId
     *   onReady           — called once after the first render
     *   onNavigated       — called every time the visible level changes
     *   onUnitSelected    — called when the user clicks a leaf region
     *   onError           — called when rendering fails
     *
     * Returns a small API: { navigate({to}), highlight(regionId), destroy() }
     */
    function mount(container, opts) {
        if (!container) throw new Error('RealEstateDrillViewer.mount: container required');

        const apiBase = (opts && opts.apiBase) != null ? opts.apiBase : '/api/v1';
        const apiGet = (opts && opts.apiGet) || ((path) => defaultApiGet(apiBase + path));
        const cb = {
            ready:        (opts && opts.onReady)        || null,
            navigated:    (opts && opts.onNavigated)    || null,
            unitSelected: (opts && opts.onUnitSelected) || null,
            error:        (opts && opts.onError)        || null,
        };

        container.classList.add('o_re_drill_2d');
        container.innerHTML = `
            <div class="re-stage">
                <div class="re-breadcrumbs" data-role="crumbs"></div>
                <div class="re-body">
                    <div class="re-canvas" data-role="canvas">
                        <div class="re-loading">Loading…</div>
                    </div>
                    <aside class="re-side" data-role="side" hidden=""></aside>
                </div>
                <div class="re-lightbox" data-role="lightbox" hidden="">
                    <button type="button" class="re-lightbox-close" data-role="lightbox-close" aria-label="Close">×</button>
                    <img class="re-lightbox-img" data-role="lightbox-img" alt=""/>
                </div>
            </div>
        `;
        const $crumbs = container.querySelector('[data-role=crumbs]');
        const $canvas = container.querySelector('[data-role=canvas]');
        const $side = container.querySelector('[data-role=side]');
        const $lightbox = container.querySelector('[data-role=lightbox]');
        const $lightboxImg = container.querySelector('[data-role=lightbox-img]');
        container.querySelector('[data-role=lightbox-close]')
            .addEventListener('click', () => closeLightbox());
        $lightbox.addEventListener('click', (e) => {
            if (e.target === $lightbox) closeLightbox();
        });
        function openLightbox(url) {
            $lightboxImg.src = url;
            $lightbox.removeAttribute('hidden');
        }
        function closeLightbox() {
            $lightbox.setAttribute('hidden', '');
            $lightboxImg.src = '';
        }

        const state = { stack: [], current: null };

        function showError(message) {
            $canvas.innerHTML = `<div class="re-error">${escapeHtml(message)}</div>`;
            if (cb.error) cb.error({ code: 'render', message });
        }

        function renderPicker(tree, kids) {
            $canvas.innerHTML = '';
            const grid = document.createElement('div');
            grid.className = 're-picker';
            const heading = document.createElement('div');
            heading.className = 're-picker-heading';
            heading.textContent = (tree.name || 'Project') + ' — pick a starting point';
            grid.appendChild(heading);
            const list = document.createElement('div');
            list.className = 're-picker-grid';
            for (const k of kids) {
                const card = document.createElement('button');
                card.type = 'button';
                card.className = 're-picker-card';
                card.title = k.hierarchy_level || '';
                card.addEventListener('click', () => {
                    loadTreeByEndpoint(`/properties/${k.id}/plan-2d`, true);
                });
                const thumb = document.createElement('div');
                thumb.className = 're-picker-thumb';
                if (k.thumb_url) {
                    const img = document.createElement('img');
                    img.src = k.thumb_url;
                    img.loading = 'lazy';
                    img.decoding = 'async';
                    img.alt = k.name || '';
                    thumb.appendChild(img);
                } else {
                    thumb.classList.add('re-picker-thumb-empty');
                }
                const label = document.createElement('div');
                label.className = 're-picker-label';
                label.textContent = k.name || '(unnamed)';
                const sub = document.createElement('div');
                sub.className = 're-picker-sub';
                sub.textContent = k.hierarchy_level || '';
                card.appendChild(thumb);
                card.appendChild(label);
                card.appendChild(sub);
                list.appendChild(card);
            }
            grid.appendChild(list);
            $canvas.appendChild(grid);
        }

        function renderCrumbs() {
            // The full chain comes from the CURRENT tree's
            // ``breadcrumbs`` field (the server walks parent_id +
            // prepends the project, so it's already absolute from the
            // root). Clicking a segment either jumps to an existing
            // stack frame for that level — when the user got there by
            // drilling — or fetches that level fresh when no frame
            // exists yet (deep-link case, e.g. ``?property=100`` with
            // the project pill clickable to load the picker).
            $crumbs.innerHTML = '';
            const tree = state.current;
            if (!tree) return;
            const chain = tree.breadcrumbs || [];

            // Build a map: stack frame index → its tail crumb id+kind,
            // so we know which crumbs are "drill points" vs "deeper".
            const frameByCrumb = new Map();
            for (let i = 0; i < state.stack.length; i++) {
                const fbc = state.stack[i].breadcrumbs || [];
                const tail = fbc[fbc.length - 1];
                if (tail) frameByCrumb.set(`${tail.kind}:${tail.id}`, i);
            }

            for (let i = 0; i < chain.length; i++) {
                const c = chain[i];
                if (i > 0) {
                    const sep = document.createElement('span');
                    sep.className = 're-sep'; sep.textContent = '›';
                    $crumbs.appendChild(sep);
                }
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.textContent = c.name || '(unnamed)';
                btn.title = c.hierarchy_level || c.kind || '';
                btn.className = 're-crumb';
                const isCurrent = (i === chain.length - 1);
                if (isCurrent) btn.classList.add('re-crumb-current');

                const key = `${c.kind}:${c.id}`;
                const frameIdx = frameByCrumb.get(key);
                btn.addEventListener('click', () => {
                    if (isCurrent) return;
                    if (frameIdx !== undefined) {
                        // Already in the stack — pop down to that frame.
                        jumpTo(frameIdx);
                        return;
                    }
                    // Ancestor not in the stack yet — fetch it fresh
                    // and replace the stack so further drills make sense.
                    const path = c.kind === 'project'
                        ? `/projects/${c.id}/plan-2d`
                        : `/properties/${c.id}/plan-2d`;
                    state.stack = [];
                    loadTreeByEndpoint(path, true);
                });
                $crumbs.appendChild(btn);
            }
        }

        function renderSide(tree) {
            const gallery = Array.isArray(tree.gallery) ? tree.gallery : [];
            $side.innerHTML = '';
            if (!gallery.length) {
                $side.setAttribute('hidden', '');
                return;
            }
            $side.removeAttribute('hidden');
            const title = document.createElement('div');
            title.className = 're-side-title';
            title.textContent = 'Images';
            $side.appendChild(title);
            const strip = document.createElement('div');
            strip.className = 're-side-strip';
            for (const item of gallery) {
                if (!item || !item.thumb_url) continue;
                const fig = document.createElement('button');
                fig.type = 'button';
                fig.className = 're-thumb';
                fig.title = item.name || '';
                fig.addEventListener('click', () => openLightbox(item.url || item.thumb_url));
                const img = document.createElement('img');
                img.src = item.thumb_url;
                img.loading = 'lazy';
                img.decoding = 'async';
                img.alt = item.name || '';
                fig.appendChild(img);
                strip.appendChild(fig);
            }
            $side.appendChild(strip);
        }

        function renderPlan(tree) {
            renderCrumbs();
            renderSide(tree);

            // Picker mode: no image at this level, but the level lists
            // drillable children. Render a card grid so the user can
            // choose where to start the drill.
            if (!tree.image_url) {
                const kids = Array.isArray(tree.drillable_children) ? tree.drillable_children : [];
                if (!kids.length) {
                    showError('No plan available at this level.');
                    return;
                }
                renderPicker(tree, kids);
                if (cb.navigated) cb.navigated({
                    level: tree.hierarchy_level || tree.root_kind,
                    id: tree.root_id,
                    name: tree.name,
                    breadcrumbs: tree.breadcrumbs,
                    region_count: 0,
                    picker: true,
                });
                return;
            }

            $canvas.innerHTML = '';
            // Wrap image + SVG in a box sized to the image's natural
            // aspect ratio. Without this wrapper, the SVG fills the
            // canvas (which is wider than the letterboxed image), and
            // polygon coordinates — which are percentages of the image —
            // get stretched across the full canvas width, extending
            // past the image edges into the checkerboard padding.
            const wrap = document.createElement('div');
            wrap.className = 're-plan-wrap';

            const img = document.createElement('img');
            img.className = 're-plan-image';
            img.alt = tree.name || '';
            img.loading = 'lazy';
            img.decoding = 'async';
            img.addEventListener('load', () => {
                if (img.naturalWidth && img.naturalHeight) {
                    wrap.style.aspectRatio =
                        `${img.naturalWidth} / ${img.naturalHeight}`;
                }
            });
            img.src = tree.image_url;
            wrap.appendChild(img);

            const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            svg.classList.add('re-region-svg');
            svg.setAttribute('viewBox', '0 0 100 100');
            svg.setAttribute('preserveAspectRatio', 'none');

            // Render EVERY region — the drill is about navigation, not
            // sale state. ``target_status`` only affects styling (so e.g.
            // 'sold' renders grey) and whether a leaf click can open a
            // unit-detail modal. Drilling into a building/floor always
            // works regardless of the descendants' listing state.
            const regions = Array.isArray(tree.regions) ? tree.regions : [];
            for (const r of regions) {
                let pts;
                try { pts = JSON.parse(r.polygon || '[]'); }
                catch (_e) { continue; }
                if (!Array.isArray(pts) || pts.length < 3) continue;

                const poly = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
                poly.classList.add('re-region');
                if (r.target_status) poly.dataset.status = r.target_status;
                if (r.id) poly.dataset.regionId = r.id;
                poly.setAttribute('points', pts.map((p) => `${p[0]},${p[1]}`).join(' '));
                poly.setAttribute('fill', r.color || '#3b82f6');
                poly.setAttribute('stroke', r.color || '#3b82f6');
                poly.addEventListener('click', () => onRegionClick(r));
                const titleEl = document.createElementNS('http://www.w3.org/2000/svg', 'title');
                titleEl.textContent = r.label || r.target_name || '';
                poly.appendChild(titleEl);
                svg.appendChild(poly);
            }
            wrap.appendChild(svg);
            $canvas.appendChild(wrap);

            if (cb.navigated) cb.navigated({
                level: tree.hierarchy_level || tree.root_kind,
                id: tree.root_id,
                name: tree.name,
                breadcrumbs: tree.breadcrumbs,
                region_count: regions.length,
            });
        }

        async function loadTreeByEndpoint(endpoint, push) {
            $canvas.innerHTML = `<div class="re-loading">Loading…</div>`;
            try {
                const tree = await apiGet(endpoint);
                if (!tree) {
                    showError('No plan available at this level.');
                    return;
                }
                // `renderPlan` itself decides between image + regions
                // and picker-grid (no image, with drillable children).
                // No early-exit on missing image — that's the picker case.
                if (push !== false) state.stack.push(tree);
                state.current = tree;
                renderPlan(tree);
            } catch (e) {
                showError(e.message || 'Failed to load plan.');
            }
        }

        function onRegionClick(region) {
            if (region.target_has_plan) {
                loadTreeByEndpoint(`/properties/${region.target_id}/plan-2d`, true);
                return;
            }
            // Leaf — enrich with unit detail then hand to the host.
            apiGet(`/units/${region.target_id}`).then(
                (unit) => cb.unitSelected && cb.unitSelected(unit),
                (e) => cb.unitSelected && cb.unitSelected({
                    id: region.target_id,
                    name: region.target_name,
                    _error: e.message,
                }),
            );
        }

        function jumpTo(index) {
            if (index < 0 || index >= state.stack.length) return;
            state.stack = state.stack.slice(0, index + 1);
            state.current = state.stack[state.stack.length - 1];
            renderPlan(state.current);
        }

        function goBack() {
            if (state.stack.length <= 1) {
                if (cb.navigated) cb.navigated({ level: 'root', id: null, name: null });
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

        function navigate(arg) {
            if (!arg) return;
            if (arg.to === 'back') return goBack();
            if (arg.to === 'home') return goHome();
            if (typeof arg.to === 'string' && arg.to.startsWith('property:')) {
                const id = parseInt(arg.to.split(':')[1], 10);
                if (id) return loadTreeByEndpoint(`/properties/${id}/plan-2d`, true);
            }
        }

        function highlight(regionId) {
            if (!regionId) return;
            const el = container.querySelector(`polygon[data-region-id="${regionId}"]`);
            if (el) {
                el.classList.add('re-flash');
                setTimeout(() => el.classList.remove('re-flash'), 800);
            }
        }

        function destroy() {
            container.innerHTML = '';
            container.classList.remove('o_re_drill_2d');
        }

        // --- Initial load -------------------------------------------------
        (async function boot() {
            const initialEndpoint = (
                opts.initialEndpoint
                || (opts.projectId ? `/projects/${opts.projectId}/plan-2d` : null)
                || (opts.propertyId ? `/properties/${opts.propertyId}/plan-2d` : null)
            );
            if (!initialEndpoint) {
                showError('Unsupported viewer configuration.');
                if (cb.ready) cb.ready();
                return;
            }
            await loadTreeByEndpoint(initialEndpoint, true);
            if (cb.ready) cb.ready();
        })();

        return { navigate, highlight, destroy };
    }

    window.RealEstateDrillViewer = { mount };

    // ─────────────────────────────────────────────────────────────────────
    // Auto-boot for the iframe embed page.
    //
    // ``embed_bridge.js`` sets ``window.ReEmbed`` if and only if we're
    // inside the chromeless embed shell. On any other page (e.g. the
    // portal) ``ReEmbed`` is absent and this block is a no-op — the
    // host calls ``mount()`` directly instead.
    // ─────────────────────────────────────────────────────────────────────
    const ROOT = document.getElementById('embed-root');
    if (!ROOT || !window.ReEmbed) return;

    const { config, emit, observeHeight, onInbound, apiGet } = window.ReEmbed;

    observeHeight();

    if (config.resource_model !== 'realestate.project'
        && config.resource_model !== 'realestate.property') {
        // Strict: don't try to fall back to a different resource.
        ROOT.innerHTML = '<div class="re-error">Unsupported embed configuration.</div>';
        emit('error', { code: 'render', message: 'Unsupported embed configuration.' });
        emit('ready', { height: window.innerHeight });
        return;
    }

    const handle = mount(ROOT, {
        // Use the embed bridge's apiGet so the api_base baked into the
        // token is honoured.
        apiGet:        (path) => apiGet(path),
        projectId:     config.resource_model === 'realestate.project'  ? config.resource_id : null,
        propertyId:    config.resource_model === 'realestate.property' ? config.resource_id : null,
        onNavigated:   (n) => emit('navigated', n),
        onUnitSelected:(u) => emit('unitSelected', u),
        onError:       (e) => emit('error', e),
        onReady:       () => emit('ready', { height: window.innerHeight }),
    });

    // Host → iframe inbound messages dispatch to viewer methods.
    onInbound((type, payload) => {
        if (type === 'navigate') handle.navigate(payload);
        else if (type === 'highlight') handle.highlight(payload && payload.regionId);
    });
})();
