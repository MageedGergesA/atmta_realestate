/** @odoo-module **/

import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component, onMounted, onWillUnmount, useRef } from "@odoo/owl";

const THREEJS_LIB = "/real_estate_maquette/static/src/lib/threejs";

async function loadThree() {
    const THREE = await import(`${THREEJS_LIB}/three.module.js`);
    const [{ GLTFLoader }, { OrbitControls }] = await Promise.all([
        import(`${THREEJS_LIB}/GLTFLoader.js`),
        import(`${THREEJS_LIB}/OrbitControls.js`),
    ]);
    return { ...THREE, GLTFLoader, OrbitControls };
}

/**
 * Minimal GLB viewer field widget — renders a unit's 3D interior model.
 *
 *   <field name="interior_glb" widget="glb_viewer"/>
 *
 * Loads the model from /maquette/interior/<record id>; orbit/zoom/pan only.
 */
export class GlbViewerField extends Component {
    static template = "real_estate_maquette.GlbViewerField";
    static props = { ...standardFieldProps };

    setup() {
        this.canvasRef = useRef("canvas");
        this._needFit = true;
        onMounted(() => this._init());
        onWillUnmount(() => this._teardown());
    }

    get recordId() {
        return this.props.record.resId || 0;
    }
    get hasModel() {
        return !!this.props.record.data[this.props.name];
    }

    async _init() {
        if (!this.recordId || !this.hasModel) return;
        try {
            const T = await loadThree();
            this._three = T;
            const canvas = this.canvasRef.el;
            const w = canvas.clientWidth || 640;
            const h = canvas.clientHeight || 420;

            const scene = new T.Scene();
            scene.background = new T.Color(0xf1f5f9);
            const camera = new T.PerspectiveCamera(45, w / h, 0.05, 5000);
            camera.position.set(6, 5, 8);
            const renderer = new T.WebGLRenderer({ canvas, antialias: true, alpha: true });
            renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
            renderer.setSize(w, h, false);
            renderer.outputColorSpace = T.SRGBColorSpace;

            scene.add(new T.AmbientLight(0xffffff, 0.7));
            const dir = new T.DirectionalLight(0xffffff, 1.0);
            dir.position.set(5, 10, 7);
            scene.add(dir);

            const controls = new T.OrbitControls(camera, renderer.domElement);
            controls.enableDamping = true;
            controls.dampingFactor = 0.08;

            this._scene = scene;
            this._camera = camera;
            this._renderer = renderer;
            this._controls = controls;

            this._onResize = () => {
                const w2 = canvas.clientWidth;
                const h2 = canvas.clientHeight;
                if (!w2 || !h2) return;
                camera.aspect = w2 / h2;
                camera.updateProjectionMatrix();
                renderer.setSize(w2, h2, false);
                if (this._needFit && this._root) {
                    this._fit(this._root);
                    this._needFit = false;
                }
            };
            window.addEventListener("resize", this._onResize);
            if (window.ResizeObserver) {
                this._ro = new ResizeObserver(() => this._onResize());
                this._ro.observe(canvas);
            }

            const loader = new T.GLTFLoader();
            loader.load(
                `/maquette/interior/${this.recordId}`,
                (gltf) => {
                    this._root = gltf.scene;
                    scene.add(gltf.scene);
                    this._fit(gltf.scene);
                },
                undefined,
                (err) => console.error("Interior model load failed:", err),
            );

            this._animate();
        } catch (e) {
            console.error("Interior viewer init failed:", e);
        }
    }

    _fit(obj) {
        const T = this._three;
        const box = new T.Box3().setFromObject(obj);
        const size = box.getSize(new T.Vector3());
        const center = box.getCenter(new T.Vector3());
        const maxDim = Math.max(size.x, size.y, size.z) || 1;
        const dist = (maxDim / (2 * Math.tan((Math.PI / 180) * this._camera.fov / 2))) * 1.6;
        this._camera.position.set(center.x + dist, center.y + dist * 0.7, center.z + dist);
        this._camera.near = maxDim / 100;
        this._camera.far = maxDim * 100;
        this._camera.updateProjectionMatrix();
        this._controls.target.copy(center);
        this._controls.update();
    }

    _animate() {
        this._raf = requestAnimationFrame(() => this._animate());
        if (this._controls) this._controls.update();
        if (this._renderer) this._renderer.render(this._scene, this._camera);
    }

    _teardown() {
        if (this._raf) cancelAnimationFrame(this._raf);
        if (this._onResize) window.removeEventListener("resize", this._onResize);
        if (this._ro) { this._ro.disconnect(); this._ro = null; }
        if (this._renderer) { this._renderer.dispose(); this._renderer = null; }
    }
}

export const glbViewerField = {
    component: GlbViewerField,
    supportedTypes: ["binary", "integer", "char"],
};

registry.category("fields").add("glb_viewer", glbViewerField);
