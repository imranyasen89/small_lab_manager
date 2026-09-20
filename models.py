import os
import logging
from datetime import datetime, date
from werkzeug.exceptions import abort
from database import get_firestore_client, get_next_id

logger = logging.getLogger('pro_lab_models')

# In-memory store fallback (used if Firestore credentials are being configured)
_IN_MEMORY_STORE = {
    'users': {},
    'settings': {},
    'doctors': {},
    'patients': {},
    'tests': {},
    'parameters': {},
    'patient_tests': {},
    'results': {},
    'refund_records': {},
}


def _parse_datetime(val):
    if val is None:
        return datetime.now()
    if isinstance(val, datetime):
        return val
    if isinstance(val, date):
        return datetime.combine(val, datetime.min.time())
    if isinstance(val, str):
        for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S'):
            try:
                return datetime.strptime(val, fmt)
            except ValueError:
                pass
    return datetime.now()


class Query:
    """Universal Query builder for Firestore and in-memory fallback."""
    def __init__(self, model_class, collection_name):
        self.model_class = model_class
        self.collection_name = collection_name
        self._filters = []
        self._order_by = None
        self._order_desc = False
        self._limit_num = None

    def filter_by(self, **kwargs):
        for k, v in kwargs.items():
            self._filters.append(('eq', k, v))
        return self

    def filter(self, *criteria):
        for crit in criteria:
            if isinstance(crit, tuple) and len(crit) == 3:
                # Custom tuple from db.func.date or similar
                self._filters.append(crit)
            elif hasattr(crit, 'operator_type'):
                self._filters.append(crit)
            elif callable(crit):
                self._filters.append(('callable', crit, None))
            elif isinstance(crit, bool):
                if not crit:
                    self._filters.append(('bool', False, None))
        return self

    def order_by(self, field):
        if hasattr(field, 'desc_order') and field.desc_order:
            self._order_by = field.field_name
            self._order_desc = True
        elif hasattr(field, 'field_name'):
            self._order_by = field.field_name
            self._order_desc = False
        elif isinstance(field, str):
            self._order_by = field
            self._order_desc = False
        return self

    def limit(self, num):
        self._limit_num = num
        return self

    def _fetch_all_from_source(self):
        fs = get_firestore_client()
        items = []
        if fs is not None:
            try:
                docs = fs.collection(self.collection_name).stream()
                for doc in docs:
                    data = doc.to_dict()
                    obj = self.model_class.from_dict(data, doc.id)
                    items.append(obj)
                return items
            except Exception as e:
                logger.warning(f"Firestore read error ({self.collection_name}): {e}")

        # Fallback to in-memory store
        raw_dict = _IN_MEMORY_STORE.get(self.collection_name, {})
        for doc_id, data in raw_dict.items():
            obj = self.model_class.from_dict(data, doc_id)
            items.append(obj)
        return items

    def _apply_filters(self, items):
        filtered = []
        for item in items:
            match = True
            for f in self._filters:
                op = f[0]
                if op == 'eq':
                    field_name, expected = f[1], f[2]
                    actual = getattr(item, field_name, None)
                    if actual != expected:
                        match = False
                        break
                elif op == 'ne':
                    field_name, expected = f[1], f[2]
                    actual = getattr(item, field_name, None)
                    if actual == expected:
                        match = False
                        break
                elif op in ('ge', 'le'):
                    field_wrapper, target_date = f[1], f[2]
                    field_name = field_wrapper.field_name if hasattr(field_wrapper, 'field_name') else str(field_wrapper)
                    val = getattr(item, field_name, None)
                    if val is None:
                        match = False
                        break
                    val_date = val.date() if isinstance(val, datetime) else val
                    if isinstance(target_date, datetime):
                        target_date = target_date.date()
                    if op == 'ge' and not (val_date >= target_date):
                        match = False
                        break
                    elif op == 'le' and not (val_date <= target_date):
                        match = False
                        break
                elif op == 'like':
                    field_name, pattern = f[1], f[2]
                    val = str(getattr(item, field_name, ''))
                    # Simple prefix matching for "LAB-20260920-%"
                    clean_pattern = pattern.rstrip('%')
                    if not val.startswith(clean_pattern):
                        match = False
                        break
                elif op == 'bool' and f[1] is False:
                    match = False
                    break
            if match:
                filtered.append(item)

        # Apply ordering
        if self._order_by:
            def sort_key(x):
                v = getattr(x, self._order_by, None)
                if v is None:
                    return ""
                return v
            filtered.sort(key=sort_key, reverse=self._order_desc)

        # Apply limit
        if self._limit_num is not None:
            filtered = filtered[:self._limit_num]

        return filtered

    def all(self):
        items = self._fetch_all_from_source()
        return self._apply_filters(items)

    def first(self):
        res = self.all()
        return res[0] if res else None

    def count(self):
        return len(self.all())

    def get(self, ident):
        if ident is None:
            return None
        s_id = str(ident)
        fs = get_firestore_client()
        if fs is not None:
            try:
                doc = fs.collection(self.collection_name).document(s_id).get()
                if doc.exists:
                    return self.model_class.from_dict(doc.to_dict(), doc.id)
            except Exception as e:
                logger.warning(f"Firestore get error: {e}")

        # Check in-memory
        data = _IN_MEMORY_STORE.get(self.collection_name, {}).get(s_id)
        if data:
            return self.model_class.from_dict(data, s_id)

        # Try matching by numeric id attribute in in-memory
        try:
            int_id = int(ident)
            for d_id, data in _IN_MEMORY_STORE.get(self.collection_name, {}).items():
                if data.get('id') == int_id or d_id == s_id:
                    return self.model_class.from_dict(data, d_id)
        except (ValueError, TypeError):
            pass

        return None

    def get_or_404(self, ident):
        obj = self.get(ident)
        if obj is None:
            abort(404)
        return obj

    def join(self, other_model):
        # In NoSQL we resolve relationships via properties, so join returns self
        return self


