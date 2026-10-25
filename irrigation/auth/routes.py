import os
import secrets
from datetime import datetime

from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, send_from_directory, url_for)
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.utils import secure_filename

from irrigation.access import (ROLES, ROLE_ADMIN, ROLE_PROJECT_ADMIN,
                               ROLE_PROJECT_OWNER, admin_required)
from irrigation.auth.user import User
from irrigation.extensions import mongo
from irrigation.models import get_next_id
from irrigation.utils.mailer import send_email
from irrigation.utils.whatsapp import send_whatsapp

auth_bp = Blueprint('auth', __name__)

#: Endpoints a user may still reach while a new password is required.
PASSWORD_CHANGE_ENDPOINTS = ('auth.set_password', 'auth.logout', 'auth.login',
                             'auth.forgot_password', 'static')


def _temporary_password(length=12):
    """Readable one-time password, without look-alike characters."""
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def _temporary_password_email(user_doc, password):
    """Bilingual message carrying the temporary password."""
    name = user_doc.get('full_name') or user_doc.get('username') or ''
    username = user_doc.get('username') or ''
    return (
        'Hello %s,\n\n'
        'A temporary password was requested for your Irrigation Planner '
        'account (%s).\n\n'
        '    %s\n\n'
        'This password can be used only once: after signing in you will be asked '
        'to choose a new password.\n'
        'If you did not ask for it, ignore this e-mail.\n\n'
        '---\n\n'
        'مرحبا %s،\n\n'
        'تم طلب كلمة مرور مؤقتة لحسابك في نظام تخطيط الري (%s).\n\n'
        '    %s\n\n'
        'تُستعمل كلمة المرور هذه مرة واحدة فقط: بعد الدخول سيُطلب منك اختيار '
        'كلمة مرور جديدة.\n'
        'إذا لم تطلب ذلك، تجاهل هذه الرسالة.\n'
        % (name, username, password, name, username, password)
    )


def _temporary_password_whatsapp(user_doc, password):
    """Short bilingual WhatsApp copy carrying the temporary password."""
    name = user_doc.get('full_name') or user_doc.get('username') or ''
    username = user_doc.get('username') or ''
    return (
        'Hello %s,\n\n'
        'Your one-time password for the Irrigation Planner account (%s) is:\n\n'
        '    %s\n\n'
        'Use it once: after signing in you will be asked to choose a new '
        'password.\n'
        'If you did not ask for it, ignore this message.\n\n'
        '---\n\n'
        'مرحبا %s،\n\n'
        'كلمة المرور المؤقتة لحسابك في نظام تخطيط الري (%s):\n\n'
        '    %s\n\n'
        'تُستعمل مرة واحدة فقط: بعد الدخول سيُطلب منك اختيار كلمة مرور جديدة.\n'
        'إذا لم تطلب ذلك، تجاهل هذه الرسالة.'
        % (name, username, password, name, username, password)
    )


@auth_bp.before_app_request
def _require_new_password():
    """Keep a user who signed in with a temporary password on Set Password."""
    if not current_user.is_authenticated:
        return None
    if not getattr(current_user, 'must_change_password', False):
        return None
    if request.endpoint in PASSWORD_CHANGE_ENDPOINTS:
        return None
    return redirect(url_for('auth.set_password'))


def _photo_upload(file_storage, user_id):
    """Save an uploaded profile photo and return its relative path."""
    if not file_storage or not file_storage.filename:
        return None
    original = secure_filename(file_storage.filename)
    if not original:
        return None
    folder = os.path.join(current_app.config.get('UPLOAD_FOLDER', 'uploads'), 'photos')
    os.makedirs(folder, exist_ok=True)
    stored = 'user_%s_%s_%s' % (user_id, int(datetime.utcnow().timestamp()), original)
    file_storage.save(os.path.join(folder, stored))
    return 'photos/' + stored


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    """Public registration is closed: an administrator creates the accounts.

    The route is kept so an old bookmark lands on the login page, where a
    forgotten password can be requested, instead of a dead 404.
    """
    flash('Accounts are created by the administrator / '
          'يُنشئ المسؤول الحسابات', 'info')
    return redirect(url_for('auth.login'))


