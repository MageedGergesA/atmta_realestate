from odoo import http
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal


class RealEstatePortal(CustomerPortal):

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _re_partner(self):
        return request.env.user.partner_id

    def _re_contracts(self, partner):
        return request.env['realestate.sale.contract'].sudo().search(
            [('partner_id', '=', partner.id)], order='contract_date desc')

    def _invoice_schedule(self, invoice):
        """Installment schedule = the invoice's receivable due lines (the
        payment term split). Per-line paid status from its residual."""
        if not invoice or invoice.state != 'posted':
            return []
        recv = invoice.line_ids.filtered(
            lambda l: l.account_id.account_type == 'asset_receivable')
        rows = []
        for line in recv.sorted(lambda l: (l.date_maturity or l.date)):
            residual = abs(line.amount_residual_currency) if line.currency_id else abs(line.amount_residual)
            amount = abs(line.amount_currency) if line.currency_id else abs(line.balance)
            rows.append({
                'date': line.date_maturity or line.date,
                'amount': amount,
                'paid': residual < 0.01,
                'currency': invoice.currency_id,
            })
        return rows

    def _owns_property(self, partner, property_id):
        return bool(request.env['realestate.sale.contract'].sudo().search_count(
            [('partner_id', '=', partner.id), ('property_id', '=', property_id)]))

    # ------------------------------------------------------------------
    # Home counters
    # ------------------------------------------------------------------
    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        partner = self._re_partner()
        if 'sale_contract_count' in counters:
            values['sale_contract_count'] = request.env['realestate.sale.contract'].sudo().search_count(
                [('partner_id', '=', partner.id)])
        if 'installment_count' in counters:
            count = 0
            for inv in self._re_contracts(partner).mapped('invoice_id'):
                count += sum(1 for r in self._invoice_schedule(inv) if not r['paid'])
            values['installment_count'] = count
        if 'snagging_count' in counters:
            values['snagging_count'] = request.env['realestate.snagging.issue'].sudo().search_count(
                [('handover_id.partner_id', '=', partner.id)])
        return values

    # ------------------------------------------------------------------
    # Sale contracts
    # ------------------------------------------------------------------
    @http.route(['/my/sale-contracts'], type='http', auth='user', website=True)
    def portal_my_sale_contracts(self, **kw):
        return request.render('real_estate_portal.portal_my_sale_contracts', {
            'contracts': self._re_contracts(self._re_partner()),
            'page_name': 'sale_contracts',
        })

    @http.route(['/my/sale-contracts/<int:contract_id>'], type='http', auth='user', website=True)
    def portal_my_sale_contract(self, contract_id, **kw):
        partner = self._re_partner()
        contract = request.env['realestate.sale.contract'].sudo().search(
            [('id', '=', contract_id), ('partner_id', '=', partner.id)], limit=1)
        if not contract:
            return request.redirect('/my/sale-contracts')
        prop = contract.property_id
        return request.render('real_estate_portal.portal_my_sale_contract_detail', {
            'contract': contract,
            'schedule': self._invoice_schedule(contract.invoice_id),
            'handovers': request.env['realestate.handover'].sudo().search(
                [('sale_contract_id', '=', contract.id)]),
            'warranties': request.env['realestate.warranty'].sudo().search(
                [('sale_contract_id', '=', contract.id)]),
            'has_floor_plan': bool(prop and prop.floor_plan_image),
            'gallery': prop.property_Attachment_media_ids if prop else prop,
            'page_name': 'sale_contract',
        })

    @http.route(['/my/installments'], type='http', auth='user', website=True)
    def portal_my_installments(self, **kw):
        partner = self._re_partner()
        rows, total, paid = [], 0.0, 0.0
        for c in self._re_contracts(partner):
            for r in self._invoice_schedule(c.invoice_id):
                rows.append(dict(r, contract=c))
                total += r['amount']
                if r['paid']:
                    paid += r['amount']
        rows.sort(key=lambda r: r['date'] or '')
        return request.render('real_estate_portal.portal_my_installments', {
            'rows': rows, 'total': total, 'paid': paid, 'balance': total - paid,
            'currency': partner.company_id.currency_id or request.env.company.currency_id,
            'page_name': 'installments',
        })

    # ------------------------------------------------------------------
    # Unit images (served with sudo after an ownership check)
    # ------------------------------------------------------------------
    @http.route(['/my/re-image/<int:property_id>/floor-plan'], type='http', auth='user')
    def portal_re_floor_plan(self, property_id, **kw):
        if not self._owns_property(self._re_partner(), property_id):
            return request.not_found()
        prop = request.env['realestate.property'].sudo().browse(property_id)
        return request.env['ir.binary']._get_image_stream_from(prop, 'floor_plan_image').get_response()

    @http.route(['/my/re-image/gallery/<int:image_id>'], type='http', auth='user')
    def portal_re_gallery_image(self, image_id, **kw):
        img = request.env['property.image'].sudo().browse(image_id).exists()
        if not img or not self._owns_property(self._re_partner(), img.property_id.id):
            return request.not_found()
        return request.env['ir.binary']._get_image_stream_from(img, 'image_1024').get_response()

    # ------------------------------------------------------------------
    # Snagging
    # ------------------------------------------------------------------
    @http.route(['/my/snagging'], type='http', auth='user', website=True)
    def portal_my_snagging(self, **kw):
        partner = self._re_partner()
        issues = request.env['realestate.snagging.issue'].sudo().search(
            [('handover_id.partner_id', '=', partner.id)], order='reported_date desc')
        return request.render('real_estate_portal.portal_my_snagging', {
            'issues': issues, 'page_name': 'snagging',
        })

    @http.route(['/my/snagging/new'], type='http', auth='user', methods=['GET', 'POST'], website=True)
    def portal_my_snagging_new(self, **post):
        partner = self._re_partner()
        handovers = request.env['realestate.handover'].sudo().search(
            [('partner_id', '=', partner.id), ('state', '!=', 'cancelled')])
        if request.httprequest.method == 'POST':
            handover_id = int(post.get('handover_id') or 0)
            if handover_id and handover_id in handovers.ids:
                request.env['realestate.snagging.issue'].sudo().create({
                    'handover_id': handover_id,
                    'description': post.get('description', '').strip(),
                    'severity': post.get('severity') or 'minor',
                    'location_on_unit': post.get('location') or '',
                })
                return request.redirect('/my/snagging')
        return request.render('real_estate_portal.portal_my_snagging_new', {
            'handovers': handovers, 'page_name': 'snagging_new',
        })
