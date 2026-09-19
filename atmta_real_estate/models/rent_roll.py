"""Enterprise rent roll (Phase 24).

The rent roll is the single report a property company is actually run from:
every leasable unit, who is in it, what they pay, what they owe, and when it
expires -- including the units nobody is in, because vacancy is the number that
costs money.

Implemented as a **PostgreSQL view** (``_auto = False``) rather than a computed
model. At the target scale (tens of thousands of units, hundreds of thousands
of obligations) a Python compute would issue a query per row; this issues one.
It also means list, pivot, graph and the XLSX export all read the same
definition.

The grain is **one row per lease allocation**, plus one row per vacant leasable
unit. That makes ``SUM(annualized_rent)`` correct without de-duplication, and
lets vacancy sit in the same report as occupancy.

``project_id`` is included only when ``real_estate_developer`` is installed --
the column is added to ``realestate_property`` by that module, so the view is
built conditionally rather than depending on it.
"""

from odoo import api, fields, models, tools

#: Ancestor lookup by hierarchy level, driven off ``parent_path``.
#:
#: A property's name is its product template's, which is translatable and so
#: stored as JSON. It is read in the reader's language, falling back to
#: English, as Odoo reads translated fields. Returning the JSON itself put an
#: object into a Char column, and the web client crashed rendering Rent Roll
#: ("this.child.mount is not a function") for any unit with a building.
_ANCESTOR_SQL = """
    LEFT JOIN LATERAL (
        SELECT anc.id, anc.property_code,
               COALESCE(pt.name->>%(lang)s, pt.name->>'en_US')::varchar AS name
        FROM realestate_property anc
        JOIN product_template pt ON pt.id = anc.product_tmpl_id
        WHERE anc.id = ANY (
            string_to_array(trim(both '/' from p.parent_path), '/')::int[])
          AND anc.hierarchy_level = '%(level)s'
        LIMIT 1
    ) %(alias)s ON TRUE
"""


