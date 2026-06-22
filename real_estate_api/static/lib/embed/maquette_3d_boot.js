/* eslint-disable */
/*
 * 3D maquette embed.
 *
 * Fetches /api/v1/projects/<id>/maquette-3d, lazy-loads three.js from
 * the maquette module's static bundle (we depend on it in the manifest)
 * and renders a basic GLB scene with orbit controls + per-unit clicks.
 *
 * Clicking a unit mesh: fetch /api/v1/units/<id> and fire
 * `unitSelected` postMessage. No silent fallback: meshes that don't
 * map to a known unit do nothing.
 */

(function () {
    'use strict';
    const ROOT = document.getElementById('embed-root');
    if (!ROOT || !window.ReEmbed) return;

    const { config, emit, apiGet, observeHeight, onInbound } = window.ReEmbed;

    // Path to the three.js bundle vendored under real_estate_maquette.
    // We depend on that module in __manifest__.py, so this URL is always
    // available wherever this embed loads.
    const THREEJS_LIB = '/real_estate_maquette/static/src/lib/threejs';

    let THREE = null, GLTFLoader = null, OrbitControls = null;
    let scene, camera, renderer, controls, raycaster, mouse;
    let meshByUnitId = new Map();    // unit_id -> THREE.Mesh
    let unitsByMesh = new Map();     // mesh.uuid -> unit dict
    let descriptor = null;

    // --- DOM scaffolding ---------------------------------------------------
    ROOT.innerHTML = `
        <div class="re-scene"></div>
        <div class="re-loading">Loading 3D model…</div>
    `;
    const $scene = ROOT.querySelector('.re-scene');
    const $loading = ROOT.querySelector('.re-loading');
    let $card = null;

    function showError(message) {
        $loading.remove();
        const e = document.createElement('div');
        e.className = 're-error';
        e.textContent = message;
        ROOT.appendChild(e);
        emit('error', { code: 'render', message });
    }

    async function loadThree() {
        const t = await import(`${THREEJS_LIB}/three.module.js`);
        const [{ GLTFLoader: G }, { OrbitControls: O }] = await Promise.all([
            import(`${THREEJS_LIB}/GLTFLoader.js`),
            import(`${THREEJS_LIB}/OrbitControls.js`),
        ]);
        return { THREE: t, GLTFLoader: G, OrbitControls: O };
    }

    function initScene() {
        scene = new THREE.Scene();
        scene.background = new THREE.Color(0xf3f4f6);

        const w = ROOT.clientWidth, h = ROOT.clientHeight;
        camera = new THREE.PerspectiveCamera(50, w / h, 0.1, 5000);
        camera.position.set(50, 40, 50);

        renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
        renderer.setPixelRatio(window.devicePixelRatio || 1);
        renderer.setSize(w, h, false);
        $scene.appendChild(renderer.domElement);

        const amb = new THREE.AmbientLight(0xffffff, 0.6);
        scene.add(amb);
        const dir = new THREE.DirectionalLight(0xffffff, 0.7);
        dir.position.set(30, 60, 20);
        scene.add(dir);

        controls = new OrbitControls(camera, renderer.domElement);
        controls.enableDamping = true;
        controls.dampingFactor = 0.08;

        raycaster = new THREE.Raycaster();
        mouse = new THREE.Vector2();

        // Optional: load default camera from descriptor.
        if (descriptor.default_camera) {
            try {
                const c = JSON.parse(descriptor.default_camera);
                if (Array.isArray(c.position)) camera.position.fromArray(c.position);
                if (Array.isArray(c.target)) controls.target.fromArray(c.target);
                controls.update();
            } catch (_e) { /* ignore */ }
        }

        renderer.domElement.addEventListener('click', onClickScene);
        window.addEventListener('resize', onResize);
        animate();
    }

    function onResize() {
        const w = ROOT.clientWidth, h = ROOT.clientHeight;
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        renderer.setSize(w, h, false);
    }

    function animate() {
        requestAnimationFrame(animate);
        if (controls) controls.update();
        if (renderer && scene && camera) renderer.render(scene, camera);
    }

    function buildMeshIndex(gltf, units) {
        // Map each mesh name → unit by exact match on maquette_mesh_name.
        // Units without a mesh name are skipped (no silent guess).
        const byMeshName = new Map();
        for (const u of units || []) {
            if (u.mesh_name) byMeshName.set(u.mesh_name, u);
        }
        gltf.scene.traverse((obj) => {
            if (!obj.isMesh) return;
            const unit = byMeshName.get(obj.name);
            if (!unit) return;
            meshByUnitId.set(unit.id, obj);
            unitsByMesh.set(obj.uuid, unit);
            if (unit.color_override) {
                try {
                    obj.material = obj.material.clone();
                    obj.material.color = new THREE.Color(unit.color_override);
                } catch (_e) { /* leave default */ }
            }
        });
    }

    function onClickScene(event) {
        const rect = renderer.domElement.getBoundingClientRect();
        mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
        mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
        raycaster.setFromCamera(mouse, camera);
        const hits = raycaster.intersectObjects(scene.children, true);
        for (const hit of hits) {
            const unit = unitsByMesh.get(hit.object.uuid);
            if (unit) {
                onUnitClicked(unit);
                return;
            }
        }
    }

    async function onUnitClicked(unit) {
        // Pull full detail for the host. If detail fetch fails we still
        // fire the event with what we have — that's not a fallback, the
        // failure is reported in `_error`.
        let detail = null, error = null;
        try { detail = await apiGet(`/units/${unit.id}`); }
        catch (e) { error = e.message; }
        renderUnitCard(detail || unit);
        emit('unitSelected', detail ? detail : {
            id: unit.id, name: unit.name, _error: error,
        });
    }

    function renderUnitCard(unit) {
        if ($card) $card.remove();
        $card = document.createElement('div');
        $card.className = 're-unit-card';
        $card.innerHTML = `
            <h3 class="re-unit-title"></h3>
            <div class="re-unit-row re-area"></div>
            <div class="re-unit-row re-price"></div>
            <div class="re-unit-row re-bedrooms"></div>
            <button class="re-cta">I'm interested</button>
        `;
        $card.querySelector('.re-unit-title').textContent = unit.name || '(unit)';
        const area = unit.area_sqm || 0;
        $card.querySelector('.re-area').textContent = area
            ? `${area.toLocaleString()} m²` : '';
        const price = unit.price || 0;
        $card.querySelector('.re-price').textContent = price
            ? `${unit.currency_symbol || ''} ${price.toLocaleString()}` : '';
        const beds = unit.bedrooms || 0;
        $card.querySelector('.re-bedrooms').textContent = beds
            ? `${beds} bedroom${beds > 1 ? 's' : ''}` : '';
        $card.querySelector('.re-cta').addEventListener('click', () => {
            emit('interestRequested', { id: unit.id, kind: 'unit' });
        });
        ROOT.appendChild($card);
    }

    function focusUnit(id) {
        const mesh = meshByUnitId.get(id);
        if (!mesh) return false;
        const box = new THREE.Box3().setFromObject(mesh);
        const center = box.getCenter(new THREE.Vector3());
        const size = box.getSize(new THREE.Vector3()).length() || 1;
        controls.target.copy(center);
        camera.position.copy(center.clone().add(
            new THREE.Vector3(size * 1.4, size * 1.0, size * 1.4),
        ));
        controls.update();
        return true;
    }

    onInbound((type, payload) => {
        if (type === 'navigate' && typeof payload.to === 'string'
            && payload.to.startsWith('property:')) {
            const id = parseInt(payload.to.split(':')[1], 10);
            if (id) focusUnit(id);
        }
    });

    // --- Boot --------------------------------------------------------------
    (async function boot() {
        observeHeight();
        if (config.resource_model !== 'realestate.project') {
            showError('3D embed is only supported on projects.');
            return;
        }
        try {
            descriptor = await apiGet(`/projects/${config.resource_id}/maquette-3d`);
        } catch (e) {
            showError(e.message || 'Failed to load maquette descriptor.');
            return;
        }
        if (!descriptor || !descriptor.glb_url) {
            showError('No 3D model available for this project.');
            return;
        }
        try {
            const libs = await loadThree();
            THREE = libs.THREE; GLTFLoader = libs.GLTFLoader; OrbitControls = libs.OrbitControls;
        } catch (e) {
            showError('Failed to load 3D engine: ' + (e.message || e));
            return;
        }
        initScene();
        const loader = new GLTFLoader();
        loader.load(
            descriptor.glb_url,
            (gltf) => {
                scene.add(gltf.scene);
                buildMeshIndex(gltf, descriptor.units);
                $loading.remove();
                emit('ready', { height: ROOT.clientHeight });
            },
            undefined,
            (err) => showError('Failed to load 3D model: ' + (err.message || err)),
        );
    })();
})();
