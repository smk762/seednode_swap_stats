from __future__ import annotations

import bisect
import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .models import Swap


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


