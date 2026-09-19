# -*- coding: utf-8 -*-
"""Regressions for the screen sweep of 19 September 2026.

The sweep opened every menu of the suite twice: once as an administrator, and
once as a user holding nothing but the group the menu is offered to. Five
Rental screens raised an ``AccessError`` for the legacy Read-only role.

That role is not a leftover. ``security/leasing_groups.xml`` draws the ladder
as ``Read-only -> Rental User -> Leasing Agent -> Property Manager -> Rental
Manager``, and ``views/menus.xml`` deliberately offers Leases, Units and
Tenants to it beside Rental User, "so those users keep their screens". The
screens were offered; the rows behind them were not readable.

What these tests do is what the sweep did, and what the web client does when a
user clicks the menu: read the action, ``get_views`` for its view modes, then
``web_search_read`` every field the views name and ``web_read`` the first few
records. A list column or a form tab whose model the role cannot read fails
here, with the screen and the model that refused.

The sweep named three models because it stopped at a screen's first error.
Reading every field of every view shows the whole gap: the five screens also
reach unit turns, lease parties, charge rules, escalations, incentives,
renewals, amendments, terminations and move-in/move-out. All of them are
granted the same read the rung above (Rental User) already has, and nothing
more.
"""

from lxml import etree

from odoo.exceptions import AccessError
from odoo.tests.common import new_test_user, tagged
from odoo.tools.safe_eval import safe_eval

from .common import LeaseCase


#: The five screens the sweep failed on, by the menu it opened them from.
READ_ONLY_SCREENS = {
    'Rental/Units/Available Units': 'atmta_real_estate.action_available_units',
    'Rental/Units/All Rental Units': 'atmta_real_estate.action_property_view',
    'Rental/Leases/Leases': 'atmta_real_estate.action_realestate_contract',
    'Rental/Leases/Expiring & Renewals': 'atmta_real_estate.action_lease_expiry_board',
    'Rental/Tenants': 'atmta_real_estate.action_rental_tenants',
}