@auth_bp.route('/users/project-admin', methods=['POST'])
@login_required
@admin_required
def create_project_admin():
    """Create a Project Admin from the Create Project page modal."""
    full_name = request.form.get('full_name', '').strip()
    username = request.form.get('username', '').strip()
    email = request.form.get('email', '').strip().lower()
    phone = request.form.get('phone', '').strip()
    cin = request.form.get('cin', '').strip()
    password = request.form.get('password', '')

    if not username or not email or not password:
        flash('Username, email and password are required / اسم المستخدم والبريد وكلمة المرور مطلوبة', 'danger')
        return redirect(url_for('project.create'))

    duplicate_checks = [{'username': username}, {'email': email}]
    if cin:
        duplicate_checks.append({'cin': cin})
    if mongo.db.users.find_one({'$or': duplicate_checks}):
        flash('User already exists / المستخدم موجود بالفعل', 'danger')
        return redirect(url_for('project.create'))

    user_id = get_next_id('users')
    mongo.db.users.insert_one({
        'id': user_id,
        'full_name': full_name or username,
        'username': username,
        'email': email,
        'phone': phone,
        'password_hash': User.set_password(password),
        'profile': ROLE_PROJECT_ADMIN,
        'cin': cin,
        'photo': None,
        'is_active': True,
        'created_at': datetime.utcnow(),
    })
    flash('Project Admin created / تم إنشاء مسؤول المشروع', 'success')
    return redirect(url_for('project.create', admin_user_id=user_id))


#: Document kinds accepted for the signed company agreement.
AGREEMENT_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg'}


def _agreement_upload(file_storage, user_id):
    """Store the signed company agreement; returns ``(path, error)``."""
    if not file_storage or not file_storage.filename:
        return None, None

    original = secure_filename(file_storage.filename)
    suffix = original.rsplit('.', 1)[-1].lower() if '.' in original else ''
    if suffix not in AGREEMENT_EXTENSIONS:
        return None, ('The agreement document must be a PDF or an image / '
                      'وثيقة الاتفاقية يجب أن تكون PDF أو صورة')

    folder = os.path.join(current_app.config.get('UPLOAD_FOLDER', 'uploads'), 'agreements')
    os.makedirs(folder, exist_ok=True)
    stored = 'user_%s_%s_%s' % (user_id, int(datetime.utcnow().timestamp()), original)
    file_storage.save(os.path.join(folder, stored))
    return 'agreements/' + stored, None


