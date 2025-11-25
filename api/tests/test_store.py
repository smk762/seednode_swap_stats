from __future__ import annotations

import time
from decimal import Decimal

from app.events import Event
from app.models import Swap
from app.store import SwapStore, _normalize_symbol


def make_swap(
	id: int,
	uuid: str,
	maker_coin: str,
	taker_coin: str,
	finished_at: int,
	maker_amount: Decimal,
	taker_amount: Decimal,
	maker_pubkey: str | None = None,
	taker_pubkey: str | None = None,
	maker_ticker: str | None = None,
	taker_ticker: str | None = None,
	maker_usd: Decimal | None = None,
	taker_usd: Decimal | None = None,
) -> Swap:
	return Swap(
		id=id,
		uuid=uuid,
		maker_coin=maker_coin,
		taker_coin=taker_coin,
		maker_coin_ticker=maker_ticker,
		taker_coin_ticker=taker_ticker,
		started_at=finished_at - 10,
		finished_at=finished_at,
		maker_amount=maker_amount,
		taker_amount=taker_amount,
		maker_coin_usd_price=maker_usd,
		taker_coin_usd_price=taker_usd,
		is_success=True,
		maker_pubkey=maker_pubkey,
		taker_pubkey=taker_pubkey,
	)


def test_normalize_symbol_prefers_ticker_and_strips_suffix():
	assert _normalize_symbol("DGB-segwit", None) == "DGB"
	assert _normalize_symbol("DGB-segwit", "dgb") == "DGB"
	assert _normalize_symbol("KMD", "kmd") == "KMD"


def test_upsert_and_swaps_for_event_pair_ordering():
	store = SwapStore()
	event = Event(name="E", start=100, stop=1000, base_coin="KMD", rel_coin="DGB", extra={})
	# Two swaps across both directions; later one should come first
	s1 = make_swap(
		1,
		"u1",
		"KMD",
		"DGB",
		finished_at=200,
		maker_amount=Decimal("1"),
		taker_amount=Decimal("2"),
	)
	s2 = make_swap(
		2,
		"u2",
		"DGB",
		"KMD",
		finished_at=300,
		maker_amount=Decimal("3"),
		taker_amount=Decimal("4"),
	)
	assert store.upsert_swap(s1) is True
	assert store.upsert_swap(s2) is True
	rows = store.swaps_for_event_pair(event, event.start, event.stop)
	assert [r.uuid for r in rows] == ["u2", "u1"]


def test_prune_respects_retention_and_event_windows():
	store = SwapStore()
	now = int(time.time())
	old_ts = now - 7200
	new_ts = now - 60
	old_swap = make_swap(1, "old", "KMD", "DGB", finished_at=old_ts, maker_amount=Decimal("1"), taker_amount=Decimal("1"))
	new_swap = make_swap(2, "new", "KMD", "DGB", finished_at=new_ts, maker_amount=Decimal("1"), taker_amount=Decimal("1"))
	store.upsert_swap(old_swap)
	store.upsert_swap(new_swap)
	# Protect old swap via event window
	event = Event(name="E", start=old_ts - 10, stop=old_ts + 10, base_coin="KMD", rel_coin="DGB", extra={})
	store.set_events([event])
	removed = store.prune(now)
	assert removed == 0
	# Remove protection and prune again; only old should be removed
	store.set_events([])
	removed2 = store.prune(now)
	assert removed2 == 1
	assert store.get_swap("new") is not None
	assert store.get_swap("old") is None


def test_aggregate_trader_metrics_basic():
	store = SwapStore()
	event = Event(name="E", start=0, stop=999999, base_coin="KMD", rel_coin="DGB", extra={})
	# One swap: maker KMD 10 @ $2, taker DGB 5 @ $10
	s = make_swap(
		1,
		"u1",
		"KMD",
		"DGB",
		finished_at=500,
		maker_amount=Decimal("10"),
		taker_amount=Decimal("5"),
		maker_pubkey="pkA",
		taker_pubkey="pkB",
		maker_usd=Decimal("2"),
		taker_usd=Decimal("10"),
	)
	store.upsert_swap(s)
	rows = store.aggregate_trader_metrics(event, event.start, event.stop, price_cache=None)
	# Expect two traders with totals: base 10*2=20, rel 5*10=50, total=70
	assert len(rows) == 2
	for r in rows:
		assert r["trades_total"] == 1
		assert r["usd_base_value"] == 20.0
		assert r["usd_rel_value"] == 50.0
		assert r["usd_total_value"] == 70.0
	# Rank should be 1 and 2 after sort, but equal totals => stable order by rank assignment
	ranks = {r["rank"] for r in rows}
	assert ranks == {1, 2}












