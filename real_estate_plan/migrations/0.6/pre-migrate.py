# -*- coding: utf-8 -*-
"""Re-parent the boundary overlay before Plan's views reload.

`view_project_form_boundary_overlay` puts the 2D master-plan upload inside the
Plot Boundary page. That page used to be part of the project form; when the base
project views moved to `atmta_project_core`, the page stayed with the developer
application, in `real_estate_developer.view_project_form_developer`, because it
uses Developer's `boundary_picker` widget. The overlay's XML was re-pointed to
that extension accordingly.

On a fresh install that is all it takes. On an upgrade it is not: the overlay's
database record still has the project form as its parent, and it only gets the
new one when Plan's loader reaches that record. The record before it in the same
file (`view_project_form_hide_old_plan_2d`) is written first, and writing a view
validates the whole inheritance tree under the project form -- which still holds
the overlay, looking for a page the project form no longer has. The upgrade then
stops with "cannot be located in parent view" and the registry does not load.
Measured on the review database (overlay view 2714, parent 2555).

Re-parenting the record here, before any of Plan's data loads, makes the tree
consistent before anything validates it. Developer is a dependency of Plan, so
its extension already exists by the time this runs. The update only touches the
overlay, and only while it still hangs off the project form.
"""
import logging

_logger = logging.getLogger(__name__)


def _view_id(cr, module, name):
    cr.execute(
        "SELECT res_id FROM ir_model_data WHERE module = %s AND name = %s AND model = 'ir.ui.view'",
        (module, name),
    )
    row = cr.fetchone()
    return row[0] if row else None


def migrate(cr, version):
    if not version:
        return
    overlay = _view_id(cr, 'real_estate_plan', 'view_project_form_boundary_overlay')
    project_form = _view_id(cr, 'atmta_project_core', 'view_project_form')
    developer_ext = _view_id(cr, 'real_estate_developer', 'view_project_form_developer')
    if not overlay:
        return
    if not developer_ext:
        _logger.warning(
            "Plan 0.6: Developer's project form extension is missing, so the "
            "boundary overlay (view %s) was left on its current parent.", overlay)
        return
    cr.execute(
        "UPDATE ir_ui_view SET inherit_id = %s WHERE id = %s AND inherit_id = %s",
        (developer_ext, overlay, project_form),
    )
    if cr.rowcount:
        _logger.info(
            "Plan 0.6: boundary overlay (view %s) re-parented from the project form "
            "(view %s) to Developer's extension (view %s).",
            overlay, project_form, developer_ext)
