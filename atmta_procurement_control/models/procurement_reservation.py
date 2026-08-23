# -*- coding: utf-8 -*-
"""M3A — the reservation: approved demand consuming purchasing capacity.

### What a reservation is

Authorised demand that has not yet become anybody's obligation. Between
"approved" and "ordered" there is a real period — often weeks — in which the
project has decided to spend money and no supplier has been told anything. M2
had no way to represent that period, so ten 3,000,000 requisitions could be
approved against a 10,000,000 budget and every one of them looked affordable.

### What a reservation is not

Not an accounting encumbrance. Not a journal entry. Not a Construction
commitment. Not a purchase order and not an actual cost. Nothing in this file
writes to the ledger, and `test_r_reservation_creates_no_accounting` exists to
keep it that way.

### The one rule everything here serves

```
    ONE ECONOMIC OBLIGATION IN ONE CONTROL STAGE AT A TIME.
```

While demand is reserved, it consumes capacity here. The moment a purchase
order confirms, Construction's commitment becomes authoritative and the
reservation must stop consuming anything — converted, in the same transaction,
never both at once. A reservation of 3,000,000 sitting beside a commitment of
3,000,000 for the same demand would report 6,000,000 of exposure on a project
that owes 3,000,000, and would do it in the direction that looks prudent,
which is how such an error survives review.

### Line level, not header

Rule 2. Construction controls money per cost code, so a requisition for
2,000,000 of concrete and 1,000,000 of electrical is two reservations against
two positions, not one 3,000,000 reservation against nothing in particular.
The cost code itself is added to this model by `real_estate_construction` —
Construction owns cost codes and depends on Procurement, so the foreign key
cannot point the other way.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.sql import index_exists

RESERVATION_STATE = [
    ('draft', 'Draft'),
    ('reserved', 'Reserved'),
    ('converted', 'Converted'),
    ('released', 'Released'),
    ('cancelled', 'Cancelled'),
    ('expired', 'Expired'),
]

#: States in which a reservation consumes purchasing capacity. Exactly one.
ACTIVE_STATES = ('reserved',)


class ProcurementReservation(models.Model):
    _name = 'realestate.procurement.reservation'
    _description = 'Procurement Reservation'
    _order = 'id desc'

    name = fields.Char(required=True, copy=False, readonly=True,
                       default=lambda self: _('New'))
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        related='company_id.currency_id', readonly=True,
        string='Control Currency',
        help="Every control comparison happens in company currency, because "
             "that is the currency Construction's budget and commitment are "
             "stated in. A USD request measured against an EGP budget is not "
             "a comparison.")
    project_id = fields.Many2one(
        'realestate.project', index=True, ondelete='restrict', required=True)
    request_id = fields.Many2one(
        'realestate.material.request', string='Requisition', required=True,
        index=True, ondelete='restrict')
    request_line_id = fields.Many2one(
        'realestate.material.request.line', string='Requisition Line',
        index=True, ondelete='set null',
        help="The demand this reservation is for. Cleared rather than "
             "cascaded if the line is ever removed, so the record of what was "
             "reserved survives the record of what was asked for.")
    line_description = fields.Char(
        readonly=True,
        help="What the line said when it was reserved. A snapshot, so the "
             "register still reads correctly after a revision.")
    revision = fields.Integer(
        readonly=True, default=0,
        help="The requisition revision this reservation belongs to. A "
             "revision releases its predecessor and reserves again — the same "
             "demand at a later version is not the same authorisation.")

    # -- Amounts -------------------------------------------------------
    source_currency_id = fields.Many2one(
        'res.currency', readonly=True, string='Requested In')
    source_amount = fields.Monetary(
        currency_field='source_currency_id', readonly=True,
        string='Requested Amount',
        help="Tax-exclusive, in the currency the requisition was raised in.")
    rate_date = fields.Date(
        readonly=True,
        help="The date the control rate was taken on. Kept because the same "
             "amount converts differently a month later, and a control figure "
             "whose basis date is unknown cannot be reconciled.")
    amount_reserved = fields.Monetary(
        readonly=True, string='Reserved',
        help="The approved, tax-exclusive amount in company currency. Rule 3: "
             "budget and commitment are compared tax-exclusive, so the "
             "reservation must be too — reserving the VAT-inclusive figure "
             "would overstate every position by the tax rate.")
    amount_converted = fields.Monetary(
        compute='_compute_amount_converted', store=True, string='Converted',
        help="How much of it has become Construction commitment. Computed "
             "from the conversion records rather than accumulated, so "
             "cancelling a purchase order gives the capacity back by "
             "reversing one row instead of by arithmetic nobody can audit.")
    amount_released = fields.Monetary(
        readonly=True, string='Released',
        help="How much stopped consuming capacity without becoming a "
             "commitment.")
    amount_active = fields.Monetary(
        compute='_compute_amount_active', store=True, string='Active',
        help="Reserved minus converted minus released. This, and only this, "
             "is what reduces available-to-procure.")

    requested_basis_amount = fields.Monetary(
        readonly=True, string='Requested Basis',
        help="The estimate at submission. Kept beside the approved amount so "
             "an approval that changed the number is visible rather than "
             "inferred.")

    # -- Lifecycle -----------------------------------------------------
    state = fields.Selection(
        RESERVATION_STATE, default='draft', required=True, index=True,
        help="Converted and Released are deliberately different endings. "
             "Converted means an authoritative commitment replaced it; "
             "Released means the demand stopped consuming capacity without "
             "ever becoming one.")
    reservation_date = fields.Datetime(readonly=True)
    converted_date = fields.Datetime(readonly=True)
    released_date = fields.Datetime(readonly=True)
    expiry_date = fields.Date(
        readonly=True,
        help="Set from the company's expiry policy at reservation time. Blank "
             "means it does not expire, which is the default: a reservation "
             "that vanished on a date nobody chose would release real demand "
             "silently.")
    release_reason = fields.Text(readonly=True)
    notes = fields.Text()

    conversion_ids = fields.One2many(
        'realestate.procurement.reservation.conversion', 'reservation_id',
        string='Conversions', readonly=True)
    purchase_order_ids = fields.Many2many(
        'purchase.order', string='Purchase Orders',
        compute='_compute_purchase_orders',
        help="Every order that consumed part of this reservation. Many, "
             "because a split award is two orders against one authorisation.")

    # -- Control snapshot ----------------------------------------------
    control_status = fields.Selection([
        ('ok', 'Within Budget'),
        ('over_budget', 'Over Budget'),
        ('insufficient_data', 'Position Unknown'),
    ], readonly=True, index=True,
        help="What the control position said when this was reserved. A "
             "snapshot: the project's position changes constantly and this "
             "records the one the decision was made against.")
    control_note = fields.Text(readonly=True)
    exception_id = fields.Many2one(
        'realestate.procurement.control.exception', readonly=True,
        ondelete='set null',
        help="The over-budget evidence, where the policy produced any.")

    anomaly_note = fields.Text(
        compute='_compute_anomaly', string='Anomalies',
        help="M3U findings. Stated, never auto-corrected — a control system "
             "that quietly fixes its own inconsistencies destroys the "
             "evidence that they happened.")
    has_anomaly = fields.Boolean(compute='_compute_anomaly', store=True,
                                 index=True)

    # ------------------------------------------------------------------
    @api.depends('conversion_ids.amount', 'conversion_ids.state')
    def _compute_amount_converted(self):
        for rec in self:
            rec.amount_converted = sum(rec.conversion_ids.filtered(
                lambda c: c.state == 'applied').mapped('amount'))

    @api.depends('amount_reserved', 'amount_converted', 'amount_released')
    def _compute_amount_active(self):
        for rec in self:
            rec.amount_active = max(0.0, (rec.amount_reserved or 0.0)
                                    - (rec.amount_converted or 0.0)
                                    - (rec.amount_released or 0.0))

    def _compute_purchase_orders(self):
        for rec in self:
            rec.purchase_order_ids = rec.conversion_ids.mapped(
                'po_line_id.order_id')

    @api.depends('state', 'amount_active', 'amount_converted',
                 'request_id.state', 'conversion_ids.po_line_id',
                 'expiry_date')
    def _compute_anomaly(self):
        today = fields.Date.context_today(self)
        for rec in self:
            findings = []
            if rec.state == 'reserved' and rec.request_id.state == 'cancelled':
                findings.append(_(
                    "The requisition is cancelled but this reservation is "
                    "still consuming capacity."))
            if rec.amount_converted and not rec.conversion_ids.filtered(
                    lambda c: c.po_line_id.state in ('purchase', 'done')):
                findings.append(_(
                    "Converted, but no confirmed purchase line accounts for "
                    "it — the commitment it converted into cannot be found."))
            if rec.amount_converted > (rec.amount_reserved or 0.0) + 0.01:
                findings.append(_(
                    "More has been converted than was ever reserved."))
            if rec.state == 'reserved' and rec.expiry_date \
                    and rec.expiry_date < today:
                findings.append(_(
                    "Expired on %s and is still active.") % rec.expiry_date)
            rec.anomaly_note = '\n'.join(findings)
            rec.has_anomaly = bool(findings)

    # ------------------------------------------------------------------
    def init(self):
        """One active reservation per requisition line, enforced by Postgres.

        A Python constraint is not enough and the difference matters here.
        Two transactions reserving the same line both pass an ORM check that
        reads a row neither has committed yet; only a unique index rejects the
        second insert. It is partial because the uniqueness applies to *active*
        reservations — a line that was released and reserved again has two
        rows and should.
        """
        super().init()
        if not index_exists(self.env.cr,
                            'proc_reservation_one_active_per_line'):
            self.env.cr.execute("""
                CREATE UNIQUE INDEX proc_reservation_one_active_per_line
                    ON realestate_procurement_reservation (request_line_id)
                 WHERE state = 'reserved' AND request_line_id IS NOT NULL
            """)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.procurement.reservation') or _('New')
        return super().create(vals_list)

    @api.constrains('company_id', 'project_id', 'request_id')
    def _check_company(self):
        """M3W — company A's demand cannot reserve company B's capacity."""
        for rec in self:
            for record, label in ((rec.project_id, _('project')),
                                  (rec.request_id, _('requisition'))):
                other = record.company_id if record else False
                if other and other != rec.company_id:
                    raise ValidationError(_(
                        "%(label)s belongs to %(other)s, this reservation to "
                        "%(company)s.", label=label, other=other.display_name,
                        company=rec.company_id.display_name))

    _AUDIT_FIELDS = {
        'amount_reserved', 'amount_converted', 'amount_released', 'state',
        'request_id', 'request_line_id', 'project_id', 'company_id',
        'source_amount', 'exception_id',
    }

    def write(self, vals):
        """Amounts and state move through the methods below, or not at all.

        Not a permission check — a manager with every right still should not
        be able to type a different number into a control record, because the
        register's totals have to reconcile to the availability service and a
        hand-edited amount reconciles to nothing.
        """
        if not self.env.context.get('re_reservation_engine'):
            touched = self._AUDIT_FIELDS & set(vals)
            if touched:
                raise UserError(_(
                    "%(fields)s on a reservation are maintained by the "
                    "control engine. Release it, or revise the requisition — "
                    "either way the change stays visible.",
                    fields=', '.join(sorted(touched))))
        return super().write(vals)

    def unlink(self):
        """M3Z — anything that ever consumed capacity is history now."""
        live = self.filtered(lambda r: r.state != 'draft')
        if live:
            raise UserError(_(
                "%s has been part of a control position. Release or cancel "
                "it; deleting it would remove the explanation for a number "
                "somebody has already read.")
                % ', '.join(live.mapped('name')))
        return super().unlink()

    def _engine(self):
        return self.with_context(re_reservation_engine=True)

    # ------------------------------------------------------------------
    # Creation — M3B
    # ------------------------------------------------------------------
    @api.model
    def reserve_request(self, request, initial_state='reserved'):
        """Reserve the approved demand of one requisition.

        Called after the last approval step, never before: the brief's default
        control point is final approval, because reserving submitted demand
        would let anybody consume a project's capacity by typing a request.

        Idempotent by construction — a line that already has an active
        reservation is skipped — so re-approval after a revision, the
        migration activation runbook and a retried transaction all produce the
        same result.

        `initial_state` is `reserved` for the ordinary path and `draft` for the
        migration rollout, where reservations are staged for a manager to read
        before any capacity moves.
        """
        Control = self.env['realestate.procurement.control']
        project = request.project_id
        policy = Control.budget_policy_for(project, request.company_id)
        if policy == 'none':
            return self.browse()

        lines = request.line_ids.filtered(
            lambda ln: not ln.reservation_ids.filtered(
                lambda r: r.state in ACTIVE_STATES))
        if not lines:
            return self.browse()
        if not project:
            # Rule 4. No project is not "no constraint" — it is a position
            # this system cannot establish, and under a blocking policy that
            # is a refusal rather than a free pass.
            if policy == 'block':
                raise UserError(_(
                    "%s names no project, so there is no budget to reserve "
                    "against.") % request.name)
            return self.browse()

        demand = {}
        for line in lines:
            code_id = line._control_cost_code_id()
            demand.setdefault(code_id, []).append(line)

        # Lock before reading. Everything from here to the end of the
        # transaction is serialised per scope, so the position read below is
        # still true when the reservation is written.
        Control.lock_control_scopes([
            (request.company_id.id, project.id, code_id)
            for code_id in demand])
        positions = Control.positions_by_cost_code(project, list(demand))

        created = self.browse()
        for code_id, code_lines in demand.items():
            position = positions[code_id]
            amount = sum(ln._control_amount() for ln in code_lines)
            # A draft reservation consumes nothing, so there is nothing yet to
            # enforce a policy against. The check runs when somebody activates
            # it — which is the moment the capacity actually moves, and the
            # moment the position is worth reading again anyway.
            exception = False if initial_state == 'draft' else \
                request._enforce_budget_policy(policy, position, amount,
                                               code_id)
            for line in code_lines:
                created |= self._create_for_line(
                    line, position, exception, state=initial_state)
        return created

    @api.model
    def _create_for_line(self, line, position, exception=None,
                         state='reserved'):
        request = line.request_id
        company = request.company_id
        control_amount = line._control_amount()
        values = {
            'company_id': company.id,
            'project_id': request.project_id.id,
            'request_id': request.id,
            'request_line_id': line.id,
            'line_description': line.description or (
                line.product_id.display_name or ''),
            'revision': request.revision,
            'source_currency_id': request.currency_id.id,
            'source_amount': line.estimated_cost,
            'rate_date': fields.Date.context_today(line),
            'amount_reserved': control_amount,
            'requested_basis_amount': control_amount,
            'state': state,
            'reservation_date': fields.Datetime.now(),
            'expiry_date': request._reservation_expiry_date(),
            'control_status': position['status'],
            'control_note': self.env[
                'realestate.procurement.control'].describe_position(position),
            'exception_id': exception.id if exception else False,
        }
        values.update(line._control_scope_values())
        return self._engine().create(values)

    def action_reserve(self):
        """Activate a draft reservation — the migration rollout path.

        The runbook creates draft reservations for existing approved demand so
        a manager can read the list before a single unit of capacity moves.
        This is the button that then moves it.
        """
        Control = self.env['realestate.procurement.control']
        # Server-side, not a `groups=` on the button. Activating a draft
        # reservation moves real purchasing capacity, and a check that lives
        # in a view is absent from every other way this method can be called.
        if not self.env.user.has_group(
                'real_estate_procurement.group_procurement_manager'):
            raise UserError(_(
                "Activating a reservation consumes a project's purchasing "
                "capacity. That is a procurement manager's decision."))
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("%s is not a draft reservation.") % rec.name)
            code_id = rec._cost_code_scope_id()
            Control.lock_control_scopes([(rec.company_id.id, rec.project_id.id,
                                          code_id)])
            position = Control.get_procurement_control_position(
                rec.project_id, rec.cost_code_id
                if 'cost_code_id' in rec._fields else None)
            policy = Control.budget_policy_for(rec.project_id, rec.company_id)
            exception = rec.request_id._enforce_budget_policy(
                policy, position, rec.amount_reserved, code_id)
            if exception:
                rec._engine().write({'exception_id': exception.id})
            rec._engine().write({
                'state': 'reserved',
                'reservation_date': fields.Datetime.now(),
            })
        return True

    # ------------------------------------------------------------------
    # Conversion — M3L
    # ------------------------------------------------------------------
    def _convert(self, amount, po_line, qty=0.0):
        """Turn part or all of this reservation into commitment.

        Called from the purchase-order confirmation gate, inside the same
        transaction as `button_confirm()`. If the confirmation rolls back the
        conversion rolls back with it, and if the conversion fails the
        confirmation goes with it — the two are one financial event and there
        is no correct half of it.
        """
        self.ensure_one()
        if self.state != 'reserved':
            raise UserError(_(
                "%s is not active, so there is nothing to convert.")
                % self.name)
        rounding = self.currency_id.rounding or 0.01
        if amount <= 0:
            return self.env['realestate.procurement.reservation.conversion']
        if amount > self.amount_active + rounding:
            raise UserError(_(
                "%(order)s would convert %(amount)s against %(name)s, which "
                "has %(active)s left. Converting more than was authorised is "
                "the double-count this control exists to prevent.",
                order=po_line.order_id.display_name,
                amount=self._format(amount),
                name=self.name, active=self._format(self.amount_active)))
        conversion = self.env[
            'realestate.procurement.reservation.conversion']._engine().create({
                'reservation_id': self.id,
                'po_line_id': po_line.id,
                'amount': amount,
                'quantity': qty,
                'company_id': self.company_id.id,
            })
        self.invalidate_recordset(['amount_converted', 'amount_active'])
        self._engine().write({'converted_date': fields.Datetime.now()})
        self._sync_state()
        return conversion

    @api.model
    def _reverse_conversions(self, po_lines, reason):
        """Give the capacity back when a confirmed order is cancelled.

        A cancelled purchase order stops being a Construction commitment the
        moment its state changes, because commitment is read from confirmed
        orders. If the reservation stayed converted, the demand would exist in
        neither control stage — approved, unordered, and invisible to
        availability. That is the same double-count defect with the sign
        flipped, and it is easier to miss because the number gets smaller.
        """
        conversions = self.env[
            'realestate.procurement.reservation.conversion'].sudo().search([
                ('po_line_id', 'in', po_lines.ids),
                ('state', '=', 'applied')])
        if not conversions:
            return False
        reservations = conversions.mapped('reservation_id')
        conversions._engine().write({
            'state': 'reversed',
            'reversal_reason': reason,
        })
        reservations.invalidate_recordset(['amount_converted',
                                           'amount_active'])
        for reservation in reservations:
            if reservation.state not in ('converted', 'reserved') \
                    or reservation.amount_active <= 0:
                continue
            if reservation.request_id.state in ('cancelled', 'rejected'):
                continue
            competing = reservation.search_count([
                ('id', '!=', reservation.id),
                ('request_line_id', '=', reservation.request_line_id.id),
                ('state', '=', 'reserved')]) if reservation.request_line_id \
                else 0
            if competing:
                # The demand was re-approved and reserved again after this
                # conversion. Reactivating would mean two live reservations
                # for one line — which the unique index refuses, and rightly.
                # Left converted with an anomaly instead of guessing which of
                # the two authorisations the project meant.
                continue
            reservation._engine().write({'state': 'reserved'})
        return True

    def _release(self, reason, state='released'):
        """Stop consuming capacity, keeping the record of having done so."""
        for rec in self:
            if rec.state not in ('reserved', 'draft'):
                continue
            if not reason:
                raise UserError(_(
                    "Releasing %s needs a reason. Capacity coming back with "
                    "no explanation is indistinguishable from a bug.")
                    % rec.name)
            rec._engine().write({
                'amount_released': rec.amount_released + rec.amount_active,
                'released_date': fields.Datetime.now(),
                'release_reason': reason,
            })
            rec._sync_state(ended_as=state)
        return True

    def _sync_state(self, ended_as=None):
        """A reservation is active until nothing is left of it."""
        for rec in self:
            if rec.amount_active > (rec.currency_id.rounding or 0.01) / 2:
                if rec.state == 'draft':
                    continue
                rec._engine().write({'state': 'reserved'})
                continue
            if ended_as and not rec.amount_converted:
                rec._engine().write({'state': ended_as})
            elif rec.amount_converted:
                # Converted outranks released when both happened to the same
                # reservation. An order that came in under the approved amount
                # converts most of it and releases the rest, and the ending
                # that matters is the one that produced a commitment — calling
                # it "released" would file a reservation that became real
                # money alongside the ones that came to nothing.
                rec._engine().write({'state': 'converted'})
            else:
                rec._engine().write({'state': 'released'})

    def action_open_release_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Release Reservation'),
            'res_model': 'realestate.procurement.reservation.release',
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'new',
            'context': {'default_reservation_id': self.id},
        }

    @api.model
    def expire_due_reservations(self, company=None):
        """Cron/manager action — release what a company chose to time out.

        Only runs where a company set an expiry; the default of zero means
        this finds nothing, which is deliberate.
        """
        today = fields.Date.context_today(self)
        due = self.search([('state', '=', 'reserved'),
                           ('expiry_date', '!=', False),
                           ('expiry_date', '<', today)]
                          + ([('company_id', '=', company.id)] if company
                             else []))
        for rec in due:
            rec._release(_("Expired on %s without being sourced.")
                         % rec.expiry_date, state='expired')
        return len(due)

    # ------------------------------------------------------------------
    def _cost_code_scope_id(self):
        self.ensure_one()
        if 'cost_code_id' in self._fields:
            return self.cost_code_id.id
        return False

    def _format(self, amount):
        return self.env['realestate.procurement.control'
                        ].format_control_amount(amount, self.currency_id)


class ProcurementReservationConversion(models.Model):
    """One purchase line consuming part of one reservation.

    A child model rather than a `converted_by_po_id` field on the reservation,
    because M3O requires split award to be possible without a migration: one
    reservation of 1,000,000 may be consumed 600,000 by one order and 400,000
    by another, and a single foreign key could only ever name one of them.
    """
    _name = 'realestate.procurement.reservation.conversion'
    _description = 'Procurement Reservation Conversion'
    _order = 'id'

    reservation_id = fields.Many2one(
        'realestate.procurement.reservation', required=True,
        ondelete='cascade', index=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        related='company_id.currency_id', readonly=True)
    po_line_id = fields.Many2one(
        'purchase.order.line', required=True, ondelete='cascade', index=True)
    order_id = fields.Many2one(
        related='po_line_id.order_id', store=True, readonly=True)
    amount = fields.Monetary(readonly=True)
    quantity = fields.Float(readonly=True)
    conversion_date = fields.Datetime(
        default=fields.Datetime.now, readonly=True)
    state = fields.Selection([
        ('applied', 'Applied'),
        ('reversed', 'Reversed'),
    ], default='applied', required=True, index=True, readonly=True,
        help="Reversed when the order that made the commitment was "
             "cancelled. The row stays: it is the record of a commitment "
             "that existed for a while, and deleting it would make the "
             "reservation's history unreadable.")
    reversal_reason = fields.Char(readonly=True)

    def _engine(self):
        return self.with_context(re_reservation_engine=True)

    def write(self, vals):
        if not self.env.context.get('re_reservation_engine'):
            raise UserError(_(
                "A conversion records what a purchase order committed. It is "
                "not editable — reverse the order instead."))
        return super().write(vals)
