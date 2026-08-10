# -*- coding: utf-8 -*-
"""Fixtures for Construction.

The module shipped with **no tests at all**, so there is no baseline to
preserve — there is a baseline to *establish*. Everything here builds the
smallest believable construction project: a company, a project with a budget,
a contractor with a vendor, a milestone, a work item and a BOQ.
"""

from odoo import fields
from odoo.tests.common import TransactionCase


class ConstructionCommon(TransactionCase):

    _seq = 0

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.currency = cls.company.currency_id
        # The fixture user approves budgets and awards packages, so it holds
        # the authority those actions require. Granting it here rather than
        # weakening the maker/checker rule keeps the rule real: a separate
        # test asserts that a preparer without this group is refused.
        cls.env.user.groups_id |= cls.env.ref(
            'real_estate_construction.group_construction_manager')
        # Fixtures raise and approve as the same user, which the maker/checker
        # rule refuses by design. The limit is lifted here so that routine
        # fixtures can build approved changes; `TestChangeGovernance` clears it
        # again and asserts the refusal, so the rule stays real.
        cls.env['ir.config_parameter'].sudo().set_param(
            'real_estate_construction.self_approval_limit', '1000000000')
        # M8 — the same arrangement for claims. Fixtures submit and determine
        # as the same user, which the maker/checker rule refuses by design;
        # `TestClaimGovernance` turns it back off and asserts the refusal, so
        # the rule stays real.
        cls.env['ir.config_parameter'].sudo().set_param(
            'real_estate_construction.allow_self_determination', 'True')
        cls.env.user.groups_id |= cls.env.ref(
            'real_estate_construction.group_construction_commercial')
        cls.today = fields.Date.context_today(cls.env['res.partner'])
        cls.Project = cls.env['realestate.project']
        cls.Milestone = cls.env['realestate.construction.milestone']
        cls.CostLine = cls.env['realestate.construction.cost.line']
        cls.BOQ = cls.env['realestate.boq']
        cls.BOQLine = cls.env['realestate.boq.line']
        cls.Certificate = cls.env['realestate.construction.payment.certificate']
        cls.CertLine = cls.env[
            'realestate.construction.payment.certificate.line']
        cls.Contractor = cls.env['realestate.contractor']
        cls.WorkItem = cls.env['realestate.work.item']
        cls.uom_unit = cls.env.ref('uom.product_uom_unit')

    def _next(self):
        type(self)._seq += 1
        return type(self)._seq

    def _project(self, budget=10_000_000.0, **kwargs):
        seq = self._next()
        vals = {
            'name': 'Construction Project %d' % seq,
            'code': 'CON%03d' % seq,
            'company_id': self.company.id,
            'expected_budget': budget,
        }
        vals.update(kwargs)
        return self.Project.create(vals)

    def _contractor(self, retention=5.0, **kwargs):
        seq = self._next()
        partner = self.env['res.partner'].create({
            'name': 'Contractor Vendor %d' % seq,
            'supplier_rank': 1,
        })
        vals = {'name': 'Contractor %d' % seq, 'partner_id': partner.id,
                'retention_pct': retention}
        vals.update(kwargs)
        return self.Contractor.create(vals)

    def _milestone(self, project, budget=1_000_000.0, weight=10.0, **kwargs):
        vals = {
            'name': 'Milestone %d' % self._next(),
            'project_id': project.id,
            'budget_amount': budget,
            'weight': weight,
        }
        vals.update(kwargs)
        return self.Milestone.create(vals)

    def _wbs(self, project, code='01', name='Node', **kwargs):
        vals = {'project_id': project.id, 'code': code, 'name': name}
        vals.update(kwargs)
        return self.env['realestate.construction.wbs'].create(vals)

    def _cost_code(self, code, name, category='other', **kwargs):
        vals = {'code': code, 'name': name, 'category': category}
        vals.update(kwargs)
        return self.env['realestate.construction.cost.code'].create(vals)

    def _analytic_line(self, project, cost_code=None, amount=0.0, date=None):
        """One analytic posting, shaped the way the ledger shapes them.

        Expenditure is **negative** on an analytic line — that is Odoo's sign
        convention, not ours, and writing the fixture the other way round would
        make every assertion about actual cost meaningless.
        """
        Analytic = self.env['realestate.construction.analytic']
        distribution = Analytic.distribution_for(project, cost_code)
        vals = {
            'name': 'Posting %d' % self._next(),
            'amount': -abs(amount),
            'date': date or self.today,
            'company_id': project.company_id.id or self.company.id,
        }
        # One comma-joined key, one line, both plan columns — the same shape
        # Odoo writes when it posts a bill.
        for key in (distribution or {}):
            for account_id in key.split(','):
                account = self.env['account.analytic.account'].browse(
                    int(account_id))
                vals[account.plan_id._column_name()] = account.id
        return self.env['account.analytic.line'].create(vals)

    def _budget(self, project, amount=1_000_000.0, code=None, **kwargs):
        code = code or self._cost_code(
            'BUD%03d' % self._next(), 'Budget code', 'subcontract')
        vals = {
            'project_id': project.id,
            'line_ids': [(0, 0, {
                'cost_code_id': code.id,
                'description': 'Works',
                'amount_mode': 'lumpsum',
                'original_amount': amount,
            })],
        }
        vals.update(kwargs)
        return self.env['realestate.construction.budget'].create(vals)

    def _baselined(self, project, amount=1_000_000.0, code=None):
        budget = self._budget(project, amount, code=code)
        budget.action_submit()
        budget.action_approve()
        budget.action_baseline()
        return budget

    def _package(self, project, contractor=None, value=0.0, award=False,
                 **kwargs):
        vals = {
            'title': 'Package %d' % self._next(),
            'project_id': project.id,
            'contractor_id': (contractor or self._contractor()).id,
            'tender_value': value,
        }
        vals.update(kwargs)
        package = self.env[
            'realestate.construction.contract.package'].create(vals)
        if award:
            package.action_award()
        return package

    def _po(self, project, contractor, lines, confirm=True, package=None,
            tax=None):
        """A purchase order coded to cost codes, the way M2 expects them.

        `lines` is a list of `(cost_code, untaxed_amount)`.
        """
        product = contractor._ensure_service_product()
        order_lines = []
        for cost_code, amount in lines:
            order_lines.append((0, 0, {
                'product_id': product.id,
                'name': cost_code.display_name if cost_code else 'Uncoded',
                'product_qty': 1.0,
                'price_unit': amount,
                'taxes_id': [(6, 0, tax.ids)] if tax else [(5, 0, 0)],
                're_cost_code_id': cost_code.id if cost_code else False,
            }))
        po = self.env['purchase.order'].create({
            'partner_id': contractor.partner_id.id,
            're_project_id': project.id,
            're_package_id': package.id if package else False,
            'order_line': order_lines,
        })
        if confirm:
            po.button_confirm()
        return po

    def _vat(self, percent=15.0):
        return self.env['account.tax'].create({
            'name': 'VAT %s%%' % percent,
            'amount': percent,
            'amount_type': 'percent',
            'type_tax_use': 'purchase',
            'company_id': self.company.id,
        })

    def _post_bill(self, project, cost_code, amount, tax=None,
                   contractor=None, date=None):
        contractor = contractor or self._contractor()
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice',
            'partner_id': contractor.partner_id.id,
            'invoice_date': date or self.today,
            'date': date or self.today,
            'company_id': self.company.id,
            'invoice_line_ids': [(0, 0, {
                'name': 'Posted cost',
                'quantity': 1,
                'price_unit': amount,
                'tax_ids': [(6, 0, tax.ids)] if tax else [(5, 0, 0)],
                'analytic_distribution': self.env[
                    'realestate.construction.analytic'].distribution_for(
                        project, cost_code),
            })],
        })
        bill.action_post()
        return bill

    def _attachment(self, name='drawing.pdf', data=b'PDF-BYTES'):
        import base64
        return self.env['ir.attachment'].create({
            'name': name,
            'datas': base64.b64encode(data),
        })

    def _document(self, project, number=None, **kwargs):
        vals = {
            'document_number': number or 'DOC-%04d' % self._next(),
            'name': 'Document %d' % self._next(),
            'project_id': project.id,
            'document_type': 'drawing',
        }
        vals.update(kwargs)
        return self.env['realestate.construction.document'].create(vals)

    def _revision(self, document, code='A', issue=False, approve=False,
                  purpose='review', **kwargs):
        vals = {
            'document_id': document.id,
            'revision_code': code,
            'purpose_of_issue': purpose,
            'attachment_id': self._attachment('%s-%s.pdf' % (
                document.document_number, code)).id,
        }
        vals.update(kwargs)
        revision = self.env[
            'realestate.construction.document.revision'].create(vals)
        if approve:
            revision.action_approve()
        elif issue:
            revision.action_issue_for_review()
        return revision

    def _rfi(self, project, **kwargs):
        vals = {
            'subject': 'Question %d' % self._next(),
            'question': '<p>Please clarify.</p>',
            'project_id': project.id,
        }
        vals.update(kwargs)
        return self.env['realestate.construction.rfi'].create(vals)

    def _submittal(self, project, contractor=None, **kwargs):
        vals = {
            'title': 'Submittal %d' % self._next(),
            'project_id': project.id,
            'contractor_id': (contractor or self._contractor()).id,
            'submittal_type': 'shop_drawing',
        }
        vals.update(kwargs)
        return self.env['realestate.construction.submittal'].create(vals)

    def _transmittal(self, project, revisions=None, recipients=None,
                     **kwargs):
        recipients = recipients or self.env['res.partner'].create(
            {'name': 'Recipient %d' % self._next()})
        vals = {
            'subject': 'Transmittal %d' % self._next(),
            'project_id': project.id,
            'recipient_partner_ids': [(6, 0, recipients.ids)],
        }
        if revisions is not None:
            vals['line_ids'] = [
                (0, 0, {'document_revision_id': revision.id})
                for revision in revisions
            ]
        vals.update(kwargs)
        return self.env['realestate.construction.transmittal'].create(vals)

    def _itp(self, project, contractor=None, points=(('normal',),), **kwargs):
        vals = {
            'title': 'ITP %d' % self._next(),
            'project_id': project.id,
            'contractor_id': (contractor.id if contractor else False),
            'item_ids': [(0, 0, {
                'activity': 'Activity %d' % index,
                'description': 'Check %d' % index,
                'inspection_point': point[0],
            }) for index, point in enumerate(points)],
        }
        vals.update(kwargs)
        return self.env['realestate.construction.itp'].create(vals)

    def _active_itp(self, project, contractor=None, points=(('normal',),)):
        itp = self._itp(project, contractor, points)
        itp.action_submit()
        itp.action_approve()
        itp.action_activate()
        return itp

    def _inspection_request(self, project, contractor=None, **kwargs):
        vals = {
            'title': 'Inspection request %d' % self._next(),
            'project_id': project.id,
            'contractor_id': (contractor or self._contractor()).id,
            'required_datetime': fields.Datetime.now(),
        }
        vals.update(kwargs)
        return self.env[
            'realestate.construction.inspection.request'].create(vals)

    def _inspection(self, project, contractor=None, **kwargs):
        vals = {
            'project_id': project.id,
            'contractor_id': (contractor or self._contractor()).id,
        }
        vals.update(kwargs)
        return self.env['realestate.construction.inspection'].create(vals)

    def _observation(self, project, contractor=None, **kwargs):
        vals = {
            'description': 'Observation %d' % self._next(),
            'project_id': project.id,
            'contractor_id': (contractor or self._contractor()).id,
        }
        vals.update(kwargs)
        return self.env[
            'realestate.construction.quality.observation'].create(vals)

    def _ncr(self, project, contractor=None, **kwargs):
        vals = {
            'title': 'NCR %d' % self._next(),
            'description': 'Work does not meet the specification.',
            'project_id': project.id,
            'contractor_id': (contractor or self._contractor()).id,
        }
        vals.update(kwargs)
        ncr = self.env['realestate.construction.ncr'].create(vals)
        ncr.action_open()
        return ncr

    def _daily_report(self, project, **kwargs):
        vals = {'project_id': project.id, 'report_date': self.today}
        vals.update(kwargs)
        return self.env[
            'realestate.construction.daily.report'].create(vals)

    def _change_event(self, project, estimated_cost=0.0,
                      estimated_revenue=0.0, **kwargs):
        vals = {
            'title': 'Change event %d' % self._next(),
            'project_id': project.id,
            'source': 'design_change',
            'estimated_cost_impact': estimated_cost,
            'estimated_revenue_impact': estimated_revenue,
        }
        vals.update(kwargs)
        event = self.env['realestate.construction.change.event'].create(vals)
        event.action_identify()
        return event

    # ------------------------------------------------------------------
    # M8 — claims, delays, EOT, risk, issues
    # ------------------------------------------------------------------
    def _delay_event(self, project, package=None, contractor=None, **kwargs):
        vals = {
            'title': 'Delay event %d' % self._next(),
            'project_id': project.id,
            'package_id': package.id if package else False,
            'contractor_id': contractor.id if contractor else False,
            'event_type': 'access_delay',
            'start_date': fields.Datetime.now(),
        }
        vals.update(kwargs)
        return self.env['realestate.construction.delay.event'].create(vals)

    def _notice(self, project, package=None, awareness_date=None,
                notice_date=None, **kwargs):
        vals = {
            'project_id': project.id,
            'package_id': package.id if package else False,
            'awareness_date': awareness_date or self.today,
            'notice_type': 'claim_event',
        }
        vals.update(kwargs)
        notice = self.env['realestate.construction.notice'].create(vals)
        if notice_date:
            notice.action_issue(notice_date=notice_date)
        return notice

    def _claim(self, project, package=None, claimed_cost=0.0,
               claimed_days=0.0, notice=None, contractor=None, **kwargs):
        vals = {
            'title': 'Claim %d' % self._next(),
            'project_id': project.id,
            'package_id': package.id if package else False,
            'contractor_id': (contractor or (package.contractor_id
                                             if package else False))
            and (contractor or package.contractor_id).id or False,
            'claim_type': 'other',
            'claimed_cost': claimed_cost,
            'claimed_days': claimed_days,
            'cost_claimed_known': bool(claimed_cost),
        }
        vals.update(kwargs)
        claim = self.env['realestate.construction.claim'].create(vals)
        if notice is not None:
            notice.claim_id = claim.id
            claim.invalidate_recordset()
        return claim

    def _eot(self, claim, claimed_days=0.0, determined_days=0.0, **kwargs):
        vals = {
            'project_id': claim.project_id.id,
            'package_id': claim.package_id.id,
            'claim_id': claim.id,
            'claimed_days': claimed_days,
            'determined_days': determined_days,
            'reason': '<p>Assessed against the programme.</p>',
        }
        vals.update(kwargs)
        return self.env['realestate.construction.eot'].create(vals)

    def _risk(self, project, cost_exposure=0.0, **kwargs):
        vals = {
            'title': 'Risk %d' % self._next(),
            'project_id': project.id,
            'category': 'procurement',
            'probability': 3,
            'cost_impact_score': 4,
            'schedule_impact_score': 3,
            'cost_exposure_likely': cost_exposure,
            'response_strategy': 'mitigate',
            'mitigation_plan': 'Second source identified.',
        }
        vals.update(kwargs)
        return self.env['realestate.construction.risk'].create(vals)

    def _issue(self, project, **kwargs):
        vals = {
            'title': 'Issue %d' % self._next(),
            'project_id': project.id,
            'category': 'other',
        }
        vals.update(kwargs)
        return self.env['realestate.construction.issue'].create(vals)

    def _configure_construction_accounts(self):
        """The control accounts M7 refuses to post without.

        Created here rather than shipped as data: a chart of accounts belongs
        to the company that owns it, and guessing one in a module is how
        retention ends up in the wrong place.
        """
        Account = self.env['account.account']

        def account(code, name, account_type):
            existing = Account.search(
                [('code', '=', code), ('company_ids', 'in', self.company.id)],
                limit=1)
            if existing:
                return existing
            return Account.create({
                'code': code, 'name': name, 'account_type': account_type,
                'company_ids': [(6, 0, self.company.ids)],
            })

        self.company.write({
            'construction_retention_account_id': account(
                'RE2101', 'Contractor Retention Payable',
                'liability_current').id,
            'construction_advance_account_id': account(
                'RE1301', 'Advances to Contractors', 'asset_current').id,
            'construction_owner_retention_account_id': account(
                'RE1302', 'Owner Retention Receivable', 'asset_current').id,
            'construction_owner_advance_account_id': account(
                'RE2102', 'Owner Advances Received', 'liability_current').id,
        })
        return self.company

    def _retention_release(self, project, contractor=None, amount=0.0,
                           stage='practical_completion', reason=None,
                           **kwargs):
        vals = {
            'project_id': project.id,
            'contractor_id': contractor.id if contractor else False,
            'amount': amount,
            'stage': stage,
            'reason': reason or 'Released for test.',
        }
        vals.update(kwargs)
        return self.env[
            'realestate.construction.retention.release'].create(vals)

    def _advance(self, project, contractor=None, amount=0.0, **kwargs):
        vals = {
            'project_id': project.id,
            'contractor_id': contractor.id if contractor else False,
            'amount': amount,
        }
        vals.update(kwargs)
        return self.env['realestate.construction.advance'].create(vals)

    def _owner_billing(self, project, amount=0.0, retention_pct=0.0,
                       partner=None, **kwargs):
        partner = partner or self.env['res.partner'].create(
            {'name': 'Owner %d' % self._next()})
        vals = {
            'project_id': project.id,
            'partner_id': partner.id,
            'contract_value': amount,
            'current_certified_pct': 100.0,
            'retention_pct': retention_pct,
            'period_end': self.today,
        }
        vals.update(kwargs)
        return self.env['realestate.owner.progress.billing'].create(vals)

    def _change_order(self, project, order_type='contractor_variation',
                      lines=(), contractor=None, package=None, event=None,
                      **kwargs):
        """`lines` is `(cost_code, impact_side, amount)` triples."""
        vals = {
            'title': 'Change order %d' % self._next(),
            'project_id': project.id,
            'order_type': order_type,
            'contractor_id': contractor.id if contractor else False,
            'package_id': package.id if package else False,
            'change_event_id': event.id if event else False,
            'line_ids': [(0, 0, {
                'cost_code_id': code.id,
                'impact_side': side,
                'estimated_amount': amount,
                'submitted_amount': amount,
                'negotiated_amount': amount,
            }) for code, side, amount in lines],
        }
        vals.update(kwargs)
        return self.env['realestate.construction.change.order'].create(vals)

    def _approve_and_implement(self, order):
        order.action_submit()
        order.action_request_approval()
        order.action_approve()
        order.action_implement()
        return order

    def _forecast(self, project, as_of_date=None, copy_forward=True):
        return self.env['realestate.construction.forecast'].generate_for(
            project, as_of_date=as_of_date, copy_forward=copy_forward)

    def _work_item(self, rate=100.0):
        seq = self._next()
        return self.WorkItem.create({
            'name': 'Work Item %d' % seq,
            'code': 'WI%04d' % seq,
            'uom_id': self.uom_unit.id,
            'default_rate': rate,
        })

    def _boq(self, project, quantities=((100.0, 50.0),), approve=True,
             **kwargs):
        """A BOQ with one line per (quantity, rate) pair given."""
        vals = {'project_id': project.id}
        vals.update(kwargs)
        boq = self.BOQ.create(vals)
        for qty, rate in quantities:
            self.BOQLine.create({
                'boq_id': boq.id,
                'work_item_id': self._work_item(rate).id,
                'quantity': qty,
                'unit_rate': rate,
                'uom_id': self.uom_unit.id,
            })
        if approve:
            boq.action_approve()
        return boq

    def _certificate(self, project, contractor, boq_line=None, qty=0.0,
                     pct=0.0, contract_value=0.0, amount=None, package=None,
                     cost_code=None, **kwargs):
        vals = {
            'project_id': project.id,
            'contractor_id': contractor.id,
            'period_end': self.today,
            'contract_value': contract_value,
            'current_certified_pct': pct,
            'retention_pct': contractor.retention_pct,
        }
        if amount is not None:
            vals['applied_amount'] = amount
        if package is not None:
            vals['package_id'] = package.id
        if cost_code is not None:
            vals['cost_code_id'] = cost_code.id
        vals.update(kwargs)
        certificate = self.Certificate.create(vals)
        if boq_line is not None:
            self.CertLine.create({
                'certificate_id': certificate.id,
                'boq_line_id': boq_line.id,
                'qty': qty,
                'unit_rate': boq_line.unit_rate,
            })
        return certificate
