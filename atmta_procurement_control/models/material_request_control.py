"""What a requisition means to the control system — Wave 6 / AD-008.

`atmta_procurement_request` owns demand: what was asked for, by whom, against
which project, and how it was revised. It does not know what a reservation or
an approval step is, which is what lets it install on its own.

Everything here is the other half: the reverse links into the control tables,
the figures derived from them, and the behaviour at the seams Request leaves
open. Every method body below was moved from `material_request.py`
unchanged — this file changes where the code lives, not what it does.
"""
import hashlib
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class MaterialRequestControl(models.Model):
    """Control-facing extension of the requisition."""
    _inherit = 'realestate.material.request'


    def _control_findings(self):
        """M3U — what the control records say about each other.

        Stated, never repaired. Every one of these is a disagreement between
        two systems, and the useful response to that is a person looking at
        it, not this module choosing which of the two it prefers.
        """
        self.ensure_one()
        findings = []
        policy = self.env['realestate.procurement.control'].budget_policy_for(
            self.project_id, self.company_id)
        approved_states = ('approved', 'sourcing')
        if policy != 'none' and self.state in approved_states \
                and self.project_id and not self.reservation_ids:
            findings.append(_(
                "Approved demand with no reservation, under a %s policy.")
                % policy)
        if self.state == 'cancelled' and self.reserved_amount:
            findings.append(_(
                "Cancelled, but %s of capacity is still reserved.")
                % self.env['realestate.procurement.control'
                           ].format_control_amount(
                    self.reserved_amount, self.company_id.currency_id))
        anomalous = self.sudo().reservation_ids.filtered('has_anomaly')
        for reservation in anomalous:
            findings.append('%s: %s' % (reservation.name,
                                        reservation.anomaly_note))
        if self.approved_by_id and self.approved_by_id == self.requested_by_id:
            findings.append(_(
                "Raised and approved by the same person (%s).")
                % self.approved_by_id.display_name)
        return findings

    # ------------------------------------------------------------------
    # M3 — control position and reservation
    # ------------------------------------------------------------------
    reservation_ids = fields.One2many(
        'realestate.procurement.reservation', 'request_id',
        string='Reservations', readonly=True)
    reserved_amount = fields.Monetary(
        compute='_compute_reservation', store=True,
        help="Approved demand still consuming this project's purchasing "
             "capacity. It is not a commitment: nobody is owed it, no journal "
             "entry exists and Construction's figures are untouched.")
    converted_amount = fields.Monetary(
        compute='_compute_reservation', store=True,
        help="How much of the reservation has become Construction "
             "commitment through a confirmed order.")
    reservation_status = fields.Selection([
        ('none', 'Not Reserved'),
        ('reserved', 'Reserved'),
        ('partial', 'Partly Converted'),
        ('converted', 'Converted'),
        ('released', 'Released'),
    ], compute='_compute_reservation', store=True, index=True,
        help="Approved and reserved are different facts, and the lifecycle "
             "keeps them in one field each rather than inventing a state that "
             "means both.")

    @api.depends('reservation_ids.state', 'reservation_ids.amount_active',
                 'reservation_ids.amount_converted')
    def _compute_reservation(self):
        for rec in self:
            # Read the control records with elevated rights and nothing else.
            # A requisition is legible to plenty of people who have no
            # business editing a reservation, and the alternative — granting
            # everyone who can open a request read access on the control
            # tables — is a much wider door than this one line.
            reservations = rec.sudo().reservation_ids
            rec.reserved_amount = sum(reservations.filtered(
                lambda r: r.state == 'reserved').mapped('amount_active'))
            rec.converted_amount = sum(reservations.mapped('amount_converted'))
            if not reservations:
                rec.reservation_status = 'none'
            elif rec.reserved_amount and rec.converted_amount:
                rec.reservation_status = 'partial'
            elif rec.reserved_amount:
                rec.reservation_status = 'reserved'
            elif rec.converted_amount:
                rec.reservation_status = 'converted'
            else:
                rec.reservation_status = 'released'

    control_status = fields.Selection([
        ('ok', 'Within Budget'),
        ('over_budget', 'Over Budget'),
        ('insufficient_data', 'Position Unknown'),
    ], compute='_compute_control_position',
        help="The live position, read from Construction every time it is "
             "asked for. Deliberately not stored: it is the difference "
             "between three numbers that move independently, and a stored "
             "copy would be wrong for as long as it took something else to "
             "change.")
    control_note = fields.Text(compute='_compute_control_position')
    approval_control_status = fields.Selection([
        ('ok', 'Within Budget'),
        ('over_budget', 'Over Budget'),
        ('insufficient_data', 'Position Unknown'),
    ], readonly=True, copy=False, index=True, string='Position At Submission',
        help="What the position said when this went for approval. The "
             "approval matrix matched against this, so it is the figure the "
             "approvers were answering — not today's.")

    def _compute_control_position(self):
        Control = self.env['realestate.procurement.control']
        for rec in self:
            # The request's own reservation is excluded from the availability
            # it is measured against. Counting it would compare this demand to
            # a figure this demand has already reduced, so every reserved
            # requisition would look as though it no longer fitted.
            positions = rec._control_positions(ignore_own_reservations=True)
            if not positions:
                rec.control_status = False
                rec.control_note = False
                continue
            rec.control_status = rec._demand_status(positions)
            rec.control_note = '\n\n'.join(
                Control.describe_position(position)
                for position in positions.values())

    def _control_positions(self, ignore_own_reservations=False):
        """`{cost_code_id: position}` for every scope this request touches."""
        self.ensure_one()
        if not self.project_id or not self.line_ids:
            return {}
        Control = self.env['realestate.procurement.control']
        code_ids = list({line._control_cost_code_id()
                         for line in self.line_ids})
        ignore = self.reservation_ids.filtered(
            lambda r: r.state == 'reserved') if ignore_own_reservations \
            else None
        return Control.positions_by_cost_code(
            self.project_id, code_ids, ignore_reservations=ignore)

    def _demand_status(self, positions):
        """Does *this demand* fit? Not: is the position already negative.

        The two questions have different answers and the approval matrix needs
        the first. A project with 1,000,000 left has a perfectly healthy
        position right up to the moment somebody asks it for 3,000,000, and a
        rule that routes over-budget demand to a director has to fire then —
        not a month later when the position has gone negative and the money is
        already committed.

        The worst answer wins across the scopes: a request is only fine if all
        of it is.
        """
        self.ensure_one()
        demand = {}
        for line in self.line_ids:
            code_id = line._control_cost_code_id()
            demand[code_id] = demand.get(code_id, 0.0) + line._control_amount()
        rounding = (self.company_id.currency_id.rounding or 0.01)
        statuses = set()
        for code_id, position in positions.items():
            if position['status'] == 'insufficient_data':
                statuses.add('insufficient_data')
            elif demand.get(code_id, 0.0) - position['available'] > rounding:
                statuses.add('over_budget')
        for status in ('insufficient_data', 'over_budget'):
            if status in statuses:
                return status
        return 'ok'

    # ---------- Approval matrix ----------
    approval_step_ids = fields.One2many(
        'realestate.procurement.approval.step', 'request_id',
        string='Approval Steps', copy=False,
    )
    all_approvals_done = fields.Boolean(
        compute='_compute_all_approvals_done', store=True,
    )
    current_step_id = fields.Many2one(
        'realestate.procurement.approval.step',
        compute='_compute_approval_progress', store=True,
        string='Awaiting')
    waiting_since = fields.Datetime(
        compute='_compute_approval_progress', store=True)
    days_waiting = fields.Integer(
        compute='_compute_approval_progress', store=True,
        help="M3R — how long this has been sitting with somebody. An "
             "operational number, kept on the record so a register can sort "
             "by it without a dashboard.")

    @api.depends('approval_step_ids.decision')
    def _compute_all_approvals_done(self):
        """Every step decided, and none of them a refusal.

        The old version asked `all(s.approved)`, which is the same question
        only as long as the sole alternative to approved is not-yet-approved.
        It no longer is.
        """
        for rec in self:
            steps = rec.approval_step_ids
            rec.all_approvals_done = bool(steps) and not any(
                step.decision in ('pending', 'rejected') for step in steps)

    @api.depends('approval_step_ids.decision', 'approval_step_ids.sequence',
                 'submitted_on', 'state')
    def _compute_approval_progress(self):
        now = fields.Datetime.now()
        for rec in self:
            pending = rec.approval_step_ids.filtered(
                lambda s: s.decision == 'pending').sorted('sequence')
            rec.current_step_id = pending[:1]
            if rec.state != 'submitted' or not pending:
                rec.waiting_since = False
                rec.days_waiting = 0
                continue
            rec.waiting_since = pending[0].requested_on or rec.submitted_on
            rec.days_waiting = (now - rec.waiting_since).days \
                if rec.waiting_since else 0

    def _generate_approval_steps(self):
        """Snapshot the authority this requisition needs, as it stands today.

        M3H: the steps record what was required, not a pointer to what the
        configuration currently says. A rule renamed, re-bracketed or archived
        next quarter must not change the answer to "who approved the
        3,000,000 of concrete in March".
        """
        self.ensure_one()
        Step = self.env['realestate.procurement.approval.step']
        Rule = self.env['realestate.procurement.approval.rule']
        amount = self._control_amount()
        status = self.approval_control_status or 'ok'
        candidates = Rule.search(
            ['|', ('company_id', '=', False),
             ('company_id', '=', self.company_id.id)],
            order='sequence, min_amount')
        matching = candidates.filtered(
            lambda rule: rule._matches(self, amount, status))
        # Undecided snapshots from an earlier cycle are replaced; decided ones
        # are history and stay.
        self.approval_step_ids.filtered(
            lambda s: s.decision == 'pending').unlink()
        basis = self._basis_hash()
        for seq, rule in enumerate(matching, start=1):
            step = Step.create({
                'request_id': self.id,
                'rule_id': rule.id,
                'rule_name': rule.name,
                'group_id': rule.group_id.id,
                'approver_user_id': rule.approver_user_id.id or False,
                'amount_basis': amount,
                'basis_hash': basis,
                'sequence': rule.sequence or seq * 10,
                'requested_on': fields.Datetime.now(),
            })
            step._schedule_activity()

    def _check_not_self_approval(self, amount=None):
        """M3I — the requester does not approve the requester's request.

        Phase 0's check asked whether the user was in an approver group,
        which is a question about capability. Separation of duties is a
        question about identity, and the two are unrelated: in a small
        company almost everybody is in the approver group, which is precisely
        where self-approval is most likely and least visible.

        A company may allow it below a stated limit. That is a decision
        somebody makes in configuration, not a default and not an accident.
        """
        self.ensure_one()
        user = self.env.user
        if user != self.requested_by_id and user != self.create_uid:
            return True
        company = self.company_id
        amount = self._control_amount() if amount is None else amount
        if company.procurement_allow_self_approval \
                and amount <= company.procurement_self_approval_limit:
            return True
        raise UserError(_(
            "%(name)s was raised by you. Asking for something and authorising "
            "it are two roles, and this company has not chosen to let one "
            "person hold both%(limit)s.",
            name=self.name,
            limit=(_(" above %s") % self.env[
                'realestate.procurement.control'].format_control_amount(
                    company.procurement_self_approval_limit,
                    company.currency_id))
            if company.procurement_allow_self_approval else ''))

    def _maybe_promote_after_approval(self):
        """Called after every step decision; promote when the cycle is done."""
        for rec in self:
            if rec.state == 'submitted' and rec.all_approvals_done:
                rec._approve_now()

    def _approve_now(self):
        """The single place a requisition becomes approved demand.

        Reservation happens here and nowhere else, so there is one answer to
        "when does approved demand start consuming capacity" instead of one
        per approval path.
        """
        self.ensure_one()
        self.state = 'approved'
        self.approved_by_id = self.env.user
        self.approval_date = fields.Datetime.now()
        self._reserve_approved_demand()
        self.message_post(body=_(
            "Approved. %s") % (
                _("Reserved %s of purchasing capacity.")
                % self.env['realestate.procurement.control'
                           ].format_control_amount(
                    self.reserved_amount, self.company_id.currency_id)
                if self.reserved_amount else
                _("No purchasing capacity was reserved.")))

    def _on_step_rejected(self, step):
        """A refusal is a state, and it keeps the approvals that preceded it."""
        self.ensure_one()
        self.with_context(re_procurement_revision=True).write({
            'state': 'rejected',
        })
        self.approval_step_ids.filtered(
            lambda s: s.decision == 'pending').write({'decision': 'cancelled'})
        self._release_reservations(_(
            "Requisition rejected at %s.") % (step.rule_name or _('approval')))
        self.message_post(body=_(
            "Rejected at %(rule)s by %(user)s: %(reason)s",
            rule=step.rule_name or '', user=self.env.user.display_name,
            reason=step.comment or ''))

    # ------------------------------------------------------------------
    # Reservation orchestration — M3B / M3E / M3N
    # ------------------------------------------------------------------
    def _reserve_approved_demand(self):
        """Consume purchasing capacity for approved demand."""
        self.ensure_one()
        return self.env['realestate.procurement.reservation'].reserve_request(
            self)

    def _enforce_budget_policy(self, policy, position, amount, code_id):
        """Apply the company's chosen answer to "this does not fit".

        Returns the exception record that authorises the overrun, or False
        when none was needed. Raises when the policy says no.

        The four policies are genuinely different decisions and none of them
        is silently the others:

        ```
            NONE               no reservation at all; this is not reached
            WARN               reserve, and leave evidence of the overage
            APPROVAL_REQUIRED  reserve only with authority already granted
            BLOCK              refuse
        ```

        Nothing here increases a Construction budget. A project that needs
        more money needs a change order, and manufacturing budget inside
        Procurement would put the two systems permanently out of agreement.
        """
        self.ensure_one()
        Control = self.env['realestate.procurement.control']
        currency = self.company_id.currency_id
        rounding = currency.rounding or 0.01
        unknown = position['status'] == 'insufficient_data'
        shortfall = amount - position['available']
        over = shortfall > rounding and not unknown
        if not over and not unknown:
            return False

        note = Control.describe_position(position)
        if policy == 'block':
            raise UserError(_(
                "%(name)s cannot be approved.\n\n%(reason)s\n\n%(note)s\n\n"
                "Raise a budget exception for authority to exceed it, or a "
                "change order to fund it.",
                name=self.name,
                reason=(_("The control position could not be established, and "
                          "unknown is not the same as available.") if unknown
                        else _("It needs %(amount)s and %(available)s is "
                               "available — %(short)s short.",
                               amount=Control.format_control_amount(
                                   amount, currency),
                               available=Control.format_control_amount(
                                   position['available'], currency),
                               short=Control.format_control_amount(
                                   shortfall, currency))),
                note=note))

        if policy == 'approval_required':
            exception = self._find_authorised_exception(code_id, amount)
            if not exception:
                raise UserError(_(
                    "%(name)s exceeds what is available and this company "
                    "requires that to be authorised first.\n\n%(note)s\n\n"
                    "Request a budget exception; a procurement manager "
                    "approves it, and then this can be approved.",
                    name=self.name, note=note))
            return exception

        # WARN — allowed, and recorded. An over-budget approval that leaves
        # nothing behind is indistinguishable from one that fitted.
        if not over:
            # The position is unknown rather than exceeded. That is carried on
            # the reservation's own control status and shown in the register;
            # manufacturing an "exception" for every uncoded line would fill
            # the exception register with records nobody decided anything
            # about, and the register is where real decisions have to be
            # findable.
            return False
        return self.env['realestate.procurement.control.exception'].create({
            'exception_type': 'over_budget',
            'company_id': self.company_id.id,
            'project_id': self.project_id.id,
            'request_id': self.id,
            'requested_amount': amount,
            'available_amount': position['available'],
            'policy': policy,
            'control_note': note,
            'reason': _(
                "Approved under a Warn policy: the demand exceeded available "
                "capacity by %s and the company's policy is to record rather "
                "than refuse.") % Control.format_control_amount(
                    shortfall, currency),
            'state': 'noted',
        })

    def _find_authorised_exception(self, code_id, amount):
        """An approved exception big enough to cover this scope.

        Deliberately strict about the amount: an exception approved for
        500,000 does not authorise 5,000,000 because somebody edited the
        requisition afterwards.
        """
        self.ensure_one()
        domain = [
            ('request_id', '=', self.id),
            ('state', '=', 'approved'),
            ('exception_type', 'in', ('over_budget', 'insufficient_data')),
            ('requested_amount', '>=', amount - 0.01),
        ]
        Exception_ = self.env['realestate.procurement.control.exception']
        if code_id and 'cost_code_id' in Exception_._fields:
            domain += ['|', ('cost_code_id', '=', False),
                       ('cost_code_id', '=', code_id)]
        return Exception_.search(domain, limit=1)

    def _release_reservations(self, reason):
        """Stop consuming capacity, for every reason that is not a purchase.

        Cancellation, rejection, a return to draft and a revision all mean the
        same thing to the control system: this demand is no longer the demand
        that was authorised. Conversion is the one ending that goes elsewhere.
        """
        # Elevated on purpose, and only here. Cancelling, rejecting or
        # revising a requisition is a decision the user is entitled to make;
        # the reservation is a consequence of it, not a record they are being
        # given the right to edit by hand.
        active = self.sudo().reservation_ids.filtered(
            lambda r: r.state == 'reserved')
        if active:
            active._release(reason)
        return True

    def action_request_budget_exception(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Request Budget Exception'),
            'res_model': 'realestate.procurement.budget.exception',
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'new',
            'context': {'default_request_id': self.id},
        }

    def action_view_reservations(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Reservations'),
            'res_model': 'realestate.procurement.reservation',
            'view_mode': 'list,form',
            'views': [[False, 'list'], [False, 'form']],
            'domain': [('request_id', '=', self.id)],
        }

    @api.model
    def action_activate_procurement_reservations(self, project=None):
        """The rollout runbook — M3AA.

        Creates **draft** reservations for approved, unordered demand so that
        somebody can read the list, in the register, before a single unit of
        capacity moves. Activating them is a second, deliberate action.

        Historic confirmed purchase orders are excluded by construction: they
        are already Construction commitment, and a reservation on top of a
        commitment for the same money is exactly the 6,000,000-for-3,000,000
        error the whole milestone is built to prevent.
        """
        if not self.env.user.has_group(
                'real_estate_procurement.group_procurement_manager'):
            raise UserError(_(
                "Switching budget control on for existing demand is a "
                "procurement manager's decision, not a side effect of an "
                "upgrade."))
        Reservation = self.env['realestate.procurement.reservation']
        domain = [('state', 'in', ('approved', 'sourcing')),
                  ('project_id', '!=', False)]
        if project:
            domain.append(('project_id', '=', project.id))
        created = Reservation.browse()
        for request in self.search(domain):
            if request.reservation_ids:
                continue
            created |= Reservation.reserve_request(
                request, initial_state='draft')
        return created

    def action_approve(self):
        """Take every pending decision this user is entitled to take.

        The matrix is authoritative — this is the button on the requisition,
        and it resolves to the same step records a per-step approval writes.
        When no rule matches at all there are no steps, and a single
        approver-group decision stands in; that path carries exactly the same
        separation-of-duties and budget checks, because a company with no
        matrix configured is the one most likely to need them.
        """
        for rec in self:
            if rec.state != 'submitted':
                raise UserError(_("Only submitted requests can be approved."))
            steps = rec.approval_step_ids.filtered(
                lambda s: s.decision == 'pending')
            if steps:
                for step in steps.sorted('sequence'):
                    if step._blocking_predecessors():
                        break
                    step.action_approve()
                continue
            if not self.env.user.has_group(
                    'real_estate_procurement.group_procurement_approver'):
                raise UserError(_(
                    "You do not have permission to approve material requests."
                ))
            rec._check_not_self_approval()
            rec._approve_now()

    # ------------------------------------------------------------------
    # The seams Request leaves open
    # ------------------------------------------------------------------
    @api.depends('project_id', 'state', 'needed_by', 'line_ids.qty',
                 'line_ids.uom_id', 'line_ids.estimate_is_known',
                 'line_ids.required_on_site_date', 'lines_missing_cost_code',
                 'lines_missing_wbs', 'line_ids.po_line_ids',
                 'reservation_ids.state', 'reservation_ids.has_anomaly',
                 'reserved_amount', 'approved_by_id')
    def _compute_data_quality(self):
        """Same computation, widened triggers.

        Request recomputes on its own data. Once control records exist, a
        reservation changing state is also a reason for the warning list to
        change, and the dependency has to say so or the stored flag goes
        stale.
        """
        return super()._compute_data_quality()

    def _cancel_pending_approvals(self):
        for rec in self:
            rec.approval_step_ids.filtered(
                lambda s: s.decision == 'pending').write(
                    {'decision': 'cancelled'})
        return True

    def _on_submitted(self):
        for rec in self:
            positions = rec._control_positions(ignore_own_reservations=True)
            rec.approval_control_status = rec._demand_status(positions) \
                if positions else False
            rec._generate_approval_steps()
        return True

    def _has_approval_snapshot(self):
        self.ensure_one()
        return bool(self.approval_step_ids)


