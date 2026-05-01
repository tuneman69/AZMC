from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from dateutil.relativedelta import relativedelta
import base64
import io
import zipfile

# --- ROBUST MARKUP IMPORT ---
try:
    from odoo.tools import Markup
except ImportError:
    try:
        from markupsafe import Markup
    except ImportError:
        def Markup(html):
            return html


class QmsRejectWizard(models.TransientModel):
    _name = 'qms.reject.wizard'
    _description = 'QMS Document Rejection Reason'

    document_id = fields.Many2one('qms.document', string='Document', required=True)
    reason = fields.Text(string='Reason for Rejection', required=True)

    def action_confirm_reject(self):
        self.ensure_one()
        doc = self.document_id
        reason = self.reason

        if doc.state == 'qm_approval':
            doc.state = 'review'
            doc.message_post(
                body=Markup(f"<span style='color:red;'><b>Returned to Reviewer</b> by Quality Manager {self.env.user.name}.</span><br/><b>Reason:</b> {reason}"),
                partner_ids=[doc.reviewer_id.partner_id.id] if doc.reviewer_id else [],
                message_type='notification',
                subtype_xmlid='mail.mt_comment'
            )
            try:
                template = self.env.ref('qms_document_control.email_template_qms_doc_qm_reject', raise_if_not_found=False)
                if template:
                    template.with_context(reject_reason=reason).send_mail(doc.id, force_send=True)
            except Exception as e:
                doc.message_post(body=Markup(f"<span style='color:red;'>EMAIL SEND ERROR: {e}</span>"))

        elif doc.state == 'review':
            doc.state = 'draft'
            doc.approved_by = False
            doc.approved_date = False
            doc.message_post(
                body=Markup(f"<span style='color:red;'><b>Returned to Draft</b> by Reviewer {self.env.user.name}.</span><br/><b>Reason:</b> {reason}"),
                partner_ids=[doc.process_owner_id.partner_id.id] if doc.process_owner_id else [],
                message_type='notification',
                subtype_xmlid='mail.mt_comment'
            )
            try:
                template = self.env.ref('qms_document_control.email_template_qms_doc_reject', raise_if_not_found=False)
                if template:
                    template.with_context(reject_reason=reason).send_mail(doc.id, force_send=True)
            except Exception as e:
                doc.message_post(body=Markup(f"<span style='color:red;'>EMAIL SEND ERROR: {e}</span>"))

        return {'type': 'ir.actions.act_window_close'}


class QmsEditRolesWizard(models.TransientModel):
    _name = 'qms.edit.roles.wizard'
    _description = 'Edit Document Roles'

    document_id = fields.Many2one('qms.document', string='Document', required=True)
    process_owner_id = fields.Many2one('res.users', string='Process Owner')
    reviewer_id = fields.Many2one('res.users', string='Document Reviewer')
    quality_manager_id = fields.Many2one('res.users', string='Quality Manager')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        doc_id = self.env.context.get('default_document_id')
        if doc_id:
            doc = self.env['qms.document'].browse(doc_id)
            res.update({
                'process_owner_id': doc.process_owner_id.id or False,
                'reviewer_id': doc.reviewer_id.id or False,
                'quality_manager_id': doc.quality_manager_id.id or False,
            })
        return res

    def action_save(self):
        self.ensure_one()
        doc = self.document_id
        changes = []

        old_owner = doc.process_owner_id.name or 'None'
        old_reviewer = doc.reviewer_id.name or 'None'
        old_qm = doc.quality_manager_id.name or 'None'
        new_owner = self.process_owner_id.name or 'None'
        new_reviewer = self.reviewer_id.name or 'None'
        new_qm = self.quality_manager_id.name or 'None'

        if self.process_owner_id != doc.process_owner_id:
            changes.append(f"Process Owner: {old_owner} → {new_owner}")
        if self.reviewer_id != doc.reviewer_id:
            changes.append(f"Document Reviewer: {old_reviewer} → {new_reviewer}")
        if self.quality_manager_id != doc.quality_manager_id:
            changes.append(f"Quality Manager: {old_qm} → {new_qm}")

        doc.write({
            'process_owner_id': self.process_owner_id.id or False,
            'reviewer_id': self.reviewer_id.id or False,
            'quality_manager_id': self.quality_manager_id.id or False,
        })

        if changes:
            change_lines = "<br/>".join(changes)
            doc.message_post(
                body=Markup(f"<b>Roles updated by {self.env.user.name}:</b><br/>{change_lines}"),
                message_type='notification',
                subtype_xmlid='mail.mt_comment'
            )

        return {'type': 'ir.actions.act_window_close'}


