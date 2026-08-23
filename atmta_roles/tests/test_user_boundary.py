# -*- coding: utf-8 -*-
"""Where a canonical ATMTA role sits in Odoo's user-type universe.

"Permission-neutral" needed refining. `ATMTA User` implies `base.group_user`, so
holding a canonical role is *not* neutral with respect to Odoo user type — it
places the account in the Internal User universe deliberately. It is neutral
with respect to ATMTA business ACLs, record rules, menus and domain access.

These tests pin both halves of that: the ATMTA delta is zero, and the Odoo
user-type consequence is intended rather than accidental.
"""

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

ATMTA_MODELS = [
    'realestate.project', 'realestate.property',
    'realestate.procurement.vendor.qualification',
    'realestate.procurement.vendor.restriction',
]


@tagged('post_install', '-at_install', 'atmta_v2', 'atmta_wave4')
class TestCanonicalRoleUserBoundary(TransactionCase):

    def _closure(self, groups):
        seen, stack = set(), list(groups.ids)
        while stack:
            gid = stack.pop()
            if gid in seen:
                continue
            seen.add(gid)
            stack.extend(self.env['res.groups'].browse(gid).implied_ids.ids)
        return self.env['res.groups'].browse(sorted(seen))

    def test_a_role_only_user_lands_exactly_where_intended(self):
        """Minimum valid configuration: assign the canonical role and nothing
        else. `base.group_user` is NOT added by hand — if the account ends up an
        internal user, it is because the role said so."""
        buyer = self.env.ref('atmta_roles.group_procurement_buyer')
        user = self.env['res.users'].create({
            'name': 'w4.buyer.only', 'login': 'w4.buyer.only',
            'groups_id': [(6, 0, [buyer.id])]})

        closure = self._closure(user.groups_id)
        self.assertIn(buyer, closure)
        self.assertIn(self.env.ref('atmta_roles.group_atmta_user'), closure,
                      "The canonical role must reach ATMTA User.")
        self.assertIn(self.env.ref('base.group_user'), closure,
                      "ATMTA User is designed to imply Odoo's Internal User.")

        # Nothing from any business module may appear.
        offenders = []
        for g in closure:
            owner = self.env['ir.model.data'].search(
                [('model', '=', 'res.groups'), ('res_id', '=', g.id)], limit=1)
            module = owner.module or ''
            if module.startswith('real_estate') or (
                    module.startswith('atmta') and module != 'atmta_roles'):
                offenders.append('%s.%s' % (module, owner.name))
        self.assertFalse(
            offenders,
            "A role-only user reached business-module groups: %s" % offenders)

    def test_the_atmta_permission_delta_is_zero(self):
        """The claim stated precisely.

        Compare a plain Internal User against the same thing plus one canonical
        role. On every ATMTA model the CRUD bits and the record reach must be
        identical. What the role adds is Internal User status, which the
        control already has — so the ATMTA delta is what this measures.
        """
        employee = self.env.ref('base.group_user')
        control = self.env['res.users'].create({
            'name': 'w4.plain.internal', 'login': 'w4.plain.internal',
            'groups_id': [(6, 0, [employee.id])]})
        subject = self.env['res.users'].create({
            'name': 'w4.internal.buyer', 'login': 'w4.internal.buyer',
            'groups_id': [(6, 0, [employee.id,
                                  self.env.ref('atmta_roles.group_procurement_buyer').id])]})

        for model in [m for m in ATMTA_MODELS if m in self.env]:
            for who, label in ((control, 'plain'), (subject, 'with role')):
                pass
            bits = {}
            for user, label in ((control, 'control'), (subject, 'subject')):
                M = self.env[model].with_user(user)
                crud = ''
                for op in ('read', 'write', 'create', 'unlink'):
                    try:
                        M.check_access_rights(op, raise_exception=True)
                        crud += '1'
                    except Exception:
                        crud += '0'
                try:
                    reach = M.search_count([])
                except Exception as exc:
                    reach = 'DENIED(%s)' % type(exc).__name__
                bits[label] = (crud, reach)
            self.assertEqual(
                bits['control'], bits['subject'],
                "Canonical Buyer changed access to %s: plain=%s with-role=%s"
                % (model, bits['control'], bits['subject']))

    def test_a_portal_user_cannot_also_hold_a_canonical_role(self):
        """Canonical ATMTA business roles are Internal-User roles.

        Odoo enforces one user type per account. Assigning a canonical role to a
        portal account must not leave a contradictory Portal+Internal state —
        either Odoo refuses, or the account genuinely becomes internal. This
        records which, without going behind Odoo's back to force it.
        """
        portal = self.env.ref('base.group_portal')
        buyer = self.env.ref('atmta_roles.group_procurement_buyer')
        user = self.env['res.users'].create({
            'name': 'w4.portal', 'login': 'w4.portal',
            'groups_id': [(6, 0, [portal.id])]})
        self.assertIn(portal, user.groups_id)

        refused = False
        try:
            # A savepoint, so a refusal leaves a transaction that can still be
            # read. Without it the failed write stays visible in a poisoned
            # transaction and "unchanged" would assert nothing.
            with self.env.cr.savepoint():
                user.write({'groups_id': [(4, buyer.id)]})
                user.flush_recordset()
        except ValidationError:
            refused = True
        user.invalidate_recordset()

        if refused:
            # Measured behaviour on Odoo 18: refused with
            # "The user cannot have more than one user types."
            self.assertIn(portal, user.groups_id,
                          "The write was refused; the account must still be portal.")
            self.assertNotIn(
                buyer, user.groups_id,
                "The canonical role persisted despite the refusal.")
            self.assertNotIn(
                self.env.ref('base.group_user'), user.groups_id,
                "The refused write left the account internal.")
        else:
            # Odoo accepted it, so the account must now be genuinely internal —
            # never both.
            closure = self._closure(user.groups_id)
            self.assertNotIn(
                portal, closure,
                "The account holds Portal and a canonical ATMTA role at once. "
                "That is the contradictory state this test exists to forbid.")
            self.assertIn(self.env.ref('base.group_user'), closure)

    def test_no_canonical_role_is_preassigned_to_the_public_user(self):
        public = self.env.ref('base.public_user', raise_if_not_found=False)
        if not public:
            self.skipTest("No public user in this database.")
        role_ids = set(self.env['ir.model.data'].search([
            ('module', '=', 'atmta_roles'), ('model', '=', 'res.groups')]).mapped('res_id'))
        held = role_ids & set(self._closure(public.groups_id).ids)
        self.assertFalse(
            held, "The public user reaches canonical ATMTA roles: %s"
            % self.env['res.groups'].browse(sorted(held)).mapped('name'))
