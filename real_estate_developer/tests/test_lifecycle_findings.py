# -*- coding: utf-8 -*-
"""Regressions for the developer lifecycle run (17 Sep 2026).

A scripted end-to-end run drove a project from launch to handover through the
forms and recorded where the product let a deal go wrong. Each class below
pins one of those findings.
"""

from datetime import timedelta

from lxml import etree

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import tagged
from odoo.tools.safe_eval import safe_eval

from .common import DeveloperCommon
from .test_contract import ContractCommon
from .test_contract_changes import ChangeCommon


def visible_header_buttons(record):
    """Names of the header buttons the form shows for ``record``, in order."""
    record.invalidate_recordset()
    arch = etree.fromstring(record.get_view(view_type='form')['arch'])
    names = {n for n in arch.xpath('//field/@name') if n in record._fields}
    values = record.read(list(names), load=False)[0] if names else {}
    values.update({'id': record.id, 'uid': record.env.uid, 'context': {}})
    shown = []
    for button in arch.xpath('//header/button'):
        expr = button.get('invisible')
        if not expr or not safe_eval(expr, values):
            shown.append(button.get('name'))
    return shown


class FindingsCommon(ChangeCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.other_buyer = cls.env['res.partner'].create({'name': 'Other Buyer'})
        cls.Promotion = cls.env['realestate.promotion']
        cls.Rule = cls.env['realestate.commercial.approval.rule']
        cls.Request = cls.env['realestate.commercial.approval.request']
        cls.approver = cls.env['res.users'].create({
            'name': 'Carla Commercial', 'login': 'finding_approver',
            'email': 'carla@example.com',
            'company_id': cls.company.id,
            'company_ids': [(6, 0, [cls.company.id])],
            'groups_id': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('real_estate_developer.group_dev_manager').id])],
        })

    def _hold(self, unit=None, partner=None, **vals):
        values = {
            'property_id': (unit or self.unit).id,
            'partner_id': (partner or self.buyer).id,
        }
        values.update(vals)
        return self.Reservation.create(values)

    def _promotion(self, **vals):
        values = {
            'name': 'Launch', 'company_id': self.company.id,
            'project_id': self.project.id,
            'discount_type': 'percent', 'discount_value': 1.0,
        }
        values.update(vals)
        return self.Promotion.create(values)

    def _active_promotion(self, **vals):
        promo = self._promotion(**vals)
        promo.action_submit()
        promo.action_approve()
        return promo

    def _above_band_rule(self, action_type='discount', **vals):
        values = {
            'name': 'Above 2%% (%s)' % action_type,
            'company_id': self.company.id, 'action_type': action_type,
            'threshold_type': 'percent', 'min_value': 2.0001, 'max_value': 0.0,
            'approver_user_id': self.approver.id,
        }
        values.update(vals)
        return self.Rule.create(values)


# ----------------------------------------------------------------------
# 1 & 2 — the same unit sold twice
# ----------------------------------------------------------------------
@tagged('post_install', '-at_install', 'atmta_developer')
class TestNoDoubleSale(FindingsCommon):

    def test_signing_on_a_unit_another_deal_holds_is_refused(self):
        held = self._hold(partner=self.other_buyer)
        contract = self._contract(payment_plan_id=self._plan().id)
        with self.assertRaises(UserError) as err:
            contract.action_sign()
        self.assertIn(held.name, str(err.exception))
        self.assertEqual(contract.state, 'draft')
        self.assertFalse(contract.installment_ids)

    def test_signing_on_a_unit_that_is_not_for_sale_is_refused(self):
        unreleased = self.units[1]
        contract = self._contract(property_id=unreleased.id,
                                  payment_plan_id=self._plan().id)
        with self.assertRaises(UserError):
            contract.action_sign()
        self.assertEqual(contract.state, 'draft')

    def test_signing_from_the_holding_reservation_still_works(self):
        res = self._hold(booking_amount_required=0.0)
        res.action_confirm_booking()
        contract = self._contract(reservation_id=res.id, sale_price=res.net_price,
                                  payment_plan_id=self._plan().id)
        contract.action_sign()
        self.assertEqual(contract.state, 'signed')

    def test_a_draft_contract_from_a_booking_keeps_the_unit_committed(self):
        res = self._hold(booking_amount_required=0.0, proposed_price=1000000.0)
        res.action_confirm_booking()
        res.action_create_sale_contract()
        self.assertEqual(res.state, 'confirmed')
        self.assertTrue(res.sale_contract_id)
        self.assertEqual(self.unit.commercial_status, 'reserved')
        with self.assertRaises(UserError):
            self._hold(partner=self.other_buyer)

    def test_cancelling_that_draft_contract_frees_the_unit(self):
        res = self._hold(booking_amount_required=0.0, proposed_price=1000000.0)
        res.action_confirm_booking()
        res.action_create_sale_contract()
        res.sale_contract_id.action_cancel()
        self.assertEqual(self.unit.commercial_status, 'available')


