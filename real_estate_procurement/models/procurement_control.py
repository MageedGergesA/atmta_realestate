# -*- coding: utf-8 -*-
"""M3 — the one place that answers "can this project still buy this?".

```
    AVAILABLE TO PROCURE = CURRENT BUDGET
                         - CURRENT COMMITMENT        (Construction's number)
                         - ACTIVE PROCUREMENT RESERVATIONS
```

Three sources, one of which is ours. The other two belong to Construction and
are **read**, never recomputed: `budget_by_cost_code()` and
`current_commitment_by_cost_code()` already encode rules Procurement has no
business restating — that a package and its purchase orders are one commitment
counted once, that an approved variation folded into an amended order is not
added on top, that uncoded money is reported under Unassigned rather than
dropped. A second implementation of any of those would eventually disagree
with the cost report, and then nobody could say which number was the project's.

`available_to_procure` is deliberately not a stored field on anything. It is
the difference between three moving numbers, and a stored copy would be a
fourth number that is wrong for as long as it takes something to recompute it.

### Unknown is not available

Rule 4, and the reason this service returns a status rather than only a
figure. When the position cannot be established — no project, no cost code, no
Construction baseline, Construction not installed at all — the answer is
`insufficient_data`. Not zero, which would refuse legitimate demand and be
read as "the budget is exhausted", and not unlimited, which is how a control
system becomes decorative.
"""

from odoo import _, api, fields, models

#: The three answers this service can give.
STATUS_OK = 'ok'
STATUS_OVER = 'over_budget'
STATUS_UNKNOWN = 'insufficient_data'

#: Prefix for the advisory lock token, so procurement's locks cannot collide
#: with another subsystem that happens to hash the same project id.
LOCK_NAMESPACE = 'realestate.procurement.control'


