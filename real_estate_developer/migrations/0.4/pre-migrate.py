# -*- coding: utf-8 -*-
"""Wave 2 — hand the Project Core model identifiers over to their new owner.

``realestate.project``, ``realestate.phase`` and
``realestate.project.boundary.point`` are now declared by
``atmta_project_core``. The models, their tables, their columns and every row in
them are untouched; what moves is the *module that owns the identifier*.

Odoo would very nearly do this by itself. ``IrModelData._process_end`` drops an
XML-ID that an updated module no longer produces, and at
``ir_model.py:2644-2652`` it explicitly keeps the underlying record when another
XML-ID still points at it -- which is exactly the situation here, because
``atmta_project_core`` loads first (``real_estate_developer`` depends on it) and
claims the identifier before the stale one is examined.

Relying on that would make the outcome depend on load order, on a dependency
edge staying where it is, and on a subtlety of a method nobody reading this
module would think to check. The three identifiers are referenced by twelve ACL
rows and two record rules, so if the inference were ever wrong the failure would
be silent access loss rather than a crash. It is cheap to state the intent
directly instead, and to assert the result.

The two multi-company record rules move with them. `company_id` is now
declared by ``atmta_project_core``, so the rules that filter on it belong to the
same module; ``atmta_project_core`` creates its own copies when it loads, and
this script retires the originals. They cannot be left to ``_process_end``:
their file carries ``noupdate="1"``, and that method skips no-update records
entirely, so removing them from the XML alone would strand two duplicate global
rules in every existing database forever.

Field identifiers are deliberately NOT handed over. Nothing in the suite
references a ``field_*`` XML-ID -- verified by search across every XML and CSV
file -- so the self-healing path is safe for them, and enumerating a hundred
field names here would be a list that rots the first time somebody adds a field.
The counts are logged so the evidence shows what actually happened.
"""

import logging

_logger = logging.getLogger(__name__)

OLD = 'real_estate_developer'
NEW = 'atmta_project_core'

MODELS = (
    'realestate.project',
    'realestate.phase',
    'realestate.project.boundary.point',
)
MODEL_XMLIDS = tuple('model_' + m.replace('.', '_') for m in MODELS)


def _hand_over(cr, names):
    """Move ``ir_model_data`` rows OLD.<name> -> NEW.<name>, collision-safe.

    If the new module already claimed the identifier (it loads first, so this is
    the normal case on an upgrade), the stale row is deleted rather than
    renamed: ``ir_model_data`` carries a UNIQUE(module, name) constraint, and an
    UPDATE would hit it.
    """
    renamed = deduped = 0
    cr.execute("""
        SELECT o.id, o.name, n.id
        FROM ir_model_data o
        LEFT JOIN ir_model_data n ON n.module = %s AND n.name = o.name
        WHERE o.module = %s AND o.name = ANY(%s)
    """, (NEW, OLD, list(names)))
    for old_id, name, new_id in cr.fetchall():
        if new_id:
            cr.execute("DELETE FROM ir_model_data WHERE id = %s", (old_id,))
            deduped += 1
        else:
            cr.execute("UPDATE ir_model_data SET module = %s WHERE id = %s",
                       (NEW, old_id))
            renamed += 1
    return renamed, deduped


SUPERSEDED_RULES = ('rule_project_company', 'rule_phase_company')


def _retire_superseded_rules(cr):
    """Delete the Project/Phase company rules this module used to own.

    Idempotent: after the first run there is nothing left to match. Refuses to
    delete anything unless the replacement is already in place, so a partial or
    reordered load cannot leave the models unprotected -- an isolation rule is
    the one thing that must never be removed optimistically.
    """
    cr.execute("""
        SELECT count(*) FROM ir_model_data
        WHERE module = %s AND model = 'ir.rule' AND name = ANY(%s)
    """, (NEW, list(SUPERSEDED_RULES)))
    if cr.fetchone()[0] != len(SUPERSEDED_RULES):
        raise AssertionError(
            "Refusing to retire the Project/Phase company rules: %s has not "
            "created its replacements. Removing them now would leave both "
            "models with no company isolation at all." % NEW)

    cr.execute("""
        SELECT id, res_id FROM ir_model_data
        WHERE module = %s AND model = 'ir.rule' AND name = ANY(%s)
    """, (OLD, list(SUPERSEDED_RULES)))
    rows = cr.fetchall()
    if rows:
        cr.execute("DELETE FROM ir_rule WHERE id = ANY(%s)",
                   ([r[1] for r in rows],))
        cr.execute("DELETE FROM ir_model_data WHERE id = ANY(%s)",
                   ([r[0] for r in rows],))
    return len(rows)


SEQUENCE_XMLID = 'seq_realestate_project'
SEQUENCE_CODE = 'realestate.project'


