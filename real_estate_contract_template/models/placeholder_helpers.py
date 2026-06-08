"""Helpers for the placeholder catalog + auto-fill flow.

Pure functions, no Odoo state — the helpers are imported by the catalog and
auto-fill wizards. They:
  * enumerate fields of a target model and emit {{ record.path }} expressions
  * scan a .docx for [Bracketed Tokens]
  * fuzzy-match tokens against the field catalog
  * rewrite the .docx in-place, swapping [Token] for {{ jinja_expr }}
"""
import io
import re
from difflib import SequenceMatcher

try:
    from docx import Document
except ImportError:
    Document = None


# ---------------------------------------------------------------------------
# Field catalog
# ---------------------------------------------------------------------------

# Field types we never want to expose — too noisy or internal.
_SKIP_FIELDS = {
    'id', 'create_uid', 'write_uid', 'create_date', 'write_date',
    '__last_update', 'message_ids', 'message_follower_ids',
    'message_attachment_count', 'message_main_attachment_id',
    'message_is_follower', 'message_partner_ids', 'message_has_error',
    'message_has_error_counter', 'message_has_sms_error',
    'message_needaction', 'message_needaction_counter', 'message_unread',
    'message_unread_counter', 'activity_ids', 'activity_state',
    'activity_user_id', 'activity_type_id', 'activity_type_icon',
    'activity_date_deadline', 'activity_summary', 'activity_exception_decoration',
    'activity_exception_icon', 'activity_calendar_event_id',
    'access_token', 'access_url', 'access_warning',
    'website_message_ids', 'has_message', 'rating_ids',
}

# Subfield drill-down for relational fields. Only these names get expanded
# when we walk past depth 0.
_RELATIONAL_SUBFIELDS = (
    'name', 'display_name', 'ref', 'code',
    'email', 'phone', 'mobile', 'vat',
    'street', 'street2', 'city', 'zip', 'country_id',
    'partner_id',
)


def build_field_catalog(env, model_name, depth=1):
    """Return a list of catalog rows for ``model_name``.

    Each row is a dict:
      {
        'path': 'partner_id.name',
        'jinja': '{{ record.partner_id.name }}',
        'label': 'Customer / Name',
        'help': 'The customer linked to this contract',
        'type': 'char',
        'group': 'Customer',
        'searchable': 'partner customer name',
      }

    ``depth`` controls how many levels of many2one drilldown to emit
    (0 = own fields only).
    """
    if model_name not in env:
        return []
    Model = env[model_name]
    rows = []
    rows.append({
        'path': 'display_name',
        'jinja': '{{ record.display_name }}',
        'label': 'Record / Display Name',
        'help': 'Human-readable identifier of this record.',
        'type': 'char',
        'group': 'Identity',
        'searchable': _searchable('record display name identifier'),
    })
    _walk_fields(env, Model, rows, prefix='', label_prefix='', depth=depth)
    return rows


def _walk_fields(env, Model, rows, prefix, label_prefix, depth):
    fields_info = Model.fields_get()
    for fname, fdef in sorted(fields_info.items()):
        if fname in _SKIP_FIELDS:
            continue
        ftype = fdef.get('type')
        if ftype in ('binary',):
            continue
        path = f"{prefix}{fname}" if prefix else fname
        label_self = fdef.get('string') or fname
        label = f"{label_prefix}{label_self}" if label_prefix else label_self
        help_text = fdef.get('help') or ''

        if ftype == 'many2one':
            comodel = fdef.get('relation')
            rows.append({
                'path': f"{path}.display_name",
                'jinja': "{{ record." + path + ".display_name }}",
                'label': f"{label} (display name)",
                'help': help_text,
                'type': 'many2one',
                'group': _group_for(label),
                'searchable': _searchable(f"{label} {fname} display name"),
            })
            if depth > 0 and comodel and comodel in env:
                CoModel = env[comodel]
                sub_info = CoModel.fields_get(_RELATIONAL_SUBFIELDS)
                for sub_name in _RELATIONAL_SUBFIELDS:
                    if sub_name not in sub_info:
                        continue
                    sub_def = sub_info[sub_name]
                    sub_label = sub_def.get('string') or sub_name
                    sub_path = f"{path}.{sub_name}"
                    sub_type = sub_def.get('type')
                    if sub_type == 'many2one':
                        rows.append({
                            'path': f"{sub_path}.display_name",
                            'jinja': "{{ record." + sub_path + ".display_name }}",
                            'label': f"{label} / {sub_label}",
                            'help': sub_def.get('help') or '',
                            'type': 'many2one',
                            'group': _group_for(label),
                            'searchable': _searchable(f"{label} {sub_label} {fname} {sub_name}"),
                        })
                    else:
                        rows.append({
                            'path': sub_path,
                            'jinja': "{{ record." + sub_path + " }}",
                            'label': f"{label} / {sub_label}",
                            'help': sub_def.get('help') or '',
                            'type': sub_type or 'char',
                            'group': _group_for(label),
                            'searchable': _searchable(f"{label} {sub_label} {fname} {sub_name}"),
                        })
        elif ftype in ('one2many', 'many2many'):
            rows.append({
                'path': path,
                'jinja': "{% for line in record." + path + " %} {{ line.display_name }} {% endfor %}",
                'label': f"{label} (loop)",
                'help': help_text + " — Use docxtpl row-loops: place {%tr for line in record." + path + " %} in one table row and {%tr endfor %} in another.",
                'type': ftype,
                'group': _group_for(label),
                'searchable': _searchable(f"{label} {fname} list loop lines"),
            })
        else:
            rows.append({
                'path': path,
                'jinja': "{{ record." + path + " }}",
                'label': label,
                'help': help_text,
                'type': ftype or 'char',
                'group': _group_for(label),
                'searchable': _searchable(f"{label} {fname}"),
            })


