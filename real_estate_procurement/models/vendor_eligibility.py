# -*- coding: utf-8 -*-
"""M4J / M4X — the one place that answers "may this vendor participate?".

Every screen, gate and future tender asks the same question here, for the same
reason M3 has exactly one availability service: two implementations of a
control eventually disagree, and then nobody can say which answer was the
company's.

```
    check_vendor_eligibility(vendor, company, trade, project, date, purpose)
        → { eligible, qualified, status, qualification, valid_from, valid_to,
            conditions, blocking_reasons, warnings, restriction_ids, policy }
```

A boolean would be useless. The question a buyer actually asks is *why wasn't
Vendor X invited*, and a service that answers True/False cannot be quoted back
at them.

### Eligible is not qualified

Two separate keys, deliberately:

```
    qualified   a current, valid, approved assessment says so
    eligible    company policy allows them to take part today
```

Under OPTIONAL policy an unassessed vendor is `eligible=True, qualified=False,
status='no_qualification'`. Nobody has decided they are good; the company has
decided not to require the decision yet. Collapsing those into one flag is how
"we allow it for now" turns into "the system says they're approved" — which is
what UNKNOWN IS NOT ELIGIBLE is protecting against.

### As-of date

Never implicitly today (M4X). Sourcing invitation, award and confirmation
happen on different dates and the answer legitimately differs between them, so
the date is an argument. `date=None` means today, and that is a default, not
an assumption baked into the arithmetic.

### Order of precedence (M4M)

```
    1  governance status      suspended / inactive vendor      → blocks
    2  restrictions in force  scoped by trade and project      → blocks
    3  general qualification  company + trade                  → policy decides
    4  project endorsement    where the project demands one    → policy decides
    5  document expiry        mandatory, expiry-sensitive      → policy decides
```

A project endorsement can never rescue a vendor from a company-level
suspension: 1 and 2 run first and return before 3 is reached.
"""

from odoo import _, api, fields, models

#: Every status this service can return. Named so a screen can print them.
ST_ELIGIBLE = 'eligible'
ST_CONDITIONS = 'eligible_with_conditions'
ST_NO_QUALIFICATION = 'no_qualification'
ST_NOT_QUALIFIED = 'not_qualified'
ST_PENDING = 'pending_information'
ST_EXPIRED = 'expired'
ST_DOCUMENT_EXPIRED = 'document_expired'
ST_ENDORSEMENT = 'endorsement_required'
ST_SUSPENDED = 'suspended'
ST_INACTIVE = 'governance_inactive'

STATUS_LABELS = {
    ST_ELIGIBLE: 'Eligible',
    ST_CONDITIONS: 'Eligible With Conditions',
    ST_NO_QUALIFICATION: 'No Qualification',
    ST_NOT_QUALIFIED: 'Not Qualified',
    ST_PENDING: 'Pending Information',
    ST_EXPIRED: 'Qualification Expired',
    ST_DOCUMENT_EXPIRED: 'Mandatory Document Expired',
    ST_ENDORSEMENT: 'Project Endorsement Required',
    ST_SUSPENDED: 'Suspended',
    ST_INACTIVE: 'Vendor Inactive',
}

#: Policies under which the absence of a valid qualification refuses.
REQUIRES = {
    'sourcing': ('required_for_sourcing',),
    'award': ('required_for_sourcing', 'required_for_award'),
}

VALID_RESULTS = ('qualified', 'qualified_with_conditions')


