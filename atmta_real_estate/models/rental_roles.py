"""Rental roles and the legacy Real Estate groups they replace.

Rental re-architecture, decision 4. The three legacy groups (Read-only, User,
Manager) stay installed as hidden aliases: every Rental role implies one, so ACL
rows, menus and other modules that name them keep working, but nobody is given
one directly any more.
"""

import logging

from odoo import models

_logger = logging.getLogger(__name__)

LEGACY_GROUPS = (
    'group_realestate_readonly',
    'group_realestate_user',
    'group_realestate_manager',
)
ROLE_GROUPS = (
    'group_rental_user',
    'group_rental_agent',
    'group_property_manager',
    'group_rental_manager',
)
# Highest legacy group first: a user is moved by the most senior one they hold.
LEGACY_TO_ROLE = (
    ('group_realestate_manager', 'group_rental_manager'),
    ('group_realestate_user', 'group_rental_agent'),
    ('group_realestate_readonly', 'group_rental_user'),
)


def _changes_groups(vals):
    # The user form writes groups through reified fields, not groups_id.
    return any(key == 'groups_id' or key.startswith(('in_group_', 'sel_groups_'))
               for key in vals)


class ResUsers(models.Model):
    _inherit = 'res.users'

    def _rental_group(self, name):
        return self.env.ref('atmta_real_estate.' + name, raise_if_not_found=False)

    def _rental_groups(self, names):
        groups = self.env['res.groups'].sudo()
        for name in names:
            group = self._rental_group(name)
            if group:
                groups |= group
        return groups

    def write(self, vals):
        if not _changes_groups(vals):
            return super().write(vals)
        roles = self._rental_groups(ROLE_GROUPS)
        roles_before = {user.id: user.sudo().groups_id & roles for user in self}
        res = super().write(vals)
        demoted = self.filtered(
            lambda user: roles_before[user.id] - user.sudo().groups_id)
        demoted._drop_orphan_legacy_groups()
        return res

    def _drop_orphan_legacy_groups(self):
        """Remove legacy aliases that none of the user's other groups implies.

        Odoo keeps a group's implied groups when the group itself is removed.
        The legacy groups are hidden from the user form, so without this a
        demoted Rental Manager would keep the legacy Manager's rights with no
        visible way to take them away.
        """
        legacy = self._rental_groups(LEGACY_GROUPS)
        for user in self.sudo():
            groups = user.groups_id
            implied = (groups - legacy).mapped('trans_implied_ids')
            orphans = (groups & legacy) - implied
            if orphans:
                user.write({'groups_id': [(3, group.id) for group in orphans]})

    def _map_legacy_rental_roles(self):
        """Give each user holding a legacy group the matching Rental role.

        * Manager -> Rental Manager, User -> Leasing Agent,
          Read-only -> Rental User.
        * The lease portfolio rule narrows Rental Users and Leasing Agents to
          their own and unassigned leases. A user it does not narrow today also
          gets See All Portfolios, so nobody loses sight of a lease.
        * Portal and public users are reported and left unchanged: a Rental
          role would make them internal users.

        Users who already hold the matching role are left alone, so running it
        again changes nothing.

        :return: dict of ``res.users`` recordsets keyed ``rental_manager``,
            ``rental_agent``, ``rental_user``, ``all_portfolios`` and
            ``external``.
        """
        legacy = self._rental_groups(LEGACY_GROUPS)
        rental_user = self._rental_group('group_rental_user')
        property_manager = self._rental_group('group_property_manager')
        all_portfolios = self._rental_group('group_rental_all_portfolios')
        report = {key: self.browse() for key in (
            'rental_manager', 'rental_agent', 'rental_user',
            'all_portfolios', 'external')}

        for user in self.with_context(active_test=False).sudo():
            groups = user.groups_id
            if not groups & legacy:
                continue
            if user.share:
                report['external'] |= user
                continue
            legacy_name, role_name = next(
                (legacy_name, role_name) for legacy_name, role_name in LEGACY_TO_ROLE
                if self._rental_group(legacy_name) in groups)
            role = self._rental_group(role_name)
            if role in groups:
                continue
            added = role
            narrowed_today = rental_user in groups
            role_sees_everything = property_manager in (role | role.trans_implied_ids)
            if not narrowed_today and not role_sees_everything:
                added |= all_portfolios
                report['all_portfolios'] |= user
            user.write({'groups_id': [(4, group.id) for group in added]})
            report[role_name.replace('group_', '')] |= user
            _logger.info("Rental roles: %s moved from %s to %s%s.",
                         user.login, legacy_name, role_name,
                         ' with See All Portfolios' if all_portfolios in added else '')
        return report
