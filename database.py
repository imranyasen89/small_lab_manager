import os
import json
import logging
from datetime import datetime, date

logger = logging.getLogger('pro_lab_db')

# Global Firestore DB reference
_firestore_client = None
_firebase_initialized = False

def get_firestore_client():
    global _firestore_client, _firebase_initialized
    if _firestore_client is not None:
        return _firestore_client

    import firebase_admin
    from firebase_admin import credentials, firestore

    if not firebase_admin._apps:
        cred = None
        project_id = os.environ.get('FIREBASE_PROJECT_ID', 'small-lab-manager')

        # 1. Check FIREBASE_CREDENTIALS environment variable
        env_cred = os.environ.get('FIREBASE_CREDENTIALS')
        if env_cred:
            env_cred_str = env_cred.strip()
            if env_cred_str.startswith('{'):
                try:
                    cred_info = json.loads(env_cred_str)
                    cred = credentials.Certificate(cred_info)
                    logger.info("Loaded Firebase credentials from FIREBASE_CREDENTIALS JSON string.")
                except Exception as e:
                    logger.error(f"Failed to parse FIREBASE_CREDENTIALS JSON: {e}")
            elif os.path.isfile(env_cred_str):
                try:
                    cred = credentials.Certificate(env_cred_str)
                    logger.info(f"Loaded Firebase credentials from file: {env_cred_str}")
                except Exception as e:
                    logger.error(f"Failed to load credentials from file {env_cred_str}: {e}")

        # 2. Check known local file locations
        if not cred:
            possible_files = [
                os.environ.get('FIREBASE_KEY_PATH'),
                'serviceAccountKey.json',
                'firebase_credentials.json',
                os.path.join(os.path.dirname(__file__), 'serviceAccountKey.json'),
                os.path.join(os.path.dirname(__file__), 'firebase_credentials.json'),
                os.path.join(os.path.expanduser('~'), 'Downloads', 'serviceAccountKey.json'),
            ]
            for f in possible_files:
                if f and os.path.isfile(f):
                    try:
                        cred = credentials.Certificate(f)
                        logger.info(f"Loaded Firebase credentials from {f}")
                        break
                    except Exception as e:
                        logger.error(f"Failed loading {f}: {e}")

        # 3. Initialize Firebase app
        if cred:
            try:
                firebase_admin.initialize_app(cred, {'projectId': project_id})
                _firebase_initialized = True
            except Exception as e:
                logger.warning(f"Firebase app initialization: {e}")
        elif os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'):
            try:
                firebase_admin.initialize_app(options={'projectId': project_id})
                _firebase_initialized = True
            except Exception as e:
                logger.warning(f"Firebase app initialization: {e}")
        else:
            logger.info("No Firebase credentials configured yet. Memory fallback will be active.")
            return None

    try:
        _firestore_client = firestore.client()
        return _firestore_client
    except Exception as e:
        logger.warning(f"Could not connect to live Firestore client ({e}). Memory fallback will be active.")
        return None


# Helper for auto-increment integer IDs
def get_next_id(collection_name):
    fs = get_firestore_client()
    if fs is not None:
        try:
            counter_ref = fs.collection('_counters').document(collection_name)
            from google.cloud import firestore
            counter_ref.set({'next_id': firestore.Increment(1)}, merge=True)
            doc = counter_ref.get()
            if doc.exists:
                val = doc.to_dict().get('next_id')
                if val:
                    return int(val)
        except Exception as e:
            logger.warning(f"Firestore counter error: {e}")

    # Fallback ID generator based on epoch milliseconds
    import time
    return int(time.time() * 1000) % 1000000000


class _Session:
    """Session adapter for db.session.add, commit, delete, flush, rollback."""
    def __init__(self):
        self._new = []
        self._deleted = []

    def add(self, obj):
        if obj not in self._new:
            self._new.append(obj)

    def delete(self, obj):
        if obj in self._new:
            self._new.remove(obj)
        if obj not in self._deleted:
            self._deleted.append(obj)

    def commit(self):
        for obj in self._deleted:
            if hasattr(obj, '_do_delete'):
                obj._do_delete()
        self._deleted.clear()

        for obj in self._new:
            if hasattr(obj, '_do_save'):
                obj._do_save()
        self._new.clear()

    def flush(self):
        # Save pending objects to assign IDs immediately
        for obj in self._new:
            if hasattr(obj, '_do_save'):
                obj._do_save()

    def rollback(self):
        self._new.clear()
        self._deleted.clear()

    def query(self, *args):
        # Supports db.session.query(db.func.sum(...)).filter(...)
        from models import AggregateQuery
        return AggregateQuery(*args)


class _FuncDate:
    def __init__(self, field):
        self.field = field

    def __ge__(self, other):
        return ('ge', self.field, other)

    def __le__(self, other):
        return ('le', self.field, other)

    def __eq__(self, other):
        return ('eq', self.field, other)


class _FuncSum:
    def __init__(self, field):
        self.field = field


class _DBFunc:
    def date(self, field):
        return _FuncDate(field)

    def sum(self, field):
        return _FuncSum(field)


class DatabaseAdapter:
    """Drop-in adapter replacing SQLAlchemy db object."""
    def __init__(self):
        self.session = _Session()
        self.func = _DBFunc()

    def init_app(self, app):
        # Eagerly initialize Firebase client if credentials exist
        try:
            get_firestore_client()
        except Exception as e:
            logger.warning(f"Firebase init during init_app: {e}")

    def create_all(self):
        # No-op for Firestore since collections are schematic and created on write
        pass


db = DatabaseAdapter()