# ----------------------------------------------------------------------
# 4 — a price change must not rescale fees
# ----------------------------------------------------------------------
@tagged('post_install', '-at_install', 'atmta_developer')
class TestPriceChangeLeavesFeesAlone(FindingsCommon):

    def _approved(self, contract, kind, **vals):
        amendment = self.Amendment.create(dict(
            contract_id=contract.id, amendment_type=kind,
            reason='Agreed with the buyer', **vals))
        amendment.action_submit()
        amendment.action_approve()
        return amendment

    def test_the_transfer_fee_keeps_its_amount(self):
        contract = self._signed_contract()
        self._approved(contract, 'buyer_change',
                       new_partner_id=self.other_buyer.id,
                       fee_amount=5000.0).action_apply()
        fee = contract.installment_ids.filtered(
            lambda i: i.description == 'Transfer fee')
        self.assertEqual(fee.current_amount, 5000.0)

        self._approved(contract, 'price_change', new_price=900000.0,
                       financial_effect=-100000.0).action_apply()
        contract.invalidate_recordset()
        self.assertEqual(fee.current_amount, 5000.0,
                         "a fee is not part of the price")
        self.assertAlmostEqual(contract.scheduled_amount, 905000.0, places=2)


# ----------------------------------------------------------------------
# 5 — above-band approvals must have a way forward
# ----------------------------------------------------------------------
@tagged('post_install', '-at_install', 'atmta_developer')
class TestApprovalCanBeRequested(FindingsCommon):

    def _approve_as_approver(self, request):
        request.with_user(self.approver).action_approve()
        self.assertEqual(request.state, 'approved')

    def test_a_promotion_above_band_raises_its_own_request(self):
        self._above_band_rule()
        promo = self._promotion(discount_value=3.0)
        promo.action_submit()
        with self.assertRaises(UserError):
            promo.action_approve()
        self.assertTrue(promo.approval_request_available)
        self.assertIn('action_request_approval', visible_header_buttons(promo))

        promo.action_request_approval()
        request = self.Request.search([
            ('res_model', '=', promo._name), ('res_id', '=', promo.id)])
        self.assertEqual(len(request), 1)
        self.assertEqual((request.res_model, request.res_id, request.state),
                         ('realestate.promotion', promo.id, 'pending'))
        self.assertTrue(request.rule_id)
        promo.invalidate_recordset(['approval_request_available'])
        self.assertFalse(promo.approval_request_available)
        with self.assertRaises(UserError):
            promo.action_request_approval()

        self._approve_as_approver(request)
        promo.action_approve()
        self.assertEqual(promo.state, 'active')

    def test_nothing_to_request_is_refused(self):
        promo = self._promotion(discount_value=1.0)
        promo.action_submit()
        self.assertFalse(promo.approval_request_available)
        with self.assertRaises(UserError):
            promo.action_request_approval()

    def test_a_reservation_discount_above_band_raises_its_own_request(self):
        self._above_band_rule()
        res = self._hold(booking_amount_required=0.0, discount_amount=30000.0)
        with self.assertRaises(UserError):
            res.action_confirm_booking()
        self.assertTrue(res.approval_request_available)
        res.action_request_approval()
        self._approve_as_approver(res.approval_request_ids)
        res.action_confirm_booking()
        self.assertEqual(res.state, 'booked')

    def test_an_amendment_above_band_raises_its_own_request(self):
        self._above_band_rule('price_change', threshold_type='amount',
                              min_value=50000.0)
        contract = self._signed_contract()
        amendment = self.Amendment.create({
            'contract_id': contract.id, 'amendment_type': 'price_change',
            'reason': 'Renegotiated', 'new_price': 900000.0,
            'financial_effect': -100000.0,
        })
        amendment.action_submit()
        with self.assertRaises(UserError):
            amendment.action_approve()
        self.assertTrue(amendment.approval_request_available)
        amendment.action_request_approval()
        request = self.Request.search([
            ('res_model', '=', amendment._name), ('res_id', '=', amendment.id)])
        self._approve_as_approver(request)
        amendment.action_approve()
        self.assertEqual(amendment.state, 'approved')

    def test_a_manually_raised_request_finds_its_rule(self):
        rule = self._above_band_rule()
        request = self.Request.create({
            'action_type': 'discount', 'company_id': self.company.id,
            'requested_value': 3.0, 'reason': 'Raised from the menu',
        })
        request.action_submit()
        self.assertEqual(request.rule_id, rule)
        self._approve_as_approver(request)

    def test_a_manual_request_nothing_governs_is_refused(self):
        request = self.Request.create({
            'action_type': 'discount', 'company_id': self.company.id,
            'requested_value': 3.0,
        })
        with self.assertRaises(UserError):
            request.action_submit()


