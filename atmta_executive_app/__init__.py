# -*- coding: utf-8 -*-
from . import models

#: Legacy roots this application replaces (data/retire_legacy_navigation.xml).
RETIRED_ROOTS = (
    'real_estate_investment.menu_investment_root',
)


def uninstall_hook(env):
    """Give the legacy roots back when this application is removed."""
    for xmlid in RETIRED_ROOTS:
        menu = env.ref(xmlid, raise_if_not_found=False)
        if menu:
            menu.active = True
