# -*- coding: utf-8 -*-
"""Demo layer: a site that is actually being built.

The Construction dashboard and the Control Tower both answer "is this project
in trouble", and both answer it from milestones, packages, BOQs, quality
findings and information flow. With none of that present the Control Tower
reads N/A across the board, which demonstrates only that it is honest about
having no data.

This builds one project's worth of real work: three contractors on three
packages, a work-item library and a priced BOQ per package, a milestone
programme with some of it finished, some running and one slipping, plus the
quality and information records (NCRs, RFIs, daily reports) the tower counts.

Everything hangs off the TMR project the developer layer creates, and every
date is relative to today so the programme still reads correctly next quarter.
"""

import logging

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

MODULE = 'real_estate_construction'
SENTINEL = '%s.demo_package_civil' % MODULE

#: (code, name, uom hint, rate) -- the priced library the BOQs draw on.
WORK_ITEMS = [
    ('EXC-01', 'Bulk excavation to reduced level', 'm3', 180.0),
    ('CON-01', 'Reinforced concrete to foundations', 'm3', 3850.0),
    ('CON-02', 'Reinforced concrete to slabs and columns', 'm3', 4200.0),
    ('STL-01', 'High-tensile reinforcement, cut bent and fixed', 'kg', 48.0),
    ('BLK-01', 'Blockwork to internal partitions', 'm2', 320.0),
    ('PLS-01', 'Cement plaster, two coats', 'm2', 145.0),
    ('MEP-01', 'Electrical first fix per apartment', 'Units', 18500.0),
    ('MEP-02', 'Plumbing first fix per apartment', 'Units', 16400.0),
    ('MEP-03', 'HVAC ducting and diffusers', 'm2', 410.0),
    ('FIN-01', 'Porcelain floor tiling', 'm2', 580.0),
    ('FIN-02', 'Painting to walls and ceilings', 'm2', 95.0),
    ('FAC-01', 'Unitised curtain wall', 'm2', 3100.0),
]

#: (key, title, package_type, contractor index, state, tender value, retention)
PACKAGES = [
    ('civil', 'TMR-P1 Civil & Structural Works', 'civil', 0, 'active',
     420000000.0, 10.0),
    ('mep', 'TMR-P1 MEP Installation', 'mep', 1, 'active', 185000000.0, 10.0),
    ('finishes', 'TMR-P1 Finishing & Fit-out', 'finishes', 2, 'awarded',
     96000000.0, 5.0),
]

#: (name, category, months from start, duration months, state, % complete, weight)
MILESTONES = [
    ('Site mobilisation and enabling works', 'excavation', 0, 2, 'completed', 100.0, 3.0),
    ('Bulk excavation and shoring', 'excavation', 2, 3, 'completed', 100.0, 6.0),
    ('Raft foundation — Tower A', 'foundation', 5, 3, 'completed', 100.0, 10.0),
    ('Raft foundation — Tower B', 'foundation', 6, 3, 'completed', 100.0, 8.0),
    ('Superstructure — Tower A', 'structure', 8, 8, 'completed', 100.0, 18.0),
    ('Superstructure — Tower B', 'structure', 10, 7, 'in_progress', 82.0, 14.0),
    ('Blockwork and internal partitions', 'masonry', 15, 5, 'in_progress', 55.0, 7.0),
    ('MEP first fix — Tower A', 'mep', 16, 6, 'in_progress', 48.0, 9.0),
    # One milestone slipping, because a programme where nothing is late
    # demonstrates nothing the Control Tower exists to show.
    ('Facade — unitised curtain wall', 'facade', 17, 7, 'delayed', 22.0, 11.0),
    ('Internal finishes — Tower A', 'finishing', 21, 6, 'not_started', 0.0, 8.0),
    ('Landscape and external works', 'landscape', 24, 4, 'not_started', 0.0, 4.0),
    ('Handover preparation and snagging', 'handover_prep', 27, 3, 'not_started', 0.0, 2.0),
]


