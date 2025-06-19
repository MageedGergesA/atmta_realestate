# -*- coding: utf-8 -*-
# from odoo import http


# class AtmtaMultiCompanyCount(http.Controller):
#     @http.route('/atmta_multi_company_count/atmta_multi_company_count', auth='public')
#     def index(self, **kw):
#         return "Hello, world"

#     @http.route('/atmta_multi_company_count/atmta_multi_company_count/objects', auth='public')
#     def list(self, **kw):
#         return http.request.render('atmta_multi_company_count.listing', {
#             'root': '/atmta_multi_company_count/atmta_multi_company_count',
#             'objects': http.request.env['atmta_multi_company_count.atmta_multi_company_count'].search([]),
#         })

#     @http.route('/atmta_multi_company_count/atmta_multi_company_count/objects/<model("atmta_multi_company_count.atmta_multi_company_count"):obj>', auth='public')
#     def object(self, obj, **kw):
#         return http.request.render('atmta_multi_company_count.object', {
#             'object': obj
#         })

