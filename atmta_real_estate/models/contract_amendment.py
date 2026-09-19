"""Lease amendments (Phase 18).

Mid-term changes -- a rent review, an extra parking bay, a tenant substitution,
a six-month extension -- used to be done by editing the lease in place. The
change happened, but the *record* of it did not: no effective date, no
before/after, no approval, no document. Six months later nobody can reconstruct
what was agreed or when it started applying.

An amendment is a document with a workflow. It carries the previous value and
the new value, it is approved and signed before it applies, and applying it is
**idempotent** -- ``applied_date`` is the guard, so re-clicking Apply, a retried
cron, or a double-submitted form can never charge the change twice.

Commercial history is never overwritten: the lease's own chatter records every
applied amendment, and the amendment keeps its own snapshot forever.
"""

import logging

from dateutil.relativedelta import relativedelta

from markupsafe import Markup
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

AMENDMENT_TYPES = [
    ('rent_change', 'Rent Change'),
    ('term_extension', 'Term Extension'),
    ('term_reduction', 'Term Reduction'),
    ('property_addition', 'Property Addition'),
    ('property_removal', 'Property Removal'),
    ('party_change', 'Party Change'),
    ('charge_change', 'Charge Change'),
    ('schedule_change', 'Schedule Change'),
    ('deposit_change', 'Deposit Change'),
    ('other', 'Other'),
]

AMENDMENT_STATES = [
    ('draft', 'Draft'),
    ('proposed', 'Proposed'),
    ('approved', 'Approved'),
    ('signed', 'Signed'),
    ('applied', 'Applied'),
    ('cancelled', 'Cancelled'),
]


