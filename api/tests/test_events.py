from __future__ import annotations

import json
from pathlib import Path

from app.events import Event, load_events


def test_event_matches_pair_case_insensitive_and_reversed():
	# base/rel are compared in a case-insensitive and order-agnostic manner
	ev = Event(name="grp_DGB", start=1, stop=2, base_coin="KMD", rel_coin="DGB", extra={})
	assert ev.matches_pair("kmd", "dgb") is True
	assert ev.matches_pair("DGB", "KMD") is True
	assert ev.matches_pair("KMD", "BTC") is False


def test_load_events_parses_grouped_schema(tmp_path: Path):
	data = {
		"KOMODO_FEST": {
			"start": 100,
			"stop": 200,
			"base_coin": "KMD",
			"rel_coins": ["ARRR", "dGb"],
			"note": "extra field kept",
		}
	}
	json_path = tmp_path / "events.json"
	json_path.write_text(json.dumps(data))

	events = load_events(str(json_path))
	# One per rel coin
	assert len(events) == 2
	# Names should be suffixed with rel coin and uppercased symbols
	names = {e.name for e in events}
	assert names == {"KOMODO_FEST_ARRR", "KOMODO_FEST_DGB"}
	# Extra retains group_name and rel_coins in uppercase
	for e in events:
		assert e.extra.get("group_name") == "KOMODO_FEST"
		assert set(e.extra.get("rel_coins") or []) == {"ARRR", "DGB"}
		assert e.extra.get("note") == "extra field kept"
		assert e.base_coin == "KMD"
		assert e.rel_coin in {"ARRR", "DGB"}












