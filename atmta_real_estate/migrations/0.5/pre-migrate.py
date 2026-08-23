# -*- coding: utf-8 -*-
"""Wave 3 — hand the Property Core identifiers over to their new owner.

``realestate.property``, ``property.type``, ``property.usage`` and
``property.image`` are now declared by ``atmta_property_core``. The models,
their tables, their columns and every row in them are untouched; what moves is
the *module that owns the identifier*.

This is the Wave 2 mechanism, unchanged, applied to four models instead of
three. ``IrModelData._process_end`` would very nearly do it by itself — it drops
an XML-ID an updated module no longer produces, and at
``ir_model.py:2644-2652`` keeps the underlying record whenever another XML-ID
still points at it — but the three identifiers are referenced by sixteen ACL
rows across two modules, and a wrong inference there fails as silent access
loss rather than as a crash. So the intent is stated directly and the result
asserted.

Two things this migration must not break that Wave 2 did not have to think
about:

* ``realestate.property`` uses ``_inherits = {'product.template':
  'product_tmpl_id'}``. Every property row owns a ``product.template`` row, and
  a mistake in model ownership could orphan or delete them. The count is
  asserted before and after.
* ``_parent_store = True``. The ``parent_path`` column encodes the hierarchy;
  nothing here touches it, and that it still holds is asserted.
"""

import logging

_logger = logging.getLogger(__name__)

OLD = 'atmta_real_estate'
NEW = 'atmta_property_core'

MODELS = (
    'realestate.property',
    'property.type',
    'property.usage',
    'property.image',
)
MODEL_XMLIDS = tuple('model_' + m.replace('.', '_') for m in MODELS)
TABLES = ('realestate_property', 'property_type', 'property_usage', 'property_image')

SEQUENCE_XMLID = 'seq_realestate_property_code'
SEQUENCE_CODE = 'realestate.property.code'


def _hand_over(cr, names, model=None):
    """Move ``ir_model_data`` rows OLD.<name> -> NEW.<name>, collision-safe.

    If the new module already claimed the identifier (it loads first, so this is
    the normal case on an upgrade) the stale row is deleted rather than renamed:
    ``ir_model_data`` carries UNIQUE(module, name) and an UPDATE would hit it.
    """
    renamed = deduped = 0
    extra = " AND o.model = %s" if model else ""
    params = [NEW, OLD, list(names)] + ([model] if model else [])
    cr.execute("""
        SELECT o.id, o.name, n.id
        FROM ir_model_data o
        LEFT JOIN ir_model_data n ON n.module = %%s AND n.name = o.name
        WHERE o.module = %%s AND o.name = ANY(%%s)%s
    """ % extra, params)
    for old_id, name, new_id in cr.fetchall():
        if new_id:
            cr.execute("DELETE FROM ir_model_data WHERE id = %s", (old_id,))
            deduped += 1
        else:
            cr.execute("UPDATE ir_model_data SET module = %s WHERE id = %s",
                       (NEW, old_id))
            renamed += 1
    return renamed, deduped


def _adopt_property_sequence(cr):
    """Give the property code sequence to its new owner without resetting it.

    ``atmta_property_core`` loads first and declares the sequence too, so by the
    time this runs there are two rows with the code ``realestate.property.code``:
    the original, whose counter has already issued every existing PROP- code,
    and a new one sitting at 1. Keep the original — ``realestate_property``
    carries a uniqueness constraint on the code, so a sequence restarting at 1
    would re-issue codes that already exist. Keeping the original row leaves its
    id, its backing Postgres sequence and its counter exactly as they were; only
    the identifier changes hands.
    """
    cr.execute("""
        SELECT module, id, res_id FROM ir_model_data
        WHERE model = 'ir.sequence' AND name = %s AND module IN (%s, %s)
    """, (SEQUENCE_XMLID, OLD, NEW))
    rows = {m: (i, r) for m, i, r in cr.fetchall()}
    old_row, new_row = rows.get(OLD), rows.get(NEW)

    if old_row and new_row:
        new_imd_id, new_seq_id = new_row
        cr.execute("DELETE FROM ir_model_data WHERE id = %s", (new_imd_id,))
        cr.execute("DELETE FROM ir_sequence WHERE id = %s", (new_seq_id,))
        cr.execute("DROP SEQUENCE IF EXISTS ir_sequence_%03d" % new_seq_id)
        cr.execute("UPDATE ir_model_data SET module = %s WHERE id = %s",
                   (NEW, old_row[0]))
        outcome = 'kept the original, discarded the duplicate'
    elif old_row:
        cr.execute("UPDATE ir_model_data SET module = %s WHERE id = %s",
                   (NEW, old_row[0]))
        outcome = 'renamed the original'
    else:
        outcome = 'nothing to adopt'

    cr.execute("SELECT count(*) FROM ir_sequence WHERE code = %s", (SEQUENCE_CODE,))
    count = cr.fetchone()[0]
    if count != 1:
        raise AssertionError(
            "Expected exactly one '%s' sequence after Wave 3, found %s. Two "
            "sequences sharing a code means next_by_code picks one at random."
            % (SEQUENCE_CODE, count))
    return outcome