class ColumnField:
    """Helper to support expressions like Patient.registration_date.desc(), etc."""
    def __init__(self, field_name):
        self.field_name = field_name
        self.desc_order = False

    def desc(self):
        cf = ColumnField(self.field_name)
        cf.desc_order = True
        return cf

    def asc(self):
        cf = ColumnField(self.field_name)
        cf.desc_order = False
        return cf

    def like(self, pattern):
        return ('like', self.field_name, pattern)

    def __eq__(self, other):
        return ('eq', self.field_name, other)

    def __ne__(self, other):
        return ('ne', self.field_name, other)


class AggregateQuery:
    """Supports db.session.query(db.func.sum(field)).filter(...).scalar()."""
    def __init__(self, *args):
        self.args = args
        self._filters = []

    def filter(self, *criteria):
        for crit in criteria:
            if isinstance(crit, tuple):
                self._filters.append(crit)
        return self

    def scalar(self):
        if not self.args:
            return 0.0
        func_sum = self.args[0]
        if not hasattr(func_sum, 'field'):
            return 0.0
        field = func_sum.field
        field_name = field.field_name if hasattr(field, 'field_name') else str(field)

        # Determine target collection based on field
        collection = 'patients'
        if 'refund' in field_name or 'amount_refunded' in field_name:
            collection = 'refund_records'

        model = Patient if collection == 'patients' else RefundRecord
        q = Query(model, collection)
        for f in self._filters:
            q.filter(f)

        total = 0.0
        for item in q.all():
            val = getattr(item, field_name, 0.0)
            if val:
                total += float(val)
        return total


class _MetaModel(type):
    @property
    def query(cls):
        return Query(cls, cls.collection_name)


class BaseModel(metaclass=_MetaModel):
    collection_name = ""

    def _do_save(self):
        if getattr(self, 'id', None) is None:
            self.id = get_next_id(self.collection_name)
        s_id = str(self.id)
        data = self.to_dict()

        # Update in-memory
        _IN_MEMORY_STORE.setdefault(self.collection_name, {})[s_id] = data

        # Update Firestore
        fs = get_firestore_client()
        if fs is not None:
            try:
                fs.collection(self.collection_name).document(s_id).set(data, merge=True)
            except Exception as e:
                logger.error(f"Error saving to Firestore {self.collection_name}/{s_id}: {e}")

    def _do_delete(self):
        s_id = str(getattr(self, 'id', ''))
        _IN_MEMORY_STORE.get(self.collection_name, {}).pop(s_id, None)
        fs = get_firestore_client()
        if fs is not None and s_id:
            try:
                fs.collection(self.collection_name).document(s_id).delete()
            except Exception as e:
                logger.error(f"Error deleting from Firestore {self.collection_name}/{s_id}: {e}")


