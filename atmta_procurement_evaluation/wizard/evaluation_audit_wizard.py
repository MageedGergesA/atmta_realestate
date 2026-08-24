# -*- coding: utf-8 -*-
"""M6 — a way for a person to actually run the integrity audit.

`realestate.procurement.evaluation.audit.run()` was complete and well tested
and completely unreachable: an `AbstractModel` method with no view, no action
and no menu. Whatever it found, nobody in the interface could ask it.

This is the smallest surface that fixes that, and deliberately not a dashboard.
The audit answers one question — *is there anything here that would make an
evaluation indefensible* — so the wizard asks it once and shows what came back.

It reports and it does not repair. There is no "fix" button anywhere in this
file, and there will not be one: a script that silently corrected a duplicate
rank or supplied a missing rationale would destroy the evidence that either had
ever been wrong.
"""

from odoo import _, api, fields, models

#: Ordered worst-first, the way the findings themselves are sorted.
SEVERITY_SEQUENCE = ('critical', 'high', 'medium', 'low')


class EvaluationAuditWizard(models.TransientModel):
    _name = 'realestate.procurement.evaluation.audit.wizard'
    _description = 'Run the Evaluation Integrity Audit'

    company_id = fields.Many2one(
        'res.company', required=True, default=lambda s: s.env.company,
        help="Evaluations are audited one company at a time, because that is "
             "the boundary the record rules draw.")
    round_id = fields.Many2one(
        'realestate.procurement.evaluation.round', string='Single Round',
        domain="[('company_id', '=', company_id)]",
        help="Leave empty to audit every evaluation in the company.")

    state = fields.Selection([
        ('draft', 'Ready'),
        ('done', 'Reported'),
    ], default='draft', readonly=True)
    run_on = fields.Datetime(readonly=True)
    scope = fields.Char(readonly=True)
    round_count = fields.Integer(readonly=True)

    critical_count = fields.Integer(readonly=True)
    high_count = fields.Integer(readonly=True)
    medium_count = fields.Integer(readonly=True)
    low_count = fields.Integer(readonly=True)
    finding_ids = fields.One2many(
        'realestate.procurement.evaluation.audit.finding', 'wizard_id',
        readonly=True)
    summary = fields.Text(readonly=True)

    def action_run(self):
        """Ask the audit, and write down exactly what it said."""
        self.ensure_one()
        Audit = self.env['realestate.procurement.evaluation.audit']
        # No sudo. `run()` checks the caller is entitled to it, and borrowing
        # superuser here would hand every finding — which names bids, vendors
        # and rounds — to whoever opened the wizard.
        report = Audit.run(company=self.company_id, round_=self.round_id or None)
        counts = report['counts']

        self.finding_ids.unlink()
        self.write({
            'state': 'done',
            'run_on': report['as_of'],
            'scope': (self.round_id.display_name if self.round_id
                      else _("All evaluations in %s") % self.company_id.name),
            'round_count': len(report['round_ids']),
            'critical_count': counts.get('critical', 0),
            'high_count': counts.get('high', 0),
            'medium_count': counts.get('medium', 0),
            'low_count': counts.get('low', 0),
            'summary': self._summarise(report),
            'finding_ids': [
                (0, 0, {
                    'key': finding['key'],
                    'severity': finding['severity'],
                    'summary': finding['summary'],
                    'record_count': finding['count'],
                    'references': ', '.join(finding['references']),
                    'remediation': finding['remediation'],
                })
                for finding in report['findings']
            ],
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'name': _("Evaluation Integrity Audit"),
        }

    @api.model
    def _summarise(self, report):
        counts = report['counts']
        total = sum(counts.values())
        if not report['round_ids']:
            return _("There are no evaluation rounds in scope, so there was "
                     "nothing to audit. That is not the same as a clean "
                     "result.")
        if not total:
            return _(
                "%s evaluation round(s) audited. No findings.\n\n"
                "Seventeen checks ran and none of them matched.",
                len(report['round_ids']))
        return _(
            "%(rounds)s evaluation round(s) audited. %(critical)s critical, "
            "%(high)s high, %(medium)s medium, %(low)s low.\n\n"
            "Nothing has been changed. Every finding below is a question for "
            "a person to answer — correcting one automatically would remove "
            "the evidence that it happened.",
            rounds=len(report['round_ids']), critical=counts.get('critical', 0),
            high=counts.get('high', 0), medium=counts.get('medium', 0),
            low=counts.get('low', 0))


class EvaluationAuditFinding(models.TransientModel):
    _name = 'realestate.procurement.evaluation.audit.finding'
    _description = 'Evaluation Integrity Finding'
    _order = 'severity_sequence, id'

    wizard_id = fields.Many2one(
        'realestate.procurement.evaluation.audit.wizard', required=True,
        ondelete='cascade', index=True)
    key = fields.Char(readonly=True)
    severity = fields.Selection([
        ('critical', 'Critical'),
        ('high', 'High'),
        ('medium', 'Medium'),
        ('low', 'Low'),
    ], readonly=True)
    severity_sequence = fields.Integer(
        compute='_compute_severity_sequence', store=True)
    summary = fields.Text(readonly=True)
    record_count = fields.Integer(readonly=True)
    references = fields.Text(
        readonly=True,
        help="The records the finding is about, named so it can be followed "
             "up. Capped by the audit itself rather than listed in full.")
    remediation = fields.Text(readonly=True)

    @api.depends('severity')
    def _compute_severity_sequence(self):
        for finding in self:
            finding.severity_sequence = (
                SEVERITY_SEQUENCE.index(finding.severity)
                if finding.severity in SEVERITY_SEQUENCE else 99)