class QmsFolder(models.Model):
    _name = 'qms.folder'
    _description = 'QMS Directory'
    _parent_name = 'parent_id'
    _order = 'complete_name'
    _rec_name = 'complete_name'

    name = fields.Char('Folder Name', required=True)
    parent_id = fields.Many2one('qms.folder', string='Parent Folder', index=True, ondelete='cascade')
    child_ids = fields.One2many('qms.folder', 'parent_id', string='Sub-Folders')
    complete_name = fields.Char('Full Path', compute='_compute_complete_name', store=True, recursive=True)

    @api.depends('name', 'parent_id.complete_name')
    def _compute_complete_name(self):
        for folder in self:
            if folder.parent_id:
                folder.complete_name = '%s / %s' % (folder.parent_id.complete_name, folder.name)
            else:
                folder.complete_name = folder.name


class QmsDocument(models.Model):
    _name = 'qms.document'
    _description = 'QMS Controlled Document'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'state_sequence asc, name asc, revision desc'

    active = fields.Boolean('Active', default=True, tracking=True)

    # --- STATE SORT SEQUENCE ---
    state_sequence = fields.Integer(
        string='State Sequence',
        compute='_compute_state_sequence',
        store=True,
        index=True,
    )

    @api.depends('state')
    def _compute_state_sequence(self):
        sequence_map = {
            'draft': 1,
            'review': 2,
            'qm_approval': 3,
            'published': 4,
            'obsolete': 5,
        }
        for doc in self:
            doc.state_sequence = sequence_map.get(doc.state, 99)

    # --- HISTORY / SMART BUTTON LOGIC ---
    revision_count = fields.Integer(compute='_compute_revision_count', string="Revision Count")

    def _compute_revision_count(self):
        for doc in self:
            doc.revision_count = self.search_count([
                ('name', '=', doc.name),
                '|', ('active', '=', True), ('active', '=', False)
            ])

    def action_view_revisions(self):
        self.ensure_one()
        return {
            'name': f'History: {self.name}',
            'type': 'ir.actions.act_window',
            'res_model': 'qms.document',
            'view_mode': 'list,form',
            'domain': [('name', '=', self.name), '|', ('active', '=', True), ('active', '=', False)],
            'context': {'default_name': self.name},
        }

    # --- MASS DOWNLOAD LOGIC (ZIPPING) ---
    def action_mass_download(self):
        if not self:
            return

        if len(self) == 1:
            if not self.doc_file:
                raise UserError("No file attached to this document.")
            return {
                'type': 'ir.actions.act_url',
                'url': '/web/content/qms.document/%s/doc_file/%s?download=true' % (self.id, self.file_name),
                'target': 'self',
            }

        stream = io.BytesIO()
        try:
            with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as zip_archive:
                for doc in self:
                    if doc.doc_file and doc.file_name:
                        file_content = base64.b64decode(doc.doc_file)
                        safe_name = f"{doc.name}_{doc.file_name}"
                        zip_archive.writestr(safe_name, file_content)
        except Exception as e:
            raise UserError(f"Error creating zip file: {e}")

        zip_filename = "QMS_Documents_Batch.zip"
        attachment = self.env['ir.attachment'].create({
            'name': zip_filename,
            'type': 'binary',
            'datas': base64.b64encode(stream.getvalue()),
            'mimetype': 'application/zip',
        })

        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/ir.attachment/%s/datas?download=true&filename=%s' % (attachment.id, zip_filename),
            'target': 'self',
        }

    # --- PREVENT DUPLICATES ---
    @api.constrains('name', 'revision')
    def _check_unique_doc(self):
        for doc in self:
            domain = [
                ('name', '=', doc.name),
                ('revision', '=', doc.revision),
                ('id', '!=', doc.id)
            ]
            if self.search_count(domain) > 0:
                raise ValidationError(f"The document {doc.name} (Rev {doc.revision}) already exists. Duplicates are not allowed.")

    # --- ENFORCE NUMERIC REVISIONS ---
    @api.constrains('revision')
    def _check_revision_numeric(self):
        for doc in self:
            if doc.revision:
                try:
                    val = int(doc.revision)
                    if val < 0:
                        raise ValidationError("Revision must be a non-negative whole number (e.g. 0, 1, 2).")
                except ValueError:
                    raise ValidationError(
                        f"Revision '{doc.revision}' is not valid. Revision must be a whole number (e.g. 0, 1, 2)."
                    )

    name = fields.Char(string='Document Number', required=True, copy=False, readonly=False)
    title = fields.Char(string='Title', required=True, tracking=True)
    keywords = fields.Text(string='Search Keywords')
    revision = fields.Char(string='Revision', default='0', tracking=True, copy=False)

    # --- DOCUMENT TYPE — ordered by ISO 9001/IATF 16949/AS9100D hierarchy ---
    # Level 1: Manual, Scope, Policy
    # Level 2: Procedure
    # Level 3: Work Instruction, Specification
    # Level 4: Form, Record
    # Supporting: Training Material
    doc_type = fields.Selection([
        ('manual', 'Manual'),
        ('scope', 'Scope'),
        ('policy', 'Policy'),
        ('procedure', 'Procedure'),
        ('work_instruction', 'Work Instruction'),
        ('specification', 'Specification'),
        ('form', 'Form'),
        ('record', 'Record'),
        ('training_material', 'Training Material'),
    ], string='Document Type', tracking=True, index=True)

    folder_id = fields.Many2one('qms.folder', string='Folder Location', required=True, tracking=True, index=True)

    # required=True removed from model level — enforced at action_submit gate instead
    doc_file = fields.Binary(string='Document File', attachment=True)
    file_name = fields.Char(string='File Name', tracking=True)

    # --- Roles ---
    process_owner_id = fields.Many2one('res.users', string='Process Owner', default=lambda self: self.env.user, tracking=True)
    reviewer_id = fields.Many2one(
        'res.users', string='Document Reviewer', tracking=True,
        help="Must be a QMS Manager group member. Responsible for verifying template layout and format."
    )
    quality_manager_id = fields.Many2one('res.users', string='Quality Manager', tracking=True)

    # --- Workflow ---
    state = fields.Selection([
        ('draft', 'Draft'),
        ('review', 'Under Review'),
        ('qm_approval', 'QM Approval'),
        ('published', 'Published'),
        ('obsolete', 'Obsolete')
    ], string='Status', default='draft', tracking=True)

    approved_date = fields.Datetime(string='Date Approved', readonly=True, copy=False)
    approved_by = fields.Many2one('res.users', string='Approved By', readonly=True, copy=False)

    # --- METRICS COMPUTED FIELDS ---
    create_year = fields.Char(string='Year Created', compute='_compute_create_year', store=True)
    approved_year = fields.Char(string='Year Published', compute='_compute_approved_year', store=True)
    days_to_publish = fields.Integer(string='Days to Publish', compute='_compute_days_to_publish', store=True)

    @api.depends('create_date')
    def _compute_create_year(self):
        for doc in self:
            doc.create_year = str(doc.create_date.year) if doc.create_date else ''

    @api.depends('approved_date')
    def _compute_approved_year(self):
        for doc in self:
            doc.approved_year = str(doc.approved_date.year) if doc.approved_date else ''

    @api.depends('create_date', 'approved_date')
    def _compute_days_to_publish(self):
        for doc in self:
            if doc.create_date and doc.approved_date:
                doc.days_to_publish = (doc.approved_date.date() - doc.create_date.date()).days
            else:
                doc.days_to_publish = 0

    # --- ANNUAL REVIEW FIELDS ---
    next_review_date = fields.Date(string="Next Review Due", readonly=True, tracking=True)
    is_overdue = fields.Boolean(compute='_compute_is_overdue', store=False)

    @api.depends('next_review_date', 'state')
    def _compute_is_overdue(self):
        for doc in self:
            if doc.state == 'published' and doc.next_review_date and doc.next_review_date < fields.Date.today():
                doc.is_overdue = True
            else:
                doc.is_overdue = False

    # --- CLEAN NAME LOGIC ---
    def _get_clean_name(self, raw_name):
        if not raw_name:
            return False
        clean = raw_name.strip().upper()
        clean = clean.replace(' ', '-').replace('_', '-')
        while '--' in clean:
            clean = clean.replace('--', '-')
        return clean

    @api.onchange('name')
    def _onchange_clean_name(self):
        if self.name:
            self.name = self._get_clean_name(self.name)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name'):
                vals['name'] = self._get_clean_name(vals['name'])

            if vals.get('name'):
                existing_doc = self.search([('name', '=', vals['name'])], limit=1)
                if existing_doc and not self.env.context.get('force_revision_create'):
                    raise ValidationError(
                        f"Document Series '{vals['name']}' already exists.\n\n"
                        "You cannot manually create a new version.\n"
                        "Please go to the existing document and click 'Create Revision'."
                    )

            if not vals.get('name'):
                vals['name'] = self.env['ir.sequence'].next_by_code('qms.document') or 'New'

        documents = super(QmsDocument, self).create(vals_list)
        for doc in documents:
            doc._manage_followers()
        return documents

    def write(self, vals):
        if vals.get('name'):
            vals['name'] = self._get_clean_name(vals['name'])
        res = super(QmsDocument, self).write(vals)
        if 'process_owner_id' in vals or 'reviewer_id' in vals or 'quality_manager_id' in vals:
            self._manage_followers()
        return res

    def _manage_followers(self):
        for doc in self:
            partner_ids = []
            if doc.process_owner_id:
                partner_ids.append(doc.process_owner_id.partner_id.id)
            if doc.reviewer_id:
                partner_ids.append(doc.reviewer_id.partner_id.id)
            if doc.quality_manager_id:
                partner_ids.append(doc.quality_manager_id.partner_id.id)
            if partner_ids:
                doc.message_subscribe(partner_ids=partner_ids)

    # --- WORKFLOW ACTIONS ---

    def action_submit(self):
        if not self.doc_file:
            raise UserError("You must upload a file before submitting for review.")
        if not self.reviewer_id:
            raise UserError("Please select a Document Reviewer before submitting.")
        if not self.quality_manager_id:
            raise UserError("Please select a Quality Manager before submitting.")
        self.state = 'review'
        self.message_post(
            body=f"Document submitted for review by {self.env.user.name}. Awaiting Document Reviewer approval.",
            partner_ids=[self.reviewer_id.partner_id.id],
            message_type='notification',
            subtype_xmlid='mail.mt_comment'
        )
        try:
            template = self.env.ref('qms_document_control.email_template_qms_doc_submitted', raise_if_not_found=False)
            if template:
                template.send_mail(self.id, force_send=True)
        except Exception as e:
            self.message_post(body=Markup(f"<span style='color:red;'>EMAIL SEND ERROR: {e}</span>"))

    def action_approve_reviewer(self):
        """Document Reviewer approves format/layout — advances to QM Approval."""
        if self.env.user != self.reviewer_id and not self.env.is_superuser():
            raise UserError(f"Only the assigned Document Reviewer ({self.reviewer_id.name}) can approve at this stage.")
        self.state = 'qm_approval'
        self.message_post(
            body=f"Document Reviewer ({self.env.user.name}) approved. Forwarded to Quality Manager for final publish.",
            partner_ids=[self.quality_manager_id.partner_id.id],
            message_type='notification',
            subtype_xmlid='mail.mt_comment'
        )
        try:
            template = self.env.ref('qms_document_control.email_template_qms_doc_reviewer_approved', raise_if_not_found=False)
            if template:
                template.send_mail(self.id, force_send=True)
        except Exception as e:
            self.message_post(body=Markup(f"<span style='color:red;'>EMAIL SEND ERROR: {e}</span>"))

    def action_approve_qm(self):
        """Kept for backwards compatibility — not used in new workflow."""
        self.state = 'qm_approval'
        self.message_post(body="Department review complete. Waiting for Final Publish.")

    def action_publish(self):
        self.ensure_one()
        if self.env.user != self.quality_manager_id and not self.env.is_superuser():
            raise UserError(f"Only the assigned Quality Manager ({self.quality_manager_id.name}) can Publish.")

        existing_published = self.search([
            ('name', '=', self.name),
            ('id', '!=', self.id),
            ('state', '=', 'published')
        ], limit=1)

        if existing_published and existing_published.revision and self.revision:
            if int(existing_published.revision) > int(self.revision):
                raise UserError(
                    f"CANNOT PUBLISH: Revision {existing_published.revision} is already active.\n"
                    f"You cannot publish an older revision ({self.revision}) over a newer one."
                )

        self.state = 'published'
        self.approved_date = fields.Datetime.now()
        self.approved_by = self.env.user
        self.next_review_date = fields.Date.today() + relativedelta(years=1)

        old_versions = self.search([
            ('name', '=', self.name),
            ('id', '!=', self.id),
            ('state', '=', 'published')
        ])
        if old_versions:
            old_versions.write({'state': 'obsolete'})
            for old in old_versions:
                old.message_post(body=f"Marked Obsolete by release of Revision {self.revision}")

        try:
            template = self.env.ref('qms_document_control.email_template_qms_doc_publish', raise_if_not_found=False)
            if template:
                template.send_mail(self.id, force_send=True)
        except Exception as e:
            self.message_post(body=Markup(f"<span style='color:red;'>EMAIL SEND ERROR: {e}</span>"))

        self.message_post(body=Markup(f"<b>OFFICIAL RELEASE</b> (Rev {self.revision})"))

    def action_confirm_review(self):
        self.ensure_one()
        if self.state != 'published':
            raise UserError("You can only review Published documents.")

        self.next_review_date = fields.Date.today() + relativedelta(years=1)
        self.message_post(body=f"Annual Review confirmed by {self.env.user.name}. Valid for another year.")

    def action_send_reminder(self):
        self.ensure_one()
        if not self.process_owner_id:
            raise UserError("There is no Process Owner assigned to this document.")

        try:
            template = self.env.ref('qms_document_control.email_template_qms_doc_reminder', raise_if_not_found=False)
            if template:
                template.send_mail(self.id, force_send=True)
        except Exception as e:
            self.message_post(body=Markup(f"<span style='color:red;'>EMAIL SEND ERROR: {e}</span>"))

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Email Sent',
                'message': f'Reminder sent to {self.process_owner_id.name}',
                'type': 'success',
                'sticky': False,
            }
        }

    def action_reset_draft(self):
        """Opens rejection reason wizard — cascades based on current state."""
        self.ensure_one()
        if self.state not in ('review', 'qm_approval'):
            raise UserError("This document cannot be rejected from its current state.")
        return {
            'name': 'Reason for Rejection',
            'type': 'ir.actions.act_window',
            'res_model': 'qms.reject.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_document_id': self.id},
        }

    def action_edit_roles(self):
        """Reassign roles on any document regardless of state.
        Button is visible to all users — access is enforced here in Python.
        Non-admins receive a clear error message."""
        self.ensure_one()
        if not self.env.user.has_group('qms_document_control.group_qms_admin'):
            raise UserError("Only QMS Administrators can edit document roles.")
        return {
            'name': 'Edit Document Roles',
            'type': 'ir.actions.act_window',
            'res_model': 'qms.edit.roles.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_document_id': self.id},
        }

    def action_new_revision(self):
        self.ensure_one()
        try:
            next_rev = str(int(self.revision) + 1)
        except ValueError:
            next_rev = '1'

        existing_draft = self.search([
            ('name', '=', self.name),
            ('revision', '=', next_rev),
            ('state', '=', 'draft'),
        ], limit=1)

        if existing_draft:
            return {
                'type': 'ir.actions.act_window',
                'name': f'Revision {next_rev} (Draft in Progress)',
                'res_model': 'qms.document',
                'res_id': existing_draft.id,
                'view_mode': 'form',
                'target': 'current',
            }

        new_doc = self.with_context(force_revision_create=True).copy({
            'name': self.name,
            'revision': next_rev,
            'state': 'draft',
            'approved_by': False,
            'approved_date': False,
            'doc_file': False,
            'file_name': False,
            'next_review_date': False,
            'reviewer_id': self.reviewer_id.id or False,
            'active': True,
        })

        return {
            'type': 'ir.actions.act_window',
            'name': f'New Revision ({next_rev})',
            'res_model': 'qms.document',
            'res_id': new_doc.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_open_file(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/qms.document/%s/doc_file/%s' % (self.id, self.file_name),
            'target': 'new',
        }

    @api.model
    def _ensure_email_templates(self):
        """Creates QMS email templates in the database if they don't exist."""
        MailTemplate = self.env['mail.template']
        model_id = self.env['ir.model']._get_id('qms.document')
        IrModelData = self.env['ir.model.data']

        templates = [
            {
                'xmlid': 'email_template_qms_doc_submitted',
                'name': 'QMS Document: Submitted for Review',
                'subject': 'Action Required: Review Requested - {{ object.name }} - {{ object.title }}',
                'email_to': '{{ object.reviewer_id.email }}',
                'body': '''<div>
                    <p>Hello <t t-out="object.reviewer_id.name"/>,</p>
                    <p><strong>A document has been submitted and requires your review and approval.</strong></p>
                    <ul>
                        <li><strong>Document Number:</strong> <t t-out="object.name"/></li>
                        <li><strong>Title:</strong> <t t-out="object.title"/></li>
                        <li><strong>Revision:</strong> <t t-out="object.revision"/></li>
                        <li><strong>Submitted By:</strong> <t t-out="object.process_owner_id.name"/></li>
                    </ul>
                    <p>Please log in, verify the template layout and formatting, then click <strong>Approve (Reviewer)</strong> or <strong>Reject / Return</strong>.</p>
                </div>''',
            },
            {
                'xmlid': 'email_template_qms_doc_reviewer_approved',
                'name': 'QMS Document: Reviewer Approved - Awaiting QM Publish',
                'subject': 'Action Required: Final Publish - {{ object.name }} - {{ object.title }}',
                'email_to': '{{ object.quality_manager_id.email }}',
                'body': '''<div>
                    <p>Hello <t t-out="object.quality_manager_id.name"/>,</p>
                    <p><strong>A document has passed Document Reviewer approval and is awaiting your final publish.</strong></p>
                    <ul>
                        <li><strong>Document Number:</strong> <t t-out="object.name"/></li>
                        <li><strong>Title:</strong> <t t-out="object.title"/></li>
                        <li><strong>Revision:</strong> <t t-out="object.revision"/></li>
                        <li><strong>Process Owner:</strong> <t t-out="object.process_owner_id.name"/></li>
                        <li><strong>Reviewed By:</strong> <t t-out="object.reviewer_id.name"/></li>
                    </ul>
                    <p>Please log in, review the document content, then click <strong>Final Publish</strong> or <strong>Reject / Return</strong>.</p>
                </div>''',
            },
            {
                'xmlid': 'email_template_qms_doc_qm_reject',
                'name': 'QMS Document: Returned by QM to Reviewer',
                'subject': 'RETURNED: {{ object.name }} - {{ object.title }} - Please Re-Review',
                'email_to': '{{ object.reviewer_id.email }}',
                'body': '''<div>
                    <p>Hello <t t-out="object.reviewer_id.name"/>,</p>
                    <p style="color:red;font-weight:bold;">RETURNED FOR RE-REVIEW</p>
                    <p>The Quality Manager has returned document <strong><t t-out="object.name"/> - <t t-out="object.title"/></strong> (Rev <t t-out="object.revision"/>) to the Under Review stage.</p>
                    <p><strong>Action Required:</strong> Please log in, check the chatter for comments, and re-approve when ready.</p>
                    <p><em>Returned by: <t t-out="user.name"/></em></p>
                </div>''',
            },
            {
                'xmlid': 'email_template_qms_doc_reminder',
                'name': 'QMS Document: Annual Review Reminder',
                'subject': 'Action Required: {{ object.name }} Annual Review',
                'email_to': '{{ object.process_owner_id.email }}',
                'body': '''<div>
                    <p>Hello <t t-out="object.process_owner_id.name"/>,</p>
                    <p><strong>REMINDER: Annual Review Required</strong></p>
                    <ul>
                        <li><strong>Document:</strong> <t t-out="object.name"/></li>
                        <li><strong>Title:</strong> <t t-out="object.title"/></li>
                        <li><strong>Next Review Date:</strong> <t t-out="object.next_review_date"/></li>
                    </ul>
                    <p>Please review this document. If still valid, click <strong>Confirm Review</strong> in the system.</p>
                </div>''',
            },
            {
                'xmlid': 'email_template_qms_doc_reject',
                'name': 'QMS Document: Rejection Notice',
                'subject': 'REJECTED: {{ object.name }} - {{ object.title }}',
                'email_to': '{{ object.process_owner_id.email }}',
                'body': '''<div>
                    <p>Hello <t t-out="object.process_owner_id.name"/>,</p>
                    <p style="color:red;font-weight:bold;">REJECTED / RETURNED TO DRAFT</p>
                    <p>The document <strong><t t-out="object.name"/></strong> (Rev <t t-out="object.revision"/>) was returned to draft status.</p>
                    <p><strong>Action Required:</strong> Please log in, review the chatter comments, and revise the document.</p>
                    <p><em>Returned by: <t t-out="user.name"/></em></p>
                </div>''',
            },
            {
                'xmlid': 'email_template_qms_doc_publish',
                'name': 'QMS Document: Publication Notice',
                'subject': 'PUBLISHED: {{ object.name }} - {{ object.title }}',
                'email_to': '{{ object.process_owner_id.email }},{{ object.reviewer_id.email }},{{ object.quality_manager_id.email }}',
                'body': '''<div>
                    <p><strong>OFFICIAL RELEASE</strong></p>
                    <p>Document <strong><t t-out="object.name"/> - <t t-out="object.title"/></strong> (Rev <t t-out="object.revision"/>) is now published and valid for production use.</p>
                    <ul>
                        <li><strong>Approved By:</strong> <t t-out="object.approved_by.name"/></li>
                        <li><strong>Approved Date:</strong> <t t-out="object.approved_date"/></li>
                        <li><strong>Next Review Due:</strong> <t t-out="object.next_review_date"/></li>
                    </ul>
                </div>''',
            },
        ]

        for t in templates:
            existing = IrModelData.search([
                ('module', '=', 'qms_document_control'),
                ('name', '=', t['xmlid']),
            ], limit=1)
            if existing:
                template = MailTemplate.browse(existing.res_id)
                if template.exists():
                    template.unlink()
                existing.unlink()
            mail_server = self.env['ir.mail_server'].search([
                ('name', 'ilike', 'QMS')
            ], limit=1)
            record = MailTemplate.create({
                'name': t['name'],
                'model_id': model_id,
                'subject': t['subject'],
                'email_from': 'qms-documentation@polycharge.com',
                'reply_to': '{{ user.email_formatted }}',
                'email_to': t['email_to'],
                'body_html': t['body'],
                'auto_delete': True,
                'use_default_to': False,
                'mail_server_id': mail_server.id if mail_server else False,
            })
            IrModelData.create({
                'module': 'qms_document_control',
                'name': t['xmlid'],
                'model': 'mail.template',
                'res_id': record.id,
                'noupdate': False,
            })