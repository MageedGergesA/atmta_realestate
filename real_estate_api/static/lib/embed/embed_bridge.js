/* eslint-disable */
/*
 * Embed bridge — postMessage protocol between the iframe and its host.
 *
 * Exposed as window.ReEmbed.* so the per-kind boot scripts can use it
 * without an import (these files load as a plain bundle, not as Odoo
 * modules).
 *
 *  Outbound  iframe → host           Inbound  host → iframe
 *  ─────────────────────────         ──────────────────────────
 *  ready    { height, level, … }     setTheme  { primary, mode, lang, hide }
 *  resize   { height }               navigate  { to: "property:N" | "back" | "home" }
 *  navigated{ level, id, name, … }   highlight { regionId }
 *  unitSelected   { id, name, … }    ping
 *  interestRequested { … }
 *  pong
 *  error    { code, message }
 *
 * Strict origin check: messages from origins NOT in
 * config.allowed_origins are silently dropped (and a "blocked" diagnostic
 * is recorded in console.debug). No silent best-guess.
 */

(function () {
    'use strict';
    if (window.ReEmbed) return;

    // --- Config + theme bootstrap ------------------------------------------
    function readJsonScript(id, fallback) {
        const el = document.getElementById(id);
        if (!el || !el.textContent) return fallback;
        try { return JSON.parse(el.textContent) || fallback; }
        catch (_e) { return fallback; }
    }
    const config = readJsonScript('embed-bridge-config', {
        version: 'v1', kind: '', resource_model: '', resource_id: 0,
        api_base: '/api/v1', db: '', allowed_origins: [],
    });
    const initialTheme = readJsonScript('embed-theme', {});

    function applyTheme(theme) {
        if (!theme || typeof theme !== 'object') return;
        const root = document.documentElement;
        if (theme.primary && typeof theme.primary === 'string') {
            root.style.setProperty('--re-primary', theme.primary);
        }
        if (theme.background) root.style.setProperty('--re-bg', theme.background);
        if (theme.foreground) root.style.setProperty('--re-fg', theme.foreground);
        if (theme.mode === 'dark' || theme.mode === 'light') {
            root.setAttribute('data-mode', theme.mode);
        }
        if (Array.isArray(theme.hide)) {
            for (const h of theme.hide) {
                if (typeof h === 'string') root.setAttribute(`data-hide-${h}`, '1');
            }
        }
    }
    applyTheme(initialTheme);

    // --- Origin allow-list -------------------------------------------------
    const allowed = new Set((config.allowed_origins || []).filter(Boolean));
    const allowAny = allowed.has('*');
    function isAllowedOrigin(origin) {
        return allowAny || allowed.has(origin);
    }

    // --- Message router ----------------------------------------------------
    const inboundHandlers = {
        setTheme(payload) { applyTheme(payload); },
        ping() { emit('pong', {}); },
        // navigate + highlight are forwarded to the per-kind viewer below
    };

    function emit(type, payload) {
        const msg = { source: 're-embed', version: 'v1', type, payload: payload || {} };
        try { window.parent.postMessage(msg, '*'); }
        catch (_e) { /* host may be sandboxed; nothing to do */ }
    }

    window.addEventListener('message', (event) => {
        if (!isAllowedOrigin(event.origin)) {
            console.debug('[re-embed] blocked message from', event.origin);
            return;
        }
        const data = event.data;
        if (!data || typeof data !== 'object' || data.source !== 're-embed-host') return;
        const handler = inboundHandlers[data.type];
        if (handler) {
            try { handler(data.payload || {}); }
            catch (e) { emit('error', { code: 'handler', message: String(e) }); }
        } else {
            // Forward unknown types to the per-kind viewer (if registered)
            if (window.ReEmbed._forwardInbound) {
                window.ReEmbed._forwardInbound(data.type, data.payload || {});
            }
        }
    });

    // --- Auto-resize -------------------------------------------------------
    function observeHeight() {
        const send = () => {
            const h = Math.max(
                document.documentElement.scrollHeight,
                document.body ? document.body.scrollHeight : 0,
                window.innerHeight || 0,
            );
            emit('resize', { height: h });
        };
        const ro = new ResizeObserver(() => send());
        if (document.body) ro.observe(document.body);
        window.addEventListener('load', send);
    }

    // --- API fetch wrapper -------------------------------------------------
    // We send no cookies (multi-DB embeds: a third-party origin's cookie
    // jar wouldn't carry one anyway). The db routed for this request is
    // baked into the bridge config by the embed shell — append it to
    // every fetch so the API router picks the right database. Image URLs
    // returned in JSON bodies (`/api/v1/image/...`) get the same
    // treatment so <img> tags pointing at them also resolve.
    function _withDb(relPath) {
        if (!config.db) return `${config.api_base}${relPath}`;
        const sep = relPath.includes('?') ? '&' : '?';
        return `${config.api_base}${relPath}${sep}db=${encodeURIComponent(config.db)}`;
    }
    function _absWithDb(absPath) {
        if (!config.db || typeof absPath !== 'string') return absPath;
        if (!absPath.startsWith('/api/v1/')) return absPath;
        const sep = absPath.includes('?') ? '&' : '?';
        return `${absPath}${sep}db=${encodeURIComponent(config.db)}`;
    }
    function _rewriteUrls(node) {
        if (!node || typeof node !== 'object') return;
        if (Array.isArray(node)) { node.forEach(_rewriteUrls); return; }
        for (const k in node) {
            const v = node[k];
            if (typeof v === 'string' && v.startsWith('/api/v1/')) {
                node[k] = _absWithDb(v);
            } else if (v && typeof v === 'object') {
                _rewriteUrls(v);
            }
        }
    }
    async function apiGet(path) {
        const res = await fetch(_withDb(path), {
            credentials: 'omit',
            headers: { 'Accept': 'application/json' },
        });
        if (!res.ok) {
            const text = await res.text().catch(() => '');
            let err = { code: `http_${res.status}`, message: res.statusText };
            try { const parsed = JSON.parse(text); if (parsed && parsed.error) err = parsed.error; }
            catch (_e) { /* keep default */ }
            const e = new Error(err.message || res.statusText);
            e.code = err.code; e.status = res.status;
            throw e;
        }
        const data = await res.json();
        _rewriteUrls(data);
        return data;
    }

    window.ReEmbed = {
        config,
        emit,
        apiGet,
        applyTheme,
        observeHeight,
        registerInbound(type, fn) { inboundHandlers[type] = fn; },
        // The per-kind viewer can plug a catch-all handler for navigate/highlight.
        _forwardInbound: null,
        onInbound(fn) { window.ReEmbed._forwardInbound = fn; },
    };
})();
