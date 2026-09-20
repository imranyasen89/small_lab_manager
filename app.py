from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, g
from database import db
from models import Patient, Test, Parameter, PatientTest, Result, User, Setting, RefundRecord, Doctor
from datetime import datetime, date, timedelta
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-pro-lab-2026')

db.init_app(app)

# Custom Jinja2 filter: allows {{ list | enumerate }} in templates
app.jinja_env.filters['enumerate'] = enumerate

login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(user_id)

# RBAC Decorator
def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('login', next=request.url))
            if current_user.role not in roles and current_user.role not in ['Admin', 'SuperAdmin']:
                flash('You do not have permission to access this page.', 'error')
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# ── Settings cache (per-request, using Flask g) ──────────────────────────────
def get_settings():
    """Return settings dict, cached in g so DB is hit at most once per request."""
    if 'settings' not in g:
        try:
            g.settings = {s.key: s.value for s in Setting.query.all()}
        except Exception:
            g.settings = {}
    return g.settings


# Global Context
@app.context_processor
def inject_globals():
    settings = get_settings()
    return dict(
        lab_name=settings.get('lab_name', 'Ideal Diagnostic Center'),
        lab_address=settings.get('lab_address', ''),
        lab_contact=settings.get('lab_contact', ''),
        footer_dr1=settings.get('footer_dr1', ''),
        footer_dr2=settings.get('footer_dr2', ''),
        footer_dr3=settings.get('footer_dr3', ''),
        current_user=current_user,
        now=datetime.now(),
    )

# Bootstrap default settings and admin user if empty
def init_defaults():
    try:
        if Setting.query.count() == 0:
            db.session.add(Setting(key='lab_name', value='Ideal Diagnostic Center'))
            db.session.add(Setting(key='lab_address', value='123 Health Avenue, Medical District'))
            db.session.add(Setting(key='lab_contact', value='+92 3027563119'))
            db.session.add(Setting(key='footer_dr1', value='Dr. A. Pathologist'))
            db.session.add(Setting(key='footer_dr2', value=''))
            db.session.add(Setting(key='footer_dr3', value=''))
            db.session.commit()

        if User.query.count() == 0:
            admin_user = User(
                username='admin',
                password_hash=generate_password_hash('admin123'),
                role='Admin',
                name='System Administrator'
            )
            db.session.add(admin_user)
            db.session.commit()
    except Exception as e:
        app.logger.warning(f"Default initialization warning: {e}")

with app.app_context():
    init_defaults()

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('dashboard'))
        else:
            flash('Invalid username or password', 'error')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/users', methods=['GET', 'POST'])
@login_required
@role_required('Admin')
def manage_users():
    if request.method == 'POST':
        name = request.form.get('name')
        username = request.form.get('username')
        password = request.form.get('password')
        role = request.form.get('role')
        if name and username and password and role:
            if User.query.filter_by(username=username).first():
                flash('Username already exists.', 'error')
            else:
                new_user = User(name=name, username=username, password_hash=generate_password_hash(password), role=role)
                db.session.add(new_user)
                db.session.commit()
                flash('User created successfully!', 'success')
                return redirect(url_for('manage_users'))
    users = User.query.all()
    return render_template('users/index.html', users=users)

@app.route('/users/<int:user_id>/delete', methods=['POST'])
@login_required
@role_required('Admin')
def delete_user(user_id):
    if current_user.id == user_id:
        flash('You cannot delete your own account.', 'error')
        return redirect(url_for('manage_users'))
    user = User.query.get_or_404(user_id)
    db.session.delete(user)
    db.session.commit()
    flash('User deleted successfully.', 'success')
    return redirect(url_for('manage_users'))

@app.route('/settings', methods=['GET', 'POST'])
@login_required
@role_required('Admin')
def settings():
    if request.method == 'POST':
        if current_user.role == 'SuperAdmin':
            keys_to_update = ['lab_name', 'lab_address', 'lab_contact', 'footer_dr1', 'footer_dr2', 'footer_dr3']
        else:
            keys_to_update = ['footer_dr1', 'footer_dr2', 'footer_dr3']
            
        for key in keys_to_update:
            new_val = request.form.get(key)
            if new_val is not None:
                setting = Setting.query.filter_by(key=key).first()
                if setting:
                    setting.value = new_val
                    db.session.add(setting)
                else:
                    db.session.add(Setting(key=key, value=new_val))
        
        db.session.commit()
        flash('Settings updated successfully!', 'success')
        return redirect(url_for('settings'))
        
    all_settings = {s.key: s.value for s in Setting.query.all()}
    return render_template('settings.html', settings=all_settings)

