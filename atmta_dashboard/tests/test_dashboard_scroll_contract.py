"""Every ATMTA dashboard must have a scroll container that actually scrolls.

Odoo's own layout sets, in `web/static/src/webclient/webclient_layout.scss`:

    .o_web_client > .o_action_manager > .o_action {
        height: 100%;
        display: flex;
        flex-flow: column nowrap;
        overflow: hidden;      <-- specificity (0,3,0)
    }

A dashboard whose root element carries `o_action` therefore *cannot* scroll
itself: a bare `.o_my_dashboard { overflow-y: auto; }` is specificity (0,1,0)
and loses. The content below the fold becomes unreachable with no scrollbar
and no error -- the Treasury dashboard shipped 2089px of content in an 830px
box, and the Control Tower 1529px, both silently clipped.

The fix, which the Rental dashboard already uses, is to leave the root as
Odoo's full-height column and give the *body* the scroll:

    .o_my_dashboard_body {
        flex: 1 1 auto;
        min-block-size: 0;     // or min-height: 0
        overflow-y: auto;
    }

`min-block-size: 0` is not optional. Without it a flex child refuses to shrink
below its content height, so the body is as tall as its content, nothing
overflows it, and `overflow-y: auto` never produces a scrollbar.

This test reads the sources, so it covers every dashboard in the suite whether
or not its module is installed in the test database, and it fails for any new
dashboard that repeats the mistake.
"""

import os
import re

from odoo.modules.module import get_module_path
from odoo.tests.common import TransactionCase, tagged

# Utility classes that are never the dashboard's own identity.
_IGNORED = {
    'o_action', 'o_content', 'o_control_panel',
}

# Dashboards that fill their area with a single full-bleed canvas instead of a
# document that flows. These must NOT scroll: the map is `position: absolute;
# inset: 0` and pans by itself, and a scrollbar on top of it would be a bug.
#: The module that owns the shared dashboard component library.
_DESIGN_SYSTEM = 'atmta_dashboard'

_FULL_BLEED = {
    'o_pmd_dashboard': "Leaflet properties map; the canvas fills the action "
                       "area and pans, so there is nothing to scroll.",
}
_BOOTSTRAP = re.compile(
    r'^(d-|flex-|h-|w-|p-|m-|g-|align-|justify-|text-|bg-|border-|overflow-|position-)')

_SCROLL = re.compile(r'overflow(-y)?\s*:\s*(auto|scroll)\s*;')
_MIN_ZERO = re.compile(r'min-(block-size|height)\s*:\s*0')


def _iter_blocks(scss, start=0, end=None):
    """Yield ``(selector, body)`` for each brace block at this nesting level."""
    end = len(scss) if end is None else end
    i, sel_start = start, start
    while i < end:
        ch = scss[i]
        if ch == '{':
            depth, j = 1, i + 1
            while j < end and depth:
                if scss[j] == '{':
                    depth += 1
                elif scss[j] == '}':
                    depth -= 1
                j += 1
            yield scss[sel_start:i].strip(), scss[i + 1:j - 1]
            i = sel_start = j
            continue
        if ch == ';':
            sel_start = i + 1
        i += 1


def _own_declarations(body):
    """The declarations of a block, excluding everything inside nested blocks."""
    out, depth, buf = [], 0, []
    for ch in body:
        if ch == '{':
            depth += 1
            if depth == 1:
                buf = []
                continue
        elif ch == '}':
            depth -= 1
            continue
        if depth == 0:
            buf.append(ch)
    return ''.join(buf)


def _strip_comments(text):
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    return re.sub(r'//[^\n]*', '', text)


