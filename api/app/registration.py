from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
import logging

logger = logging.getLogger(__name__)


@dataclass
class RegisteredUser:
	id: int
	moniker: str
	address: str  # KMD address provided by user
	pubkey: str
	pubkey_hash: str
	rego_fee: float
	rego_uuid: str
	rego_transaction: Optional[str]
	status: str  # expired | pending | registered
	last_update: int


class RegistrationRepo:
	def __init__(self, db_path: str) -> None:
		self._db_path = db_path

	def _connect(self) -> sqlite3.Connection:
		conn = sqlite3.connect(self._db_path, isolation_level=None)
		conn.row_factory = sqlite3.Row
		return conn

	def ensure_schema(self) -> None:
		with self._connect() as conn, closing(conn.cursor()) as cur:
			cur.execute(
				"""
				CREATE TABLE IF NOT EXISTS registered_users (
					id INTEGER PRIMARY KEY AUTOINCREMENT,
					moniker TEXT NOT NULL UNIQUE CHECK(length(moniker) <= 16),
					address TEXT NOT NULL UNIQUE CHECK(length(address) <= 64),
					pubkey TEXT NOT NULL,
					pubkey_hash TEXT NOT NULL,
					rego_fee REAL NOT NULL,
					rego_uuid TEXT NOT NULL UNIQUE CHECK(length(rego_uuid) <= 64),
					rego_transaction TEXT UNIQUE,
					status TEXT NOT NULL CHECK(status IN ('expired','pending','registered')),
					last_update INTEGER NOT NULL
				)
				"""
			)
			# Useful indices
			cur.execute("CREATE INDEX IF NOT EXISTS idx_registered_users_status ON registered_users(status)")
			cur.execute("CREATE INDEX IF NOT EXISTS idx_registered_users_last_update ON registered_users(last_update)")

	def _row_to_model(self, row: sqlite3.Row) -> RegisteredUser:
		return RegisteredUser(
			id=int(row["id"]),
			moniker=str(row["moniker"]),
			address=str(row["address"]),
			pubkey=str(row["pubkey"]),
			pubkey_hash=str(row["pubkey_hash"]),
			rego_fee=float(row["rego_fee"]),
			rego_uuid=str(row["rego_uuid"]),
			rego_transaction=str(row["rego_transaction"]) if row["rego_transaction"] is not None else None,
			status=str(row["status"]),
			last_update=int(row["last_update"]),
		)

	def get_by_address(self, address: str) -> Optional[RegisteredUser]:
		with self._connect() as conn, closing(conn.cursor()) as cur:
			cur.execute("SELECT * FROM registered_users WHERE address = ?", (address,))
			row = cur.fetchone()
			return self._row_to_model(row) if row else None

	def moniker_in_use(self, moniker: str, ignore_address: Optional[str] = None) -> bool:
		with self._connect() as conn, closing(conn.cursor()) as cur:
			if ignore_address:
				cur.execute(
					"SELECT 1 FROM registered_users WHERE moniker = ? AND address != ? AND status IN ('pending','registered') LIMIT 1",
					(moniker, ignore_address),
				)
			else:
				cur.execute(
					"SELECT 1 FROM registered_users WHERE moniker = ? AND status IN ('pending','registered') LIMIT 1",
					(moniker,),
				)
			return cur.fetchone() is not None

	def create_or_refresh_pending(self, *, moniker: str, address: str, pubkey: str, pubkey_hash: str, rego_fee: float, rego_uuid: str) -> RegisteredUser:
		now_ts = int(time.time())
		with self._connect() as conn, closing(conn.cursor()) as cur:
			existing: Optional[sqlite3.Row] = None
			cur.execute("SELECT * FROM registered_users WHERE address = ?", (address,))
			existing = cur.fetchone()
			# Enforce moniker uniqueness among active (pending/registered) users
			if self.moniker_in_use(moniker, ignore_address=address):
				raise ValueError("moniker already in use")
			if existing is None:
				cur.execute(
					"""
					INSERT INTO registered_users (moniker, address, pubkey, pubkey_hash, rego_fee, rego_uuid, rego_transaction, status, last_update)
					VALUES (?, ?, ?, ?, ?, ?, NULL, 'pending', ?)
					""",
					(moniker, address, pubkey, pubkey_hash, float(rego_fee), rego_uuid, now_ts),
				)
				cur.execute("SELECT * FROM registered_users WHERE address = ?", (address,))
				row = cur.fetchone()
				assert row is not None
				return self._row_to_model(row)
			else:
				if existing["status"] in ("pending", "registered"):
					raise ValueError(f"address already {existing['status']}")
				# expired -> refresh as new registration
				cur.execute(
					"""
					UPDATE registered_users
					SET moniker = ?, pubkey = ?, pubkey_hash = ?, rego_fee = ?, rego_uuid = ?, rego_transaction = NULL, status = 'pending', last_update = ?
					WHERE address = ?
					""",
					(moniker, pubkey, pubkey_hash, float(rego_fee), rego_uuid, now_ts, address),
				)
				cur.execute("SELECT * FROM registered_users WHERE address = ?", (address,))
				row2 = cur.fetchone()
				assert row2 is not None
				return self._row_to_model(row2)

	def list_pending(self) -> List[RegisteredUser]:
		with self._connect() as conn, closing(conn.cursor()) as cur:
			cur.execute("SELECT * FROM registered_users WHERE status = 'pending' ORDER BY last_update ASC")
			return [self._row_to_model(r) for r in cur.fetchall()]

	def expire_old(self, older_than_seconds: int) -> int:
		threshold = int(time.time()) - int(older_than_seconds)
		with self._connect() as conn, closing(conn.cursor()) as cur:
			cur.execute(
				"UPDATE registered_users SET status = 'expired', last_update = ? WHERE status = 'pending' AND last_update < ?",
				(int(time.time()), threshold),
			)
			return cur.rowcount or 0

	def set_registered(self, *, address: str, txid: str) -> None:
		with self._connect() as conn, closing(conn.cursor()) as cur:
			cur.execute(
				"UPDATE registered_users SET rego_transaction = ?, status = 'registered', last_update = ? WHERE address = ? AND status = 'pending'",
				(txid, int(time.time()), address),
			)

	def list_players(self) -> List[Dict[str, Any]]:
		with self._connect() as conn, closing(conn.cursor()) as cur:
			cur.execute("SELECT moniker, pubkey_hash FROM registered_users WHERE status = 'registered' ORDER BY moniker ASC")
			rows = cur.fetchall()
			return [{"moniker": str(r["moniker"]), "pubkey_hash": str(r["pubkey_hash"])} for r in rows]


