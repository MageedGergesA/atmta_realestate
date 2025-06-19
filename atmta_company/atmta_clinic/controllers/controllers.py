# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from odoo.addons.auth_signup.controllers.main import AuthSignupHome
from odoo.addons.portal.controllers.portal import CustomerPortal


# class AtmtaClinic(http.Controller):
#     @http.route('/atmta_clinic/atmta_clinic', auth='public')
#     def index(self, **kw):
#         return "Hello, world"

#     @http.route('/atmta_clinic/atmta_clinic/objects', auth='public')
#     def list(self, **kw):
#         return http.request.render('atmta_clinic.listing', {
#             'root': '/atmta_clinic/atmta_clinic',
#             'objects': http.request.env['atmta_clinic.atmta_clinic'].search([]),
#         })

#     @http.route('/atmta_clinic/atmta_clinic/objects/<model("atmta_clinic.atmta_clinic"):obj>', auth='public')
#     def object(self, obj, **kw):
#         return http.request.render('atmta_clinic.object', {
#             'object': obj
#         })

class ClinicPortal(http.Controller):
    @http.route(['/my/appointments'], type='http', auth='user', website=True)
    def portal_my_appointments(self, **kw):
        patient = request.env['clinic.patient'].sudo().search([('user_id', '=', request.env.user.id)], limit=1)
        appointments = request.env['clinic.appointment'].sudo().search([('patient_id', '=', patient.id)]) if patient else []
        return request.render('atmta_clinic.portal_my_appointments', {
            'appointments': appointments,
        })

    @http.route(['/my/bills'], type='http', auth='user', website=True)
    def portal_my_bills(self, **kw):
        patient = request.env['clinic.patient'].sudo().search([('user_id', '=', request.env.user.id)], limit=1)
        bills = request.env['clinic.billing'].sudo().search([('patient_id', '=', patient.id)]) if patient else []
        return request.render('atmta_clinic.portal_my_bills', {
            'bills': bills,
        })

    @http.route(['/my/lab_results'], type='http', auth='user', website=True)
    def portal_my_lab_results(self, **kw):
        patient = request.env['clinic.patient'].sudo().search([('user_id', '=', request.env.user.id)], limit=1)
        lab_results = request.env['clinic.lab.result'].sudo().search([('request_id.patient_id', '=', patient.id)]) if patient else []
        return request.render('atmta_clinic.portal_my_lab_results', {
            'lab_results': lab_results,
        })

    @http.route(['/my/prescriptions'], type='http', auth='user', website=True)
    def portal_my_prescriptions(self, **kw):
        patient = request.env['clinic.patient'].sudo().search([('user_id', '=', request.env.user.id)], limit=1)
        prescriptions = request.env['clinic.prescription'].sudo().search([('patient_id', '=', patient.id)]) if patient else []
        return request.render('atmta_clinic.portal_my_prescriptions', {
            'prescriptions': prescriptions,
        })

    # @http.route(['/my/profile'], type='http', auth='user', website=True)
    # def portal_my_profile(self, **kw):
    #     patient = request.env['clinic.patient'].sudo().search([('user_id', '=', request.env.user.id)], limit=1)
    #     print(patient)
    #     print('patient')
    #     appointments = request.env['clinic.appointment'].sudo().search([('patient_id', '=', patient.id)]) if patient else []
    #     appointment_requests = request.env['clinic.appointment.request'].sudo().search([('patient_id', '=', patient.id)]) if patient else []
    #     bills = request.env['clinic.billing'].sudo().search([('patient_id', '=', patient.id)]) if patient else []
    #     bill_payments = request.env['clinic.bill.pay'].sudo().search([('billing_id.patient_id', '=', patient.id)]) if patient else []
    #     claims = request.env['clinic.claim'].sudo().search([('patient_id', '=', patient.id)]) if patient else []
    #     lab_results = request.env['clinic.lab.result'].sudo().search([('request_id.patient_id', '=', patient.id)]) if patient else []
    #     prescriptions = request.env['clinic.prescription'].sudo().search([('patient_id', '=', patient.id)]) if patient else []
    #     progress_notes = patient.progress_note_ids if patient else []
    #     document_uploads = patient.document_upload_ids if patient else []
    #     portal_messages = request.env['clinic.portal.message'].sudo().search([('patient_id', '=', patient.id)]) if patient else []
    #     return request.render('atmta_clinic.portal_my_profile', {
    #         'patient': patient,
    #         'appointments': appointments,
    #         'appointment_requests': appointment_requests,
    #         'bills': bills,
    #         'bill_payments': bill_payments,
    #         'claims': claims,
    #         'lab_results': lab_results,
    #         'prescriptions': prescriptions,
    #         'progress_notes': progress_notes,
    #         'document_uploads': document_uploads,
    #         'portal_messages': portal_messages,
    #     })

