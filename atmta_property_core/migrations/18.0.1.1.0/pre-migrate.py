# -*- coding: utf-8 -*-
"""Hand the property view identifiers over from Rental to Property Core.

The five base property views were declared by `atmta_real_estate` even though
`atmta_property_core` owns the model. They now live in Property Core. On an
installed database the rows in `ir_model_data` still say `atmta_real_estate`,
and Odoo deletes a module's stale identifiers at the end of its own load -- so
without this the views would be dropped mid-upgrade and every inheriting view
in Brokerage, Developer, Handover, Maquette, Plan and Rental itself would lose
its parent.

Re-owning the rows keeps the same `ir_model_data` id, the same view id and the
same local name, so nothing that points at the views by database id notices,
and references written as `atmta_property_core.view_property_form` resolve to
exactly the record that was there before.

Property Core loads before Rental, so this runs before Rental's load could
delete anything. On a database without Rental the UPDATE simply matches no
rows.
"""

OLD_MODULE = 'atmta_real_estate'
NEW_MODULE = 'atmta_property_core'

VIEW_NAMES = (
    'view_property_form',
    'view_property_list',
    'view_property_search',
    'view_property_kanban',
    'view_property_hierarchy',
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