@app.route('/api/dashboard-stats')
@login_required
def dashboard_stats():
    today_default = datetime.now().date()

    def _parse(val):
        try:
            return datetime.strptime(val, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            return today_default

    date_from = _parse(request.args.get('date_from') or request.args.get('date'))
    date_to   = _parse(request.args.get('date_to')   or request.args.get('date'))
    if date_to < date_from:
        date_to = date_from

    matching_patients = Patient.query.filter(
        db.func.date(Patient.registration_date) >= date_from,
        db.func.date(Patient.registration_date) <= date_to
    ).all()

    total_patients_target = len(matching_patients)
    total_tests_target = sum(1 for p in matching_patients for pt in p.tests if pt.status != 'Cancelled')

    revenue_target = 0.0
    cancelled_target = 0
    refunded_target = 0.0

    if current_user.role == 'Admin':
        revenue_target = sum(p.paid_amount for p in matching_patients)
        cancelled_target = sum(1 for p in matching_patients for pt in p.tests if pt.status == 'Cancelled')

        refunded_val = db.session.query(db.func.sum(RefundRecord.amount_refunded)).filter(
            db.func.date(RefundRecord.refund_date) >= date_from,
            db.func.date(RefundRecord.refund_date) <= date_to
        ).scalar()
        refunded_target = refunded_val if refunded_val else 0.0

    return jsonify({
        'patients': total_patients_target,
        'tests': total_tests_target,
        'revenue': revenue_target,
        'refunded_count': cancelled_target,
        'refunded_amount': refunded_target
    })

@app.route('/')
@login_required
def dashboard():
    today_default = datetime.now().date()

    def _parse(val):
        try:
            return datetime.strptime(val, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            return today_default

    date_from = _parse(request.args.get('date_from'))
    date_to   = _parse(request.args.get('date_to'))
    if date_to < date_from:
        date_to = date_from

    matching_patients = Patient.query.filter(
        db.func.date(Patient.registration_date) >= date_from,
        db.func.date(Patient.registration_date) <= date_to
    ).all()

    total_patients_today = len(matching_patients)
    total_tests_today = sum(1 for p in matching_patients for pt in p.tests if pt.status != 'Cancelled')

    revenue_today = 0.0
    total_cancelled_today = 0
    refunded_today = 0.0

    if current_user.role == 'Admin':
        revenue_today = sum(p.paid_amount for p in matching_patients)
        total_cancelled_today = sum(1 for p in matching_patients for pt in p.tests if pt.status == 'Cancelled')

        refunded_val = db.session.query(db.func.sum(RefundRecord.amount_refunded)).filter(
            db.func.date(RefundRecord.refund_date) >= date_from,
            db.func.date(RefundRecord.refund_date) <= date_to
        ).scalar()
        refunded_today = refunded_val if refunded_val else 0.0

    recent_patients = sorted(matching_patients, key=lambda p: p.registration_date, reverse=True)[:10]

    return render_template('dashboard.html',
                           total_patients_today=total_patients_today,
                           total_tests_today=total_tests_today,
                           revenue_today=revenue_today,
                           total_cancelled_today=total_cancelled_today,
                           refunded_today=refunded_today,
                           recent_patients=recent_patients,
                           date_from=str(date_from),
                           date_to=str(date_to))

@app.route('/tests')
@login_required
@role_required('Admin')
def test_list():
    tests = Test.query.all()
    return render_template('tests/index.html', tests=tests)

@app.route('/tests/new', methods=['GET', 'POST'])
@login_required
@role_required('Admin')
def test_new():
    if request.method == 'POST':
        name = request.form.get('name')
        price = request.form.get('price')
        if name and price:
            new_test = Test(name=name, price=float(price))
            db.session.add(new_test)
            db.session.commit()
            flash('Test added successfully!', 'success')
            return redirect(url_for('test_list'))
        else:
            flash('Name and price are required.', 'error')
    return render_template('tests/form.html', test=None)

@app.route('/tests/<int:test_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('Admin')
def test_edit(test_id):
    test = Test.query.get_or_404(test_id)
    if request.method == 'POST':
        test.name = request.form.get('name')
        test.price = float(request.form.get('price'))
        db.session.add(test)
        db.session.commit()
        flash('Test updated successfully!', 'success')
        return redirect(url_for('test_list'))
    return render_template('tests/form.html', test=test)

@app.route('/tests/<int:test_id>/delete', methods=['POST'])
@login_required
@role_required('Admin')
def test_delete(test_id):
    test = Test.query.get_or_404(test_id)
    db.session.delete(test)
    db.session.commit()
    flash('Test deleted successfully.', 'success')
    return redirect(url_for('test_list'))

@app.route('/tests/<int:test_id>/parameters', methods=['GET', 'POST'])
@login_required
@role_required('Admin')
def test_parameters(test_id):
    test = Test.query.get_or_404(test_id)
    if request.method == 'POST':
        name = request.form.get('name')
        unit = request.form.get('unit')
        normal_range = request.form.get('normal_range')
        notes = request.form.get('notes')
        if name:
            new_param = Parameter(test_id=test.id, name=name, unit=unit, normal_range=normal_range, notes=notes)
            db.session.add(new_param)
            db.session.commit()
            flash('Parameter added successfully!', 'success')
            return redirect(url_for('test_parameters', test_id=test.id))
        else:
            flash('Parameter name is required.', 'error')
    
    parameters = Parameter.query.filter_by(test_id=test.id).all()
    return render_template('tests/parameters.html', test=test, parameters=parameters)

@app.route('/parameters/<int:param_id>/delete', methods=['POST'])
@login_required
@role_required('Admin')
def parameter_delete(param_id):
    param = Parameter.query.get_or_404(param_id)
    test_id = param.test_id
    db.session.delete(param)
    db.session.commit()
    flash('Parameter deleted successfully!', 'success')
    return redirect(url_for('test_parameters', test_id=test_id))

@app.route('/doctors', methods=['GET', 'POST'])
@login_required
@role_required('Admin')
def doctor_list():
    if request.method == 'POST':
        name = request.form.get('name')
        share = request.form.get('share_percentage')
        if name and share:
            new_dr = Doctor(name=name, share_percentage=float(share))
            db.session.add(new_dr)
            db.session.commit()
            flash('Doctor added successfully!', 'success')
            return redirect(url_for('doctor_list'))
    doctors = Doctor.query.all()
    return render_template('doctors/index.html', doctors=doctors)

@app.route('/doctors/<int:dr_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('Admin')
def doctor_edit(dr_id):
    dr = Doctor.query.get_or_404(dr_id)
    if request.method == 'POST':
        dr.name = request.form.get('name')
        dr.share_percentage = float(request.form.get('share_percentage'))
        db.session.add(dr)
        db.session.commit()
        flash('Doctor updated successfully!', 'success')
        return redirect(url_for('doctor_list'))
    return render_template('doctors/form.html', doctor=dr)

@app.route('/doctors/<int:dr_id>/delete', methods=['POST'])
@login_required
@role_required('Admin')
def doctor_delete(dr_id):
    dr = Doctor.query.get_or_404(dr_id)
    db.session.delete(dr)
    db.session.commit()
    flash('Doctor deleted successfully.', 'success')
    return redirect(url_for('doctor_list'))

@app.route('/patients/new', methods=['GET', 'POST'])
@login_required
@role_required('Admin', 'Receptionist')
def patient_new():
    if request.method == 'POST':
        name = request.form.get('name')
        gender = request.form.get('gender')
        age = request.form.get('age')
        contact = request.form.get('contact')
        doctor_id = request.form.get('doctor_id')
        selected_test_ids = request.form.getlist('tests')
        
        discount_type = request.form.get('discount_type', 'none')
        discount_value = float(request.form.get('discount_value', 0) or 0)

        if not name or not age or not gender or not doctor_id:
            flash('Please fill all required fields.', 'error')
            return redirect(url_for('patient_new'))
        
        if not selected_test_ids:
            flash('Please select at least one test.', 'error')
            return redirect(url_for('patient_new'))

        # Calculate amounts
        gross_total = 0.0
        for test_id in selected_test_ids:
            test = Test.query.get(int(test_id))
            if test:
                gross_total += test.price
        
        discount_amount = 0.0
        if discount_type == 'fixed':
            discount_amount = discount_value
        elif discount_type == 'percentage':
            discount_amount = (gross_total * discount_value) / 100
        
        net_total = gross_total - discount_amount

        # Generate unique Lab Number
        today_pk = datetime.now()
        today_str = today_pk.strftime('%Y%m%d')
        count_today = Patient.query.filter(Patient.lab_number.like(f"LAB-{today_str}-%")).count()
        lab_number = f"LAB-{today_str}-{count_today + 1:03d}"

        try:
            new_patient = Patient(
                lab_number=lab_number,
                name=name,
                gender=gender,
                age=int(age),
                contact=contact,
                doctor_id=int(doctor_id) if (doctor_id and doctor_id != '0') else None,
                referring_doctor=request.form.get('referring_doctor', '') if doctor_id == '0' else '',
                total_amount=gross_total,
                discount_type=discount_type,
                discount_value=discount_value,
                paid_amount=net_total
            )
            db.session.add(new_patient)
            db.session.flush()

            for test_id in selected_test_ids:
                pt = PatientTest(patient_id=new_patient.id, test_id=int(test_id))
                db.session.add(pt)
            
            db.session.commit()
            receipt_url = url_for('patient_receipt', patient_id=new_patient.id, autoprint='true')
            flash(f'Patient registered successfully! <a href="{receipt_url}" class="alert-link" style="text-decoration: underline; font-weight: bold;">Click here to view/print receipt</a>', 'success')
            return redirect(receipt_url)
        except Exception as e:
            db.session.rollback()
            flash(f'An error occurred during registration: {str(e)}', 'error')
            return redirect(url_for('patient_new'))

    tests = Test.query.all()
    doctors = Doctor.query.all()
    return render_template('patients/new.html', tests=tests, doctors=doctors)

@app.route('/patients/<int:patient_id>/receipt')
@login_required
@role_required('Admin', 'Receptionist')
def patient_receipt(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    
    # Calculate discount amount for display
    discount_amount = 0.0
    if patient.discount_type == 'fixed':
        discount_amount = patient.discount_value
    elif patient.discount_type == 'percentage':
        discount_amount = (patient.total_amount * patient.discount_value) / 100
        
    return render_template('patients/receipt.html', 
                           patient=patient, 
                           total_amount=patient.total_amount,
                           discount_amount=discount_amount,
                           net_amount=patient.paid_amount)

@app.route('/patient_tests/<int:pt_id>/refund', methods=['POST'])
@login_required
@role_required('Admin', 'Receptionist')
def refund_patient_test(pt_id):
    pt = PatientTest.query.get_or_404(pt_id)
    if pt.status != 'Cancelled':
        pt.status = 'Cancelled'
        db.session.add(pt)
        reason = request.form.get('reason', 'Refunded by staff')
        refund = RefundRecord(patient_test_id=pt.id, amount_refunded=pt.test.price, reason=reason)
        db.session.add(refund)
        db.session.commit()
        flash(f'Test "{pt.test.name}" has been refunded. Amount: PKR {pt.test.price}', 'success')
    else:
        flash('This test has already been refunded.', 'info')
    return redirect(url_for('patient_receipt', patient_id=pt.patient_id))

@app.route('/results')
@login_required
def results_list():
    patients = Patient.query.order_by(Patient.registration_date.desc()).limit(100).all()
    return render_template('results/index.html', patients=patients)

@app.route('/patients/<int:patient_id>/results', methods=['GET', 'POST'])
@login_required
@role_required('Admin', 'Lab Technician', 'Technologist', 'Pathologist')
def patient_results(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    
    if request.method == 'POST':
        for pt in patient.tests:
            if pt.status == 'Cancelled':
                continue
            pt.status = 'Completed'
            db.session.add(pt)
            for param in pt.test.parameters:
                res_val = request.form.get(f'result_{param.id}')
                if res_val is not None:
                    existing_result = Result.query.filter_by(patient_test_id=pt.id, parameter_id=param.id).first()
                    if existing_result:
                        existing_result.result_value = res_val
                        db.session.add(existing_result)
                    else:
                        new_res = Result(patient_test_id=pt.id, parameter_id=param.id, result_value=res_val)
                        db.session.add(new_res)
        
        db.session.commit()
        flash('Results saved successfully!', 'success')
        return redirect(url_for('patient_report', patient_id=patient.id))

    existing_results = {}
    for pt in patient.tests:
        if pt.status == 'Cancelled':
            continue
        for res in pt.results:
            existing_results[res.parameter_id] = res.result_value

    return render_template('results/form.html', patient=patient, existing_results=existing_results)

@app.route('/patients/<int:patient_id>/report')
@login_required
def patient_report(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    
    report_data = []
    for pt in patient.tests:
        if pt.status == 'Cancelled':
            continue
        test_data = {
            'test_name': pt.test.name,
            'results': []
        }
        for param in pt.test.parameters:
            res = next((r for r in pt.results if r.parameter_id == param.id), None)
            val = res.result_value if res else ''
            
            is_abnormal = False
            try:
                if val and param.normal_range:
                    parts = param.normal_range.split('-')
                    if len(parts) == 2:
                        min_val = float(parts[0].strip())
                        max_val = float(parts[1].strip())
                        float_val = float(val)
                        if float_val < min_val or float_val > max_val:
                            is_abnormal = True
            except:
                pass
                
            test_data['results'].append({
                'parameter_name': param.name,
                'result_value': val,
                'unit': param.unit,
                'normal_range': param.normal_range,
                'is_abnormal': is_abnormal
            })
        report_data.append(test_data)
        
    return render_template('results/report.html', patient=patient, report_data=report_data)

@app.route('/reports/cash-summary')
@login_required
@role_required('Admin')
def cash_summary():
    today = date.today()
    period = request.args.get('period', 'today')

    custom_from = request.args.get('date_from', '')
    custom_to   = request.args.get('date_to', '')

    if custom_from and custom_to:
        try:
            start_date = datetime.strptime(custom_from, '%Y-%m-%d').date()
            end_date   = datetime.strptime(custom_to,   '%Y-%m-%d').date()
            period = 'custom'
        except ValueError:
            start_date = end_date = today
    else:
        if period == 'weekly':
            start_date = today - timedelta(days=today.weekday())
            end_date   = today
        elif period == 'monthly':
            start_date = today.replace(day=1)
            end_date   = today
        elif period == 'yesterday':
            start_date = end_date = today - timedelta(days=1)
        else:  # today
            start_date = end_date = today

    filter_dr_id = request.args.get('doctor_id', '', type=str)

    all_in_period = Patient.query.filter(
        db.func.date(Patient.registration_date) >= start_date,
        db.func.date(Patient.registration_date) <= end_date
    ).all()

    if filter_dr_id and filter_dr_id != '':
        try:
            target_id = int(filter_dr_id)
            patients = [p for p in all_in_period if p.doctor_id == target_id]
        except ValueError:
            patients = all_in_period
    else:
        patients = all_in_period

    patients = sorted(patients, key=lambda p: p.registration_date)

    summary_data = {
        'total_revenue': 0.0,
        'total_dr_share': 0.0,
        'net_cash': 0.0,
        'total_patients': len(patients),
        'doctor_breakdown': {}
    }

    for p in patients:
        summary_data['total_revenue'] += p.paid_amount
        if p.doctor_id and p.doctor:
            dr = p.doctor
            share = (p.paid_amount * dr.share_percentage) / 100
            summary_data['total_dr_share'] += share
            if dr.id not in summary_data['doctor_breakdown']:
                summary_data['doctor_breakdown'][dr.id] = {
                    'name': dr.name,
                    'share_pct': dr.share_percentage,
                    'patients_count': 0,
                    'revenue': 0.0,
                    'share': 0.0,
                    'patients': []
                }
            bd = summary_data['doctor_breakdown'][dr.id]
            bd['patients_count'] += 1
            bd['revenue']        += p.paid_amount
            bd['share']          += share
            bd['patients'].append({
                'lab_number': p.lab_number,
                'name':       p.name,
                'age':        p.age,
                'gender':     p.gender,
                'date':       p.registration_date.strftime('%d %b %Y'),
                'amount':     p.paid_amount,
                'share':      share
            })
        else:
            key = 0
            if key not in summary_data['doctor_breakdown']:
                summary_data['doctor_breakdown'][key] = {
                    'name': 'Self / No Doctor',
                    'share_pct': 0,
                    'patients_count': 0,
                    'revenue': 0.0,
                    'share': 0.0,
                    'patients': []
                }
            bd = summary_data['doctor_breakdown'][key]
            bd['patients_count'] += 1
            bd['revenue']        += p.paid_amount
            bd['share']          += 0.0
            bd['patients'].append({
                'lab_number': p.lab_number,
                'name':       p.name,
                'age':        p.age,
                'gender':     p.gender,
                'date':       p.registration_date.strftime('%d %b %Y'),
                'amount':     p.paid_amount,
                'share':      0.0
            })

    summary_data['net_cash'] = summary_data['total_revenue'] - summary_data['total_dr_share']
    all_doctors = sorted(Doctor.query.all(), key=lambda d: d.name)

    return render_template('reports/cash_summary.html',
                           summary=summary_data,
                           period=period,
                           start_date=start_date,
                           end_date=end_date,
                           all_doctors=all_doctors,
                           filter_dr_id=filter_dr_id,
                           custom_from=str(start_date),
                           custom_to=str(end_date))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, host='0.0.0.0', port=port)
