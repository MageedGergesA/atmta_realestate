"""Download endpoints — contracts, invoices, payments, receipts, statements.

Two scopes:

* **By-ref** (per-customer) — every endpoint resolves the caller's target
  partner from ``<external_ref>`` and refuses to serve documents that
  don't belong to that partner. Any valid API key with scope
  ``real_estate_api`` may call these; each caller only ever sees the
  partner they own.

* **Top-level** (record-id addressed) — same documents but addressed by
  raw record id. Restricted to users in the
  ``group_realestate_api_downloads`` group (implied by Manager), so a
  compromised customer-integration key can't walk the whole invoice
  table.

Response shape:

* On success: ``application/pdf`` or ``application/zip`` binary with
  ``Content-Disposition: attachment; filename="..."`` and
  ``Cache-Control: private, no-store`` (financial/PII data must never
  linger in intermediaries or the browser).
* On error: the same JSON envelope every other API endpoint returns
  (``{"error": {"code": "...", "message": "..."}}``).

``?template=std|custom`` picks the renderer:

* Contracts — ``custom`` (default) uses the real-estate-specific QWeb
  template (rental: ``atmta_real_estate.action_report_contract``,
  sale: ``real_estate_api.action_report_sale_contract``). ``std`` is
  rejected — Odoo has no built-in "real estate contract" report.
* Invoices — both aliases resolve to ``account.account_invoices`` (the
  standard Odoo invoice PDF). The parameter is accepted for forward
  compatibility so callers who bake it into URLs don't break when a
  real-estate-specific per-invoice template is added later.

Errors:

* Contract/installment/payment id doesn't exist OR belongs to a
  different partner → **404** (never 403 — that would confirm the id
  exists).
* Installment/payment is not yet invoiced (``move_state != 'posted'``)
  → **404** ``not_invoiced``.
* Payment invoice not yet paid (``payment_state != 'paid'``) → **404**
  ``not_paid``.
* Report template missing (module not installed) → **404**
  ``report_missing``.
* Unhandled error rendering PDF → **500** ``render_failed``.
"""

import functools
import io
import logging
import re
import zipfile

import werkzeug.exceptions

from odoo import _, http
from odoo.exceptions import AccessError, MissingError, UserError, ValidationError
from odoo.http import Response, request

from ._base import (
    _apply_cors_headers,
    _apply_default_rate_limit,
    _authenticate_api_key,
    json_error,
)
from .api_v1_portal import _resolve_partner


_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Report xml_ids
# ---------------------------------------------------------------------------

REPORT_INVOICE_STD = 'account.account_invoices'
REPORT_PAYMENT_RECEIPT = 'account.action_report_payment_receipt'
REPORT_CONTRACT_RENTAL_CUSTOM = 'atmta_real_estate.action_realestate_contract_report'
REPORT_CONTRACT_SALE_CUSTOM = 'real_estate_api.action_report_sale_contract'
REPORT_STATEMENT = 'real_estate_api.action_report_partner_statement'
# atmta_real_estate ships these templates but its manifest does not
# currently register them. If/when the manifest adds them, these
# endpoints start working — until then `_render_pdf` returns 404
# `report_missing` so consumers get a clear failure.
REPORT_PAYMENT_SCHEDULE = 'atmta_real_estate.action_report_payment_schedule'
REPORT_FINANCIAL_SUMMARY = 'atmta_real_estate.action_report_financial_summary'
REPORT_CONTRACT_FULL = 'atmta_real_estate.action_report_contract_full'


VALID_TEMPLATES = ('std', 'custom')
VALID_KINDS = ('sale', 'rental')


