# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError
from uuid import uuid4
from dateutil.relativedelta import relativedelta
import logging
import base64
import csv
import io
from datetime import datetime

_logger = logging.getLogger(__name__)

class CtTag(models.Model):
    _name = 'ct.tag'
    _description = 'Training Tags'
    name = fields.Char(required=True)
    color = fields.Integer(string='Color Index')

class CtJobRole(models.Model):
    _name = 'ct.job.role'
    _description = 'Job Roles'
    _rec_name = 'name'
    name = fields.Char(required=True)

class CtTrainee(models.Model):
    _name = 'ct.trainee'
    _description = 'Trainee'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'name'

    name = fields.Char(required=True, tracking=True)
    email = fields.Char(required=True)
    employee_number = fields.Char(string="Employee ID", tracking=True)
    job_role_id = fields.Many2one('ct.job.role', string="Job Role", tracking=True)
    tag_ids = fields.Many2many('ct.tag', string="Tags")
    active = fields.Boolean(default=True)
    log_count = fields.Integer(compute='_compute_log_count')

    def _compute_log_count(self):
        for record in self:
            record.log_count = self.env['ct.log'].search_count([('trainee_id', '=', record.id)])

    def action_view_training_logs(self):
        return {
            'name': _('Training History'),
            'type': 'ir.actions.act_window',
            'res_model': 'ct.log',
            'view_mode': 'list,form',
            'domain': [('trainee_id', '=', self.id)],
            'context': {'default_trainee_id': self.id},
        }

class CtContent(models.Model):
    _name = 'ct.content'
    _description = 'Training Content'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'display_name' 

    title = fields.Char(required=True, tracking=True)
    revision = fields.Char(default='A', required=True, tracking=True, readonly=True)
    active = fields.Boolean(default=True, string="Active (Current Rev)")
    min_pass_score = fields.Integer(string="Passing Score (%)", default=100)
    question_ids = fields.One2many('ct.question', 'content_id', string="Quiz Questions")

    display_name = fields.Char(compute='_compute_display_name', store=True)

    delivery_method = fields.Selection([
        ('online', 'Online (Self-Study)'),
        ('otj', 'On-The-Job (Manager Verified)')
    ], default='online', required=True, string="Delivery Method")

    content_type = fields.Selection([('video', 'Video URL'), ('file', 'File Download')], default='file', required=True)
    video_url = fields.Char(string="Video URL")
    embed_url = fields.Char(compute='_compute_embed_url')
    file_data = fields.Binary(string="File Content")
    file_name = fields.Char(string="File Name")
    
    frequency_months = fields.Integer(string="Frequency (Months)", required=True, default=12, help="Enter 0 for One-Time training (No Expiration)")
    description = fields.Html(string="Description/Instructions")

    @api.constrains('frequency_months')
    def _check_frequency_validity(self):
        for record in self:
            if record.frequency_months < 0:
                raise ValidationError("Frequency cannot be negative. Enter 0 for Does Not Expire.")

    @api.depends('title', 'revision')
    def _compute_display_name(self):
        for record in self:
            rev = record.revision.upper() if record.revision else '?'
            record.display_name = f"{record.title} (Rev {rev})"

    @api.onchange('revision')
    def _onchange_revision(self):
        if self.revision:
            self.revision = self.revision.upper()

    @api.depends('video_url')
    def _compute_embed_url(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        for record in self:
            if not record.video_url:
                record.embed_url = False
                continue
            url = record.video_url
            if 'youtube.com/watch' in url:
                try:
                    video_id = url.split('v=')[1].split('&')[0]
                    record.embed_url = f"https://www.youtube.com/embed/{video_id}"
                except IndexError:
                    record.embed_url = url
            elif 'youtu.be/' in url:
                try:
                    video_id = url.split('youtu.be/')[1].split('?')[0]
                    record.embed_url = f"https://www.youtube.com/embed/{video_id}"
                except IndexError:
                    record.embed_url = url
            else:
                record.embed_url = url

    def action_create_revision(self):
        self.ensure_one()

        # Cancel any pending (draft/sent) logs on the old revision
        pending_logs = self.env['ct.log'].search([('content_id', '=', self.id), ('state', 'in', ['draft', 'sent'])])
        for log in pending_logs:
            log.state = 'expired'
            log.message_post(body=f"System: Training Cancelled. A newer revision ({self.revision} -> New) has been issued.")

        # Find trainees who COMPLETED the old revision — candidates for reassignment
        completed_logs = self.env['ct.log'].search([('content_id', '=', self.id), ('state', '=', 'completed')])
        trainee_ids = completed_logs.mapped('trainee_id.id')

        # Archive old revision and create new one
        self.write({'active': False})
        new_rev = self.copy({
            'title': self.title,
            'revision': chr(ord(self.revision[0]) + 1) if self.revision and self.revision[0].isalpha() else 'B',
            'active': True,
            'min_pass_score': self.min_pass_score,
            'delivery_method': self.delivery_method,
            'frequency_months': self.frequency_months,
        })

        # Open reassignment wizard with context
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ct.rev.reassign',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_new_content_id': new_rev.id,
                'default_old_revision': self.revision,
                'default_trainee_ids': [(6, 0, trainee_ids)],
                'default_trainee_count': len(trainee_ids),
            },
        }

