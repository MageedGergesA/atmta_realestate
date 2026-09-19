"""Every Rental report, bound server action and scheduled job runs.

These run far from any form or list: a report renders a template over a record,
a gear-menu action runs code on the selected record, a scheduled job runs code
on its own. A field or method the cleanup removed but one of them still names
fails only when that report is printed, that action is chosen or that job fires.
Each is found from Rental's own XML IDs and run against a realistic record.
"""

from dateutil.relativedelta import relativedelta

from odoo.tests.common import tagged

from .common import LeaseCase


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestActionsSweep(LeaseCase):

    def _world(self):
        start = self.today.replace(day=1) - relativedelta(months=3)
        lease = self.activate(self.make_lease(
            prop=self.unit_a, start=start, end=start + relativedelta(years=1, days=-1),
            rent=1000.0, use_billing_engine=True))
        lease.action_generate_billing_schedule()
        lease.action_invoice_due_obligations()
        deposit = self.env['realestate.contract.deposit'].create({
            'contract_id': lease.id, 'partner_id': self.tenant.id, 'requested_amount': 2000.0,
        })
        move_in = self.env['realestate.move.in'].create({
            'contract_id': lease.id, 'property_id': self.unit_a.id,
            'scheduled_date': self.today, 'tenant_acknowledged': True,
        })
        renewal = self.env['realestate.contract.renewal'].create({
            'contract_id': lease.id,
            'proposed_start_date': lease.end_date + relativedelta(days=1),
            'proposed_end_date': lease.end_date + relativedelta(years=1),
            'proposed_rent': 1050.0,
        })
        amendment = self.env['realestate.contract.amendment'].create({
            'contract_id': lease.id, 'amendment_type': 'rent_change',
            'effective_date': self.today + relativedelta(months=1),
            'new_rent': 1100.0, 'reason': 'Review',
        })
        cutoff = self.today.replace(day=1) + relativedelta(months=2)
        termination = self.env['realestate.contract.termination'].create({
            'contract_id': lease.id, 'requested_end_date': cutoff,
            'effective_date': cutoff, 'reason': 'tenant_notice', 'requested_by': 'tenant',
        })
        records = {
            'realestate.contract': lease,
            'realestate.contract.payment': lease.contract_payment_ids.filtered('move_id')[:1]
                                           or lease.contract_payment_ids[:1],
            'realestate.contract.deposit': deposit,
            'realestate.move.in': move_in,
            'realestate.contract.renewal': renewal,
            'realestate.contract.amendment': amendment,
            'realestate.contract.termination': termination,
            'realestate.property': self.unit_a,
            'res.partner': self.tenant,
        }
        return records

    def _rental_records(self, model):
        data = self.env['ir.model.data'].search(
            [('module', '=', 'atmta_real_estate'), ('model', '=', model)])
        return self.env[model].browse(data.mapped('res_id')).exists()

    def test_every_report_renders(self):
        records = self._world()
        failures, rendered = [], []
        for report in self._rental_records('ir.actions.report'):
            record = records.get(report.model)
            if not record:
                continue
            try:
                with self.env.cr.savepoint():
                    self.env['ir.actions.report']._render_qweb_html(report.report_name, record.ids)
                rendered.append(report.report_name)
            except Exception as exc:  # noqa: BLE001 - collect every report
                failures.append('%s (%s): %s: %s' % (
                    report.report_name, report.model, type(exc).__name__,
                    (str(exc).splitlines() or [''])[0][:200]))
        self.assertTrue(rendered, "no Rental report was exercised")
        self.assertFalse(failures, '\n'.join(failures))

    def test_every_bound_server_action_runs(self):
        records = self._world()
        failures, ran = [], []
        for action in self._rental_records('ir.actions.server'):
            model = action.binding_model_id.model
            record = records.get(model)
            if not model or not record:
                continue
            try:
                with self.env.cr.savepoint():
                    action.with_context(active_model=model, active_id=record.id,
                                        active_ids=record.ids).run()
                ran.append(action.name)
            except Exception as exc:  # noqa: BLE001
                failures.append('%s (%s): %s: %s' % (
                    action.name, model, type(exc).__name__,
                    (str(exc).splitlines() or [''])[0][:200]))
        self.assertTrue(ran, "no bound Rental server action was exercised")
        self.assertFalse(failures, '\n'.join(failures))

    def test_every_scheduled_job_runs(self):
        self._world()
        failures, ran = [], []
        for cron in self._rental_records('ir.cron').with_context(active_test=False):
            try:
                with self.env.cr.savepoint():
                    cron.ir_actions_server_id.run()
                ran.append(cron.name)
            except Exception as exc:  # noqa: BLE001
                failures.append('%s: %s: %s' % (
                    cron.name, type(exc).__name__, (str(exc).splitlines() or [''])[0][:200]))
        self.assertTrue(ran, "no Rental scheduled job was exercised")
        self.assertFalse(failures, '\n'.join(failures))