def _group_for(label):
    lo = label.lower()
    if any(w in lo for w in ('customer', 'partner', 'client', 'buyer', 'tenant', 'landlord', 'developer', 'broker')):
        return 'Parties'
    if any(w in lo for w in ('date', 'period', 'duration', 'expiry')):
        return 'Dates'
    if any(w in lo for w in ('price', 'amount', 'value', 'total', 'fee', 'rent', 'commission', 'deposit', 'cost')):
        return 'Money'
    if any(w in lo for w in ('address', 'street', 'city', 'zip', 'country')):
        return 'Address'
    if any(w in lo for w in ('unit', 'property', 'project', 'phase', 'building')):
        return 'Property'
    if any(w in lo for w in ('payment', 'installment', 'invoice', 'plan')):
        return 'Payment'
    if any(w in lo for w in ('status', 'state', 'stage')):
        return 'Status'
    return 'Other'


# ---------------------------------------------------------------------------
# Token scanner
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r'\[([^\[\]\n]{1,80}?)\]')


def scan_docx_tokens(docx_bytes):
    """Return a list of unique bracketed tokens found in the .docx.

    Walks: body paragraphs, tables (incl. nested), section headers + footers.
    """
    if Document is None:
        return []
    doc = Document(io.BytesIO(docx_bytes))
    seen = []
    for text in _iter_doc_text(doc):
        for match in _TOKEN_RE.finditer(text):
            token = match.group(1).strip()
            if not token:
                continue
            if token not in seen:
                seen.append(token)
    return seen


def _iter_doc_text(doc):
    for p in doc.paragraphs:
        yield p.text
    for t in doc.tables:
        yield from _iter_table_text(t)
    for section in doc.sections:
        for hdr in (section.header, section.first_page_header, section.even_page_header):
            for p in hdr.paragraphs:
                yield p.text
            for t in hdr.tables:
                yield from _iter_table_text(t)
        for ftr in (section.footer, section.first_page_footer, section.even_page_footer):
            for p in ftr.paragraphs:
                yield p.text
            for t in ftr.tables:
                yield from _iter_table_text(t)


def _iter_table_text(table):
    for row in table.rows:
        for cell in row.cells:
            for p in cell.paragraphs:
                yield p.text
            for nested in cell.tables:
                yield from _iter_table_text(nested)


# ---------------------------------------------------------------------------
# Fuzzy matcher
# ---------------------------------------------------------------------------

_SYNONYMS = {
    # English
    'client': 'customer partner buyer',
    'buyer': 'customer partner client',
    'tenant': 'partner customer renter',
    'landlord': 'owner partner',
    'seller': 'partner vendor',
    'price': 'amount total value',
    'total': 'price amount value',
    'qty': 'quantity',
    # Arabic → English keywords
    'العميل': 'customer partner client',
    'المشتري': 'customer partner buyer client',
    'البائع': 'vendor partner seller',
    'المستأجر': 'tenant partner customer',
    'المؤجر': 'landlord owner partner',
    'الاسم': 'name',
    'العنوان': 'address street',
    'الهاتف': 'phone mobile',
    'البريد': 'email',
    'التاريخ': 'date',
    'السعر': 'price amount',
    'القيمة': 'value amount',
    'الإجمالي': 'total amount',
    'المبلغ': 'amount value total',
    'الوحدة': 'unit property',
    'المشروع': 'project',
    'العقد': 'contract',
    'الرقم': 'number reference code',
    'الحالة': 'state status',
    'الدفعة': 'payment installment',
    'القسط': 'installment payment',
    'العمولة': 'commission',
    'الإيجار': 'rent rental',
}