class ContractAmendment(models.Model):
    _name = 'realestate.contract.amendment'
    _description = 'Lease Amendment'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'effective_date desc, id desc'

    name = fields.Char(
        string='Reference', required=True, copy=False, readonly=True,
        default=lambda self: _('New'),
    )
    contract_id = fields.Many2one(
        'realestate.contract', string='Lease', required=True,
        ondelete='cascade', index=True, tracking=True,
    )
    company_id = fields.Many2one(
        related='contract_id.company_id', store=True, index=True, readonly=True,
    )
    currency_id = fields.Many2one(
        related='contract_id.currency_id', store=True, readonly=True,
    )
    partner_id = fields.Many2one(
        related='contract_id.partner_id', store=True, readonly=True)

    amendment_type = fields.Selection(
        AMENDMENT_TYPES, string='Type', required=True, default='rent_change',
        tracking=True, index=True,
    )
    state = fields.Selection(
        AMENDMENT_STATES, default='draft', required=True, tracking=True,
        index=True, copy=False,
    )
    effective_date = fields.Date(
        string='Effective From', required=True, tracking=True, index=True,
        default=fields.Date.context_today,
        help="The date the change starts to apply. Billing periods before it "
             "are untouched.",
    )
    reason = fields.Text(string='Reason', tracking=True)

    # ---------------- Change payload ----------------
    new_rent = fields.Monetary(string='New Rent', tracking=True)
    new_end_date = fields.Date(string='New End Date', tracking=True)
    property_id = fields.Many2one(
        'realestate.property', string='Property',
        help="Property to add or remove.",
    )
    property_rent = fields.Monetary(
        string='Property Rent', help="Allocated rent for a property being added.")
    new_partner_id = fields.Many2one('res.partner', string='New Tenant')
    new_deposit_amount = fields.Monetary(string='New Deposit')

    # ---------------- Snapshot (never rewritten) ----------------
    previous_value = fields.Text(
        string='Previous Value', readonly=True, copy=False,
        help="Human-readable snapshot of what the lease said before this "
             "amendment was applied.",
    )
    new_value = fields.Text(string='New Value', readonly=True, copy=False)

    # ---------------- Governance ----------------
    approver_id = fields.Many2one(
        'res.users', string='Approved By', readonly=True, copy=False, tracking=True)
    approval_date = fields.Datetime(readonly=True, copy=False)
    signed_date = fields.Date(string='Signed On', tracking=True)
    applied_date = fields.Datetime(
        string='Applied On', readonly=True, copy=False, tracking=True,
        help="Set the moment the amendment takes effect. Its presence is what "
             "makes Apply idempotent.",
    )
    document_ids = fields.Many2many(
        'ir.attachment', 'realestate_amendment_attachment_rel',
        'amendment_id', 'attachment_id', string='Documents',
    )
    notes = fields.Html()

    # ==================================================================
    # Workflow
    # ==================================================================
    def action_propose(self):
        for rec in self:
            rec._validate_payload()
            rec.state = 'proposed'
            rec.message_post(body=_("Amendment proposed, effective %s.",
                                    rec.effective_date))
        return True

    def action_approve(self):
        self.mapped('contract_id')._require_group(
            'atmta_real_estate.group_rental_manager')
        for rec in self:
            if rec.state != 'proposed':
                raise UserError(_(
                    "Amendment %s must be proposed before approval.", rec.name))
            rec._validate_payload()
            rec.write({
                'state': 'approved',
                'approver_id': self.env.user.id,
                'approval_date': fields.Datetime.now(),
            })
            rec.message_post(body=_("Amendment approved."))
        return True

    def action_sign(self):
        for rec in self:
            if rec.state != 'approved':
                raise UserError(_(
                    "Amendment %s must be approved before it is signed.", rec.name))
            rec.write({
                'state': 'signed',
                'signed_date': rec.signed_date or fields.Date.context_today(rec),
            })
        return True

    def action_apply(self):
        """Apply the change to the lease. Safe to call more than once."""
        self.mapped('contract_id')._require_group(
            'atmta_real_estate.group_rental_manager')
        for rec in self:
            if rec.applied_date:
                # Idempotency guard: silently succeed rather than double-apply.
                _logger.info(
                    "Amendment %s already applied on %s; skipping.",
                    rec.name, rec.applied_date)
                continue
            if rec.state != 'signed':
                raise UserError(_(
                    "Amendment %s must be signed before it can be applied.",
                    rec.name))
            rec._validate_payload()
            rec.previous_value = rec._snapshot_current()
            handler = getattr(rec, '_apply_%s' % rec.amendment_type, None)
            if handler is None:
                raise UserError(_(
                    "Amendment type '%s' has no apply handler.",
                    rec.amendment_type))
            handler()
            rec.new_value = rec._snapshot_current()
            rec.write({'state': 'applied', 'applied_date': fields.Datetime.now()})
            rec.contract_id.message_post(body=Markup(_(
                "Amendment <b>%(name)s</b> (%(type)s) applied, effective "
                "%(date)s.<br/>Before: %(before)s<br/>After: %(after)s")) % {
                    'name': rec.name,
                    'type': dict(AMENDMENT_TYPES).get(rec.amendment_type),
                    'date': rec.effective_date,
                    'before': rec.previous_value, 'after': rec.new_value})
        return True

    def action_cancel(self):
        for rec in self:
            if rec.state == 'applied':
                raise UserError(_(
                    "Amendment %s has been applied. Raise a reversing "
                    "amendment rather than cancelling history.", rec.name))
            rec.state = 'cancelled'
        return True

    # ==================================================================
    # Validation
    # ==================================================================
    def _validate_payload(self):
        self.ensure_one()
        contract = self.contract_id
        kind = self.amendment_type

        if contract.start_date and self.effective_date < contract.start_date:
            raise ValidationError(_(
                "Amendment %(name)s takes effect before the lease starts "
                "(%(start)s).", name=self.name, start=contract.start_date))

        if kind == 'rent_change' and not self.new_rent:
            raise ValidationError(_("Set the new rent on amendment %s.", self.name))
        if kind in ('term_extension', 'term_reduction'):
            if not self.new_end_date:
                raise ValidationError(_(
                    "Set the new end date on amendment %s.", self.name))
            if kind == 'term_extension' and contract.end_date \
                    and self.new_end_date <= contract.end_date:
                raise ValidationError(_(
                    "A term extension must end after the current end date "
                    "(%s).", contract.end_date))
            if kind == 'term_reduction' and contract.end_date \
                    and self.new_end_date >= contract.end_date:
                raise ValidationError(_(
                    "A term reduction must end before the current end date "
                    "(%s).", contract.end_date))
            if self.new_end_date < self.effective_date:
                raise ValidationError(_(
                    "The new end date cannot be before the effective date."))
        if kind in ('property_addition', 'property_removal') and not self.property_id:
            raise ValidationError(_(
                "Select the property on amendment %s.", self.name))
        if kind == 'party_change' and not self.new_partner_id:
            raise ValidationError(_(
                "Select the new tenant on amendment %s.", self.name))
        if kind == 'deposit_change' and not self.new_deposit_amount:
            raise ValidationError(_(
                "Set the new deposit amount on amendment %s.", self.name))

    def _snapshot_current(self):
        """Readable state of the fields this amendment touches."""
        self.ensure_one()
        contract = self.contract_id
        kind = self.amendment_type
        if kind == 'rent_change':
            return _("Rent: %s") % contract._rent_on(self.effective_date)
        if kind in ('term_extension', 'term_reduction'):
            return _("End date: %s") % contract.end_date
        if kind in ('property_addition', 'property_removal'):
            props = contract.property_line_ids.mapped('property_id.display_name')
            return _("Properties: %s") % (', '.join(props) or _('none'))
        if kind == 'party_change':
            return _("Tenant: %s") % contract.partner_id.display_name
        if kind == 'deposit_change':
            return _("Deposit: %s") % contract.deposit_amount
        if kind == 'charge_change':
            return _("Charges: %s") % ', '.join(
                '%s=%s' % (r.name, r.amount)
                for r in contract.charge_rule_ids.filtered('active')) or _('none')
        return _("Lease: %s") % contract.display_name

    # ==================================================================
    # Apply handlers -- one per amendment type
    # ==================================================================
    def _apply_rent_change(self):
        """A mid-term rent change is an escalation with an effective date.

        Modelling it that way (rather than overwriting ``price``) keeps the
        original contracted rent intact and makes the schedule generator
        produce the right amount for every period automatically.
        """
        self.ensure_one()
        self.env['realestate.rent.escalation.rule'].create({
            'contract_id': self.contract_id.id,
            'effective_date': self.effective_date,
            'escalation_type': 'scheduled_amount',
            'scheduled_amount': self.new_rent,
            'notes': _("Set by amendment %s. %s") % (self.name, self.reason or ''),
        })
        self._regenerate_future_schedule()

    def _apply_term_extension(self):
        self.ensure_one()
        self.contract_id.write({'end_date': self.new_end_date})
        self.contract_id.property_line_ids.filtered(
            lambda line: not line.move_out_date
        ).write({'end_date': self.new_end_date})
        if self.contract_id.lifecycle_state == 'ended':
            self.contract_id._do_transition('active', message=_(
                "Reactivated by term extension %s.") % self.name)
        self._regenerate_future_schedule()

    def _apply_term_reduction(self):
        self.ensure_one()
        contract = self.contract_id
        contract.property_line_ids.filtered(
            lambda line: not line.end_date or line.end_date > self.new_end_date
        ).write({'end_date': self.new_end_date})
        contract.write({'end_date': self.new_end_date})
        self._cancel_obligations_after(self.new_end_date)
        self._regenerate_future_schedule()

    def _apply_property_addition(self):
        self.ensure_one()
        contract = self.contract_id
        Allocation = self.env['realestate.contract.property.line']
        clash = Allocation._check_property_free(
            self.property_id.id, self.effective_date, contract.end_date,
            exclude_contract=contract.id)
        if clash:
            raise ValidationError(_(
                "Cannot add '%(prop)s': it is committed to lease '%(lease)s' "
                "for %(start)s → %(end)s.",
                prop=self.property_id.display_name,
                lease=clash.contract_id.display_name,
                start=clash.start_date, end=clash.end_date or _('open-ended')))
        Allocation.create({
            'contract_id': contract.id,
            'property_id': self.property_id.id,
            'start_date': self.effective_date,
            'end_date': contract.end_date,
            'allocated_rent': self.property_rent,
            'origin': 'manual',
        })
        self._regenerate_future_schedule()

    def _apply_property_removal(self):
        self.ensure_one()
        allocations = self.contract_id.property_line_ids.filtered(
            lambda line: line.property_id == self.property_id)
        if not allocations:
            raise UserError(_(
                "Property '%s' is not allocated to this lease.",
                self.property_id.display_name))
        # End the allocation rather than deleting it -- the tenant did occupy
        # the unit for part of the term and the billing history must stand.
        allocations.write({
            'end_date': self.effective_date - relativedelta(days=1),
            'move_out_date': self.effective_date - relativedelta(days=1),
        })
        self._regenerate_future_schedule()

    def _apply_party_change(self):
        self.ensure_one()
        contract = self.contract_id
        old_partner = contract.partner_id
        primary = contract.party_ids.filtered('is_primary')
        if primary:
            primary[0].write({
                'end_date': self.effective_date - relativedelta(days=1),
                'is_primary': False,
            })
        self.env['realestate.contract.party'].create({
            'contract_id': contract.id,
            'partner_id': self.new_partner_id.id,
            'role': 'company' if self.new_partner_id.is_company else 'tenant',
            'is_primary': True,
            'start_date': self.effective_date,
            'end_date': contract.end_date,
        })
        contract.message_post(body=_(
            "Tenant changed from %(old)s to %(new)s.",
            old=old_partner.display_name, new=self.new_partner_id.display_name))

    def _apply_charge_change(self):
        """Charge edits are made directly on the rules; this records that the
        change was authorised and re-cuts the forward schedule."""
        self.ensure_one()
        self._regenerate_future_schedule()

    def _apply_schedule_change(self):
        self.ensure_one()
        self._regenerate_future_schedule()

    def _apply_deposit_change(self):
        self.ensure_one()
        self.contract_id.write({'deposit_amount': self.new_deposit_amount})

    def _apply_other(self):
        """No structural change -- the amendment exists for the audit trail."""
        return True

    # ==================================================================
    # Shared helpers
    # ==================================================================
    def _regenerate_future_schedule(self):
        """Re-cut obligations from the effective date forward.

        Obligations already invoiced are never touched -- the generator itself
        refuses to rewrite them -- so this only reprices what has not yet been
        billed.
        """
        self.ensure_one()
        contract = self.contract_id
        if not contract.use_billing_engine:
            return
        contract._generate_billing_schedule()

    def _cancel_obligations_after(self, cutoff):
        """Drop un-invoiced obligations that fall entirely after ``cutoff``.

        Anything already invoiced is left alone: reversing a posted invoice is
        a credit-note decision for the termination workflow, not a silent
        delete here.
        """
        self.ensure_one()
        doomed = self.contract_id.contract_payment_ids.filtered(
            lambda o: o.state == 'draft'
            and not o.move_id
            and o.period_start
            and o.period_start > cutoff)
        if doomed:
            doomed.unlink()

    # ==================================================================
    # ORM
    # ==================================================================
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.contract.amendment') or _('New')
        return super().create(vals_list)


class ContractAmendmentMixin(models.Model):
    _inherit = 'realestate.contract'

    amendment_ids = fields.One2many(
        'realestate.contract.amendment', 'contract_id', string='Amendments',
    )
    amendment_count = fields.Integer(compute='_compute_amendment_count')

    @api.depends('amendment_ids')
    def _compute_amendment_count(self):
        for rec in self:
            rec.amendment_count = len(rec.amendment_ids)

    def action_new_amendment(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Lease Amendment'),
            'res_model': 'realestate.contract.amendment',
            'view_mode': 'form',
            'context': {'default_contract_id': self.id},
        }

    def action_view_amendments(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Amendments'),
            'res_model': 'realestate.contract.amendment',
            'view_mode': 'list,form',
            'domain': [('contract_id', '=', self.id)],
            'context': {'default_contract_id': self.id},
        }