@auth_bp.route('/users/create', methods=['POST'])
@login_required
@admin_required
def create_user():
    """Create a user from the Users page modal.

    Profiles: Local Admin is stored as Project Admin (and carries the company
    agreement), Customer as Project Owner; Admin keeps every privilege.
    """
    full_name = request.form.get('full_name', '').strip()
    username = request.form.get('username', '').strip()
    email = request.form.get('email', '').strip().lower()
    phone = request.form.get('phone', '').strip()
    cin = request.form.get('cin', '').strip()
    password = request.form.get('password', '')
    profile = request.form.get('profile', '').strip()

    if not username or not email or not password:
        flash('Username, email and password are required / اسم المستخدم والبريد وكلمة المرور مطلوبة', 'danger')
        return redirect(url_for('auth.users'))
    if profile not in ROLES:
        flash('Unknown role / دور غير معروف', 'danger')
        return redirect(url_for('auth.users'))

    duplicate_checks = [{'username': username}, {'email': email}]
    if cin:
        duplicate_checks.append({'cin': cin})
    if mongo.db.users.find_one({'$or': duplicate_checks}):
        flash('User already exists / المستخدم موجود بالفعل', 'danger')
        return redirect(url_for('auth.users'))

    user_id = get_next_id('users')

    # Only the local admin (Project Admin) signs a company agreement.
    company = None
    if profile == ROLE_PROJECT_ADMIN:
        company_name = request.form.get('company_name', '').strip()
        agreement_number = request.form.get('agreement_number', '').strip()
        agreement_date = request.form.get('agreement_date', '').strip()
        try:
            agreement_date = datetime.strptime(agreement_date, '%Y-%m-%d').date().isoformat()
        except (TypeError, ValueError):
            agreement_date = ''

        document, error = _agreement_upload(request.files.get('agreement_file'), user_id)
        if error:
            flash(error, 'danger')
            return redirect(url_for('auth.users'))

        if company_name or agreement_number or agreement_date or document:
            company = {
                'name': company_name,
                'agreement_number': agreement_number,
                'agreement_date': agreement_date,
                'agreement_file': document,
            }

    user_doc = {
        'id': user_id,
        'full_name': full_name or username,
        'username': username,
        'email': email,
        'phone': phone,
        'password_hash': User.set_password(password),
        'profile': profile,
        'cin': cin,
        'photo': None,
        'is_active': True,
        'created_at': datetime.utcnow(),
    }
    if company:
        user_doc['company'] = company

    mongo.db.users.insert_one(user_doc)
    flash('User created / تم إنشاء المستخدم', 'success')
    return redirect(url_for('auth.users'))


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        login_name = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        user_doc = mongo.db.users.find_one({
            '$or': [{'username': login_name}, {'email': login_name.lower()}]
        })
        if user_doc:
            if not user_doc.get('is_active', True):
                flash('Account disabled / الحساب معطل', 'danger')
                return redirect(url_for('auth.login'))

            if User.check_password(user_doc.get('password_hash', ''), password):
                # The remembered password cancels any reset that was still pending.
                if user_doc.get('temp_password_hash') or user_doc.get('must_change_password'):
                    mongo.db.users.update_one(
                        {'id': user_doc['id']},
                        {'$set': {'must_change_password': False},
                         '$unset': {'temp_password_hash': '',
                                    'temp_password_created_at': ''}})
                    user_doc['must_change_password'] = False
                login_user(User(user_doc))
                flash('Welcome back / مرحبا بعودتك', 'success')
                return redirect(url_for('project.index'))

            temp_hash = user_doc.get('temp_password_hash')
            if temp_hash and User.check_password(temp_hash, password):
                # Rule: the temporary password works once. It is consumed here,
                # and the user cannot leave the Set Password page until a new
                # password has been chosen.
                mongo.db.users.update_one(
                    {'id': user_doc['id']},
                    {'$set': {'must_change_password': True},
                     '$unset': {'temp_password_hash': '',
                                'temp_password_created_at': ''}})
                user_doc.pop('temp_password_hash', None)
                user_doc['must_change_password'] = True
                login_user(User(user_doc))
                flash('Temporary password accepted — choose a new password / '
                      'تم قبول كلمة المرور المؤقتة — اختر كلمة مرور جديدة', 'info')
                return redirect(url_for('auth.set_password'))

        flash('Invalid credentials / بيانات اعتماد غير صالحة', 'danger')
        return redirect(url_for('auth.login'))

    active_tab = 'forgot' if request.args.get('tab') == 'forgot' else 'login'
    return render_template('auth/login.html', active_tab=active_tab)


