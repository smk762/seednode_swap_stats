from __future__ import annotations

import os
from typing import Optional
import time
import asyncio
import sys
import logging

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
import hmac
import hashlib
from fastapi.responses import JSONResponse
from dataclasses import dataclass
from .config import AppConfig
def _configure_logging() -> None:
	root = logging.getLogger()
	if not root.handlers:
		h = logging.StreamHandler(sys.stdout)
		h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
		root.addHandler(h)
	root.setLevel(logging.INFO)


_configure_logging()

from .db_monitor import SQLiteSwapMonitor
from .models import Swap
from .store import SwapStore
from .events import load_events
from .prices import CoinConfig, PriceCache
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

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
	loaded_events = load_events(config.events_json_path)
	store.set_events(loaded_events)
	for ev in loaded_events:
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

	@app.get("/swap/{uuid}")
	def get_swap(uuid: str, store: SwapStore = Depends(get_store)):
		s = store.get_swap(uuid)
		if not s:
			raise HTTPException(status_code=404, detail="swap not found")
		secret = (config.pubkey_hash_key or "").encode("utf-8")
		def _hash_pubkey(value: Optional[str]) -> Optional[str]:
			if not value:
				return None
			if not secret:
				return hashlib.sha256(value.encode("utf-8")).hexdigest()
			return hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()
		payload = {**s.dict()}
		maker_hash = _hash_pubkey(payload.get("maker_pubkey"))
		taker_hash = _hash_pubkey(payload.get("taker_pubkey"))
		payload.pop("maker_pubkey", None)
		payload.pop("taker_pubkey", None)
		payload.update({
			"maker_pubkey_hash": maker_hash,
			"taker_pubkey_hash": taker_hash,
		})
		return payload

	# Proposed endpoints per issue
	# /event_details?event_name=...
	@app.get("/event_details")
	def event_details(event_name) -> dict:
		# Return details for requested event
		all_events = app.state.swap_state.store.get_events()  # type: ignore[attr-defined]
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
		return {"error": f"event `{event_name}` not found"}

	@app.get("/events")
	def events() -> dict:
		# Return all event names
		return JSONResponse([ev.name for ev in app.state.swap_state.store.get_events()])  # type: ignore[attr-defineded]

	# /hash_pubkey?pubkey=...
	@app.get("/hash_pubkey")
	def hash_pubkey(pubkey: str = Query(...)) -> dict:
		secret = (config.pubkey_hash_key or "").encode("utf-8")
		if not secret:
			return {"pubkey_hash": hashlib.sha256(pubkey.encode("utf-8")).hexdigest()}
		return {"pubkey_hash": hmac.new(secret, pubkey.encode("utf-8"), hashlib.sha256).hexdigest()}

	# /traders?event_name=...&limit=50&offset=0&search=
	@app.get("/traders")
	def traders(event_name: str = Query(...), limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0), search: Optional[str] = Query(None)):
		logger.info(f"Traders request: {event_name} {limit} {offset} {search}")
		all_events = app.state.swap_state.store.get_events()  # type: ignore[attr-defined]
		ev = next((e for e in all_events if e.name == event_name), None)
		if not ev:
			return {"error": f"event `{event_name}` not found"}
		rows = store.aggregate_trader_metrics(ev, ev.start, ev.stop, price_cache, pubkey_search=search)
		# Annotate with coin tickers so clients can label volumes
		sliced = rows[offset:offset+limit]
		secret = (config.pubkey_hash_key or "").encode("utf-8")
		def _hash_pubkey(value: Optional[str]) -> Optional[str]:
			if not value:
				return None
			if not secret:
				# If no key configured, return a deterministic salted hash with empty key
				return hashlib.sha256(value.encode("utf-8")).hexdigest()
			return hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()
		annotated = []
		for r in sliced:
			pubkey = r.get("pubkey")
			h = _hash_pubkey(pubkey)
			out = {k: v for k, v in r.items() if k != "pubkey"}
			out.update({
				"pubkey_hash": h,
				"event_base_coin": ev.base_coin,
				"event_rel_coin": ev.rel_coin,
				"pair": f"{ev.base_coin}/{ev.rel_coin}",
			})
			annotated.append(out)
		return annotated

	# /trader_swaps?event_name=...&pubkey=...&limit=50&offset=0&search=
	@app.get("/trader_swaps")
	def trader_swaps(event_name: str = Query(...), pubkey: Optional[str] = Query(None), limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0), search: Optional[str] = Query(None)):
		all_events = app.state.swap_state.store.get_events()  # type: ignore[attr-defined]
		ev = next((e for e in all_events if e.name == event_name), None)
		if not ev:
			return {"error": f"event `{event_name}` not found"}
		swaps = store.swaps_for_event_pair(ev, ev.start, ev.stop)
		if search:
			needle = search.lower()
			swaps = [s for s in swaps if (s.maker_pubkey and needle in s.maker_pubkey.lower()) or (s.taker_pubkey and needle in s.taker_pubkey.lower())]
		if pubkey:
			secret = (config.pubkey_hash_key or "").encode("utf-8")
			def _hash_pubkey(value: Optional[str]) -> Optional[str]:
				if not value:
					return None
				if not secret:
					return hashlib.sha256(value.encode("utf-8")).hexdigest()
				return hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()
			needle_raw_or_hash = str(pubkey)
			def _matches_swap_pubkey(s_pub: Optional[str]) -> bool:
				if not s_pub:
					return False
				return s_pub == needle_raw_or_hash or _hash_pubkey(s_pub) == needle_raw_or_hash
			swaps = [s for s in swaps if _matches_swap_pubkey(s.maker_pubkey) or _matches_swap_pubkey(s.taker_pubkey)]
		# Enrich with USD price and values per coin
		b_price = price_cache.get_price_usd(ev.base_coin)
		r_price = price_cache.get_price_usd(ev.rel_coin)
		secret = (config.pubkey_hash_key or "").encode("utf-8")
		def _hash_pubkey_resp(value: Optional[str]) -> Optional[str]:
			if not value:
				return None
			if not secret:
				return hashlib.sha256(value.encode("utf-8")).hexdigest()
			return hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()
		def _row(s: Swap) -> dict:
			base_vol = 0.0
			rel_vol = 0.0
			if s.maker_coin_ticker.upper() == ev.base_coin.upper():
				base_vol += float(str(s.maker_amount))
			elif s.maker_coin_ticker.upper() == ev.rel_coin.upper():
				rel_vol += float(str(s.maker_amount))
			if s.taker_coin_ticker.upper() == ev.base_coin.upper():
				base_vol += float(str(s.taker_amount))
			elif s.taker_coin_ticker.upper() == ev.rel_coin.upper():
				rel_vol += float(str(s.taker_amount))
			# Prefer per-swap recorded USD prices aligned to event base/rel, fallback to cache
			swap_base_price = None
			swap_rel_price = None
			if s.maker_coin_ticker and s.maker_coin_ticker.upper() == ev.base_coin.upper() and s.maker_coin_usd_price is not None:
				swap_base_price = float(s.maker_coin_usd_price)
			elif s.taker_coin_ticker and s.taker_coin_ticker.upper() == ev.base_coin.upper() and s.taker_coin_usd_price is not None:
				swap_base_price = float(s.taker_coin_usd_price)
			if s.maker_coin_ticker and s.maker_coin_ticker.upper() == ev.rel_coin.upper() and s.maker_coin_usd_price is not None:
				swap_rel_price = float(s.maker_coin_usd_price)
			elif s.taker_coin_ticker and s.taker_coin_ticker.upper() == ev.rel_coin.upper() and s.taker_coin_usd_price is not None:
				swap_rel_price = float(s.taker_coin_usd_price)
			usd_base_value = base_vol * (swap_base_price if swap_base_price is not None else (b_price or 0.0))
			usd_rel_value = rel_vol * (swap_rel_price if swap_rel_price is not None else (r_price or 0.0))
			payload = {**s.dict()}
			maker_hash = _hash_pubkey_resp(s.maker_pubkey)
			taker_hash = _hash_pubkey_resp(s.taker_pubkey)
			if "maker_pubkey" in payload:
				payload.pop("maker_pubkey", None)
			if "taker_pubkey" in payload:
				payload.pop("taker_pubkey", None)
			payload.update({
				"maker_pubkey_hash": maker_hash,
				"taker_pubkey_hash": taker_hash,
				"usd_base_price": swap_base_price if swap_base_price is not None else b_price,
				"usd_rel_price": swap_rel_price if swap_rel_price is not None else r_price,
				"usd_base_value": usd_base_value,
				"usd_rel_value": usd_rel_value,
				"usd_total_value": usd_base_value + usd_rel_value,
				"event_base_coin": ev.base_coin,
				"event_rel_coin": ev.rel_coin,
			})
			return payload
		swaps_sorted = sorted(swaps, key=lambda s: int(s.finished_at or 0), reverse=True)
		rows = [_row(s) for s in swaps_sorted]
		return rows[offset:offset+limit]

	# Removed legacy stats endpoints; new event-based endpoints will be added below

	# Legacy websocket removed with totals endpoint

	return app

app = create_app()