# ----------------------------------------------------------------------
# 7 — promotion state, validity and cap
# ----------------------------------------------------------------------
@tagged('post_install', '-at_install', 'atmta_developer')
class TestPromotionIsEnforced(FindingsCommon):

    def test_a_draft_promotion_cannot_be_applied(self):
        promo = self._promotion(discount_value=10.0)
        with self.assertRaises(UserError):
            self._hold(promotion_id=promo.id)

    def test_an_expired_window_cannot_be_applied(self):
        promo = self._active_promotion()
        yesterday = fields.Date.context_today(promo) - timedelta(days=1)
        promo.write({'date_start': yesterday - timedelta(days=10),
                     'date_end': yesterday})
        with self.assertRaises(UserError):
            self._hold(promotion_id=promo.id)

    def test_use_count_follows_the_deals(self):
        promo = self._active_promotion(max_uses=2)
        res = self._hold(promotion_id=promo.id)
        self.assertEqual(promo.use_count, 1)
        self.assertEqual(promo.remaining_uses, 1)
        res._cancel('other')
        self.assertEqual(promo.use_count, 0)

    def test_a_capped_promotion_cannot_be_used_beyond_its_cap(self):
        promo = self._active_promotion(max_uses=1)
        first = self._hold(promotion_id=promo.id)
        self.assertEqual(first.promotion_amount, 10000.0,
                         "the first use is within the cap")
        second_unit = self.units[1]
        self._release(second_unit)
        with self.assertRaises(UserError):
            self._hold(unit=second_unit, partner=self.other_buyer,
                       promotion_id=promo.id)

    def test_the_field_offers_active_promotions_only(self):
        domain = self.Reservation._fields['promotion_id'].domain
        self.assertIn('active', str(domain))


# ----------------------------------------------------------------------
# 9 — a unit created under a project belongs to the project's company
# ----------------------------------------------------------------------
@tagged('post_install', '-at_install', 'atmta_developer')
class TestUnitCompanyFromProject(FindingsCommon):

    def test_unit_takes_the_project_company(self):
        unit = self.Property.create({
            'name': 'No company unit', 'property_code': 'NCU-1',
            'hierarchy_level': 'unit', 'project_id': self.project.id,
        })
        self.assertEqual(unit.company_id, self.project.company_id)


# ----------------------------------------------------------------------
# 10 — editing the duration must not undo an extension
# ----------------------------------------------------------------------
@tagged('post_install', '-at_install', 'atmta_developer')
class TestExtensionSurvivesDurationEdit(FindingsCommon):

    def test_extension_is_kept(self):
        res = self._hold()
        res.action_extend_hold(hours=24.0)
        res.hold_duration_hours = 50.0
        self.assertEqual(
            res.hold_expiry_at,
            res.hold_started_at + timedelta(hours=50.0 + 24.0))


