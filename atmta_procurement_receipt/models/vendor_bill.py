# -*- coding: utf-8 -*-
"""M8 — the three-way match, where the money finally has to agree.

```
    ORDERED  — what was authorised
    RECEIVED — what arrived and was accepted
    BILLED   — what the vendor says is owed
```

Three numbers about one purchase, and until now nothing in this suite made
them agree. M3 controls what may be ordered, M7 controls what may be awarded,
M8's inspection controls what counts as received — and a vendor could still
bill for more than any of it, because posting a bill went through native
Accounting where nobody had told it about the project.

**What this refuses is over-billing, not disagreement.** A bill that is short,
early, or in a different period is somebody else's problem and a perfectly
normal thing. A bill for more than turned up is a payment the site cannot
justify, and the moment to stop it is before it is posted, because after
posting it is a ledger entry somebody has to reverse.

**The tolerance is the one M3 already configures.** No hard-coded 5% appears
here for the same reason it appears nowhere else in this module: whichever
number were chosen would be somebody's policy adopted silently.
"""

from odoo import _, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _re_project_orders(self):
        """The project-coded purchase orders this bill draws on.

        Read through `sudo()`: an accounts-payable clerk posting a vendor bill
        is not somebody with read access to `realestate.project`, and a gate
        that raised an `AccessError` at the person entitled to do the thing is
        the M7 Confirmation Gate mistake. It has now been made three times in
        this codebase and caught three times; the pattern is written down here
        so it is not made a fourth.
        """
        self.ensure_one()
        lines = self.invoice_line_ids.sudo().filtered('purchase_line_id')
        orders = lines.purchase_line_id.order_id
        if 're_project_id' not in orders._fields:
            return orders.browse()
        return orders.filtered('re_project_id')

    def _re_match_tolerance(self, order):
        """The percentage allowance, from the company that owns the order.

        Only the percentage. M3 also configures a flat
        `procurement_amount_tolerance_amount`, and it is deliberately not used
        here: it is an allowance in **money**, and this check is on
        **quantity**. Converting one into the other would need a price, the
        price in question is the one under dispute, and a tolerance derived
        from the disputed number is not a tolerance.
        """
        return order.company_id.sudo().procurement_amount_tolerance_pct or 0.0

    def _re_billed_quantities(self):
        """What this move adds, per purchase line.

        Used for the message, **not** for the comparison. `qty_invoiced` on the
        purchase line already counts draft bills — including this one, which is
        still draft while `_post()` runs — so adding this move's quantity to it
        counts the same bill twice and refuses a perfectly correct invoice for
        exactly what arrived. The first version of this gate did that, and the
        very first test caught it.
        """
        self.ensure_one()
        billed = {}
        sign = -1.0 if self.move_type == 'in_refund' else 1.0
        for line in self.invoice_line_ids.sudo().filtered('purchase_line_id'):
            quantity = line.quantity * sign
            billed[line.purchase_line_id] = billed.get(
                line.purchase_line_id, 0.0) + quantity
        return billed

    def _check_three_way_match(self):
        """Refuse a bill for more than the site accepted.

        Checked per purchase line rather than per bill total. A bill that is
        under on one item and over on another nets to something reasonable and
        is still paying for material that never arrived — netting is exactly
        how that stops being visible.
        """
        for move in self:
            if move.move_type not in ('in_invoice', 'in_refund'):
                continue
            orders = move._re_project_orders()
            if not orders:
                continue
            # No inspection-policy lookup here on purpose. Where a project does
            # not inspect, "accepted" is simply whatever Inventory says
            # arrived, and the arithmetic is unchanged — it is the *inspection*
            # that is optional, not the match. An earlier draft read the policy
            # and did nothing with it, which is the dead-compute shape M8 has
            # just finished removing from the requisition.
            over = []
            adds = move._re_billed_quantities()
            for order in orders:
                pct = move._re_match_tolerance(order)
                for line, quantity in adds.items():
                    if line.order_id != order or quantity <= 0:
                        continue
                    received = line.qty_received or 0.0
                    # `qty_invoiced` is the total including this draft move.
                    # It is the whole comparison; nothing is added to it.
                    billed = line.qty_invoiced or 0.0
                    allowance = max(received * pct / 100.0, 0.0)
                    if billed <= received + allowance + 1e-6:
                        continue
                    over.append((line, billed, quantity, received))

            if not over:
                continue
            details = '\n'.join(
                _("  %(product)s — this bill adds %(adding)s, billed in total "
                  "%(billed)s, accepted on site %(received)s",
                  product=line.product_id.display_name or line.name,
                  adding=quantity, billed=billed, received=received)
                for line, billed, quantity, received in over)
            raise UserError(_(
                "%(move)s bills for more than this site accepted:\n\n"
                "%(details)s\n\n"
                "Receive and inspect the rest before billing it, or ask the "
                "vendor for a corrected invoice. Material that was rejected "
                "is not billable, and posting this would put the difference "
                "into the ledger where somebody has to reverse it.",
                move=move.display_name or _('This bill'), details=details))

    def _post(self, soft=True):
        """The gate sits on posting, which is the moment money becomes owed.

        Not on create: a draft bill that overshoots is a normal thing to be
        working on, and refusing to save it would send people to a spreadsheet.
        """
        self._check_three_way_match()
        return super()._post(soft=soft)
