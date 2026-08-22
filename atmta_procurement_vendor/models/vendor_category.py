# -*- coding: utf-8 -*-
"""M4L — what a vendor is capable of, which is not what we bought from them.

Four classifications already existed in this codebase and none of them is the
one eligibility needs:

```
    product.category                  what the item is        (Concrete, MEP…)
    res.partner.category              what kind of party      (Contractor…)
    realestate.contractor.specialization  a Construction label on a
                                      Construction record, one value, no dates
    contract.package.package_type     what a subcontract covers
```

The first is about goods, not suppliers: a vendor qualified for *HVAC
installation* is neither narrower nor wider than the *MEP* product category —
it is a different axis, and keying approval on the product tree would mean a
vendor approved to supply ducting was thereby approved to install it. The
second is a seven-value global tag list with no company, no hierarchy and no
history. The third and fourth belong to Construction, which depends on this
module, so Procurement cannot reference them without inverting the dependency
that M2 and M3 were careful to keep pointing one way.

So: a trade tree of its own, deliberately small, with an explicit optional
mapping to product categories used **only** to suggest a trade on a
requisition line. The mapping suggests; it never decides.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class VendorCategory(models.Model):
    _name = 'realestate.procurement.vendor.category'
    _description = 'Procurement Vendor Trade'
    _parent_store = True
    _order = 'complete_name'

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, help='Short stable identifier.')
    complete_name = fields.Char(
        compute='_compute_complete_name', store=True, recursive=True,
        string='Trade')
    parent_id = fields.Many2one(
        'realestate.procurement.vendor.category', string='Parent Trade',
        index=True, ondelete='restrict')
    parent_path = fields.Char(index=True)
    child_ids = fields.One2many(
        'realestate.procurement.vendor.category', 'parent_id',
        string='Sub-trades')
    company_id = fields.Many2one(
        'res.company', string='Company',
        help="Leave empty for a trade every company shares. A trade is a "
             "vocabulary, not a decision — sharing one does not share any "
             "qualification made against it.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    note = fields.Text(string='Scope Notes')

    product_category_ids = fields.Many2many(
        'product.category',
        relation='procurement_vendor_trade_product_categ_rel',
        column1='trade_id', column2='categ_id',
        string='Suggests For Product Categories',
        help="Requisition lines for products in these categories will default "
             "to this trade. A suggestion only: the buyer may change it, and "
             "nothing about eligibility is derived from the product tree.")

    _sql_constraints = [
        ('code_company_uniq',
         'unique(code, company_id)',
         'A trade code must be unique within its company.'),
    ]

    @api.depends('name', 'parent_id.complete_name')
    def _compute_complete_name(self):
        for rec in self:
            rec.complete_name = (
                '%s / %s' % (rec.parent_id.complete_name, rec.name)
                if rec.parent_id else rec.name)

    @api.constrains('parent_id')
    def _check_recursion_loop(self):
        if not self._check_recursion():
            raise ValidationError(_("A trade cannot contain itself."))

    @api.model
    def suggest_for_product(self, product):
        """The trade a product's category points at, if exactly one does.

        Ambiguity is returned as nothing rather than as a guess: two trades
        claiming the same product category is a configuration question, and
        picking the lower id would answer it silently.

        Read elevated, and only here. Whoever confirms a purchase order may
        be a plain Purchase Manager with no procurement rights at all, and
        deciding *which control question to ask about them* must not depend
        on their being allowed to browse the trade list. A trade is a word,
        not a decision — nothing about any vendor is exposed by resolving
        one.
        """
        if not product or not product.categ_id:
            return self.browse()
        categories = product.categ_id
        parent = product.categ_id.parent_id
        while parent:
            categories |= parent
            parent = parent.parent_id
        for categ in categories:      # nearest category first
            matches = self.sudo().search([
                ('product_category_ids', 'in', categ.id),
                '|', ('company_id', '=', False),
                ('company_id', 'in', self.env.companies.ids),
            ])
            if len(matches) == 1:
                return matches
        return self.sudo().browse()