# ----------------------------------------------------------------------
# 11 — the legacy Cancel buttons must not bypass the structured flows
# ----------------------------------------------------------------------
@tagged('post_install', '-at_install', 'atmta_developer')
class TestLegacyCancelRespectsTheFlows(FindingsCommon):

    def test_plain_cancel_with_booking_money_is_refused(self):
        res = self._hold(booking_amount_required=20000.0,
                         booking_amount_received=20000.0)
        with self.assertRaises(UserError):
            res.action_cancel()
        self.assertEqual(res.state, 'hold')

    def test_plain_cancel_without_money_still_works(self):
        res = self._hold(booking_amount_required=0.0)
        res.action_cancel()
        self.assertEqual(res.state, 'cancelled')

    def test_plain_cancel_on_a_signed_contract_is_refused(self):
        contract = self._signed_contract()
        with self.assertRaises(UserError):
            contract.action_cancel()
        self.assertEqual(contract.state, 'signed')
        self.assertFalse(contract.installment_ids.filtered('is_cancelled'))
        self.assertNotIn('action_cancel', visible_header_buttons(contract))

    def test_plain_cancel_on_a_draft_contract_still_works(self):
        contract = self._contract()
        contract.action_cancel()
        self.assertEqual(contract.state, 'cancelled')


# ----------------------------------------------------------------------
# 12 — contract balances read the instalments
# ----------------------------------------------------------------------
@tagged('post_install', '-at_install', 'atmta_developer')
class TestContractBalancesReadInstalments(FindingsCommon):

    def test_payment_shows_on_the_contract(self):
        contract = self._signed_contract()
        paid = self._pay_first(contract)
        contract.invalidate_recordset()
        self.assertEqual(contract.paid_amount, paid.paid_amount)
        self.assertEqual(contract.invoiced_amount, paid.invoiced_amount)
        self.assertAlmostEqual(contract.balance_due,
                               1000000.0 - paid.paid_amount, places=2)
        self.assertAlmostEqual(contract.progress, 20.0, places=2)

    def test_draft_contract_still_owes_its_price(self):
        contract = self._contract()
        self.assertEqual(contract.balance_due, 1000000.0)
        self.assertEqual(contract.paid_amount, 0.0)


# ----------------------------------------------------------------------
# 13 & 14 — pricing edges, promotion cron, settlement rounding
# ----------------------------------------------------------------------
@tagged('post_install', '-at_install', 'atmta_developer')
class TestPricingEdges(FindingsCommon):

    def _book(self):
        return self.env['realestate.price.book'].create({
            'name': 'Launch', 'project_id': self.project.id,
            'company_id': self.company.id,
        })

    def test_a_price_book_line_outside_the_project_is_refused(self):
        other_project = self.Project.create({
            'name': 'Elsewhere', 'code': 'EW', 'company_id': self.company.id})
        foreign = self._make_units(count=1, prefix='EW-U', phase=False,
                                   project=other_project)
        with self.assertRaises(ValidationError):
            self.env['realestate.price.book.line'].create({
                'price_book_id': self._book().id, 'property_id': foreign.id,
                'base_amount': 1.0,
            })

    def test_floor_premium_reads_the_unit_floor_number(self):
        book = self._book()
        rule = self.env['realestate.price.rule'].create({
            'name': 'Floor 5+', 'price_book_id': book.id,
            'premium_type': 'floor', 'calculation_type': 'fixed',
            'value': 1000.0, 'floor_from': 5,
        })
        unit = self.units[2]
        unit.floor_number = 7
        self.assertTrue(rule._applies_to(unit))
        unit.floor_number = 3
        self.assertFalse(rule._applies_to(unit))

    def test_promotion_expiry_is_scheduled(self):
        cron = self.env.ref('real_estate_developer.cron_expire_promotions')
        self.assertTrue(cron.active)
        self.assertIn('_cron_expire_promotions', cron.code)

    def test_settlement_figures_are_rounded(self):
        contract = self._contract()
        today = fields.Date.context_today(contract)
        self.Installment.create([
            {'sale_contract_id': contract.id, 'original_amount': amount,
             'date_due': today, 'sequence': seq}
            for seq, amount in ((1, 0.1), (2, 0.2))])
        preview = contract._settlement_preview()
        self.assertEqual(preview['outstanding_amount'], 0.3)
        self.assertEqual(preview['net_settlement'], 0.3)


