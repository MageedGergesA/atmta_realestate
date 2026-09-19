"""0.8 post-migration: the legacy Real Estate groups become Rental roles.

Rental re-architecture, decision 4. Both group files are ``noupdate``, so
changes to records that already exist are applied here. The Rental Permissions
category, the See All Portfolios flag and its lease rule are new records and
arrive with the module data before this runs.

1. The legacy groups (Read-only, User, Manager) move to the hidden Technical
   category under names that say they are legacy. They stay installed: every
   Rental role implies one, and ACL rows, menus and other modules name them.
2. The Real Estate category becomes Rental, and Allow Self-Approval moves to
   Rental Permissions beside See All Portfolios, so the four roles show as one
   choice on the user form.
3. Every internal user holding a legacy group without its Rental role is given
   the role (``res.users._map_legacy_rental_roles``). Users the lease portfolio
   rule does not narrow today also get See All Portfolios. Portal and public
   users are logged, never changed.

Each step is safe to re-run.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

LEGACY_NAMES = {
    'atmta_real_estate.group_realestate_readonly': 'Real Estate Read-only (legacy)',
    'atmta_real_estate.group_realestate_user': 'Real Estate User (legacy)',
    'atmta_real_estate.group_realestate_manager': 'Real Estate Manager (legacy)',
}


def _hide_legacy_groups(env):
    hidden = env.ref('base.module_category_hidden')
    for xmlid, name in LEGACY_NAMES.items():
        group = env.ref(xmlid, raise_if_not_found=False)
        if group:
            group.write({'name': name, 'category_id': hidden.id})
    env.ref('atmta_real_estate.module_category_real_estate').name = 'Rental'
    env.ref('atmta_real_estate.group_rental_self_approval').category_id = env.ref(
        'atmta_real_estate.module_category_rental_permissions')
    _logger.info("Rental roles: legacy Real Estate groups hidden; "
                 "Allow Self-Approval moved to Rental Permissions.")


def _map_legacy_users(env):
    legacy = env['res.groups'].browse(
        [env.ref(xmlid).id for xmlid in LEGACY_NAMES])
    users = env['res.users'].with_context(active_test=False).search(
        [('groups_id', 'in', legacy.ids)])
    report = users._map_legacy_rental_roles()
    _logger.info(
        "Rental roles: %s user(s) hold a legacy group; moved to Rental Manager %s, "
        "Leasing Agent %s, Rental User %s; See All Portfolios given to %s.",
        len(users), len(report['rental_manager']), len(report['rental_agent']),
        len(report['rental_user']), len(report['all_portfolios']))
    for user in report['external']:
        _logger.warning(
            "Rental roles: %s is a portal or public user holding a legacy "
            "Real Estate group; left unchanged for manual review.", user.login)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    _hide_legacy_groups(env)
    _map_legacy_users(env)
