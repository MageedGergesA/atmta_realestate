from openpyxl.styles.builtins import output

from odoo import http
from odoo.http import request
import io
import xlsxwriter


class ContractReport(http.Controller):

    @http.route('contract/excel/report', type='http', auth='user')
    def download_contract_excel_report(self):
        output = io.BytesIO()
        workbook = xlsxwriter.workbook(output, {'in_memory':True})
        worksheet = workbook.add_worksheet('properties')
