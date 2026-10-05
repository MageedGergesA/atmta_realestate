# -*- coding: utf-8 -*-
"""Demo layer: a post-dated cheque book against the sale schedule.

In this market instalments are routinely secured by a book of post-dated
cheques handed over at signing, so the natural source of demo cheques is the
sale instalments that already exist -- one cheque per upcoming instalment,
dated to match.

The Treasury dashboard is built around one distinction: **paper is not cash.**
A cheque on hand, deposited, or in clearing is still only an instrument; only
a cleared one is money. The demo therefore spreads cheques across the whole
lifecycle -- on hand, deposited, in clearing, cleared, and two bounced -- so
that distinction is visible rather than theoretical.
"""

import logging

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

MODULE = 'real_estate_checks'
SENTINEL = '%s.demo_check_location_treasury' % MODULE

#: (state, how many, due offset in days). Weighted towards paper still held,
#: because that is what a treasury function actually looks like.
CHEQUE_PLAN = [
    ('cleared', 6, -120),
    ('cleared', 5, -75),
    ('bounced', 2, -45),
    ('in_clearing', 3, -8),
    ('deposited', 4, -2),
    ('registered', 10, 25),
    ('registered', 8, 60),
    ('registered', 6, 95),
]


class ChecksDemoBuilder(models.AbstractModel):
    _name = 'realestate.demo.checks'
    _description = 'Demo Builder — Post-Dated Cheques'

    @api.model
    def _xmlid(self, suffix, record):
        self.env['ir.model.data']._update_xmlids([{
            'xml_id': '%s.%s' % (MODULE, suffix),
            'record': record,
            'noupdate': True,
        }])
        return record

    # ------------------------------------------------------------------
    @api.model
    def _build(self):
        if self.env.ref(SENTINEL, raise_if_not_found=False):
            return True
        locations = self._build_locations()
        banks = self._build_banks()
        self._build_cheques(locations, banks)
        return True

    @api.model
    def _build_locations(self):
        Location = self.env['realestate.check.location']
        wanted = [('treasury', 'Treasury', 'treasury'),
                  ('safe', 'Head Office Safe', 'safe'),
                  ('sales_office', 'Sales Office', 'sales_office'),
                  ('bank', 'Collecting Bank', 'bank')]
        locations = {}
        for key, name, kind in wanted:
            existing = Location.search([('name', '=', name)], limit=1)
            location = existing or Location.create({'name': name, 'kind': kind})
            locations[key] = self._xmlid('demo_check_location_%s' % key, location)
        return locations

    @api.model
    def _build_banks(self):
        """Banks the cheques are drawn on.

        `realestate.check.bank_id` is required: a cheque with no drawee is not
        a cheque, and the dashboard groups presentations by bank.
        """
        Bank = self.env['res.bank']
        names = [('National Bank of Egypt', 'NBEGEGCX'),
                 ('Banque Misr', 'BMISEGCX'),
                 ('Commercial International Bank', 'CIBEEGCX')]
        banks = []
        for index, (name, bic) in enumerate(names, start=1):
            existing = Bank.search([('name', '=', name)], limit=1)
            bank = existing or Bank.create({'name': name, 'bic': bic})
            banks.append(self._xmlid('demo_bank_%d' % index, bank))
        return banks

    # ------------------------------------------------------------------
    @api.model
    def _build_cheques(self, locations, banks):
        Check = self.env['realestate.check']
        today = fields.Date.context_today(self)
        instalments = self._instalments()
        if not instalments:
            _logger.warning("Demo: no sale instalments, cheque book skipped")
            return True

        index, position = 0, 0
        for state, count, offset in CHEQUE_PLAN:
            for _n in range(count):
                if position >= len(instalments):
                    position = 0
                instalment = instalments[position]
                position += 1
                index += 1
                vals = {
                    'check_number': '%07d' % (4500000 + index),
                    'partner_id': instalment.sale_contract_id.partner_id.id,
                    'bank_id': banks[index % len(banks)].id,
                    'amount': instalment.current_amount or instalment.amount or 50000.0,
                    'due_date': today + relativedelta(days=offset),
                    # A cheque cannot fall due before it was written, and the
                    # issue date defaults to today -- so a back-dated cheque
                    # needs its issue date back-dated too. Post-dated cheques
                    # in this market are typically handed over a quarter
                    # ahead, which is what this reflects.
                    'issue_date': today + relativedelta(days=offset - 90),
                }
                try:
                    with self.env.cr.savepoint():
                        cheque = Check.create(vals)
                        # The state is walked, not written at creation: the
                        # lifecycle is the thing the Treasury screen reports
                        # on, and a demo that writes `cleared` straight into
                        # `create` would hide the day the lifecycle stops
                        # allowing it.
                        cheque.state = state
                        self._xmlid('demo_check_%d' % index, cheque)
                except Exception as error:      # noqa: BLE001 - demo only
                    _logger.warning("Demo: cheque %s could not reach %s: %s",
                                    vals['check_number'], state, error)
        return True

    @api.model
    def _instalments(self):
        """Instalments worth securing: the ones still to be collected."""
        if 'realestate.sale.installment' not in self.env:
            return self.env['realestate.sale.installment']
        return self.env['realestate.sale.installment'].search(
            [('is_cancelled', '=', False)], order='date_due', limit=60)