# ----------------------------------------------------------------------
# UX — header buttons match what the methods accept
# ----------------------------------------------------------------------
@tagged('post_install', '-at_install', 'atmta_developer')
class TestHeaderButtons(FindingsCommon):

    def test_sign_is_offered_wherever_signing_is_accepted(self):
        contract = self._contract(payment_plan_id=self._plan().id)
        contract.action_submit_for_approval()
        self.assertIn('action_sign', visible_header_buttons(contract))
        contract.action_send_for_signature()
        self.assertIn('action_sign', visible_header_buttons(contract))

    def test_hand_over_is_offered_on_an_active_contract(self):
        contract = self._signed_contract()
        contract.action_activate()
        self.assertIn('action_handover', visible_header_buttons(contract))

    def test_confirm_booking_is_offered_while_awaiting_payment(self):
        res = self._hold(booking_amount_required=20000.0)
        res.action_request_booking_payment()
        self.assertIn('action_confirm_booking', visible_header_buttons(res))

    def test_a_hold_shows_one_extend_and_one_cancel(self):
        res = self._hold()
        shown = visible_header_buttons(res)
        self.assertEqual(shown.count('action_extend_hold'), 1)
        self.assertEqual(
            len([b for b in shown
                 if b in ('action_cancel', 'action_open_cancellation')]), 1)


#: The two Developer screens the screen sweep of 19 September 2026 could not
#: open as a user holding nothing but the Read-only role it offers them to.
DEV_READ_ONLY_SCREENS = {
    'Development & Sales/Projects & Inventory/Units':
        'real_estate_developer.action_developer_units',
    'Development & Sales/Reporting/Unit Availability':
        'real_estate_developer.action_report_unit_availability',
}


def _screen_spec(model, names):
    """The ``web_read`` specification the client sends for ``names``.

    Relational fields are asked for their ``display_name``, which is what
    makes the client read the comodel -- and what makes a missing ACL row on
    the comodel surface here rather than in production.
    """
    spec = {}
    for name in names:
        field = model._fields.get(name)
        if field is None:
            continue
        spec[name] = ({'fields': {'display_name': {}}}
                      if field.type in ('many2one', 'many2many', 'one2many') else {})
    return spec


