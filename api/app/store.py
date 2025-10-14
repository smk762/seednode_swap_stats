from __future__ import annotations

import bisect
import threading
from collections import defaultdict
from dataclasses import dataclass
import logging
from typing import Dict, List, Optional, Tuple

from .models import Swap
from .events import Event
from .prices import PriceCache

logger = logging.getLogger(__name__)


def _pair_key(maker_coin: str, taker_coin: str) -> str:
	return f"{maker_coin.upper()}|{taker_coin.upper()}"


@dataclass(order=True)
class _TimedSwap:
	finished_at: int
	uuid: str


class SwapStore:
	"""Thread-safe in-memory store for swaps and derived stats."""

	def __init__(self) -> None:
		self._lock = threading.RLock()
		self._uuid_to_swap: Dict[str, Swap] = {}
		self._pair_to_uuids_by_time: Dict[str, List[_TimedSwap]] = defaultdict(list)
		self._events: List[Event] = []
		self._retention_seconds: int = 1 * 3600
		self._price_cache: Optional[PriceCache] = None

	def set_retention_hours(self, hours: int) -> None:
		with self._lock:
			self._retention_seconds = max(0, hours) * 3600

	def set_events(self, events: List[Event]) -> None:
		with self._lock:
			self._events = events

	def set_price_cache(self, cache: PriceCache) -> None:
		with self._lock:
			self._price_cache = cache

	def upsert_swap(self, swap: Swap) -> bool:
		"""Insert swap if new. Returns True if it was newly added."""
		if swap.finished_at is None:
			return False
		with self._lock:
			if swap.uuid in self._uuid_to_swap:
				return False
			self._uuid_to_swap[swap.uuid] = swap
			key = _pair_key(swap.maker_coin, swap.taker_coin)
			bucket = self._pair_to_uuids_by_time[key]
			bisect.insort(bucket, _TimedSwap(finished_at=int(swap.finished_at), uuid=swap.uuid))
			return True

	def get_swap(self, uuid: str) -> Optional[Swap]:
		with self._lock:
			return self._uuid_to_swap.get(uuid)

	def total_count(self) -> int:
		with self._lock:
			return len(self._uuid_to_swap)

	def _is_within_any_event(self, swap: Swap) -> bool:
		if not self._events:
			return False
		for ev in self._events:
			if swap.finished_at is None:
				continue
			if ev.matches_pair(swap.maker_coin, swap.taker_coin) and ev.start <= int(swap.finished_at) <= ev.stop:
				return True
		return False

	def prune(self, now_ts: int) -> int:
		"""Prune swaps older than retention window unless protected by event windows.

		Returns number of removed swaps.
		"""
		cutoff = now_ts - self._retention_seconds
		removed = 0
		with self._lock:
			# Collect UUIDs to delete
			to_delete: List[str] = []
			for uuid, swap in list(self._uuid_to_swap.items()):
				if swap.finished_at is None:
					continue
				if int(swap.finished_at) <= cutoff and not self._is_within_any_event(swap):
					logger.info(f"Pruning swap {uuid} because it is older than {cutoff}")
					to_delete.append(uuid)

			# Remove from primary map
			for uuid in to_delete:
				removed += 1
				del self._uuid_to_swap[uuid]

			# Rebuild pair buckets for removed uuids
			if to_delete:
				for key, bucket in list(self._pair_to_uuids_by_time.items()):
					self._pair_to_uuids_by_time[key] = [entry for entry in bucket if entry.uuid not in to_delete]
		return removed

	def stats_for_pair(self, maker_coin: str, taker_coin: str, start_ts: int, end_ts: int) -> dict:
		"""Compute aggregate stats for a pair within [start_ts, end_ts]."""
		key = _pair_key(maker_coin, taker_coin)
		with self._lock:
			bucket = self._pair_to_uuids_by_time.get(key, [])
			if not bucket:
				return {
					"maker_coin": maker_coin.upper(),
					"taker_coin": taker_coin.upper(),
					"start": start_ts,
					"end": end_ts,
					"total_swaps": 0,
					"maker_amount_sum": "0",
					"taker_amount_sum": "0",
				}
			left = bisect.bisect_left(bucket, _TimedSwap(finished_at=int(start_ts), uuid=""))
			right = bisect.bisect_right(bucket, _TimedSwap(finished_at=int(end_ts), uuid="\uffff"))
			subset = bucket[left:right]
			maker_sum = 0
			taker_sum = 0
			for entry in subset:
				s = self._uuid_to_swap.get(entry.uuid)
				if not s:
					continue
				# Convert Decimal to numeric via str to avoid float rounding issues
				maker_sum += float(str(s.maker_amount))
				taker_sum += float(str(s.taker_amount))
			return {
				"maker_coin": maker_coin.upper(),
				"taker_coin": taker_coin.upper(),
				"start": start_ts,
				"end": end_ts,
				"total_swaps": len(subset),
				"maker_amount_sum": f"{maker_sum}",
				"taker_amount_sum": f"{taker_sum}",
			}

	def event_overview(self, event: Event) -> dict:
		"""Overview for an event window and its coin pair (role-agnostic volumes)."""
		swaps = self.swaps_for_event_pair(event, event.start, event.stop)
		base_sum = 0.0
		rel_sum = 0.0
		users = set()
		for s in swaps:
			if s.maker_coin.upper() == event.base_coin.upper():
				base_sum += float(str(s.maker_amount))
			elif s.maker_coin.upper() == event.rel_coin.upper():
				rel_sum += float(str(s.maker_amount))
			if s.taker_coin.upper() == event.base_coin.upper():
				base_sum += float(str(s.taker_amount))
			elif s.taker_coin.upper() == event.rel_coin.upper():
				rel_sum += float(str(s.taker_amount))
			if s.maker_pubkey:
				users.add(s.maker_pubkey)
			if s.taker_pubkey:
				users.add(s.taker_pubkey)
		b_price = self._price_cache.get_price_usd(event.base_coin) if self._price_cache else None
		r_price = self._price_cache.get_price_usd(event.rel_coin) if self._price_cache else None
		usd_base_value = base_sum * (b_price or 0.0)
		usd_rel_value = rel_sum * (r_price or 0.0)
		return {
			"event_name": event.name,
			"start": event.start,
			"stop": event.stop,
			"event_base_coin": event.base_coin,
			"event_rel_coin": event.rel_coin,
			"user_count": len(users),
			"total_trades": len(swaps),
			"total_base_coin_volume": f"{base_sum}",
			"total_rel_coin_volume": f"{rel_sum}",
			"usd_base_price": b_price,
			"usd_rel_price": r_price,
			"usd_base_value": usd_base_value,
			"usd_rel_value": usd_rel_value,
			"usd_total_value": usd_base_value + usd_rel_value,
		}

	def swaps_for_event_pair(self, event: Event, start_ts: int, end_ts: int) -> List[Swap]:
		"""Return swaps for the event pair within the time window, regardless of maker/taker role."""
		with self._lock:
			left_key = _pair_key(event.base_coin, event.rel_coin)
			right_key = _pair_key(event.rel_coin, event.base_coin)
			result: List[Swap] = []
			for key in (left_key, right_key):
				bucket = self._pair_to_uuids_by_time.get(key, [])
				if not bucket:
					continue
				left = bisect.bisect_left(bucket, _TimedSwap(finished_at=int(start_ts), uuid=""))
				right = bisect.bisect_right(bucket, _TimedSwap(finished_at=int(end_ts), uuid="\uffff"))
				for entry in bucket[left:right]:
					s = self._uuid_to_swap.get(entry.uuid)
					if s:
						result.append(s)
			return sorted(result, key=lambda s: int(s.finished_at or 0), reverse=True)

	def aggregate_trader_metrics(self, event: Event, start_ts: int, end_ts: int, price_cache: Optional[PriceCache], pubkey_search: Optional[str] = None) -> List[dict]:
		"""Aggregate per-trader metrics within event window, summing volumes per coin irrespective of maker/taker.

		Sort by newest activity (finished_at desc) then by USD total value desc.
		"""
		swaps = self.swaps_for_event_pair(event, start_ts, end_ts)
		if pubkey_search:
			needle = pubkey_search.lower()
			swaps = [s for s in swaps if (s.maker_pubkey and needle in s.maker_pubkey.lower()) or (s.taker_pubkey and needle in s.taker_pubkey.lower())]

		# Ensure prices are tracked
		symbols = {event.base_coin.upper(), event.rel_coin.upper()}
		if price_cache:
			price_cache.register_symbols(symbols)

		per_trader: Dict[str, dict] = {}
		for s in swaps:
			for pubkey in filter(None, [s.maker_pubkey, s.taker_pubkey]):
				rec = per_trader.setdefault(pubkey, {
					"pubkey": pubkey,
					"base_coin_volume": 0.0,
					"rel_coin_volume": 0.0,
					"trades_as_maker": 0,
					"trades_as_taker": 0,
					"trades_total": 0,
					"last_finished_at": 0,
					"usd_base_price": None,
					"usd_rel_price": None,
					"usd_base_value": 0.0,
					"usd_rel_value": 0.0,
					"usd_total_value": 0.0,
				})
			# Accumulate volumes irrespective of role
			if s.maker_coin.upper() == event.base_coin.upper():
				rec["base_coin_volume"] += float(str(s.maker_amount))
			elif s.maker_coin.upper() == event.rel_coin.upper():
				rec["rel_coin_volume"] += float(str(s.maker_amount))
			if s.taker_coin.upper() == event.base_coin.upper():
				rec["base_coin_volume"] += float(str(s.taker_amount))
			elif s.taker_coin.upper() == event.rel_coin.upper():
				rec["rel_coin_volume"] += float(str(s.taker_amount))
			# Role counts for both pubkeys
			rec["trades_total"] += 1
			if pubkey == s.maker_pubkey:
				rec["trades_as_maker"] += 1
			else:
				rec["trades_as_taker"] += 1
			rec["last_finished_at"] = max(rec["last_finished_at"], int(s.finished_at or 0))

		# Prices and USD value
		base_price = price_cache.get_price_usd(event.base_coin) if price_cache else None
		rel_price = price_cache.get_price_usd(event.rel_coin) if price_cache else None
		for rec in per_trader.values():
			rec["usd_base_price"] = base_price
			rec["usd_rel_price"] = rel_price
			rec["usd_base_value"] = (rec["base_coin_volume"] or 0.0) * (base_price or 0.0)
			rec["usd_rel_value"] = (rec["rel_coin_volume"] or 0.0) * (rel_price or 0.0)
			rec["usd_total_value"] = rec["usd_base_value"] + rec["usd_rel_value"]

		rows = list(per_trader.values())
		rows.sort(key=lambda r: (r["last_finished_at"], r["usd_total_value"]), reverse=True)
		return rows