class ProcurementControl(models.AbstractModel):
    _name = 'realestate.procurement.control'
    _description = 'Procurement Control Position'

    # ------------------------------------------------------------------
    # Policy resolution — company default, project override.
    # ------------------------------------------------------------------
    @api.model
    def budget_policy_for(self, project, company=None):
        company = company or (project.company_id if project else False) \
            or self.env.company
        policy = project.procurement_budget_policy if project else 'company'
        if policy and policy != 'company':
            return policy
        return company.procurement_budget_policy or 'warn'

    @api.model
    def po_governance_for(self, project, company=None):
        company = company or (project.company_id if project else False) \
            or self.env.company
        policy = project.procurement_po_governance if project else 'company'
        if policy and policy != 'company':
            return policy
        return company.procurement_po_governance or 'optional'

    # ------------------------------------------------------------------
    # Locking — M3D.
    # ------------------------------------------------------------------
    @api.model
    def lock_control_scopes(self, scopes):
        """Serialise everybody who is about to spend the same capacity.

        Without this the reservation calculation is a read-then-write race:
        two approvals of 8,000,000 against a 10,000,000 budget both read
        10,000,000 available, both reserve, and the project is 6,000,000 over
        before anybody has done anything wrong.

        The lock is a PostgreSQL transaction-level advisory lock, so it is
        released by commit or rollback and never by us forgetting to. Its
        scope is one company/project/cost code — locking the whole procurement
        system would make two unrelated projects queue behind each other for
        no reason.

        **Lock ordering.** A requisition spanning concrete and electrical
        takes two locks, and a second requisition spanning the same two in the
        other order would deadlock with it. The tokens are therefore sorted
        before anything is taken, so every caller in the system acquires the
        same scopes in the same sequence regardless of the order the user
        happened to type the lines in.
        """
        tokens = sorted({self._scope_token(*scope) for scope in scopes})
        for token in tokens:
            self.env.cr.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (token,))
        return tokens

    @api.model
    def _scope_token(self, company_id, project_id, cost_code_id):
        return '%s:%s:%s:%s' % (LOCK_NAMESPACE, company_id or 0,
                                project_id or 0, cost_code_id or 0)

    # ------------------------------------------------------------------
    # The position.
    # ------------------------------------------------------------------
    @api.model
    def get_procurement_control_position(self, project, cost_code=None,
                                         ignore_reservations=None):
        """The control position for one project/cost-code scope.

        Returns a dict rather than a record: this is a reading of three other
        systems at a moment in time, and giving it an id would invite somebody
        to store it and quote it later.
        """
        code_id = cost_code.id if cost_code else False
        return self.positions_by_cost_code(
            project, [code_id],
            ignore_reservations=ignore_reservations)[code_id]

    @api.model
    def positions_by_cost_code(self, project, cost_code_ids,
                               ignore_reservations=None):
        """Every requested scope on one project, in a bounded set of queries.

        M3AC: a requisition with forty lines must not ask Construction for the
        budget forty times. The three source maps are read once per project
        and the positions are assembled in Python.
        """
        budget_map, commitment_map, base_warnings = self._construction_maps(
            project)
        reserved_map = self._reserved_by_cost_code(
            project, ignore_reservations=ignore_reservations)
        company = (project.company_id if project else False) or self.env.company
        currency = company.currency_id
        as_of = fields.Datetime.now()

        positions = {}
        for code_id in cost_code_ids:
            warnings = list(base_warnings)
            budget_row = budget_map.get(code_id) if budget_map else None
            budget = budget_row['current'] if budget_row else 0.0
            commitment = commitment_map.get(code_id, 0.0)
            reserved = reserved_map.get(code_id, 0.0)
            available = budget - commitment - reserved

            status = STATUS_OK
            if not project:
                status = STATUS_UNKNOWN
                warnings.append(_("The demand names no project, so there is "
                                  "no budget to measure it against."))
            elif not code_id:
                status = STATUS_UNKNOWN
                warnings.append(_("The demand carries no cost code. Control "
                                  "is per cost code, so this cannot be "
                                  "positioned against a budget line."))
            elif base_warnings:
                # Construction absent, or no baselined budget at all.
                status = STATUS_UNKNOWN
            elif budget_row is None:
                status = STATUS_UNKNOWN
                warnings.append(_(
                    "The approved budget has no line for this cost code. "
                    "Nothing budgeted and not yet baselined look identical "
                    "from here, and they are not the same thing."))
            elif available < 0:
                status = STATUS_OVER

            positions[code_id] = {
                'project_id': project.id if project else False,
                'cost_code_id': code_id,
                'company_id': company.id,
                'currency_id': currency.id,
                'current_budget': budget,
                'current_commitment': commitment,
                'reserved': reserved,
                'available': available,
                'status': status,
                'warnings': warnings,
                'as_of': as_of,
                'source': 'realestate.construction.controls' if not
                base_warnings else 'procurement',
            }
        return positions

    # ------------------------------------------------------------------
    @api.model
    def _construction_maps(self, project):
        """Budget and commitment as Construction states them.

        Returns `(budget_map, commitment_map, warnings)`. A non-empty warnings
        list means the maps are not authoritative and every position built
        from them is `insufficient_data`.
        """
        if not project:
            return {}, {}, [_("No project.")]
        if 'realestate.construction.controls' not in self.env:
            return {}, {}, [_(
                "Construction is not installed, so this system has no budget "
                "or commitment to read. Procurement will not invent one.")]
        # Elevated, and only for these two readings.
        #
        # A requester submitting a requisition has no rights on a Construction
        # budget and should not acquire any: the project's baseline is
        # commercially sensitive and belongs to the cost-control team. What
        # they need is the *answer* — whether their demand fits — and that is
        # a control statement this service is entitled to make on their
        # behalf. Solved on the Procurement side deliberately: widening
        # Construction's access rules would hand every procurement user the
        # underlying budget records, which is a far bigger door than this.
        Controls = self.env['realestate.construction.controls'].sudo()
        Commitment = self.env['realestate.construction.commitment'].sudo()
        budget_map = Controls.budget_by_cost_code(project)
        commitment_map = Commitment.current_commitment_by_cost_code(project)
        if not budget_map:
            return budget_map, commitment_map, [_(
                "%s has no baselined budget, so there is nothing authoritative "
                "to measure demand against.") % project.display_name]
        return budget_map, commitment_map, []

    @api.model
    def _reserved_by_cost_code(self, project, ignore_reservations=None):
        """Active procurement reservations, per cost code.

        Only `reserved` records count. A converted reservation has become a
        commitment and is counted there; a released or cancelled one is not
        counted anywhere, which is the entire point of releasing it.
        """
        Reservation = self.env['realestate.procurement.reservation']
        if not project:
            return {}
        domain = [('project_id', '=', project.id), ('state', '=', 'reserved')]
        if ignore_reservations:
            domain.append(('id', 'not in', list(ignore_reservations.ids)))
        if 'cost_code_id' not in Reservation._fields:
            total = sum(Reservation.sudo().search(domain).mapped(
                'amount_active'))
            return {False: total} if total else {}
        groups = Reservation.sudo()._read_group(
            domain, groupby=['cost_code_id'], aggregates=['amount_active:sum'])
        return {(code.id if code else False): total or 0.0
                for code, total in groups}

    # ------------------------------------------------------------------
    @api.model
    def describe_position(self, position):
        """The position as a sentence somebody can act on."""
        currency = self.env['res.currency'].browse(position['currency_id'])
        text = _(
            "Budget %(budget)s − commitment %(commitment)s − reserved "
            "%(reserved)s = %(available)s available.",
            budget=self.format_control_amount(position['current_budget'],
                                              currency),
            commitment=self.format_control_amount(
                position['current_commitment'], currency),
            reserved=self.format_control_amount(position['reserved'],
                                                currency),
            available=self.format_control_amount(position['available'],
                                                 currency))
        if position['warnings']:
            text += '\n' + '\n'.join(position['warnings'])
        return text

    @api.model
    def format_control_amount(self, amount, currency=None):
        """Thousands-separated, two decimals, currency named.

        Control amounts run to eight figures and an unseparated one is
        routinely misread by a factor of ten in exactly the conversation where
        that matters most.
        """
        currency = currency or self.env.company.currency_id
        return '%s %s' % (currency.name, '{:,.2f}'.format(amount or 0.0))
