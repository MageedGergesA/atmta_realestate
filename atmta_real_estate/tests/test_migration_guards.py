"""Migrations run on databases of every earlier version.

Odoo runs all applicable pre-migrations before it creates any new table, so a
database upgraded from 0.5 reaches ``0.10/pre-migrate.py`` without the
allocation table that 0.6 introduced. These tests run the real script against a
stub cursor shaped like such a database; a real 0.5 → 0.10 upgrade is verified
separately (audit §L, Phase 5).
"""

import importlib.util
import os

from odoo.tests.common import BaseCase, tagged

MIGRATION = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                         'migrations', '0.10', 'pre-migrate.py')


def _load_migration():
    spec = importlib.util.spec_from_file_location('rental_pre_migrate_0_10', MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _StubCursor:
    """Answers the information_schema questions the script asks, records the rest."""

    def __init__(self, tables, columns):
        self.tables = tables        # table name -> table_type
        self.columns = columns      # table name -> set of column names
        self.statements = []
        self.rowcount = 0
        self._rows = []

    def execute(self, query, params=None):
        sql = ' '.join(query.split())
        self.statements.append(sql)
        self.rowcount = 0
        if 'information_schema.tables' in sql:
            kind = self.tables.get(params[0])
            self._rows = [(kind,)] if kind else []
        elif 'information_schema.columns' in sql:
            self._rows = [(name,) for name in sorted(self.columns.get(params[0], ()))]
        elif sql.startswith('SELECT count(*), count(allocation_id)'):
            self._rows = [(0, 0)]
        else:
            self._rows = []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


@tagged('post_install', '-at_install', 'atmta_leasing')
class TestMigrationGuards(BaseCase):

    def _run(self, tables, columns):
        cr = _StubCursor(tables, columns)
        _load_migration().migrate(cr, '0.5.0')
        return cr

    def _writes_to(self, cr, table):
        return [sql for sql in cr.statements
                if table in sql and 'information_schema' not in sql]

    def test_upgrade_from_0_5_does_not_touch_the_missing_allocation_table(self):
        cr = self._run(
            tables={'realestate_contract_line': 'BASE TABLE'},
            columns={'realestate_contract_payment': {'id', 'contract_id', 'property_id'}},
        )
        self.assertFalse(self._writes_to(cr, 'realestate_contract_property_line'))
        # The legacy lines are still recorded before the model goes away.
        self.assertTrue(any(sql.startswith('CREATE TABLE re_legacy_contract_line_backup')
                            for sql in cr.statements))

    def test_upgrade_with_allocations_still_releases_the_mirrors(self):
        cr = self._run(
            tables={'realestate_contract_line': 'BASE TABLE',
                    'realestate_contract_property_line': 'BASE TABLE'},
            columns={'realestate_contract_property_line': {'id', 'origin', 'legacy_line_id'},
                     'realestate_contract_payment': {'id', 'contract_id', 'property_id'}},
        )
        self.assertEqual(
            [sql for sql in self._writes_to(cr, 'realestate_contract_property_line')
             if sql.startswith('UPDATE')],
            ["UPDATE realestate_contract_property_line SET origin = 'manual' "
             "WHERE origin = 'legacy_line'"])

    def test_a_database_without_legacy_lines_is_left_alone(self):
        cr = self._run(tables={}, columns={})
        self.assertFalse([sql for sql in cr.statements
                          if not sql.startswith('SELECT')])