@tagged('post_install', '-at_install')
class TestDeveloperReadOnlyOpensItsScreens(DeveloperCommon):
    """The unit screens show the unit's gallery, so Read-only must read it.

    The sweep opened every menu twice: as an administrator, and as a minimal
    user holding only the group the menu is offered to. Both Developer unit
    screens raised ``AccessError`` on ``property.image`` for the Read-only
    role: the unit form's Gallery group carries
    ``property_Attachment_media_ids``, a one2many to that model, and the role
    that is offered the screen could not read the rows behind it.

    These tests do what the sweep did, and what the web client does when the
    user clicks the menu: read the action, ``get_views`` for its view modes,
    then ``web_search_read`` every field the views name and ``web_read`` the
    first few records.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Only the Read-only role: every other Developer role implies it, so a
        # user holding one of those would hide the gap being tested.
        cls.readonly = cls.env['res.users'].create({
            'name': 'Dana Readonly', 'login': 'sweep_dev_readonly',
            'company_id': cls.company.id, 'company_ids': [(6, 0, [cls.company.id])],
            'groups_id': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('real_estate_developer.group_dev_readonly').id])],
        })
        # A unit with a picture, so the gallery has a row to read.
        cls.unit_image = cls.env['property.image'].create({
            'name': 'Living room',
            'property_id': cls.units[0].id,
        })

    def _open_screen(self, action):
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
        spec = _screen_spec(model, names)
        domain = safe_eval(action.domain or '[]', {'uid': self.readonly.id})
        rows = model.web_search_read(domain, spec, limit=20)
        ids = [row['id'] for row in rows['records']][:5]
        if ids:
            model.browse(ids).web_read(spec)

    def test_the_read_only_role_opens_every_unit_screen_it_is_offered(self):
        failures = []
        for label, xmlid in DEV_READ_ONLY_SCREENS.items():
            self.env.invalidate_all()
            try:
                self._open_screen(self.env.ref(xmlid))
            except AccessError as error:
                failures.append('%s: %s' % (label, str(error).splitlines()[0]))
        self.assertFalse(
            failures,
            "The Developer Read-only role is offered these screens and cannot "
            "read them:\n  " + "\n  ".join(failures))

    def test_a_unit_screen_shows_the_unit_gallery(self):
        """The finding itself: the gallery on the unit form."""
        self.env['property.image'].with_user(self.readonly).web_search_read(
            [], {'display_name': {}, 'property_id': {'fields': {'display_name': {}}}},
            limit=5)

    def test_the_read_only_role_stays_read_only_on_the_gallery(self):
        images = self.env['property.image'].with_user(self.readonly)
        self.assertTrue(images.has_access('read'))
        for operation in ('write', 'create', 'unlink'):
            self.assertFalse(
                images.has_access(operation),
                "The Developer Read-only role may not %s a property image."
                % operation)


@tagged('post_install', '-at_install')
class TestTheSharedProjectFormOutsideDeveloper(DeveloperCommon):
    """The commercial value on the shared project form is Developer's alone.

    ``realestate.project`` belongs to Project Core, and Construction, Visual,
    Plan and this application all extend its form. The sweep opened
    Construction/Projects as a Construction User and was refused
    ``realestate.sale.contract``: ``contracted_value`` and
    ``contracted_deal_count`` sum the project's live sale contracts, and this
    application is the only one that grants read on them.

    The Construction role cannot be built in this suite -- Developer installs
    without the Construction application -- so the user below stands in for
    it, holding exactly what Construction grants: read on the project and read
    on the property.
    """

    DEVELOPER_ONLY_NODES = ('contracted_value', 'contracted_deal_count')

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.outsider = cls.env['res.users'].create({
            'name': 'Cora Construction', 'login': 'sweep_project_outsider',
            'company_id': cls.company.id, 'company_ids': [(6, 0, [cls.company.id])],
            'groups_id': [(6, 0, [cls.env.ref('base.group_user').id])],
        })
        # Exactly what the Construction application grants a site user:
        # project, phase and boundary (real_estate_construction's own rows)
        # plus the property read the construction floor adds.
        for model in ('realestate.project', 'realestate.phase',
                      'realestate.project.boundary.point', 'realestate.property'):
            cls.env['ir.model.access'].create({
                'name': 'sweep outsider reads %s' % model,
                'model_id': cls.env['ir.model']._get_id(model),
                'group_id': cls.env.ref('base.group_user').id,
                'perm_read': True, 'perm_write': False,
                'perm_create': False, 'perm_unlink': False,
            })
        cls.dev_readonly = cls.env['res.users'].create({
            'name': 'Dana Developer', 'login': 'sweep_project_dev_readonly',
            'company_id': cls.company.id, 'company_ids': [(6, 0, [cls.company.id])],
            'groups_id': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('real_estate_developer.group_dev_readonly').id])],
        })
        cls.units[0].write({'base_price': 100000.0})

    def test_a_user_outside_developer_is_not_offered_the_sale_value(self):
        arch = etree.fromstring(
            self.env['realestate.project'].with_user(self.outsider).get_view(
                view_type='form')['arch'])
        offered = {node.get('name') for node in arch.iter('field')}
        for name in self.DEVELOPER_ONLY_NODES:
            self.assertNotIn(
                name, offered,
                "%s is offered to a user outside the Developer application, "
                "and reading it reads realestate.sale.contract, which only "
                "that application grants." % name)

    def test_a_developer_role_keeps_the_sale_value(self):
        """The group is scoped, not removed."""
        arch = etree.fromstring(
            self.env['realestate.project'].with_user(self.dev_readonly).get_view(
                view_type='form')['arch'])
        offered = {node.get('name') for node in arch.iter('field')}
        for name in self.DEVELOPER_ONLY_NODES:
            self.assertIn(name, offered, name)

    def test_a_user_outside_developer_can_open_the_project_screen(self):
        """The whole point: the shared screen loads for them."""
        model = self.env['realestate.project'].with_user(self.outsider)
        views = model.get_views([(False, 'list'), (False, 'kanban'),
                                 (False, 'form'), (False, 'search')])
        names = set()
        for mode in ('list', 'kanban', 'form'):
            arch = views['views'].get(mode, {}).get('arch')
            if arch:
                names |= {node.get('name') for node in etree.fromstring(arch).iter('field')}
        spec = _screen_spec(model, names)
        rows = model.web_search_read([], spec, limit=20)
        ids = [row['id'] for row in rows['records']][:5]
        self.assertTrue(ids)
        model.browse(ids).web_read(spec)


@tagged('post_install', '-at_install')
class TestCollectedThisMonthIsMoneyReceived(ContractCommon):
    """"Revenue MTD / collected" must measure cash in, not obligations due.

    The KPI summed ``paid_amount`` over the instalments whose ``date_due``
    fell in the current month. An instalment carries no payment date --
    ``paid_amount`` is derived from its invoice's residual -- so that domain
    answers "how much of what fell due has been paid", while the screen labels
    it "collected". Money banked today against an instalment due in two years
    moved the figure by nothing, and money banked two months ago against an
    instalment falling due this month moved it in full.

    The Executive dashboard was corrected first
    (``atmta_executive_app/models/executive_dashboard.py``,
    ``_collected_tiles``) and this follows it exactly, so the two screens
    answer the same question: inbound customer payments DATED in the month,
    excluding draft/cancelled/rejected, narrowed to the payments reconciled
    against sale instalment invoices so it stays a sales figure rather than
    every customer receipt.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Dashboard = cls.env['realestate.developer.dashboard']

    def _data(self):
        return self.Dashboard.with_context(
            allowed_company_ids=[self.company.id]).get_data()

    def _signed_contract(self):
        contract = self._contract(payment_plan_id=self._plan().id)
        contract.action_sign()
        return contract

    def _settle(self, installment, when=None):
        """Invoice an instalment and pay it, the way the wizard does."""
        installment.action_generate_invoice()
        invoice = installment.move_id
        payment = self.env['account.payment'].create({
            'payment_type': 'inbound', 'partner_type': 'customer',
            'partner_id': invoice.partner_id.id,
            'amount': invoice.amount_total,
            'currency_id': invoice.currency_id.id,
            'company_id': invoice.company_id.id,
            'date': when or fields.Date.context_today(invoice),
        })
        payment.action_post()
        lines = (invoice.line_ids + payment.move_id.line_ids).filtered(
            lambda line: line.account_id.account_type == 'asset_receivable'
            and not line.reconciled)
        lines.reconcile()
        installment.invalidate_recordset()
        return payment

    # ------------------------------------------------------------------
    def test_money_received_this_month_counts_however_far_off_it_fell_due(self):
        contract = self._signed_contract()
        instalment = contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))[0]
        payment = self._settle(instalment)
        # Due in two years, banked today. The date that matters is the one the
        # money carries.
        instalment.date_due = fields.Date.context_today(instalment) + timedelta(days=730)
        instalment.invalidate_recordset()

        self.assertAlmostEqual(
            self._data()['kpis']['paid_mtd'], payment.amount, places=2,
            msg="money received this month must be collected this month, "
                "whenever the instalment it settles falls due")

    def test_money_received_in_an_earlier_month_is_not_collected_this_month(self):
        contract = self._signed_contract()
        today = fields.Date.context_today(contract)
        instalment = contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))[0]
        # Falls due this month, but the cash arrived long before it.
        self._settle(instalment, when=today - timedelta(days=40))
        instalment.date_due = today
        instalment.invalidate_recordset()

        self.assertAlmostEqual(
            self._data()['kpis']['paid_mtd'], 0.0, places=2,
            msg="money that arrived in an earlier month is not this month's "
                "collection, however this month's obligations are dated")

    def test_an_unpaid_obligation_is_never_collection(self):
        contract = self._signed_contract()
        instalment = contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))[0]
        instalment.action_generate_invoice()
        instalment.date_due = fields.Date.context_today(instalment)
        instalment.invalidate_recordset()

        self.assertAlmostEqual(self._data()['kpis']['paid_mtd'], 0.0, places=2)

    # ------------------------------------------------------------------
    def test_the_figure_equals_the_records_its_drill_down_opens(self):
        contract = self._signed_contract()
        instalment = contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))[0]
        self._settle(instalment)

        dashboard = self.Dashboard.with_context(allowed_company_ids=[self.company.id])
        action = dashboard.action_collected_mtd()
        opened = self.env[action['res_model']].search(action['domain'])

        self.assertTrue(opened, "the drill-down opened nothing")
        self.assertAlmostEqual(
            sum(opened.mapped('amount')), dashboard.get_data()['kpis']['paid_mtd'],
            places=2,
            msg="the figure and the records behind it must be the same money")

    def test_a_customer_receipt_that_pays_no_instalment_is_not_counted(self):
        """It is a sales figure, not every receipt the company banks."""
        contract = self._signed_contract()
        instalment = contract.installment_ids.sorted(
            lambda i: (i.date_due, i.sequence))[0]
        expected = self._settle(instalment).amount
        stray = self.env['account.payment'].create({
            'payment_type': 'inbound', 'partner_type': 'customer',
            'partner_id': self.buyer.id, 'amount': 777000.0,
            'currency_id': self.company.currency_id.id,
            'company_id': self.company.id,
            'date': fields.Date.context_today(self.env['res.partner']),
        })
        stray.action_post()

        self.assertAlmostEqual(self._data()['kpis']['paid_mtd'], expected, places=2)

    # ------------------------------------------------------------------
    def test_the_figure_is_dropped_for_a_user_who_cannot_read_the_payments(self):
        """The dashboard convention: drop it, never show a misleading zero."""
        user = self.env['res.users'].create({
            'name': 'Dee Developer', 'login': 'dash_dev_no_accounting',
            'company_id': self.company.id, 'company_ids': [(6, 0, [self.company.id])],
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('real_estate_developer.group_dev_readonly').id])],
        })
        self.assertFalse(self.env['account.payment'].with_user(user).has_access('read'))
        data = self.Dashboard.with_user(user).with_context(
            allowed_company_ids=[self.company.id]).get_data()
        self.assertNotIn(
            'paid_mtd', data['kpis'],
            "a figure the user cannot open is dropped, not shown as zero")


