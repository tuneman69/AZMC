from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

# --- NEW MODEL FOR VENDOR DROPDOWN ---
class QmsVendor(models.Model):
    _name = 'qms.vendor'
    _description = 'Calibration Vendor'
    _order = 'name'

    name = fields.Char(string='Vendor Name', required=True)
    
    @api.constrains('name')
    def _check_name_unique(self):
        for rec in self:
            domain = [('name', '=ilike', rec.name), ('id', '!=', rec.id)]
            if self.search_count(domain):
                raise ValidationError(f"The Vendor '{rec.name}' already exists.")
# -------------------------------------

class QmsCalibrationEvent(models.Model):
    _name = 'qms.calibration.event'
    _description = 'QMS Calibration Event Log'
    _order = 'date_calibration desc'
    _rec_name = 'certificate_number'

    state = fields.Selection([
        ('draft', 'Draft'),
        ('done', 'Finalized')
    ], string='Status', default='draft', required=True)

    # RESTRICT DELETE to preserve history (AS9100D Requirement)
    gage_id = fields.Many2one('qms.calibration.gage', string='Gage', required=True, ondelete='restrict')
    gage_description = fields.Char(related='gage_id.description', string='Asset ID', store=True)
    
    # Used for UI Logic (Verification vs Calibration)
    control_type = fields.Selection(related='gage_id.control_type', string="Event Type", store=True)

    # UPDATED: Removed default=fields.Date.context_today
    date_calibration = fields.Date(string='Date of Calibration', default=False, required=True)
    
    calibrated_by_type = fields.Selection([
        ('internal', 'Internal'),
        ('external', 'External')
    ], string='Calibrated By', default='external')
    
    # Removed required=True to prevent DB Constraint on Drafts
    certificate_number = fields.Char(string="Certificate Number", copy=False, default='Draft')

    internal_user_id = fields.Many2one('res.users', string='Internal User', default=lambda self: self.env.user)
    
    # Linked to the new model above
    external_vendor_id = fields.Many2one('qms.vendor', string='External Vendor')
    
    as_found_condition = fields.Selection([
        ('in_tolerance', 'In Tolerance'),
        ('out_of_tolerance', 'Out of Tolerance')
    ], string="As Found Condition", required=True, default='in_tolerance')

    as_left_condition = fields.Selection([
        ('in_tolerance', 'In Tolerance'),
        ('out_of_tolerance', 'Out of Tolerance')
    ], string="As Left Condition", required=True, default='in_tolerance')

    environment = fields.Char(string="Environmental Conditions")
    standards_used = fields.Text(string="Standards Used")

    result = fields.Selection([
        ('pass', 'Pass'),
        ('fail', 'Fail')
    ], string='Final Result') 
    
    as_found = fields.Text(string='"As Found" Readings / Notes')
    was_adjusted = fields.Boolean(string="Was Adjusted?")
    as_left = fields.Text(string='"As Left" Readings / Notes')
    
    certificate_attachment = fields.Binary(string='Calibration Certificate')
    certificate_filename = fields.Char(string='Certificate Filename')

    oot_impact_analysis = fields.Text(string="Action Taken / Impact Analysis")

    @api.onchange('gage_id')
    def _onchange_gage_id(self):
        if self.gage_id and self.gage_id.calibration_strategy:
            if self.gage_id.calibration_strategy == 'external':
                self.calibrated_by_type = 'external'
                self.certificate_number = ''
            else:
                self.calibrated_by_type = 'internal'
                self.certificate_number = 'Draft'

    @api.onchange('certificate_number')
    def _onchange_check_duplicate_cert(self):
        if self.certificate_number and self.calibrated_by_type == 'external':
            domain = [
                ('certificate_number', '=', self.certificate_number.strip()),
                ('gage_id', '!=', self.gage_id.id)
            ]
            duplicates = self.search(domain)
            if duplicates:
                return {
                    'warning': {
                        'title': "Duplicate Certificate Detected",
                        'message': f"WARNING: The Certificate Number '{self.certificate_number}' has already been used on Gage: {duplicates[0].gage_description}."
                    }
                }

    @api.constrains('certificate_number', 'gage_id')
    def _check_unique_cert_per_gage(self):
        for rec in self:
            if rec.certificate_number and rec.calibrated_by_type == 'external':
                domain = [
                    ('certificate_number', '=', rec.certificate_number.strip()),
                    ('gage_id', '=', rec.gage_id.id),
                    ('id', '!=', rec.id)
                ]
                if self.search_count(domain) > 0:
                    raise ValidationError(f"DUPLICATE: This certificate is already logged for this gage.")

    def action_confirm(self):
        self.ensure_one()
        
        if not self.result:
            raise ValidationError("MISSING RESULT:\nYou cannot finalize a record without selecting Pass or Fail.")

        if self.calibrated_by_type == 'internal':
            self.certificate_number = self.env['ir.sequence'].next_by_code('qms.calibration.certificate') or 'INT-Error'

        if self.calibrated_by_type == 'external' and self.control_type == 'calibration':
            if not self.certificate_attachment:
                raise ValidationError("MISSING DOCS:\nYou must upload the vendor's certificate.")
            if not self.external_vendor_id:
                raise ValidationError("MISSING VENDOR:\nPlease select or create the External Vendor.")
            if not self.certificate_number:
                raise ValidationError("MISSING CERT #:\nPlease enter the Vendor's Certificate Number.")
        
        if (self.result == 'fail' or self.as_found_condition == 'out_of_tolerance') and not self.oot_impact_analysis:
             raise ValidationError("MISSING IMPACT ANALYSIS:\nGage failed or was OOT. You must document the Impact Analysis.")

        if self.gage_id:
            if self.result == 'pass':
                self.gage_id.write({
                    'date_last_calibration': self.date_calibration,
                    'status': 'active'
                })
            elif self.result == 'fail':
                self.gage_id.write({'status': 'out_of_service'})

        self.state = 'done'
        return {'type': 'ir.actions.act_window_close'}

    def action_reset_draft(self):
        for rec in self:
            if rec.gage_id:
                rec.gage_id.message_post(
                    body=f"⚠️ Calibration Record {rec.certificate_number} was unlocked/reset to Draft by {self.env.user.name} for corrections."
                )
            rec.state = 'draft'

    def write(self, vals):
        if self.state == 'done' and 'state' not in vals:
             raise ValidationError("AUDIT LOCK: This record is finalized. Use 'Action > Unlock for Correction' to edit.")
        return super(QmsCalibrationEvent, self).write(vals)