class ClinicPortalHome(CustomerPortal):
    @http.route(['/my/home'], type='http', auth='user', website=True)
    def home(self, **kw):
        response = super().home(**kw)
        user = request.env.user
        patient = request.env['clinic.patient'].sudo().search([('user_id', '=', user.id)], limit=1)
        appointments = request.env['clinic.appointment'].sudo().search([('patient_id', '=', patient.id)]) if patient else []
        appointment_requests = request.env['clinic.appointment.request'].sudo().search([('patient_id', '=', patient.id)]) if patient else []
        bills = request.env['clinic.billing'].sudo().search([('patient_id', '=', patient.id)]) if patient else []
        bill_payments = request.env['clinic.bill.pay'].sudo().search([('billing_id.patient_id', '=', patient.id)]) if patient else []
        claims = request.env['clinic.claim'].sudo().search([('patient_id', '=', patient.id)]) if patient else []
        lab_results = request.env['clinic.lab.result'].sudo().search([('request_id.patient_id', '=', patient.id)]) if patient else []
        prescriptions = request.env['clinic.prescription'].sudo().search([('patient_id', '=', patient.id)]) if patient else []
        progress_notes = patient.progress_note_ids if patient else []
        document_uploads = patient.document_upload_ids if patient else []
        portal_messages = request.env['clinic.portal.message'].sudo().search([('patient_id', '=', patient.id)]) if patient else []
        response.qcontext.update({
            'patient': patient,
            'appointments': appointments,
            'appointment_requests': appointment_requests,
            'bills': bills,
            'bill_payments': bill_payments,
            'claims': claims,
            'lab_results': lab_results,
            'prescriptions': prescriptions,
            'progress_notes': progress_notes,
            'document_uploads': document_uploads,
            'portal_messages': portal_messages,
            'no_patient_linked': not bool(patient),
        })
        return response

class ClinicWebsite(http.Controller):
    @http.route('/', type='http', auth='public', website=True)
    def website_homepage(self, **kw):
        return request.render('atmta_clinic.website_homepage')

    @http.route('/appointment/request', type='http', auth='public', website=True)
    def website_appointment_request(self, **kw):
        return request.render('atmta_clinic.website_appointment_request')

    @http.route('/appointment/request/submit', type='http', auth='public', website=True, methods=['POST'])
    def website_appointment_request_submit(self, **post):
        # Find or create the patient
        patient = request.env['clinic.patient'].sudo().search([
            ('email', '=', post.get('email'))
        ], limit=1)
        if not patient:
            patient = request.env['clinic.patient'].sudo().create({
                'name': post.get('name'),
                'email': post.get('email'),
                'phone': post.get('phone'),
            })
        # Create the appointment request
        request.env['clinic.appointment.request'].sudo().create({
            'patient_id': patient.id,
            'requested_date': post.get('date'),
            'notes': post.get('notes'),
        })
        return request.render('atmta_clinic.website_appointment_thankyou')

class ClinicSignup(AuthSignupHome):
    def do_signup(self, qcontext):
        res = super().do_signup(qcontext)
        user = request.env['res.users'].sudo().search([('login', '=', qcontext.get('login'))], limit=1)
        if user and not request.env['clinic.patient'].sudo().search([('user_id', '=', user.id)], limit=1):
            request.env['clinic.patient'].sudo().create({
                'name': qcontext.get('name') or user.name or user.login,
                'email': user.email,
                'user_id': user.id,
            })
        return res