class CtQuestion(models.Model):
    _name = 'ct.question'
    _description = 'Competency Quiz Question'
    content_id = fields.Many2one('ct.content', ondelete='cascade')
    question_text = fields.Char(required=True)
    correct_answer = fields.Selection([('true', 'True'), ('false', 'False')], required=True, default='true')

class CtLog(models.Model):
    _name = 'ct.log'
    _description = 'Training Audit Log'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'trainee_id'
    _order = 'completion_date desc'

    trainee_id = fields.Many2one('ct.trainee', required=True, string="Trainee")
    content_id = fields.Many2one('ct.content', required=True, string="Content", context={'active_test': False})
    
    delivery_method = fields.Selection([
        ('online', 'Online (Self-Study)'),
        ('otj', 'On-The-Job (Manager Verified)')
    ], string="Delivery Method", required=True)

    revision = fields.Char(related='content_id.revision', store=True, string="Rev")
    state = fields.Selection([('draft', 'Draft'), ('sent', 'Sent'), ('completed', 'Completed'), ('expired', 'Expired')], default='draft', tracking=True, group_expand='_expand_states')
    access_token = fields.Char(default=lambda self: str(uuid4()), readonly=True)
    training_url = fields.Char(string="Training Link", compute='_compute_training_url')
    signature_image = fields.Binary(string="Signature", readonly=True, attachment=True)
    completion_date = fields.Datetime(readonly=True)
    expiry_date = fields.Date(compute='_compute_expiry', store=True)
    quiz_score = fields.Char(string="Score/Result", readonly=True)

    @api.onchange('content_id')
    def _onchange_content_id(self):
        if self.content_id:
            self.revision = self.content_id.revision
            self.delivery_method = self.content_id.delivery_method

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if 'content_id' in vals:
                content = self.env['ct.content'].browse(vals['content_id'])
                if not vals.get('revision'):
                    vals['revision'] = content.revision
                if not vals.get('delivery_method'):
                    vals['delivery_method'] = content.delivery_method
        return super(CtLog, self).create(vals_list)

    @api.depends('access_token')
    def _compute_training_url(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        for record in self:
            record.training_url = f"{base_url}/training/{record.access_token}"

    @api.depends('completion_date', 'content_id.frequency_months')
    def _compute_expiry(self):
        for record in self:
            if record.completion_date and record.content_id.frequency_months > 0:
                record.expiry_date = record.completion_date.date() + relativedelta(months=record.content_id.frequency_months)
            else:
                record.expiry_date = False

    def action_send_email(self):
        """
        Sends the email using the Odoo queue.
        Safeguard: Skips sending if training is already completed or expired.
        """
        template = self.env.ref('compliance_training.email_template_training_request')
        for record in self:
            if record.state in ['completed', 'expired']:
                continue
            if record.trainee_id.email:
                template.send_mail(record.id, force_send=False, raise_exception=False)
                record.state = 'sent'
            else:
                record.message_post(body="Error: Cannot send email. Trainee has no email address.")

    @api.model
    def _expand_states(self, states, domain, order):
        return [key for key, val in type(self).state.selection]

    def action_open_otj_wizard(self):
        return {
            'name': 'Record OTJ Result',
            'type': 'ir.actions.act_window',
            'res_model': 'ct.otj.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_log_id': self.id},
        }

    @api.model
    def action_auto_expire(self):
        """
        Flip any completed log whose expiry_date has passed to expired.
        Called as a server action bound to the log list — no cron required.
        """
        today = fields.Date.today()
        try:
            overdue = self.search([
                ('state', '=', 'completed'),
                ('expiry_date', '<', today),
                ('expiry_date', '!=', False),
            ])
            if overdue:
                overdue.write({'state': 'expired'})
                _logger.info("Auto-expire: flipped %d log(s) to expired.", len(overdue))
        except Exception as e:
            _logger.warning("Auto-expire check failed (non-critical): %s", e)

