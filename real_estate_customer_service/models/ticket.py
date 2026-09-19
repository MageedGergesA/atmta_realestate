from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


TICKET_STATES = [
    ('new', 'New'),
    ('assigned', 'Assigned'),
    ('in_progress', 'In Progress'),
    ('waiting', 'Waiting on Customer'),
    ('resolved', 'Resolved'),
    ('closed', 'Closed'),
    ('cancelled', 'Cancelled'),
]

PRIORITIES = [
    ('0', 'Low'),
    ('1', 'Normal'),
    ('2', 'High'),
    ('3', 'Urgent'),
]


class CustomerTicket(models.Model):
    """Post-handover ticket raised by a resident/owner."""
    _name = 'realestate.customer.ticket'
    _description = 'Real Estate Customer Ticket'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'priority desc, create_date desc'

    name = fields.Char(
        string='Reference', copy=False, required=True, readonly=True,
        default=lambda self: _('New'),
    )
    title = fields.Char(required=True, tracking=True)
    description = fields.Html()

    partner_id = fields.Many2one(
        'res.partner', string='Reporter', required=True, tracking=True,
    )
    property_id = fields.Many2one(
        'realestate.property', string='Property', tracking=True, index=True,
    )
    project_id = fields.Many2one(
        'realestate.project', string='Project', tracking=True,
        compute='_compute_project_from_property', store=True, readonly=False,
    )
    category_id = fields.Many2one(
        'realestate.customer.ticket.category', string='Category', tracking=True,
    )
    priority = fields.Selection(PRIORITIES, default='1', tracking=True, required=True)

    assignee_id = fields.Many2one(
        'res.users', string='Assignee', tracking=True,
    )
    state = fields.Selection(
        TICKET_STATES, default='new', tracking=True, required=True, copy=False,
    )

    sla_deadline = fields.Datetime(
        string='SLA Deadline', tracking=True,
        compute='_compute_sla_deadline', store=True, readonly=False,
    )
    sla_deadline_manual = fields.Boolean(
        string='Deadline Set Manually', copy=False, readonly=True,
        help='The deadline was written by hand rather than derived from the '
             'category, so a category change leaves it alone.',
    )
    sla_breached = fields.Boolean(
        compute='_compute_sla_breached', store=True,
        help='True when the ticket is unresolved past its SLA deadline.',
    )
    resolved_on = fields.Datetime(readonly=True, copy=False)
    closed_on = fields.Datetime(readonly=True, copy=False)

    material_request_id = fields.Many2one(
        'realestate.material.request', string='Material Request',
        readonly=True, copy=False,
        help='Spawned when the ticket needs materials/parts.',
    )
    material_request_available = fields.Boolean(
        compute='_compute_material_request_available',
        help="Material requests belong to Procurement. Without it the link has no "
             "model behind it, so the field and its button are hidden.")

    def _compute_material_request_available(self):
        # Procurement must be installed *and* the user must be allowed to
        # create a request: otherwise the button only leads to an AccessError.
        available = (
            'realestate.material.request' in self.env
            and self.env['realestate.material.request'].has_access('create')
        )
        for rec in self:
            rec.material_request_available = available

    @api.depends('property_id')
    def _compute_project_from_property(self):
        for rec in self:
            if rec.property_id and 'project_id' in rec.property_id._fields:
                rec.project_id = rec.property_id.project_id

    @api.depends('create_date', 'category_id')
    def _compute_sla_deadline(self):
        """Deadline = when the ticket was raised + the category's SLA.

        A form has no create_date yet, and the compute used to wait for one:
        the form then saved its (empty) value and the ticket never got a
        deadline. Until the record exists, "raised" is now.

        The deadline follows the category -- a ticket moved to a 4h category
        is due in 4h -- unless somebody set it by hand. Editing a category's
        default hours does not rewrite existing tickets.
        """
        now = fields.Datetime.now()
        for rec in self:
            if rec.sla_deadline_manual:
                continue
            if not rec.category_id:
                rec.sla_deadline = False
                continue
            rec.sla_deadline = (rec.create_date or now) + timedelta(
                hours=rec.category_id.default_sla_hours or 0,
            )

    #: States in which a ticket is still somebody's problem, so a deadline
    #: passing is a breach rather than history.
    OPEN_STATES = ('new', 'assigned', 'in_progress', 'waiting')

    @api.depends('sla_deadline', 'state')
    def _compute_sla_breached(self):
        now = fields.Datetime.now()
        for rec in self:
            rec.sla_breached = (
                rec.sla_deadline
                and rec.state in self.OPEN_STATES
                and rec.sla_deadline < now
            )

    @api.model
    def _cron_refresh_sla_breaches(self, limit=2000):
        """Mark tickets that have gone past their deadline unattended.

        The field is stored and depends on the deadline and the state, so it
        only ever recomputed when somebody touched the ticket. The one case
        that matters is the ticket nobody touched: it was created inside its
        SLA, went past it in the night, and stayed `sla_breached = False` for
        as long as it was ignored -- missing from the SLA Breached filter and
        undecorated in the list, which is precisely the ticket a supervisor
        needs to see.

        Nothing is invented here. The flag is recomputed from the same rule
        the compute uses; only the passage of time has changed.
        """
        now = fields.Datetime.now()
        stale = self.sudo().search([
            ('sla_breached', '=', False),
            ('state', 'in', list(self.OPEN_STATES)),
            ('sla_deadline', '!=', False),
            ('sla_deadline', '<', now),
        ], limit=limit)
        if stale:
            stale.modified(['sla_deadline'])
            stale.flush_recordset(['sla_breached'])
        # A ticket that was reopened, or whose deadline was pushed out, has to
        # be able to stop being breached as well.
        recovered = self.sudo().search([
            ('sla_breached', '=', True),
            '|', ('state', 'not in', list(self.OPEN_STATES)),
            ('sla_deadline', '>', now),
        ], limit=limit)
        if recovered:
            recovered.modified(['sla_deadline'])
            recovered.flush_recordset(['sla_breached'])
        return len(stale) + len(recovered)

    @staticmethod
    def _sla_deadline_written_by_hand(vals):
        """A deadline in `vals` without a category is somebody's own date.

        The form sends the deadline its onchange derived together with the
        category that produced it; that is not a manual choice."""
        return bool(vals.get('sla_deadline')) and 'category_id' not in vals

    def _sla_deadline_is_the_derived_one(self, value):
        """True when `value` is exactly the deadline the category already gives.

        Writing a deadline back unchanged -- an import, an RPC or an intake
        that returns the field it was handed -- decides nothing, so it must not
        count as a manual override. It used to: the ticket was frozen, and
        moving it to a 4h category left it running on the 72h deadline of the
        category it had left, which reports an SLA as met when it was missed.
        """
        value = fields.Datetime.to_datetime(value)
        for rec in self:
            if not rec.category_id or not rec.create_date:
                return False
            derived = rec.create_date + timedelta(
                hours=rec.category_id.default_sla_hours or 0)
            # Datetimes are stored to the second; the form sends what the
            # compute produced, so an exact comparison is enough.
            if derived != value:
                return False
        return True

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('realestate.customer.ticket') or 'TCK/NEW'
            if self._sla_deadline_written_by_hand(vals):
                vals['sla_deadline_manual'] = True
        records = super().create(vals_list)
        for rec in records:
            # An empty deadline sent with a category (a form saved before the
            # compute could run) is derived from the real creation time.
            if rec.category_id and not rec.sla_deadline and not rec.sla_deadline_manual:
                rec.sla_deadline = rec.create_date + timedelta(
                    hours=rec.category_id.default_sla_hours or 0)
        for rec in records:
            if rec.category_id.default_assignee_id and not rec.assignee_id:
                rec.assignee_id = rec.category_id.default_assignee_id
                rec.state = 'assigned'
        return records

    def write(self, vals):
        if (self._sla_deadline_written_by_hand(vals)
                and not self._sla_deadline_is_the_derived_one(vals['sla_deadline'])):
            vals = dict(vals, sla_deadline_manual=True)
        return super().write(vals)

    # ---------- Actions ----------
    def action_assign(self):
        for rec in self:
            if not rec.assignee_id:
                raise UserError(_("Pick an assignee before assigning."))
            rec.state = 'assigned'

    def action_start(self):
        for rec in self:
            if rec.state not in ('new', 'assigned', 'waiting'):
                raise UserError(_("Cannot start from state %s.", rec.state))
            rec.state = 'in_progress'

    def action_wait_customer(self):
        for rec in self:
            if rec.state != 'in_progress':
                raise UserError(_("Only a ticket in progress can wait on the customer."))
            rec.state = 'waiting'

    def action_resolve(self):
        for rec in self:
            if rec.state not in ('assigned', 'in_progress', 'waiting'):
                raise UserError(_("Cannot resolve a ticket from state %s.", rec.state))
            rec.state = 'resolved'
            rec.resolved_on = fields.Datetime.now()

    def action_close(self):
        for rec in self:
            if rec.state != 'resolved':
                raise UserError(_("Resolve the ticket before closing."))
            rec.state = 'closed'
            rec.closed_on = fields.Datetime.now()

    def action_reopen(self):
        for rec in self:
            if rec.state not in ('resolved', 'closed'):
                raise UserError(_("Only resolved/closed tickets can be reopened."))
            # Back to the assignee if there is one; otherwise it is a new,
            # unassigned ticket again, not "assigned" to nobody.
            rec.state = 'assigned' if rec.assignee_id else 'new'
            rec.resolved_on = False
            rec.closed_on = False

    def action_cancel(self):
        for rec in self:
            # A resolved or closed ticket is history; reopen it first.
            if rec.state not in self.OPEN_STATES:
                raise UserError(_("Only an open ticket can be cancelled."))
            rec.state = 'cancelled'
            # A cancelled ticket was not resolved: the stamp would count it in
            # "Resolved This Month".
            rec.resolved_on = False
            rec.closed_on = False

    def action_create_material_request(self):
        self.ensure_one()
        if 'realestate.material.request' not in self.env.registry:
            raise UserError(_("Install real_estate_procurement to use this."))
        if self.material_request_id:
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'realestate.material.request',
                'res_id': self.material_request_id.id,
                'view_mode': 'form',
            }
        if not self.env['realestate.material.request'].has_access('create'):
            raise UserError(_(
                "You are not allowed to raise material requests. Ask a Procurement "
                "user to raise one for ticket %s.", self.name))
        request = self.env['realestate.material.request'].create({
            'project_id': self.project_id.id if self.project_id else False,
            'property_id': self.property_id.id if self.property_id else False,
            'source_ref': f'realestate.customer.ticket,{self.id}',
            'source_model': 'realestate.customer.ticket',
            'notes': f'<p>Materials for ticket {self.name}: {self.title}</p>',
        })
        self.material_request_id = request
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'realestate.material.request',
            'res_id': request.id,
            'view_mode': 'form',
        }