class MaterialRequestLineControl(models.Model):
    """Control-facing extension of the requisition line."""
    _inherit = 'realestate.material.request.line'


    # ------------------------------------------------------------------
    # M3 — the control scope of one line.
    #
    # Rule 2: reservation is per cost code, because that is the dimension
    # Construction controls money in. The cost code itself is added to this
    # model by `real_estate_construction`, so everything here asks whether the
    # field exists rather than assuming it — a Procurement-only install has no
    # cost codes and must still be able to run.
    # ------------------------------------------------------------------
    reservation_ids = fields.One2many(
        'realestate.procurement.reservation', 'request_line_id',
        string='Reservations', readonly=True)
    reserved_amount = fields.Monetary(
        compute='_compute_reserved_amount', store=True,
        help="Capacity this line is holding. Zero once it has been ordered, "
             "because the confirmed order is then the obligation.")

    @api.depends('reservation_ids.amount_active', 'reservation_ids.state')
    def _compute_reserved_amount(self):
        for ln in self:
            # Same narrow elevation as on the request: read the control
            # record, do not hand out the right to change it.
            ln.reserved_amount = sum(ln.sudo().reservation_ids.filtered(
                lambda r: r.state == 'reserved').mapped('amount_active'))

    def _control_scope_values(self):
        """Coding to copy onto a reservation, where coding exists at all."""
        self.ensure_one()
        Reservation = self.env['realestate.procurement.reservation']
        values = {}
        if 'cost_code_id' in Reservation._fields:
            values['cost_code_id'] = self._control_cost_code_id()
        if 'wbs_id' in Reservation._fields:
            values['wbs_id'] = self._control_wbs_id()
        return values

    def _check_unlink_allowed(self):
        """A line holding capacity cannot be deleted out from under it.

        Released and converted reservations do not block anything — they are
        history and they keep their own snapshot of what the line said. An
        *active* one is a live control position, and deleting its demand would
        leave the project's availability quietly wrong.
        """
        holding = self.filtered(lambda ln: ln.sudo().reservation_ids.filtered(
            lambda r: r.state == 'reserved'))
        if holding:
            raise UserError(_(
                "%s is reserving purchasing capacity. Revise the requisition "
                "— that releases the reservation and leaves a record of why.")
                % ', '.join(holding.mapped(
                    lambda ln: ln.description or ln.product_id.display_name)))
        return True