@tagged('post_install', '-at_install')
class TestDeveloperManagerCanAddMedia(DeveloperCommon):
    """The unit form offers "Add Media"; the role it offers it to must be able
    to use it.

    The gallery (``property_Attachment_media_ids``, widget
    ``x2_many_media_viewer``) adds, edits and removes ``property.image`` rows
    through the property form. Read was granted to the Read-only role so the
    gallery would load; without write, create and unlink the widget's own
    buttons end in an access error for the role that owns the unit. In a
    database with Rental installed this was hidden -- Rental grants those
    rights -- so it only showed in a Developer-only install.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = cls.env['res.users'].create({
            'name': 'Mona Manager', 'login': 'media_dev_manager',
            'company_id': cls.company.id, 'company_ids': [(6, 0, [cls.company.id])],
            'groups_id': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('real_estate_developer.group_dev_manager').id])],
        })
        cls.readonly = cls.env['res.users'].create({
            'name': 'Rita Readonly', 'login': 'media_dev_readonly',
            'company_id': cls.company.id, 'company_ids': [(6, 0, [cls.company.id])],
            'groups_id': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('real_estate_developer.group_dev_readonly').id])],
        })

    def test_a_manager_adds_edits_and_removes_a_photo_from_the_unit_form(self):
        """Exactly the writes the gallery widget sends through the form."""
        unit = self.units[0].with_user(self.manager)

        unit.write({'property_Attachment_media_ids': [(0, 0, {'name': 'Facade'})]})
        image = unit.property_Attachment_media_ids
        self.assertEqual(image.name, 'Facade')

        unit.write({'property_Attachment_media_ids': [(1, image.id, {'name': 'Facade, dusk'})]})
        self.assertEqual(image.name, 'Facade, dusk')

        unit.write({'property_Attachment_media_ids': [(2, image.id)]})
        self.assertFalse(unit.property_Attachment_media_ids)

    def test_the_read_only_role_still_only_reads_the_gallery(self):
        image = self.env['property.image'].create({
            'name': 'Living room', 'property_id': self.units[0].id})
        images = self.env['property.image'].with_user(self.readonly)
        self.assertTrue(images.has_access('read'))
        for operation in ('write', 'create', 'unlink'):
            self.assertFalse(images.has_access(operation), operation)
        with self.assertRaises(AccessError):
            image.with_user(self.readonly).write({'name': 'Renamed'})