class CtBatchAssign(models.TransientModel):
    _name = 'ct.batch.assign'
    _description = 'Batch Training Assignment'

    content_ids = fields.Many2many('ct.content', string="Content to Assign", domain=[('active', '=', True)])
    target_mode = fields.Selection([('tag', 'By Tag'), ('role', 'By Job Role'), ('specific', 'Specific Trainees')], default='tag', required=True)
    tag_ids = fields.Many2many('ct.tag', string="Tags")
    job_role_id = fields.Many2one('ct.job.role', string="Job Role")
    trainee_ids = fields.Many2many('ct.trainee', string="Trainees")

    def action_assign(self):
        trainees = self.env['ct.trainee']
        if self.target_mode == 'tag':
            trainees = self.env['ct.trainee'].search([('tag_ids', 'in', self.tag_ids.ids)])
        elif self.target_mode == 'role':
            trainees = self.env['ct.trainee'].search([('job_role_id', '=', self.job_role_id.id)])
        else:
            trainees = self.trainee_ids

        if not trainees:
            raise UserError("No trainees found matching your selection.")

        created_logs = self.env['ct.log']
        for content in self.content_ids:
            for trainee in trainees:
                existing = self.env['ct.log'].search([
                    ('trainee_id', '=', trainee.id), 
                    ('content_id', '=', content.id), 
                    ('state', 'in', ['draft', 'sent', 'completed'])
                ])
                if not existing:
                    log = self.env['ct.log'].create({
                        'trainee_id': trainee.id, 
                        'content_id': content.id, 
                        'state': 'draft',
                        'revision': content.revision,
                        'delivery_method': content.delivery_method
                    })
                    created_logs += log
        
        if created_logs:
            created_logs.action_send_email()
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Success',
                    'message': f'Assigned training to {len(created_logs)} trainees. Emails have been queued.',
                    'type': 'success',
                    'sticky': False,
                    'next': {'type': 'ir.actions.act_window_close'},
                }
            }
        else:
            raise UserError("All selected trainees already have active assignments for this content.")

class CtOtjWizard(models.TransientModel):
    _name = 'ct.otj.wizard'
    _description = 'OTJ Verification Wizard'

    log_id = fields.Many2one('ct.log', required=True, readonly=True)
    completion_date = fields.Datetime(string="Date Performed", required=True, default=fields.Datetime.now)
    trainer_name = fields.Char(string="Trainer / Observer", default=lambda self: self.env.user.name, required=True)
    notes = fields.Text(string="Observation Notes")

    def action_confirm(self):
        self.ensure_one()
        self.log_id.write({
            'state': 'completed',
            'completion_date': self.completion_date,
            'quiz_score': 'OTJ / Pass',
            'signature_image': False,
        })
        self.log_id.message_post(body=f"OTJ Training Verified by {self.trainer_name}. Notes: {self.notes}")
        return {'type': 'ir.actions.act_window_close'}


# ============================================================
# REVISION REASSIGNMENT WIZARD
# ============================================================