class ConstructionDemoBuilder(models.AbstractModel):
    _name = 'realestate.demo.construction'
    _description = 'Demo Builder — Construction Site'

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
        foundation = self.env['realestate.demo.property']
        project = self.env.ref('real_estate_developer.demo_tmr_project',
                               raise_if_not_found=False)
        if not project:
            _logger.warning("Demo: TMR project missing, construction skipped")
            return True
        phase = self.env.ref('real_estate_developer.demo_tmr_phase_1',
                             raise_if_not_found=False)

        contractors = self._build_contractors(foundation)
        items = self._build_work_items()
        packages = self._build_packages(project, contractors)
        milestones = self._build_milestones(project, phase, contractors)
        self._build_boqs(project, phase, packages, contractors, items, milestones)
        self._build_quality(project, packages, contractors)
        self._build_information(project, packages, contractors)
        self._build_daily_reports(project)
        return True

    # ------------------------------------------------------------------
    @api.model
    def _build_contractors(self, foundation):
        Contractor = self.env['realestate.contractor']
        spec = [('civil', 'CIV-2019-4471'), ('mep', 'MEP-2020-8832'),
                ('finishing', 'FIN-2021-2290')]
        contractors = []
        for index, (specialization, licence) in enumerate(spec, start=1):
            partner = foundation._demo_partner('contractor_%d' % index)
            contractor = Contractor.create({
                'name': partner.name,
                'partner_id': partner.id,
                'specialization': specialization,
                'license_number': licence,
                'rating': 'a' if index == 1 else 'b',
                'retention_pct': 10.0 if index < 3 else 5.0,
            })
            contractors.append(self._xmlid('demo_contractor_%d' % index, contractor))
        return contractors

    # ------------------------------------------------------------------
    @api.model
    def _build_work_items(self):
        Item = self.env['realestate.work.item']
        Category = self.env['realestate.work.item.category']
        Uom = self.env['uom.uom']
        category = Category.search([('name', '=', 'Building Works')], limit=1)
        if not category:
            category = Category.create({'name': 'Building Works', 'code': 'BW'})
            self._xmlid('demo_work_category', category)

        # The BOQ needs a unit for every line. Fall back to Units rather than
        # skipping the line: a BOQ with holes in it prices nothing.
        default_uom = self.env.ref('uom.product_uom_unit', raise_if_not_found=False) \
            or Uom.search([], limit=1)
        items = {}
        for index, (code, name, uom_name, rate) in enumerate(WORK_ITEMS, start=1):
            uom = Uom.search([('name', '=', uom_name)], limit=1) or default_uom
            item = Item.create({
                'name': name,
                'code': code,
                'category_id': category.id,
                'uom_id': uom.id,
                'default_rate': rate,
            })
            items[code] = self._xmlid('demo_work_item_%d' % index, item)
        return items

    # ------------------------------------------------------------------
    @api.model
    def _build_packages(self, project, contractors):
        Package = self.env['realestate.construction.contract.package']
        today = fields.Date.context_today(self)
        packages = {}
        for key, title, package_type, index, state, value, retention in PACKAGES:
            package = Package.create({
                'title': title,
                'project_id': project.id,
                'contractor_id': contractors[index].id,
                'package_type': package_type,
                'state': state,
                'tender_value': value,
                'retention_pct': retention,
                'advance_pct': 10.0,
                'advance_amount': value * 0.10,
                'date_start': today - relativedelta(months=20 - index * 4),
                'current_completion_date': today + relativedelta(months=9 + index * 3),
                'notice_required': True,
                'notice_period_days': 28,
                'notice_day_basis': 'calendar',
                'detailed_claim_period_days': 42,
                'scope_description': '<p>%s for Teklines Marina Residences '
                                     'Phase 1.</p>' % title,
            })
            packages[key] = self._xmlid('demo_package_%s' % key, package)
        return packages

    # ------------------------------------------------------------------
    @api.model
    def _build_milestones(self, project, phase, contractors):
        Milestone = self.env['realestate.construction.milestone']
        today = fields.Date.context_today(self)
        start = today - relativedelta(months=20)
        by_category = {'excavation': 0, 'foundation': 0, 'structure': 0,
                       'masonry': 0, 'mep': 1, 'facade': 0,
                       'finishing': 2, 'landscape': 2, 'handover_prep': 2}
        milestones = []
        for index, (name, category, offset, duration, state, done,
                    weight) in enumerate(MILESTONES, start=1):
            expected_start = start + relativedelta(months=offset)
            expected_end = expected_start + relativedelta(months=duration)
            vals = {
                'name': name,
                'project_id': project.id,
                'phase_id': phase.id if phase else False,
                'category': category,
                'state': state,
                'sequence': index * 10,
                'weight': weight,
                'completion_percentage': done,
                'expected_start_date': expected_start,
                'expected_end_date': expected_end,
                'contractor_id': contractors[by_category.get(category, 0)].id,
                'budget_amount': round(weight * 7000000, -3),
            }
            if state in ('completed', 'in_progress', 'delayed'):
                vals['actual_start_date'] = expected_start
            if state == 'completed':
                vals['actual_end_date'] = expected_end
            milestone = Milestone.create(vals)
            milestones.append(self._xmlid('demo_milestone_%d' % index, milestone))
        return milestones

    # ------------------------------------------------------------------
    #: package key -> (work item codes, quantity multiplier)
    BOQ_SCOPE = {
        'civil': [('EXC-01', 48000), ('CON-01', 7200), ('CON-02', 14500),
                  ('STL-01', 1850000), ('BLK-01', 38000)],
        'mep': [('MEP-01', 40), ('MEP-02', 40), ('MEP-03', 32000)],
        'finishes': [('PLS-01', 62000), ('FIN-01', 28000), ('FIN-02', 71000),
                     ('FAC-01', 9400)],
    }

    @api.model
    def _build_boqs(self, project, phase, packages, contractors, items, milestones):
        Boq = self.env['realestate.boq']
        contractor_by_key = {'civil': 0, 'mep': 1, 'finishes': 2}
        for key, scope in self.BOQ_SCOPE.items():
            lines = []
            for sequence, (code, quantity) in enumerate(scope, start=1):
                item = items.get(code)
                if not item:
                    continue
                lines.append((0, 0, {
                    'sequence': sequence * 10,
                    'work_item_id': item.id,
                    'description': item.name,
                    'quantity': quantity,
                    'uom_id': item.uom_id.id,
                    'unit_rate': item.default_rate,
                }))
            if not lines:
                continue
            boq = Boq.create({
                'name': 'BOQ — %s' % packages[key].title,
                'project_id': project.id,
                'phase_id': phase.id if phase else False,
                'contractor_id': contractors[contractor_by_key[key]].id,
                'state': 'approved' if key != 'finishes' else 'draft',
                'revision': 1,
                'line_ids': lines,
            })
            self._xmlid('demo_boq_%s' % key, boq)
        return True

    # ------------------------------------------------------------------
    #: (severity, discipline, source, days ago, title, description)
    NCRS = [
        ('major', 'structural', 'inspection', 34,
         'Honeycombing to column C-14, level 7',
         'Honeycombing observed to column C-14 at level 7 after striking.'),
        ('minor', 'architectural', 'observation', 21,
         'Blockwork out of plumb, apartment B-402',
         'Blockwork coursing out of plumb by 12mm in apartment B-402.'),
        ('critical', 'mechanical', 'test', 12,
         'Chilled water pressure test failure, riser 3',
         'Chilled water pressure test failed at riser 3; joint leaking.'),
        ('minor', 'electrical', 'inspection', 7,
         'Cable tray support spacing, basement corridor',
         'Cable tray supports exceed 1.5m spacing in basement corridor.'),
    ]

    @api.model
    def _build_quality(self, project, packages, contractors):
        Ncr = self.env['realestate.construction.ncr']
        today = fields.Date.context_today(self)
        package_cycle = list(packages.values())
        for index, (severity, discipline, source, days, title,
                    description) in enumerate(self.NCRS, start=1):
            package = package_cycle[index % len(package_cycle)]
            ncr = Ncr.create({
                'title': title,
                'project_id': project.id,
                'package_id': package.id,
                'contractor_id': package.contractor_id.id,
                'severity': severity,
                'discipline': discipline,
                'source': source,
                'discovery_date': today - relativedelta(days=days),
                'description': description,
                'requirement_violated': 'Specification section 03300 / 15400',
                'location': 'Tower A, level %d' % (index + 3),
                'cost_impact': 'potential' if severity != 'minor' else 'none',
                'schedule_impact': 'potential' if severity == 'critical' else 'none',
                'estimated_rework_cost': 180000.0 if severity == 'critical' else 0.0,
                'root_cause_category': 'workmanship',
                'proposed_disposition': 'rework',
            })
            self._xmlid('demo_ncr_%d' % index, ncr)
        return True

    # ------------------------------------------------------------------
    #: (subject, discipline, state, days ago, question)
    RFIS = [
        ('Curtain wall bracket fixing detail at slab edge', 'structural',
         'answered', 40,
         'Please confirm the embed plate detail where the unitised panel '
         'bracket lands on the slab edge at levels 5 to 9.'),
        ('Clash between chilled water riser and structural beam', 'mechanical',
         'under_review', 18,
         'Riser 3 clashes with beam B-220. Confirm whether the beam may be '
         'penetrated or the riser rerouted.'),
        ('Floor finish transition at lift lobby', 'architectural', 'open', 9,
         'Specification shows two different thresholds. Confirm which applies '
         'at the Tower A lift lobbies.'),
        ('Earthing arrangement for the generator room', 'electrical', 'open', 3,
         'Confirm the earth pit configuration and test values required before '
         'backfilling.'),
    ]

    @api.model
    def _build_information(self, project, packages, contractors):
        Rfi = self.env['realestate.construction.rfi']
        today = fields.Date.context_today(self)
        package_cycle = list(packages.values())
        for index, (subject, discipline, state, days,
                    question) in enumerate(self.RFIS, start=1):
            package = package_cycle[index % len(package_cycle)]
            created = today - relativedelta(days=days)
            vals = {
                'subject': subject,
                'project_id': project.id,
                'package_id': package.id,
                'contractor_id': package.contractor_id.id,
                'discipline': discipline,
                'state': state,
                'created_date': created,
                'submitted_date': created,
                # Deliberately in the past for the two open ones, so the
                # Control Tower's "overdue RFI" count is not always zero.
                'required_response_date': created + relativedelta(days=14),
                'question': '<p>%s</p>' % question,
                'cost_impact': 'potential' if index == 2 else 'none',
                'schedule_impact': 'potential' if index == 2 else 'none',
                'priority': '2' if index <= 2 else '1',
            }
            if state == 'answered':
                vals['official_response'] = (
                    '<p>Use detail SK-104 rev B. Embed plate to be '
                    '300x300x12mm with 4 no. headed studs.</p>')
            self._xmlid('demo_rfi_%d' % index, Rfi.create(vals))
        return True

    # ------------------------------------------------------------------
    @api.model
    def _build_daily_reports(self, project):
        Report = self.env['realestate.construction.daily.report']
        today = fields.Date.context_today(self)
        weather = ['clear', 'clear', 'cloudy', 'clear', 'wind', 'clear', 'sandstorm']
        for offset in range(1, 15):
            day = today - relativedelta(days=offset)
            condition = weather[offset % len(weather)]
            Report.create({
                'project_id': project.id,
                'report_date': day,
                'shift': 'day',
                'state': 'closed' if offset > 3 else 'submitted',
                'weather_condition': condition,
                'weather_stopped_work': condition == 'sandstorm',
                'temperature_min': 19.0 + (offset % 4),
                'temperature_max': 29.0 + (offset % 5),
                'site_conditions': 'Access roads clear; tower crane operational.',
                'safety_notes': 'Toolbox talk held. No lost-time incidents.',
                'quality_notes': 'Pour inspections signed off for level 8 slab.',
            })
        return True