class User(BaseModel):
    collection_name = 'users'
    id = ColumnField('id')
    username = ColumnField('username')
    role = ColumnField('role')
    name = ColumnField('name')

    def __init__(self, id=None, username="", password_hash="", role="Admin", name=""):
        self.id = id
        self.username = username
        self.password_hash = password_hash
        self.role = role
        self.name = name

    @property
    def is_authenticated(self):
        return True

    @property
    def is_active(self):
        return True

    @property
    def is_anonymous(self):
        return False

    def get_id(self):
        return str(self.id)

    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'password_hash': self.password_hash,
            'role': self.role,
            'name': self.name,
        }

    @classmethod
    def from_dict(cls, data, doc_id=None):
        raw_id = data.get('id') or doc_id
        try:
            uid = int(raw_id)
        except (ValueError, TypeError):
            uid = raw_id
        return cls(
            id=uid,
            username=data.get('username', ''),
            password_hash=data.get('password_hash', ''),
            role=data.get('role', 'Admin'),
            name=data.get('name', ''),
        )


class Setting(BaseModel):
    collection_name = 'settings'
    id = ColumnField('id')
    key = ColumnField('key')
    value = ColumnField('value')

    def __init__(self, id=None, key="", value=""):
        self.id = id
        self.key = key
        self.value = value

    def to_dict(self):
        return {
            'id': self.id,
            'key': self.key,
            'value': self.value,
        }

    @classmethod
    def from_dict(cls, data, doc_id=None):
        raw_id = data.get('id') or doc_id
        try:
            sid = int(raw_id)
        except (ValueError, TypeError):
            sid = raw_id
        return cls(
            id=sid,
            key=data.get('key', ''),
            value=data.get('value', ''),
        )


class Doctor(BaseModel):
    collection_name = 'doctors'
    id = ColumnField('id')
    name = ColumnField('name')
    share_percentage = ColumnField('share_percentage')

    def __init__(self, id=None, name="", share_percentage=0.0):
        self.id = id
        self.name = name
        self.share_percentage = float(share_percentage or 0.0)

    @property
    def patients(self):
        return Patient.query.filter_by(doctor_id=self.id).all()

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'share_percentage': self.share_percentage,
        }

    @classmethod
    def from_dict(cls, data, doc_id=None):
        raw_id = data.get('id') or doc_id
        try:
            did = int(raw_id)
        except (ValueError, TypeError):
            did = raw_id
        return cls(
            id=did,
            name=data.get('name', ''),
            share_percentage=float(data.get('share_percentage', 0.0) or 0.0),
        )


class Test(BaseModel):
    collection_name = 'tests'
    id = ColumnField('id')
    name = ColumnField('name')
    price = ColumnField('price')

    def __init__(self, id=None, name="", price=0.0):
        self.id = id
        self.name = name
        self.price = float(price or 0.0)

    @property
    def parameters(self):
        return Parameter.query.filter_by(test_id=self.id).all()

    @property
    def patient_tests(self):
        return PatientTest.query.filter_by(test_id=self.id).all()

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'price': self.price,
        }

    @classmethod
    def from_dict(cls, data, doc_id=None):
        raw_id = data.get('id') or doc_id
        try:
            tid = int(raw_id)
        except (ValueError, TypeError):
            tid = raw_id
        return cls(
            id=tid,
            name=data.get('name', ''),
            price=float(data.get('price', 0.0) or 0.0),
        )


