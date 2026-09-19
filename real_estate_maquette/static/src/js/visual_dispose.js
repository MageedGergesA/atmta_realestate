/** @odoo-module **/

/**
 * M8 — giving the GPU its memory back.
 *
 * The audit found `_teardown()` disposing exactly one thing:
 *
 * ```js
 * if (this._renderer) { this._renderer.dispose(); this._renderer = null; }
 * ```
 *
 * No geometry, no materials, no textures, no environment map, no render
 * targets, no decoder worker. Three.js does not garbage-collect GPU resources —
 * WebGL objects live until something calls `dispose()` on them, and a JavaScript
 * reference going out of scope does not do that. GLTFLoader's documentation
 * warns about exactly this for image resources.
 *
 * The practical consequence: open a project, navigate away, open it again, and
 * the tab's GPU memory goes up every time until the browser drops the context —
 * which surfaces to the user as the viewer suddenly going black.
 *
 * ### What has to be walked
 *
 * ```
 *     scene
 *       └── traverse()
 *              ├── geometry.dispose()
 *              └── material(s)
 *                     ├── every texture-valued property .dispose()
 *                     └── material.dispose()
 *     scene.environment / .background   → texture.dispose()
 *     pmremGenerator                    → dispose()
 *     renderer                          → dispose() + forceContextLoss()
 *     DRACOLoader                       → dispose()   (terminates its workers)
 * ```
 *
 * Every function here is defensive: it is called during teardown, often after
 * something else has already failed, and a disposal routine that throws leaves
 * more leaked than it frees.
 */

/** Every material property Three.js may hold a texture in. */
const TEXTURE_PROPERTIES = [
    "map", "lightMap", "aoMap", "emissiveMap", "bumpMap", "normalMap",
    "displacementMap", "roughnessMap", "metalnessMap", "alphaMap",
    "envMap", "specularMap", "gradientMap", "clearcoatMap",
    "clearcoatNormalMap", "clearcoatRoughnessMap", "iridescenceMap",
    "iridescenceThicknessMap", "sheenColorMap", "sheenRoughnessMap",
    "transmissionMap", "thicknessMap", "specularIntensityMap",
    "specularColorMap", "anisotropyMap",
];

function safe(fn) {
    try {
        fn();
    } catch (_err) {
        /* teardown must not throw: it usually runs after something already
           went wrong, and a disposal routine that raises leaks the rest. */
    }
}

/** Dispose every texture a material references, then the material. */
export function disposeMaterial(material) {
    if (!material) {
        return;
    }
    for (const prop of TEXTURE_PROPERTIES) {
        const texture = material[prop];
        if (texture && typeof texture.dispose === "function") {
            safe(() => texture.dispose());
            material[prop] = null;
        }
    }
    safe(() => material.dispose());
}

/** Dispose one object's geometry and material(s). */
export function disposeObject(object) {
    if (!object) {
        return;
    }
    if (object.geometry && typeof object.geometry.dispose === "function") {
        safe(() => object.geometry.dispose());
    }
    const material = object.material;
    if (Array.isArray(material)) {
        material.forEach(disposeMaterial);
    } else if (material) {
        disposeMaterial(material);
    }
}

/**
 * Walk a scene graph and dispose everything in it, then detach it.
 *
 * Returns a small count of what was released, which the lifecycle test asserts
 * on — "we called dispose" is checkable in a way "memory went down" is not,
 * from inside a browser test.
 */
export function disposeScene(scene) {
    const counts = { objects: 0, geometries: 0, materials: 0, textures: 0 };
    if (!scene) {
        return counts;
    }

    const seenMaterials = new Set();
    const seenGeometries = new Set();

    safe(() => {
        scene.traverse((object) => {
            counts.objects += 1;
            if (object.geometry && !seenGeometries.has(object.geometry)) {
                seenGeometries.add(object.geometry);
                counts.geometries += 1;
            }
            const materials = Array.isArray(object.material)
                ? object.material
                : (object.material ? [object.material] : []);
            for (const material of materials) {
                if (seenMaterials.has(material)) {
                    continue;
                }
                seenMaterials.add(material);
                counts.materials += 1;
                for (const prop of TEXTURE_PROPERTIES) {
                    if (material[prop]) {
                        counts.textures += 1;
                    }
                }
            }
        });
    });

    // Dispose after traversing rather than during it: mutating material
    // properties inside `traverse` has bitten people before, and geometries
    // are shared between meshes often enough that double-disposal is real.
    safe(() => {
        scene.traverse((object) => disposeObject(object));
    });

    // Detach children so nothing holds the graph alive.
    safe(() => {
        while (scene.children.length) {
            scene.remove(scene.children[0]);
        }
    });

    return counts;
}

/** Dispose an environment map and a background, if they are textures. */
export function disposeEnvironment(scene) {
    if (!scene) {
        return;
    }
    for (const prop of ["environment", "background"]) {
        const value = scene[prop];
        if (value && typeof value.dispose === "function") {
            safe(() => value.dispose());
        }
        scene[prop] = null;
    }
}

/**
 * Release everything a viewer holds.
 *
 * Accepts the viewer's own bag of references so the call site stays one line
 * and nothing can be forgotten by being spelled differently in two places.
 */
export function disposeViewer({
    scene = null,
    renderer = null,
    controls = null,
    pmremGenerator = null,
    dracoLoader = null,
    ktx2Loader = null,
} = {}) {
    const counts = disposeScene(scene);
    disposeEnvironment(scene);

    safe(() => controls && controls.dispose && controls.dispose());
    safe(() => pmremGenerator && pmremGenerator.dispose());

    // Decoder workers are separate threads. Not terminating them leaves a
    // worker per viewer open for the life of the tab.
    safe(() => dracoLoader && dracoLoader.dispose());
    safe(() => ktx2Loader && ktx2Loader.dispose());

    if (renderer) {
        safe(() => renderer.dispose());
        // `dispose()` releases Three's own objects; `forceContextLoss()` is
        // what tells the browser it may reclaim the context itself. Without
        // it, repeated open/close eventually exhausts the per-page limit and
        // the newest viewer is the one that goes black.
        safe(() => renderer.forceContextLoss && renderer.forceContextLoss());
        safe(() => {
            if (renderer.domElement) {
                renderer.domElement.width = 1;
                renderer.domElement.height = 1;
            }
        });
    }

    return counts;
}
