"""``/api/v1/partners/by-ref/<external_ref>/*`` — customer portal reads.

The 3rd-party site keeps its own customer accounts. Once it has synced a
partner via ``POST /api/v1/partners`` (getting back an ``external_ref``),
it can now pull that customer's:

* interests    (crm.lead   rows they submitted)
* viewings     (realestate.viewing appointments)
* contracts    (realestate.sale.contract + realestate.contract, unified)
* installments (realestate.sale.installment schedule)
* payments     (realestate.contract.payment schedule)

All endpoints are **Bearer-auth required** — customer PII + financial data.
All endpoints are **strict**: unknown ``external_ref`` → 404, never an
empty list, per the ``no_silent_fallbacks`` rule.

The token scopes access to the caller (3rd-party site), which is why we
resolve the partner by ``external_ref`` and filter each collection by
``partner_id`` in the same request — no cross-partner leak path.
"""

import re
from datetime import datetime

import werkzeug.exceptions

from odoo import _, http
from odoo.http import request

from ._base import (
    _int_arg,
    json_endpoint,
    json_response,
    parse_pagination,
)


EXTERNAL_REF_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")

HARD_LIMIT_MAX = 500


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_partner(env, external_ref):
    """Strict lookup by ``realestate_api_external_ref``.

    ``NotFound`` (404) if the ref is malformed or unknown — never returns
    a "best guess" match.
    """
    if not external_ref or not EXTERNAL_REF_RE.match(external_ref):
        raise werkzeug.exceptions.BadRequest(_(
            "'external_ref' must be 1-128 chars of [A-Za-z0-9._:-]."))
    partner = env['res.partner'].sudo().search([
        ('realestate_api_source', '=', True),
        ('realestate_api_external_ref', '=', external_ref),
    ], limit=1)
    if not partner:
        raise werkzeug.exceptions.NotFound(_(
            "No API-created partner with external_ref=%s.") % external_ref)
    return partner


def _parse_date(raw, arg_name):
    if not raw:
        return None
    try:
        return datetime.strptime(raw, '%Y-%m-%d').date()
    except ValueError:
        raise werkzeug.exceptions.BadRequest(_(
            "'%s' must be YYYY-MM-DD.") % arg_name)


def _iso(dt):
    if not dt:
        return None
    if hasattr(dt, 'isoformat'):
        return dt.isoformat()
    return str(dt)


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------

