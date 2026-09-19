# -*- coding: utf-8 -*-
"""Phases 5, 6 & 13 — the developer price book.

A developer price is not one number. It is a base, plus a stack of premiums
that are themselves policy (floor, view, corner, garden), and only then a list
price. Today the module has a single ``property.base_price`` field with no
history, no approval and no versioning: changing it changes the price of every
unsigned deal that reads it, silently.

The price book is therefore a *document*: it is approved, activated, and then
frozen. Repricing means issuing a new version, never editing an approved book.
That is Rule 5 applied at the pricing layer, and it is what makes a signed deal
defensible six months later.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

PRICE_BOOK_STATE = [
    ('draft', 'Draft'),
    ('pending_approval', 'Pending Approval'),
    ('approved', 'Approved'),
    ('active', 'Active'),
    ('expired', 'Expired'),
    ('archived', 'Archived'),
]

#: States in which the book's commercial content may no longer be edited.
PRICE_BOOK_FROZEN = ('approved', 'active', 'expired', 'archived')

#: The premium categories a real-estate price is actually built from. A
#: deliberately concrete, real-estate-specific list rather than a generic
#: rules engine nobody can administer (Phase 6).
PREMIUM_TYPE = [
    ('floor', 'Floor Premium'),
    ('view', 'View Premium'),
    ('orientation', 'Orientation Premium'),
    ('corner', 'Corner Premium'),
    ('garden', 'Garden Premium'),
    ('terrace', 'Terrace / Roof Premium'),
    ('parking', 'Parking'),
    ('storage', 'Storage'),
    ('finishing', 'Finishing Premium'),
    ('location', 'Location Premium'),
    ('custom', 'Custom Adjustment'),
]

CALCULATION_TYPE = [
    ('fixed', 'Fixed Amount'),
    ('percent', '% of Base Price'),
    ('per_sqm', 'Amount per m²'),
]


class PriceBook(models.Model):
    _name = 'realestate.price.book'
    _description = 'Real Estate Price Book'
    _inherit = ['mail.thread', 'mail.activity.mixin',
                'realestate.commercial.approval.mixin']
    _order = 'project_id, version desc, id desc'
    _check_company_auto = True

    name = fields.Char(required=True, tracking=True, index='trigram')
    code = fields.Char(copy=False, tracking=True)
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, index=True,
        default=lambda self: self.env.company,
    )
    project_id = fields.Many2one(
        'realestate.project', string='Project', required=True,
        ondelete='cascade', index=True, tracking=True, check_company=True)
    phase_id = fields.Many2one(
        'realestate.phase', string='Phase', ondelete='cascade', tracking=True,
        domain="[('project_id', '=', project_id)]", check_company=True,
        help="Leave empty for a project-wide book.")
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id)

    state = fields.Selection(
        PRICE_BOOK_STATE, default='draft', required=True, tracking=True,
        index=True, copy=False)

    # ---- Versioning (Phase 13) ----
    version = fields.Integer(default=1, required=True, readonly=True, copy=False)
    previous_version_id = fields.Many2one(
        'realestate.price.book', string='Supersedes', readonly=True, copy=False,
        ondelete='restrict')
    next_version_ids = fields.One2many(
        'realestate.price.book', 'previous_version_id', string='Superseded By')

    valid_from = fields.Date(string='Valid From', tracking=True)
    valid_until = fields.Date(string='Valid Until', tracking=True)

    line_ids = fields.One2many(
        'realestate.price.book.line', 'price_book_id', string='Unit Prices',
        copy=True)
    rule_ids = fields.One2many(
        'realestate.price.rule', 'price_book_id', string='Premium Rules',
        copy=True)
    line_count = fields.Integer(compute='_compute_counts', store=True)
    rule_count = fields.Integer(compute='_compute_counts', store=True)

    total_list_value = fields.Monetary(
        compute='_compute_totals', string='Total List Value', store=True,
        help="Sum of the list price of every unit in this book.")
    average_price_per_sqm = fields.Monetary(
        compute='_compute_totals', string='Avg Price / m²', store=True)

    notes = fields.Html()

    approved_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    approved_on = fields.Datetime(readonly=True, copy=False)
    activated_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    activated_on = fields.Datetime(readonly=True, copy=False)

    _sql_constraints = [
        ('price_book_version_uniq',
         'UNIQUE(project_id, phase_id, version, company_id)',
         'A price book with this version already exists for that project/phase.'),
    ]

    @api.depends('line_ids', 'rule_ids')
    def _compute_counts(self):
        for book in self:
            book.line_count = len(book.line_ids)
            book.rule_count = len(book.rule_ids)

    @api.depends('line_ids.list_price', 'line_ids.area_sqm')
    def _compute_totals(self):
        for book in self:
            lines = book.line_ids
            book.total_list_value = sum(lines.mapped('list_price'))
            area = sum(lines.mapped('area_sqm'))
            book.average_price_per_sqm = (
                book.total_list_value / area if area else 0.0)

    @api.constrains('valid_from', 'valid_until')
    def _check_validity(self):
        for book in self:
            if (book.valid_from and book.valid_until
                    and book.valid_until < book.valid_from):
                raise ValidationError(_(
                    "Price book '%s': validity ends before it starts.") % book.name)

    @api.constrains('phase_id', 'project_id')
    def _check_phase(self):
        for book in self:
            if book.phase_id and book.phase_id.project_id != book.project_id:
                raise ValidationError(_(
                    "Price book '%s': the phase belongs to another project."
                ) % book.name)

    # ------------------------------------------------------------------
    # Immutability
    # ------------------------------------------------------------------
    #: Fields that define what the book actually charges. Editing any of them
    #: after approval would change historical pricing.
    COMMERCIAL_FIELDS = {
        'line_ids', 'rule_ids', 'project_id', 'phase_id', 'currency_id',
        'valid_from', 'version',
    }

    def write(self, vals):
        touching = self.COMMERCIAL_FIELDS & set(vals)
        if touching:
            frozen = self.filtered(lambda b: b.state in PRICE_BOOK_FROZEN)
            if frozen:
                raise UserError(_(
                    "Price book '%s' is %s and its pricing can no longer be "
                    "edited.\n\nUse 'New Version' to reprice. Editing an "
                    "approved book would silently restate deals that were "
                    "signed against it."
                ) % (frozen[0].display_name,
                     dict(PRICE_BOOK_STATE)[frozen[0].state]))
        return super().write(vals)

    def unlink(self):
        posted = self.filtered(lambda b: b.state != 'draft')
        if posted:
            raise UserError(_(
                "Only draft price books can be deleted. '%s' is %s — archive "
                "it instead, so the pricing history survives."
            ) % (posted[0].display_name, dict(PRICE_BOOK_STATE)[posted[0].state]))
        return super().unlink()

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_submit(self):
        for book in self:
            if book.state != 'draft':
                raise UserError(_("Only draft price books can be submitted."))
            if not book.line_ids:
                raise UserError(_(
                    "Price book '%s' prices no units.") % book.name)
            book.state = 'pending_approval'

    def action_approve(self):
        for book in self:
            if book.state != 'pending_approval':
                raise UserError(_(
                    "Price book '%s' is not awaiting approval.") % book.name)
            book._require_approval(
                'price_book_activation', book.total_list_value,
                amount=book.total_list_value)
            book.write({
                'state': 'approved',
                'approved_by_id': self.env.user.id,
                'approved_on': fields.Datetime.now(),
            })

    def action_activate(self):
        """Make this the live book, superseding any other active one.

        Activating is what actually moves unit prices, so it is also what
        writes price history.
        """
        for book in self:
            if book.state != 'approved':
                raise UserError(_(
                    "Price book '%s' must be approved before activation."
                ) % book.name)
            superseded = self.search([
                ('id', '!=', book.id),
                ('project_id', '=', book.project_id.id),
                ('phase_id', '=', book.phase_id.id),
                ('state', '=', 'active'),
            ])
            superseded.write({'state': 'expired'})
            book.write({
                'state': 'active',
                'activated_by_id': self.env.user.id,
                'activated_on': fields.Datetime.now(),
            })
            book._apply_to_properties()
            book.message_post(body=_(
                "Activated. %s unit price(s) applied; %s book(s) expired."
            ) % (len(book.line_ids), len(superseded)))

    def action_archive_book(self):
        self.write({'state': 'archived'})

    def action_new_version(self):
        """Copy this book as the next version, in draft.

        The correct way to reprice. The old book stays exactly as it was, so
        every deal signed under it remains explicable.
        """
        self.ensure_one()
        latest = self.search([
            ('project_id', '=', self.project_id.id),
            ('phase_id', '=', self.phase_id.id),
            ('company_id', '=', self.company_id.id),
        ], order='version desc', limit=1)
        new_book = self.copy({
            'name': _('%s (v%s)') % (self.name.split(' (v')[0],
                                     (latest.version or self.version) + 1),
            'version': (latest.version or self.version) + 1,
            'previous_version_id': self.id,
            'state': 'draft',
            'approved_by_id': False, 'approved_on': False,
            'activated_by_id': False, 'activated_on': False,
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('New Price Book Version'),
            'res_model': 'realestate.price.book',
            'views': [(False, 'form')],
            'view_mode': 'form',
            'res_id': new_book.id,
            'target': 'current',
        }

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    def _apply_to_properties(self):
        """Push this book's list prices onto the units, recording history.

        Batched deliberately: at 20,000 units a per-record write with a
        per-record history create is the difference between a slow action and
        an unusable one.
        """
        self.ensure_one()
        History = self.env['realestate.price.history']
        Component = self.env['realestate.property.price.component']
        today = fields.Date.context_today(self)

        history_vals = []
        component_vals = []
        properties = self.line_ids.mapped('property_id')
        # Old components for these units under any book are replaced; the
        # history rows are what preserve the past, not the components.
        Component.search([('property_id', 'in', properties.ids)]).unlink()

        for line in self.line_ids:
            prop = line.property_id
            old = prop.base_price
            new = line.list_price
            if old != new:
                history_vals.append({
                    'property_id': prop.id,
                    'company_id': self.company_id.id,
                    'price_book_id': self.id,
                    'old_price': old,
                    'new_price': new,
                    'effective_date': today,
                    'currency_id': self.currency_id.id,
                    'reason': _('Price book %s activated') % self.display_name,
                })
            component_vals.extend(line._component_values())
            line.property_id.base_price = new

        if history_vals:
            History.create(history_vals)
        if component_vals:
            Component.create(component_vals)
        return True

    def action_view_lines(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Unit Prices'),
            'res_model': 'realestate.price.book.line',
            'views': [(False, 'list'), (False, 'form')],
            'view_mode': 'list,form',
            'domain': [('price_book_id', '=', self.id)],
            'context': {'default_price_book_id': self.id},
            'target': 'current',
        }

    @api.model
    def _find_active(self, property_record):
        """The book that governs a unit today, phase first then project."""
        today = fields.Date.context_today(self)
        domain = [
            ('state', '=', 'active'),
            ('company_id', '=', property_record.company_id.id),
            ('project_id', '=', property_record.project_id.id),
            '|', ('valid_from', '=', False), ('valid_from', '<=', today),
            '|', ('valid_until', '=', False), ('valid_until', '>=', today),
        ]
        if property_record.phase_id:
            phase_book = self.search(
                domain + [('phase_id', '=', property_record.phase_id.id)], limit=1)
            if phase_book:
                return phase_book
        return self.search(domain + [('phase_id', '=', False)], limit=1)


class PriceBookLine(models.Model):
    """One unit's price build-up inside a book.

    The base is entered either as a lump sum or as a rate per m²; premiums come
    from the book's rules. ``list_price`` is base + premiums, and it is what a
    reservation snapshots.
    """
    _name = 'realestate.price.book.line'
    _description = 'Price Book Line'
    _order = 'price_book_id, property_id'
    _check_company_auto = True

    price_book_id = fields.Many2one(
        'realestate.price.book', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='price_book_id.company_id', store=True, index=True, readonly=True)
    currency_id = fields.Many2one(
        related='price_book_id.currency_id', store=True, readonly=True)
    property_id = fields.Many2one(
        'realestate.property', string='Unit', required=True,
        ondelete='cascade', index=True, check_company=True)
    area_sqm = fields.Float(
        related='property_id.area_sqm', store=True, readonly=True, string='Area (m²)')

    base_mode = fields.Selection([
        ('amount', 'Lump Sum'),
        ('per_sqm', 'Rate per m²'),
    ], default='amount', required=True)
    base_amount = fields.Monetary(string='Base Amount')
    base_rate_sqm = fields.Monetary(string='Base Rate / m²')
    base_price = fields.Monetary(
        compute='_compute_prices', store=True, string='Base Price')

    premium_total = fields.Monetary(compute='_compute_prices', store=True)
    list_price = fields.Monetary(
        compute='_compute_prices', store=True, string='List Price',
        help="Base plus every premium the book's rules match. This is the "
             "price a buyer is quoted before promotions and discounts.")
    price_per_sqm = fields.Monetary(compute='_compute_prices', store=True)

    manual_adjustment = fields.Monetary(
        string='Manual Adjustment',
        help="A per-unit correction the rules cannot express. Recorded as its "
             "own component so it is never mistaken for a rule outcome.")
    adjustment_reason = fields.Char()

    _sql_constraints = [
        ('price_book_line_uniq', 'UNIQUE(price_book_id, property_id)',
         'This unit is already priced in this book.'),
    ]

    @api.constrains('property_id', 'price_book_id')
    def _check_property_in_scope(self):
        """A book may only price units of its own project (and phase).

        Enforced as a constraint, like a release batch's scope: activating a
        book writes the price of every unit it lists, so a line for another
        project's unit repriced inventory that book does not govern.
        """
        for line in self:
            book = line.price_book_id
            prop = line.property_id
            if prop.project_id != book.project_id:
                raise ValidationError(_(
                    "Price book '%(book)s' is for project %(project)s; unit "
                    "%(unit)s is not in it."
                ) % {'book': book.display_name,
                     'project': book.project_id.display_name,
                     'unit': prop.display_name})
            if book.phase_id and prop.phase_id and prop.phase_id != book.phase_id:
                raise ValidationError(_(
                    "Price book '%(book)s' is for phase %(phase)s; unit "
                    "%(unit)s is in phase %(other)s."
                ) % {'book': book.display_name,
                     'phase': book.phase_id.display_name,
                     'unit': prop.display_name,
                     'other': prop.phase_id.display_name})

    @api.depends('base_mode', 'base_amount', 'base_rate_sqm', 'area_sqm',
                 'manual_adjustment',
                 'price_book_id.rule_ids.value',
                 'price_book_id.rule_ids.calculation_type',
                 'price_book_id.rule_ids.active')
    def _compute_prices(self):
        for line in self:
            base = (line.base_rate_sqm * line.area_sqm
                    if line.base_mode == 'per_sqm' else line.base_amount)
            line.base_price = base
            premiums = sum(
                amount for _rule, amount in line._matching_premiums(base))
            line.premium_total = premiums + line.manual_adjustment
            line.list_price = base + line.premium_total
            line.price_per_sqm = (
                line.list_price / line.area_sqm if line.area_sqm else 0.0)

    def _matching_premiums(self, base=None):
        """(rule, amount) for every rule in the book that applies to this unit."""
        self.ensure_one()
        base = line_base = base if base is not None else self.base_price
        out = []
        # `_origin.id`: rules being added on the price book form are unsaved.
        for rule in self.price_book_id.rule_ids.sorted(lambda r: (r.sequence, r._origin.id or 0)):
            if not rule.active or not rule._applies_to(self.property_id):
                continue
            out.append((rule, rule._amount_for(self.property_id, line_base)))
        return out

    def _component_values(self):
        """Rows describing how this price was built, for the unit's record."""
        self.ensure_one()
        vals = [{
            'property_id': self.property_id.id,
            'company_id': self.company_id.id,
            'price_book_id': self.price_book_id.id,
            'sequence': 0,
            'name': _('Base Price'),
            'component_type': 'base',
            'amount': self.base_price,
            'currency_id': self.currency_id.id,
        }]
        for index, (rule, amount) in enumerate(self._matching_premiums(), start=1):
            if not amount:
                continue
            vals.append({
                'property_id': self.property_id.id,
                'company_id': self.company_id.id,
                'price_book_id': self.price_book_id.id,
                'rule_id': rule.id,
                'sequence': index * 10,
                'name': rule.name,
                'component_type': rule.premium_type,
                'amount': amount,
                'currency_id': self.currency_id.id,
            })
        if self.manual_adjustment:
            vals.append({
                'property_id': self.property_id.id,
                'company_id': self.company_id.id,
                'price_book_id': self.price_book_id.id,
                'sequence': 9999,
                'name': self.adjustment_reason or _('Manual Adjustment'),
                'component_type': 'custom',
                'amount': self.manual_adjustment,
                'currency_id': self.currency_id.id,
            })
        return vals