def _retire_superseded_rule(cr):
    """Retire the property company rule this module used to own.

    Its file carries ``noupdate="1"`` and ``_process_end`` skips no-update
    records entirely, so removing it from the XML alone would strand a duplicate
    global rule in every existing database forever.

    Refuses to act unless the replacement is already in place: an isolation rule
    is the one thing that must never be removed optimistically.
    """
    cr.execute("""SELECT count(*) FROM ir_model_data
                  WHERE module = %s AND model = 'ir.rule' AND name = 'rule_property_company'""",
               (NEW,))
    if not cr.fetchone()[0]:
        raise AssertionError(
            "Refusing to retire the property company rule: %s has not created "
            "its replacement. Removing it now would leave realestate.property "
            "with no company isolation at all." % NEW)
    cr.execute("""SELECT id, res_id FROM ir_model_data
                  WHERE module = %s AND model = 'ir.rule' AND name = 'rule_property_company'""",
               (OLD,))
    rows = cr.fetchall()
    if rows:
        cr.execute("DELETE FROM ir_rule WHERE id = ANY(%s)", ([r[1] for r in rows],))
        cr.execute("DELETE FROM ir_model_data WHERE id = ANY(%s)", ([r[0] for r in rows],))
    return len(rows)


def migrate(cr, version):
    if not version:
        return

    cr.execute("SELECT count(*) FROM product_template")
    templates_before = cr.fetchone()[0]

    renamed, deduped = _hand_over(cr, MODEL_XMLIDS, model='ir.model')
    adopted = _adopt_property_sequence(cr)
    retired = _retire_superseded_rule(cr)

    cr.execute("""
        SELECT count(*) FROM ir_model_data d
        WHERE d.module = %s AND d.model = 'ir.model.fields'
          AND d.res_id IN (SELECT f.id FROM ir_model_fields f
                           JOIN ir_model m ON m.id = f.model_id
                           WHERE m.model = ANY(%s))
    """, (OLD, list(MODELS)))
    stale_fields = cr.fetchone()[0]

    _logger.info(
        "ATMTA V2 Wave 3: handed %s model identifier(s) over to %s "
        "(%s renamed, %s de-duplicated). Property code sequence: %s. "
        "Retired %s superseded record rule(s). %s field identifier(s) remain "
        "owned by %s and will be re-claimed or dropped by _process_end.",
        renamed + deduped, NEW, renamed, deduped, adopted, retired,
        stale_fields, OLD)

    # -- what this migration must not have broken -------------------------
    cr.execute("SELECT model FROM ir_model WHERE model = ANY(%s)", (list(MODELS),))
    missing = set(MODELS) - {r[0] for r in cr.fetchall()}
    if missing:
        raise AssertionError(
            "Wave 3 migration lost the model definition(s) %s." % sorted(missing))

    for table in TABLES:
        cr.execute("SELECT to_regclass(%s)", (table,))
        if cr.fetchone()[0] is None:
            raise AssertionError("Wave 3 migration lost the table %s." % table)

    cr.execute("SELECT count(*) FROM product_template")
    templates_after = cr.fetchone()[0]
    if templates_after != templates_before:
        raise AssertionError(
            "Wave 3 migration changed the product.template count from %s to %s. "
            "realestate.property delegates to product.template, so a template "
            "disappearing means a property lost the record it inherits from."
            % (templates_before, templates_after))

    cr.execute("""SELECT count(*) FROM realestate_property p
                  LEFT JOIN product_template t ON t.id = p.product_tmpl_id
                  WHERE p.product_tmpl_id IS NULL OR t.id IS NULL""")
    orphans = cr.fetchone()[0]
    if orphans:
        raise AssertionError(
            "%s propert(ies) have no delegated product.template after the Wave 3 "
            "migration." % orphans)

    cr.execute("SELECT count(*) FROM realestate_property WHERE parent_path IS NULL")
    if cr.fetchone()[0]:
        raise AssertionError(
            "Wave 3 migration left realestate.property rows with a NULL "
            "parent_path; the _parent_store hierarchy is broken.")

    cr.execute("""SELECT count(*) FROM ir_model_data
                  WHERE module = %s AND name = ANY(%s)""", (NEW, list(MODEL_XMLIDS)))
    if cr.fetchone()[0] != len(MODEL_XMLIDS):
        raise AssertionError(
            "Wave 3 migration did not leave all %s model identifiers with %s."
            % (len(MODEL_XMLIDS), NEW))

    cr.execute("""SELECT count(*) FROM ir_rule r JOIN ir_model m ON m.id = r.model_id
                  WHERE m.model = 'realestate.property' AND r.global = true""")
    count = cr.fetchone()[0]
    if count != 1:
        raise AssertionError(
            "Expected exactly one global company rule on realestate.property "
            "after Wave 3, found %s." % count)
