from __future__ import annotations

import os
from typing import Optional
import time
import asyncio

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from dataclasses import dataclass
from .config import AppConfig

from .db_monitor import SQLiteSwapMonitor
from .models import Swap
from .store import SwapStore
from .events import load_events
from .prices import CoinConfig, PriceCache


@dataclass
class AppState:
	# Simple holder for app state
	store: SwapStore
	monitor: SQLiteSwapMonitor


def create_app() -> FastAPI:
	app = FastAPI(title="Swap Tracker API", version="0.1.0")

	# Centralized config
	config = AppConfig.load()

	# DB path
	db_path = config.kdf_db_path
	store = SwapStore()
	# Price cache
	coin_cfg = CoinConfig.load()
	price_cache = PriceCache(coin_cfg)
	price_cache.start()
	store.set_price_cache(price_cache)
	monitor = SQLiteSwapMonitor(db_path=db_path, callback=lambda s: store.upsert_swap(s), load_history=config.kdf_load_history)
    
	# Backfill since given timestamp or last 24 hours to make stats available on launch
	try:
		if config.backfill_since is not None:
			monitor.backfill_range(int(config.backfill_since), int(time.time()))
		else:
			monitor.backfill_last_hours(1)
	except Exception:
		pass

	# Load events from JSON path and backfill their windows
	events = load_events(config.events_json_path)
	store.set_events(events)
	for ev in events:
		try:
			monitor.backfill_range(ev.start, ev.stop)
		except Exception:
			pass
	monitor.start()

	state = AppState(store=store, monitor=monitor)
	app.state.swap_state = state  # type: ignore[attr-defined]

	@app.on_event("shutdown")
	def _on_shutdown() -> None:
		state.monitor.stop()

	# Periodic pruning task: keep last 24h (configurable) and protect event windows
	store.set_retention_hours(config.retention_hours)

	async def _pruner() -> None:
		while True:
			store.prune(int(time.time()))
			await asyncio.sleep(60)

	@app.on_event("startup")
	async def _on_startup() -> None:
		asyncio.create_task(_pruner())

	def get_store() -> SwapStore:
		return app.state.swap_state.store  # type: ignore[attr-defined]

	@app.get("/healthz")
	def healthz() -> dict:
		return {"ok": True}

	@app.get("/swap/{uuid}", response_model=Swap)
	def get_swap(uuid: str, store: SwapStore = Depends(get_store)):
		s = store.get_swap(uuid)
		if not s:
			raise HTTPException(status_code=404, detail="swap not found")
		return s

	# Proposed endpoints per issue
	# /events?event_name=...
	@app.get("/events")
	def events(event_name: Optional[str] = Query(None)):
		# Return details for requested event or overview list
		all_events = events
		if event_name:
			for ev in all_events:
				if ev.name == event_name:
					return {
						ev.name: {
							"start": ev.start,
							"stop": ev.stop,
							"base_coin": ev.base_coin,
							"rel_coin": ev.rel_coin,
							**ev.extra,
						}
					}
			return {}
		# Overview list
		return [app.state.swap_state.store.event_overview(ev) for ev in all_events]  # type: ignore[attr-defined]

	# /traders?event_name=...&limit=50&offset=0&search=
	@app.get("/traders")
	def traders(event_name: str = Query(...), limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0), search: Optional[str] = Query(None)):
		all_events = events
		ev = next((e for e in all_events if e.name == event_name), None)
		if not ev:
			raise HTTPException(status_code=404, detail="event not found")
		rows = store.aggregate_trader_metrics(ev, ev.start, ev.stop, price_cache, pubkey_search=search)
		return rows[offset:offset+limit]

	# /trader_swaps?event_name=...&pubkey=...&limit=50&offset=0&search=
	@app.get("/trader_swaps")
	def trader_swaps(event_name: str = Query(...), pubkey: Optional[str] = Query(None), limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0), search: Optional[str] = Query(None)):
		all_events = events
		ev = next((e for e in all_events if e.name == event_name), None)
		if not ev:
			raise HTTPException(status_code=404, detail="event not found")
		swaps = store.swaps_for_event_pair(ev, ev.start, ev.stop)
		if search:
			needle = search.lower()
			swaps = [s for s in swaps if (s.maker_pubkey and needle in s.maker_pubkey.lower()) or (s.taker_pubkey and needle in s.taker_pubkey.lower())]
		if pubkey:
			swaps = [s for s in swaps if s.maker_pubkey == pubkey or s.taker_pubkey == pubkey]
		# Enrich with USD price and values per coin
		b_price = price_cache.get_price_usd(ev.base_coin)
		r_price = price_cache.get_price_usd(ev.rel_coin)
		def _row(s: Swap) -> dict:
			base_vol = 0.0
			rel_vol = 0.0
			if s.maker_coin.upper() == ev.base_coin.upper():
				base_vol += float(str(s.maker_amount))
			elif s.maker_coin.upper() == ev.rel_coin.upper():
				rel_vol += float(str(s.maker_amount))
			if s.taker_coin.upper() == ev.base_coin.upper():
				base_vol += float(str(s.taker_amount))
			elif s.taker_coin.upper() == ev.rel_coin.upper():
				rel_vol += float(str(s.taker_amount))
			usd_base_value = base_vol * (b_price or 0.0)
			usd_rel_value = rel_vol * (r_price or 0.0)
			return {
				**s.dict(),
				"usd_base_price": b_price,
				"usd_rel_price": r_price,
				"usd_base_value": usd_base_value,
				"usd_rel_value": usd_rel_value,
				"usd_total_value": usd_base_value + usd_rel_value,
			}
		swaps_sorted = sorted(swaps, key=lambda s: int(s.finished_at or 0), reverse=True)
		rows = [_row(s) for s in swaps_sorted]
		return rows[offset:offset+limit]

	# Removed legacy stats endpoints; new event-based endpoints will be added below

	# Legacy websocket removed with totals endpoint

	return app


app = create_app()


