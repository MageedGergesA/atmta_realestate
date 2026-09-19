/** @odoo-module **/

/**
 * M9 / Rule 4 — what this device can actually render, and what to do if it
 * cannot render anything.
 *
 * The audit found `new THREE.WebGLRenderer(...)` called with no capability
 * probe at all. Any failure — no GPU, a driver blocklist, a lost context, a
 * malformed GLB — landed in one outer `catch` and produced a red box
 * containing a Three.js exception message. Rule 4 requires
 *
 *     3D → optimised 2D → normal property details
 *
 * and there was no second or third step.
 *
 * ### Capability is measured, never sniffed
 *
 * The brief is explicit that user-agent strings must not be the primary
 * detection, and it is right: a UA string tells you what a browser claims to
 * be, not whether it has a working GPU. Everything below comes from creating a
 * real context and asking it real questions, then throwing it away.
 *
 * ```
 *     probe() ──▶ { supported, tier, reasons, limits }
 *
 *     high    discrete-class limits, plenty of texture units
 *     medium  ordinary integrated GPU
 *     low     software rasteriser, or very small limits
 *     none    no context at all → 2D
 * ```
 */

/** Tiers, in descending capability. Mirrors CAPABILITY_TIER server-side. */
export const TIER_HIGH = "high";
export const TIER_MEDIUM = "medium";
export const TIER_LOW = "low";
export const TIER_NONE = "none";

const TIER_ORDER = [TIER_NONE, TIER_LOW, TIER_MEDIUM, TIER_HIGH];

/** Renderer strings that mean "this is the CPU pretending to be a GPU". */
const SOFTWARE_RENDERERS = [
    "swiftshader", "llvmpipe", "software", "microsoft basic render",
];

/**
 * Probe the device once and report what it can do.
 *
 * Creates a throwaway context, reads its limits, and disposes it immediately —
 * a probe that leaks a context is a strange way to start a memory-conscious
 * viewer.
 */
export function probeCapability() {
    const result = {
        supported: false,
        tier: TIER_NONE,
        reasons: [],
        limits: {},
        renderer: "",
    };

    let canvas;
    let gl = null;
    try {
        canvas = document.createElement("canvas");
        // `failIfMajorPerformanceCaveat` is what surfaces a software
        // rasteriser as an outright failure rather than as a context that
        // renders one frame every two seconds.
        gl = canvas.getContext("webgl2", { failIfMajorPerformanceCaveat: true })
            || canvas.getContext("webgl", { failIfMajorPerformanceCaveat: true });
        if (!gl) {
            // Try again without the caveat flag: a software renderer is worth
            // knowing about, and `low` is a better answer than `none` if the
            // user has nothing else.
            gl = canvas.getContext("webgl2") || canvas.getContext("webgl");
            if (gl) {
                result.reasons.push("major_performance_caveat");
            }
        }
    } catch (err) {
        result.reasons.push(`context_threw:${err && err.message}`);
    }

    if (!gl) {
        result.reasons.push("no_webgl_context");
        return result;
    }

    result.supported = true;
    try {
        const debugInfo = gl.getExtension("WEBGL_debug_renderer_info");
        if (debugInfo) {
            result.renderer = String(
                gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL) || "");
        }
        result.limits = {
            maxTextureSize: gl.getParameter(gl.MAX_TEXTURE_SIZE) || 0,
            maxTextureUnits:
                gl.getParameter(gl.MAX_COMBINED_TEXTURE_IMAGE_UNITS) || 0,
            maxRenderbufferSize: gl.getParameter(gl.MAX_RENDERBUFFER_SIZE) || 0,
            webgl2: typeof WebGL2RenderingContext !== "undefined"
                && gl instanceof WebGL2RenderingContext,
        };
    } catch (err) {
        result.reasons.push(`limits_unreadable:${err && err.message}`);
    }

    result.tier = tierFor(result);

    // Give the context back immediately. Browsers cap how many live WebGL
    // contexts a page may hold, and spending one on a probe that never
    // released it would eventually stop the real viewer from getting one.
    try {
        const lose = gl.getExtension("WEBGL_lose_context");
        if (lose) {
            lose.loseContext();
        }
    } catch (_err) {
        /* nothing useful to do; the canvas is unreferenced either way */
    }

    return result;
}

/** Classify a probe result into a tier. */
function tierFor(result) {
    const renderer = (result.renderer || "").toLowerCase();
    if (SOFTWARE_RENDERERS.some((s) => renderer.includes(s))
        || result.reasons.includes("major_performance_caveat")) {
        return TIER_LOW;
    }
    const { maxTextureSize = 0, maxTextureUnits = 0, webgl2 } = result.limits;
    if (maxTextureSize < 4096 || maxTextureUnits < 16) {
        return TIER_LOW;
    }
    if (webgl2 && maxTextureSize >= 16384 && maxTextureUnits >= 32) {
        return TIER_HIGH;
    }
    return TIER_MEDIUM;
}

/** Whether `tier` is at least `minimum`. */
export function meetsTier(tier, minimum) {
    return TIER_ORDER.indexOf(tier) >= TIER_ORDER.indexOf(minimum);
}

/**
 * Decide which experience to open, given the device and what the project has.
 *
 * Pure function of two plain objects, so the decision is testable without a
 * browser and without a project.
 *
 * @param {object} capability result of `probeCapability()`
 * @param {object} fallback   the server's `fallback` descriptor
 * @returns {{experience: string, reason: string}}
 */
export function chooseExperience(capability, fallback) {
    const wanted = (fallback && fallback.default_experience) || "auto";
    const minimum = (fallback && fallback.min_capability) || TIER_LOW;
    const has2d = Boolean(fallback && fallback.has_2d);

    const can3d = capability.supported && meetsTier(capability.tier, minimum);

    if (wanted === "2d") {
        return has2d
            ? { experience: "2d", reason: "configured" }
            : { experience: "list", reason: "configured_but_no_plan" };
    }
    if (wanted === "list") {
        return { experience: "list", reason: "configured" };
    }
    if (wanted === "3d" && can3d) {
        return { experience: "3d", reason: "configured" };
    }

    // "auto", or a configured 3D the device cannot deliver: walk the chain.
    if (can3d) {
        return { experience: "3d", reason: "capable" };
    }
    if (has2d) {
        return {
            experience: "2d",
            reason: capability.supported ? "below_minimum_tier" : "no_webgl",
        };
    }
    // Rule 4's floor. A unit list is a complete navigation path on its own,
    // and is also the accessible one (M25).
    return {
        experience: "list",
        reason: capability.supported ? "below_minimum_tier" : "no_webgl",
    };
}