class RentRoll(models.Model):
    _name = 'realestate.rent.roll'
    _description = 'Rent Roll'
    _auto = False
    _rec_name = 'property_code'
    _order = 'building_name, floor_name, property_code'

    # ---------------- Identity ----------------
    company_id = fields.Many2one('res.company', string='Company', readonly=True)
    property_id = fields.Many2one('realestate.property', string='Unit', readonly=True)
    property_code = fields.Char(string='Property Code', readonly=True)
    unit_number = fields.Char(string='Unit Number', readonly=True)
    usage_category = fields.Char(string='Usage', readonly=True)

    # ---------------- Hierarchy ----------------
    #: Declared as an Integer, not a Many2one, on purpose: ``realestate.project``
    #: only exists when ``real_estate_developer`` is installed, and a Many2one to
    #: a missing comodel breaks registry setup. ``project_name`` carries the
    #: label so the report is still groupable without the dependency.
    project_id = fields.Integer(string='Project ID', readonly=True)
    project_name = fields.Char(string='Project', readonly=True)
    compound_id = fields.Many2one('realestate.property', string='Compound', readonly=True)
    compound_name = fields.Char(readonly=True)
    building_id = fields.Many2one('realestate.property', string='Building', readonly=True)
    building_name = fields.Char(readonly=True)
    floor_id = fields.Many2one('realestate.property', string='Floor', readonly=True)
    floor_name = fields.Char(readonly=True)

    # ---------------- Lease ----------------
    contract_id = fields.Many2one('realestate.contract', string='Lease', readonly=True)
    lease_reference = fields.Char(string='Lease Reference', readonly=True)
    partner_id = fields.Many2one('res.partner', string='Tenant', readonly=True)
    lease_start = fields.Date(string='Lease Start', readonly=True)
    lease_end = fields.Date(string='Lease End', readonly=True)
    lifecycle_state = fields.Char(string='Lease Status', readonly=True)
    is_vacant = fields.Boolean(string='Vacant', readonly=True)

    # ---------------- Commercials ----------------
    currency_id = fields.Many2one('res.currency', string='Currency', readonly=True)
    area_sqm = fields.Float(string='Area (m²)', readonly=True)
    current_rent = fields.Monetary(string='Current Rent', readonly=True)
    recurring_charges = fields.Monetary(string='Recurring Charges', readonly=True)
    annualized_rent = fields.Monetary(string='Annualised Rent', readonly=True)
    rent_per_sqm = fields.Monetary(string='Rent / m²', readonly=True)
    deposit_held = fields.Monetary(string='Deposit Held', readonly=True)

    # ---------------- Money ----------------
    invoiced_amount = fields.Monetary(string='Invoiced', readonly=True)
    paid_amount = fields.Monetary(string='Paid', readonly=True)
    outstanding_amount = fields.Monetary(string='Outstanding', readonly=True)
    is_overdue = fields.Boolean(string='Overdue', readonly=True)

    # ---------------- Forward look ----------------
    next_escalation_date = fields.Date(string='Next Escalation', readonly=True)
    next_escalation_pct = fields.Float(string='Next Escalation (%)', readonly=True)
    days_to_expiry = fields.Integer(string='Days to Expiry', readonly=True)
    expiry_bucket = fields.Char(string='Expiry Bucket', readonly=True)
    renewal_state = fields.Char(string='Renewal Status', readonly=True)

    # ==================================================================
    # View definition
    # ==================================================================
    @property
    def _table_query(self):
        return self._rent_roll_query()

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            "CREATE OR REPLACE VIEW %s AS (%s)" % (self._table, self._rent_roll_query()))

    @api.model
    def _has_project_column(self):
        """``real_estate_developer`` adds ``project_id`` to the property table."""
        self.env.cr.execute("""
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'realestate_property' AND column_name = 'project_id'
        """)
        return bool(self.env.cr.fetchone())

    @api.model
    def _rent_roll_query(self):
        lang = self.env.cr.mogrify('%s', [self.env.lang or 'en_US']).decode()
        ancestors = ''.join(
            _ANCESTOR_SQL % {'level': level, 'alias': alias, 'lang': lang}
            for level, alias in (('compound', 'cmp'), ('building', 'bld'), ('floor', 'flr'))
        )
        # Money per (contract, property) read straight from the obligations,
        # which in turn read it from account.move.
        money_join = """
            LEFT JOIN LATERAL (
                SELECT
                    COALESCE(SUM(o.amount_invoiced), 0) AS invoiced,
                    COALESCE(SUM(o.amount_paid), 0)     AS paid,
                    COALESCE(SUM(o.amount_residual), 0) AS outstanding,
                    -- Same rule as the obligation's days overdue, evaluated
                    -- when the view is read (CURRENT_DATE is the database
                    -- server's date).
                    BOOL_OR(NOT o.is_settled AND o.amount_residual > 0.01
                            AND o.date_due < CURRENT_DATE) AS overdue
                FROM realestate_contract_payment o
                WHERE o.contract_id = c.id
                  AND (o.property_line_id IS NULL OR o.property_line_id = l.id)
                  AND o.state <> 'cancelled'
            ) money ON TRUE
        """
        deposit_join = """
            LEFT JOIN LATERAL (
                SELECT COALESCE(SUM(d.held_amount), 0) AS held
                FROM realestate_contract_deposit d
                WHERE d.contract_id = c.id
            ) dep ON TRUE
        """
        charges_join = """
            LEFT JOIN LATERAL (
                SELECT COALESCE(SUM(
                    CASE cr.calculation_type
                        WHEN 'fixed' THEN cr.amount
                        WHEN 'percentage' THEN l.allocated_rent * cr.percentage / 100.0
                        WHEN 'per_sqm' THEN cr.rate_per_sqm * COALESCE(l.area_sqm, 0)
                        ELSE 0
                    END
                    / GREATEST(CASE cr.frequency
                        WHEN 'monthly' THEN 1 WHEN 'quarterly' THEN 3
                        WHEN 'semiannual' THEN 6 WHEN 'annual' THEN 12
                        WHEN 'custom' THEN GREATEST(cr.custom_interval_months, 1)
                        ELSE NULL END, 1)
                ), 0) AS monthly
                FROM realestate_contract_charge_rule cr
                WHERE cr.contract_id = c.id
                  AND cr.active
                  AND cr.frequency <> 'one_time'
                  AND (cr.property_line_id IS NULL OR cr.property_line_id = l.id)
            ) chg ON TRUE
        """
        # Annualisation multiplier from the lease's billing frequency.
        periods_per_year = """
            CASE c.billing_frequency
                WHEN 'monthly' THEN 12 WHEN 'quarterly' THEN 4
                WHEN 'semiannual' THEN 2 WHEN 'annual' THEN 1 ELSE 12 END
        """
        has_project = self._has_project_column()
        project_leased = 'p.project_id' if has_project else 'NULL::int'
        project_join = ("""
            LEFT JOIN LATERAL (
                SELECT prj.name AS name
                FROM realestate_project prj WHERE prj.id = p.project_id
            ) prj ON TRUE
        """ if has_project else '')
        project_name = 'prj.name' if has_project else 'NULL::varchar'

        leased = """
            SELECT
                l.id                                        AS id,
                l.company_id                                AS company_id,
                p.id                                        AS property_id,
                p.property_code                             AS property_code,
                p.property_number                           AS unit_number,
                p.usage_category                            AS usage_category,
                {project_leased}                            AS project_id,
                {project_name}                              AS project_name,
                cmp.id AS compound_id, cmp.name AS compound_name,
                bld.id AS building_id, bld.name AS building_name,
                flr.id AS floor_id,    flr.name AS floor_name,
                c.id                                        AS contract_id,
                c.name                                      AS lease_reference,
                c.partner_id                                AS partner_id,
                l.start_date                                AS lease_start,
                l.end_date                                  AS lease_end,
                c.lifecycle_state                           AS lifecycle_state,
                FALSE                                       AS is_vacant,
                c.currency_id                               AS currency_id,
                COALESCE(l.area_sqm, 0)                     AS area_sqm,
                COALESCE(l.allocated_rent, 0)               AS current_rent,
                COALESCE(chg.monthly, 0)                    AS recurring_charges,
                COALESCE(l.allocated_rent, 0) * ({ppy})     AS annualized_rent,
                CASE WHEN COALESCE(l.area_sqm, 0) > 0
                     THEN COALESCE(l.allocated_rent, 0) / l.area_sqm
                     ELSE 0 END                             AS rent_per_sqm,
                COALESCE(dep.held, 0)                       AS deposit_held,
                COALESCE(money.invoiced, 0)                 AS invoiced_amount,
                COALESCE(money.paid, 0)                     AS paid_amount,
                COALESCE(money.outstanding, 0)              AS outstanding_amount,
                COALESCE(money.overdue, FALSE)              AS is_overdue,
                c.next_escalation_date                      AS next_escalation_date,
                COALESCE(c.next_escalation_pct, 0)          AS next_escalation_pct,
                -- Derived from the end date when the view is read, so they
                -- never go stale.
                CASE WHEN c.end_date IS NULL
                          OR c.lifecycle_state IN ('ended', 'terminated', 'cancelled')
                     THEN 0 ELSE c.end_date - CURRENT_DATE END   AS days_to_expiry,
                (CASE WHEN c.end_date IS NULL
                           OR c.lifecycle_state IN ('ended', 'terminated', 'cancelled')
                      THEN NULL
                      WHEN c.end_date < CURRENT_DATE THEN 'expired'
                      WHEN c.end_date - CURRENT_DATE <= 30 THEN 'lte_30'
                      WHEN c.end_date - CURRENT_DATE <= 60 THEN '31_60'
                      WHEN c.end_date - CURRENT_DATE <= 90 THEN '61_90'
                      WHEN c.end_date - CURRENT_DATE <= 180 THEN '91_180'
                      ELSE 'gt_180' END)::varchar                AS expiry_bucket,
                c.renewal_state                             AS renewal_state
            FROM realestate_contract_property_line l
            JOIN realestate_contract c ON c.id = l.contract_id
            JOIN realestate_property p ON p.id = l.property_id
            {ancestors}
            {project_join}
            {money}
            {deposit}
            {charges}
            WHERE c.lifecycle_state <> 'cancelled'
        """.format(ancestors=ancestors, money=money_join, deposit=deposit_join,
                   charges=charges_join, ppy=periods_per_year,
                   project_leased=project_leased, project_name=project_name,
                   project_join=project_join)

        project_vacant = project_leased
        vacant = """
            SELECT
                -p.id                                       AS id,
                p.company_id                                AS company_id,
                p.id                                        AS property_id,
                p.property_code                             AS property_code,
                p.property_number                           AS unit_number,
                p.usage_category                            AS usage_category,
                {project_vacant}                            AS project_id,
                {project_name}                              AS project_name,
                cmp.id AS compound_id, cmp.name AS compound_name,
                bld.id AS building_id, bld.name AS building_name,
                flr.id AS floor_id,    flr.name AS floor_name,
                NULL::int AS contract_id, NULL::varchar AS lease_reference,
                NULL::int AS partner_id,
                NULL::date AS lease_start, NULL::date AS lease_end,
                NULL::varchar AS lifecycle_state,
                TRUE                                        AS is_vacant,
                p.currency_id                               AS currency_id,
                COALESCE(p.area_sqm, 0)                     AS area_sqm,
                0 AS current_rent, 0 AS recurring_charges,
                0 AS annualized_rent, 0 AS rent_per_sqm, 0 AS deposit_held,
                0 AS invoiced_amount, 0 AS paid_amount, 0 AS outstanding_amount,
                FALSE AS is_overdue,
                NULL::date AS next_escalation_date,
                0 AS next_escalation_pct, 0 AS days_to_expiry,
                NULL::varchar AS expiry_bucket, NULL::varchar AS renewal_state
            FROM realestate_property p
            JOIN product_template ptv ON ptv.id = p.product_tmpl_id
            {ancestors}
            {project_join}
            WHERE p.is_leasable
              AND ptv.active
              AND NOT EXISTS (
                  SELECT 1 FROM realestate_contract_property_line l2
                  JOIN realestate_contract c2 ON c2.id = l2.contract_id
                  WHERE l2.property_id = p.id
                    AND c2.lifecycle_state IN ('pending_signature', 'active', 'notice')
              )
        """.format(ancestors=ancestors, project_vacant=project_vacant,
                   project_name=project_name, project_join=project_join)

        return '(%s) UNION ALL (%s)' % (leased, vacant)
