# -*- coding: utf-8 -*-
# from odoo import http


# class AtmtaRealEstate(http.Controller):
#     @http.route('/atmta_real_estate/atmta_real_estate', auth='public')
#     def index(self, **kw):
#         return "Hello, world"

#     @http.route('/atmta_real_estate/atmta_real_estate/objects', auth='public')
#     def list(self, **kw):
#         return http.request.render('atmta_real_estate.listing', {
#             'root': '/atmta_real_estate/atmta_real_estate',
#             'objects': http.request.env['atmta_real_estate.atmta_real_estate'].search([]),
#         })

#     @http.route('/atmta_real_estate/atmta_real_estate/objects/<model("atmta_real_estate.atmta_real_estate"):obj>', auth='public')
#     def object(self, obj, **kw):
#         return http.request.render('atmta_real_estate.object', {
#             'object': obj
#         })