_EXC_STATUS = {
    MissingError: 404,
    AccessError: 403,
    UserError: 400,
    ValidationError: 400,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sanitize_filename(name):
    """Strip chars that would break Content-Disposition or fs mounts."""
    if not name:
        return 'document'
    safe = re.sub(r'[^A-Za-z0-9._-]+', '_', str(name))[:120].strip('._')
    return safe or 'document'


def _binary_response(body, filename, content_type):
    resp = Response(body, status=200, content_type=content_type)
    resp.headers['Content-Length'] = str(len(body))
    resp.headers['Content-Disposition'] = (
        'attachment; filename="%s"' % _sanitize_filename(filename)
    )
    # Financial + PII documents must never sit in an intermediate cache.
    resp.headers['Cache-Control'] = 'private, no-store'
    return _apply_cors_headers(request.env, resp)


def _render_pdf(env, report_ref, res_ids):
    """Render a QWeb PDF report by xml_id → bytes.

    Missing template → 404 ``report_missing``. Render blow-up → propagates
    (the decorator turns unhandled exceptions into 500 ``render_failed``).
    """
    report = env.ref(report_ref, raise_if_not_found=False)
    if not report:
        raise werkzeug.exceptions.NotFound(_(
            "Report template not installed: %s") % report_ref)
    pdf_bytes, _ext = report._render_qweb_pdf(report_ref, res_ids)
    return pdf_bytes


def _pick_contract_report(kind, template):
    template = (template or 'custom').lower()
    if template != 'custom':
        raise werkzeug.exceptions.BadRequest(_(
            "'template=std' is not available for contracts — no Odoo-"
            "standard real-estate contract report exists. Use "
            "'template=custom' (the default)."
        ))
    if kind == 'sale':
        return REPORT_CONTRACT_SALE_CUSTOM
    return REPORT_CONTRACT_RENTAL_CUSTOM


def _pick_invoice_report(template):
    template = (template or 'std').lower()
    if template not in VALID_TEMPLATES:
        raise werkzeug.exceptions.BadRequest(_(
            "'template' must be 'std' or 'custom'."))
    # No real-estate-specific per-invoice template exists yet; both
    # aliases resolve to the standard Odoo invoice report. Keeping the
    # query param around so callers baking it into URLs don't break
    # when one is introduced.
    return REPORT_INVOICE_STD


def _model_for_kind(kind):
    return ('realestate.sale.contract' if kind == 'sale'
            else 'realestate.contract')


def _resolve_contract(env, cid, kind, partner=None):
    """Look up a contract by (id, kind). Optionally require ``partner``
    ownership. 404 on miss or mismatch — never leaks existence."""
    model = _model_for_kind(kind)
    if model not in env.registry:
        raise werkzeug.exceptions.NotFound(_(
            "Contract module not installed."))
    rec = env[model].sudo().browse(cid).exists()
    if not rec:
        raise werkzeug.exceptions.NotFound(_("Contract not found."))
    if partner is not None and rec.partner_id.id != partner.id:
        raise werkzeug.exceptions.NotFound(_("Contract not found."))
    return rec


def _resolve_contract_from_kw(env, cid, kw, partner=None):
    """Look up a contract using ?kind= from the query string if provided;
    otherwise auto-detect by searching both models scoped by the optional
    partner and returning the unique match.

    Returns (contract, kind) — callers that need kind later (report
    picker, zip builder) don't have to re-derive it.

    - 400 if ?kind= is present but not 'sale'/'rental'
    - 400 if kind is omitted and cid exists in BOTH models under the scope
    - 404 on miss / partner mismatch — never leaks existence
    """
    raw = (kw.get('kind') or '').strip().lower()
    if raw:
        if raw not in VALID_KINDS:
            raise werkzeug.exceptions.BadRequest(_(
                "'kind' must be 'sale' or 'rental'."))
        return _resolve_contract(env, cid, raw, partner=partner), raw
    candidates = []
    for k in VALID_KINDS:
        model = _model_for_kind(k)
        if model not in env.registry:
            continue
        rec = env[model].sudo().browse(cid).exists()
        if not rec:
            continue
        if partner is not None and rec.partner_id.id != partner.id:
            continue
        candidates.append((rec, k))
    if not candidates:
        raise werkzeug.exceptions.NotFound(_("Contract not found."))
    if len(candidates) > 1:
        raise werkzeug.exceptions.BadRequest(_(
            "Contract id %s exists as both sale and rental — "
            "add ?kind=sale or ?kind=rental to disambiguate.") % cid)
    return candidates[0]


def _resolve_installment(env, iid, partner=None):
    if 'realestate.sale.installment' not in env.registry:
        raise werkzeug.exceptions.NotFound(_(
            "Installments not available (real_estate_developer not installed)."))
    rec = env['realestate.sale.installment'].sudo().browse(iid).exists()
    if not rec:
        raise werkzeug.exceptions.NotFound(_("Installment not found."))
    if partner is not None and rec.partner_id.id != partner.id:
        raise werkzeug.exceptions.NotFound(_("Installment not found."))
    return rec


def _resolve_rental_payment(env, pid, partner=None):
    if 'realestate.contract.payment' not in env.registry:
        raise werkzeug.exceptions.NotFound(_(
            "Payments not available (atmta_real_estate not installed)."))
    rec = env['realestate.contract.payment'].sudo().browse(pid).exists()
    if not rec:
        raise werkzeug.exceptions.NotFound(_("Payment not found."))
    if partner is not None and rec.partner_id.id != partner.id:
        raise werkzeug.exceptions.NotFound(_("Payment not found."))
    return rec


def _require_posted_move(rec):
    """Return rec.move_id iff it exists and is posted, else 404."""
    move = rec.move_id if 'move_id' in rec._fields else False
    if not move:
        raise werkzeug.exceptions.NotFound(_(
            "This record has no invoice attached yet."))
    if move.state != 'posted':
        return _http_error_json('not_invoiced',
                                _("The invoice for this record is not "
                                  "yet posted."), status=404)
    return move


class _JsonHTTPError(werkzeug.exceptions.HTTPException):
    """HTTPException subclass that carries a machine-readable ``code`` for
    the JSON envelope, in addition to the HTTP status. ``HTTPException.name``
    is a read-only property in modern werkzeug, so subclassing is the only
    way to override it — the decorator reads ``_json_code`` if present."""
    def __init__(self, code, message, status):
        super().__init__(description=message)
        self.code = status  # HTTP status code
        self._json_code = code  # machine-readable error code


def _http_error_json(code, message, status):
    """Return a raisable HTTPException whose decorator handler will emit
    ``{"error": {"code": <code>, "message": <message>}}`` at ``status``."""
    return _JsonHTTPError(code, message, status)


def _resolve_brokerage_tx(env, txid, partner=None):
    """Locate a brokerage transaction; if partner is given, require that
    partner to be the seller or the buyer on the transaction (either
    role is legitimate for downloading their commission invoice)."""
    if 'real_estate_brokerage.transaction' not in env.registry:
        raise werkzeug.exceptions.NotFound(_(
            "Brokerage not available (real_estate_brokerage not installed)."))
    rec = env['real_estate_brokerage.transaction'].sudo().browse(txid).exists()
    if not rec:
        raise werkzeug.exceptions.NotFound(_("Transaction not found."))
    if partner is not None and partner.id not in (
            rec.seller_id.id, rec.buyer_id.id):
        raise werkzeug.exceptions.NotFound(_("Transaction not found."))
    return rec


def _contract_attachments(env, contract):
    """Return an ir.attachment recordset attached to the contract via
    ``attachment_ids``. Also includes chatter attachments (attached via
    the message thread) — customers uploading through the portal or
    Odoo backend end up in either bucket."""
    Att = env['ir.attachment'].sudo()
    explicit = contract.attachment_ids if 'attachment_ids' in contract._fields \
        else env['ir.attachment']
    chatter = Att.search([
        ('res_model', '=', contract._name),
        ('res_id', '=', contract.id),
    ])
    return (explicit | chatter).sorted('create_date')


def _property_attachments(env, prop):
    """Same idea for realestate.property.attachment_ids + chatter."""
    Att = env['ir.attachment'].sudo()
    explicit = prop.attachment_ids if 'attachment_ids' in prop._fields \
        else env['ir.attachment']
    chatter = Att.search([
        ('res_model', '=', prop._name),
        ('res_id', '=', prop.id),
    ])
    return (explicit | chatter).sorted('create_date')


def _attachment_response(att):
    """Stream a single ir.attachment back to the caller. Filters out
    anything without payload bytes (URL attachments etc.) — those aren't
    a downloadable file."""
    import base64
    if not att.exists():
        raise werkzeug.exceptions.NotFound(_("Attachment not found."))
    if att.type == 'url' or not att.datas:
        raise werkzeug.exceptions.NotFound(_(
            "Attachment has no downloadable payload."))
    body = base64.b64decode(att.datas)
    return _binary_response(
        body,
        att.name or ('attachment-%d' % att.id),
        content_type=att.mimetype or 'application/octet-stream',
    )


def _attachment_list_json(records):
    """Small JSON metadata list — used by the list endpoints so callers
    can pick which file(s) to fetch."""
    from odoo.http import Response
    import json as _json
    payload = {
        'results': [
            {
                'id': a.id,
                'name': a.name or '',
                'mimetype': a.mimetype or '',
                'size': a.file_size or 0,
                'create_date': a.create_date.isoformat() if a.create_date else None,
            }
            for a in records
            if a.type != 'url' and a.datas  # only downloadables
        ],
    }
    resp = Response(_json.dumps(payload), status=200,
                    content_type='application/json; charset=utf-8')
    resp.headers['Cache-Control'] = 'private, no-store'
    return _apply_cors_headers(request.env, resp)


# ---------------------------------------------------------------------------
# Decorator: auth + rate-limit + exception → JSON error envelope
# ---------------------------------------------------------------------------

def pdf_endpoint(*, require_downloads_group=False):
    """Wrap a download route.

    * OPTIONS → 204 preflight with CORS.
    * GET only.
    * Requires a valid API key (401 otherwise).
    * If ``require_downloads_group``, the key's user must be in
      ``group_realestate_api_downloads`` (403 otherwise).
    * Applies the default per-key rate limit (429 on cap hit).
    * Unhandled exceptions turn into a JSON error envelope with the
      right HTTP status — matches every other endpoint in this module.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            env = request.env
            try:
                if request.httprequest.method == 'OPTIONS':
                    return _apply_cors_headers(env, Response('', status=204))
                if request.httprequest.method != 'GET':
                    return json_error('method_not_allowed',
                                      _("Method not allowed."), status=405)

                # Auth
                try:
                    uid, fingerprint = _authenticate_api_key()
                except AccessError as e:
                    return json_error('unauthorized', str(e), status=401)
                if not uid:
                    return json_error('unauthorized',
                                      _("API key required."), status=401)
                request.update_env(user=uid)
                env = request.env

                # Group gate (top-level endpoints only)
                if require_downloads_group and not env.user.has_group(
                        'real_estate_api.group_realestate_api_downloads'):
                    return json_error('forbidden',
                                      _("Downloads-group membership required "
                                        "for top-level (non-by-ref) endpoints."),
                                      status=403)

                # Rate limit
                if not _apply_default_rate_limit(
                        env, authed_uid=uid, fingerprint=fingerprint):
                    return json_error('rate_limited',
                                      _("Too many requests."), status=429)

                return func(self, *args, **kwargs)

            except werkzeug.exceptions.HTTPException as exc:
                # _JsonHTTPError carries a machine-readable code; plain
                # HTTPExceptions fall back to their `.name` slug.
                code = getattr(exc, '_json_code', None)
                if not code:
                    code = (getattr(exc, 'name', '') or 'error'
                            ).lower().replace(' ', '_')
                return json_error(
                    code=code,
                    message=exc.description or getattr(exc, 'name', '') or '',
                    status=exc.code or 500,
                )
            except tuple(_EXC_STATUS.keys()) as exc:
                status = next(s for t, s in _EXC_STATUS.items()
                              if isinstance(exc, t))
                return json_error(type(exc).__name__.lower(), str(exc),
                                  status=status)
            except Exception:  # noqa: BLE001
                _logger.exception("Download error in %s", func.__name__)
                return json_error('render_failed',
                                  _("Failed to produce the requested "
                                    "document."), status=500)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# ZIP + statement builders
# ---------------------------------------------------------------------------

def _iter_contract_invoices(env, contract, kind):
    """Yield (filename, pdf_bytes) for every posted invoice under this
    contract. Silently skips any child record that fails to render — the
    goal of the zip is best-effort completeness, not all-or-nothing."""
    if kind == 'sale':
        if 'realestate.sale.installment' not in env.registry:
            return
        children = env['realestate.sale.installment'].sudo().search([
            ('sale_contract_id', '=', contract.id),
        ], order='date_due, id')
        label = 'Installment'
    else:
        if 'realestate.contract.payment' not in env.registry:
            return
        children = env['realestate.contract.payment'].sudo().search([
            ('contract_id', '=', contract.id),
        ], order='date_due, id')
        label = 'Payment'
    for child in children:
        move = child.move_id if 'move_id' in child._fields else False
        if not move or move.state != 'posted':
            continue
        try:
            pdf = _render_pdf(env, REPORT_INVOICE_STD, [move.id])
        except Exception:
            _logger.exception(
                "zip: failed to render invoice %s for %s#%s",
                move.name, label, child.id)
            continue
        fname = _sanitize_filename(
            'Invoice-%s-%s.pdf' % (label, move.name or child.id))
        yield fname, pdf


def _build_contract_zip(env, contract, kind):
    """Return (zip_bytes, filename). Bundles:

    * Contract PDF (custom template — sale or rental).
    * The contract's PRIMARY ``invoice_id`` PDF (if any + posted).
    * Every posted per-installment / per-payment invoice under it.
    * Every downloadable attachment on the contract (signed scans, IDs,
      etc.).

    Best-effort throughout — individual failures are logged and skipped
    rather than aborting the whole archive.
    """
    import base64
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        # 1. Contract PDF (custom template).
        try:
            report_ref = _pick_contract_report(kind, 'custom')
            pdf = _render_pdf(env, report_ref, [contract.id])
            zf.writestr(_sanitize_filename(
                'Contract-%s.pdf' % (contract.name or contract.id)), pdf)
        except Exception:
            _logger.exception(
                "zip: failed to render contract %s pdf", contract.id)

        # 2. Primary invoice on the contract itself (some flows bill
        #    the whole price as one move instead of / in addition to
        #    installments).
        primary = (contract.invoice_id
                   if 'invoice_id' in contract._fields else False)
        if primary and primary.state == 'posted':
            try:
                pdf = _render_pdf(env, REPORT_INVOICE_STD, [primary.id])
                zf.writestr(_sanitize_filename(
                    'Invoice-Primary-%s.pdf' % (primary.name or primary.id)),
                    pdf)
            except Exception:
                _logger.exception(
                    "zip: failed to render primary invoice %s",
                    primary.name)

        # 3. Every posted invoice under this contract.
        for fname, pdf in _iter_contract_invoices(env, contract, kind):
            zf.writestr(fname, pdf)

        # 4. Attachments (signed scans, IDs, permits, ...).
        for att in _contract_attachments(env, contract):
            if att.type == 'url' or not att.datas:
                continue
            try:
                zf.writestr(
                    _sanitize_filename(
                        'attachment-%d-%s' % (att.id, att.name or 'file')),
                    base64.b64decode(att.datas),
                )
            except Exception:
                _logger.exception(
                    "zip: failed to include attachment %s", att.id)

    body = buf.getvalue()
    fname = _sanitize_filename(
        'Contract-%s-documents.zip' % (contract.name or contract.id))
    return body, fname


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------

class DownloadsApiV1(http.Controller):

    # =====================================================================
    # BY-REF (per-customer) — scoped to the partner resolved from external_ref
    # =====================================================================

    # ---- Contract PDF -----------------------------------------------------

    @http.route('/api/v1/partners/by-ref/<string:external_ref>/'
                'contracts/<int:cid>/download',
                type='http', auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @pdf_endpoint()
    def by_ref_contract_pdf(self, external_ref, cid, **kw):
        env = request.env
        partner = _resolve_partner(env, external_ref)
        contract, kind = _resolve_contract_from_kw(env, cid, kw, partner=partner)
        report_ref = _pick_contract_report(kind, kw.get('template'))
        pdf = _render_pdf(env, report_ref, [contract.id])
        return _binary_response(
            pdf,
            'Contract-%s.pdf' % (contract.name or contract.id),
            content_type='application/pdf',
        )

    # ---- Installment invoice ---------------------------------------------

    @http.route('/api/v1/partners/by-ref/<string:external_ref>/'
                'installments/<int:iid>/invoice',
                type='http', auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @pdf_endpoint()
    def by_ref_installment_invoice(self, external_ref, iid, **kw):
        env = request.env
        partner = _resolve_partner(env, external_ref)
        inst = _resolve_installment(env, iid, partner=partner)
        move = _require_posted_move(inst)
        if isinstance(move, werkzeug.exceptions.HTTPException):
            raise move
        report_ref = _pick_invoice_report(kw.get('template'))
        pdf = _render_pdf(env, report_ref, [move.id])
        return _binary_response(
            pdf, 'Invoice-%s.pdf' % (move.name or move.id),
            content_type='application/pdf',
        )

    # ---- Rental payment invoice -------------------------------------------

    @http.route('/api/v1/partners/by-ref/<string:external_ref>/'
                'payments/<int:pid>/invoice',
                type='http', auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @pdf_endpoint()
    def by_ref_payment_invoice(self, external_ref, pid, **kw):
        env = request.env
        partner = _resolve_partner(env, external_ref)
        pay = _resolve_rental_payment(env, pid, partner=partner)
        move = _require_posted_move(pay)
        if isinstance(move, werkzeug.exceptions.HTTPException):
            raise move
        report_ref = _pick_invoice_report(kw.get('template'))
        pdf = _render_pdf(env, report_ref, [move.id])
        return _binary_response(
            pdf, 'Invoice-%s.pdf' % (move.name or move.id),
            content_type='application/pdf',
        )

    # ---- Payment receipt --------------------------------------------------

    @http.route('/api/v1/partners/by-ref/<string:external_ref>/'
                'payments/<int:pid>/receipt',
                type='http', auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @pdf_endpoint()
    def by_ref_payment_receipt(self, external_ref, pid, **kw):
        env = request.env
        partner = _resolve_partner(env, external_ref)
        pay = _resolve_rental_payment(env, pid, partner=partner)
        move = _require_posted_move(pay)
        if isinstance(move, werkzeug.exceptions.HTTPException):
            raise move
        if move.payment_state not in ('paid', 'in_payment', 'reversed'):
            raise _http_error_json(
                'not_paid',
                _("Invoice is not yet paid — no receipt to render."),
                status=404,
            )
        # Odoo's standard receipt report is scoped to account.payment,
        # not account.move. Find the payment(s) that reconciled this
        # invoice.
        payments = move._get_reconciled_payments() if hasattr(
            move, '_get_reconciled_payments') else move.env['account.payment']
        if not payments:
            # Fallback: some flows create payments through account.move
            # directly without a linked account.payment. In that case
            # render the invoice with its payment block as a "paid" PDF.
            pdf = _render_pdf(request.env, REPORT_INVOICE_STD, [move.id])
        else:
            pdf = _render_pdf(request.env, REPORT_PAYMENT_RECEIPT,
                              payments.ids)
        return _binary_response(
            pdf, 'Receipt-%s.pdf' % (move.name or move.id),
            content_type='application/pdf',
        )

    # ---- Zip bundle per contract ------------------------------------------

    @http.route('/api/v1/partners/by-ref/<string:external_ref>/'
                'contracts/<int:cid>/documents.zip',
                type='http', auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @pdf_endpoint()
    def by_ref_contract_zip(self, external_ref, cid, **kw):
        env = request.env
        partner = _resolve_partner(env, external_ref)
        contract, kind = _resolve_contract_from_kw(env, cid, kw, partner=partner)
        body, fname = _build_contract_zip(env, contract, kind)
        return _binary_response(body, fname, content_type='application/zip')

    # ---- Per-partner statement -------------------------------------------

    @http.route('/api/v1/partners/by-ref/<string:external_ref>/statement.pdf',
                type='http', auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @pdf_endpoint()
    def by_ref_statement(self, external_ref, **kw):
        env = request.env
        partner = _resolve_partner(env, external_ref)
        pdf = _render_pdf(env, REPORT_STATEMENT, [partner.id])
        return _binary_response(
            pdf, 'Statement-%s.pdf' % (partner.name or partner.id),
            content_type='application/pdf',
        )

    # ---- Primary contract invoice (contract.invoice_id) -------------------

    @http.route('/api/v1/partners/by-ref/<string:external_ref>/'
                'contracts/<int:cid>/invoice.pdf',
                type='http', auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @pdf_endpoint()
    def by_ref_contract_invoice(self, external_ref, cid, **kw):
        env = request.env
        partner = _resolve_partner(env, external_ref)
        contract, _kind = _resolve_contract_from_kw(env, cid, kw, partner=partner)
        move = contract.invoice_id if 'invoice_id' in contract._fields else False
        if not move:
            raise werkzeug.exceptions.NotFound(_(
                "This contract has no primary invoice attached."))
        if move.state != 'posted':
            raise _http_error_json(
                'not_invoiced',
                _("The primary invoice on this contract is not yet posted."),
                status=404,
            )
        report_ref = _pick_invoice_report(kw.get('template'))
        pdf = _render_pdf(env, report_ref, [move.id])
        return _binary_response(
            pdf, 'Invoice-%s.pdf' % (move.name or move.id),
            content_type='application/pdf',
        )

    # ---- Rental payment schedule (whole-contract atmta report) ----------

    @http.route('/api/v1/partners/by-ref/<string:external_ref>/'
                'contracts/<int:cid>/payment-schedule.pdf',
                type='http', auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @pdf_endpoint()
    def by_ref_payment_schedule(self, external_ref, cid, **kw):
        env = request.env
        partner = _resolve_partner(env, external_ref)
        # atmta_real_estate.action_report_payment_schedule is bound to
        # realestate.contract only.
        contract = _resolve_contract(env, cid, 'rental', partner=partner)
        pdf = _render_pdf(env, REPORT_PAYMENT_SCHEDULE, [contract.id])
        return _binary_response(
            pdf, 'PaymentSchedule-%s.pdf' % (contract.name or contract.id),
            content_type='application/pdf',
        )

    # ---- Financial summary (atmta report, rental) ------------------------

    @http.route('/api/v1/partners/by-ref/<string:external_ref>/'
                'contracts/<int:cid>/financial-summary.pdf',
                type='http', auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @pdf_endpoint()
    def by_ref_financial_summary(self, external_ref, cid, **kw):
        env = request.env
        partner = _resolve_partner(env, external_ref)
        contract = _resolve_contract(env, cid, 'rental', partner=partner)
        pdf = _render_pdf(env, REPORT_FINANCIAL_SUMMARY, [contract.id])
        return _binary_response(
            pdf,
            'FinancialSummary-%s.pdf' % (contract.name or contract.id),
            content_type='application/pdf',
        )

    # ---- Full contract summary (atmta report, rental) --------------------

    @http.route('/api/v1/partners/by-ref/<string:external_ref>/'
                'contracts/<int:cid>/full-summary.pdf',
                type='http', auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @pdf_endpoint()
    def by_ref_full_summary(self, external_ref, cid, **kw):
        env = request.env
        partner = _resolve_partner(env, external_ref)
        contract = _resolve_contract(env, cid, 'rental', partner=partner)
        pdf = _render_pdf(env, REPORT_CONTRACT_FULL, [contract.id])
        return _binary_response(
            pdf, 'ContractFull-%s.pdf' % (contract.name or contract.id),
            content_type='application/pdf',
        )

    # ---- Contract attachments --------------------------------------------

    @http.route('/api/v1/partners/by-ref/<string:external_ref>/'
                'contracts/<int:cid>/attachments',
                type='http', auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @pdf_endpoint()
    def by_ref_contract_attachments_list(self, external_ref, cid, **kw):
        env = request.env
        partner = _resolve_partner(env, external_ref)
        contract, _kind = _resolve_contract_from_kw(env, cid, kw, partner=partner)
        return _attachment_list_json(_contract_attachments(env, contract))

    @http.route('/api/v1/partners/by-ref/<string:external_ref>/'
                'contracts/<int:cid>/attachments/<int:aid>',
                type='http', auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @pdf_endpoint()
    def by_ref_contract_attachment_file(self, external_ref, cid, aid, **kw):
        env = request.env
        partner = _resolve_partner(env, external_ref)
        contract, _kind = _resolve_contract_from_kw(env, cid, kw, partner=partner)
        # Enforce ownership: the attachment id must belong to this contract.
        allowed = _contract_attachments(env, contract).ids
        if aid not in allowed:
            raise werkzeug.exceptions.NotFound(_("Attachment not found."))
        return _attachment_response(env['ir.attachment'].sudo().browse(aid))

    # ---- Property attachments --------------------------------------------

    @http.route('/api/v1/partners/by-ref/<string:external_ref>/'
                'properties/<int:pid>/attachments',
                type='http', auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @pdf_endpoint()
    def by_ref_property_attachments_list(self, external_ref, pid, **kw):
        env = request.env
        partner = _resolve_partner(env, external_ref)
        prop = self._resolve_partner_property(env, partner, pid)
        return _attachment_list_json(_property_attachments(env, prop))

    @http.route('/api/v1/partners/by-ref/<string:external_ref>/'
                'properties/<int:pid>/attachments/<int:aid>',
                type='http', auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @pdf_endpoint()
    def by_ref_property_attachment_file(self, external_ref, pid, aid, **kw):
        env = request.env
        partner = _resolve_partner(env, external_ref)
        prop = self._resolve_partner_property(env, partner, pid)
        allowed = _property_attachments(env, prop).ids
        if aid not in allowed:
            raise werkzeug.exceptions.NotFound(_("Attachment not found."))
        return _attachment_response(env['ir.attachment'].sudo().browse(aid))

    def _resolve_partner_property(self, env, partner, pid):
        """A property is 'this partner's' iff at least one sale or rental
        contract with matching partner_id targets it. Refuses otherwise —
        no fishing for other people's property docs."""
        prop = env['realestate.property'].sudo().browse(pid).exists()
        if not prop:
            raise werkzeug.exceptions.NotFound(_("Property not found."))
        SC = env.registry.get('realestate.sale.contract')
        RC = env.registry.get('realestate.contract')
        linked = False
        if SC:
            linked = bool(env['realestate.sale.contract'].sudo().search_count([
                ('partner_id', '=', partner.id),
                ('property_id', '=', prop.id),
            ]))
        if not linked and RC:
            # rental contracts link via property_ids (M2M) in some flows
            fields = env['realestate.contract']._fields
            if 'property_id' in fields:
                linked = bool(env['realestate.contract'].sudo().search_count([
                    ('partner_id', '=', partner.id),
                    ('property_id', '=', prop.id),
                ]))
            if not linked and 'property_ids' in fields:
                linked = bool(env['realestate.contract'].sudo().search_count([
                    ('partner_id', '=', partner.id),
                    ('property_ids', 'in', prop.id),
                ]))
        if not linked:
            raise werkzeug.exceptions.NotFound(_("Property not found."))
        return prop

    # ---- Brokerage commission invoice ------------------------------------

    @http.route('/api/v1/partners/by-ref/<string:external_ref>/'
                'brokerage/<int:txid>/invoice.pdf',
                type='http', auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @pdf_endpoint()
    def by_ref_brokerage_invoice(self, external_ref, txid, **kw):
        env = request.env
        partner = _resolve_partner(env, external_ref)
        tx = _resolve_brokerage_tx(env, txid, partner=partner)
        move = tx.invoice_id if 'invoice_id' in tx._fields else False
        if not move:
            raise werkzeug.exceptions.NotFound(_(
                "This transaction has no invoice attached."))
        if move.state != 'posted':
            raise _http_error_json(
                'not_invoiced',
                _("The commission invoice is not yet posted."),
                status=404,
            )
        report_ref = _pick_invoice_report(kw.get('template'))
        pdf = _render_pdf(env, report_ref, [move.id])
        return _binary_response(
            pdf, 'Commission-%s.pdf' % (move.name or move.id),
            content_type='application/pdf',
        )

    # =====================================================================
    # TOP-LEVEL (manager-scoped) — addressed by raw record id
    # =====================================================================

    @http.route('/api/v1/contracts/<int:cid>/download',
                type='http', auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @pdf_endpoint(require_downloads_group=True)
    def contract_pdf(self, cid, **kw):
        env = request.env
        contract, kind = _resolve_contract_from_kw(env, cid, kw)
        report_ref = _pick_contract_report(kind, kw.get('template'))
        pdf = _render_pdf(env, report_ref, [contract.id])
        return _binary_response(
            pdf, 'Contract-%s.pdf' % (contract.name or contract.id),
            content_type='application/pdf',
        )

    @http.route('/api/v1/installments/<int:iid>/invoice',
                type='http', auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @pdf_endpoint(require_downloads_group=True)
    def installment_invoice(self, iid, **kw):
        env = request.env
        inst = _resolve_installment(env, iid)
        move = _require_posted_move(inst)
        if isinstance(move, werkzeug.exceptions.HTTPException):
            raise move
        report_ref = _pick_invoice_report(kw.get('template'))
        pdf = _render_pdf(env, report_ref, [move.id])
        return _binary_response(
            pdf, 'Invoice-%s.pdf' % (move.name or move.id),
            content_type='application/pdf',
        )

    @http.route('/api/v1/payments/<int:pid>/invoice',
                type='http', auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @pdf_endpoint(require_downloads_group=True)
    def payment_invoice(self, pid, **kw):
        env = request.env
        pay = _resolve_rental_payment(env, pid)
        move = _require_posted_move(pay)
        if isinstance(move, werkzeug.exceptions.HTTPException):
            raise move
        report_ref = _pick_invoice_report(kw.get('template'))
        pdf = _render_pdf(env, report_ref, [move.id])
        return _binary_response(
            pdf, 'Invoice-%s.pdf' % (move.name or move.id),
            content_type='application/pdf',
        )

    @http.route('/api/v1/payments/<int:pid>/receipt',
                type='http', auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @pdf_endpoint(require_downloads_group=True)
    def payment_receipt(self, pid, **kw):
        env = request.env
        pay = _resolve_rental_payment(env, pid)
        move = _require_posted_move(pay)
        if isinstance(move, werkzeug.exceptions.HTTPException):
            raise move
        if move.payment_state not in ('paid', 'in_payment', 'reversed'):
            raise _http_error_json(
                'not_paid',
                _("Invoice is not yet paid — no receipt to render."),
                status=404,
            )
        payments = move._get_reconciled_payments() if hasattr(
            move, '_get_reconciled_payments') else move.env['account.payment']
        if not payments:
            pdf = _render_pdf(env, REPORT_INVOICE_STD, [move.id])
        else:
            pdf = _render_pdf(env, REPORT_PAYMENT_RECEIPT, payments.ids)
        return _binary_response(
            pdf, 'Receipt-%s.pdf' % (move.name or move.id),
            content_type='application/pdf',
        )

    @http.route('/api/v1/contracts/<int:cid>/documents.zip',
                type='http', auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @pdf_endpoint(require_downloads_group=True)
    def contract_zip(self, cid, **kw):
        env = request.env
        contract, kind = _resolve_contract_from_kw(env, cid, kw)
        body, fname = _build_contract_zip(env, contract, kind)
        return _binary_response(body, fname, content_type='application/zip')

    # ---- Top-level mirrors of the 9 additions ---------------------------

    @http.route('/api/v1/contracts/<int:cid>/invoice.pdf',
                type='http', auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @pdf_endpoint(require_downloads_group=True)
    def contract_invoice(self, cid, **kw):
        env = request.env
        contract, _kind = _resolve_contract_from_kw(env, cid, kw)
        move = contract.invoice_id if 'invoice_id' in contract._fields else False
        if not move:
            raise werkzeug.exceptions.NotFound(_(
                "This contract has no primary invoice attached."))
        if move.state != 'posted':
            raise _http_error_json(
                'not_invoiced',
                _("The primary invoice on this contract is not yet posted."),
                status=404,
            )
        report_ref = _pick_invoice_report(kw.get('template'))
        pdf = _render_pdf(env, report_ref, [move.id])
        return _binary_response(
            pdf, 'Invoice-%s.pdf' % (move.name or move.id),
            content_type='application/pdf',
        )

    @http.route('/api/v1/contracts/<int:cid>/payment-schedule.pdf',
                type='http', auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @pdf_endpoint(require_downloads_group=True)
    def contract_payment_schedule(self, cid, **kw):
        env = request.env
        contract = _resolve_contract(env, cid, 'rental')
        pdf = _render_pdf(env, REPORT_PAYMENT_SCHEDULE, [contract.id])
        return _binary_response(
            pdf, 'PaymentSchedule-%s.pdf' % (contract.name or contract.id),
            content_type='application/pdf',
        )

    @http.route('/api/v1/contracts/<int:cid>/financial-summary.pdf',
                type='http', auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @pdf_endpoint(require_downloads_group=True)
    def contract_financial_summary(self, cid, **kw):
        env = request.env
        contract = _resolve_contract(env, cid, 'rental')
        pdf = _render_pdf(env, REPORT_FINANCIAL_SUMMARY, [contract.id])
        return _binary_response(
            pdf, 'FinancialSummary-%s.pdf' % (contract.name or contract.id),
            content_type='application/pdf',
        )

    @http.route('/api/v1/contracts/<int:cid>/full-summary.pdf',
                type='http', auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @pdf_endpoint(require_downloads_group=True)
    def contract_full_summary(self, cid, **kw):
        env = request.env
        contract = _resolve_contract(env, cid, 'rental')
        pdf = _render_pdf(env, REPORT_CONTRACT_FULL, [contract.id])
        return _binary_response(
            pdf, 'ContractFull-%s.pdf' % (contract.name or contract.id),
            content_type='application/pdf',
        )

    @http.route('/api/v1/contracts/<int:cid>/attachments',
                type='http', auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @pdf_endpoint(require_downloads_group=True)
    def contract_attachments_list(self, cid, **kw):
        env = request.env
        contract, _kind = _resolve_contract_from_kw(env, cid, kw)
        return _attachment_list_json(_contract_attachments(env, contract))

    @http.route('/api/v1/contracts/<int:cid>/attachments/<int:aid>',
                type='http', auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @pdf_endpoint(require_downloads_group=True)
    def contract_attachment_file(self, cid, aid, **kw):
        env = request.env
        contract, _kind = _resolve_contract_from_kw(env, cid, kw)
        if aid not in _contract_attachments(env, contract).ids:
            raise werkzeug.exceptions.NotFound(_("Attachment not found."))
        return _attachment_response(env['ir.attachment'].sudo().browse(aid))

    @http.route('/api/v1/properties/<int:pid>/attachments',
                type='http', auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @pdf_endpoint(require_downloads_group=True)
    def property_attachments_list(self, pid, **kw):
        env = request.env
        prop = env['realestate.property'].sudo().browse(pid).exists()
        if not prop:
            raise werkzeug.exceptions.NotFound(_("Property not found."))
        return _attachment_list_json(_property_attachments(env, prop))

    @http.route('/api/v1/properties/<int:pid>/attachments/<int:aid>',
                type='http', auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @pdf_endpoint(require_downloads_group=True)
    def property_attachment_file(self, pid, aid, **kw):
        env = request.env
        prop = env['realestate.property'].sudo().browse(pid).exists()
        if not prop:
            raise werkzeug.exceptions.NotFound(_("Property not found."))
        if aid not in _property_attachments(env, prop).ids:
            raise werkzeug.exceptions.NotFound(_("Attachment not found."))
        return _attachment_response(env['ir.attachment'].sudo().browse(aid))

    @http.route('/api/v1/brokerage/<int:txid>/invoice.pdf',
                type='http', auth='public', methods=['GET', 'OPTIONS'],
                csrf=False, save_session=False)
    @pdf_endpoint(require_downloads_group=True)
    def brokerage_invoice(self, txid, **kw):
        env = request.env
        tx = _resolve_brokerage_tx(env, txid)
        move = tx.invoice_id if 'invoice_id' in tx._fields else False
        if not move:
            raise werkzeug.exceptions.NotFound(_(
                "This transaction has no invoice attached."))
        if move.state != 'posted':
            raise _http_error_json(
                'not_invoiced',
                _("The commission invoice is not yet posted."),
                status=404,
            )
        report_ref = _pick_invoice_report(kw.get('template'))
        pdf = _render_pdf(env, report_ref, [move.id])
        return _binary_response(
            pdf, 'Commission-%s.pdf' % (move.name or move.id),
            content_type='application/pdf',
        )
