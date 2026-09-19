# -*- coding: utf-8 -*-
"""Shared fixtures for the investment suite.

A feasibility study appraises a project, so the smallest believable setup is
one project with a budget and a revenue expectation, and a helper to build a
study from a plain list of yearly net flows.
"""

from odoo.tests.common import TransactionCase


class InvestmentCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Study = cls.env['realestate.investment.feasibility']
        cls.project = cls.env['realestate.project'].create({
            'name': 'Investment Towers', 'code': 'INVT',
            'company_id': cls.env.company.id,
            'expected_budget': 1000000.0, 'expected_revenue': 1500000.0,
        })

    @classmethod
    def _study(cls, flows=(), **vals):
        """A study whose period ``t`` nets ``flows[t]`` (inflow if positive,
        outflow if negative)."""
        values = {'name': 'Study', 'discount_rate': 10.0, 'analysis_period_years': 3,
                  'cash_flow_ids': [(0, 0, {
                      'period_index': t,
                      'inflow': max(cf, 0.0),
                      'outflow': max(-cf, 0.0),
                  }) for t, cf in enumerate(flows)]}
        values.update(vals)
        return cls.Study.create(values)
