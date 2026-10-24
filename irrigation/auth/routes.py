import os
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

auth_bp = Blueprint('auth', __name__)


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
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip().lower()
        phone = request.form.get('phone', '').strip()
        cin = request.form.get('cin', '').strip()
        password = request.form.get('password', '')

        if not username or not email or not password:
            flash('Full name, email, username and password are required / الاسم والبريد واسم المستخدم وكلمة المرور مطلوبة', 'danger')
            return redirect(url_for('auth.register'))

        duplicate_checks = [{'username': username}, {'email': email}]
        if cin:
            duplicate_checks.append({'cin': cin})
        existing = mongo.db.users.find_one({'$or': duplicate_checks})
        if existing:
            flash('User already exists / المستخدم موجود بالفعل', 'danger')
            return redirect(url_for('auth.register'))

        first_user = mongo.db.users.count_documents({}) == 0
        user_id = get_next_id('users')
        photo = _photo_upload(request.files.get('photo'), user_id)
        user_doc = {
            'id': user_id,
            'full_name': full_name or username,
            'username': username,
            'email': email,
            'phone': phone,
            'password_hash': User.set_password(password),
            'profile': ROLE_ADMIN if first_user else ROLE_PROJECT_OWNER,
            'cin': cin,
            'photo': photo,
            'is_active': True,
            'created_at': datetime.utcnow(),
        }
        mongo.db.users.insert_one(user_doc)
        flash('Registration successful / تم التسجيل بنجاح', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/login.html', active_tab='register')


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
        if user_doc and User.check_password(user_doc['password_hash'], password):
            user = User(user_doc)
            if not user.is_active:
                flash('Account disabled / الحساب معطل', 'danger')
                return redirect(url_for('auth.login'))
            login_user(user)
            flash('Welcome back / مرحبا بعودتك', 'success')
            return redirect(url_for('project.index'))

        flash('Invalid credentials / بيانات اعتماد غير صالحة', 'danger')
        return redirect(url_for('auth.login'))

    return render_template('auth/login.html', active_tab='login')


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
