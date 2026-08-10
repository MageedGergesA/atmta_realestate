# -*- coding: utf-8 -*-
"""M2 — the commitment engine.

A commitment is a commercial obligation that exists **before** any accounting
cost does. Knowing it is the difference between "we have spent 3M" and "we have
spent 3M and promised another 5M", which is the whole reason project controls
exist.

### When cost becomes committed

```
    RFQ / draft PO      →  potential.  Not committed.
    CONFIRMED PO        →  committed, on the day it is confirmed.
    CANCELLED PO        →  no longer committed; the order stays on file.
    RECEIPT / BILL / PAYMENT
                        →  change nothing about commitment. They are later
                           stages of the same money, not new money.
```

### Source precedence — the double-count rule

The failure the brief names is a package of 8M with a purchase order of 8M
reading as 16M. The rule that prevents it:

```
    package WITH purchase orders   →  its purchase orders are the commitment
                                      (the package itself contributes nothing)
    package WITHOUT purchase orders→  its own current contract value
    PO with no package             →  the PO
```

A package is a commercial agreement; a purchase order is how that agreement is
executed. When both exist, the order is the operative document, so it wins —
and the package's value becomes the *comparison*, not an addition.

### Basis

**Untaxed, always** — `amount_untaxed` and `price_subtotal`, never
`amount_total`. Phase 0 found commitment measured tax-inclusive against
tax-exclusive budgets, which made a fully committed 1,000,000 milestone appear
150,000 over budget the moment it was ordered. Tax is Accounting's business and
is not project cost.

### Granularity

Line level, because one purchase order routinely spans several cost codes:
concrete 2M, steel 3M, plant 1M. Allocating a whole order to one code would
make the cost report a work of fiction.
"""

from odoo import _, api, models

#: PO states that represent a live commercial obligation.
COMMITTED_PO_STATES = ('purchase', 'done')


