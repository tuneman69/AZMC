# -*- coding: utf-8 -*-
{
    'name': "Compliance Training",
    'version': '19.0.1.0.0',
    'summary': "Competency & Training Management System",
    'author': "Todd Bitner",
    'category': 'Human Resources',
    'license': 'OPL-1',
    'price': 149.00,
    'currency': 'USD',
    'depends': ['base', 'web', 'mail', 'website'],
    'web_icon': 'compliance_training,static/description/icon.png',
    'description': """
Compliance Training Management System
======================================
A complete employee training and competency management system for Odoo.

Features:
- Assign online or on-the-job (OTJ) training to employees
- Track training status: Draft, Sent, Completed, Expired
- Built-in competency quiz with True/False questions and pass score enforcement
- Digital signature capture on training completion
- Automatic training expiry and renewal tracking
- Batch assignment by Job Role or Tag
- Training revision control with auto-reassignment wizard
- Historical training import via CSV
- Printable Certificate of Competency (PDF)
- Automated email notifications via Odoo mail queue
- Full audit log with chatter tracking
- Three-tier security: User / Manager / Admin
""",
    'data': [
        'security/compliance_security.xml',
        'security/ir.model.access.csv',
        'data/mail_template_data.xml',
        'views/views.xml',
        'views/templates.xml',
        'reports/training_certificate.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'compliance_training/static/src/css/training_portal.css',
            'compliance_training/static/src/js/training_portal.js',
        ],
    },
    'installable': True,
    'application': True,
}
