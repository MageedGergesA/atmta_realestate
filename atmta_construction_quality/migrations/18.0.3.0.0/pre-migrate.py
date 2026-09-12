"""Re-point the project-team rules that came down in Wave 25.

These rules guarded models this module already owned, but they were declared
in `real_estate_construction`: their domains read
`project_id.construction_member_ids`, and until Wave 25 that module declared
the field. A rule may be declared above the model it guards. It may not be
declared below the field it reads.

Wave 25 moved the field to `atmta_construction_core`, which this module
already depends on, and the rules followed to the modules that own their
models.

On a database where this module is already installed `pre_init_hook` does not
fire: a hook runs once, at install. Only a migration runs on an upgrade. Wave
21 proved what happens without one -- the records are deleted and recreated
with new database ids, every total still matches, and anything layered on the
old rows is silently orphaned. An access rule losing its identity is worse
than a screen losing one: the old row is what a group is still attached to.

Re-pointing first means the load finds the rows and updates them in place,
which is also what applies the canonical group binding. This module is a
dependency of `real_estate_construction`, so it upgrades first and the window
never opens.

`noupdate` is cleared in the same statement, and that is a deliberate change
rather than a side effect. The monolith declared these rules under
`<odoo noupdate="1">`, so the rows carry the flag and a reload skips them --
which would have left the legacy group binding in place on every upgraded
database while a fresh install got the canonical one. The company rules
already in this file are `noupdate="0"`, and a file cannot coherently freeze
the project rule for a model while leaving the company rule for the same model
updatable. Upgrade now converges on what a fresh install produces, which is
the whole point of measuring the two against each other.

`(module, name)` is unique on `ir_model_data`, so this is an UPDATE of one
column. No business row is read, written or deleted.
"""
import logging

from odoo.addons.atmta_construction_quality.hooks import W25_RULE_XMLIDS

_logger = logging.getLogger(__name__)

OLD = 'real_estate_construction'
NEW = 'atmta_construction_quality'


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE ir_model_data SET module = %s, noupdate = false
         WHERE module = %s AND model = 'ir.rule' AND name = ANY(%s)
    """, (NEW, OLD, W25_RULE_XMLIDS))
    _logger.info("Wave 25: %s record rules re-pointed to %s", cr.rowcount, NEW)