class ConstructionCommitment(models.AbstractModel):
    """The one service that answers "what is committed"."""
    _name = 'realestate.construction.commitment'
    _description = 'Construction Commitment Engine'

    # ------------------------------------------------------------------
    # Purchase orders
    # ------------------------------------------------------------------
    @api.model
    def _po_line_domain(self, project, packages=None):
        """Confirmed, non-cancelled order lines belonging to this project.

        A line is this project's when its own coding says so, or — for the
        common case of a wholly-project order — when the order header says so.
        Line coding wins where the two disagree, because a line coded to a
        different project is the more specific statement.
        """
        domain = [
            ('order_id.state', 'in', COMMITTED_PO_STATES),
            ('order_id.re_project_id', '=', project.id),
        ]
        if packages is not None:
            domain += [('order_id.re_package_id', 'in', packages.ids)]
        return domain

    @api.model
    def po_commitment_by_cost_code(self, project):
        """`{cost_code_id or False: untaxed_amount}` from confirmed orders.

        `_read_group`, because M46 assumes thousands of orders and the
        alternative is reading them all into Python to add up three numbers.

        `False` collects lines that carry no cost code. They are reported under
        "unassigned" rather than dropped: money committed against a project
        with no code is a fact somebody needs to see and fix, and silently
        omitting it would make the cost report add up to less than the truth.
        """
        groups = self.env['purchase.order.line'].sudo()._read_group(
            self._po_line_domain(project),
            groupby=['re_cost_code_id'],
            aggregates=['price_subtotal:sum'],
        )
        return {
            (cost_code.id if cost_code else False): total
            for cost_code, total in groups
        }

    # ------------------------------------------------------------------
    # Packages
    # ------------------------------------------------------------------
    @api.model
    def _package_commitment(self, package):
        """What one package commits, and which source said so.

        Returns `(amount, source)` where source is `purchase_orders`,
        `package` or `none`. The caller needs the source as much as the
        number: a cost report that cannot say where a figure came from is a
        cost report nobody trusts twice.
        """
        live_orders = package.purchase_order_ids.filtered(
            lambda po: po.state in COMMITTED_PO_STATES)
        if live_orders:
            return sum(live_orders.mapped('amount_untaxed')), 'purchase_orders'
        if package.state in ('awarded', 'active', 'substantially_complete',
                             'complete'):
            # **Original**, not current. `current_contract_value` already
            # includes approved variations, and those variations are
            # counted again by `approved_change_by_cost_code()` whenever
            # no purchase order has absorbed them — which, for a package
            # with no orders, is always. Returning the current value here
            # reported a 13,000,000 obligation as 16,000,000. Found by M8,
            # whose whole subject is counting one commercial event once.
            return package.original_contract_value, 'package'
        return 0.0, 'none'

    @api.model
    def package_commitment_by_cost_code(self, project):
        """Commitment from packages that have **no** purchase orders.

        Packages whose orders exist are represented by those orders and are
        deliberately absent here — that absence is the whole double-count
        defence, and it is asserted by
        `test_a_package_and_its_po_are_one_commitment`.

        The value taken is the package's **original** contract value.
        Approved variations reach the total through
        `approved_change_by_cost_code()` instead, so that a package with
        no purchase orders does not report each variation twice.

        A package's value is spread across its declared cost codes evenly when
        it names several, because a package with no orders has told us nothing
        finer. An even split is a stated convention rather than a guess
        dressed up as precision, and it disappears the moment real orders
        arrive.
        """
        packages = self.env[
            'realestate.construction.contract.package'].sudo().search([
                ('project_id', '=', project.id),
                ('state', 'in', ('awarded', 'active',
                                 'substantially_complete', 'complete')),
            ])
        by_code = {}
        for package in packages:
            amount, source = self._package_commitment(package)
            if source != 'package' or not amount:
                continue
            codes = package.cost_code_ids
            if not codes:
                by_code[False] = by_code.get(False, 0.0) + amount
                continue
            share = amount / len(codes)
            for code in codes:
                by_code[code.id] = by_code.get(code.id, 0.0) + share
        return by_code

    # ------------------------------------------------------------------
    # The answer
    # ------------------------------------------------------------------
    @api.model
    def approved_change_by_cost_code(self, project):
        """M4's approved commitment changes, per cost code.

        Changes already folded into an amended purchase order are **excluded**:
        the order then carries the new value, and adding the change on top
        would count the same variation twice. This is the M2 source-precedence
        rule extended to variations, and `reflected_in_purchase_order` is the
        switch that says which side owns the number.
        """
        groups = self.env[
            'realestate.construction.commitment.change'].sudo()._read_group(
                [('project_id', '=', project.id),
                 ('reflected_in_purchase_order', '=', False),
                 ('change_order_id.state', 'in',
                  ('implemented', 'closed'))],
                groupby=['cost_code_id'],
                aggregates=['amount:sum'],
            )
        return {
            (cost_code.id if cost_code else False): total
            for cost_code, total in groups
        }

    @api.model
    def current_commitment_by_cost_code(self, project):
        """`{cost_code_id or False: amount}` — the project's live commitment.

        Three sources, combined once:

        ```
            confirmed purchase orders
          + awarded packages that have no orders
          + approved commitment changes not yet folded into an order
        ```

        The precedence rules keep every commercial obligation represented
        exactly once, whichever documents happen to exist.
        """
        totals = dict(self.po_commitment_by_cost_code(project))
        for source in (self.package_commitment_by_cost_code(project),
                       self.approved_change_by_cost_code(project)):
            for code_id, amount in source.items():
                totals[code_id] = totals.get(code_id, 0.0) + amount
        return totals

    @api.model
    def original_commitment_by_cost_code(self, project):
        """Commitment before any approved change — the "original" of M2."""
        totals = dict(self.po_commitment_by_cost_code(project))
        for code_id, amount in self.package_commitment_by_cost_code(
                project).items():
            totals[code_id] = totals.get(code_id, 0.0) + amount
        return totals

    @api.model
    def current_commitment(self, project):
        return sum(self.current_commitment_by_cost_code(project).values())

    @api.model
    def commitment_rows(self, project):
        """The commitment register: one row per underlying document.

        Rows, not a total — §33 requires a register that explains the number,
        and §29 requires every amount on the cost report to be explainable by
        clicking it.
        """
        rows = []
        lines = self.env['purchase.order.line'].sudo().search(
            self._po_line_domain(project))
        for line in lines:
            rows.append({
                'source': 'purchase_order',
                'document': line.order_id.name,
                'document_model': 'purchase.order',
                'document_id': line.order_id.id,
                'package_id': line.order_id.re_package_id.id or False,
                'partner': line.order_id.partner_id.display_name,
                'wbs_id': line.re_wbs_id.id or False,
                'cost_code_id': line.re_cost_code_id.id or False,
                'description': line.name,
                'currency_id': line.currency_id.id,
                'amount': line.price_subtotal,
                'state': line.order_id.state,
            })

        packages = self.env[
            'realestate.construction.contract.package'].sudo().search([
                ('project_id', '=', project.id)])
        for package in packages:
            amount, source = self._package_commitment(package)
            if source != 'package' or not amount:
                continue
            rows.append({
                'source': 'package',
                'document': package.name,
                'document_model': 'realestate.construction.contract.package',
                'document_id': package.id,
                'package_id': package.id,
                'partner': package.contractor_id.display_name or '',
                'wbs_id': False,
                'cost_code_id': (package.cost_code_ids[:1].id
                                 if package.cost_code_ids else False),
                'description': package.title,
                'currency_id': package.currency_id.id,
                'amount': amount,
                'state': package.state,
            })
        return rows
