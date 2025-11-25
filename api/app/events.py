from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import List, Optional, Dict, Any


@dataclass
class Event:
	name: str
	start: int
	stop: int
	base_coin: str
	rel_coin: str
	extra: Dict[str, Any]

	def matches_pair(self, coin_a: str, coin_b: str) -> bool:
		ca = coin_a.upper()
		cb = coin_b.upper()
		b = self.base_coin.upper()
		r = self.rel_coin.upper()
		return (ca == b and cb == r) or (ca == r and cb == b)


def load_events(path: Optional[str]) -> List[Event]:
	if not path:
		return []
	if not os.path.exists(path):
		return []
	with open(path, "r") as f:
		data = json.load(f)

	# New schema (dict):
	# {
	#   "group_name": {
	#     "start": ..., "stop": ..., "base_coin": "KMD",
	#     "rel_coins": ["ARRR", "DGB"],
	#     ... extra fields ...
	#   }
	# }
	result: List[Event] = []
	if not isinstance(data, dict):
		return result
	for group_name, details in data.items():
		try:
			start = int(details["start"])  # required
			stop = int(details["stop"])    # required
			base_coin = str(details["base_coin"]).upper()
			rel_coins_raw = details.get("rel_coins")
			if not isinstance(rel_coins_raw, (list, tuple)) or not rel_coins_raw:
				continue
			rel_coins = [str(r).upper() for r in rel_coins_raw if str(r).strip()]
			if not rel_coins:
				continue
			# Build extra payload, excluding core keys and including group metadata
			extra = {k: v for k, v in details.items() if k not in {"start", "stop", "base_coin", "rel_coins"}}
			extra["group_name"] = str(group_name)
			extra["rel_coins"] = rel_coins
			for rel in rel_coins:
				name = f"{group_name}_{rel}"
				result.append(Event(
					name=name,
					start=start,
					stop=stop,
					base_coin=base_coin,
					rel_coin=rel,
					extra=dict(extra),
				))
		except Exception:
			continue
	return result


