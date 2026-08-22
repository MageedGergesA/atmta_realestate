# -*- coding: utf-8 -*-
"""M4P — suspension as a dated record, not as a flag on the vendor.

A boolean called `blocked` on the partner cannot answer any of the questions
that get asked about a suspension: who imposed it, when, on whose authority,
covering which trades, until when, and whether the vendor was suspended on the
day that tender closed. So it is a record, with all of those, and it can be
lifted without erasing that it happened.

### What a restriction does and does not do

It changes the answer to "may we source from them **now**". It does not:

```
    cancel a purchase order          those are commitments, and cancelling
                                     one is a management decision with money
                                     attached
    reverse a vendor bill            likewise, and with an accounting period
    rewrite a qualification          the assessor concluded what they
                                     concluded; a later suspension is a
                                     different fact, not a correction
    change history                   a tender issued in March is still a
                                     tender issued in March
```

The open orders a suspension touches are surfaced as a worklist so somebody
can decide about them. Deciding is not this module's job.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

RESTRICTION_TYPE = [
    ('sourcing_suspension', 'Sourcing Suspension'),
    ('award_suspension', 'Award Suspension'),
    ('category_restriction', 'Trade Restriction'),
    ('project_restriction', 'Project Restriction'),
    ('temporary_hold', 'Temporary Hold'),
    ('probation', 'Probation'),
    ('debarment', 'Debarment'),
]

RESTRICTION_STATE = [
    ('draft', 'Draft'),
    ('active', 'Active'),
    ('lifted', 'Lifted'),
    ('expired', 'Expired'),
    ('cancelled', 'Cancelled'),
]

#: Which purposes each type blocks. Probation blocks nothing — it is a
#: recorded warning that travels with the vendor, and a restriction that
#: silently stopped sourcing while being called "probation" would be worse
#: than not having the type at all.
BLOCKS = {
    'sourcing_suspension': ('sourcing', 'award'),
    'award_suspension': ('award',),
    'category_restriction': ('sourcing', 'award'),
    'project_restriction': ('sourcing', 'award'),
    'temporary_hold': ('sourcing', 'award'),
    'debarment': ('sourcing', 'award'),
    'probation': (),
}


class VendorRestriction(models.Model):
    _name = 'realestate.procurement.vendor.restriction'
    _description = 'Vendor Restriction'
    _inherit = ['mail.thread']
    _order = 'effective_from desc, id desc'

    name = fields.Char(
        required=True, copy=False, readonly=True, default=lambda self: _('New'))
    partner_id = fields.Many2one(
        'res.partner', string='Vendor', required=True, index=True,
        ondelete='restrict', tracking=True)
    profile_id = fields.Many2one(
        'realestate.procurement.vendor.profile', string='Governance Profile',
        readonly=True, index=True, ondelete='cascade')
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    project_id = fields.Many2one(
        'realestate.project', string='Project', index=True,
        ondelete='restrict',
        help="Empty means every project. Set it to restrict the vendor on "
             "one development while they carry on elsewhere.")
    category_id = fields.Many2one(
        'realestate.procurement.vendor.category', string='Trade',
        index=True, ondelete='restrict',
        help="Empty means every trade.")

    restriction_type = fields.Selection(
        RESTRICTION_TYPE, required=True, default='sourcing_suspension',
        tracking=True)
    reason = fields.Text(required=True, tracking=True)
    source = fields.Char(
        string='Raised From',
        help="Where this came from — a quality report, a site incident, a "
             "legal notice, a client instruction.")
    effective_from = fields.Date(
        required=True, tracking=True,
        default=lambda self: fields.Date.context_today(self))
    effective_to = fields.Date(
        tracking=True,
        help="Empty means until it is lifted. A temporary hold that nobody "
             "dated becomes permanent by accident, so the type suggests one.")
    imposed_by_id = fields.Many2one(
        'res.users', string='Imposed By', readonly=True, copy=False,
        default=lambda self: self.env.user)
    approved_by_id = fields.Many2one(
        'res.users', string='Approved By', readonly=True, copy=False,
        tracking=True)
    lifted_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    lifted_on = fields.Date(readonly=True, copy=False)
    lift_reason = fields.Text(readonly=True, copy=False)
    state = fields.Selection(
        RESTRICTION_STATE, default='draft', required=True, readonly=True,
        copy=False, index=True, tracking=True)
    notes = fields.Html()

    is_in_force = fields.Boolean(
        compute='_compute_is_in_force', string='In Force',
        help="Active and inside its dates, as of today.")
    open_order_count = fields.Integer(compute='_compute_open_orders')

    @api.constrains('effective_from', 'effective_to')
    def _check_dates(self):
        for rec in self:
            if rec.effective_to and rec.effective_to < rec.effective_from:
                raise ValidationError(_(
                    "%s would end before it started.") % rec.display_name)

    @api.constrains('restriction_type', 'category_id', 'project_id')
    def _check_scope_matches_type(self):
        for rec in self:
            if rec.restriction_type == 'category_restriction' and \
                    not rec.category_id:
                raise ValidationError(_(
                    "A trade restriction has to name the trade. Leave it "
                    "empty and it silently restricts everything."))
            if rec.restriction_type == 'project_restriction' and \
                    not rec.project_id:
                raise ValidationError(_(
                    "A project restriction has to name the project."))

    @api.depends('state', 'effective_from', 'effective_to')
    def _compute_is_in_force(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.is_in_force = rec._applies_on(today)

    def _compute_open_orders(self):
        """M4P — what a suspension touches, so somebody can decide about it."""
        PO = self.env['purchase.order']
        for rec in self:
            rec.open_order_count = PO.search_count([
                ('partner_id', '=', rec.partner_id.id),
                ('company_id', '=', rec.company_id.id),
                ('state', 'in', ('draft', 'sent', 'purchase')),
            ]) if rec.partner_id else 0

    # ------------------------------------------------------------------
    def _applies_on(self, date):
        """Is this restriction in force on `date`?

        Date-driven rather than flag-driven so a question about March gets
        March's answer, whether or not the nightly expiry job has run.
        """
        self.ensure_one()
        # `lifted` is included deliberately: a restriction lifted in June was
        # still in force in April, and a question about April must get April's
        # answer. `draft` and `cancelled` never applied to anything.
        if self.state not in ('active', 'expired', 'lifted'):
            return False
        if self.effective_from and self.effective_from > date:
            return False
        if self.effective_to and self.effective_to < date:
            return False
        if self.state == 'lifted' and self.lifted_on and self.lifted_on <= date:
            return False
        return True

    def _blocks(self, purpose):
        self.ensure_one()
        return purpose in BLOCKS.get(self.restriction_type, ())

    def _covers(self, category=None, project=None):
        """Does this restriction reach the trade and project being asked about?

        The two unnamed cases resolve in opposite directions, and that is
        deliberate. A **project**-scoped restriction is about one development;
        a purchase that names no project is not that development, so it is
        genuinely out of scope. A **trade**-scoped restriction asked a
        trade-less question is the other way round: the unclassified scope
        might well be the restricted trade, nobody has said otherwise, and
        unknown is not eligible.
        """
        self.ensure_one()
        if self.category_id and category and self.category_id != category:
            return False
        if self.project_id and project and self.project_id != project:
            return False
        if self.project_id and not project:
            # A project-scoped restriction says nothing about sourcing that
            # names no project. Reporting it as blocking would stop purchases
            # it was never about.
            return False
        return True

    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        Profile = self.env['realestate.procurement.vendor.profile']
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.procurement.vendor.restriction') or _('New')
        records = super().create(vals_list)
        for rec in records:
            rec.profile_id = Profile._get_or_create(
                rec.partner_id, rec.company_id)
        return records

    def action_activate(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_(
                    "%s is already %s.", rec.display_name,
                    dict(RESTRICTION_STATE)[rec.state]))
            rec.write({'state': 'active',
                       'approved_by_id': self.env.user.id})
            rec.profile_id._sync_status_from_restrictions()
            rec.message_post(body=_(
                "%(type)s in force from %(date)s. Reason: %(reason)s",
                type=dict(RESTRICTION_TYPE)[rec.restriction_type],
                date=rec.effective_from, reason=rec.reason))

    def action_lift(self, reason=None):
        reason = reason or self.env.context.get('lift_reason')
        if not reason:
            raise UserError(_(
                "Say why the restriction is being lifted. It was imposed with "
                "a reason and it comes off with one."))
        for rec in self:
            if rec.state != 'active':
                raise UserError(_("%s is not in force.") % rec.display_name)
            rec.write({'state': 'lifted',
                       'lifted_by_id': self.env.user.id,
                       'lifted_on': fields.Date.context_today(rec),
                       'lift_reason': reason})
            rec.profile_id._sync_status_from_restrictions()
            rec.message_post(body=_("Lifted: %s") % reason)

    def action_cancel(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_(
                    "Only a draft restriction can be cancelled. Lift %s "
                    "instead, so the record says what happened.")
                    % rec.display_name)
            rec.state = 'cancelled'

    def action_view_open_orders(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Open Orders — %s') % self.partner_id.display_name,
            'res_model': 'purchase.order',
            'view_mode': 'list,form',
            'domain': [('partner_id', '=', self.partner_id.id),
                       ('company_id', '=', self.company_id.id),
                       ('state', 'in', ('draft', 'sent', 'purchase'))],
        }

    @api.model
    def expire_due_restrictions(self):
        """Nightly. A dated restriction stops applying on its own date.

        The state follows for the sake of the screens; `_applies_on()` never
        needed it, which is why a missed cron run cannot make a lapsed
        suspension keep biting.
        """
        today = fields.Date.context_today(self)
        due = self.search([('state', '=', 'active'),
                           ('effective_to', '!=', False),
                           ('effective_to', '<', today)])
        due.write({'state': 'expired'})
        due.mapped('profile_id')._sync_status_from_restrictions()
        return len(due)

    def unlink(self):
        for rec in self:
            if rec.state in ('active', 'lifted', 'expired'):
                raise UserError(_(
                    "%s has been in force. Lift it rather than deleting it — "
                    "an audit that cannot see a past suspension cannot "
                    "explain the sourcing that happened around it.")
                    % rec.display_name)
        return super().unlink()