def _adopt_project_sequence(cr):
    """Give the project code sequence to its new owner without resetting it.

    ``atmta_project_core`` loads first and declares the sequence too, so by the
    time this runs there are two ``ir_sequence`` rows with the code
    ``realestate.project``: the original, carrying a counter that has already
    issued every existing project code, and a brand new one sitting at 1.

    The original is the one to keep. ``realestate_project`` carries
    UNIQUE (company_id, code), so a sequence restarting at 1 would re-issue
    PRJ-0001 and the next project created in that company would simply fail to
    save. Transferring the counter between the two would work, but it means
    reading and re-seeding a backing Postgres sequence in raw SQL for no gain;
    keeping the original row untouched and discarding the duplicate leaves the
    id, the backing sequence and the counter exactly as they were, and only the
    identifier changes hands.
    """
    cr.execute("""
        SELECT module, id, res_id FROM ir_model_data
        WHERE model = 'ir.sequence' AND name = %s AND module IN (%s, %s)
    """, (SEQUENCE_XMLID, OLD, NEW))
    rows = {module: (imd_id, res_id) for module, imd_id, res_id in cr.fetchall()}
    old_row, new_row = rows.get(OLD), rows.get(NEW)

    if old_row and new_row:
        new_imd_id, new_seq_id = new_row
        # Drop the duplicate and the Postgres sequence Odoo created behind it.
        cr.execute("DELETE FROM ir_model_data WHERE id = %s", (new_imd_id,))
        cr.execute("DELETE FROM ir_sequence WHERE id = %s", (new_seq_id,))
        cr.execute("DROP SEQUENCE IF EXISTS ir_sequence_%03d" % new_seq_id)
        cr.execute("UPDATE ir_model_data SET module = %s WHERE id = %s",
                   (NEW, old_row[0]))
        adopted = 'kept the original, discarded the duplicate'
    elif old_row:
        cr.execute("UPDATE ir_model_data SET module = %s WHERE id = %s",
                   (NEW, old_row[0]))
        adopted = 'renamed the original'
    else:
        adopted = 'nothing to adopt'

    cr.execute("SELECT count(*) FROM ir_sequence WHERE code = %s", (SEQUENCE_CODE,))
    count = cr.fetchone()[0]
    if count != 1:
        raise AssertionError(
            "Expected exactly one '%s' sequence after Wave 2, found %s. Two "
            "sequences sharing a code means next_by_code picks one of them at "
            "random." % (SEQUENCE_CODE, count))
    return adopted


def migrate(cr, version):
    if not version:
        # Fresh install: there is nothing to hand over, and `atmta_project_core`
        # will create the identifiers itself.
        return

    renamed, deduped = _hand_over(cr, MODEL_XMLIDS)

    # What the self-healing path is left to deal with, reported rather than
    # assumed.
    cr.execute("""
        SELECT count(*) FROM ir_model_data d
        WHERE d.module = %s AND d.model = 'ir.model.fields'
          AND d.res_id IN (
              SELECT f.id FROM ir_model_fields f
              JOIN ir_model m ON m.id = f.model_id
              WHERE m.model = ANY(%s))
    """, (OLD, list(MODELS)))
    stale_fields = cr.fetchone()[0]

    retired = _retire_superseded_rules(cr)
    adopted = _adopt_project_sequence(cr)

    _logger.info(
        "ATMTA V2 Wave 2: handed %s model identifier(s) over to %s "
        "(%s renamed, %s de-duplicated). Retired %s superseded record rule(s). "
        "Project code sequence: %s. "
        "%s field identifier(s) remain owned by %s and will be re-claimed or "
        "dropped by _process_end.",
        renamed + deduped, NEW, renamed, deduped, retired, adopted,
        stale_fields, OLD)

    # -- Assert the things this migration must not have broken ---------------
    cr.execute("SELECT model FROM ir_model WHERE model = ANY(%s)", (list(MODELS),))
    found = {row[0] for row in cr.fetchall()}
    missing = set(MODELS) - found
    if missing:
        raise AssertionError(
            "Wave 2 migration lost the model definition(s) %s." % sorted(missing))

    for table in ('realestate_project', 'realestate_phase',
                  'realestate_project_boundary_point'):
        cr.execute("SELECT to_regclass(%s)", (table,))
        if cr.fetchone()[0] is None:
            raise AssertionError(
                "Wave 2 migration lost the table %s." % table)

    cr.execute("""
        SELECT count(*) FROM ir_model_data
        WHERE module = %s AND name = ANY(%s)
    """, (NEW, list(MODEL_XMLIDS)))
    if cr.fetchone()[0] != len(MODEL_XMLIDS):
        raise AssertionError(
            "Wave 2 migration did not leave all %s model identifiers with %s."
            % (len(MODEL_XMLIDS), NEW))

    # Exactly one global company rule per core model, owned by the new module.
    for model in MODELS:
        cr.execute("""
            SELECT count(*) FROM ir_rule r
            JOIN ir_model m ON m.id = r.model_id
            WHERE m.model = %s AND r.global = true
        """, (model,))
        count = cr.fetchone()[0]
        if count != 1:
            raise AssertionError(
                "Expected exactly one global company rule on %s after Wave 2, "
                "found %s." % (model, count))
