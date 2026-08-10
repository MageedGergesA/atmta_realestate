# -*- coding: utf-8 -*-
"""Post-upgrade classification for the enterprise Construction release.

This script **classifies**; it does not invent. Legacy databases predate WBS,
cost codes, budget baselines, forecasts, company scoping and every other
control concept M1–M9 introduced, and the honest response to an ambiguous
legacy value is to record that it is ambiguous — not to pick one and make the
cost sheet look tidy.

Everything here is idempotent and restart-safe: it derives a classification,
writes it only where the target is still empty, and can be run again with no
further effect. `M10F` proves that by running the upgrade twice and comparing
totals.
"""
import logging

_logger = logging.getLogger(__name__)

#: The vocabulary used in every log line, so an operator can grep one word.
MIGRATED = 'MIGRATED'
DETERMINISTIC = 'DETERMINISTIC'
LEGACY_VALID = 'LEGACY_VALID'
AMBIGUOUS = 'AMBIGUOUS'
NEEDS_FINANCE_REVIEW = 'NEEDS_FINANCE_REVIEW'
SKIPPED = 'SKIPPED'


def migrate(cr, version):
    if not version:
        # A fresh install has no legacy data to classify.
        return

    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})

    counts = {}
    counts.update(_classify_company(env))
    counts.update(_classify_budgets(env))
    counts.update(_classify_retention(env))
    counts.update(_classify_certification(env))
    counts.update(_report_project_access(env))

    _logger.info('Construction migration classification: %s',
                 ', '.join('%s=%s' % item for item in sorted(counts.items())))


def _classify_company(env):
    """Derive a company only where it is deterministic.

    Phase 0 found no company architecture at all. Defaulting every orphan to
    the current user's company would silently place another company's records
    in this one — the exact failure the M9 global rules exist to prevent.
    """
    deterministic = ambiguous = 0
    models = (
        ('realestate.construction.payment.certificate', 'project_id'),
        ('realestate.construction.contract.package', 'project_id'),
        ('realestate.boq', 'project_id'),
        ('realestate.owner.progress.billing', 'project_id'),
    )
    for model_name, project_field in models:
        if model_name not in env:
            continue
        model = env[model_name]
        if 'company_id' not in model._fields:
            continue
        orphans = model.sudo().search([('company_id', '=', False)])
        for record in orphans:
            project = record[project_field]
            company = project.company_id if project else False
            if company:
                record.company_id = company
                deterministic += 1
            else:
                ambiguous += 1
                _logger.warning(
                    '%s %s: %s — no deterministic company; left unset for '
                    'review.', AMBIGUOUS, model_name, record.id)
    return {'company_%s' % DETERMINISTIC.lower(): deterministic,
            'company_%s' % AMBIGUOUS.lower(): ambiguous}


def _classify_budgets(env):
    """Report which projects can be baselined deterministically.

    The classifier already exists (`realestate.construction.budget.migration`)
    and is deliberately read-only. This records its verdict in the log so the
    upgrade leaves evidence, and creates nothing.
    """
    Migration = env.get('realestate.construction.budget.migration')
    if Migration is None:
        return {}
    #: The classifier's own vocabulary, mapped to the migration vocabulary.
    status_to_class = {
        'deterministic': DETERMINISTIC,
        'ambiguous': AMBIGUOUS,
        'legacy_only': LEGACY_VALID,
        'no_source': SKIPPED,
    }
    verdicts = {}
    for row in Migration.classify():
        key = status_to_class.get(row['status'], SKIPPED)
        verdicts[key] = verdicts.get(key, 0) + 1
        if key == AMBIGUOUS:
            _logger.warning(
                '%s budget: project %s (%s) — expected %.2f, milestones '
                '%.2f, BOQ %.2f disagree by %.2f. No baseline created; a cost '
                'controller must choose.', AMBIGUOUS, row['project_id'],
                row['project_name'], row['expected_budget'],
                row['milestone_total'], row['boq_total'], row['spread'])
    return {'budget_%s' % key.lower(): value
            for key, value in verdicts.items()}


def _classify_retention(env):
    """Quantify the pre-M7 retention understatement. Never post an entry.

    Reclassifying posted entries is a Finance decision with a period and an
    approver attached to it. The upgrade's job is to say exactly how much is
    affected and stop.
    """
    Certificate = env.get('realestate.construction.payment.certificate')
    if Certificate is None:
        return {}
    affected = Certificate.sudo().search([
        ('state', 'in', ('invoiced', 'paid')),
        ('retention_amount', '>', 0),
        ('retention_posted_correctly', '=', False),
    ])
    if affected:
        total = sum(affected.mapped('retention_amount'))
        _logger.warning(
            '%s retention: %s certificate(s) posted retention as negative '
            'expense before M7, understating construction cost by %.2f. The '
            'posted entries are NOT rewritten. See UPGRADE_AND_OPERATIONS.md '
            '— Finance runbook.', NEEDS_FINANCE_REVIEW, len(affected), total)
    return {'retention_%s' % NEEDS_FINANCE_REVIEW.lower(): len(affected)}


def _classify_certification(env):
    """Mark legacy over-certification without rewriting history."""
    BOQLine = env.get('realestate.boq.line')
    if BOQLine is None:
        return {}
    over = BOQLine.sudo().search([('is_over_certified', '=', True)])
    for line in over:
        _logger.warning(
            '%s certification: BOQ line %s certified %.2f against an '
            'authorised %.2f. History preserved; future certificates already '
            'refuse to add to it.', LEGACY_VALID, line.id,
            line.certified_qty, line.authorised_quantity)
    return {'certification_legacy_overcertification': len(over)}


def _report_project_access(env):
    """M9's project restriction is opt-in and stays that way.

    Populating members here would activate access control on every project on
    upgrade day and lock people out of their own work. Projects with no team
    keep company-wide visibility until somebody names one.
    """
    Project = env['realestate.project']
    if 'construction_member_ids' not in Project._fields:
        return {}
    restricted = Project.sudo().search_count(
        [('construction_member_ids', '!=', False)])
    total = Project.sudo().search_count([])
    _logger.info(
        '%s project access: %s of %s project(s) have a construction team and '
        'are therefore restricted; the rest retain company-wide visibility by '
        'design.', LEGACY_VALID, restricted, total)
    return {'projects_restricted': restricted, 'projects_total': total}
