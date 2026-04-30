# -*- coding: utf-8 -*-
from odoo import http, fields
from odoo.http import request
import base64  # <--- REQUIRED FOR FILE DOWNLOADS

class TrainingController(http.Controller):

    @http.route(['/training', '/training/<string:token>'], type='http', auth='public', website=True)
    def training_interface(self, token=None, **kwargs):
        if not token:
            return request.render('compliance_training.404_page')

        # Sudo() is critical here: It allows "Public" users to read this specific log
        log = request.env['ct.log'].sudo().search([('access_token', '=', token)], limit=1)
        
        if not log:
            return request.render('compliance_training.404_page')
        
        if log.state == 'expired':
            return request.render('compliance_training.404_page')
            
        if log.state == 'completed':
            return request.render('compliance_training.already_completed')

        return request.render('compliance_training.training_portal', {
            'log': log,
            'content': log.content_id,
            'trainee': log.trainee_id,
            'questions': log.content_id.question_ids,
        })

    # --- NEW SECURE DOWNLOAD FUNCTION ---
    @http.route('/training/download/<string:token>', type='http', auth='public')
    def training_download_file(self, token):
        # 1. Use the Token to find the log securely (Bypass Login requirement)
        log = request.env['ct.log'].sudo().search([('access_token', '=', token)], limit=1)
        
        if not log:
            return request.not_found()
            
        # 2. Get the content
        content = log.content_id
        if not content.file_data:
            return request.not_found()

        # 3. Decode the file and serve it directly
        try:
            file_content = base64.b64decode(content.file_data)
            filename = content.file_name or "Training_Document.pdf"
            
            headers = [
                ('Content-Type', 'application/octet-stream'),
                ('Content-Disposition', f'attachment; filename="{filename}"')
            ]
            
            return request.make_response(file_content, headers)
        except Exception:
            return request.not_found()

    @http.route('/training/submit', type='http', auth='public', methods=['POST'], csrf=False)
    def training_submit(self, **kwargs):
        token = kwargs.get('token')
        signature_base64 = kwargs.get('signature_base64')

        if not token:
            return "Error: Missing Token"

        log = request.env['ct.log'].sudo().search([('access_token', '=', token)], limit=1)
        if not log:
            return "Error: Invalid Token"
        
        if log.state == 'expired':
            return "Error: This training request has expired."

        # QUIZ SCORING
        questions = log.content_id.question_ids
        score = 0
        total = len(questions)
        
        if total > 0:
            for q in questions:
                user_answer = kwargs.get(f'question_{q.id}')
                if user_answer == q.correct_answer:
                    score += 1
            
            percent = (score / total) * 100
            required_score = log.content_id.min_pass_score
            
            if percent < required_score:
                return request.render('compliance_training.quiz_failed', {
                    'score': score,
                    'total': total,
                    'percent': int(percent),
                    'required': required_score,
                    'token': token
                })

        # Process Signature
        if signature_base64 and isinstance(signature_base64, str) and signature_base64.startswith('data:image'):
            signature_base64 = signature_base64.split(',')[1]

        # Save result
        log.write({
            'signature_image': signature_base64,
            'completion_date': fields.Datetime.now(),
            'state': 'completed',
            'quiz_score': f"{int((score/total)*100)}%" if total > 0 else "N/A"
        })
        
        return request.redirect('/training/thanks')

    @http.route('/training/thanks', type='http', auth='public', website=True)
    def training_thanks(self, **kwargs):
        return request.render('compliance_training.thank_you_page')