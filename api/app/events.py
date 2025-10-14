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

	# Accept two shapes:
	# 1) List[Event-like]
	# 2) Dict[name -> details]
	result: List[Event] = []
	if isinstance(data, list):
		for item in data:
			try:
				extra = {k: v for k, v in item.items() if k not in {"name", "event_name", "start", "stop", "base_coin", "rel_coin"}}
				result.append(Event(
					name=str(item.get("name") or item.get("event_name") or "event"),
					start=int(item["start"]),
					stop=int(item["stop"]),
					base_coin=str(item["base_coin"]).upper(),
					rel_coin=str(item["rel_coin"]).upper(),
					extra=extra,
				))
			except Exception:
				continue
	elif isinstance(data, dict):
		for name, details in data.items():
			try:
				extra = {k: v for k, v in details.items() if k not in {"start", "stop", "base_coin", "rel_coin"}}
				result.append(Event(
					name=str(name),
					start=int(details["start"]),
					stop=int(details["stop"]),
					base_coin=str(details["base_coin"]).upper(),
					rel_coin=str(details["rel_coin"]).upper(),
					extra=extra,
				))
			except Exception:
				continue
	return result


