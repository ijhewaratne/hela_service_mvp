"""SQLite demo storage. Never treat this module as production security or concurrency design."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sqlite3

SCHEMA = r'''
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS actors (
  id TEXT PRIMARY KEY, role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS categories (
  code TEXT PRIMARY KEY, display_name TEXT NOT NULL, enabled INTEGER NOT NULL,
  hourly_rate_lkr INTEGER NOT NULL, safeguards_required INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS addresses (
  id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES actors(id),
  label TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS providers (
  actor_id TEXT PRIMARY KEY REFERENCES actors(id),
  status TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL,
  service_radius_km REAL NOT NULL, recent_decline_rate REAL NOT NULL DEFAULT 0,
  recent_assignments INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS provider_skills (
  provider_id TEXT NOT NULL REFERENCES providers(actor_id),
  category TEXT NOT NULL REFERENCES categories(code),
  status TEXT NOT NULL, credentials_expires_at TEXT,
  safeguards_verified INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (provider_id, category)
);
CREATE TABLE IF NOT EXISTS provider_availability (
  provider_id TEXT NOT NULL REFERENCES providers(actor_id),
  starts_at TEXT NOT NULL, ends_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS requests (
  id TEXT PRIMARY KEY, booker_id TEXT NOT NULL REFERENCES actors(id),
  address_id TEXT NOT NULL REFERENCES addresses(id),
  category TEXT NOT NULL REFERENCES categories(code), task_details TEXT NOT NULL,
  starts_at TEXT NOT NULL, ends_at TEXT NOT NULL,
  status TEXT NOT NULL, estimate_lkr INTEGER NOT NULL,
  pricing_policy_version TEXT NOT NULL DEFAULT 'demo-v1',
  idempotency_key TEXT NOT NULL, payload_hash TEXT NOT NULL,
  created_at TEXT NOT NULL, UNIQUE(booker_id,idempotency_key)
);
CREATE TABLE IF NOT EXISTS offers (
  id TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES requests(id),
  provider_id TEXT NOT NULL REFERENCES providers(actor_id),
  status TEXT NOT NULL, expires_at TEXT NOT NULL,
  match_score REAL NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(request_id,provider_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_accepted_offer_per_request
  ON offers(request_id) WHERE status='accepted';
CREATE TABLE IF NOT EXISTS bookings (
  id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE REFERENCES requests(id),
  provider_id TEXT NOT NULL REFERENCES providers(actor_id),
  offer_id TEXT NOT NULL UNIQUE REFERENCES offers(id), status TEXT NOT NULL,
  version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS booking_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  booking_id TEXT NOT NULL REFERENCES bookings(id), actor_id TEXT NOT NULL,
  from_status TEXT, to_status TEXT NOT NULL, details TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS match_decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id TEXT NOT NULL REFERENCES requests(id),
  summary_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settlement_records (
  booking_id TEXT PRIMARY KEY REFERENCES bookings(id),
  agreed_amount_lkr INTEGER NOT NULL, commission_due_lkr INTEGER NOT NULL DEFAULT 0,
  payment_status TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS incidents (
  id TEXT PRIMARY KEY, booking_id TEXT NOT NULL REFERENCES bookings(id),
  reporter_id TEXT NOT NULL REFERENCES actors(id), severity TEXT NOT NULL,
  kind TEXT NOT NULL, description TEXT NOT NULL,
  status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS action_idempotency (
  actor_id TEXT NOT NULL, scope TEXT NOT NULL, key TEXT NOT NULL,
  request_hash TEXT NOT NULL, response_json TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY(actor_id,scope,key)
);
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id TEXT NOT NULL,
  action TEXT NOT NULL, entity_id TEXT NOT NULL,
  details_json TEXT NOT NULL, created_at TEXT NOT NULL
);
'''

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec='microseconds')

class DemoDatabase:
    def __init__(self, path: str):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connection(self, *, write: bool = False):
        con = sqlite3.connect(self.path, timeout=20, isolation_level=None, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        con.execute('PRAGMA busy_timeout=20000')
        try:
            # BEGIN IMMEDIATE serializes writes in this single-node SQLite demo.
            con.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield con
            con.execute('COMMIT')
        except Exception:
            if con.in_transaction:
                con.execute('ROLLBACK')
            raise
        finally:
            con.close()

    def initialize(self):
        with sqlite3.connect(self.path) as con:
            con.executescript(SCHEMA)
            con.commit()
        self.seed()

    def seed(self):
        # Fixed identifiers, synthetic locations/personas only; all future slots.
        now = now_utc()
        slot_start = iso(now - timedelta(days=1))
        slot_end = iso(now + timedelta(days=60))
        expiry = iso(now + timedelta(days=90))
        expired = iso(now - timedelta(days=3))
        actors = [('customer_asha','customer'),('customer_nimal','customer'),
                  ('provider_alpha','provider'),('provider_beta','provider'),
                  ('provider_gamma','provider'),('provider_unverified','provider'),
                  ('provider_suspended','provider'),('provider_expired','provider'),
                  ('provider_far','provider'),('admin_ops','admin')]
        with self.connection(write=True) as con:
            for actor, role in actors:
                con.execute('INSERT OR IGNORE INTO actors(id,role) VALUES(?,?)', (actor,role))
            for category, title, enabled, rate, safeguard in [
                ('house_cleaning','Household cleaning',1,1200,0),
                ('errands','Shopping and errands',1,1000,0),
                ('elder_companionship','Elder companionship (pilot disabled)',0,1500,1),
                ('child_supervision','Child supervision (pilot disabled)',0,1500,1),
                ('clinical_care','Clinical care (not supported)',0,2000,1)]:
                con.execute('INSERT OR IGNORE INTO categories VALUES(?,?,?,?,?)',
                            (category,title,enabled,rate,safeguard))
            for address, owner, label, lat, lon in [
                ('addr_asha','customer_asha','Synthetic Kandy address',7.2906,80.6337),
                ('addr_nimal','customer_nimal','Synthetic Kandy address',7.2920,80.6300)]:
                con.execute('INSERT OR IGNORE INTO addresses VALUES(?,?,?,?,?)',
                            (address,owner,label,lat,lon))
            for actor, status, lat, lon, radius, decline, count in [
                ('provider_alpha','active',7.2910,80.6340,10,0.05,2),
                ('provider_beta','active',7.2940,80.6390,10,0.10,1),
                ('provider_gamma','active',7.2980,80.6400,10,0.04,0),
                ('provider_unverified','active',7.29061,80.63371,10,0,0),
                ('provider_suspended','suspended',7.2907,80.6338,10,0,0),
                ('provider_expired','active',7.2908,80.6339,10,0,0),
                ('provider_far','active',6.9271,79.8612,5,0,0)]:
                con.execute('INSERT OR IGNORE INTO providers VALUES(?,?,?,?,?,?,?)',
                            (actor,status,lat,lon,radius,decline,count))
                existing_slots=con.execute('SELECT 1 FROM provider_availability WHERE provider_id=? LIMIT 1', (actor,)).fetchone()
                if not existing_slots:
                    con.execute('INSERT INTO provider_availability VALUES(?,?,?)',
                                (actor,slot_start,slot_end))
                skill_status = 'pending' if actor == 'provider_unverified' else 'approved'
                credential = expired if actor == 'provider_expired' else expiry
                con.execute('INSERT OR IGNORE INTO provider_skills VALUES(?,?,?,?,?)',
                            (actor,'house_cleaning',skill_status,credential,0))
            for actor in ('provider_alpha','provider_beta','provider_gamma'):
                con.execute('INSERT OR IGNORE INTO provider_skills VALUES(?,?,?,?,?)',
                            (actor,'errands','approved',expiry,0))
