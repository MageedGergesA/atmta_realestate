# -*- coding: utf-8 -*-
"""Phases 28–32 — the effects of a post-signature change, on the contract.

The wizards collect the intent and build a preview; these methods are what
actually move the deal. They live on the contract rather than in the wizards so
that an amendment applied from anywhere — a wizard, a script, a future API —
takes exactly the same path.

The rule every one of them obeys: **paid history is never rewritten.** Only
unpaid, uninvoiced, future obligations move. Posted invoices are reversed or
credited through Odoo, never deleted.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

#: Sequences of the obligations the change workflows raise on top of the plan.
#: They are how those lines are recognised afterwards: the instalment carries
#: no other marker.
SETTLEMENT_SEQUENCE = 9000
TRANSFER_FEE_SEQUENCE = 9500


class SaleContractChanges(models.Model):
    _inherit = 'realestate.sale.contract'

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------
    def _open_installments(self):
        """Obligations that may still be changed.

        Excludes anything cancelled, anything with money against it, and
        anything already invoiced — an invoiced obligation is an accounting
        document and is Odoo's to reverse, not ours to rewrite.
        """
        self.ensure_one()
        return self.installment_ids.filtered(
            lambda i: not i.is_cancelled
            and i.paid_amount <= 0.0001
            and not (i.move_id and i.move_id.state == 'posted'))

    def _settled_amount(self):
        self.ensure_one()
        return sum(self.installment_ids.mapped('paid_amount'))

    def _outstanding_amount(self):
        self.ensure_one()
        live = self.installment_ids.filtered(lambda i: not i.is_cancelled)
        return sum(live.mapped('current_amount')) - sum(live.mapped('paid_amount'))

    def _rebalance_schedule(self, amendment, new_total, adjustment_type, reason):
        """Make the live schedule add up to ``new_total`` once, without
        rewriting anything already billed.

        What the open instalments must cover is the new total less everything
        the contract keeps, and it keeps more than the money received. An
        instalment that is invoiced but unpaid is excluded from the open lines
        (it is an accounting document now), yet it is still owed. Netting off
        only cash collected charged it twice: a 1,000 unit with a retained,
        unpaid 400 invoice and an open 600 instalment re-cut the 600 to 1,000,
        leaving 1,400 owed on a 1,000 unit.

        Measured on ``current_amount``, the schedule's own basis, which is the
        basis ``sale_price`` is validated against. Cash received is not on that
        basis (it is read off invoice residuals, tax included), so it is
        reported, not subtracted.

        * The open lines are scaled through adjustments on ``amendment``; the
          rounding remainder lands on the last one, so the total is exact.
        * With nothing open to scale, an increase becomes one balancing
          instalment. Without it the increase would never be billed while the
          sale price already says it is owed.
        * A total below what is already invoiced or paid is refused. The
          excess sits on posted invoices, which only Accounting can credit;
          clamping the open balance to zero would leave the buyer owing more
          than the new price.
        * A contract with no live instalments has no schedule to rebalance.
          Nothing is raised, because such a contract is billed some other way
          (V1 invoiced the whole price at once).

        A transfer fee is not part of the price, so it is left out of both
        sides: it is neither kept against the new total nor re-cut to it. An
        early-settlement line is the price agreed to clear the deal; it is
        kept as it stands rather than re-scaled. Re-cutting both used to turn
        a 5,000 fee into 4,672.18 on a price cut.
        """
        self.ensure_one()
        currency = self.currency_id
        live = self.installment_ids.filtered(
            lambda i: not i.is_cancelled
            and i.sequence != TRANSFER_FEE_SEQUENCE)
        open_lines = self._open_installments().filtered(
            lambda i: i.sequence not in (TRANSFER_FEE_SEQUENCE,
                                         SETTLEMENT_SEQUENCE))
        retained_value = sum((live - open_lines).mapped('current_amount'))
        remaining = currency.round(new_total - retained_value)
        result = {
            'retained_value': retained_value,
            'remaining': max(remaining, 0.0),
            'open_installments': open_lines,
            'balancing_installment': self.env['realestate.sale.installment'],
        }
        if currency.compare_amounts(remaining, 0.0) < 0:
            raise UserError(_(
                "Contract %(contract)s already has %(retained)s invoiced or "
                "paid, which is more than the new total of %(total)s.\n\n"
                "Credit the excess in Accounting first, by reversing or "
                "crediting the invoices concerned. A schedule cannot be cut "
                "below what has already been billed."
            ) % {
                'contract': self.name,
                'retained': currency.format(retained_value),
                'total': currency.format(new_total),
            })
        if not live:
            return result

        open_total = sum(open_lines.mapped('current_amount'))
        if open_lines and not currency.is_zero(open_total):
            Adjustment = self.env['realestate.sale.installment.adjustment']
            ordered = open_lines.sorted(lambda i: (i.date_due, i.sequence, i.id))
            scale = remaining / open_total
            allotted = 0.0
            for line in ordered:
                if line == ordered[-1]:
                    amount_after = currency.round(remaining - allotted)
                else:
                    amount_after = currency.round(line.current_amount * scale)
                    allotted += amount_after
                Adjustment.create({
                    'installment_id': line.id,
                    'amendment_id': amendment.id,
                    'adjustment_type': adjustment_type,
                    'amount_after': amount_after,
                    'reason': reason,
                })
        elif currency.compare_amounts(remaining, 0.0) > 0:
            result['balancing_installment'] = self.env[
                'realestate.sale.installment'].create({
                    'sale_contract_id': self.id,
                    'sequence': max(self.installment_ids.mapped('sequence') or [0]) + 1,
                    'kind': 'installment',
                    'description': reason,
                    'original_amount': remaining,
                    'date_due': fields.Date.context_today(self),
                })
        return result

    # ==================================================================
    # Phase 28 — restructuring
    # ==================================================================
    def _apply_restructure(self, amendment, new_plan, effective_date=None):
        """Re-cut the remaining schedule under a new plan.

        Paid instalments and posted invoices are untouched; only the open
        balance is re-scheduled. The old schedule survives on the amendment's
        adjustments, so what changed is still legible afterwards.
        """
        self.ensure_one()
        open_lines = self._open_installments()
        if not open_lines:
            raise UserError(_(
                "Contract %s has no open instalments left to restructure. "
                "Everything remaining is invoiced or paid."
            ) % self.name)

        self._assert_no_blocking_checks(open_lines)

        remaining = sum(open_lines.mapped('current_amount'))
        if not remaining:
            raise UserError(_("There is no open balance to restructure."))

        rows = new_plan._generate_schedule(
            total_price=remaining,
            booking_date=effective_date or fields.Date.context_today(self),
            contract_date=self.contract_date,
            handover_date=(self.expected_handover_date
                           or (self.project_id.expected_handover_date
                               if self.project_id else False)),
        )
        new_plan._validate_schedule_total(rows, remaining)

        # Cancel the open lines through the adjustment trail, then raise the
        # replacements. Cancelling rather than editing keeps the original
        # obligations visible.
        Adjustment = self.env['realestate.sale.installment.adjustment']
        for line in open_lines:
            Adjustment.create({
                'installment_id': line.id,
                'amendment_id': amendment.id if amendment else False,
                'adjustment_type': 'reschedule',
                'amount_after': 0.0,
                'reason': _('Superseded by restructuring %s') % (
                    amendment.name if amendment else ''),
            })
            line.is_cancelled = True

        Installment = self.env['realestate.sale.installment']
        Installment.create([{
            'sale_contract_id': self.id,
            'sequence': row['sequence'],
            'kind': self._map_kind(row['kind']),
            'description': row['name'],
            'percent': row['percent'],
            'original_amount': row['amount'],
            'date_due': row['date_due'],
        } for row in rows])

        self.payment_plan_id = new_plan
        self.message_post(body=_(
            "Restructured: %s open instalment(s) totalling %s replaced by %s "
            "instalment(s) under plan %s."
        ) % (len(open_lines), remaining, len(rows), new_plan.display_name))
        return True

    # ==================================================================
    # Phase 29 — early settlement
    # ==================================================================
    def _settlement_preview(self, settlement_date=None, discount_amount=0.0,
                            fee_amount=0.0):
        """What it would cost the buyer to clear the contract today.

        Returns the figures rather than writing anything, so the wizard can
        show them and the user can decide.
        """
        self.ensure_one()
        eligible = self._open_installments()
        currency = self.currency_id
        # Rounded: a float sum of many instalments showed 869400.0000000001.
        outstanding = currency.round(sum(eligible.mapped('current_amount')))
        return {
            'eligible_installments': eligible,
            'eligible_count': len(eligible),
            'outstanding_amount': outstanding,
            'already_paid': self._settled_amount(),
            'discount_amount': discount_amount,
            'fee_amount': fee_amount,
            'net_settlement': max(currency.round(
                outstanding - discount_amount + fee_amount), 0.0),
            'settlement_date': settlement_date or fields.Date.context_today(self),
        }

    def _apply_settlement(self, amendment, discount_amount=0.0,
                          fee_amount=0.0, settlement_date=None):
        """Collapse the remaining schedule into one settlement obligation."""
        self.ensure_one()
        preview = self._settlement_preview(
            settlement_date, discount_amount, fee_amount)
        eligible = preview['eligible_installments']
        if not eligible:
            raise UserError(_(
                "Contract %s has nothing left to settle early — every "
                "remaining instalment is already invoiced or paid."
            ) % self.name)

        self._assert_no_blocking_checks(eligible)

        Adjustment = self.env['realestate.sale.installment.adjustment']
        for line in eligible:
            Adjustment.create({
                'installment_id': line.id,
                'amendment_id': amendment.id if amendment else False,
                'adjustment_type': 'settlement',
                'amount_after': 0.0,
                'reason': _('Cleared by early settlement %s') % (
                    amendment.name if amendment else ''),
            })
            line.is_cancelled = True

        self.env['realestate.sale.installment'].create({
            'sale_contract_id': self.id,
            'sequence': SETTLEMENT_SEQUENCE,
            'kind': 'installment',
            'description': _('Early settlement'),
            'original_amount': preview['net_settlement'],
            'date_due': preview['settlement_date'],
        })
        self.message_post(body=_(
            "Early settlement: %s outstanding, %s discount, %s fee → %s due on "
            "%s. %s future instalment(s) cleared."
        ) % (preview['outstanding_amount'], discount_amount, fee_amount,
             preview['net_settlement'], preview['settlement_date'],
             len(eligible)))
        return preview

    # ==================================================================
    # Phase 30 — cancellation / termination
    # ==================================================================
    def _cancellation_preview(self, penalty_amount=0.0, forfeit_amount=0.0):
        """The financial position if this contract were cancelled now."""
        self.ensure_one()
        live = self.installment_ids.filtered(lambda i: not i.is_cancelled)
        posted = live.filtered(
            lambda i: i.move_id and i.move_id.state == 'posted')
        paid = self._settled_amount()
        refundable = max(paid - penalty_amount - forfeit_amount, 0.0)
        return {
            'paid_amount': paid,
            'outstanding_amount': self._outstanding_amount(),
            'future_installments': self._open_installments(),
            'posted_invoices': posted.mapped('move_id'),
            'penalty_amount': penalty_amount,
            'forfeit_amount': forfeit_amount,
            'refundable_amount': refundable,
        }

    def _apply_cancellation(self, amendment, reason_note=None,
                            penalty_amount=0.0, forfeit_amount=0.0,
                            release_property=True, terminate=False):
        """Cancel or terminate, without destroying anything.

        Explicitly does NOT: delete paid instalments, delete posted invoices,
        erase reservation history, or blindly return the unit to the market.
        Availability is recomputed from the authoritative engine instead.
        """
        self.ensure_one()
        if self.state in ('cancelled', 'terminated'):
            raise UserError(_(
                "Contract %s is already %s.") % (self.name, self.state))

        preview = self._cancellation_preview(penalty_amount, forfeit_amount)

        # Future, unbilled obligations are cancelled through the adjustment
        # trail. Invoiced ones are left standing: reversing a posted invoice is
        # Odoo's job and must be a deliberate accounting act.
        Adjustment = self.env['realestate.sale.installment.adjustment']
        for line in preview['future_installments']:
            Adjustment.create({
                'installment_id': line.id,
                'amendment_id': amendment.id if amendment else False,
                'adjustment_type': 'cancellation',
                'amount_after': 0.0,
                'reason': reason_note or _('Contract cancelled'),
            })
            line.is_cancelled = True

        self.write({
            'state': 'terminated' if terminate else 'cancelled',
            'cancellation_reason': reason_note or self.cancellation_reason,
        })

        if release_property and self.property_id:
            # Back to available only if nothing else commits the unit. The
            # availability engine is the authority, not this workflow.
            other = self.property_id.sale_contract_ids.filtered(
                lambda c: c.id != self.id
                and c.state in ('signed', 'active', 'financially_cleared',
                                'handed_over'))
            if not other:
                self.property_id.commercial_status = 'available'
                self.property_id._recompute_sale_availability()

        self.message_post(body=_(
            "%s. Paid %s, penalty %s, forfeited %s, refundable %s. "
            "%s future instalment(s) cancelled; %s posted invoice(s) left "
            "standing for accounting to reverse."
        ) % (_('Terminated') if terminate else _('Cancelled'),
             preview['paid_amount'], penalty_amount, forfeit_amount,
             preview['refundable_amount'],
             len(preview['future_installments']),
             len(preview['posted_invoices'])))
        return preview

    # ==================================================================
    # Phase 31 — unit swap
    # ==================================================================
    def _swap_preview(self, new_property, new_price=None):
        """What changing units would cost, before anything moves."""
        self.ensure_one()
        old_value = self.sale_price
        target_price = (new_price if new_price is not None
                        else new_property.list_price_developer)
        return {
            'old_property': self.property_id,
            'new_property': new_property,
            'old_value': old_value,
            'new_value': target_price,
            'difference': target_price - old_value,
            'already_paid': self._settled_amount(),
            'outstanding_amount': self._outstanding_amount(),
            'open_installments': self._open_installments(),
        }

    def _apply_unit_swap(self, amendment):
        """Move the deal onto another unit, carrying payments forward.

        The original unit reference is not overwritten silently: the amendment
        holds both sides, and the old unit is released only if nothing else
        commits it.
        """
        self.ensure_one()
        new_property = amendment.new_property_id
        if not new_property:
            raise UserError(_(
                "Amendment %s is a unit swap but names no target unit."
            ) % amendment.name)

        old_property = self.property_id
        if new_property == old_property:
            raise UserError(_("The target unit is the current unit."))

        new_property._check_available_for_sale()
        preview = self._swap_preview(
            new_property, amendment.new_price or None)

        open_lines = self._open_installments()
        self._assert_no_blocking_checks(open_lines)

        # Record where the deal came from, before moving it.
        amendment.write({
            'old_property_id': old_property.id,
            'old_price': self.sale_price,
        })

        # Re-cut the open balance to the new price. Refused when the new unit
        # costs less than what is already invoiced or paid.
        new_total = preview['new_value']
        carried = preview['already_paid']
        rebalanced = self._rebalance_schedule(
            amendment, new_total, 'transfer', _('Unit swap %s → %s') % (
                old_property.display_name, new_property.display_name))
        retained_value = rebalanced['retained_value']
        remaining = rebalanced['remaining']

        self.write({
            'property_id': new_property.id,
            'sale_price': new_total,
        })
        new_property.commercial_status = 'contracted'

        others = old_property.sale_contract_ids.filtered(
            lambda c: c.id != self.id
            and c.state in ('signed', 'active', 'financially_cleared',
                            'handed_over'))
        if not others:
            old_property.commercial_status = 'available'
            old_property._recompute_sale_availability()

        self.message_post(body=_(
            "Unit swapped from %s to %s. Value %s → %s (difference %s); %s "
            "already paid carried forward; %s kept on invoiced or paid "
            "instalments, %s re-cut across %s open instalment(s)."
        ) % (old_property.display_name, new_property.display_name,
             preview['old_value'], new_total, preview['difference'], carried,
             retained_value, remaining, len(open_lines)))
        return preview

    # ==================================================================
    # Price change
    # ==================================================================
    def _apply_price_change(self, amendment):
        """Change the price and make the schedule bill the new price.

        Writing ``sale_price`` alone changed nothing that is billed: the
        instalments already raised keep their amounts, signing does not raise
        them again, and invoicing reads each instalment's ``current_amount``.
        A signed 1,000 contract raised to 1,200 still billed 1,000.
        """
        self.ensure_one()
        new_price = amendment.new_price
        if self.currency_id.compare_amounts(new_price, 0.0) <= 0:
            raise UserError(_(
                "Amendment %s is a price change but names no new price."
            ) % amendment.name)
        self._assert_no_blocking_checks(self._open_installments())

        old_price = self.sale_price
        amendment.write({'old_price': old_price})
        rebalanced = self._rebalance_schedule(
            amendment, new_price, 'correction', _('Price change %s → %s') % (
                self.currency_id.format(old_price),
                self.currency_id.format(new_price)))
        self.sale_price = new_price

        self.message_post(body=_(
            "Price changed from %(old)s to %(new)s. %(retained)s stays on "
            "invoiced or paid instalments; %(remaining)s is spread over %(open)s "
            "open instalment(s)%(balance)s."
        ) % {
            'old': self.currency_id.format(old_price),
            'new': self.currency_id.format(new_price),
            'retained': self.currency_id.format(rebalanced['retained_value']),
            'remaining': self.currency_id.format(rebalanced['remaining']),
            'open': len(rebalanced['open_installments']),
            'balance': (_(', raised as one balancing instalment')
                        if rebalanced['balancing_installment'] else ''),
        })
        return rebalanced

    # ==================================================================
    # Phase 32 — buyer change / transfer
    # ==================================================================
    def _apply_buyer_change(self, amendment):
        """Replace or add a buyer, keeping the old parties on record.

        Country-specific registration (Wafi, Oqood, Tawtheeq) is deliberately
        absent — Phase 52 rules it out of this module. The hook is that the
        amendment record carries everything a localisation would need.
        """
        self.ensure_one()
        new_partner = amendment.new_partner_id
        if not new_partner:
            raise UserError(_(
                "Amendment %s is a buyer change but names no new buyer."
            ) % amendment.name)

        old_partner = self.partner_id
        amendment.old_partner_id = old_partner.id

        Party = self.env['realestate.sale.contract.party']
        # The outgoing buyer stays on the contract as an assignor, so who held
        # the deal before is answerable years later.
        existing = self.party_ids.filtered(
            lambda p: p.partner_id == old_partner and p.role == 'primary_buyer')
        if existing:
            existing.write({'role': 'assignee', 'is_signatory': False,
                            'notes': _('Transferred out on %s')
                            % amendment.effective_date})

        self.partner_id = new_partner
        self._sync_primary_party()

        if amendment.fee_amount:
            self.env['realestate.sale.installment'].create({
                'sale_contract_id': self.id,
                'sequence': TRANSFER_FEE_SEQUENCE,
                'kind': 'installment',
                'description': _('Transfer fee'),
                'original_amount': amendment.fee_amount,
                'date_due': amendment.effective_date,
            })

        self.message_post(body=_(
            "Buyer transferred from %s to %s.%s"
        ) % (old_partner.display_name, new_partner.display_name,
             _(' Transfer fee %s raised.') % amendment.fee_amount
             if amendment.fee_amount else ''))
        return True

    # ==================================================================
    # Entry points for the wizards
    # ==================================================================
    def action_open_restructure(self):
        self.ensure_one()
        return self._open_change_wizard('realestate.contract.restructure',
                                        _('Restructure Payment Plan'))

    def action_open_settlement(self):
        self.ensure_one()
        return self._open_change_wizard('realestate.contract.settlement',
                                        _('Early Settlement'))

    def action_open_cancellation(self):
        self.ensure_one()
        return self._open_change_wizard('realestate.contract.cancel',
                                        _('Cancel Contract'))

    def action_open_unit_swap(self):
        self.ensure_one()
        return self._open_change_wizard('realestate.contract.unit.swap',
                                        _('Swap Unit'))

    def action_open_buyer_transfer(self):
        self.ensure_one()
        return self._open_change_wizard('realestate.contract.transfer',
                                        _('Transfer Buyer'))

    def _open_change_wizard(self, model, title):
        self.ensure_one()
        if self.state not in ('signed', 'active', 'financially_cleared'):
            raise UserError(_(
                "Contract %s is %s. Post-signature changes apply to live "
                "contracts only."
            ) % (self.name, self.state))
        return {
            'type': 'ir.actions.act_window',
            'name': title,
            'res_model': model,
            'views': [(False, 'form')],
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_contract_id': self.id},
        }
