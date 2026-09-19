"""Lease lifecycle redesign (Phase 8) + canonical property allocation sync.

The legacy ``realestate.contract.state`` conflated a lease lifecycle with a
billing milestone -- ``invoiced`` is not a thing that happens to a *lease*, it
is a thing that happens to its *money*. That single overloaded selection also
meant "has the tenant signed", "has anything been billed" and "is the unit
occupied" had nowhere to live.

This module introduces:

* ``lifecycle_state``  -- the authoritative lease lifecycle
* ``billing_status``   -- how much of the schedule has been invoiced
* ``payment_status``   -- how much has been collected (from Odoo residuals)
* ``signature_status`` -- execution of the document
* ``occupancy_status`` -- physical possession

``state`` is retained as a **computed, read-only compatibility bridge** so the
public API, the portal, the partner statement report and the rental dashboard
keep reading the values they always read. Writes to ``state`` are translated
into lifecycle transitions rather than rejected.
"""

import logging

from markupsafe import Markup
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .lease_states import (
    APPROVAL_GATED_TRANSITIONS,
    BILLING_STATUS,
    LEGACY_TO_LIFECYCLE,
    LIFECYCLE_STATES,
    LIFECYCLE_TO_LEGACY,
    LIFECYCLE_TRANSITIONS,
    PAYMENT_STATUS,
    SIGNATURE_STATUS,
)

_logger = logging.getLogger(__name__)

#: Guard against re-entrancy while the allocation mirror is running.
SYNC_CTX = 're_syncing_property_lines'

#: Set by ``_do_transition`` so ``write`` does not re-validate a move it has
#: already checked.
TRANSITION_CTX = 're_lifecycle_transition'

#: The value `TRANSITION_CTX` must carry for the validation bypass to apply.
#:
#: A plain `True` was forgeable: a context travels from the client on every
#: RPC call, so `with_context(re_lifecycle_transition=True).write(...)` let a
#: caller skip the transition check entirely. A context arriving over RPC is
#: JSON, so it can contain strings, numbers and lists -- never this object.
#: Identity is the test, so only code inside this module can set it.
TRANSITION_TOKEN = object()


