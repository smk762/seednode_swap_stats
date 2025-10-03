from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import closing
from decimal import Decimal
from typing import Callable, Optional
import logging

logger = logging.getLogger(__name__)

from .models import Swap


class SQLiteSwapMonitor:
	"""Polls a sqlite database for newly completed swaps and pushes them to a callback."""

	def __init__(
		self,
		db_path: str,
		callback: Callable[[Swap], None],
		poll_interval_seconds: float = 2.0,
		load_history: bool = True,
	) -> None:
		self._db_path = db_path
		self._callback = callback
		self._poll_interval_seconds = poll_interval_seconds
		self._last_seen_id: int = -1
		self._load_history = load_history
		self._thread: Optional[threading.Thread] = None
		self._stop_event = threading.Event()

	def start(self) -> None:
		if self._thread and self._thread.is_alive():
			return
		self._stop_event.clear()
		self._thread = threading.Thread(target=self._run, name="sqlite-swap-monitor", daemon=True)
		self._thread.start()

	def stop(self) -> None:
		self._stop_event.set()
		if self._thread and self._thread.is_alive():
			self._thread.join(timeout=5)

	def _connect(self) -> sqlite3.Connection:
		conn = sqlite3.connect(self._db_path)
		conn.row_factory = sqlite3.Row
		return conn

	def _ensure_last_seen(self, conn: sqlite3.Connection) -> None:
		with closing(conn.cursor()) as cur:
			if self._load_history:
				self._last_seen_id = -1
			else:
				cur.execute("SELECT COALESCE(MAX(id), -1) AS max_id FROM stats_swaps")
				row = cur.fetchone()
				self._last_seen_id = int(row["max_id"]) if row and row["max_id"] is not None else -1

	def _run(self) -> None:
		# Backoff loop waiting for DB file
		while not os.path.exists(self._db_path) and not self._stop_event.is_set():
			time.sleep(1.0)
		if self._stop_event.is_set():
			return

		with self._connect() as conn:
			self._ensure_last_seen(conn)
			while not self._stop_event.is_set():
				try:
					new_last_seen = self._poll_once(conn)
					if new_last_seen is not None:
						self._last_seen_id = new_last_seen
				except Exception:
					# On error, reopen connection after brief delay
					try:
						conn.close()
					except Exception:
						pass
					time.sleep(self._poll_interval_seconds)
					conn = self._connect()
				time.sleep(self._poll_interval_seconds)

	def _poll_once(self, conn: sqlite3.Connection) -> Optional[int]:
		query = (
			"SELECT id, maker_coin, taker_coin, uuid, started_at, finished_at, maker_amount, taker_amount, "
			"is_success, maker_coin_ticker, maker_coin_platform, taker_coin_ticker, taker_coin_platform, "
			"maker_coin_usd_price, taker_coin_usd_price, maker_pubkey, taker_pubkey, maker_gui, taker_gui, maker_version, taker_version "
			"FROM stats_swaps WHERE id > ? ORDER BY id ASC"
		)
		with closing(conn.cursor()) as cur:
			cur.execute(query, (self._last_seen_id,))
			rows = cur.fetchall()
			last_id = None
			for row in rows:
				last_id = int(row["id"])  # type: ignore
				swap = self._row_to_swap(row)
				self._callback(swap)
			return last_id

	def _row_to_swap(self, row: sqlite3.Row) -> Swap:
		logger.info(f"Processing swap: {row}")
		return Swap(
			id=int(row["id"]),
			uuid=str(row["uuid"]),
			maker_coin=str(row["maker_coin"]),
			taker_coin=str(row["taker_coin"]),
			maker_coin_ticker=row["maker_coin_ticker"],
			maker_coin_platform=row["maker_coin_platform"],
			taker_coin_ticker=row["taker_coin_ticker"],
			taker_coin_platform=row["taker_coin_platform"],
			started_at=row["started_at"],
			finished_at=row["finished_at"],
			maker_amount=Decimal(str(row["maker_amount"])) if row["maker_amount"] is not None else Decimal("0"),
			taker_amount=Decimal(str(row["taker_amount"])) if row["taker_amount"] is not None else Decimal("0"),
			maker_coin_usd_price=Decimal(str(row["maker_coin_usd_price"])) if row["maker_coin_usd_price"] is not None else None,
			taker_coin_usd_price=Decimal(str(row["taker_coin_usd_price"])) if row["taker_coin_usd_price"] is not None else None,
			is_success=bool(row["is_success"]) if row["is_success"] is not None else None,
			maker_pubkey=row["maker_pubkey"],
			taker_pubkey=row["taker_pubkey"],
			maker_gui=row["maker_gui"],
			taker_gui=row["taker_gui"],
			maker_version=row["maker_version"],
			taker_version=row["taker_version"],
		)

	def backfill_range(self, start_ts: int, end_ts: int) -> Optional[int]:
		"""Load swaps whose finished_at is within [start_ts, end_ts]. Returns max id loaded."""
		if not os.path.exists(self._db_path):
			return None
		with self._connect() as conn, closing(conn.cursor()) as cur:
			query = (
				"SELECT id, maker_coin, taker_coin, uuid, started_at, finished_at, maker_amount, taker_amount, "
				"is_success, maker_coin_ticker, maker_coin_platform, taker_coin_ticker, taker_coin_platform, "
				"maker_coin_usd_price, taker_coin_usd_price, maker_pubkey, taker_pubkey, maker_gui, taker_gui, maker_version, taker_version "
				"FROM stats_swaps WHERE finished_at BETWEEN ? AND ? ORDER BY id ASC"
			)
			cur.execute(query, (int(start_ts), int(end_ts)))
			rows = cur.fetchall()
			last_id = None
			for row in rows:
				s = self._row_to_swap(row)
				self._callback(s)
				last_id = int(row["id"])  # type: ignore
			return last_id

	def backfill_last_hours(self, hours: int) -> Optional[int]:
		end_ts = int(time.time())
		start_ts = end_ts - hours * 3600
		return self.backfill_range(start_ts, end_ts)