class Parameter(BaseModel):
    collection_name = 'parameters'
    id = ColumnField('id')
    test_id = ColumnField('test_id')
    name = ColumnField('name')
    unit = ColumnField('unit')
    normal_range = ColumnField('normal_range')
    notes = ColumnField('notes')

    def __init__(self, id=None, test_id=None, name="", unit="", normal_range="", notes=""):
        self.id = id
        self.test_id = int(test_id) if test_id is not None and str(test_id).isdigit() else test_id
        self.name = name
        self.unit = unit or ""
        self.normal_range = normal_range or ""
        self.notes = notes or ""

    @property
    def test(self):
        return Test.query.get(self.test_id)

    @property
    def results(self):
        return Result.query.filter_by(parameter_id=self.id).all()

    def to_dict(self):
        return {
            'id': self.id,
            'test_id': self.test_id,
            'name': self.name,
            'unit': self.unit,
            'normal_range': self.normal_range,
            'notes': self.notes,
        }

    @classmethod
    def from_dict(cls, data, doc_id=None):
        raw_id = data.get('id') or doc_id
        try:
            pid = int(raw_id)
        except (ValueError, TypeError):
            pid = raw_id
        return cls(
            id=pid,
            test_id=data.get('test_id'),
            name=data.get('name', ''),
            unit=data.get('unit', ''),
            normal_range=data.get('normal_range', ''),
            notes=data.get('notes', ''),
        )


class Patient(BaseModel):
    collection_name = 'patients'
    id = ColumnField('id')
    lab_number = ColumnField('lab_number')
    name = ColumnField('name')
    gender = ColumnField('gender')
    age = ColumnField('age')
    contact = ColumnField('contact')
    referring_doctor = ColumnField('referring_doctor')
    doctor_id = ColumnField('doctor_id')
    registration_date = ColumnField('registration_date')
    total_amount = ColumnField('total_amount')
    discount_type = ColumnField('discount_type')
    discount_value = ColumnField('discount_value')
    paid_amount = ColumnField('paid_amount')

    def __init__(self, id=None, lab_number="", name="", gender="", age=0, contact="",
                 referring_doctor="", doctor_id=None, registration_date=None,
                 total_amount=0.0, discount_type="none", discount_value=0.0, paid_amount=0.0):
        self.id = id
        self.lab_number = lab_number
        self.name = name
        self.gender = gender
        self.age = int(age or 0)
        self.contact = contact or ""
        self.referring_doctor = referring_doctor or ""
        self.doctor_id = int(doctor_id) if doctor_id is not None and str(doctor_id).isdigit() else doctor_id
        self.registration_date = _parse_datetime(registration_date)
        self.total_amount = float(total_amount or 0.0)
        self.discount_type = discount_type or "none"
        self.discount_value = float(discount_value or 0.0)
        self.paid_amount = float(paid_amount or 0.0)

    @property
    def doctor(self):
        if self.doctor_id:
            return Doctor.query.get(self.doctor_id)
        return None

    @property
    def tests(self):
        return PatientTest.query.filter_by(patient_id=self.id).all()

    def to_dict(self):
        reg_iso = self.registration_date.isoformat() if isinstance(self.registration_date, (datetime, date)) else str(self.registration_date)
        return {
            'id': self.id,
            'lab_number': self.lab_number,
            'name': self.name,
            'gender': self.gender,
            'age': self.age,
            'contact': self.contact,
            'referring_doctor': self.referring_doctor,
            'doctor_id': self.doctor_id,
            'registration_date': reg_iso,
            'total_amount': self.total_amount,
            'discount_type': self.discount_type,
            'discount_value': self.discount_value,
            'paid_amount': self.paid_amount,
        }

    @classmethod
    def from_dict(cls, data, doc_id=None):
        raw_id = data.get('id') or doc_id
        try:
            pid = int(raw_id)
        except (ValueError, TypeError):
            pid = raw_id
        return cls(
            id=pid,
            lab_number=data.get('lab_number', ''),
            name=data.get('name', ''),
            gender=data.get('gender', ''),
            age=int(data.get('age', 0) or 0),
            contact=data.get('contact', ''),
            referring_doctor=data.get('referring_doctor', ''),
            doctor_id=data.get('doctor_id'),
            registration_date=data.get('registration_date'),
            total_amount=float(data.get('total_amount', 0.0) or 0.0),
            discount_type=data.get('discount_type', 'none'),
            discount_value=float(data.get('discount_value', 0.0) or 0.0),
            paid_amount=float(data.get('paid_amount', 0.0) or 0.0),
        )


