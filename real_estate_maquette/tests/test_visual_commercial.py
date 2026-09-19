# -*- coding: utf-8 -*-
"""Rule 2 — the gallery's commercial truth comes from Developer.

The Phase 0 audit found `get_maquette_units_data()` shipping
`'state': u.state` and `'base_price': u.base_price` to the viewer. The first
knows nothing about release batches, blocks or selling windows; the second is
the internal figure discounts are measured against, which Developer's own
pricing module added `_public_price()` to stop being published.
"""

from odoo.tests import tagged

from ..models.visual_states import visual_state_from_developer
from .common import VisualCommon


@tagged('post_install', '-at_install')
class TestStateMapping(VisualCommon):
    """The mapping is a pure function, so it is tested exhaustively."""

    def test_available_means_available(self):
        self.assertEqual(
            visual_state_from_developer(True, False, 'available'), 'available')

    def test_a_committed_unit_shows_its_commitment(self):
        for status, expected in (('held', 'held'), ('reserved', 'reserved'),
                                 ('contracted', 'contracted'),
                                 ('sold', 'sold')):
            with self.subTest(status=status):
                self.assertEqual(
                    visual_state_from_developer(False, 'committed', status),
                    expected)

    def test_every_not_released_reason_reads_as_unreleased(self):
        for reason in ('not_released', 'release_window',
                       'project_not_selling', 'phase_not_selling'):
            with self.subTest(reason=reason):
                self.assertEqual(
                    visual_state_from_developer(False, reason, 'available'),
                    'unreleased')

    def test_blocks_and_maintenance_read_as_blocked(self):
        for reason in ('blocked', 'maintenance'):
            with self.subTest(reason=reason):
                self.assertEqual(
                    visual_state_from_developer(False, reason, 'available'),
                    'blocked')

    def test_an_unmapped_reason_fails_towards_not_for_sale(self):
        """Failing towards "you cannot buy this" is the safe direction."""
        self.assertEqual(
            visual_state_from_developer(False, 'something_new', 'available'),
            'not_for_sale')

    def test_a_committed_status_nobody_mapped_still_reads_as_sold(self):
        self.assertEqual(
            visual_state_from_developer(False, 'committed', 'weird'), 'sold')


@tagged('post_install', '-at_install')
class TestVisualStateFromDeveloper(VisualCommon):
    """The same mapping, through the ORM, against the real engine."""

    def test_a_released_unit_is_available(self):
        project = self._project()
        unit = self._unit(project)

        self.assertTrue(unit.is_available_for_sale)
        self.assertEqual(self.Commercial.visual_state(unit), 'available')
        self.assertEqual(unit.visual_state, 'available')

    def test_an_unreleased_unit_is_not_shown_as_available(self):
        """The defect, stated directly.

        0.4 read `property.state`, which for a brand-new unit is `available`,
        and coloured an unreleased unit green.
        """
        project = self._project()
        unit = self._unit(project, released=False)

        self.assertEqual(unit.state, 'available')       # legacy field
        self.assertFalse(unit.is_available_for_sale)    # Developer's answer
        self.assertEqual(unit.visual_state, 'unreleased')

    def test_a_blocked_unit_reads_as_blocked(self):
        project = self._project()
        unit = self._unit(project)
        self.env['realestate.unit.block'].create({
            'property_id': unit.id,
            'reason': 'management',
        })
        unit.invalidate_recordset()

        self.assertEqual(unit.visual_state, 'blocked')

    def test_a_project_that_is_not_selling_hides_its_inventory(self):
        project = self._project()
        unit = self._unit(project)
        self.assertEqual(unit.visual_state, 'available')

        project.commercial_state = 'planning'
        unit.invalidate_recordset()

        self.assertEqual(unit.visual_state, 'unreleased')

    def test_the_stored_field_is_searchable(self):
        """Filters (M12) group on this, so it has to be a column."""
        project = self._project()
        available = self._unit(project)
        self._unit(project, released=False)

        found = self.Property.search([('visual_state', '=', 'available'),
                                      ('project_id', '=', project.id)])

        self.assertEqual(found, available)

    def test_a_non_unit_is_never_for_sale(self):
        project = self._project()
        building = self._building(project)

        self.assertEqual(building.visual_state, 'not_for_sale')


@tagged('post_install', '-at_install')
class TestVisualPrice(VisualCommon):
    """`base_price` is never published to anybody."""

    def test_an_internal_audience_gets_the_developer_list_price(self):
        project = self._project()
        unit = self._unit(project, price=1000000.0)

        price = self.Commercial.visual_price(unit, 'internal')

        self.assertEqual(price, unit.list_price_developer)

    def test_a_public_audience_gets_the_public_price(self):
        project = self._project()
        unit = self._unit(project, price=1000000.0)

        self.assertEqual(self.Commercial.visual_price(unit, 'public'),
                         unit._public_price())

    def test_an_unreleased_unit_has_no_public_price(self):
        """Guessing one leaks the pricing of a tower that has not launched."""
        project = self._project()
        unit = self._unit(project, released=False, price=1000000.0)

        self.assertEqual(self.Commercial.visual_price(unit, 'public'), 0.0)

    def test_a_broker_is_treated_as_an_external_audience(self):
        project = self._project()
        unit = self._unit(project, released=False, price=1000000.0)

        self.assertEqual(self.Commercial.visual_price(unit, 'broker'), 0.0)


