# Wave 25 — the construction team, moved down to the floor.
#
# This field is not construction data. It is the input to an access rule: the
# thirty-nine project-team rules across the nine capability modules all filter
# on `project_id.construction_member_ids`, and a rule may not be declared
# below the field it reads. While this field lived in
# `real_estate_construction` at depth twelve, every one of those rules was
# pinned there with it, guarding models that had already moved out.
#
# Declaring it here, on the floor, is what lets each rule sit beside the model
# it protects. `atmta_construction_core` already depends on
# `atmta_project_core`, which owns `realestate.project`, so nothing new is
# borrowed to say this.
#
# The relation table is named explicitly and is unchanged, so the move is a
# change of which module owns the field definition. Not one membership row is
# read, written or deleted.
from odoo import fields, models


class ProjectConstructionMember(models.Model):
    _inherit = 'realestate.project'

    construction_member_ids = fields.Many2many(
        'res.users', 'construction_project_member_rel', 'project_id',
        'user_id', string='Construction Team',
        help="Who may see this project's construction records. Leave empty "
             "and the project stays visible to everyone in the company, "
             "exactly as before — naming anybody is what turns access "
             "control on, per project, as a deliberate act.")