class PatientTest(BaseModel):
    collection_name = 'patient_tests'
    id = ColumnField('id')
    patient_id = ColumnField('patient_id')
    test_id = ColumnField('test_id')
    status = ColumnField('status')

    def __init__(self, id=None, patient_id=None, test_id=None, status="Pending"):
        self.id = id
        self.patient_id = int(patient_id) if patient_id is not None and str(patient_id).isdigit() else patient_id
        self.test_id = int(test_id) if test_id is not None and str(test_id).isdigit() else test_id
        self.status = status or "Pending"

    @property
    def test(self):
        return Test.query.get(self.test_id)

    @property
    def patient(self):
        return Patient.query.get(self.patient_id)

    @property
    def results(self):
        return Result.query.filter_by(patient_test_id=self.id).all()

    @property
    def refund(self):
        return RefundRecord.query.filter_by(patient_test_id=self.id).first()

    def to_dict(self):
        return {
            'id': self.id,
            'patient_id': self.patient_id,
            'test_id': self.test_id,
            'status': self.status,
        }

    @classmethod
    def from_dict(cls, data, doc_id=None):
        raw_id = data.get('id') or doc_id
        try:
            ptid = int(raw_id)
        except (ValueError, TypeError):
            ptid = raw_id
        return cls(
            id=ptid,
            patient_id=data.get('patient_id'),
            test_id=data.get('test_id'),
            status=data.get('status', 'Pending'),
        )


class Result(BaseModel):
    collection_name = 'results'
    id = ColumnField('id')
    patient_test_id = ColumnField('patient_test_id')
    parameter_id = ColumnField('parameter_id')
    result_value = ColumnField('result_value')

    def __init__(self, id=None, patient_test_id=None, parameter_id=None, result_value=""):
        self.id = id
        self.patient_test_id = int(patient_test_id) if patient_test_id is not None and str(patient_test_id).isdigit() else patient_test_id
        self.parameter_id = int(parameter_id) if parameter_id is not None and str(parameter_id).isdigit() else parameter_id
        self.result_value = str(result_value) if result_value is not None else ""

    @property
    def parameter(self):
        return Parameter.query.get(self.parameter_id)

    def to_dict(self):
        return {
            'id': self.id,
            'patient_test_id': self.patient_test_id,
            'parameter_id': self.parameter_id,
            'result_value': self.result_value,
        }

    @classmethod
    def from_dict(cls, data, doc_id=None):
        raw_id = data.get('id') or doc_id
        try:
            rid = int(raw_id)
        except (ValueError, TypeError):
            rid = raw_id
        return cls(
            id=rid,
            patient_test_id=data.get('patient_test_id'),
            parameter_id=data.get('parameter_id'),
            result_value=data.get('result_value', ''),
        )


class RefundRecord(BaseModel):
    collection_name = 'refund_records'
    id = ColumnField('id')
    patient_test_id = ColumnField('patient_test_id')
    amount_refunded = ColumnField('amount_refunded')
    reason = ColumnField('reason')
    refund_date = ColumnField('refund_date')

    def __init__(self, id=None, patient_test_id=None, amount_refunded=0.0, reason="", refund_date=None):
        self.id = id
        self.patient_test_id = int(patient_test_id) if patient_test_id is not None and str(patient_test_id).isdigit() else patient_test_id
        self.amount_refunded = float(amount_refunded or 0.0)
        self.reason = reason or ""
        self.refund_date = _parse_datetime(refund_date)

    @property
    def patient_test(self):
        return PatientTest.query.get(self.patient_test_id)

    def to_dict(self):
        ref_iso = self.refund_date.isoformat() if isinstance(self.refund_date, (datetime, date)) else str(self.refund_date)
        return {
            'id': self.id,
            'patient_test_id': self.patient_test_id,
            'amount_refunded': self.amount_refunded,
            'reason': self.reason,
            'refund_date': ref_iso,
        }

    @classmethod
    def from_dict(cls, data, doc_id=None):
        raw_id = data.get('id') or doc_id
        try:
            rfid = int(raw_id)
        except (ValueError, TypeError):
            rfid = raw_id
        return cls(
            id=rfid,
            patient_test_id=data.get('patient_test_id'),
            amount_refunded=float(data.get('amount_refunded', 0.0) or 0.0),
            reason=data.get('reason', ''),
            refund_date=data.get('refund_date'),
        )