class PortalApiV1(http.Controller):

    # ---- interests -----------------------------------------------------

    @http.route('/api/v1/partners/by-ref/<string:external_ref>/interests',
                type='http', auth='public',
                methods=['GET', 'OPTIONS'], csrf=False, save_session=False)
    @json_endpoint(methods=['GET'], require_key=True)
    def list_interests(self, external_ref, _body=None, _auth_uid=None,
                       _key_fp=None, **kw):
        env = request.env
        partner = _resolve_partner(env, external_ref)
        limit, offset = parse_pagination(kw, default_limit=50,
                                         max_limit=HARD_LIMIT_MAX)
        date_from = _parse_date(kw.get('date_from'), 'date_from')
        date_to = _parse_date(kw.get('date_to'), 'date_to')

        domain = [('partner_id', '=', partner.id)]
        if date_from:
            domain.append(('create_date', '>=', date_from))
        if date_to:
            domain.append(('create_date', '<=', date_to))

        Lead = env['crm.lead'].sudo()
        total = Lead.search_count(domain)
        rows = Lead.search(domain, limit=limit, offset=offset,
                           order='create_date desc, id desc')

        results = []
        for l in rows:
            results.append({
                'id': l.id,
                'name': l.name or '',
                'contact_name': l.contact_name or '',
                'email': l.email_from or '',
                'phone': l.phone or '',
                'description': (l.description or '')[:2000],
                'stage': l.stage_id.name if l.stage_id else '',
                'type': l.type or '',
                'created_at': _iso(l.create_date),
                'project_id': (l.realestate_api_project_id.id
                               if l.realestate_api_project_id else None),
                'project_name': (l.realestate_api_project_id.name
                                 if l.realestate_api_project_id else ''),
                'unit_id': (l.realestate_api_property_id.id
                            if l.realestate_api_property_id else None),
                'unit_name': (l.realestate_api_property_id.display_name
                              if l.realestate_api_property_id else ''),
            })

        return json_response({
            'external_ref': partner.realestate_api_external_ref,
            'partner_id': partner.id,
            'results': results,
            'total_count': total,
            'limit': limit,
            'offset': offset,
        })

    # ---- viewings ------------------------------------------------------

    @http.route('/api/v1/partners/by-ref/<string:external_ref>/viewings',
                type='http', auth='public',
                methods=['GET', 'OPTIONS'], csrf=False, save_session=False)
    @json_endpoint(methods=['GET'], require_key=True)
    def list_viewings(self, external_ref, _body=None, _auth_uid=None,
                      _key_fp=None, **kw):
        env = request.env
        partner = _resolve_partner(env, external_ref)
        if 'realestate.viewing' not in env.registry:
            raise werkzeug.exceptions.NotFound(_(
                "Viewings not available (real_estate_brokerage not installed)."))
        limit, offset = parse_pagination(kw, default_limit=50,
                                         max_limit=HARD_LIMIT_MAX)
        state = (kw.get('state') or '').strip().lower()

        domain = [('partner_id', '=', partner.id)]
        if state:
            if state not in ('scheduled', 'completed', 'cancelled', 'no_show'):
                raise werkzeug.exceptions.BadRequest(_(
                    "'state' must be one of scheduled|completed|cancelled|no_show."))
            domain.append(('state', '=', state))

        Viewing = env['realestate.viewing'].sudo()
        total = Viewing.search_count(domain)
        rows = Viewing.search(domain, limit=limit, offset=offset,
                              order='scheduled_at desc, id desc')
        results = [{
            'id': v.id,
            'reference': v.name or '',
            'listing_id': v.listing_id.id if v.listing_id else None,
            'property_id': v.property_id.id if v.property_id else None,
            'property_name': v.property_id.display_name if v.property_id else '',
            'scheduled_at': _iso(v.scheduled_at),
            'end_at': _iso(v.end_at),
            'duration_hours': v.duration or 0.0,
            'state': v.state or '',
            'feedback_rating': v.feedback_rating or '',
            'feedback': (v.feedback or '')[:2000],
            'next_action': v.next_action or '',
            'agent_name': v.agent_id.name if v.agent_id else '',
        } for v in rows]

        return json_response({
            'external_ref': partner.realestate_api_external_ref,
            'partner_id': partner.id,
            'results': results,
            'total_count': total,
            'limit': limit,
            'offset': offset,
        })

    # ---- contracts (sale + rental, unified) ----------------------------

    @http.route('/api/v1/partners/by-ref/<string:external_ref>/contracts',
                type='http', auth='public',
                methods=['GET', 'OPTIONS'], csrf=False, save_session=False)
    @json_endpoint(methods=['GET'], require_key=True)
    def list_contracts(self, external_ref, _body=None, _auth_uid=None,
                       _key_fp=None, **kw):
        env = request.env
        partner = _resolve_partner(env, external_ref)
        limit, offset = parse_pagination(kw, default_limit=50,
                                         max_limit=HARD_LIMIT_MAX)
        kind = (kw.get('kind') or '').strip().lower()
        if kind and kind not in ('sale', 'rental'):
            raise werkzeug.exceptions.BadRequest(_(
                "'kind' must be 'sale' or 'rental'."))

        results = []

        # Sale contracts (real_estate_developer)
        if kind in ('', 'sale') and 'realestate.sale.contract' in env.registry:
            Sale = env['realestate.sale.contract'].sudo()
            for c in Sale.search([('partner_id', '=', partner.id)],
                                 order='contract_date desc, id desc'):
                results.append({
                    'kind': 'sale',
                    'id': c.id,
                    'reference': c.name or '',
                    'state': c.state or '',
                    'contract_date': _iso(c.contract_date),
                    'signing_date': _iso(c.signing_date),
                    'expected_handover_date': _iso(c.expected_handover_date),
                    'handover_date': _iso(c.handover_date),
                    'property_id': c.property_id.id if c.property_id else None,
                    'property_name': (c.property_id.display_name
                                      if c.property_id else ''),
                    'project_id': c.project_id.id if c.project_id else None,
                    'project_name': c.project_id.name if c.project_id else '',
                    'sale_price': c.sale_price or 0.0,
                    'currency': c.currency_id.symbol if c.currency_id else '',
                    'paid_amount': c.paid_amount or 0.0,
                    'balance_due': c.balance_due or 0.0,
                    'progress_pct': c.progress or 0.0,
                })

        # Rental contracts (atmta_real_estate)
        if kind in ('', 'rental') and 'realestate.contract' in env.registry:
            Rental = env['realestate.contract'].sudo()
            for c in Rental.search([('partner_id', '=', partner.id)],
                                   order='start_date desc, id desc'):
                results.append({
                    'kind': 'rental',
                    'id': c.id,
                    'reference': c.name or '',
                    'state': c.state or '',
                    'start_date': _iso(c.start_date),
                    'end_date': _iso(c.end_date),
                    'notes': (c.notes or '')[:2000],
                    'currency': (c.currency_id.symbol
                                 if 'currency_id' in c._fields
                                 and c.currency_id else ''),
                    'contract_no': c.contract_no or '',
                    'main_contract_no': c.main_contract_no or '',
                })

        total = len(results)
        # Apply limit/offset AFTER unification (kept in-Python because we
        # unified across two models).
        results = results[offset:offset + limit]

        return json_response({
            'external_ref': partner.realestate_api_external_ref,
            'partner_id': partner.id,
            'results': results,
            'total_count': total,
            'limit': limit,
            'offset': offset,
        })

    # ---- installments (sale side) --------------------------------------

    @http.route('/api/v1/partners/by-ref/<string:external_ref>/installments',
                type='http', auth='public',
                methods=['GET', 'OPTIONS'], csrf=False, save_session=False)
    @json_endpoint(methods=['GET'], require_key=True)
    def list_installments(self, external_ref, _body=None, _auth_uid=None,
                          _key_fp=None, **kw):
        env = request.env
        partner = _resolve_partner(env, external_ref)
        if 'realestate.sale.installment' not in env.registry:
            raise werkzeug.exceptions.NotFound(_(
                "Installments not available (real_estate_developer not installed)."))
        limit, offset = parse_pagination(kw, default_limit=100,
                                         max_limit=HARD_LIMIT_MAX)
        state = (kw.get('state') or '').strip().lower()
        date_from = _parse_date(kw.get('date_from'), 'date_from')
        date_to = _parse_date(kw.get('date_to'), 'date_to')

        domain = [('partner_id', '=', partner.id)]
        if state:
            if state not in ('pending', 'invoiced', 'paid', 'cancelled'):
                raise werkzeug.exceptions.BadRequest(_(
                    "'state' must be one of pending|invoiced|paid|cancelled."))
            domain.append(('state', '=', state))
        if date_from:
            domain.append(('date_due', '>=', date_from))
        if date_to:
            domain.append(('date_due', '<=', date_to))

        Inst = env['realestate.sale.installment'].sudo()
        total = Inst.search_count(domain)
        rows = Inst.search(domain, limit=limit, offset=offset,
                           order='date_due, id')
        results = [{
            'id': i.id,
            'contract_id': i.sale_contract_id.id if i.sale_contract_id else None,
            'contract_ref': (i.sale_contract_id.name
                             if i.sale_contract_id else ''),
            'property_id': i.property_id.id if i.property_id else None,
            'property_name': (i.property_id.display_name
                              if i.property_id else ''),
            'sequence': i.sequence,
            'kind': i.kind or '',
            'amount': i.amount or 0.0,
            'currency': i.currency_id.symbol if i.currency_id else '',
            'date_due': _iso(i.date_due),
            'state': i.state or '',
            'invoice_state': i.move_state or '',
            'payment_state': i.payment_state or '',
        } for i in rows]

        return json_response({
            'external_ref': partner.realestate_api_external_ref,
            'partner_id': partner.id,
            'results': results,
            'total_count': total,
            'limit': limit,
            'offset': offset,
        })

    # ---- payments (rental side) ----------------------------------------

    @http.route('/api/v1/partners/by-ref/<string:external_ref>/payments',
                type='http', auth='public',
                methods=['GET', 'OPTIONS'], csrf=False, save_session=False)
    @json_endpoint(methods=['GET'], require_key=True)
    def list_payments(self, external_ref, _body=None, _auth_uid=None,
                      _key_fp=None, **kw):
        env = request.env
        partner = _resolve_partner(env, external_ref)
        if 'realestate.contract.payment' not in env.registry:
            raise werkzeug.exceptions.NotFound(_(
                "Payments not available (atmta_real_estate not installed)."))
        limit, offset = parse_pagination(kw, default_limit=100,
                                         max_limit=HARD_LIMIT_MAX)
        state = (kw.get('state') or '').strip().lower()
        date_from = _parse_date(kw.get('date_from'), 'date_from')
        date_to = _parse_date(kw.get('date_to'), 'date_to')

        domain = [('partner_id', '=', partner.id)]
        if state:
            if state not in ('draft', 'invoiced', 'paid', 'cancelled'):
                raise werkzeug.exceptions.BadRequest(_(
                    "'state' must be one of draft|invoiced|paid|cancelled."))
            domain.append(('state', '=', state))
        if date_from:
            domain.append(('date_due', '>=', date_from))
        if date_to:
            domain.append(('date_due', '<=', date_to))

        Payment = env['realestate.contract.payment'].sudo()
        total = Payment.search_count(domain)
        rows = Payment.search(domain, limit=limit, offset=offset,
                              order='date_due, id')
        results = [{
            'id': p.id,
            'contract_id': p.contract_id.id if p.contract_id else None,
            'contract_ref': p.contract_id.name if p.contract_id else '',
            'property_id': p.property_id.id if p.property_id else None,
            'property_name': (p.property_id.display_name
                              if p.property_id else ''),
            'label': (p.label if 'label' in p._fields else '') or '',
            'amount': p.amount or 0.0,
            'amount_total': p.amount_total or 0.0,
            'increase_amount': p.increase_amount or 0.0,
            'discount_amount': p.discount_amount or 0.0,
            'date_due': _iso(p.date_due),
            'hijri_date_due': p.hijri_date_due or '',
            'state': p.state or '',
            'invoice_state': p.move_state or '',
            'payment_state': p.payment_state or '',
        } for p in rows]

        return json_response({
            'external_ref': partner.realestate_api_external_ref,
            'partner_id': partner.id,
            'results': results,
            'total_count': total,
            'limit': limit,
            'offset': offset,
        })
