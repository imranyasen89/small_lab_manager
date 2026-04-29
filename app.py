from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from database import db
from models import Patient, Test, Parameter, PatientTest, Result, User, Setting, RefundRecord
from datetime import datetime, date
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'dev-secret-key' # Replace in production
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///lms.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# RBAC Decorator
def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('login', next=request.url))
            if current_user.role not in roles and current_user.role != 'Admin':
                flash('You do not have permission to access this page.', 'error')
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# Global Context
@app.context_processor
def inject_globals():
    settings = {}
    try:
        all_settings = Setting.query.all()
        settings = {s.key: s.value for s in all_settings}
    except Exception:
        pass # In case table doesn't exist yet during init
        
    lab_name = settings.get('lab_name', "Ideal Diagnostic Center")
    lab_address = settings.get('lab_address', "")
    lab_contact = settings.get('lab_contact', "")
    
    return dict(
        lab_name=lab_name, 
        lab_address=lab_address, 
        lab_contact=lab_contact, 
        current_user=current_user,
        now=datetime.now()
    )

with app.app_context():
    db.create_all()
    
    # Create default Settings if no settings exist
    if Setting.query.count() == 0:
        db.session.add(Setting(key='lab_name', value='Ideal Diagnostic Center'))
        db.session.add(Setting(key='lab_address', value='123 Health Avenue, Medical District'))
        db.session.add(Setting(key='lab_contact', value='+91 98765 43210'))
        db.session.commit()
        
    # Create default Admin if no users exist
    if User.query.count() == 0:
        admin_user = User(
            username='admin',
            password_hash=generate_password_hash('admin123'),
            role='Admin',
            name='System Administrator'
        )
        db.session.add(admin_user)
        db.session.commit()

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
        # Update or create settings dynamically
        keys_to_update = ['lab_name', 'lab_address', 'lab_contact']
        for key in keys_to_update:
            new_val = request.form.get(key)
            if new_val is not None:
                setting = Setting.query.filter_by(key=key).first()
                if setting:
                    setting.value = new_val
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
    date_str = request.args.get('date')
    if date_str:
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            target_date = datetime.now().date()
    else:
        target_date = datetime.now().date()

    total_patients_target = Patient.query.filter(db.func.date(Patient.registration_date) == target_date).count()
    total_tests_target = PatientTest.query.join(Patient).filter(
        db.func.date(Patient.registration_date) == target_date,
        PatientTest.status != 'Cancelled'
    ).count()
    
    revenue_target = 0.0
    cancelled_target = 0
    refunded_target = 0.0
    
    if current_user.role == 'Admin':
        revenue_val = db.session.query(db.func.sum(Test.price)).join(PatientTest).join(Patient).filter(
            db.func.date(Patient.registration_date) == target_date,
            PatientTest.status != 'Cancelled'
        ).scalar()
        revenue_target = revenue_val if revenue_val else 0.0
        
        cancelled_target = PatientTest.query.join(Patient).filter(
            db.func.date(Patient.registration_date) == target_date,
            PatientTest.status == 'Cancelled'
        ).count()
        
        refunded_val = db.session.query(db.func.sum(RefundRecord.amount_refunded)).filter(
            db.func.date(RefundRecord.refund_date) == target_date
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
    date_str = request.args.get('date')
    if date_str:
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            target_date = datetime.now().date()
    else:
        target_date = datetime.now().date()

    total_patients_today = Patient.query.filter(db.func.date(Patient.registration_date) == target_date).count()
    
    total_tests_today = PatientTest.query.join(Patient).filter(
        db.func.date(Patient.registration_date) == target_date,
        PatientTest.status != 'Cancelled'
    ).count()
    
    revenue_today = 0.0
    total_cancelled_today = 0
    refunded_today = 0.0
    
    if current_user.role == 'Admin':
        revenue_val = db.session.query(db.func.sum(Test.price)).join(PatientTest).join(Patient).filter(
            db.func.date(Patient.registration_date) == target_date,
            PatientTest.status != 'Cancelled'
        ).scalar()
        revenue_today = revenue_val if revenue_val else 0.0
        
        total_cancelled_today = PatientTest.query.join(Patient).filter(
            db.func.date(Patient.registration_date) == target_date,
            PatientTest.status == 'Cancelled'
        ).count()
        
        refunded_val = db.session.query(db.func.sum(RefundRecord.amount_refunded)).filter(
            db.func.date(RefundRecord.refund_date) == target_date
        ).scalar()
        refunded_today = refunded_val if refunded_val else 0.0

    recent_patients = Patient.query.filter(
        db.func.date(Patient.registration_date) == target_date
    ).order_by(Patient.registration_date.desc()).limit(5).all()

    return render_template('dashboard.html', 
                           total_patients_today=total_patients_today,
                           total_tests_today=total_tests_today,
                           revenue_today=revenue_today,
                           total_cancelled_today=total_cancelled_today,
                           refunded_today=refunded_today,
                           recent_patients=recent_patients,
                           selected_date=target_date)

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
    flash('Test deleted successfully!', 'success')
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
@app.route('/patients/new', methods=['GET', 'POST'])
@login_required
@role_required('Admin', 'Receptionist')
def patient_new():
    if request.method == 'POST':
        name = request.form.get('name')
        gender = request.form.get('gender')
        age = request.form.get('age')
        contact = request.form.get('contact')
        referring_doctor = request.form.get('referring_doctor')
        selected_test_ids = request.form.getlist('tests')

        if not name or not age or not gender or not referring_doctor:
            flash('Please fill all required fields.', 'error')
            return redirect(url_for('patient_new'))
        
        if not selected_test_ids:
            flash('Please select at least one test.', 'error')
            return redirect(url_for('patient_new'))

        # Generate unique Lab Number
        today_pk = datetime.now()
        today_str = today_pk.strftime('%Y%m%d')
        # Simple sequence logic: count patients today + 1
        count_today = Patient.query.filter(Patient.lab_number.like(f"LAB-{today_str}-%")).count()
        lab_number = f"LAB-{today_str}-{count_today + 1:03d}"

        new_patient = Patient(
            lab_number=lab_number,
            name=name,
            gender=gender,
            age=int(age),
            contact=contact,
            referring_doctor=referring_doctor
        )
        db.session.add(new_patient)
        db.session.flush() # To get the patient ID

        for test_id in selected_test_ids:
            pt = PatientTest(patient_id=new_patient.id, test_id=int(test_id))
            db.session.add(pt)
        
        db.session.commit()
        flash('Patient registered successfully!', 'success')
        return redirect(url_for('patient_receipt', patient_id=new_patient.id))

    tests = Test.query.all()
    return render_template('patients/new.html', tests=tests)

@app.route('/patients/<int:patient_id>/receipt')
@login_required
@role_required('Admin', 'Receptionist')
def patient_receipt(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    # Calculate total
    total_amount = sum([pt.test.price for pt in patient.tests if pt.status != 'Cancelled'])
    return render_template('patients/receipt.html', patient=patient, total_amount=total_amount)

@app.route('/patient_tests/<int:pt_id>/refund', methods=['POST'])
@login_required
@role_required('Admin', 'Receptionist')
def refund_patient_test(pt_id):
    pt = PatientTest.query.get_or_404(pt_id)
    if pt.status != 'Cancelled':
        old_status = pt.status
        pt.status = 'Cancelled'
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
    # Show patients with tests pending results
    patients = Patient.query.order_by(Patient.registration_date.desc()).all()
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
            # Mark status as completed if results are entered
            pt.status = 'Completed'
            for param in pt.test.parameters:
                # Get the result value from the form
                # Name of input is 'result_param_id'
                res_val = request.form.get(f'result_{param.id}')
                if res_val is not None:
                    # Check if result already exists
                    existing_result = Result.query.filter_by(patient_test_id=pt.id, parameter_id=param.id).first()
                    if existing_result:
                        existing_result.result_value = res_val
                    else:
                        new_res = Result(patient_test_id=pt.id, parameter_id=param.id, result_value=res_val)
                        db.session.add(new_res)
        
        db.session.commit()
        flash('Results saved successfully!', 'success')
        return redirect(url_for('patient_report', patient_id=patient.id))

    # For GET request, fetch existing results to populate form if any
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
    
    # Organize data for report: test -> list of results
    report_data = []
    for pt in patient.tests:
        if pt.status == 'Cancelled':
            continue
        test_data = {
            'test_name': pt.test.name,
            'results': []
        }
        # To display in order of parameters
        for param in pt.test.parameters:
            # Find the result
            res = next((r for r in pt.results if r.parameter_id == param.id), None)
            val = res.result_value if res else ''
            
            # Simple abnormality check logic (optional, basic string check)
            is_abnormal = False
            try:
                if val and param.normal_range:
                    # Extract numbers from normal_range "x - y"
                    parts = param.normal_range.split('-')
                    if len(parts) == 2:
                        min_val = float(parts[0].strip())
                        max_val = float(parts[1].strip())
                        float_val = float(val)
                        if float_val < min_val or float_val > max_val:
                            is_abnormal = True
            except:
                pass # If parsing fails, ignore abnormality highlighting
                
            test_data['results'].append({
                'parameter_name': param.name,
                'result_value': val,
                'unit': param.unit,
                'normal_range': param.normal_range,
                'is_abnormal': is_abnormal
            })
        report_data.append(test_data)
        
    return render_template('results/report.html', patient=patient, report_data=report_data)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
