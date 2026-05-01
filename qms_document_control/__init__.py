from . import models
from odoo import api, SUPERUSER_ID

def post_init_hook(env):
    env['qms.document']._ensure_email_templates()