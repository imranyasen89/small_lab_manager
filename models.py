from database import db
from datetime import datetime
from flask_login import UserMixin

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), nullable=False) # Admin, Receptionist, Lab Technician, Technologist, Pathologist
    name = db.Column(db.String(100), nullable=False)

class Setting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False)
    value = db.Column(db.String(255), nullable=False)

class Doctor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    share_percentage = db.Column(db.Float, default=0.0)

    patients = db.relationship('Patient', backref='doctor', lazy=True)

class Patient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lab_number = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    gender = db.Column(db.String(10), nullable=False)
    age = db.Column(db.Integer, nullable=False)
    contact = db.Column(db.String(20), nullable=True)
    referring_doctor = db.Column(db.String(100), nullable=True) # Fallback/Legacy
    doctor_id = db.Column(db.Integer, db.ForeignKey('doctor.id'), nullable=True)
    registration_date = db.Column(db.DateTime, default=datetime.now)

    # Billing fields
    total_amount = db.Column(db.Float, default=0.0)
    discount_type = db.Column(db.String(20), default='none') # none, fixed, percentage
    discount_value = db.Column(db.Float, default=0.0)
    paid_amount = db.Column(db.Float, default=0.0)

    tests = db.relationship('PatientTest', backref='patient', lazy=True)

class Test(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Float, nullable=False)

    parameters = db.relationship('Parameter', backref='test', lazy=True, cascade="all, delete-orphan")
    patient_tests = db.relationship('PatientTest', backref='test', lazy=True)

class Parameter(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    test_id = db.Column(db.Integer, db.ForeignKey('test.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    unit = db.Column(db.String(50), nullable=True)
    normal_range = db.Column(db.String(100), nullable=True)
    notes = db.Column(db.Text, nullable=True)

    results = db.relationship('Result', backref='parameter', lazy=True)

class PatientTest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)
    test_id = db.Column(db.Integer, db.ForeignKey('test.id'), nullable=False)
    status = db.Column(db.String(20), default='Pending') # Pending, Completed, Cancelled

    results = db.relationship('Result', backref='patient_test', lazy=True, cascade="all, delete-orphan")

class Result(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_test_id = db.Column(db.Integer, db.ForeignKey('patient_test.id'), nullable=False)
    parameter_id = db.Column(db.Integer, db.ForeignKey('parameter.id'), nullable=False)
    result_value = db.Column(db.String(100), nullable=True)

class RefundRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_test_id = db.Column(db.Integer, db.ForeignKey('patient_test.id'), nullable=False)
    amount_refunded = db.Column(db.Float, nullable=False)
    reason = db.Column(db.String(255), nullable=True)
    refund_date = db.Column(db.DateTime, default=datetime.now)

    patient_test = db.relationship('PatientTest', backref='refund')
