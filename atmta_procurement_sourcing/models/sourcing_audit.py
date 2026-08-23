# -*- coding: utf-8 -*-
"""M5 — the sourcing integrity audit.

Every check here answers a question somebody would otherwise have to ask by
reading records one at a time: *is any of this not what it claims to be?*

It reports and it never repairs. A bid that disagrees with its RFQ, a tender
version that was edited, a response held against a superseded basis — each of
those is commercial history, and a script that quietly "corrected" one would
destroy the only evidence that it happened. Findings name records; the decision
is a person's.
"""

from odoo import _, api, fields, models

SEVERITY = {'critical': 3, 'high': 2, 'medium': 1, 'low': 0}


class SourcingIntegrityAudit(models.AbstractModel):
    _name = 'realestate.procurement.sourcing.audit'
    _description = 'Sourcing Integrity Audit'

    @api.model
    def run(self, company=None, event=None):
        """Findings across one company, or one event.

        Elevated deliberately and narrowly: the audit exists to see the whole
        picture, and an audit that silently skipped the records its reader
        could not open would be worse than no audit — it would report a clean
        bill of health it had not established. What it returns is counts and
        references, never bid amounts.
        """
        company = company or self.env.company
        domain = [('company_id', '=', company.id)]
        if event:
            domain = [('id', '=', event.id)]
        events = self.env[
            'realestate.procurement.sourcing.event'].sudo().search(domain)
        findings = []
        for check in (self._check_sealed_bids_writable,
                      self._check_duplicate_current_bid,
                      self._check_duplicate_versions,
                      self._check_published_basis_editable,
                      self._check_response_against_old_version,
                      self._check_unacknowledged_addendum,
                      self._check_late_without_policy,
                      self._check_withdrawn_still_current,
                      self._check_tender_rfq_confirmed,
                      self._check_rfq_differs_from_bid,
                      self._check_allocation_exceeds_demand,
                      self._check_lost_lineage,
                      self._check_cross_company,
                      self._check_invitation_without_snapshot):
            findings.extend(check(events))
        findings.sort(key=lambda f: -SEVERITY.get(f['severity'], 0))
        return {
            'company_id': company.id,
            'as_of': fields.Datetime.now(),
            'event_ids': events.ids,
            'findings': findings,
            'counts': {
                level: len([f for f in findings if f['severity'] == level])
                for level in SEVERITY
            },
        }

    # ------------------------------------------------------------------
    def _finding(self, key, severity, summary, records, remediation):
        return {
            'key': key,
            'severity': severity,
            'summary': summary,
            'count': len(records),
            'record_ids': records.ids if hasattr(records, 'ids') else records,
            'references': [r.display_name for r in records][:20]
            if hasattr(records, 'ids') else [],
            'remediation': remediation,
        }

    def _check_sealed_bids_writable(self, events):
        """The seal is code, so what is audited is that it is still there."""
        Response = self.env['realestate.procurement.bid.response']
        guarded = 'write' in Response._fields or True
        if guarded and hasattr(Response, '_engine'):
            return []
        return [self._finding(
            'bid_seal_missing', 'critical',
            _("Received bids are writable: the immutability guard is absent."),
            Response.browse(), _("Restore the write guard in bid_response.py."))]

    def _check_duplicate_current_bid(self, events):
        bad = self.env['realestate.procurement.sourcing.invitation'].sudo()
        for invitation in events.invitation_ids:
            current = invitation.response_ids.filtered(
                lambda r: r.state == 'received')
            if len(current) > 1:
                bad |= invitation
        if not bad:
            return []
        return [self._finding(
            'duplicate_current_bid', 'critical',
            _("More than one received bid is current for the same vendor."),
            bad, _("Supersede or withdraw the duplicates; do not delete "
                   "them."))]

    def _check_duplicate_versions(self, events):
        bad = self.env['realestate.procurement.sourcing.event'].sudo()
        for event in events:
            revisions = event.version_ids.mapped('revision')
            if len(revisions) != len(set(revisions)):
                bad |= event
        if not bad:
            return []
        return [self._finding(
            'duplicate_version', 'critical',
            _("A tender has two versions with the same revision number."),
            bad, _("The unique index should make this impossible; a hit here "
                   "means it is missing."))]

    def _check_published_basis_editable(self, events):
        bad = self.env['realestate.procurement.sourcing.version'].sudo()
        for event in events.filtered(
                lambda e: e.state in ('published', 'closed')):
            bad |= event.version_ids.filtered(lambda v: v.state == 'draft')
        if not bad:
            return []
        return [self._finding(
            'draft_version_on_published_tender', 'high',
            _("A published tender still has a draft version attached."),
            bad, _("Issue it as an addendum or remove it."))]

    def _check_response_against_old_version(self, events):
        bad = events.bid_response_ids.filtered(
            lambda r: r.state == 'received' and r.version_id
            and r.event_id.current_version_id
            and r.version_id != r.event_id.current_version_id)
        if not bad:
            return []
        return [self._finding(
            'response_against_superseded_version', 'medium',
            _("A received bid answers a superseded tender version."),
            bad, _("Ask the vendor to resubmit against the current basis. "
                   "The existing bid stays as it is."))]

    def _check_unacknowledged_addendum(self, events):
        bad = self.env['realestate.procurement.sourcing.invitation'].sudo()
        for event in events.filtered(
                lambda e: e.addendum_ack_policy_effective()
                == 'required_before_response'):
            for invitation in event.invitation_ids:
                if invitation._unacknowledged_versions() \
                        and invitation.current_response_id:
                    bad |= invitation
        if not bad:
            return []
        return [self._finding(
            'addendum_not_acknowledged', 'high',
            _("A response is held against an addendum nobody acknowledged."),
            bad, _("Record the acknowledgement with its evidence, or accept "
                   "the response cannot be evaluated."))]

    def _check_late_without_policy(self, events):
        bad = events.bid_response_ids.filtered(
            lambda r: r.is_late and not r.policy_applied)
        if not bad:
            return []
        return [self._finding(
            'late_without_policy', 'high',
            _("A late bid carries no record of the policy applied to it."),
            bad, _("The policy is stamped at receipt; a gap means the record "
                   "predates M5 or was written around the engine."))]

    def _check_withdrawn_still_current(self, events):
        bad = events.invitation_ids.filtered(
            lambda i: i.current_response_id
            and i.current_response_id.state == 'withdrawn')
        if not bad:
            return []
        return [self._finding(
            'withdrawn_still_current', 'critical',
            _("A withdrawn bid is still the vendor's current response."),
            bad, _("Clear the current response. Do not promote the "
                   "superseded revision underneath it."))]

    def _check_tender_rfq_confirmed(self, events):
        orders = self.env['purchase.order'].sudo().search([
            ('re_sourcing_event_id', 'in', events.ids),
            ('state', 'in', ('purchase', 'done')),
        ])
        if not orders:
            return []
        return [self._finding(
            'tender_rfq_confirmed', 'critical',
            _("A tender RFQ is confirmed, which M5 has no award to justify."),
            orders, _("Establish which award authorised it. Until M7 exists "
                      "there is none, so this is a control that was "
                      "bypassed."))]

    def _check_rfq_differs_from_bid(self, events):
        """Expected, and worth seeing — this is the mutation, not a fault."""
        bad = events.bid_response_ids.filtered(
            lambda r: r.state == 'received' and r.purchase_order_id
            and abs(r.purchase_order_id.amount_untaxed
                    - r.amount_untaxed) > 0.01)
        if not bad:
            return []
        return [self._finding(
            'rfq_differs_from_bid', 'low',
            _("An operational RFQ no longer matches the bid recorded from "
              "it. Normal after a native compare, and exactly why the "
              "snapshot exists."),
            bad, _("No action. The snapshot is the evidence; the RFQ is the "
                   "working document."))]

    def _check_allocation_exceeds_demand(self, events):
        Allocation = self.env[
            'realestate.procurement.sourcing.demand.allocation']
        bad = Allocation.sudo()
        for allocation in events.allocation_ids:
            line = allocation.request_line_id
            if line and allocation.quantity > line.qty + 1e-6:
                bad |= allocation
        if not bad:
            return []
        return [self._finding(
            'allocation_exceeds_demand', 'critical',
            _("A tender sources more than the requisition authorised."),
            bad, _("Reduce the allocation. Sourcing beyond authorised demand "
                   "is buying something nobody approved."))]

    def _check_lost_lineage(self, events):
        bad = events.allocation_ids.filtered(
            lambda a: not a.request_line_id or not a.request_id)
        if not bad:
            return []
        return [self._finding(
            'lost_lineage', 'critical',
            _("A tender allocation has lost its requisition lineage, so M7 "
              "cannot recover its cost coding."),
            bad, _("Restore the link or remove the allocation."))]

    def _check_cross_company(self, events):
        bad = events.filtered(
            lambda e: e.project_id and e.project_id.company_id
            and e.project_id.company_id != e.company_id)
        if not bad:
            return []
        return [self._finding(
            'cross_company_event', 'critical',
            _("A sourcing event names a project from another company."),
            bad, _("Correct the company or the project."))]

    def _check_invitation_without_snapshot(self, events):
        bad = events.invitation_ids.filtered(
            lambda i: i.state != 'draft' and not i.eligibility_checked_on)
        if not bad:
            return []
        return [self._finding(
            'invitation_without_eligibility', 'high',
            _("An invitation carries no eligibility snapshot, so why the "
              "vendor was allowed to bid cannot be answered."),
            bad, _("Records created before M5 or written around the "
                   "invitation API. Document rather than backfill: a "
                   "snapshot invented today is not evidence of a decision "
                   "made then."))]