def _searchable(s):
    """Lowercase, strip punctuation, expand synonyms — used both for catalog
    rows and for token matching so both sides speak the same vocabulary."""
    s = (s or '').lower()
    s = re.sub(r'[_\-./]', ' ', s)
    s = re.sub(r'[^\w؀-ۿ\s]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    extra = []
    for word in s.split():
        if word in _SYNONYMS:
            extra.append(_SYNONYMS[word])
    if extra:
        s = s + ' ' + ' '.join(extra)
    return s


def match_token(token, catalog):
    """Score every catalog row against the token; return (best_row, confidence)
    where confidence is 0..100. Returns (None, 0) if catalog is empty."""
    if not catalog:
        return None, 0
    needle = _searchable(token)
    if not needle:
        return None, 0
    best_row, best_score = None, 0.0
    needle_words = set(needle.split())
    for row in catalog:
        hay = row['searchable']
        hay_words = set(hay.split())
        # Word-overlap component (0..1)
        if needle_words and hay_words:
            overlap = len(needle_words & hay_words) / len(needle_words)
        else:
            overlap = 0.0
        # Sequence-ratio component (0..1)
        ratio = SequenceMatcher(None, needle, hay).ratio()
        score = 0.7 * overlap + 0.3 * ratio
        # Tiny boost for shorter paths (prefer 'name' over 'partner_id.name'
        # when both could match equally).
        score += 0.02 * (1.0 / (1 + row['path'].count('.')))
        if score > best_score:
            best_score, best_row = score, row
    return best_row, int(round(best_score * 100))


# ---------------------------------------------------------------------------
# Rewriter
# ---------------------------------------------------------------------------

def rewrite_docx(docx_bytes, mapping):
    """Replace each ``[Token]`` in the docx with ``mapping[token]`` (already a
    full jinja expression like ``{{ record.partner_id.name }}``). Tokens not
    in ``mapping`` are left as ``[Token]`` so the user can spot them.

    Run-aware: searches the joined paragraph text, then writes the new text
    back by overwriting the first run and clearing the rest. Inline
    formatting WITHIN a replaced paragraph collapses to the first run's
    formatting; cross-paragraph formatting is preserved.
    """
    if Document is None:
        raise RuntimeError("python-docx is not installed.")
    doc = Document(io.BytesIO(docx_bytes))
    for p in doc.paragraphs:
        _rewrite_paragraph(p, mapping)
    for t in doc.tables:
        _rewrite_table(t, mapping)
    for section in doc.sections:
        for hdr in (section.header, section.first_page_header, section.even_page_header):
            for p in hdr.paragraphs:
                _rewrite_paragraph(p, mapping)
            for t in hdr.tables:
                _rewrite_table(t, mapping)
        for ftr in (section.footer, section.first_page_footer, section.even_page_footer):
            for p in ftr.paragraphs:
                _rewrite_paragraph(p, mapping)
            for t in ftr.tables:
                _rewrite_table(t, mapping)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _rewrite_paragraph(p, mapping):
    if not p.runs:
        return
    full = p.text
    if '[' not in full:
        return
    new_text, changed = _replace_in_text(full, mapping)
    if not changed:
        return
    p.runs[0].text = new_text
    for r in p.runs[1:]:
        r.text = ''


def _rewrite_table(table, mapping):
    for row in table.rows:
        for cell in row.cells:
            for p in cell.paragraphs:
                _rewrite_paragraph(p, mapping)
            for nested in cell.tables:
                _rewrite_table(nested, mapping)


def _replace_in_text(text, mapping):
    if not mapping or '[' not in text:
        return text, False
    changed = False

    def repl(m):
        nonlocal changed
        token = m.group(1).strip()
        if token in mapping:
            changed = True
            return mapping[token]
        return m.group(0)

    new_text = _TOKEN_RE.sub(repl, text)
    return new_text, changed
