from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from dateutil.relativedelta import relativedelta
from datetime import date

# --- NEW MODEL FOR DROPDOWN ---
class QmsEquipmentType(models.Model):
    _name = 'qms.equipment.type'
    _description = 'Equipment Type'
    _order = 'name'

    name = fields.Char(string='Name', required=True)
    description = fields.Char(string='Description')

    @api.constrains('name')
    def _check_name_unique(self):
        for rec in self:
            domain = [('name', '=ilike', rec.name), ('id', '!=', rec.id)]
            if self.search_count(domain):
                raise ValidationError(f"The Equipment Type '{rec.name}' already exists.")
# ------------------------------

class QmsCalibrationGage(models.Model):
    _name = 'qms.calibration.gage'
    _description = 'QMS Gage Master Record'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'name'

    active = fields.Boolean(default=True, tracking=True)

    name = fields.Char(string='Gage Control Number', copy=False, tracking=True)
    description = fields.Char(string='Asset ID Number', tracking=True, required=True)
    serial_number = fields.Char(string='Serial Number', copy=False, tracking=True)
    
    control_type = fields.Selection([
        ('calibration', 'Calibration (Traceable)'),
        ('verification', 'Verification (Process Check)')
    ], string='Control Strategy', default='calibration', required=True, tracking=True)

    calibration_strategy = fields.Selection([
        ('internal', 'Internal'),
        ('external', 'External (Vendor)')
    ], string="Provider", default='internal', tracking=True)

    equipment_type_id = fields.Many2one('qms.equipment.type', string='Equipment Type', required=True, tracking=True)

    calibration_method = fields.Char(string='Method / Procedure', tracking=True)
    acceptance_criteria = fields.Char(string='Acceptance Criteria', required=True)
    
    ownership = fields.Selection([
        ('company', 'Company Owned'),
        ('employee', 'Employee Owned'),
        ('customer', 'Customer Owned')
    ], string="Ownership", default='company', required=True, tracking=True)
    
    status = fields.Selection([
        ('active', 'Active'),
        ('out_for_calibration', 'Out for Calibration'),
        ('out_of_service', 'Out of Service'),
        ('inactive', 'Inactive')
    ], string='Status', default='active', required=True, tracking=True)
    
    owner_id = fields.Many2one('res.users', string='Person Responsible', 
                               default=lambda self: self.env.user, required=True, tracking=True)
    
    location = fields.Char(string='Location', tracking=True)
    calibration_required = fields.Boolean(string="Calibration Required", default=True, tracking=True)

    frequency_interval = fields.Integer(string='Frequency Interval', default=12, tracking=True)
    frequency_unit = fields.Selection([
        ('days', 'Days'),
        ('months', 'Months'),
        ('years', 'Years')
    ], string='Frequency Unit', default='months', tracking=True)

    date_last_calibration = fields.Date(string='Last Calibration Date', tracking=True)
    date_next_calibration = fields.Date(
        string='Next Calibration Due Date',
        compute='_compute_date_next_calibration',
        store=True,
        tracking=True
    )
    
    date_last_display = fields.Char(string='Last Cal (Display)', compute='_compute_display_dates')
    date_next_display = fields.Char(string='Next Due (Display)', compute='_compute_display_dates')
    
    event_ids = fields.One2many('qms.calibration.event', 'gage_id', string='Calibration History')

    # --- UPDATED LOGIC FOR CAPITALIZATION ---
    @api.onchange('description')
    def _onchange_description(self):
        if self.description:
            self.description = self.description.upper()

    def write(self, vals):
        # Force Uppercase on Write
        if vals.get('description'):
            vals['description'] = vals['description'].upper()

        res = super(QmsCalibrationGage, self).write(vals)
        
        if 'status' in vals and vals['status'] == 'out_for_calibration':
            for gage in self:
                existing_draft = self.env['qms.calibration.event'].search([
                    ('gage_id', '=', gage.id),
                    ('state', '=', 'draft')
                ], limit=1)
                
                if not existing_draft:
                    cal_type = 'external' if gage.calibration_strategy == 'external' else 'internal'
                    self.env['qms.calibration.event'].create({
                        'gage_id': gage.id,
                        'state': 'draft',
                        # UPDATED: Auto-created events also default to blank date
                        'date_calibration': False, 
                        'calibrated_by_type': cal_type,
                        'certificate_number': 'Draft - Out for Calibration' 
                    })
        return res

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # Force Uppercase on Create
            if vals.get('description'):
                vals['description'] = vals['description'].upper()
            
            if not vals.get('name'):
                vals['name'] = self.env['ir.sequence'].next_by_code('qms.calibration.gage') or 'SYS-New'
        return super().create(vals_list)
    # ----------------------------------------

    @api.depends('date_last_calibration', 'frequency_interval', 'frequency_unit', 'calibration_required')
    def _compute_date_next_calibration(self):
        for gage in self:
            if not gage.calibration_required or not gage.date_last_calibration or not gage.frequency_unit:
                gage.date_next_calibration = False
                continue
            delta = relativedelta()
            if gage.frequency_unit == 'days':
                delta = relativedelta(days=gage.frequency_interval)
            elif gage.frequency_unit == 'months':
                delta = relativedelta(months=gage.frequency_interval)
            elif gage.frequency_unit == 'years':
                delta = relativedelta(years=gage.frequency_interval)
            gage.date_next_calibration = gage.date_last_calibration + delta

    @api.depends('date_last_calibration', 'date_next_calibration')
    def _compute_display_dates(self):
        for gage in self:
            gage.date_last_display = gage.date_last_calibration.strftime('%b %d, %Y') if gage.date_last_calibration else ''
            gage.date_next_display = gage.date_next_calibration.strftime('%b %d, %Y') if gage.date_next_calibration else ''

    @api.onchange('status')
    def _onchange_status(self):
        if self.status in ['out_of_service', 'inactive']:
            self.calibration_required = False
        elif self.status in ['active', 'out_for_calibration']:
            self.calibration_required = True

    @api.model
    def _run_calibration_reminder(self):
        today = date.today()
        template = self.env.ref('qms_calibration.email_template_qms_calibration_reminder', raise_if_not_found=False)
        if not template:
            return
        reminder_days = [30, 15, 7]
        for days in reminder_days:
            target_date = today + relativedelta(days=days)
            gages_due = self.search([
                ('calibration_required', '=', True),
                ('status', '=', 'active'),
                ('date_next_calibration', '=', target_date)
            ])
            for gage in gages_due:
                if gage.owner_id and gage.owner_id.email:
                    template.send_mail(gage.id, force_send=True)

    @api.constrains('serial_number')
    def _check_serial_number_uniq(self):
        for rec in self:
            if rec.serial_number:
                domain = [('serial_number', '=', rec.serial_number), ('id', '!=', rec.id)]
                if self.search_count(domain):
                    raise ValidationError(_('Duplicate Serial Number detected.'))

    @api.constrains('description')
    def _check_description_uniq(self):
        for rec in self:
            if rec.description:
                domain = [('description', '=', rec.description), ('id', '!=', rec.id)]
                if self.search_count(domain):
                    raise ValidationError(_('Duplicate Asset ID Number detected.'))

    def action_open_new_event(self):
        self.ensure_one()
        existing_draft = self.env['qms.calibration.event'].search([
            ('gage_id', '=', self.id),
            ('state', '=', 'draft')
        ], limit=1)

        if existing_draft:
            return {
                'type': 'ir.actions.act_window',
                'name': 'Log Calibration Event',
                'res_model': 'qms.calibration.event',
                'res_id': existing_draft.id,
                'view_mode': 'form',
                'target': 'new',
            }

        default_type = 'external' if self.calibration_strategy == 'external' else 'internal'
        return {
            'type': 'ir.actions.act_window',
            'name': 'Log Calibration Event',
            'res_model': 'qms.calibration.event',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_gage_id': self.id,
                'default_calibrated_by_type': default_type,
            }
        }