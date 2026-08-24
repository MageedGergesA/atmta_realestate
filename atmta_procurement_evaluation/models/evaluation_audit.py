# -*- coding: utf-8 -*-
"""M6 — the evaluation integrity audit.

Reports, never repairs. Every finding here is something that would make an
evaluation indefensible if it were true, and every one of them is a question a
person has to answer — a script that "corrected" a duplicate rank or a missing
rationale would destroy the evidence that it happened.

The findings are ordered by what they would cost if nobody noticed:

```
    CRITICAL   the evaluation could not be defended at all
    HIGH       the evaluation is missing evidence it claims to have
    MEDIUM     the evaluation is internally inconsistent
    LOW        worth seeing, not necessarily wrong
```
"""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

SEVERITY = {'critical': 3, 'high': 2, 'medium': 1, 'low': 0}

#: The audit reads every evaluation in a company through `sudo()` and names the
#: records it finds. That makes it a confidentiality surface as well as a
#: diagnostic: a finding says which bid, which vendor and which round. It is
#: therefore for the people who answer for the evaluation, not for anybody the
#: registry happens to let call a method.
AUDIT_GROUPS = (
    'atmta_roles.group_procurement_evaluation_manager',
    'atmta_roles.group_procurement_manager',
)


class EvaluationIntegrityAudit(models.AbstractModel):
    _name = 'realestate.procurement.evaluation.audit'
    _description = 'Evaluation Integrity Audit'

    @api.model
    def run(self, company=None, round_=None):
        """Findings across a company's evaluations, or one round."""
        self._assert_may_audit()
        company = company or self.env.company
        domain = [('company_id', '=', company.id)]
        if round_:
            domain = [('id', '=', round_.id)]
        rounds = self.env[
            'realestate.procurement.evaluation.round'].sudo().search(domain)
        findings = []
        for check in self._audit_checks():
            findings.extend(check(rounds))
        findings.sort(key=lambda f: -SEVERITY.get(f['severity'], 0))
        return {
            'company_id': company.id,
            'as_of': fields.Datetime.now(),
            'round_ids': rounds.ids,
            'findings': findings,
            'counts': {level: len([f for f in findings
                                   if f['severity'] == level])
                       for level in SEVERITY},
        }

    @api.model
    def _audit_checks(self):
        """The checks this audit runs, in order.

        One wizard asks one question — is anything in this file indefensible —
        and the answer spans more than evaluation. Award contributes its own
        checks, and so does the receiving chain, by extending this list rather
        than by this module reaching upward into capabilities it sits beneath.
        Order is preserved: evaluation first, then whatever the upper
        capabilities append.
        """
        return [
            self._check_plan_weights,
            self._check_frozen_plan_editable,
            self._check_scored_against_unfrozen_plan,
            self._check_duplicate_candidate,
            self._check_evaluator_without_declaration,
            self._check_unresolved_conflict_scored,
            self._check_knockout_marked_responsive,
            self._check_commercial_before_technical,
            self._check_failed_bid_ranked,
            self._check_missing_fx_snapshot,
            self._check_adjustment_without_rationale,
            self._check_analysis_diverges_from_bid,
            self._check_duplicate_rank_one,
            self._check_unresolved_tie_ranked,
            self._check_bafo_overwrote_initial,
            self._check_cross_company,
            self._check_submitted_sheet_edited,
            self._check_finalised_round_edited,
            self._check_commercial_fields_unrestricted,
        ]

    @api.model
    def _assert_may_audit(self):
        if self.env.su:
            return True
        if any(self.env.user.has_group(xmlid) for xmlid in AUDIT_GROUPS):
            return True
        raise AccessError(_(
            "The evaluation integrity audit reports on every evaluation in "
            "the company and names the bids and vendors involved. Running it "
            "is an Evaluation Manager's or Procurement Manager's action."))

    # ------------------------------------------------------------------
    def _finding(self, key, severity, summary, records, remediation):
        return {
            'key': key,
            'severity': severity,
            'summary': summary,
            'count': len(records),
            'record_ids': records.ids,
            'references': [r.display_name for r in records][:20],
            'remediation': remediation,
        }

    def _check_plan_weights(self, rounds):
        bad = rounds.plan_id.filtered(
            lambda p: p.criterion_ids.filtered(
                lambda c: c.criterion_type == 'rated')
            and abs(p.rated_weight_total - 100.0) > 0.001)
        if not bad:
            return []
        return [self._finding(
            'plan_weights_not_100', 'critical',
            _("A plan's rated criteria do not come to 100%, so its scores "
              "mean something nobody declared."),
            bad, _("Raise a plan revision with corrected weights. Do not "
                   "rescale silently."))]

    def _check_frozen_plan_editable(self, rounds):
        """A criterion touched after its plan was frozen.

        Detected from the data rather than by inspecting the guard: `write_date`
        moving after `frozen_on` means something changed the basis after it was
        settled, whether through the engine, a data fix or SQL. Asserting that
        the guard *exists* would be a check that cannot fail, which is no check
        at all.
        """
        bad = self.env[
            'realestate.procurement.evaluation.criterion'].sudo()
        for plan in rounds.plan_id.filtered('frozen_on'):
            # One second of tolerance, and it is not slack. `frozen_on` comes
            # from `fields.Datetime.now()`, which truncates microseconds,
            # while `write_date` keeps them — so a criterion written in the
            # same second as the freeze reads as 0.12s *after* it and would be
            # reported every single time. Below a second the two timestamps
            # cannot order themselves, so the check does not pretend to.
            cutoff = fields.Datetime.add(plan.frozen_on, seconds=1)
            bad |= plan.criterion_ids.filtered(
                lambda c, f=cutoff: c.write_date and c.write_date > f)
        if not bad:
            return []
        return [self._finding(
            'criterion_changed_after_freeze', 'critical',
            _("A criterion was modified after its evaluation basis was "
              "frozen."),
            bad, _("Establish what changed and when. Offers were scored "
                   "against the basis as it stood at freeze."))]

    def _check_scored_against_unfrozen_plan(self, rounds):
        bad = rounds.filtered(
            lambda r: r.state != 'draft'
            and r.plan_id.state not in ('frozen', 'in_use', 'superseded'))
        if not bad:
            return []
        return [self._finding(
            'scored_against_unfrozen_plan', 'critical',
            _("Scoring took place against a basis that was never frozen."),
            bad, _("The basis must be settled before evaluators see the "
                   "offers. This evaluation cannot be defended."))]

    def _check_duplicate_candidate(self, rounds):
        bad = rounds.filtered(lambda r: len(
            r.candidate_ids.mapped('bid_response_id')) != len(r.candidate_ids))
        if not bad:
            return []
        return [self._finding(
            'duplicate_candidate', 'critical',
            _("A bid appears twice in one evaluation round."), bad,
            _("The unique index should prevent this; a hit means it is "
              "missing."))]

    def _check_evaluator_without_declaration(self, rounds):
        bad = rounds.assignment_ids.filtered(
            lambda a: a.role in ('technical_evaluator', 'technical_lead',
                                 'commercial_evaluator', 'commercial_lead')
            and a.conflict_status == 'undeclared')
        if not bad:
            return []
        return [self._finding(
            'evaluator_without_declaration', 'high',
            _("A committee member has not declared whether they have a "
              "conflict of interest."),
            bad, _("Record the declaration. An evaluation whose evaluators "
                   "never declared is challengeable on that ground alone."))]

    def _check_unresolved_conflict_scored(self, rounds):
        bad = self.env['realestate.procurement.technical.evaluation'].sudo()
        for round_ in rounds:
            blocked = round_.assignment_ids.filtered(
                lambda a: a.conflict_status == 'declared').user_id
            if blocked:
                bad |= round_.sheet_ids.filtered(
                    lambda s: s.evaluator_id in blocked
                    and s.state != 'draft')
        if not bad:
            return []
        return [self._finding(
            'unresolved_conflict_scored', 'critical',
            _("A submitted score belongs to an evaluator with an unresolved "
              "declared conflict."),
            bad, _("Clear or uphold the conflict. Do not leave the score "
                   "standing while the question is open."))]

    def _check_knockout_marked_responsive(self, rounds):
        bad = rounds.candidate_ids.filtered(
            lambda c: c.technical_result == 'responsive'
            and c.sheet_ids.filtered(
                lambda s: s.state != 'draft' and s.knockout_failed))
        if not bad:
            return []
        return [self._finding(
            'knockout_marked_responsive', 'critical',
            _("A bid that failed a mandatory criterion is recorded as "
              "technically responsive."),
            bad, _("A knockout failure is not rescuable. Re-consolidate the "
                   "round."))]

    def _check_commercial_before_technical(self, rounds):
        bad = rounds.filtered(
            lambda r: r.commercial_opened_on and (
                not r.technical_finalised_on
                or r.commercial_opened_on < r.technical_finalised_on))
        if not bad:
            return []
        return [self._finding(
            'commercial_before_technical', 'critical',
            _("Prices were opened before the technical result was final."),
            bad, _("The staging exists so a technical opinion cannot follow a "
                   "number. This round's technical result is compromised."))]

    def _check_failed_bid_ranked(self, rounds):
        bad = rounds.candidate_ids.filtered(
            lambda c: c.rank and c.technical_result != 'responsive')
        if not bad:
            return []
        return [self._finding(
            'failed_bid_ranked', 'critical',
            _("A technically non-responsive bid holds a competitive rank."),
            bad, _("Only responsive offers are ranked."))]

    def _check_missing_fx_snapshot(self, rounds):
        bad = rounds.candidate_ids.analysis_id.filtered(
            lambda a: a.raw_currency_id
            and a.raw_currency_id != a.evaluation_currency_id
            and (not a.rate_used or not a.rate_date))
        if not bad:
            return []
        return [self._finding(
            'missing_fx_snapshot', 'critical',
            _("A converted offer carries no frozen rate or rate date, so its "
              "evaluated value cannot be reproduced."),
            bad, _("Re-normalise before finalising, or the ranking depends on "
                   "whatever the rate is on the day somebody asks."))]

    def _check_adjustment_without_rationale(self, rounds):
        bad = rounds.candidate_ids.analysis_id.adjustment_ids.filtered(
            lambda a: not a.rationale)
        if not bad:
            return []
        return [self._finding(
            'adjustment_without_rationale', 'high',
            _("A commercial adjustment moves an evaluated cost with no "
              "recorded reason."),
            bad, _("Record why, or remove it."))]

    def _check_analysis_diverges_from_bid(self, rounds):
        """Expected after adjustments — reported so it is visible, not wrong."""
        bad = rounds.candidate_ids.analysis_id.filtered(
            lambda a: a.raw_amount and a.evaluated_cost
            and abs(a.evaluated_cost - a.raw_amount) > 0.01
            and not a.adjustment_ids
            and a.raw_currency_id == a.evaluation_currency_id)
        if not bad:
            return []
        return [self._finding(
            'evaluated_cost_unexplained', 'high',
            _("An evaluated cost differs from the submitted bid with no "
              "adjustment and no currency conversion to explain it."),
            bad, _("Every difference between what was offered and what it is "
                   "evaluated at must be itemised."))]

    def _check_duplicate_rank_one(self, rounds):
        bad = rounds.filtered(
            lambda r: len(r.candidate_ids.filtered(lambda c: c.rank == 1)) > 1
            and not r.candidate_ids.filtered(
                lambda c: c.rank == 1 and c.is_tied))
        if not bad:
            return []
        return [self._finding(
            'duplicate_rank_one', 'critical',
            _("Two offers hold rank 1 and neither is marked as tied."),
            bad, _("Either the tie is real and must be flagged, or the "
                   "ranking is wrong."))]

    def _check_unresolved_tie_ranked(self, rounds):
        bad = rounds.filtered(
            lambda r: r.state == 'finalised'
            and r.candidate_ids.filtered(lambda c: c.rank == 1 and c.is_tied)
            and r.result_status != 'tie_requires_decision')
        if not bad:
            return []
        return [self._finding(
            'tie_not_surfaced', 'high',
            _("A tie at rank 1 is not reported as requiring a decision."),
            bad, _("A tie is an outcome, not a winner."))]

    def _check_bafo_overwrote_initial(self, rounds):
        bad = self.env['realestate.procurement.bid.response'].sudo()
        for round_ in rounds.filtered(lambda r: r.round_type == 'bafo'):
            bad |= round_.candidate_ids.bid_response_id.filtered(
                lambda b: b.revision == 0 and b.state == 'received'
                and b.superseded_by_id)
        if not bad:
            return []
        return [self._finding(
            'bafo_overwrote_initial', 'critical',
            _("An initial offer was rewritten by a best-and-final round."),
            bad, _("A BAFO is a new revision. The first offer stays."))]

    def _check_cross_company(self, rounds):
        bad = rounds.filtered(
            lambda r: r.plan_id.company_id
            and r.plan_id.company_id != r.company_id)
        if not bad:
            return []
        return [self._finding(
            'cross_company_evaluation', 'critical',
            _("An evaluation round uses a plan from another company."),
            bad, _("Correct the company or the plan."))]

    def _check_submitted_sheet_edited(self, rounds):
        """A submitted evaluation that moved after it was submitted.

        `write()` refuses this, and that guard is tested. Detected from the
        data anyway, for the same reason `_check_frozen_plan_editable` is: a
        guard can be bypassed through the engine context, a data fix or plain
        SQL, and asserting that the guard *exists* would be a check that
        cannot fail.
        """
        bad = self.env['realestate.procurement.technical.evaluation'].sudo()
        for round_ in rounds:
            for sheet in round_.sheet_ids.filtered(
                    lambda s: s.state in ('submitted', 'consolidated')
                    and s.submitted_on):
                # One second of tolerance. `submitted_on` comes from
                # `fields.Datetime.now()`, which truncates microseconds, while
                # `write_date` keeps them.
                cutoff = fields.Datetime.add(sheet.submitted_on, seconds=1)
                if sheet.write_date and sheet.write_date > cutoff:
                    bad |= sheet
        if not bad:
            return []
        return [self._finding(
            'submitted_sheet_edited', 'critical',
            _("A submitted evaluation sheet was modified after submission."),
            bad, _("Establish what changed. A submitted sheet is the "
                   "evaluator's opinion on the record; a correction is a "
                   "reopen with a reason, not an edit."))]

    def _check_finalised_round_edited(self, rounds):
        """Evaluation records that moved after the ranking was struck."""
        bad = self.env[
            'realestate.procurement.evaluation.candidate'].sudo()
        for round_ in rounds.filtered(
                lambda r: r.state == 'finalised' and r.finalised_on):
            cutoff = fields.Datetime.add(round_.finalised_on, seconds=1)
            bad |= round_.candidate_ids.filtered(
                lambda c, f=cutoff: c.write_date and c.write_date > f)
        if not bad:
            return []
        return [self._finding(
            'finalised_round_edited', 'critical',
            _("A candidate in a finalised evaluation was modified after "
              "finalisation."),
            bad, _("The ranking was published from these records. Establish "
                   "what changed and when."))]

    def _check_commercial_fields_unrestricted(self, rounds):
        """Structural. The commercial outcome must stay restricted.

        A read-only audit found these fields readable by a Technical
        Evaluator, which disclosed the whole ranking without showing a price.
        This check exists so that removing a `groups=` fails the build rather
        than quietly reopening it, in the same way `_check_award_surface`
        guards the award boundary.
        """
        Candidate = self.env['realestate.procurement.evaluation.candidate']
        exposed = [name for name in
                   ('financial_score', 'combined_score', 'rank', 'is_tied',
                    'analysis_id')
                   if name in Candidate._fields
                   and not Candidate._fields[name].groups]
        if 'rank' in (Candidate._order or ''):
            exposed.append('_order sorts by rank')
        if not exposed:
            return []
        return [self._finding(
            'commercial_outcome_unrestricted', 'critical',
            _("The commercial outcome is not restricted: %s. A technical "
              "evaluator can read the ranking.") % ', '.join(exposed),
            Candidate.browse(),
            _("Restore the field groups, and keep `rank` out of `_order` — "
              "ordering by it hands over the ranking with nothing read."))]
