from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from irrigation.access import ROLE_ADMIN, ROLE_PROJECT_OWNER, ROLES


class User(UserMixin):
    def __init__(self, user_doc):
        self._doc = dict(user_doc)
        self.id = str(user_doc['id'])
        self.username = user_doc['username']
        self.email = user_doc.get('email', '')
        self.password_hash = user_doc.get('password_hash', '')
        self.is_active_user = user_doc.get('is_active', True)
        self.full_name = user_doc.get('full_name') or user_doc.get('username', '')
        self.phone = user_doc.get('phone', '')
        self.cin = user_doc.get('cin', '')
        self.photo = user_doc.get('photo') or None
        profile = user_doc.get('profile')
        self.profile = profile if profile in ROLES else ROLE_PROJECT_OWNER
        # Set after a temporary password has been used: the user must choose a
        # new password before reaching any other page.
        self.must_change_password = bool(user_doc.get('must_change_password'))

    def get_id(self):
        return self.id

    @property
    def is_active(self):
        return self.is_active_user

    @property
    def is_admin(self):
        return self.profile == ROLE_ADMIN

    @property
    def sees_all_projects(self):
        return self.profile in (ROLE_ADMIN, 'Project Admin')

    @staticmethod
    def set_password(password):
        return generate_password_hash(password)

    @staticmethod
    def check_password(password_hash, password):
        return check_password_hash(password_hash, password)
