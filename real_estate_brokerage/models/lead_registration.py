# -*- coding: utf-8 -*-
"""M7 — "this customer is mine", and the fight that follows.

Channel sales runs on one promise: a broker who introduces a buyer gets paid if
that buyer completes, and nobody else does. Every dispute in the business is
about who introduced whom first, so the register has to be able to answer it
months later, from a record made before anybody knew the deal was worth
arguing about.

```
   broker registers ─▶ collision check ─▶ approved ─▶ protected until <date>
   (name/phone/email)         │                            │
                              │                            └─▶ converts to
                              ├─▶ already registered            crm.lead
                              ├─▶ already a direct customer
                              └─▶ protection lapsed → free again
```

### Telling a broker "no" without telling them why

When broker B registers a customer broker A already holds, B must be refused —
and must **not** learn that A exists, who A is, or when A registered them. That
is a competitor's client list, and leaking it one field at a time is how a
channel programme loses its brokers.

So the collision check runs in `sudo()` and returns a deliberately thin answer:
*this lead is not available*. The identity of the holder is written only into
the internal audit fields, which brokers cannot read.

### Matching identity

Phone first, via Odoo's own `phone_sanitized`, because a phone number is the one
thing a buyer gives consistently and a broker cannot easily mistype into a false
negative. Then email, normalised. Names are deliberately **not** matched on:
"Mohamed Ali" collides with half a city, and a false positive here takes money
off a broker who did nothing wrong.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

REGISTRATION_STATE = [
    ('draft', 'Draft'),
    ('pending', 'Pending Review'),
    ('approved', 'Approved — Protected'),
    ('rejected', 'Rejected'),
    ('expired', 'Protection Lapsed'),
    ('converted', 'Converted'),
]

REJECTION_REASON = [
    ('duplicate', 'Already Registered'),
    ('existing_customer', 'Already a Direct Customer'),
    ('not_authorised', 'Project Not Authorised'),
    ('broker_ineligible', 'Broker Not Eligible'),
    ('insufficient_detail', 'Insufficient Contact Detail'),
    ('other', 'Other'),
]


class LeadRegistration(models.Model):
    _name = 'realestate.lead.registration'
    _description = 'Broker Lead Registration'
    _inherit = ['mail.thread']
    _order = 'registered_on desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string='Reference', required=True, copy=False, readonly=True,
        default=lambda self: _('New'))
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, index=True,
        default=lambda self: self.env.company)

    broker_partner_id = fields.Many2one(
        'res.partner', string='Broker', required=True, index=True,
        domain=[('is_realestate_broker', '=', True)], tracking=True)
    agreement_id = fields.Many2one(
        'realestate.broker.agreement', string='Agreement', index=True,
        tracking=True)

    # ------------------------------------------------------------------
    # The customer being claimed
    # ------------------------------------------------------------------
    customer_name = fields.Char(string='Customer Name', required=True)
    customer_phone = fields.Char(string='Phone')
    customer_phone_sanitized = fields.Char(
        compute='_compute_phone_sanitized', store=True, index=True,
        help="E164 where the number can be normalised — for dialling and "
             "display.")
    customer_phone_key = fields.Char(
        compute='_compute_phone_sanitized', store=True, index=True,
        string='Phone Match Key',
        help="The last nine digits. This, not the E164 form, is what "
             "collisions are decided on: E164 needs a country to normalise "
             "against, and a broker typing a local number into a company with "
             "no country set would silently defeat duplicate detection "
             "altogether. Nine digits is short enough to survive any national "
             "prefix and long enough that two real customers colliding is a "
             "manual-override case, not a daily event.")
    customer_email = fields.Char(string='Email')
    customer_email_normalized = fields.Char(
        compute='_compute_email_normalized', store=True, index=True)
    customer_partner_id = fields.Many2one(
        'res.partner', string='Customer Record', index=True,
        help="Filled once the customer exists as a contact.")

    project_id = fields.Many2one(
        'realestate.project', string='Project', index=True, tracking=True)
    notes = fields.Text()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    state = fields.Selection(
        REGISTRATION_STATE, default='draft', required=True, index=True,
        tracking=True, copy=False)
    registered_on = fields.Datetime(
        string='Registered', default=fields.Datetime.now, readonly=True,
        index=True, copy=False)
    approved_on = fields.Datetime(readonly=True, copy=False)
    protected_until = fields.Date(
        string='Protected Until', readonly=True, index=True, copy=False,
        tracking=True,
        help="After this date the customer is free for anybody to register. "
             "Set from the agreement's protection window at approval, and "
             "frozen — renegotiating the agreement later must not silently "
             "move a protection that has already been granted.")
    is_protected = fields.Boolean(
        compute='_compute_is_protected', search='_search_is_protected')

    rejection_reason = fields.Selection(
        REJECTION_REASON, string='Rejection Reason', tracking=True)
    rejection_note = fields.Char()

    #: The audit trail. Brokers cannot read these — that is the whole point.
    colliding_registration_id = fields.Many2one(
        'realestate.lead.registration', string='Collided With', readonly=True,
        copy=False, groups='real_estate_brokerage.group_realestate_sales_manager',
        help="Which registration blocked this one. Manager-only: telling a "
             "broker whose claim beat theirs hands them a competitor's client "
             "list one name at a time.")

    crm_lead_id = fields.Many2one(
        'crm.lead', string='Opportunity', readonly=True, copy=False,
        index=True, ondelete='set null')

    _sql_constraints = [
        ('registration_name_unique', 'unique(name, company_id)',
         'Registration references must be unique.'),
    ]

    # ------------------------------------------------------------------
    # Normalisation
    # ------------------------------------------------------------------
    @api.depends('customer_phone', 'company_id')
    def _compute_phone_sanitized(self):
        for rec in self:
            rec.customer_phone_sanitized = rec._re_sanitize_phone(
                rec.customer_phone)
            rec.customer_phone_key = rec._re_phone_key(rec.customer_phone)

    @api.model
    def _re_phone_key(self, number):
        """The comparable tail of a phone number.

        `+20 100 123 4567` and `0100 123 4567` are one person and must match
        without either side knowing the country.
        """
        if not number:
            return False
        digits = ''.join(ch for ch in number if ch.isdigit())
        if not digits:
            return False
        return digits[-9:]

    def _re_sanitize_phone(self, number):
        """Odoo's own normaliser, with a documented fallback.

        `phone_validation` is not guaranteed to be installed, and a registration
        that cannot be made because a helper module is missing is worse than one
        matched on digits. The fallback keeps only digits, which still makes
        `+20 100 123 4567` and `0100 123 4567` comparable at the tail.
        """
        if not number:
            return False
        try:
            from odoo.addons.phone_validation.tools import phone_validation
            country = self.company_id.country_id or self.env.company.country_id
            return phone_validation.phone_format(
                number, country.code if country else None,
                country.phone_code if country else None,
                force_format='E164', raise_exception=False) or None
        except ImportError:
            digits = ''.join(ch for ch in number if ch.isdigit())
            return digits[-9:] if len(digits) >= 9 else digits or False

    @api.depends('customer_email')
    def _compute_email_normalized(self):
        for rec in self:
            email = (rec.customer_email or '').strip().lower()
            rec.customer_email_normalized = email or False

    # ------------------------------------------------------------------
    # Protection window
    # ------------------------------------------------------------------
    #: Converting a registration into an opportunity is the broker doing
    #: exactly what the protection is for. It must not end the protection —
    #: the commission is claimed against a deal that has not happened yet.
    PROTECTED_STATES = ('approved', 'converted')

    @api.depends('state', 'protected_until')
    def _compute_is_protected(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.is_protected = bool(
                rec.state in rec.PROTECTED_STATES and rec.protected_until
                and rec.protected_until >= today)

    def _search_is_protected(self, operator, value):
        today = fields.Date.context_today(self)
        protected = [('state', 'in', list(self.PROTECTED_STATES)),
                     ('protected_until', '>=', today)]
        wants = (operator == '=') == bool(value)
        if wants:
            return protected
        return ['|', ('state', 'not in', list(self.PROTECTED_STATES)),
                ('protected_until', '<', today)]

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'realestate.lead.registration') or _('New')
        return super().create(vals_list)

    @api.constrains('customer_name', 'customer_phone', 'customer_email')
    def _check_contactable(self):
        # `customer_name` is in the trigger list only because it is always
        # present: a create that omits both contact fields would otherwise
        # never fire this, which is exactly the case worth catching.
        for rec in self:
            if not (rec.customer_phone or rec.customer_email):
                raise ValidationError(_(
                    "A registration needs a phone number or an email address. "
                    "A name on its own cannot be matched against anything, so "
                    "it protects nobody."))

    # ==================================================================
    # Submission — where the collision is decided
    # ==================================================================
    def action_submit(self):
        """Run the checks and either protect the lead or refuse it."""
        for rec in self:
            if rec.state not in ('draft', 'pending'):
                raise UserError(_(
                    "%s has already been decided.") % rec.display_name)
            rec._decide()
        return True

    def _decide(self):
        self.ensure_one()
        broker = self.broker_partner_id

        try:
            broker._check_may_transact()
        except UserError as exc:
            return self._reject('broker_ineligible', str(exc))

        agreement = self.agreement_id or broker.active_broker_agreement_id
        if not agreement:
            return self._reject(
                'broker_ineligible',
                _("No active agreement covers this registration."))
        self.agreement_id = agreement
        try:
            agreement._check_still_valid()
        except UserError as exc:
            return self._reject('broker_ineligible', str(exc))

        if self.project_id and not agreement._covers_project(self.project_id):
            return self._reject('not_authorised', _(
                "The agreement does not cover %s.")
                % self.project_id.display_name)

        holder = self._find_protecting_registration()
        if holder:
            return self._reject_duplicate(holder)

        existing = self._find_existing_direct_customer()
        if existing:
            return self._reject('existing_customer', _(
                "This customer is already known to the company directly."))

        return self._approve(agreement)

    # ------------------------------------------------------------------
    # Collision detection — thorough, and silent about who won
    # ------------------------------------------------------------------
    def _find_protecting_registration(self):
        """Any live protection over this customer, held by anybody.

        Runs `sudo()` deliberately: the *answer* must be complete even though
        the asking broker may not read the record that produced it.
        """
        self.ensure_one()
        domain = self._identity_domain()
        if not domain:
            return self.browse()
        today = fields.Date.context_today(self)
        return self.sudo().search(
            [('id', '!=', self.id),
             ('company_id', '=', self.company_id.id),
             ('state', 'in', list(self.PROTECTED_STATES)),
             ('protected_until', '>=', today)] + domain,
            order='approved_on asc', limit=1)

    def _identity_domain(self):
        """Phone or email. Never name — see the module docstring."""
        self.ensure_one()
        terms = []
        if self.customer_phone_key:
            terms.append(('customer_phone_key', '=', self.customer_phone_key))
        if self.customer_email_normalized:
            terms.append(('customer_email_normalized', '=',
                          self.customer_email_normalized))
        if not terms:
            return []
        return ['|'] * (len(terms) - 1) + terms

    def _find_existing_direct_customer(self):
        """A buyer the company already has an open opportunity with.

        A broker cannot register somebody who is already in the pipeline —
        that is not an introduction, and paying for it would mean paying for
        customers the company found itself.
        """
        self.ensure_one()
        Lead = self.env['crm.lead'].sudo()
        terms = []
        if self.customer_phone_key:
            # `like` against the tail: a CRM lead stores E164, which ends with
            # the same nine digits whatever prefix it carries.
            terms.append(('phone_sanitized', 'like', self.customer_phone_key))
        if self.customer_email_normalized:
            terms.append(('email_normalized', '=',
                          self.customer_email_normalized))
        if not terms:
            return Lead.browse()
        domain = ['|'] * (len(terms) - 1) + terms
        return Lead.search(
            [('type', '=', 'opportunity'),
             ('active', '=', True),
             ('company_id', 'in', (False, self.company_id.id))] + domain,
            limit=1)

    # ------------------------------------------------------------------
    # Outcomes
    # ------------------------------------------------------------------
    def _approve(self, agreement):
        self.ensure_one()
        now = fields.Datetime.now()
        days = agreement.protection_days or 0
        self.write({
            'state': 'approved',
            'approved_on': now,
            # Frozen at approval. Renegotiating the agreement later must not
            # quietly extend or shorten a protection already granted.
            'protected_until': fields.Date.add(
                fields.Date.context_today(self), days=days),
            'rejection_reason': False,
            'rejection_note': False,
        })
        self.message_post(body=_(
            "Registered to %(broker)s until %(until)s.",
            broker=self.broker_partner_id.display_name,
            until=self.protected_until))
        return True

    def _reject(self, reason, note=None):
        self.ensure_one()
        self.write({'state': 'rejected', 'rejection_reason': reason,
                    'rejection_note': note})
        return False

    def _reject_duplicate(self, holder):
        """Refuse without naming the holder.

        The link is written through `sudo()` into a manager-only field, so the
        company can settle a dispute later while the losing broker learns
        nothing about their competitor.
        """
        self.ensure_one()
        self.sudo().write({
            'state': 'rejected',
            'rejection_reason': 'duplicate',
            'rejection_note': _(
                "This customer is already registered and protected. No further "
                "detail is disclosed."),
            'colliding_registration_id': holder.id,
        })
        return False

    def action_override_duplicate(self, reason):
        """Approve a registration the collision check refused.

        Tail matching is deliberately slightly generous, and a broker who can
        show they introduced the customer first has to have somewhere to go.
        Manager-only, reason required, and the original collision link is left
        in place so the decision can be reviewed.
        """
        self.ensure_one()
        if not self.env.user.has_group(
                'real_estate_brokerage.group_realestate_sales_manager'):
            raise UserError(_(
                "Overriding a duplicate rejection is a manager's decision."))
        if not reason:
            raise UserError(_(
                "Overriding a duplicate needs a reason — it takes a protected "
                "lead off whoever currently holds it."))
        if self.state != 'rejected' or self.rejection_reason != 'duplicate':
            raise UserError(_(
                "%s was not rejected as a duplicate.") % self.display_name)
        agreement = self.agreement_id or \
            self.broker_partner_id.active_broker_agreement_id
        if not agreement:
            raise UserError(_(
                "No active agreement covers %s.") % self.display_name)
        self._approve(agreement)
        self.message_post(body=_(
            "Duplicate rejection overridden by %(user)s. Reason: %(reason)s",
            user=self.env.user.display_name, reason=reason))
        return True

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------
    def action_convert_to_opportunity(self):
        """Turn a protected registration into a real opportunity."""
        self.ensure_one()
        if self.crm_lead_id:
            # Asked twice. Answer with the opportunity it already made rather
            # than objecting that the registration is now `converted` — which
            # is a state this very method put it in.
            return self._open_lead()
        if self.state != 'approved':
            raise UserError(_(
                "Only an approved registration converts to an opportunity; "
                "%s is %s.") % (self.display_name, self.state))

        partner = self.customer_partner_id
        if not partner:
            partner = self.env['res.partner'].create({
                'name': self.customer_name,
                'phone': self.customer_phone,
                'email': self.customer_email,
                'company_id': False,
            })
            self.customer_partner_id = partner

        lead = self.env['crm.lead'].create({
            'name': _('%(customer)s — via %(broker)s',
                      customer=self.customer_name,
                      broker=self.broker_partner_id.display_name),
            'type': 'opportunity',
            'partner_id': partner.id,
            'contact_name': self.customer_name,
            'phone': self.customer_phone,
            'email_from': self.customer_email,
            'company_id': self.company_id.id,
            're_intent': 'buy',
            're_broker_partner_id': self.broker_partner_id.id,
            're_registration_id': self.id,
            're_project_ids': [(6, 0, self.project_id.ids)]
            if self.project_id else False,
        })
        self.write({'crm_lead_id': lead.id, 'state': 'converted'})
        return self._open_lead()

    def _open_lead(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'crm.lead',
            'res_id': self.crm_lead_id.id,
            'view_mode': 'form',
        }

    # ------------------------------------------------------------------
    # Cron
    # ------------------------------------------------------------------
    @api.model
    def _cron_expire_protection(self):
        """Let lapsed protections go, so the customer is free again."""
        today = fields.Date.context_today(self)
        lapsed = self.search([
            ('state', '=', 'approved'),
            ('protected_until', '!=', False),
            ('protected_until', '<', today),
        ])
        for rec in lapsed:
            rec.state = 'expired'
            rec.message_post(body=_(
                "Protection lapsed on %s. The customer is free to be "
                "registered again.") % rec.protected_until)
        return True


class CrmLeadBrokerOrigin(models.Model):
    """Where a broker-introduced opportunity came from."""
    _inherit = 'crm.lead'

    re_broker_partner_id = fields.Many2one(
        'res.partner', string='Introducing Broker', index=True,
        domain=[('is_realestate_broker', '=', True)], tracking=True,
        help="Set from the lead registration. This is the claim a commission "
             "is eventually paid against, so it is tracked.")
    re_registration_id = fields.Many2one(
        'realestate.lead.registration', string='Registration', readonly=True,
        index=True, ondelete='set null')
    re_broker_protected = fields.Boolean(
        related='re_registration_id.is_protected', readonly=True,
        string='Broker Protected')