def read_spec(model, names):
    """The ``web_read`` specification the client sends for ``names``.

    A relational field is asked for its ``display_name``, which is what makes
    the client read the comodel -- and what makes a missing ACL row on the
    comodel surface here rather than in production.
    """
    spec = {}
    for name in names:
        field = model._fields.get(name)
        if field is None:
            continue
        spec[name] = ({'fields': {'display_name': {}}}
                      if field.type in ('many2one', 'many2many', 'one2many') else {})
    return spec


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestReadOnlyRoleCanReadItsScreens(LeaseCase):
    """Every screen the Read-only role is offered, it can also read."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Only the legacy Read-only group: the sweep's "minimal user holding
        # the group the menu is offered to". No Rental role, because a Rental
        # role implies this one and would hide the gap being tested.
        cls.readonly = new_test_user(
            cls.env, login='sweep_rental_readonly', company_id=cls.company.id,
            groups='base.group_user,atmta_real_estate.group_realestate_readonly')

    def setUp(self):
        super().setUp()
        # Rows for the screens to show. An empty screen would still fail on the
        # ACL, but a screen with records also exercises web_read.
        self.lease = self.make_lease(
            prop=False, rent=1050.0, is_single_property=False,
            is_multi_property=True, property_id=False)
        self.lease.write({'property_line_ids': [(0, 0, {
            'property_id': self.unit_b.id,
            'allocated_rent': 1050.0,
            'start_date': self.lease.start_date,
            'end_date': self.lease.end_date,
        })]})
        self.activate(self.lease)
        self.deposit = self.env['realestate.contract.deposit'].create({
            'contract_id': self.lease.id,
            'partner_id': self.tenant.id,
            'requested_amount': 2000.0,
        })
        self.meter = self.env['realestate.property.meter'].create({
            'property_id': self.unit_b.id,
            'meter_type': 'electricity',
            'name': 'E-SWEEP-1',
        })
        self.env.flush_all()

    # ------------------------------------------------------------------
    def open_screen(self, action):
        """Open ``action`` as the Read-only role does in the web client."""
        model = self.env[action.res_model].with_user(self.readonly)
        context = safe_eval(action.context or '{}',
                            {'uid': self.readonly.id, 'active_id': False, 'active_ids': []})
        model = model.with_context(**{k: v for k, v in context.items()
                                      if not k.startswith('search_default_')})
        modes = [m for m in (action.view_mode or 'list,form').split(',') if m]
        views = model.get_views([(False, m) for m in modes]
                                + [(action.search_view_id.id or False, 'search')])

        names = set()
        for mode in modes:
            arch = views['views'].get(mode, {}).get('arch')
            if arch:
                names |= {n.get('name') for n in etree.fromstring(arch).iter('field')}
        spec = read_spec(model, names)
        domain = safe_eval(action.domain or '[]', {'uid': self.readonly.id})
        rows = model.web_search_read(domain, spec, limit=20)
        ids = [row['id'] for row in rows['records']][:5]
        if ids:
            model.browse(ids).web_read(spec)

    def test_the_read_only_role_opens_every_screen_it_is_offered(self):
        failures = []
        for label, xmlid in READ_ONLY_SCREENS.items():
            self.env.invalidate_all()
            try:
                self.open_screen(self.env.ref(xmlid))
            except AccessError as error:
                failures.append('%s: %s' % (label, str(error).splitlines()[0]))
        self.assertFalse(
            failures,
            "The Read-only role is offered these screens and cannot read "
            "them:\n  " + "\n  ".join(failures))

    # ------------------------------------------------------------------
    # The three models the sweep named, asserted one by one so a regression
    # says which screen lost its rows rather than only that one of five did.
    # ------------------------------------------------------------------
    def test_a_unit_screen_shows_its_meters(self):
        """Units/Available Units — the unit form has a Utility Meters tab."""
        self.env['realestate.property.meter'].with_user(self.readonly).web_search_read(
            [], {'display_name': {}, 'property_id': {'fields': {'display_name': {}}}}, limit=5)

    def test_a_lease_screen_shows_its_allocated_units(self):
        """Leases — the lease form has a Units tab of allocations."""
        self.env['realestate.contract.property.line'].with_user(
            self.readonly).web_search_read(
            [], {'display_name': {}, 'contract_id': {'fields': {'display_name': {}}}}, limit=5)

    def test_a_tenant_screen_shows_the_deposits_held(self):
        """Tenants — the partner form lists the deposits held from them."""
        self.env['realestate.contract.deposit'].with_user(self.readonly).web_search_read(
            [], {'display_name': {}, 'partner_id': {'fields': {'display_name': {}}}}, limit=5)

    # ------------------------------------------------------------------
    def test_the_read_only_role_stays_read_only(self):
        """Read is granted; nothing else is. The role is the bottom rung.

        ``realestate.property.meter.reading`` is deliberately absent: the
        meters tab shows ``current_reading`` and ``last_reading_date``, which
        are stored on the meter, so the screen never reads a reading row.
        """
        for model in ('realestate.property.meter',
                      'realestate.unit.turn',
                      'realestate.contract.property.line',
                      'realestate.contract.party',
                      'realestate.contract.charge.rule',
                      'realestate.rent.escalation.rule',
                      'realestate.contract.incentive',
                      'realestate.contract.deposit',
                      'realestate.contract.renewal',
                      'realestate.contract.amendment',
                      'realestate.contract.termination',
                      'realestate.move.in',
                      'realestate.move.out'):
            records = self.env[model].with_user(self.readonly)
            self.assertTrue(records.has_access('read'), model)
            for operation in ('write', 'create', 'unlink'):
                self.assertFalse(
                    records.has_access(operation),
                    "The Read-only role may not %s %s." % (operation, model))



@tagged('post_install', '-at_install', 'atmta_leasing')
class TestTheSharedUnitFormOutsideRental(LeaseCase):
    """Rental's additions to the shared unit form are Rental's alone.

    ``realestate.property`` belongs to Property Core, and Rental, Developer,
    Handover, Brokerage and Visual all extend its form. The sweep's Developer
    Read-only role opened Development & Sales/Units and was refused
    ``realestate.property.meter``: Rental's meters tab was in the arch the
    client loaded, and Rental is the only application that grants read on a
    meter, a unit turn, a lease allocation or a lease.

    The Developer role cannot be built in this suite -- Rental installs
    without the Developer application -- so the user below stands in for it,
    the way ``atmta_property_core``'s own tests do: someone who may read
    properties and holds no Rental role. What the fix does is offer each of
    those nodes to the roles that can read the rows behind it.
    """

    #: (node name, the model it would make the client read)
    RENTAL_ONLY_NODES = (
        ('meter_count', 'realestate.property.meter'),
        ('meter_ids', 'realestate.property.meter'),
        ('unit_turn_count', 'realestate.unit.turn'),
        ('unit_turn_ids', 'realestate.unit.turn'),
        ('property_line_ids', 'realestate.contract.property.line'),
        ('blocking_contract_id', 'realestate.contract'),
    )

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.outsider = new_test_user(
            cls.env, login='sweep_property_outsider', company_id=cls.company.id,
            groups='base.group_user')
        # The read another application grants on the asset it shares with
        # Rental. Created here rather than borrowed from a Rental group,
        # because the point is a user who has the first and not the second.
        for model in ('realestate.property', 'property.type', 'property.usage',
                      'property.image'):
            cls.env['ir.model.access'].create({
                'name': 'sweep outsider reads %s' % model,
                'model_id': cls.env['ir.model']._get_id(model),
                'group_id': cls.env.ref('base.group_user').id,
                'perm_read': True, 'perm_write': False,
                'perm_create': False, 'perm_unlink': False,
            })

    def setUp(self):
        super().setUp()
        # A live lease, so the unit really does carry a blocking lease, meters
        # and allocations to be refused.
        self.activate(self.make_lease(prop=self.unit_a))
        self.env['realestate.property.meter'].create({
            'property_id': self.unit_a.id, 'meter_type': 'water',
            'name': 'W-SWEEP-1',
        })
        self.env.flush_all()

    def test_a_user_outside_rental_is_not_offered_rentals_tabs(self):
        arch = etree.fromstring(
            self.env['realestate.property'].with_user(self.outsider).get_view(
                view_type='form')['arch'])
        offered = {node.get('name') for node in arch.iter('field')}
        for name, model in self.RENTAL_ONLY_NODES:
            self.assertNotIn(
                name, offered,
                "%s is offered to a user outside Rental, and reading it reads "
                "%s, which only Rental grants." % (name, model))

    def test_a_rental_role_keeps_every_one_of_them(self):
        """The nodes are scoped, not removed."""
        user = new_test_user(
            self.env, login='sweep_rental_keeps', company_id=self.company.id,
            groups='base.group_user,atmta_real_estate.group_realestate_readonly')
        arch = etree.fromstring(
            self.env['realestate.property'].with_user(user).get_view(
                view_type='form')['arch'])
        offered = {node.get('name') for node in arch.iter('field')}
        for name, _model in self.RENTAL_ONLY_NODES:
            self.assertIn(name, offered, name)

    def test_a_user_outside_rental_can_open_the_unit_form(self):
        """The whole point: the shared screen loads for them."""
        model = self.env['realestate.property'].with_user(self.outsider)
        views = model.get_views([(False, 'list'), (False, 'form'), (False, 'search')])
        names = set()
        for mode in ('list', 'form'):
            arch = views['views'].get(mode, {}).get('arch')
            if arch:
                names |= {node.get('name') for node in etree.fromstring(arch).iter('field')}
        spec = read_spec(model, names)
        rows = model.web_search_read([], spec, limit=20)
        ids = [row['id'] for row in rows['records']][:5]
        self.assertTrue(ids)
        model.browse(ids).web_read(spec)