@tagged('post_install', '-at_install', 'atmta_dashboard_scroll')
class TestDashboardScrollContract(TransactionCase):

    def _addons_root(self):
        return os.path.dirname(get_module_path('atmta_dashboard'))

    def _dashboards(self):
        """``(module, root_class, scss_path)`` for every dashboard template."""
        root = self._addons_root()
        found = []
        for module in sorted(os.listdir(root)):
            xml_dir = os.path.join(root, module, 'static', 'src', 'xml')
            if not os.path.isdir(xml_dir):
                continue
            for dirpath, _dirs, files in os.walk(xml_dir):
                for fname in files:
                    if not fname.endswith('.xml'):
                        continue
                    if 'dashboard' not in fname and 'control_tower' not in fname:
                        continue
                    path = os.path.join(dirpath, fname)
                    with open(path, encoding='utf-8') as fh:
                        source = fh.read()
                    for classes in re.findall(
                            r'<div\s+class="([^"]*\bo_action\b[^"]*)"', source):
                        names = [c for c in classes.split()
                                 if c not in _IGNORED and not _BOOTSTRAP.match(c)]
                        if names:
                            found.append((module, names[0], path))
        return found

    def _scss_for(self, module):
        """The SCSS a module's dashboard is actually styled by.

        That is the module's own stylesheets *plus* the shared design
        system's. Since the suite moved onto one dashboard component library,
        a module's dashboard template carries the shared root class
        `.o_ad_dashboard`, whose scroll container is declared once in
        `atmta_dashboard` instead of being copy-pasted into nine modules.
        Looking only at the module's own folder would report every migrated
        dashboard as broken while the rule is sitting right there in the
        library it depends on.

        The assertion below is unchanged: a root class must still have a
        block, that block must still not scroll itself, and there must still
        be a real inner scroller. Only where the declaration is allowed to
        live has widened.
        """
        blob = []
        for name in dict.fromkeys((module, _DESIGN_SYSTEM)):
            root = os.path.join(self._addons_root(), name, 'static', 'src', 'scss')
            if not os.path.isdir(root):
                continue
            for dirpath, _dirs, files in os.walk(root):
                for fname in sorted(files):
                    if fname.endswith('.scss'):
                        with open(os.path.join(dirpath, fname), encoding='utf-8') as fh:
                            blob.append(fh.read())
        return _strip_comments('\n'.join(blob))

    def test_every_dashboard_has_a_working_scroll_container(self):
        dashboards = self._dashboards()
        self.assertTrue(dashboards, "No dashboard templates were discovered.")

        broken = []
        for module, root_class, path in dashboards:
            scss = self._scss_for(module)
            block = None
            for selector, body in _iter_blocks(scss):
                if re.search(r'\.%s\b' % re.escape(root_class), selector):
                    block = body
                    break
            if block is None:
                broken.append("%s: no SCSS block for .%s (%s)"
                              % (module, root_class, os.path.basename(path)))
                continue

            # 1. The root itself must not pretend to scroll: Odoo overrides it.
            if _SCROLL.search(_own_declarations(block)):
                broken.append(
                    "%s: .%s sets overflow on the root. Odoo's "
                    "`.o_web_client > .o_action_manager > .o_action "
                    "{ overflow: hidden }` is more specific and wins, so this "
                    "never scrolls. Move the scroll to an inner body element."
                    % (module, root_class))
                continue

            if root_class in _FULL_BLEED:
                continue

            # 2. The module must define a real scroll container. It may be
            #    nested inside the root block or a sibling top-level rule --
            #    `.o_re_scroll` and `.o_ad_scroll` are written the second way.
            def _is_scroller(decls):
                return bool(_SCROLL.search(decls) and _MIN_ZERO.search(decls))

            has_scroller = any(
                _is_scroller(_own_declarations(body))
                for _sel, body in _iter_blocks(block)
            ) or any(
                _is_scroller(_own_declarations(body))
                for _sel, body in _iter_blocks(scss)
            )
            if not has_scroller:
                broken.append(
                    "%s: .%s has no inner scroll container. Give the body "
                    "`flex: 1 1 auto; min-block-size: 0; overflow-y: auto;` "
                    "or everything below the fold is unreachable."
                    % (module, root_class))

        self.assertFalse(broken, "Dashboards that cannot scroll:\n  - "
                         + "\n  - ".join(broken))