@auth_bp.route('/forgot-password', methods=['POST'])
def forgot_password():
    """E-mail a one-time password, or disable the account when it was lost.

    Rule: if a temporary password was already issued and never used, the user
    is presumed to have forgotten it. No second e-mail is sent; the account is
    switched to Disabled so an administrator has to step in.
    """
    email = request.form.get('email', '').strip().lower()
    if not email:
        flash('Enter your e-mail address / أدخل بريدك الإلكتروني', 'warning')
        return redirect(url_for('auth.login', tab='forgot'))

    user_doc = mongo.db.users.find_one({'email': email})
    if not user_doc:
        # Neither confirm nor deny that the address has an account.
        flash('If this address has an account, a temporary password has been '
              'sent to it / إذا كان لهذا البريد حساب، فقد أُرسلت إليه كلمة مرور مؤقتة',
              'info')
        return redirect(url_for('auth.login', tab='forgot'))

    if not user_doc.get('is_active', True):
        flash('This account is disabled. Contact the administrator / '
              'هذا الحساب معطل. اتصل بالمسؤول', 'warning')
        return redirect(url_for('auth.login', tab='forgot'))

    if user_doc.get('temp_password_hash') or user_doc.get('must_change_password'):
        mongo.db.users.update_one(
            {'id': user_doc['id']},
            {'$set': {'is_active': False},
             '$unset': {'temp_password_hash': '', 'temp_password_created_at': '',
                        'must_change_password': ''}})
        flash('A temporary password was already issued and not used, so the '
              'account is now Disabled. Contact the administrator / '
              'سبق إرسال كلمة مرور مؤقتة ولم تُستعمل، لذا تم تعطيل الحساب. '
              'اتصل بالمسؤول', 'danger')
        return redirect(url_for('auth.login', tab='forgot'))

    temporary = _temporary_password()
    mongo.db.users.update_one(
        {'id': user_doc['id']},
        {'$set': {'temp_password_hash': User.set_password(temporary),
                  'temp_password_created_at': datetime.utcnow(),
                  'must_change_password': False}})

    status = send_email(email, 'Temporary password / كلمة مرور مؤقتة',
                        _temporary_password_email(user_doc, temporary))
    whatsapp_status = None
    if user_doc.get('phone'):
        # The same one-time password also goes to the saved WhatsApp number.
        whatsapp_status, _ = send_whatsapp(
            user_doc['phone'], _temporary_password_whatsapp(user_doc, temporary))

    delivered = []
    if status == 'sent':
        delivered.append('email')
    if whatsapp_status == 'sent':
        delivered.append('whatsapp')

    if delivered:
        labels_en = {'email': 'e-mail', 'whatsapp': 'WhatsApp'}
        labels_ar = {'email': 'البريد الإلكتروني', 'whatsapp': 'واتساب'}
        flash('A temporary password has been sent by %s / تم إرسال كلمة مرور '
              'مؤقتة عبر %s'
              % (', '.join(labels_en[c] for c in delivered),
                 '، '.join(labels_ar[c] for c in delivered)), 'success')
    elif status == 'logged' or whatsapp_status == 'logged':
        flash('Delivery is not configured, so the temporary password was '
              'written to the server log / لم تُضبط وسيلة الإرسال، لذا كُتبت '
              'كلمة المرور المؤقتة في سجل الخادم', 'info')
    else:
        flash('The temporary password could not be sent; it was written to the '
              'server log / تعذر إرسال كلمة المرور المؤقتة، وكُتبت في سجل '
              'الخادم', 'warning')
    return redirect(url_for('auth.login', tab='forgot'))