@tagged('post_install', '-at_install')
class TestUnitPayload(VisualCommon):
    """What each audience is actually handed."""

    def test_the_payload_carries_the_visual_state_not_the_legacy_state(self):
        project = self._project()
        unit = self._unit(project, released=False)

        entry = self.Commercial.unit_payload(unit, 'internal')[0]

        self.assertEqual(entry['visual_state'], 'unreleased')
        self.assertNotIn('state', entry)
        self.assertNotIn('base_price', entry)

    def test_an_internal_audience_learns_why_a_unit_is_unavailable(self):
        project = self._project()
        unit = self._unit(project, released=False)

        entry = self.Commercial.unit_payload(unit, 'internal')[0]

        self.assertEqual(entry['unavailable_reason'], 'not_released')
        self.assertIn('commercial_status', entry)

    def test_a_public_audience_does_not(self):
        """"Blocked — VIP hold for Mr X" is exactly what M20 forbids."""
        project = self._project()
        unit = self._unit(project, released=False)

        entry = self.Commercial.unit_payload(unit, 'public')[0]

        self.assertNotIn('unavailable_reason', entry)
        self.assertNotIn('commercial_status', entry)
        self.assertNotIn('is_released', entry)

    def test_an_unknown_audience_is_treated_as_public(self):
        """Failing towards the least privileged reading."""
        project = self._project()
        unit = self._unit(project, released=False)

        entry = self.Commercial.unit_payload(unit, 'whoever')[0]

        self.assertNotIn('unavailable_reason', entry)

    def test_every_entry_carries_a_colour_and_an_icon(self):
        """M25 — status is never conveyed by colour alone."""
        project = self._project()
        unit = self._unit(project)

        entry = self.Commercial.unit_payload(unit, 'internal')[0]

        self.assertTrue(entry['color'])
        self.assertTrue(entry['icon'])

    def test_a_colour_override_still_wins(self):
        project = self._project()
        unit = self._unit(project, maquette_color_override='#123456')

        entry = self.Commercial.unit_payload(unit, 'internal')[0]

        self.assertEqual(entry['color'], '#123456')

    def test_price_per_sqm_is_computed_and_never_divides_by_zero(self):
        project = self._project()
        priced = self._unit(project, price=1200000.0)
        no_area = self._unit(project, price=1000000.0, area_sqm=0.0)

        entries = self.Commercial.unit_payload(priced | no_area, 'internal')
        by_id = {e['id']: e for e in entries}

        self.assertEqual(by_id[priced.id]['price_per_sqm'], 10000.0)
        self.assertEqual(by_id[no_area.id]['price_per_sqm'], 0.0)

    def test_the_payload_carries_the_filter_criteria(self):
        """M12 filters read from this, not from a frontend-only store."""
        project = self._project()
        unit = self._unit(project)

        entry = self.Commercial.unit_payload(unit, 'public')[0]

        for key in ('bedrooms', 'bathrooms', 'area_sqm', 'floor',
                    'property_type', 'price', 'price_per_sqm'):
            self.assertIn(key, entry)


@tagged('post_install', '-at_install')
class TestReservationEligibility(VisualCommon):
    """Rule 2 — the button is offered on Developer's answer, not the legacy one."""

    def test_a_released_unit_may_start_a_reservation(self):
        project = self._project()
        unit = self._unit(project)

        self.assertTrue(self.Commercial.may_start_reservation(unit))

    def test_an_unreleased_unit_may_not(self):
        """0.4 offered Reserve here, and Developer's form then refused it."""
        project = self._project()
        unit = self._unit(project, released=False)

        self.assertFalse(self.Commercial.may_start_reservation(unit))
        self.assertFalse(
            self.Commercial.unit_payload(unit, 'internal')[0]['reservable'])

    def test_a_blocked_unit_may_not(self):
        project = self._project()
        unit = self._unit(project)
        self.env['realestate.unit.block'].create({
            'property_id': unit.id, 'reason': 'legal'})
        unit.invalidate_recordset()

        self.assertFalse(self.Commercial.may_start_reservation(unit))


@tagged('post_install', '-at_install')
class TestLegend(VisualCommon):
    """M25 — the legend is built from the same table as the colours."""

    def test_the_legend_covers_every_state_by_default(self):
        from ..models.visual_states import VISUAL_STATE

        legend = self.Commercial.legend()

        self.assertEqual(len(legend), len(VISUAL_STATE))

    def test_the_legend_can_be_narrowed_to_what_is_on_screen(self):
        legend = self.Commercial.legend(['available', 'sold'])

        self.assertEqual([e['state'] for e in legend], ['available', 'sold'])

    def test_every_entry_has_a_label_and_an_icon(self):
        for entry in self.Commercial.legend():
            with self.subTest(state=entry['state']):
                self.assertTrue(entry['label'])
                self.assertTrue(entry['icon'])
                self.assertTrue(entry['color'])
