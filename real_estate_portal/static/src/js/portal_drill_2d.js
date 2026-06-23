/* eslint-disable */
/*
 * Portal-inline 2D drill viewer.
 *
 * Every ``.o_re_portal_2d_mount[data-project-id]`` element on the
 * current page is replaced by a fully-functional drill viewer (image
 * + polygon regions + breadcrumbs + leaf-click handler).
 *
 * The drill rendering itself is shared with the iframe embed: it lives
 * in ``real_estate_api/static/lib/embed/plan_2d_boot.js`` and exposes
 * ``window.RealEstateDrillViewer.mount(container, opts)``. We just
 * wire each mount point to that factory and translate leaf clicks
 * into the portal's existing ``re-portal-form`` window event — the
 * same event the OLD viewer used — so the EOI form below the viewer
 * still gets the property id pre-filled.
 *
 * Plain DOMContentLoaded handler, no OWL: the viewer is vanilla JS,
 * so this file inherits that simplicity. Portal pages don't need a
 * service registry round-trip just to mount it.
 */

(function () {
    'use strict';

    function dispatchPortalForm(propertyId, kind) {
        window.dispatchEvent(new CustomEvent('re-portal-form', {
            detail: { propertyId, kind: kind || 'eoi' },
        }));
    }

    /**
     * Wire the same ``re-portal-form`` window event the old viewer used.
     * Listening here means the EOI form keeps pre-filling property id +
     * scrolling into view even when the page has only a 2D mount (no 3D
     * — the OWL service in ``portal_viewers.js`` no longer covers this).
     */
    function wireEoiForm() {
        if (window.__re_portal_form_wired) return;
        window.__re_portal_form_wired = true;
        window.addEventListener('re-portal-form', (ev) => {
            const { propertyId, kind } = ev.detail || {};
            const form = document.querySelector('.o_re_eoi_form');
            if (!form) return;
            const hidden = form.querySelector("input[name='property_id']");
            if (hidden) hidden.value = propertyId || 0;
            if (kind === 'visit') {
                form.action = form.action.replace(/\/eoi$/, '/visit');
            } else {
                form.action = form.action.replace(/\/visit$/, '/eoi');
            }
            form.scrollIntoView({ behavior: 'smooth', block: 'center' });
            form.querySelector("input[name='name']")?.focus();
        });
    }

    function mountAll() {
        const targets = document.querySelectorAll('.o_re_portal_2d_mount[data-project-id]');
        if (!targets.length) return;

        if (!window.RealEstateDrillViewer) {
            console.warn(
                '[real_estate_portal] RealEstateDrillViewer missing — '
                + 'is the real_estate_api embed bundle loaded?',
            );
            return;
        }

        wireEoiForm();

        for (const el of targets) {
            const projectId = parseInt(el.dataset.projectId, 10);
            if (!projectId) continue;
            el.innerHTML = '';                            // wipe spinner
            window.RealEstateDrillViewer.mount(el, {
                apiBase: '/api/v1',
                projectId: projectId,
                onUnitSelected: (unit) => {
                    dispatchPortalForm(unit && unit.id, 'eoi');
                },
            });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', mountAll, { once: true });
    } else {
        mountAll();
    }
})();