class VendorEligibility(models.AbstractModel):
    _name = 'realestate.procurement.vendor.eligibility'
    _description = 'Vendor Sourcing Eligibility'

    # ------------------------------------------------------------------
    # Policy — M4K
    # ------------------------------------------------------------------
    @api.model
    def vendor_policy_for(self, project=None, company=None):
        """Company default, project override. The same chain M3 uses."""
        company = company or (project.company_id if project else False) \
            or self.env.company
        policy = project.procurement_vendor_policy if project else 'company'
        if policy and policy != 'company':
            return policy
        return company.procurement_vendor_policy or 'optional'

    # ------------------------------------------------------------------
    # The service — M4J
    # ------------------------------------------------------------------
    @api.model
    def check_vendor_eligibility(self, vendor, company=None, category=None,
                                 project=None, date=None, purpose='sourcing'):
        """Structured answer, never a bare boolean. See the module docstring."""
        company = company or (project.company_id if project else False) \
            or self.env.company
        date = date or fields.Date.context_today(self)
        if isinstance(date, str):
            date = fields.Date.to_date(date)
        policy = self.vendor_policy_for(project, company)
        enforcing = policy in REQUIRES.get(purpose, ())

        result = {
            'partner_id': vendor.id if vendor else False,
            'partner_name': vendor.display_name if vendor else '',
            'company_id': company.id,
            'category_id': category.id if category else False,
            'category_name': category.complete_name if category else '',
            'project_id': project.id if project else False,
            'date': date,
            'purpose': purpose,
            'policy': policy,
            'enforcing': enforcing,
            'eligible': True,
            'qualified': False,
            'status': ST_NO_QUALIFICATION,
            'qualification_id': False,
            'qualification_ref': '',
            'result': False,
            'score': 0.0,
            'valid_from': False,
            'valid_to': False,
            'conditions': [],
            'blocking_reasons': [],
            'warnings': [],
            'restriction_ids': [],
            'endorsement_id': False,
        }
        if not vendor:
            result.update(eligible=False, status=ST_INACTIVE)
            result['blocking_reasons'].append(_("No vendor was named."))
            return result

        # -- 1. governance status ------------------------------------
        profile = self.env['realestate.procurement.vendor.profile'].sudo(
        ).search([('partner_id', '=', vendor.id),
                  ('company_id', '=', company.id)], limit=1)
        if profile.governance_status == 'inactive':
            result.update(eligible=False, status=ST_INACTIVE)
            result['blocking_reasons'].append(_(
                "%s is marked inactive with %s.",
                vendor.display_name, company.display_name))
            return result

        # -- 2. restrictions ------------------------------------------
        blocking_restrictions, noted = self._restrictions(
            vendor, company, category, project, date, purpose)
        result['restriction_ids'] = (blocking_restrictions + noted)
        # Elevated to build the *explanation*, for the same reason the search
        # above is: whoever asked may hold no procurement rights at all, and a
        # refusal that turned into an access error would be strictly worse
        # than either answer. What comes back is a sentence, never a record.
        Restriction = self.env[
            'realestate.procurement.vendor.restriction'].sudo()
        for restriction in Restriction.browse(noted):
            result['warnings'].append(_(
                "%(type)s recorded: %(reason)s",
                type=dict(restriction._fields['restriction_type'].selection)[
                    restriction.restriction_type],
                reason=restriction.reason or ''))

        # -- 3. the general qualification -----------------------------
        qualification = self._effective_qualification(
            vendor, company, category, date, project=None)
        if qualification:
            result.update({
                'qualification_id': qualification.id,
                'qualification_ref': qualification.name,
                'result': qualification.result,
                'score': qualification.score,
                'valid_from': qualification.effective_date,
                'valid_to': qualification.expiry_date,
                'qualified': qualification.result in VALID_RESULTS,
                'conditions': [c._as_dict()
                               for c in qualification.condition_ids],
            })

        if blocking_restrictions:
            # After the qualification is filled in, so the screen can show
            # both: "valid assessment, currently suspended" is the truth and
            # hiding half of it helps nobody.
            result.update(eligible=False, status=ST_SUSPENDED)
            for restriction in Restriction.browse(blocking_restrictions):
                result['blocking_reasons'].append(_(
                    "%(name)s: %(type)s in force since %(date)s — %(reason)s",
                    name=restriction.name,
                    type=dict(restriction._fields[
                        'restriction_type'].selection)[
                            restriction.restriction_type],
                    date=restriction.effective_from,
                    reason=restriction.reason or ''))
            return result

        status = self._qualification_status(
            vendor, company, category, date, qualification)
        result['status'] = status

        if status in (ST_ELIGIBLE, ST_CONDITIONS):
            # -- 4. project endorsement -------------------------------
            endorsement_status = self._endorsement_status(
                vendor, company, category, project, date, result)
            if endorsement_status:
                result['status'] = status = endorsement_status
            # -- 5. mandatory documents -------------------------------
            elif qualification and self._expired_documents(
                    qualification, date):
                result['status'] = status = ST_DOCUMENT_EXPIRED

        if status not in (ST_ELIGIBLE, ST_CONDITIONS):
            message = self._explain(status, vendor, category, qualification)
            if enforcing:
                result['eligible'] = False
                result['blocking_reasons'].append(message)
            else:
                result['warnings'].append(message)
                if policy == 'warn':
                    result['warnings'].append(_(
                        "Policy is Warn, so this is recorded and sourcing "
                        "continues."))
        return result

    # ------------------------------------------------------------------
    @api.model
    def _restrictions(self, vendor, company, category, project, date, purpose):
        """`(blocking ids, noted ids)` — in force on `date`, in this scope."""
        Restriction = self.env['realestate.procurement.vendor.restriction']
        candidates = Restriction.sudo().search([
            ('partner_id', '=', vendor.id),
            ('company_id', '=', company.id),
            ('state', 'in', ('active', 'expired', 'lifted')),
            ('effective_from', '<=', date),
        ])
        blocking, noted = [], []
        for restriction in candidates:
            if not restriction._applies_on(date):
                continue
            if not restriction._covers(category=category, project=project):
                continue
            if restriction._blocks(purpose):
                blocking.append(restriction.id)
            else:
                noted.append(restriction.id)
        return blocking, noted

    @api.model
    def _effective_qualification(self, vendor, company, category, date,
                                 project=None):
        """The assessment that governed this scope on `date`.

        Expired and superseded records are candidates: that is Rule 6. What
        rules them in or out is the dates on them, plus `approved_date` — a
        decision taken in June never made anybody eligible in March, however
        early its effective date was typed.
        """
        Qualification = self.env[
            'realestate.procurement.vendor.qualification']
        domain = [
            ('partner_id', '=', vendor.id),
            ('state', 'in', ('approved', 'expired', 'superseded')),
            ('approved_date', '!=', False),
            ('approved_date', '<=', date),
            ('effective_date', '<=', date),
            ('project_id', '=', project.id if project else False),
        ]
        if category:
            domain.append(('category_id', '=', category.id))
        domain += self._company_domain(company)
        candidates = Qualification.sudo().search(
            domain, order='effective_date desc, approved_date desc, id desc')
        # Inside validity first; the newest lapsed one otherwise, so the
        # caller can be told *what* expired rather than being told nothing
        # exists.
        within = candidates.filtered(
            lambda q: not q.expiry_date or q.expiry_date >= date)
        # Returned elevated, deliberately. The caller may be a Purchase
        # Manager with no procurement rights, and the service's whole job is
        # to hand them a decision — reference, result, validity, conditions.
        # None of that is the confidential material: the evidence lives on
        # the responses, which keep their own record rules and are not
        # reachable through anything returned here.
        return within[:1] or candidates[:1]

    @api.model
    def _company_domain(self, company):
        """This company's own decisions, plus any it explicitly recognises."""
        return ['|', ('company_id', '=', company.id),
                '&', ('is_group_wide', '=', True),
                ('shared_company_ids', 'in', company.id)]

    @api.model
    def _qualification_status(self, vendor, company, category, date,
                              qualification):
        if not qualification:
            return ST_NO_QUALIFICATION
        if qualification.expiry_date and qualification.expiry_date < date:
            return ST_EXPIRED
        if qualification.result == 'not_qualified':
            return ST_NOT_QUALIFIED
        if qualification.result == 'pending_information':
            return ST_PENDING
        if qualification.result == 'qualified_with_conditions' or \
                qualification.condition_ids:
            return ST_CONDITIONS
        return ST_ELIGIBLE

    @api.model
    def _endorsement_status(self, vendor, company, category, project, date,
                            result):
        """M4M — the extra approval some developments insist on.

        Only asked for where the project says so, and only ever narrowing:
        an endorsement cannot make a vendor eligible whose general
        qualification is missing, because this runs after that check.
        """
        if not project or not project.procurement_vendor_endorsement_required:
            return None
        endorsement = self._effective_qualification(
            vendor, company, category, date, project=project)
        if endorsement and endorsement.result in VALID_RESULTS and (
                not endorsement.expiry_date or endorsement.expiry_date >= date):
            result['endorsement_id'] = endorsement.id
            result['conditions'] += [c._as_dict()
                                     for c in endorsement.condition_ids]
            return None
        return ST_ENDORSEMENT

    @api.model
    def _expired_documents(self, qualification, date):
        """M4V — only requirements that were marked as expiring are re-checked.

        Every attachment having an expiry would make the register unusable
        within a month, and a company profile PDF does not go stale the way
        an insurance certificate does.
        """
        return bool(qualification.sudo().response_ids.filtered(
            lambda r: r.expiry_sensitive
            and r.obligation == 'mandatory'
            and r.document_expiry_date
            and r.document_expiry_date < date))

    @api.model
    def _explain(self, status, vendor, category, qualification):
        trade = category.complete_name if category else _('any trade')
        if status == ST_NO_QUALIFICATION:
            return _("%(vendor)s has no qualification for %(trade)s.",
                     vendor=vendor.display_name, trade=trade)
        if status == ST_EXPIRED:
            return _("%(vendor)s's qualification for %(trade)s expired on "
                     "%(date)s.", vendor=vendor.display_name, trade=trade,
                     date=qualification.expiry_date)
        if status == ST_NOT_QUALIFIED:
            return _("%(vendor)s was assessed for %(trade)s and not "
                     "qualified.", vendor=vendor.display_name, trade=trade)
        if status == ST_PENDING:
            return _("%(vendor)s's assessment for %(trade)s is waiting on "
                     "information.", vendor=vendor.display_name, trade=trade)
        if status == ST_ENDORSEMENT:
            return _("%s needs this project's own endorsement, which it does "
                     "not have.") % vendor.display_name
        if status == ST_DOCUMENT_EXPIRED:
            return _("A mandatory document behind %s's qualification has "
                     "expired.") % vendor.display_name
        return _("%s is not eligible.") % vendor.display_name

    # ------------------------------------------------------------------
    # Bulk — M4I / M4W / M4X
    # ------------------------------------------------------------------
    @api.model
    def get_eligible_vendors(self, company=None, category=None, project=None,
                             date=None, purpose='sourcing', partners=None,
                             include_ineligible=True):
        """The sourcing pool, with a reason against every name.

        `include_ineligible` defaults to True and that is the point of M4W:
        a screen that silently drops the suspended vendor cannot answer *why
        wasn't Vendor X invited*. Callers that need only the permitted ones
        filter on `eligible` themselves, in one obvious line.
        """
        company = company or (project.company_id if project else False) \
            or self.env.company
        candidates = partners if partners is not None else \
            self._candidate_partners(company, category)
        results = []
        for partner in candidates:
            outcome = self.check_vendor_eligibility(
                partner, company=company, category=category, project=project,
                date=date, purpose=purpose)
            if include_ineligible or outcome['eligible']:
                results.append(outcome)
        results.sort(key=lambda r: (not r['eligible'], not r['qualified'],
                                    r['partner_name']))
        return results

    @api.model
    def _candidate_partners(self, company, category):
        """Who is even in the conversation for this trade.

        Vendors with a current qualification for it, plus vendors the company
        already buys from — the second group is what makes the register
        useful on day one, before anybody has been assessed.

        M4AG: this is the unbounded end of the service and callers that know
        their pool should pass `partners` rather than let it search every
        supplier in the database.
        """
        Qualification = self.env[
            'realestate.procurement.vendor.qualification']
        domain = [('is_current', '=', True), ('state', '=', 'approved')]
        domain += self._company_domain(company)
        if category:
            domain.append(('category_id', '=', category.id))
        partners = Qualification.sudo().search(domain).mapped('partner_id')
        partners |= self.env['res.partner'].search([
            ('supplier_rank', '>', 0),
            ('active', '=', True),
            '|', ('company_id', '=', False),
            ('company_id', '=', company.id),
        ])
        return partners

    # ------------------------------------------------------------------
    @api.model
    def post_governance_note(self, record, body):
        """Record a warning without ever becoming the reason something failed.

        Found the hard way. Under WARN policy, confirming an order posts a
        note saying the vendor is unassessed — and Odoo refuses to post on
        behalf of a user with no email address, so the note's failure
        cancelled the confirmation. A control that warns must not be able to
        stop the thing it is only warning about; when the acting user cannot
        be an author, the system partner is, and the text still names who
        did it.
        """
        author = self.env.user.partner_id
        if not author.email:
            body = _("%(body)s\n(Recorded on behalf of %(user)s.)",
                     body=body, user=self.env.user.display_name)
            author = self.env.ref('base.partner_root')
        record.sudo().message_post(body=body, author_id=author.id)

    # ------------------------------------------------------------------
    # M4Y — what M5 will store on an invitation
    # ------------------------------------------------------------------
    @api.model
    def eligibility_snapshot(self, outcome):
        """A flat, immutable record of one answer, safe to keep forever.

        M4 does not store these — there is nothing yet to store them on. It
        produces them in the shape M5's tender invitation will keep, so that
        a tender opened in 2026 can still print *this vendor was eligible at
        invitation, under qualification Q-0007, valid to 2027-03-01* after
        every one of those facts has changed.
        """
        return {
            'partner_id': outcome['partner_id'],
            'partner_name': outcome['partner_name'],
            'company_id': outcome['company_id'],
            'category_id': outcome['category_id'],
            'category_name': outcome['category_name'],
            'project_id': outcome['project_id'],
            'as_of': fields.Date.to_string(outcome['date']),
            'purpose': outcome['purpose'],
            'policy': outcome['policy'],
            'eligible': outcome['eligible'],
            'qualified': outcome['qualified'],
            'status': outcome['status'],
            'status_label': STATUS_LABELS.get(outcome['status'], ''),
            'qualification_id': outcome['qualification_id'],
            'qualification_ref': outcome['qualification_ref'],
            'valid_from': fields.Date.to_string(outcome['valid_from'])
            if outcome['valid_from'] else False,
            'valid_to': fields.Date.to_string(outcome['valid_to'])
            if outcome['valid_to'] else False,
            'conditions': [
                {'type': c['type'], 'name': c['name'], 'amount': c['amount']}
                for c in outcome['conditions']],
            'blocking_reasons': list(outcome['blocking_reasons']),
            'restriction_ids': list(outcome['restriction_ids']),
        }
