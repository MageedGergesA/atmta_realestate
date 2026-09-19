# Draco decoder — install here

**This directory is deliberately empty of binaries.** The Draco decoder is a
set of compiled artefacts from Google's Draco project. They are redistributable
(Apache-2.0) but they are not source, and this repository does not vendor them.

Until they are installed, a GLB using `KHR_draco_mesh_compression` is refused at
publication with a **critical** validation issue. That is deliberate: before
M4.5-D the viewer fetched the decoder from `unpkg.com` at runtime, so a
Draco model silently failed on any deployment without public internet access,
and the failure reached a customer as an unexplained error.

---

## What to install

Exactly three files, from **Three.js r160** — the revision this module vendors
(`three.module.js` line 6: `const REVISION = '160'`).

| File | Required | Used when |
|---|---|---|
| `draco_wasm_wrapper.js` | **yes** | WebAssembly available (all current browsers) |
| `draco_decoder.wasm` | **yes** | WebAssembly available |
| `draco_decoder.js` | optional | WebAssembly unavailable — a JS fallback |

Source path inside the Three.js release:

```
    three-r160/examples/jsm/libs/draco/
```

Plus two files this module's validator checks for:

| File | Contents |
|---|---|
| `THREEJS_VERSION` | the single line `160` |
| `LICENSE` | the Apache-2.0 notice from the Draco distribution |

## Why the version has to match

`DRACOLoader.js` and the decoder are one unit. The loader builds a worker from
the wrapper source and talks to it over a protocol that has changed between
releases. Pairing r160's loader with another release's decoder **does not fail
cleanly** — it produces corrupt geometry or a silent hang, which is far worse
than a missing file.

`THREEJS_VERSION` exists so that cannot happen quietly: the validator reads it
and raises a **critical** issue if it does not say `160`. If you upgrade
Three.js, replace the decoder from the same release and update that file.

## Installing

```bash
cd real_estate_maquette/static/src/lib/threejs/draco/

# From an official Three.js r160 distribution, not from a CDN at runtime:
cp <three-r160>/examples/jsm/libs/draco/draco_wasm_wrapper.js .
cp <three-r160>/examples/jsm/libs/draco/draco_decoder.wasm .
cp <three-r160>/examples/jsm/libs/draco/draco_decoder.js .      # optional
cp <three-r160>/examples/jsm/libs/draco/README.md ./LICENSE     # or the
                                                                # Apache-2.0 text

echo 160 > THREEJS_VERSION
```

Then re-validate any project whose model uses Draco. Nothing needs restarting;
the check reads the filesystem.

## Serving it from somewhere else

A deployment that already serves static assets from its own host can point at
that instead — still not a public CDN, but its own infrastructure:

```
    Settings → Technical → System Parameters
    real_estate_maquette.draco_decoder_path = https://assets.internal/draco/
```

When the parameter is set to anything other than the default, the validator
takes the deployment's word for it and stops checking the local directory.
Reaching across the network to verify would make validation depend on exactly
the connectivity this feature exists to avoid needing.

## Not using Draco at all

Entirely fine, and the common case. Export models without Draco compression and
nothing here applies — no warning, no issue, no configuration.
