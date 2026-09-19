# -*- coding: utf-8 -*-
"""M6 / M27 / M28 — where decoders come from, and how big assets are served.

### The Draco problem, stated exactly

The audit found this in the viewer:

```js
draco.setDecoderPath(`https://unpkg.com/three@0.160.0/examples/jsm/libs/draco/`);
```

Every Draco-compressed model therefore needs the customer's browser to reach
unpkg.com. Behind a corporate firewall, on an on-premise install, or in a GCC
network with restricted egress, the decoder never arrives and the model fails —
and in 0.4 that failure surfaced as a red box containing a Three.js exception.

**The decoder binaries are not in this repository.** They are compiled
artefacts from Google's Draco project (Apache-2.0, redistributable) and
inventing them is not something a code change can do. So rather than swapping
one broken path for another, this makes the location a deployment decision with
a safe default and a check that says plainly whether it has been done:

```
    ir.config_parameter  real_estate_maquette.draco_decoder_path
        unset  ──▶  /real_estate_maquette/static/src/lib/threejs/draco/
                    ├── present  → Draco models load, no internet needed
                    └── absent   → validation says so, and a Draco model is
                                   refused at publication rather than failing
                                   in front of a customer
```

A deployment that never uses Draco is unaffected and needs to do nothing.
"""

import os

from odoo import _, api, models

#: Where the decoder is expected unless a system parameter says otherwise.
DEFAULT_DRACO_PATH = '/real_estate_maquette/static/src/lib/threejs/draco/'
DRACO_PARAM = 'real_estate_maquette.draco_decoder_path'

#: The Three.js revision this module vendors. The decoder is not
#: version-independent: `DRACOLoader.js` from r160 expects the wrapper and
#: worker protocol shipped alongside r160, and pairing it with a decoder from
#: an unrelated release is the failure the brief warns about — it does not
#: error cleanly, it produces corrupt geometry or a silent hang.
BUNDLED_THREEJS_REVISION = '160'

#: What `DRACOLoader._initDecoder()` actually fetches, read from the vendored
#: loader rather than assumed:
#:
#:   WebAssembly available  → draco_wasm_wrapper.js + draco_decoder.wasm
#:   WebAssembly absent     → draco_decoder.js
#:
#: The WASM pair is required; the JS fallback is optional and only matters on
#: a browser without WebAssembly, which in 2026 is close to none.
DRACO_REQUIRED_FILES = ('draco_wasm_wrapper.js', 'draco_decoder.wasm')
DRACO_OPTIONAL_FILES = ('draco_decoder.js',)

#: Provenance. A decoder directory must say which Three.js release it came
#: from, so a well-meaning upgrade cannot silently pair r160's loader with
#: another release's decoder.
DRACO_VERSION_FILE = 'THREEJS_VERSION'

#: Third-party notices must survive alongside the binaries (Apache-2.0).
DRACO_LICENSE_FILES = ('LICENSE', 'LICENSE.txt')


class VisualAssets(models.AbstractModel):
    """Asset delivery configuration, in one place both halves can ask."""
    _name = 'realestate.visual.assets'
    _description = 'Visual Asset Delivery Configuration'

    @api.model
    def draco_decoder_path(self):
        """The URL path DRACOLoader should be pointed at."""
        return self.env['ir.config_parameter'].sudo().get_param(
            DRACO_PARAM, DEFAULT_DRACO_PATH)

    @api.model
    def _draco_dir(self):
        module_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(module_dir, 'static', 'src', 'lib', 'threejs',
                            'draco')

    @api.model
    def draco_decoder_status(self):
        """A full report on the local decoder, not just a boolean.

        Returns `installed`, `missing`, `revision`, `revision_matches` and
        `license_present`, because "it is not working" is a much less useful
        answer to an administrator than "you are missing draco_decoder.wasm"
        or "this decoder is from r155 and the bundle is r160".
        """
        path = self.draco_decoder_path()
        if path != DEFAULT_DRACO_PATH:
            # A deployment pointing at its own static host has told us it has
            # solved this. Reaching across the network during a validation run
            # to second-guess it would make validation depend on the very
            # connectivity this feature exists to avoid needing.
            return {
                'installed': True, 'external': True, 'path': path,
                'missing': [], 'revision': None, 'revision_matches': True,
                'license_present': True,
            }

        base = self._draco_dir()
        missing = [name for name in DRACO_REQUIRED_FILES
                   if not os.path.exists(os.path.join(base, name))]
        revision = None
        version_path = os.path.join(base, DRACO_VERSION_FILE)
        if os.path.exists(version_path):
            try:
                with open(version_path) as handle:
                    revision = handle.read().strip()
            except OSError:
                revision = None
        license_present = any(
            os.path.exists(os.path.join(base, name))
            for name in DRACO_LICENSE_FILES)

        return {
            'installed': not missing,
            'external': False,
            'path': path,
            'missing': missing,
            'revision': revision,
            'revision_matches': (revision == BUNDLED_THREEJS_REVISION
                                 if revision else False),
            'license_present': license_present,
        }

    @api.model
    def draco_decoder_installed(self):
        return self.draco_decoder_status()['installed']

    @api.model
    def viewer_config(self):
        """Everything the client needs to know about asset delivery.

        Sent with the unit payload so the viewer never has to guess, and so a
        deployment that has installed the decoder somewhere else does not need
        a code change.
        """
        status = self.draco_decoder_status()
        return {
            'draco_decoder_path': status['path'],
            'draco_available': status['installed'],
        }

    @api.model
    def draco_issue(self, gltf_extensions_used):
        """A validation finding about Draco, or None.

        Critical when the model needs Draco and the decoder is not installed:
        that model will not render, and letting it publish means a customer
        finds out instead of an administrator.
        """
        if 'KHR_draco_mesh_compression' not in (gltf_extensions_used or ()):
            return None
        status = self.draco_decoder_status()
        if not status['installed']:
            return ('critical', 'draco_decoder_missing', _(
                "This model uses Draco compression, but no Draco decoder is "
                "installed at %(path)s (missing: %(missing)s).\n\n"
                "The decoder is a compiled artefact from Google's Draco "
                "project (Apache-2.0) and is not shipped with this module. "
                "See the README in that directory for the exact files and the "
                "version they must come from. Until it is installed this "
                "model will not render in any browser.",
                path=status['path'],
                missing=', '.join(status['missing']) or '-'),
                'KHR_draco_mesh_compression', None)
        if not status['external'] and not status['revision_matches']:
            # A mismatched decoder is more dangerous than a missing one: it
            # does not fail cleanly, it produces corrupt geometry or hangs.
            return ('critical', 'draco_decoder_version', _(
                "The installed Draco decoder reports Three.js revision "
                "%(found)s, but this module bundles r%(expected)s. Pairing a "
                "loader with another release's decoder does not fail "
                "cleanly — it produces corrupt geometry or a silent hang.",
                found=status['revision'] or _('unknown'),
                expected=BUNDLED_THREEJS_REVISION),
                'KHR_draco_mesh_compression', None)
        return None