class CtRevReassign(models.TransientModel):
    _name = 'ct.rev.reassign'
    _description = 'Revision Reassignment Wizard'

    new_content_id = fields.Many2one('ct.content', string="New Revision", readonly=True,
                                     context={'active_test': False})
    old_revision = fields.Char(string="Previous Revision", readonly=True)
    trainee_ids = fields.Many2many('ct.trainee', string="Trainees to Reassign")
    trainee_count = fields.Integer(string="Trainees Found", readonly=True)

    def action_reassign(self):
        """Create new Draft logs for all selected trainees on the new revision."""
        self.ensure_one()
        if not self.trainee_ids or not self.new_content_id:
            raise UserError("No trainees or content to reassign.")

        created = 0
        for trainee in self.trainee_ids:
            # Skip if they already have an active log for this new revision
            existing = self.env['ct.log'].search([
                ('trainee_id', '=', trainee.id),
                ('content_id', '=', self.new_content_id.id),
                ('state', 'in', ['draft', 'sent', 'completed']),
            ], limit=1)
            if existing:
                continue
            self.env['ct.log'].create({
                'trainee_id': trainee.id,
                'content_id': self.new_content_id.id,
                'state': 'draft',
                'revision': self.new_content_id.revision,
                'delivery_method': self.new_content_id.delivery_method,
            })
            created += 1

        # Send emails for all newly created logs
        if created:
            new_logs = self.env['ct.log'].search([
                ('content_id', '=', self.new_content_id.id),
                ('state', '=', 'draft'),
            ])
            new_logs.action_send_email()

        # Navigate to the new content form after reassigning
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ct.content',
            'view_mode': 'form',
            'res_id': self.new_content_id.id,
            'target': 'current',
        }

    def action_skip(self):
        """Skip reassignment — navigate to new content form without assigning."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ct.content',
            'view_mode': 'form',
            'res_id': self.new_content_id.id,
            'target': 'current',
        }


# ============================================================
# HISTORICAL IMPORT WIZARD
# ============================================================

class CtHistoricalImport(models.TransientModel):
    _name = 'ct.historical.import'
    _description = 'Historical Training Import Wizard'

    csv_file = fields.Binary(string="CSV File", required=True)
    csv_filename = fields.Char(string="Filename")

    state = fields.Selection([
        ('draft', 'Ready'),
        ('done', 'Complete'),
    ], default='draft')

    result_summary = fields.Text(string="Import Summary", readonly=True)
    result_created = fields.Integer(string="Records Created", readonly=True)
    result_skipped = fields.Integer(string="Records Skipped (Duplicate)", readonly=True)
    result_errors = fields.Integer(string="Rows With Errors", readonly=True)
    result_error_detail = fields.Text(string="Error Detail", readonly=True)

    def action_import(self):
        self.ensure_one()

        if not self.csv_file:
            raise UserError("Please upload a CSV file before importing.")

        # Decode the uploaded file — handle both UTF-8 and UTF-8-BOM (common from Excel)
        try:
            raw = base64.b64decode(self.csv_file)
            text = raw.decode('utf-8-sig')
            reader = csv.DictReader(io.StringIO(text))
        except Exception as e:
            raise UserError(f"Could not read file. Ensure it is a valid CSV.\nError: {e}")

        # Validate required columns exist
        required_cols = {'employee_name', 'content_title', 'completion_date', 'delivery_method'}
        try:
            fieldnames = set(reader.fieldnames or [])
        except Exception:
            raise UserError("CSV file appears to be empty or malformed.")

        missing = required_cols - fieldnames
        if missing:
            raise UserError(
                f"CSV is missing required columns: {', '.join(sorted(missing))}\n\n"
                f"Required: employee_name, content_title, completion_date, delivery_method\n"
                f"Optional: employee_number, job_role, email, revision, frequency_months, score"
            )

        created = 0
        skipped = 0
        errors = []

        for i, row in enumerate(reader, start=2):
            row_num = f"Row {i}"
            row = {k: (v.strip() if v else '') for k, v in row.items()}

            employee_name   = row.get('employee_name', '')
            content_title   = row.get('content_title', '')
            completion_date = row.get('completion_date', '')
            delivery_method = row.get('delivery_method', '').lower()
            employee_number = row.get('employee_number', '')
            job_role        = row.get('job_role', '')
            email           = row.get('email', '')
            revision        = row.get('revision', 'A').upper() or 'A'
            score           = row.get('score', 'Historical Import')

            try:
                frequency_months = int(row.get('frequency_months', 12))
            except ValueError:
                frequency_months = 12

            # Validate required values
            if not employee_name:
                errors.append(f"{row_num}: Missing employee_name.")
                continue
            if not content_title:
                errors.append(f"{row_num}: Missing content_title.")
                continue
            if not completion_date:
                errors.append(f"{row_num}: Missing completion_date.")
                continue
            if delivery_method not in ('online', 'otj'):
                errors.append(f"{row_num}: delivery_method must be 'online' or 'otj'. Got: '{delivery_method}'")
                continue

            try:
                parsed_date = datetime.strptime(completion_date, '%Y-%m-%d')
            except ValueError:
                errors.append(f"{row_num}: completion_date must be YYYY-MM-DD. Got: '{completion_date}'")
                continue

            # STEP 1: Find or Create Job Role
            job_role_id = False
            if job_role:
                role_rec = self.env['ct.job.role'].search([('name', '=ilike', job_role)], limit=1)
                if not role_rec:
                    role_rec = self.env['ct.job.role'].create({'name': job_role})
                job_role_id = role_rec.id

            # STEP 2: Find or Create Trainee
            trainee = self.env['ct.trainee'].search([('name', '=ilike', employee_name)], limit=1)
            if not trainee:
                trainee_email = email if email else f"{employee_name.lower().replace(' ', '.')}@imported.local"
                trainee_vals = {'name': employee_name, 'email': trainee_email}
                if employee_number:
                    trainee_vals['employee_number'] = employee_number
                if job_role_id:
                    trainee_vals['job_role_id'] = job_role_id
                trainee = self.env['ct.trainee'].create(trainee_vals)
            else:
                update_vals = {}
                if employee_number and not trainee.employee_number:
                    update_vals['employee_number'] = employee_number
                if job_role_id and not trainee.job_role_id:
                    update_vals['job_role_id'] = job_role_id
                if update_vals:
                    trainee.write(update_vals)

            # STEP 3: Find or Create Content
            content = self.env['ct.content'].with_context(active_test=False).search(
                [('title', '=ilike', content_title), ('revision', '=ilike', revision)], limit=1
            )
            if not content:
                content = self.env['ct.content'].create({
                    'title': content_title,
                    'revision': revision,
                    'active': False,
                    'delivery_method': delivery_method,
                    'content_type': 'file',
                    'frequency_months': frequency_months,
                    'description': '<p>Legacy training record imported from historical data.</p>',
                })

            # STEP 4: Check for Duplicate
            existing = self.env['ct.log'].search([
                ('trainee_id', '=', trainee.id),
                ('content_id', '=', content.id),
                ('state', '=', 'completed'),
            ], limit=1)

            if existing:
                skipped += 1
                continue

            # STEP 5: Create Completed Log
            self.env['ct.log'].create({
                'trainee_id': trainee.id,
                'content_id': content.id,
                'delivery_method': delivery_method,
                'revision': revision,
                'state': 'completed',
                'completion_date': parsed_date,
                'quiz_score': score if score else 'Historical Import',
            })
            created += 1

        error_text = '\n'.join(errors) if errors else 'None'
        summary = (
            f"Import Complete.\n\n"
            f"  Records Created    : {created}\n"
            f"  Skipped (duplicate): {skipped}\n"
            f"  Rows with errors   : {len(errors)}"
        )

        self.write({
            'state': 'done',
            'result_summary': summary,
            'result_created': created,
            'result_skipped': skipped,
            'result_errors': len(errors),
            'result_error_detail': error_text,
        })

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ct.historical.import',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'new',
        }