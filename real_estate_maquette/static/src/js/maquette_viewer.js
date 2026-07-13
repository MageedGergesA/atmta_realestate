/** @odoo-module **/

import { Component, onMounted, onWillUnmount, useRef, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";
import { CarouselDialog } from "./image_carousel_dialog";

/**
 * 3D Maquette Viewer — Three.js inside an OWL component.
 *
 * Loads Three.js (+ GLTFLoader, OrbitControls) from a CDN at first use to keep
 * the module installable without bundling ~600 KB of vendor code. For offline
 * environments, drop the same files into static/src/lib/threejs/ and update
 * THREEJS_URLS below.
 */

const THREEJS_VERSION = "0.160.0";
// Three.js + its addon loaders are vendored locally under static/src/lib/threejs/
// and loaded as native ES modules at runtime. We don't use a CDN: Three removed
// the legacy `examples/js/` globals, the `examples/jsm/` modules use bare
// `import ... from "three"` specifiers, esm.sh proved unreachable from some
// browsers, and an import map can't be injected after Odoo has started loading
// modules. Each vendored addon's `from "three"` was rewritten to point at the
// local three.module.js below, so only one THREE instance is ever created.
const THREEJS_LIB = "/real_estate_maquette/static/src/lib/threejs";
const THREEJS_URLS = {
    three: `${THREEJS_LIB}/three.module.js`,
    GLTFLoader: `${THREEJS_LIB}/GLTFLoader.js`,
    DRACOLoader: `${THREEJS_LIB}/DRACOLoader.js`,
    OrbitControls: `${THREEJS_LIB}/OrbitControls.js`,
    RGBELoader: `${THREEJS_LIB}/RGBELoader.js`,
};

// State → hex color (used both in 3D and in the side-panel badge).
const STATE_COLORS = {
    available: "#22c55e",   // green
    reserved: "#eab308",    // yellow
    sold: "#6b7280",        // gray
    rented: "#3b82f6",      // blue (for completeness)
    pending: "#f97316",     // orange
    default: "#94a3b8",     // slate (unmapped or unknown)
};

const STATE_LABELS = {
    available: "Available",
    reserved: "Reserved",
    sold: "Sold",
    rented: "Rented",
    pending: "Pending",
};

let _threeJsLoadPromise = null;

/** Load Three.js + addon loaders as ES modules. Returns the THREE namespace
 *  augmented with GLTFLoader / DRACOLoader / OrbitControls / RGBELoader so the
 *  rest of this component can keep using `new T.GLTFLoader()` etc.
 *
 *  Note: these are native dynamic import()s of runtime URLs — Odoo's JS
 *  transpiler only rewrites static `import ... from` statements, so it leaves
 *  these untouched and the browser resolves them at runtime. */
function loadThreeJs() {
    if (_threeJsLoadPromise) {
        return _threeJsLoadPromise;
    }
    _threeJsLoadPromise = (async () => {
        const THREE = await import(THREEJS_URLS.three);
        const [{ GLTFLoader }, { DRACOLoader }, { OrbitControls }, { RGBELoader }] =
            await Promise.all([
                import(THREEJS_URLS.GLTFLoader),
                import(THREEJS_URLS.DRACOLoader),
                import(THREEJS_URLS.OrbitControls),
                import(THREEJS_URLS.RGBELoader),
            ]);
        // Merge addons onto a copy of the namespace so existing `T.GLTFLoader`
        // style references keep working.
        return { ...THREE, GLTFLoader, DRACOLoader, OrbitControls, RGBELoader };
    })();
    return _threeJsLoadPromise;
}

export class MaquetteViewer extends Component {
    static template = "real_estate_maquette.MaquetteViewer";
    static props = {
        projectId: { type: Number },
        focusPropertyId: { type: Number, optional: true },
        focusMeshName: { type: String, optional: true },
        mode: { type: String, optional: true },  // "backend" (default) | "portal"
    };

    get portalMode() {
        return this.props.mode === "portal";
    }
    get glbUrl() {
        const base = this.portalMode
            ? `/projects/${this.props.projectId}/glb`
            : `/maquette/glb/${this.props.projectId}`;
        // Append the version token so a re-uploaded/deleted GLB yields a new URL
        // the browser can't serve from its cache of the previous model.
        return this._glbVersion ? `${base}?v=${this._glbVersion}` : base;
    }
    get unitsUrl() {
        return this.portalMode
            ? `/projects/${this.props.projectId}/units.json`
            : null;
    }

    setup() {
        this.canvasRef = useRef("canvas");
        this.rootRef = useRef("root");
        this.orm = useService("orm");
        try {
            this.action = useService("action");
        } catch (_e) {
            this.action = null;
        }
        this.notification = useService("notification");
        this.dialog = useService("dialog");

        this.state = useState({
            loading: true,
            loadingMsg: "Loading 3D viewer…",
            error: "",
            selectedUnit: null,
            hovered: null,
            stats: { total: 0, available: 0, reserved: 0, sold: 0, mapped: 0 },
        });

        // Mutable rendering state — kept out of the OWL reactive state to avoid
        // re-renders on every animation frame.
        this._three = null;     // THREE namespace (+ merged addon loaders)
        this._scene = null;
        this._camera = null;
        this._renderer = null;
        this._controls = null;
        this._raycaster = null;
        this._mouse = null;
        this._rootObject = null;
        this._unitMeshes = {};  // mesh_name → Mesh
        this._unitData = {};    // mesh_name → unit metadata from server
        this._highlighted = null;
        this._origMaterials = new Map();
        this._animationId = null;
        this._glbVersion = 0;   // cache-busting token from the units payload

        onMounted(() => this._init());
        onWillUnmount(() => this._teardown());
    }

    async _init() {
        try {
            this.state.loadingMsg = "Loading Three.js…";
            this._three = await loadThreeJs();

            this.state.loadingMsg = "Fetching units…";
            let units;
            // meta carries the optional backend extras (saved camera, HDR flag).
            // Portal mode has none of those — defaults are fine.
            let meta = { default_camera: false, has_hdr: false };
            if (this.portalMode) {
                const res = await fetch(this.unitsUrl);
                if (!res.ok) throw new Error("Could not fetch unit data.");
                units = await res.json();
                this._hasGlb = true;
            } else {
                meta = await rpc(`/maquette/units/${this.props.projectId}`, {});
                if (meta.error) {
                    throw new Error(meta.error);
                }
                units = meta.units;
                this._hasGlb = !!meta.has_glb;
                this._glbVersion = meta.glb_version || 0;
            }
            this._unitData = {};
            for (const u of units) {
                if (u.mesh_name) {
                    this._unitData[u.mesh_name] = u;
                }
                if (u.property_code) {
                    this._unitData[u.property_code] = this._unitData[u.property_code] || u;
                }
            }
            this._refreshStats(units);

            this.state.loadingMsg = "Initializing scene…";
            this._buildScene();

            this.state.loadingMsg = "Loading building model…";
            await this._loadGlb();

            if (meta.default_camera) {
                try {
                    this._applyCamera(JSON.parse(meta.default_camera));
                    this._needsInitialFit = false;   // respect the saved view
                } catch (e) { /* ignore bad json */ }
            }
            if (meta.has_hdr) {
                this._loadHdrInBackground();
            }
            if (this.props.focusMeshName) {
                this._focusOnMesh(this.props.focusMeshName);
            }

            this.state.loading = false;
            this._animate();
        } catch (err) {
            console.error("Maquette init failed:", err);
            this.state.loading = false;
            this.state.error = err.message || String(err);
        }
    }

    _refreshStats(units) {
        const stats = { total: units.length, available: 0, reserved: 0, sold: 0, mapped: 0 };
        for (const u of units) {
            if (u.mesh_name) stats.mapped++;
            if (u.state === "available") stats.available++;
            else if (u.state === "reserved") stats.reserved++;
            else if (u.state === "sold") stats.sold++;
        }
        this.state.stats = stats;
    }

    _buildScene() {
        const T = this._three;
        const canvas = this.canvasRef.el;
        const w = canvas.clientWidth || 800;
        const h = canvas.clientHeight || 600;

        const scene = new T.Scene();
        scene.background = new T.Color(0xf1f5f9);

        const camera = new T.PerspectiveCamera(45, w / h, 0.1, 5000);
        camera.position.set(60, 50, 80);

        const renderer = new T.WebGLRenderer({ canvas, antialias: true, alpha: true });
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        renderer.setSize(w, h, false);
        renderer.outputColorSpace = T.SRGBColorSpace;
        renderer.toneMapping = T.ACESFilmicToneMapping;
        renderer.toneMappingExposure = 1.0;

        // Lights — bright enough that GLBs without baked light still look fine
        const ambient = new T.AmbientLight(0xffffff, 0.6);
        scene.add(ambient);
        const dir1 = new T.DirectionalLight(0xffffff, 1.2);
        dir1.position.set(50, 80, 60);
        scene.add(dir1);
        const dir2 = new T.DirectionalLight(0xffffff, 0.4);
        dir2.position.set(-50, 30, -40);
        scene.add(dir2);

        const grid = new T.GridHelper(200, 40, 0xcbd5e1, 0xe2e8f0);
        scene.add(grid);

        const controls = new T.OrbitControls(camera, renderer.domElement);
        controls.enableDamping = true;
        controls.dampingFactor = 0.08;
        controls.maxPolarAngle = Math.PI / 2 - 0.05;

        this._scene = scene;
        this._camera = camera;
        this._renderer = renderer;
        this._controls = controls;
        this._raycaster = new T.Raycaster();
        this._mouse = new T.Vector2();

        // Resize handling. The maquette lives in a notebook tab, so the canvas
        // can be 0×0 at mount; we must react to *element* size changes (tab
        // shown, layout settling), not just window resizes — and frame the
        // model the first time we get a real size, or it loads cropped.
        this._needsInitialFit = true;
        this._onResize = () => {
            const w2 = canvas.clientWidth;
            const h2 = canvas.clientHeight;
            if (!w2 || !h2) return;            // not laid out yet
            camera.aspect = w2 / h2;
            camera.updateProjectionMatrix();
            renderer.setSize(w2, h2, false);
            if (this._needsInitialFit && this._rootObject) {
                this._fitCameraToObject(this._rootObject);
                this._needsInitialFit = false;
            }
        };
        window.addEventListener("resize", this._onResize);
        if (window.ResizeObserver) {
            this._resizeObserver = new ResizeObserver(() => this._onResize());
            this._resizeObserver.observe(canvas);
        }

        // Single click selects a *mapped* unit; double click anywhere opens the
        // picker. A dblclick also fires two click events, so debounce the single
        // click and cancel it when a dblclick follows — otherwise two pickers open.
        this._clickTimer = null;
        renderer.domElement.addEventListener("click", (ev) => {
            const coords = { clientX: ev.clientX, clientY: ev.clientY };
            if (this._clickTimer) clearTimeout(this._clickTimer);
            this._clickTimer = setTimeout(() => {
                this._clickTimer = null;
                this._onSingleClick(coords);
            }, 250);
        });
        renderer.domElement.addEventListener("dblclick", (ev) => {
            if (this._clickTimer) { clearTimeout(this._clickTimer); this._clickTimer = null; }
            this._onDblClick(ev);
        });
        renderer.domElement.addEventListener("mousemove", (ev) => this._onMouseMove(ev));
    }

    async _loadGlb(attempt = 0) {
        const T = this._three;
        const url = this.glbUrl;
        const loader = new T.GLTFLoader();
        // Optional Draco decoder for compressed GLBs
        try {
            const draco = new T.DRACOLoader();
            draco.setDecoderPath(`https://unpkg.com/three@${THREEJS_VERSION}/examples/jsm/libs/draco/`);
            loader.setDRACOLoader(draco);
        } catch (e) { /* DRACOLoader missing — non-compressed GLBs still work */ }

        const MAX_RETRIES = 2;
        return new Promise((resolve, reject) => {
            loader.load(
                url,
                (gltf) => {
                    this._rootObject = gltf.scene;
                    this._scene.add(gltf.scene);
                    this._indexUnitMeshes(gltf.scene);
                    this._fitCameraToObject(gltf.scene);
                    this._applyStateColors();
                    resolve();
                },
                (xhr) => {
                    if (xhr.total) {
                        this.state.loadingMsg = `Loading building model… ${Math.round((xhr.loaded / xhr.total) * 100)}%`;
                    }
                },
                (err) => {
                    if (!this._hasGlb) {
                        // Genuinely no model uploaded — show demo cubes so the
                        // page isn't empty.
                        this._buildPlaceholderCubes();
                        resolve();
                    } else if (attempt < MAX_RETRIES) {
                        // A model exists but the fetch hiccuped (cold load /
                        // transient). Retry shortly instead of silently
                        // degrading to placeholder cubes.
                        this.state.loadingMsg = "Retrying model load…";
                        setTimeout(
                            () => this._loadGlb(attempt + 1).then(resolve, reject),
                            400);
                    } else {
                        reject(new Error(
                            "Could not load the 3D model after several tries. " +
                            "Please reload the page."));
                    }
                }
            );
        });
    }

    _buildPlaceholderCubes() {
        // 3×4 grid of cubes representing demo units so the viewer is testable
        const T = this._three;
        const group = new T.Group();
        const codes = Object.keys(this._unitData).slice(0, 12);
        const fallbackCount = Math.max(codes.length, 12);
        for (let i = 0; i < fallbackCount; i++) {
            const code = codes[i] || `demo_${i + 1}`;
            const geom = new T.BoxGeometry(8, 4, 8);
            const mat = new T.MeshStandardMaterial({ color: 0xffffff, metalness: 0.1, roughness: 0.7 });
            const mesh = new T.Mesh(geom, mat);
            mesh.position.set((i % 4) * 10 - 15, Math.floor(i / 4) * 5, 0);
            mesh.name = code;
            mesh.userData.unitKey = code;
            group.add(mesh);
            this._unitMeshes[code] = mesh;
        }
        this._rootObject = group;
        this._scene.add(group);
        this._fitCameraToObject(group);
        this._applyStateColors();
    }

    _indexUnitMeshes(root) {
        const T = this._three;
        root.traverse((child) => {
            if (!child.isMesh) return;
            const name = child.name;
            const data = this._unitData[name];
            if (data) {
                child.userData.unitKey = name;
                this._unitMeshes[name] = child;
                // ensure each mesh has its own material instance (otherwise
                // shared materials all change color together)
                if (child.material && !child.material._cloned) {
                    child.material = child.material.clone();
                    child.material._cloned = true;
                }
            }
        });
    }

    _applyStateColors() {
        const T = this._three;
        for (const [key, mesh] of Object.entries(this._unitMeshes)) {
            const data = this._unitData[key];
            const color = (data && data.color_override) ||
                          (data && STATE_COLORS[data.state]) ||
                          STATE_COLORS.default;
            if (mesh.material) {
                if (!mesh.material.color) {
                    mesh.material = new T.MeshStandardMaterial({ color: new T.Color(color) });
                } else {
                    mesh.material.color = new T.Color(color);
                    mesh.material.transparent = true;
                    mesh.material.opacity = data && data.state === "sold" ? 0.5 : 0.95;
                    mesh.material.needsUpdate = true;
                }
            }
        }
    }

    _fitCameraToObject(object) {
        const T = this._three;
        const box = new T.Box3().setFromObject(object);
        if (box.isEmpty()) return;
        const size = box.getSize(new T.Vector3());
        const center = box.getCenter(new T.Vector3());
        const maxDim = Math.max(size.x, size.y, size.z);
        const fitDist = maxDim / (2 * Math.tan((Math.PI * this._camera.fov) / 360));

        this._camera.position.copy(center);
        this._camera.position.x += fitDist * 0.9;
        this._camera.position.y += fitDist * 0.7;
        this._camera.position.z += fitDist * 0.9;
        this._camera.lookAt(center);

        this._controls.target.copy(center);
        this._controls.update();
    }

    _applyCamera(camData) {
        if (!camData || !camData.position || !camData.target) return;
        this._camera.position.set(...camData.position);
        this._controls.target.set(...camData.target);
        this._controls.update();
    }

    async _loadHdrInBackground() {
        const T = this._three;
        try {
            const rgbe = new T.RGBELoader();
            const url = this._glbVersion
                ? `/maquette/hdr/${this.props.projectId}?v=${this._glbVersion}`
                : `/maquette/hdr/${this.props.projectId}`;
            rgbe.load(url, (tex) => {
                tex.mapping = T.EquirectangularReflectionMapping;
                this._scene.environment = tex;
            });
        } catch (e) { /* HDR is optional */ }
    }

    _onMouseMove(ev) {
        const rect = this._renderer.domElement.getBoundingClientRect();
        this._mouse.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
        this._mouse.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
        this._raycaster.setFromCamera(this._mouse, this._camera);
        const hits = this._raycaster.intersectObjects(Object.values(this._unitMeshes), false);
        if (hits.length) {
            const key = hits[0].object.userData.unitKey;
            if (key !== this.state.hovered) {
                this.state.hovered = key;
                this._renderer.domElement.style.cursor = "pointer";
            }
        } else if (this.state.hovered) {
            this.state.hovered = null;
            this._renderer.domElement.style.cursor = "default";
        }
    }

    _hitUnit(ev) {
        // Returns {mesh, data} for the model mesh under the cursor (data is the
        // mapped unit or null), or null when nothing in the model was hit.
        if (!this._rootObject) return null;
        const rect = this._renderer.domElement.getBoundingClientRect();
        this._mouse.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
        this._mouse.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
        this._raycaster.setFromCamera(this._mouse, this._camera);
        const hits = this._raycaster.intersectObject(this._rootObject, true);
        if (!hits.length) return null;
        const mesh = hits[0].object;
        const data = this._unitData[mesh.name] || this._unitData[mesh.userData.unitKey] || null;
        return { mesh, data: data && data.id ? data : null };
    }

    // Single click: just SELECT an already-mapped unit → show its info in the
    // side panel. No wizard (the picker is double-click only).
    _onSingleClick(ev) {
        const hit = this._hitUnit(ev);
        if (hit && hit.data) {
            this.state.selectedUnit = hit.data;
            this._highlight(hit.mesh);
        }
    }

    // Double click: open the picker for any part of the model (pre-select if mapped).
    _onDblClick(ev) {
        const hit = this._hitUnit(ev);
        if (!hit) return;                          // empty space
        if (hit.data) {
            this.state.selectedUnit = hit.data;
            this._highlight(hit.mesh);
        }
        this._openUnitPicker(hit.data ? hit.data.id : false, hit.mesh.name);
    }

    _openUnitPicker(unitId, meshName) {
        // Guard: never open a second picker while one is already open, no matter
        // how many click/dblclick events fire.
        if (this._pickerOpen) return;
        this._pickerOpen = true;
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Pick a Unit",
            res_model: "realestate.maquette.unit.picker",
            view_mode: "form",
            views: [[false, "form"]],
            target: "new",
            context: {
                default_project_id: this.props.projectId,
                default_unit_id: unitId || false,
                default_mesh_name: meshName || false,
            },
        }, {
            onClose: () => {
                this._pickerOpen = false;
                this._refreshUnits();
            },
        });
    }

    async _refreshUnits() {
        // Re-pull unit data after the picker closes so a freshly-mapped unit
        // appears (colored) on the map without reopening the tab.
        try {
            const meta = await rpc(`/maquette/units/${this.props.projectId}`, {});
            if (meta.error) return;
            this._unitData = {};
            for (const u of meta.units) {
                if (u.mesh_name) this._unitData[u.mesh_name] = u;
                if (u.property_code) this._unitData[u.property_code] = this._unitData[u.property_code] || u;
            }
            this._refreshStats(meta.units);
            if (this._rootObject) this._indexUnitMeshes(this._rootObject);
            this._applyStateColors();
        } catch (e) { /* ignore refresh errors */ }
    }

    _highlight(mesh) {
        const T = this._three;
        // Restore previous
        if (this._highlighted && this._origMaterials.has(this._highlighted)) {
            const prevMat = this._origMaterials.get(this._highlighted);
            this._highlighted.material.emissive = prevMat.emissive;
            this._highlighted.material.emissiveIntensity = prevMat.intensity;
        }
        if (mesh.material) {
            if (!this._origMaterials.has(mesh)) {
                this._origMaterials.set(mesh, {
                    emissive: mesh.material.emissive ? mesh.material.emissive.clone() : new T.Color(0x000000),
                    intensity: mesh.material.emissiveIntensity ?? 0,
                });
            }
            mesh.material.emissive = new T.Color(0xffffff);
            mesh.material.emissiveIntensity = 0.4;
        }
        this._highlighted = mesh;
    }

    _focusOnMesh(meshName) {
        const mesh = this._unitMeshes[meshName];
        if (!mesh) return;
        this.state.selectedUnit = this._unitData[meshName];
        this._highlight(mesh);
        // Tween camera to mesh
        const T = this._three;
        const target = new T.Vector3();
        mesh.getWorldPosition(target);
        this._controls.target.copy(target);
        this._controls.update();
    }

    _animate() {
        this._animationId = requestAnimationFrame(() => this._animate());
        this._controls.update();
        this._renderer.render(this._scene, this._camera);
    }

    _teardown() {
        if (this._animationId) cancelAnimationFrame(this._animationId);
        if (this._clickTimer) { clearTimeout(this._clickTimer); this._clickTimer = null; }
        if (this._onResize) window.removeEventListener("resize", this._onResize);
        if (this._resizeObserver) { this._resizeObserver.disconnect(); this._resizeObserver = null; }
        if (this._renderer) {
            this._renderer.dispose();
            this._renderer = null;
        }
    }

    // ----- Side panel actions -----
    closeSidePanel() {
        this.state.selectedUnit = null;
        if (this._highlighted && this._origMaterials.has(this._highlighted)) {
            const prev = this._origMaterials.get(this._highlighted);
            this._highlighted.material.emissive = prev.emissive;
            this._highlighted.material.emissiveIntensity = prev.intensity;
            this._highlighted = null;
        }
    }

    async openImages() {
        const u = this.state.selectedUnit;
        if (!u || !u.id) return;
        const images = [];
        if (u.has_floor_plan) {
            images.push({ id: `fp-${u.id}`, src: `/web/image/realestate.property/${u.id}/floor_plan_image` });
        }
        const recs = await this.orm.searchRead(
            "property.image", [["property_id", "=", u.id]], ["id"], { order: "sequence, id" });
        for (const r of recs) {
            images.push({ id: r.id, src: `/web/image/property.image/${r.id}/image_1920` });
        }
        // In backend (portalMode is undefined here) we open even with no
        // images so the user can upload the first one inline.
        const portal = this.portalMode || this.props.mode === "portal";
        if (!images.length && portal) {
            this.notification.add("No images for this unit yet.", { type: "info" });
            return;
        }
        this.dialog.add(CarouselDialog, {
            images,
            title: u.name || u.property_code || "Unit Images",
            propertyId: u.id,
            canUpload: !portal,
        });
    }

    async openUnitForm() {
        const u = this.state.selectedUnit;
        if (!u || !u.id) {
            this.notification.add("This mesh is not mapped to a unit yet.", { type: "warning" });
            return;
        }
        if (this.portalMode) return; // no backend form for visitors
        await this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "realestate.property",
            res_id: u.id,
            view_mode: "form",
            views: [[false, "form"]],
            target: "current",
        });
    }

    async reserveUnit() {
        const u = this.state.selectedUnit;
        if (!u || !u.id) return;
        if (this.portalMode) {
            this._dispatchPortalForm("eoi", u.id);
            return;
        }
        if (u.state !== "available") {
            this.notification.add("Unit is not available.", { type: "warning" });
            return;
        }
        await this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "realestate.unit.reservation",
            view_mode: "form",
            views: [[false, "form"]],
            target: "current",
            context: { default_property_id: u.id },
        });
    }

    async createListing() {
        const u = this.state.selectedUnit;
        if (!u || !u.id) return;
        if (this.portalMode) {
            this._dispatchPortalForm("visit", u.id);
            return;
        }
        await this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "realestate.listing",
            view_mode: "form",
            views: [[false, "form"]],
            target: "current",
            context: { default_property_id: u.id },
        });
    }

    _dispatchPortalForm(kind, propertyId) {
        window.dispatchEvent(new CustomEvent("re-portal-form", {
            detail: { kind, propertyId, projectId: this.props.projectId },
        }));
    }

    async autoMatchMeshes() {
        // For each mesh in the GLB, if there's a unit with matching property_code,
        // persist mesh_name = code on the unit.
        if (!this._rootObject) {
            this.notification.add("Load a 3D model first.", { type: "warning" });
            return;
        }
        const meshNames = [];
        this._rootObject.traverse((c) => { if (c.isMesh && c.name) meshNames.push(c.name); });
        if (meshNames.length === 0) {
            this.notification.add("The GLB has no named meshes.", { type: "warning" });
            return;
        }
        const codesByCode = {};
        for (const u of Object.values(this._unitData)) {
            if (u && u.property_code) codesByCode[u.property_code] = u;
        }
        const mapping = {};
        for (const name of meshNames) {
            if (codesByCode[name]) {
                mapping[name] = name; // mesh name == property code
            }
        }
        if (Object.keys(mapping).length === 0) {
            this.notification.add(
                "No mesh names match existing property codes. " +
                "Rename meshes in the GLB to match unit codes, or set Mesh Name manually on each unit.",
                { type: "warning", sticky: true }
            );
            return;
        }
        const res = await rpc("/maquette/save_mesh_mapping", {
            project_id: this.props.projectId,
            mapping: mapping,
        });
        if (res.ok) {
            this.notification.add(`Mapped ${res.updated} unit(s) to meshes.`, { type: "success" });
            // re-index after a brief moment
            for (const [name, _u] of Object.entries(mapping)) {
                this._unitData[name] = this._unitData[name] || codesByCode[name];
                const mesh = this._rootObject.getObjectByName(name);
                if (mesh && mesh.isMesh) {
                    mesh.userData.unitKey = name;
                    this._unitMeshes[name] = mesh;
                }
            }
            this._applyStateColors();
        } else {
            this.notification.add("Auto-match failed.", { type: "danger" });
        }
    }

    async saveCurrentCamera() {
        const cam = {
            position: this._camera.position.toArray(),
            target: this._controls.target.toArray(),
        };
        const res = await rpc("/maquette/save_camera", {
            project_id: this.props.projectId,
            camera_json: JSON.stringify(cam),
        });
        if (res.ok) {
            this.notification.add("Default view saved.", { type: "success" });
        }
    }

    resetView() {
        if (this._rootObject) {
            this._fitCameraToObject(this._rootObject);
        }
    }

    stateColorFor(state) {
        return STATE_COLORS[state] || STATE_COLORS.default;
    }
    stateLabelFor(state) {
        return STATE_LABELS[state] || state || "—";
    }

    floorPlanUrl(propertyId) {
        return `/maquette/floor_plan/${propertyId}`;
    }
}
