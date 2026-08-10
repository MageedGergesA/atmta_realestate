# -*- coding: utf-8 -*-
"""Post-upgrade validation. Reports; never repairs.

Runs after every dependency has finished upgrading, re-derives the control
equations from their sources and logs anything that disagrees. An upgrade that
silently corrected an anomaly would destroy the evidence of how it arose.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})

    Audit = env.get('realestate.construction.integrity.audit')
    if Audit is None:
        return

    report = Audit.run()
    if not report['findings']:
        _logger.info('Construction post-upgrade validation: no findings.')
        return

    for finding in report['findings']:
        log = _logger.error if finding['severity'] == 'critical' else (
            _logger.warning if finding['severity'] == 'high'
            else _logger.info)
        log('Construction integrity [%s/%s] %s — %s (expected: %s)',
            finding['severity'], finding['category'], finding['key'],
            finding['actual'], finding['expected'])

    critical = report['by_severity'].get('critical', 0)
    if critical:
        _logger.error(
            'Construction post-upgrade validation found %s critical '
            'record(s). Nothing has been modified. Run the integrity audit '
            'and follow UPGRADE_AND_OPERATIONS.md before going live.',
            critical)