class ContractLifecycle(models.Model):
    _inherit = 'realestate.contract'

    # ------------------------------------------------------------------
    # Multi-company (Phase 28)
    # ------------------------------------------------------------------
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, index=True,
        default=lambda self: self.env.company,
        help="Company that owns this lease. Property, journals, products and "
             "invoices must all belong to it.",
    )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    lifecycle_state = fields.Selection(
        LIFECYCLE_STATES, string='Lease Status', default='draft',
        required=True, tracking=True, index=True, copy=False, readonly=True,
        help="Authoritative lease lifecycle. Use the workflow buttons; direct "
             "writes are validated against the allowed transition map.",
    )

    # ------------------------------------------------------------------
    # Orthogonal status dimensions
    # ------------------------------------------------------------------
    billing_status = fields.Selection(
        BILLING_STATUS, string='Billing', default='not_started',
        compute='_compute_billing_status', store=True, index=True,
    )
    payment_status = fields.Selection(
        PAYMENT_STATUS, string='Collection', default='not_paid',
        compute='_compute_payment_status', store=True, index=True,
    )
    amount_outstanding = fields.Monetary(
        string='Outstanding', compute='_compute_amount_outstanding',
        currency_field='currency_id',
        help="Invoiced and not yet paid, read from the invoices. Periods not "
             "invoiced yet are not due, so they are not counted; the Billing "
             "tab's Remaining Balance covers the whole term.",
    )
    signature_status = fields.Selection(
        SIGNATURE_STATUS, string='Signature', default='pending',
        tracking=True, copy=False,
    )
    signed_date = fields.Date(string='Signed On', tracking=True, copy=False)
    occupancy_status = fields.Selection(
        [('pending', 'Pending Move-In'),
         ('occupied', 'Occupied'),
         ('partially_occupied', 'Partially Occupied'),
         ('vacated', 'Vacated')],
        string='Occupancy', compute='_compute_contract_occupancy', store=True,
    )

    approved_by_id = fields.Many2one('res.users', string='Approved By',
                                     readonly=True, copy=False)
    approval_date = fields.Datetime(string='Approved On', readonly=True, copy=False)

    user_id = fields.Many2one(
        'res.users', string='Responsible', index=True, tracking=True,
        default=lambda self: self.env.user,
        help="Person accountable for this lease. Renewal reminders and expiry "
             "activities are assigned to them.",
    )

    # ------------------------------------------------------------------
    # Canonical allocations (Phase 6)
    # ------------------------------------------------------------------
    property_line_ids = fields.One2many(
        'realestate.contract.property.line', 'contract_id',
        string='Property Allocations', copy=True,
    )
    property_line_count = fields.Integer(compute='_compute_property_line_count')
    allocated_rent_total = fields.Monetary(
        string='Allocated Rent Total', compute='_compute_allocated_rent_total',
        store=True,
    )
    total_area_sqm = fields.Float(
        string='Total Area (sqm)', compute='_compute_allocated_rent_total', store=True,
    )

    # ==================================================================
    # Legacy `state` compatibility bridge
    # ==================================================================
    state = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('ready', 'Ready'),
            ('confirmed', 'Confirmed'),
            ('invoiced', 'Invoiced'),
            ('active', 'Active'),
            ('expired', 'Expired'),
            ('terminated', 'Terminated'),
        ],
        string='Status (legacy)',
        compute='_compute_legacy_state', store=True, readonly=True, index=True,
        tracking=False,
        help="DEPRECATED compatibility bridge derived from lifecycle_state. "
             "Downstream ATMTA modules and the public API still read it.",
    )

    @api.depends('lifecycle_state', 'billing_status',
                 'invoice_id', 'invoice_id.state')
    def _compute_legacy_state(self):
        """Reproduce the pre-upgrade observable values exactly.

        ``invoiced`` only ever existed as "committed and billed", so it is
        re-derived from the pair (pending_signature, something invoiced) rather
        than being lost.

        "Something invoiced" has two shapes. The billing engine invoices the
        obligations, which is what ``billing_status`` reads. The legacy
        ``action_generate_invoices`` raises one invoice for the whole lease and
        links it only through ``invoice_id``, so ``billing_status`` never moves
        for it -- and without counting it here the lease stayed ``confirmed``
        and ``action_activate`` refused it. The invoice is taken as evidence
        that the lease is billed, not spread across the obligations, which
        would count its total once per line.
        """
        for rec in self:
            legacy = LIFECYCLE_TO_LEGACY.get(rec.lifecycle_state, 'draft')
            billed = (
                rec.billing_status in ('partially_invoiced', 'fully_invoiced')
                or (rec.invoice_id and rec.invoice_id.state != 'cancel'))
            if legacy == 'confirmed' and billed:
                legacy = 'invoiced'
            rec.state = legacy

    @api.model
    def _translate_legacy_state_write(self, vals):
        """Turn a legacy ``state`` write into a lifecycle write."""
        if 'state' not in vals:
            return vals
        vals = dict(vals)
        legacy = vals.pop('state')
        target = LEGACY_TO_LIFECYCLE.get(legacy)
        if target:
            vals.setdefault('lifecycle_state', target)
        return vals

    # ==================================================================
    # Computes
    # ==================================================================
    @api.depends('property_line_ids')
    def _compute_property_line_count(self):
        for rec in self:
            rec.property_line_count = len(rec.property_line_ids)

    @api.depends('property_line_ids.allocated_rent', 'property_line_ids.area_sqm')
    def _compute_allocated_rent_total(self):
        for rec in self:
            rec.allocated_rent_total = sum(rec.property_line_ids.mapped('allocated_rent'))
            rec.total_area_sqm = sum(rec.property_line_ids.mapped('area_sqm'))

    @api.depends('property_line_ids.occupies_property', 'lifecycle_state')
    def _compute_contract_occupancy(self):
        for rec in self:
            lines = rec.property_line_ids
            if not lines:
                rec.occupancy_status = 'pending'
                continue
            occupied = lines.filtered('occupies_property')
            if not occupied:
                vacated = lines.filtered(lambda line: line.occupancy_status == 'vacated')
                rec.occupancy_status = 'vacated' if vacated == lines else 'pending'
            elif occupied == lines:
                rec.occupancy_status = 'occupied'
            else:
                rec.occupancy_status = 'partially_occupied'

    @api.depends('contract_payment_ids.amount_invoiced',
                 'contract_payment_ids.amount_due',
                 'contract_payment_ids.state')
    def _compute_billing_status(self):
        for rec in self:
            obligations = rec.contract_payment_ids.filtered(
                lambda p: p.state != 'cancelled')
            if not obligations:
                rec.billing_status = 'not_started'
                continue
            invoiced = obligations.filtered(lambda p: p.amount_invoiced > 0)
            if not invoiced:
                rec.billing_status = 'scheduled'
            elif invoiced == obligations:
                rec.billing_status = 'fully_invoiced'
            else:
                rec.billing_status = 'partially_invoiced'

    @api.depends('contract_payment_ids.amount_residual',
                 'contract_payment_ids.state',
                 'invoice_id.state', 'invoice_id.amount_residual')
    def _compute_amount_outstanding(self):
        for rec in self:
            if rec.invoice_id and rec.invoice_id.state == 'posted':
                # Legacy lease billed by one invoice for its whole term.
                rec.amount_outstanding = rec.invoice_id.amount_residual
                continue
            rec.amount_outstanding = sum(rec.contract_payment_ids.filtered(
                lambda p: p.state in ('invoiced', 'paid')
            ).mapped('amount_residual'))

    @api.depends('contract_payment_ids.amount_residual',
                 'contract_payment_ids.amount_invoiced',
                 'contract_payment_ids.date_due',
                 'contract_payment_ids.state')
    def _compute_payment_status(self):
        for rec in self:
            obligations = rec.contract_payment_ids.filtered(
                lambda p: p.state not in ('cancelled', 'draft'))
            if not obligations:
                rec.payment_status = 'not_paid'
                continue
            residual = sum(obligations.mapped('amount_residual'))
            invoiced = sum(obligations.mapped('amount_invoiced'))
            if invoiced and residual <= 0.01:
                rec.payment_status = 'paid'
            elif any(p.days_overdue > 0 and p.amount_residual > 0.01 for p in obligations):
                rec.payment_status = 'overdue'
            elif residual < invoiced:
                rec.payment_status = 'partial'
            else:
                rec.payment_status = 'not_paid'

    # ==================================================================
    # Transition engine
    # ==================================================================
    def _assert_transition(self, target):
        """Validate a lifecycle move server-side.

        Buttons disappearing from a form is not security; every transition is
        checked here regardless of how it was invoked -- workflow button,
        direct ``write``, XML-RPC or a downstream module.
        """
        labels = dict(LIFECYCLE_STATES)
        for rec in self:
            current = rec.lifecycle_state
            if current == target:
                continue
            allowed = LIFECYCLE_TRANSITIONS.get(current, ())
            if target not in allowed:
                raise UserError(_(
                    "Lease '%(name)s' cannot move from '%(cur)s' to '%(new)s'. "
                    "Allowed from here: %(allowed)s.",
                    name=rec.display_name,
                    cur=labels.get(current, current),
                    new=labels.get(target, target),
                    allowed=', '.join(
                        labels.get(a, a) for a in allowed) or _('none'),
                ))
            # sudo(): this reads a configuration flag on the record's own
            # company. Whether the workflow requires an approval step is a
            # policy setting, not a permission the acting user must hold
            # read access to res.company for.
            if ((current, target) in APPROVAL_GATED_TRANSITIONS
                    and rec.company_id.sudo().re_require_lease_approval):
                raise UserError(_(
                    "Lease '%(name)s' must be approved before it can go to "
                    "signature. Company '%(company)s' requires the approval "
                    "step.",
                    name=rec.display_name,
                    company=rec.company_id.sudo().display_name,
                ))

    def _do_transition(self, target, message=None):
        """Apply a validated transition and leave an audit trace (Phase 29)."""
        self._assert_transition(target)
        labels = dict(LIFECYCLE_STATES)
        label = labels.get(target, target)
        for rec in self:
            if rec.lifecycle_state == target:
                continue
            previous = labels.get(rec.lifecycle_state, rec.lifecycle_state)
            rec.with_context(
                **{TRANSITION_CTX: TRANSITION_TOKEN}).lifecycle_state = target
            rec.message_post(body=message or Markup(_(
                "Lease status: <b>%(prev)s</b> → <b>%(new)s</b>")) % {
                    'prev': previous, 'new': label})
        return True

    # ---------------- Forward transitions ----------------
    def action_to_proposal(self):
        self._require_group('atmta_real_estate.group_rental_agent')
        for rec in self:
            rec._validate_ready_for_proposal()
        return self._do_transition('proposal')

    def action_submit_for_approval(self):
        self._require_group('atmta_real_estate.group_rental_agent')
        for rec in self:
            rec._validate_ready_for_proposal()
        return self._do_transition('pending_approval')

    def action_approve_lease(self):
        """Commercial approval. Restricted to Rental Manager, and a user may
        not approve a lease they created (Phase 27 self-approval control)."""
        self._require_group('atmta_real_estate.group_rental_manager')
        for rec in self:
            rec._check_self_approval()
        self._do_transition('pending_signature')
        self.write({
            'approved_by_id': self.env.user.id,
            'approval_date': fields.Datetime.now(),
        })
        return True

    def action_mark_signed(self):
        self._require_group('atmta_real_estate.group_rental_agent')
        for rec in self:
            rec.write({
                'signature_status': 'signed',
                'signed_date': rec.signed_date or fields.Date.context_today(rec),
            })
        return True

    def action_activate_lease(self):
        """Start the lease. Requires signature and at least one allocation."""
        self._require_group('atmta_real_estate.group_property_manager')
        for rec in self:
            if not rec.property_line_ids:
                raise UserError(_(
                    "Lease '%s' has no property allocation.", rec.display_name))
            if rec.signature_status == 'pending':
                raise UserError(_(
                    "Lease '%s' has not been signed yet.", rec.display_name))
        return self._do_transition('active')

    def action_give_notice(self):
        self._require_group('atmta_real_estate.group_property_manager')
        return self._do_transition('notice')

    def action_end_lease(self):
        """Normal expiry at term."""
        self._require_group('atmta_real_estate.group_property_manager')
        return self._do_transition('ended')

    def action_cancel_lease(self):
        self._require_group('atmta_real_estate.group_rental_manager')
        return self._do_transition('cancelled')

    def action_reopen_draft(self):
        self._require_group('atmta_real_estate.group_rental_manager')
        return self._do_transition('draft')

    def action_withdraw_notice(self):
        """Take a lease on notice back to active.

        Notice given directly on the lease can be withdrawn by a Property
        Manager. Notice that belongs to a termination is withdrawn by cancelling
        that termination, which also handles its settlement and move-out.
        """
        self._require_group('atmta_real_estate.group_property_manager')
        for lease in self:
            if lease.active_termination_id:
                raise UserError(_(
                    "Lease %(lease)s is on notice through termination %(termination)s. "
                    "Cancel that termination to withdraw the notice.",
                    lease=lease.display_name,
                    termination=lease.active_termination_id.display_name))
        return self._do_transition('active')

    def action_amend_lease(self):
        """Open a new amendment, for a lease that can be amended."""
        self.ensure_one()
        if self.lifecycle_state not in ('pending_signature', 'active', 'notice'):
            raise UserError(_(
                "Lease %(lease)s is %(status)s. Only a lease awaiting signature, "
                "active or on notice can be amended.",
                lease=self.display_name,
                status=dict(LIFECYCLE_STATES).get(self.lifecycle_state)))
        return self.action_new_amendment()

    # ---------------- Validation helpers ----------------
    def _validate_ready_for_proposal(self):
        self.ensure_one()
        if not self.partner_id:
            raise UserError(_("Set the tenant before proposing lease '%s'.",
                              self.display_name))
        if not self.start_date:
            raise UserError(_("Set a start date before proposing lease '%s'.",
                              self.display_name))
        if not self.property_line_ids and not self.property_id:
            raise UserError(_("Allocate at least one property to lease '%s'.",
                              self.display_name))

    def _check_self_approval(self):
        """Block approving your own lease unless explicitly allowed.

        Segregation of duties: the person who drafted the commercial terms
        should not be the person who signs off on them. A dedicated group can
        override this for small teams where one person legitimately does both.
        """
        self.ensure_one()
        # sudo(): same reasoning as _assert_transition -- a policy flag.
        if self.company_id.sudo().re_allow_self_approval or self.env.su:
            return
        if self.env.user.has_group('atmta_real_estate.group_rental_self_approval'):
            return
        if self.create_uid and self.create_uid == self.env.user:
            raise AccessError(_(
                "You created lease '%s' and cannot approve it yourself. "
                "Ask another Rental Manager, or request the "
                "'Allow Self-Approval' permission.", self.display_name))

    def _require_group(self, xmlid):
        """Server-side permission gate for a workflow action."""
        if self.env.su or self.env.user.has_group(xmlid):
            return True
        group = self.env.ref(xmlid, raise_if_not_found=False)
        raise AccessError(_(
            "This action requires the '%s' permission.",
            group.display_name if group else xmlid,
        ))

    # ==================================================================
    # Allocation sync for single-unit leases (Phase 6)
    # ==================================================================
    def _sync_property_lines(self):
        """Keep a single-unit lease's allocation in step with the lease.

        * single-unit -> exactly one allocation, ``origin='single'``, mirroring
          the lease's unit, dates and rent
        * multi-unit  -> units are entered directly as allocations; only a
          single-unit mirror left over from a mode switch is removed

        Idempotent: re-running updates in place instead of duplicating, so it
        is safe to call from ``create``, ``write`` and migrations alike.
        """
        if self.env.context.get(SYNC_CTX):
            return
        Line = self.env['realestate.contract.property.line'].with_context(
            **{SYNC_CTX: True})
        for contract in self:
            if contract.is_multi_property:
                contract.property_line_ids.filtered(
                    lambda line: line.origin == 'single').unlink()
            else:
                contract._sync_single_property_line(Line)

    @api.model
    def _restore_legacy_unit_lines(self, rows):
        """Carry legacy unit lines over as allocations (0.10 upgrade).

        ``rows`` are the legacy lines recorded before the legacy model was
        removed: dicts with ``id``, ``contract_id``, ``property_id``, ``price``,
        ``start_date``, ``end_date``, ``notes`` and ``allocation_id`` (the
        allocation that already mirrored the line, if any).

        * A line an allocation already mirrored needs nothing, and neither does
          a line whose unit and dates the lease already allocates.
        * A line on a single-unit lease was never one of its units -- the
          single-unit sync ignored it and removed its mirror -- so it is
          reported and kept only in the backup table.
        * Every other line becomes an allocation with the line's unit, dates,
          rent and notes. One that would double-book its unit is reported, not
          forced in.

        :return: dict of lists ``created``, ``already_allocated``, ``skipped``
            and ``failed``; skipped and failed entries are ``(label, reason)``.
        """
        Allocation = self.env['realestate.contract.property.line'].with_context(
            **{SYNC_CTX: True})
        report = {'created': [], 'already_allocated': [], 'skipped': [], 'failed': []}
        for row in rows:
            label = _("legacy unit line %s", row['id'])
            lease = self.browse(row.get('contract_id') or []).exists()
            if not lease or not row.get('property_id'):
                report['skipped'].append(
                    (label, _("its lease or unit no longer exists")))
                continue
            if not lease.is_multi_property:
                report['skipped'].append((label, _(
                    "lease %s is a single-unit lease, so the line was not one "
                    "of its units", lease.display_name)))
                continue
            if row.get('allocation_id') and Allocation.browse(row['allocation_id']).exists():
                report['already_allocated'].append(label)
                continue
            start = row.get('start_date') or lease.start_date
            end = row.get('end_date') or lease.end_date
            if lease.property_line_ids.filtered(
                    lambda a: a.property_id.id == row['property_id']
                    and a.start_date == start and a.end_date == end):
                report['already_allocated'].append(label)
                continue
            try:
                with self.env.cr.savepoint():
                    Allocation.create({
                        'contract_id': lease.id,
                        'property_id': row['property_id'],
                        'start_date': start,
                        'end_date': end,
                        'allocated_rent': row.get('price') or 0.0,
                        'notes': row.get('notes') or False,
                        'origin': 'manual',
                    })
                report['created'].append(label)
            except (UserError, ValidationError) as error:
                report['failed'].append((label, str(error)))
        return report

    def _sync_single_property_line(self, Line):
        self.ensure_one()
        mirror = self.property_line_ids.filtered(lambda line: line.origin == 'single')
        if not self.property_id:
            mirror.unlink()
            return
        vals = {
            'property_id': self.property_id.id,
            'start_date': self.start_date,
            'end_date': self.end_date,
            'allocated_rent': self.price,
        }
        if mirror:
            mirror[:1].write(vals)
            mirror[1:].unlink()
        else:
            Line.create(dict(vals, contract_id=self.id, origin='single'))

    # ==================================================================
    # ORM overrides
    # ==================================================================
    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._translate_legacy_state_write(v) for v in vals_list]
        # A lease begins at draft. Everything past it is a transition, and a
        # transition is what carries the role check, the approval gate and the
        # signature and allocation prerequisites.
        #
        # Without this, `write` is guarded and `create` is the way round it: a
        # leasing agent could create the lease already `active` over RPC and
        # the allocation mirror would then occupy the unit for a lease nobody
        # approved or signed. The legacy `state` key arrives here already
        # translated, so it is closed by the same line.
        #
        # `env.su` keeps data loads, demo data and migrations working; they are
        # not RPC callers and they are how a database legitimately arrives
        # holding leases in mid-lifecycle.
        if not self.env.su:
            default = self._fields['lifecycle_state'].default(self)
            for vals in vals_list:
                supplied = vals.get('lifecycle_state')
                if supplied and supplied != default:
                    raise AccessError(_(
                        "A lease cannot be created with the status "
                        "'%(state)s'. Create it and then use the workflow "
                        "actions, which check the permission and the "
                        "prerequisites for each step.",
                        state=dict(LIFECYCLE_STATES).get(supplied, supplied),
                    ))
        contracts = super().create(vals_list)
        contracts._sync_property_lines()
        return contracts

    def write(self, vals):
        vals = self._translate_legacy_state_write(vals)
        # Validate lifecycle moves however they arrive -- workflow button,
        # direct write, XML-RPC, or a downstream module. _do_transition sets
        # TRANSITION_CTX because it has already validated.
        if 'lifecycle_state' in vals and \
                self.env.context.get(TRANSITION_CTX) is not TRANSITION_TOKEN:
            # The transition map is not the whole of the rule. Each workflow
            # action also checks a role, and approval additionally checks that
            # the approver is not the drafter -- none of which a bare write
            # goes near. A Leasing Agent could otherwise write
            # `pending_signature` and then `active` and arrive at a live,
            # unapproved, unsigned lease without ever pressing a button.
            #
            # The field is `readonly` and no view writes it, so refusing here
            # costs nothing legitimate: every real caller in this codebase
            # goes through `_do_transition`. `env.su` is exempt because data
            # loads and migrations are not RPC callers, and it still has to
            # satisfy the transition map below.
            if not self.env.su:
                raise AccessError(_(
                    "A lease status cannot be set by writing to it. Use the "
                    "lease workflow actions, which check the permission and "
                    "the prerequisites for each step."))
            self._assert_transition(vals['lifecycle_state'])
        res = super().write(vals)
        if not self.env.context.get(SYNC_CTX) and (
                {'property_id', 'start_date', 'end_date', 'price',
                 'is_single_property', 'is_multi_property'} & set(vals)):
            self._sync_property_lines()
        return res

    # ==================================================================
    # Company integrity
    # ==================================================================
    @api.constrains('company_id', 'property_id')
    def _check_contract_company(self):
        for rec in self:
            prop = rec.property_id
            if prop and prop.company_id and prop.company_id != rec.company_id:
                raise ValidationError(_(
                    "Lease '%(lease)s' belongs to company '%(cc)s' but property "
                    "'%(prop)s' belongs to '%(pc)s'.",
                    lease=rec.display_name, cc=rec.company_id.display_name,
                    prop=prop.display_name, pc=prop.company_id.display_name,
                ))

    # ==================================================================
    # Smart buttons
    # ==================================================================
    def action_view_property_lines(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Property Allocations'),
            'res_model': 'realestate.contract.property.line',
            'view_mode': 'list,form',
            'domain': [('contract_id', '=', self.id)],
            'context': {'default_contract_id': self.id},
        }