@auth_bp.route('/set-password', methods=['GET', 'POST'])
@login_required
def set_password():
    """Choose a new password after signing in with a temporary one."""
    user_doc = mongo.db.users.find_one({'id': int(current_user.id)})
    if not user_doc:
        return redirect(url_for('auth.logout'))
    if not user_doc.get('must_change_password'):
        return redirect(url_for('project.index'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')
        if len(password) < 8:
            flash('The new password must be at least 8 characters / '
                  'كلمة المرور الجديدة يجب أن تكون 8 أحرف على الأقل', 'danger')
        elif password != confirm:
            flash('The two passwords do not match / كلمتا المرور غير متطابقتين',
                  'danger')
        else:
            mongo.db.users.update_one(
                {'id': int(current_user.id)},
                {'$set': {'password_hash': User.set_password(password),
                          'must_change_password': False},
                 '$unset': {'temp_password_hash': '',
                            'temp_password_created_at': ''}})
            flash('New password saved / تم حفظ كلمة المرور الجديدة', 'success')
            return redirect(url_for('project.index'))

    return render_template('auth/set_password.html', user=user_doc)


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out / تم تسجيل الخروج', 'info')
    return redirect(url_for('auth.login'))


@auth_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user_doc = mongo.db.users.find_one({'id': int(current_user.id)})
    if not user_doc:
        flash('User not found / المستخدم غير موجود', 'danger')
        return redirect(url_for('auth.logout'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        username = request.form.get('username', '').strip()
        if not username or not email:
            flash('Username and email are required / اسم المستخدم والبريد مطلوبان', 'danger')
            return redirect(url_for('auth.profile'))
        duplicates = mongo.db.users.find_one({
            'id': {'$ne': int(current_user.id)},
            '$or': [{'username': username}, {'email': email}],
        })
        if duplicates:
            flash('Username or email already in use / اسم المستخدم أو البريد مستخدم', 'danger')
            return redirect(url_for('auth.profile'))

        update = {
            'full_name': request.form.get('full_name', '').strip(),
            'username': username,
            'email': email,
            'phone': request.form.get('phone', '').strip(),
            'cin': request.form.get('cin', '').strip(),
        }

        new_password = request.form.get('new_password', '')
        if new_password:
            if not User.check_password(user_doc['password_hash'], request.form.get('current_password', '')):
                flash('Current password is wrong / كلمة المرور الحالية غير صحيحة', 'danger')
                return redirect(url_for('auth.profile'))
            update['password_hash'] = User.set_password(new_password)

        photo = _photo_upload(request.files.get('photo'), int(current_user.id))
        if photo:
            update['photo'] = photo

        mongo.db.users.update_one({'id': int(current_user.id)}, {'$set': update})
        flash('Profile updated / تم تحديث الملف الشخصي', 'success')
        return redirect(url_for('auth.profile'))

    user_doc['_id'] = str(user_doc['_id'])
    return render_template('auth/profile.html', user=user_doc, roles=ROLES)


@auth_bp.route('/users')
@login_required
@admin_required
def users():
    user_list = list(mongo.db.users.find({}).sort('id', 1))
    for user_doc in user_list:
        user_doc['_id'] = str(user_doc['_id'])
    return render_template('auth/users.html', users=user_list, roles=ROLES)


@auth_bp.route('/users/<int:user_id>/role', methods=['POST'])
@login_required
@admin_required
def set_role(user_id):
    role = request.form.get('profile', '').strip()
    if role not in ROLES:
        flash('Unknown role / دور غير معروف', 'danger')
        return redirect(url_for('auth.users'))

    target = mongo.db.users.find_one({'id': user_id})
    if not target:
        flash('User not found / المستخدم غير موجود', 'danger')
        return redirect(url_for('auth.users'))

    if target.get('profile') == ROLE_ADMIN and role != ROLE_ADMIN:
        admins = mongo.db.users.count_documents({'profile': ROLE_ADMIN})
        if admins <= 1:
            flash('At least one Admin is required / يجب الإبقاء على مسؤول واحد على الأقل', 'danger')
            return redirect(url_for('auth.users'))

    mongo.db.users.update_one({'id': user_id}, {'$set': {'profile': role}})
    flash('Role updated / تم تحديث الدور', 'success')
    return redirect(url_for('auth.users'))


@auth_bp.route('/users/<int:user_id>/active', methods=['POST'])
@login_required
@admin_required
def set_active(user_id):
    target = mongo.db.users.find_one({'id': user_id})
    if not target:
        flash('User not found / المستخدم غير موجود', 'danger')
        return redirect(url_for('auth.users'))
    if target.get('id') == int(current_user.id):
        flash('You cannot deactivate your own account / لا يمكنك تعطيل حسابك', 'danger')
        return redirect(url_for('auth.users'))
    active = request.form.get('is_active') == '1'
    mongo.db.users.update_one({'id': user_id}, {'$set': {'is_active': active}})
    flash('Account updated / تم تحديث الحساب', 'success')
    return redirect(url_for('auth.users'))


@auth_bp.route('/users/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    """Remove a user account from the Users table.

    An admin cannot delete its own account, and the last remaining Admin is
    kept so the application always has someone who can administer it.
    """
    target = mongo.db.users.find_one({'id': user_id})
    if not target:
        flash('User not found / المستخدم غير موجود', 'danger')
        return redirect(url_for('auth.users'))
    if user_id == int(current_user.id):
        flash('You cannot delete your own account / لا يمكنك حذف حسابك', 'danger')
        return redirect(url_for('auth.users'))
    if target.get('profile') == ROLE_ADMIN:
        admins = mongo.db.users.count_documents({'profile': ROLE_ADMIN})
        if admins <= 1:
            flash('At least one Admin is required / يجب الإبقاء على مسؤول واحد على الأقل', 'danger')
            return redirect(url_for('auth.users'))

    mongo.db.users.delete_one({'id': user_id})
    flash('User deleted / تم حذف المستخدم', 'success')
    return redirect(url_for('auth.users'))


@auth_bp.route('/uploads/<path:filename>')
@login_required
def uploaded_file(filename):
    return send_from_directory(current_app.config.get('UPLOAD_FOLDER', 'uploads'), filename)
