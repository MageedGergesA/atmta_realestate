# -*- coding: utf-8 -*-
"""Hand the project and phase view identifiers over from Developer to Project Core.

The five base views were declared by `real_estate_developer` even though
`atmta_project_core` owns `realestate.project` and `realestate.phase`. They now
live in Project Core. On an installed database the rows in `ir_model_data`
still say `real_estate_developer`, and Odoo deletes a module's stale
identifiers at the end of its own load -- so without this the views would be
dropped mid-upgrade and every inheriting view in Construction, Procurement,
Maquette, Plan and Developer itself would lose its parent.

This is the same contract the Wave 11 procurement migrations use, and it runs
in the receiving module for the same reason they give: Odoo loads a dependency
before the module that depends on it, so by the time Developer is processed
Project Core has already loaded its data. Re-pointing the rows first means that
load finds them and updates the records already there instead of creating
rivals beside them.

Re-owning keeps the same `ir_model_data` id, the same view id and the same
local name, so anything pointing at the views by database id is untouched.

The two actions stay with Developer: `atmta_executive_app` and
`atmta_development_app` reference `real_estate_developer.action_realestate_project`
and `..._phase` by name.

`(module, name)` is unique on `ir_model_data`, so this is an UPDATE of one
column. No business row is read, written or deleted. On a database without
Developer the UPDATE simply matches no rows.
"""
import logging

_logger = logging.getLogger(__name__)

OLD_MODULE = 'real_estate_developer'
NEW_MODULE = 'atmta_project_core'

VIEW_NAMES = (
    'view_project_form',
    'view_project_list',
    'view_project_kanban',
    'view_phase_form',
    'view_phase_list',
)


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        """
        UPDATE ir_model_data
           SET module = %(new)s
         WHERE module = %(old)s
           AND model = 'ir.ui.view'
           AND name IN %(names)s
           AND NOT EXISTS (
                   SELECT 1 FROM ir_model_data taken
                    WHERE taken.module = %(new)s
                      AND taken.name = ir_model_data.name
               )
        """,
        {'new': NEW_MODULE, 'old': OLD_MODULE, 'names': VIEW_NAMES},
    )
    _logger.info("Project views: %s identifiers handed to %s",
                 cr.rowcount, NEW_MODULE)
