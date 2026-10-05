# -*- coding: utf-8 -*-
"""Demo layer: units being handed over, with a snag list.

Handover is where a developer's build quality meets its buyer, so a demo that
hands over four spotless units demonstrates nothing. These carry real snags at
every severity and every state -- open, assigned, in progress, resolved and
verified -- because the panel the handover team actually lives on is the one
that says what is still outstanding and how bad it is.

Built from the sale contracts that already exist, so a handover always points
at a real buyer and a real unit.
"""

import logging

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

MODULE = 'real_estate_handover'
SENTINEL = '%s.demo_handover_1' % MODULE

#: (state, months from today, snags as (severity, state) pairs)
HANDOVER_PLAN = [
    ('completed', -4, [('minor', 'verified'), ('minor', 'verified')]),
    ('completed', -2, [('major', 'resolved'), ('minor', 'verified')]),
    ('snagging', -1, [('critical', 'in_progress'), ('major', 'assigned'),
                      ('minor', 'open'), ('minor', 'open')]),
    ('inspection', 0, [('major', 'open'), ('minor', 'open')]),
    ('scheduled', 1, []),
    ('scheduled', 2, []),
]

SNAG_TEXT = {
    'critical': 'Water ingress at the balcony threshold during testing.',
    'major': 'Kitchen cabinet doors out of alignment; carcass not plumb.',
    'minor': 'Paint touch-up required to the hallway architrave.',
}


class HandoverDemoBuilder(models.AbstractModel):
    _name = 'realestate.demo.handover'
    _description = 'Demo Builder — Handover and Snagging'

    @api.model
    def _xmlid(self, suffix, record):
        self.env['ir.model.data']._update_xmlids([{
            'xml_id': '%s.%s' % (MODULE, suffix), 'record': record,
            'noupdate': True}])
        return record

    @api.model
    def _build(self):
        if self.env.ref(SENTINEL, raise_if_not_found=False):
            return True
        contracts = self.env['realestate.sale.contract'].search(
            [('state', 'in', ('signed', 'active', 'financially_cleared',
                              'handed_over'))], order='contract_date', limit=12)
        if not contracts:
            _logger.warning("Demo: no live sale contract, handover skipped")
            return True

        Handover = self.env['realestate.handover']
        Snag = self.env['realestate.snagging.issue']
        Warranty = self.env['realestate.warranty']
        today = fields.Date.context_today(self)

        for index, (state, months, snags) in enumerate(HANDOVER_PLAN, start=1):
            if index > len(contracts):
                break
            contract = contracts[index - 1]
            scheduled = today + relativedelta(months=months)
            vals = {'sale_contract_id': contract.id, 'state': state}
            for field, value in (('scheduled_date', scheduled),
                                 ('handover_date', scheduled if months <= 0 else False)):
                if field in Handover._fields:
                    vals[field] = value
            try:
                with self.env.cr.savepoint():
                    handover = Handover.create(vals)
            except Exception as error:          # noqa: BLE001 - demo only
                _logger.warning("Demo: handover for %s failed: %s",
                                contract.name, error)
                continue
            self._xmlid('demo_handover_%d' % index, handover)

            for position, (severity, snag_state) in enumerate(snags, start=1):
                snag_vals = {
                    'description': SNAG_TEXT[severity],
                    'severity': severity,
                    'state': snag_state,
                    'reported_date': scheduled - relativedelta(days=position * 2),
                }
                for field, value in (('handover_id', handover.id),
                                     ('property_id', contract.property_id.id)):
                    if field in Snag._fields:
                        snag_vals[field] = value
                try:
                    with self.env.cr.savepoint():
                        Snag.create(snag_vals)
                except Exception as error:      # noqa: BLE001 - demo only
                    _logger.warning("Demo: snag on %s failed: %s",
                                    handover.display_name, error)

            # A warranty only exists once the unit has actually changed hands.
            if state == 'completed':
                try:
                    with self.env.cr.savepoint():
                        Warranty.create({
                            'name': 'Structural warranty — %s'
                                    % (contract.property_id.display_name or contract.name),
                            'sale_contract_id': contract.id,
                            'start_date': scheduled,
                            'period_months': 120,
                            'end_date': scheduled + relativedelta(months=120),
                        })
                except Exception as error:      # noqa: BLE001 - demo only
                    _logger.warning("Demo: warranty for %s failed: %s",
                                    contract.name, error)
        return True
