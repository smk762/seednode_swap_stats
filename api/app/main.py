from __future__ import annotations

import os
from typing import Optional, Dict, List
import time
import asyncio
import sys
import logging

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
import hmac
import hashlib
from fastapi.responses import JSONResponse
from dataclasses import dataclass
from contextlib import asynccontextmanager
from pydantic import BaseModel, Field, validator
from .config import AppConfig
from .insight_api import InsightAPI
from .registration import RegistrationRepo
from .based58 import calc_addr_from_pubkey
from bitcoin.core import Hash160, x, b2x
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

class RegisterRequest(BaseModel):
	address: str = Field(..., max_length=64, description="KMD address")
	swap_uuid: str = Field(..., max_length=64)
	moniker: str = Field(..., max_length=16)

	@validator("moniker")
	def _trim_moniker(cls, v: str) -> str:  # type: ignore
		return v.strip()

class RegisterResponse(BaseModel):
	registration_address: str
	registration_amount: float

@dataclass
class AppState:
	# Simple holder for app state
	store: SwapStore
	monitor: SQLiteSwapMonitor
	reg_repo: RegistrationRepo
	insight: InsightAPI


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

	# Registration components
	reg_repo = RegistrationRepo(db_path=config.registration_db_path)
	reg_repo.ensure_schema()
	insight = InsightAPI(baseurl=config.doc_insight_base_url, api_path=config.doc_insight_api_path)

	state = AppState(store=store, monitor=monitor, reg_repo=reg_repo, insight=insight)
	app.state.swap_state = state  # type: ignore[attr-defined]

	# Periodic pruning task: keep last 24h (configurable) and protect event windows
	store.set_retention_hours(config.retention_hours)

	async def _pruner() -> None:
		while True:
			store.prune(int(time.time()))
			await asyncio.sleep(60)

	@asynccontextmanager
	async def lifespan(app: FastAPI):
		# Start background tasks
		pruner_task = asyncio.create_task(_pruner())
		reg_task = asyncio.create_task(_registration_watcher())
		try:
			yield
		finally:
			# Stop tasks and monitor cleanly
			for t in (pruner_task, reg_task):
				try:
					t.cancel()
				except Exception:
					pass
			try:
				await asyncio.gather(pruner_task, reg_task, return_exceptions=True)
			except Exception:
				pass
			try:
				state.monitor.stop()
			except Exception:
				pass
	# Attach lifespan handler
	app.router.lifespan_context = lifespan  # type: ignore[attr-defined]

	def get_store() -> SwapStore:
		return app.state.swap_state.store  # type: ignore[attr-defined]
	def get_reg_repo() -> RegistrationRepo:
		return app.state.swap_state.reg_repo  # type: ignore[attr-defined]
	def get_insight() -> InsightAPI:
		return app.state.swap_state.insight  # type: ignore[attr-defined]

	@app.get("/healthz")
	def healthz() -> dict:
		return {"ok": True}

	@app.get("/players")
	def players(reg_repo: RegistrationRepo = Depends(get_reg_repo)) -> Dict[str, str]:
		rows = reg_repo.list_players()
		return {r["moniker"]: r["pubkey_hash"] for r in rows}

	# Registration endpoint

	def _lookup_swap_pubkeys(uuid: str) -> dict:
		# Query the same sqlite DB used by the monitor
		import sqlite3
		from contextlib import closing
		with sqlite3.connect(config.kdf_db_path, isolation_level=None) as conn, closing(conn.cursor()) as cur:
			cur.execute(
				"SELECT maker_coin_ticker, taker_coin_ticker, maker_pubkey, taker_pubkey FROM stats_swaps WHERE uuid = ? LIMIT 1",
				(uuid,),
			)
			row = cur.fetchone()
			if not row:
				raise HTTPException(status_code=404, detail="swap uuid not found")
			return {
				"maker_coin_ticker": row[0],
				"taker_coin_ticker": row[1],
				"maker_pubkey": row[2],
				"taker_pubkey": row[3],
			}

	def _pubkey_for_kmd_address(kmd_address: str, maker_pubkey: Optional[str], taker_pubkey: Optional[str]) -> Optional[str]:
		for pk in [maker_pubkey, taker_pubkey]:
			if not pk:
				continue
			derived = calc_addr_from_pubkey("KMD", pk)
			if isinstance(derived, dict) and derived.get("error"):
				continue
			if str(derived) == str(kmd_address):
				return pk
		return None

	def _hash160_hex_from_pubkey(pubkey_hex: str) -> str:
		try:
			return b2x(Hash160(x(pubkey_hex)))
		except Exception:
			# Fallback: SHA256 then RIPEMD160 via hashlib if available
			try:
				import hashlib as _hl
				sha = _hl.sha256(bytes.fromhex(pubkey_hex)).digest()
				rip = _hl.new("ripemd160", sha).hexdigest()
				return rip
			except Exception:
				raise HTTPException(status_code=500, detail="failed to compute pubkey_hash")

	@app.post("/register", response_model=RegisterResponse)
	def register(req: RegisterRequest, reg_repo: RegistrationRepo = Depends(get_reg_repo)) -> RegisterResponse:
		# Require configured registration DOC address
		rego_addr = (config.registration_doc_address or "").strip()
		if not rego_addr:
			raise HTTPException(status_code=500, detail="registration address not configured")
		# Pick random fee
		import random
		min_amt = float(config.registration_amount_min)
		max_amt = float(config.registration_amount_max)
		fee = round(random.uniform(min_amt, max_amt), 3)
		# Map swap uuid -> pubkey and verify provided KMD address
		row = _lookup_swap_pubkeys(req.swap_uuid)
		pubkey = _pubkey_for_kmd_address(req.address, row.get("maker_pubkey"), row.get("taker_pubkey"))
		if not pubkey:
			raise HTTPException(status_code=400, detail="provided address does not match swap pubkeys")
		pubkey_hash_hex = _hash160_hex_from_pubkey(pubkey)
		try:
			reg_repo.create_or_refresh_pending(
				moniker=req.moniker,
				address=req.address,
				pubkey=pubkey,
				pubkey_hash=pubkey_hash_hex,
				rego_fee=fee,
				rego_uuid=req.swap_uuid,
			)
		except ValueError as ve:
			raise HTTPException(status_code=409, detail=str(ve))
		return RegisterResponse(registration_address=rego_addr, registration_amount=fee)

	@app.get("/swap/{uuid}")
	def get_swap(uuid: str, store: SwapStore = Depends(get_store)):
		s = store.get_swap(uuid)
		if not s:
			raise HTTPException(status_code=404, detail="swap not found")
		secret = (config.pubkey_hash_key).encode("utf-8")
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
	def event_details(event_name: str) -> dict:
		# Return group-level details for requested event (group name)
		all_events = app.state.swap_state.store.get_events()  # type: ignore[attr-defined]
		groups: Dict[str, List] = {}
		for ev in all_events:
			g = str(ev.extra.get("group_name") or ev.name)
			groups.setdefault(g, []).append(ev)
		events_for_group = groups.get(str(event_name))
		if not events_for_group:
			return {"error": f"event `{event_name}` not found"}
		# Assume common start/stop across group; compute defensively
		start_ts = min(int(e.start) for e in events_for_group)
		stop_ts = max(int(e.stop) for e in events_for_group)
		first = events_for_group[0]
		extra = dict(first.extra or {})
		# Normalize outgoing payload to grouped schema the frontend expects
		rel_coins = extra.get("rel_coins") or [e.rel_coin for e in events_for_group]
		# Remove internal grouping keys from extra
		extra.pop("group_name", None)
		extra.pop("rel_coins", None)
		return {
			str(event_name): {
				"start": start_ts,
				"stop": stop_ts,
				"base_coin": first.base_coin,
				"rel_coins": rel_coins,
				**extra,
			}
		}

	@app.get("/events")
	def events(filter: Optional[str] = Query(None, description="Optional status filter: complete | active | upcoming")) -> dict:
		# Return GROUP event names, optionally filtered by status relative to current time
		all_events = app.state.swap_state.store.get_events()  # type: ignore[attr-defined]
		groups: Dict[str, List] = {}
		for ev in all_events:
			g = str(ev.extra.get("group_name") or ev.name)
			groups.setdefault(g, []).append(ev)
		now_ts = int(time.time())
		def _group_window(name: str) -> tuple:
			lst = groups[name]
			return (min(int(e.start) for e in lst), max(int(e.stop) for e in lst))
		group_names = list(groups.keys())
		if filter:
			flt = str(filter).lower().strip()
			if flt not in {"complete", "active", "upcoming"}:
				raise HTTPException(status_code=400, detail="invalid filter; must be one of: complete, active, upcoming")
			def _include(name: str) -> bool:
				start_ts, stop_ts = _group_window(name)
				if flt == "complete":
					return stop_ts < now_ts
				if flt == "active":
					return start_ts <= now_ts <= stop_ts
				return start_ts > now_ts  # upcoming
			group_names = [n for n in group_names if _include(n)]
		return JSONResponse(group_names)

	# /hash_pubkey?pubkey=...
	@app.get("/hash_pubkey")
	def hash_pubkey(pubkey: str = Query(...)) -> dict:
		secret = (config.pubkey_hash_key).encode("utf-8")
		if not secret:
			return {"pubkey_hash": hashlib.sha256(pubkey.encode("utf-8")).hexdigest()}
		return {"pubkey_hash": hmac.new(secret, pubkey.encode("utf-8"), hashlib.sha256).hexdigest()}

	# /identify?uuid=...&ticker=...
	@app.get("/identify")
	def identify(uuid: str = Query(...), ticker: str = Query(...), store: SwapStore = Depends(get_store)) -> dict:
		s = store.get_swap(uuid)
		if not s:
			raise HTTPException(status_code=404, detail="swap not found")
		target = str(ticker).upper()
		secret = (config.pubkey_hash_key).encode("utf-8")
		def _hash_pubkey(value: Optional[str]) -> str:
			if not value:
				raise HTTPException(status_code=404, detail="pubkey not found for ticker")
			if not secret:
				return hashlib.sha256(value.encode("utf-8")).hexdigest()
			return hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()
		if s.maker_coin_ticker and s.maker_coin_ticker.upper() == target:
			return {"pubkey_hash": _hash_pubkey(s.maker_pubkey)}
		if s.taker_coin_ticker and s.taker_coin_ticker.upper() == target:
			return {"pubkey_hash": _hash_pubkey(s.taker_pubkey)}
		raise HTTPException(status_code=400, detail="ticker not part of swap")

	# /traders?event_name=...&limit=50&offset=0&search=
	@app.get("/traders")
	def traders(
		event_name: str = Query(..., description="Group name or comma-separated list of group names"),
		limit: int = Query(50, ge=1, le=500),
		offset: int = Query(0, ge=0),
		search: Optional[str] = Query(None),
		verbose: bool = Query(True, description="Verbose output"),
	):
		logger.info(f"Traders request: {event_name} {limit} {offset} {search}")
		all_events = app.state.swap_state.store.get_events()  # type: ignore[attr-defined]
		# Build group -> events mapping
		groups: Dict[str, List] = {}
		for ev in all_events:
			g = str(ev.extra.get("group_name") or ev.name)
			groups.setdefault(g, []).append(ev)
		requested_groups = [n.strip() for n in str(event_name).split(",") if n.strip()]
		selected: List = []
		missing: List[str] = []
		for g in requested_groups:
			lst = groups.get(g)
			if not lst:
				missing.append(g)
				continue
			selected.extend(lst)
		if not selected:
			return {"error": f"event `{event_name}` not found"}
		if missing:
			return {"error": f"event `{','.join(missing)}` not found"}

		secret = (config.pubkey_hash_key).encode("utf-8")
		def _hash_pubkey(value: Optional[str]) -> Optional[str]:
			if not value:
				return None
			if not secret:
				return hashlib.sha256(value.encode("utf-8")).hexdigest()
			return hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()

		# Build per-pubkey totals and per-pair breakdowns
		per_trader: dict = {}
		# Track the full set of selected pairs so we can template missing ones later
		all_pair_keys = {}
		for ev in selected:
			pair_key = f"{ev.base_coin}/{ev.rel_coin}"
			all_pair_keys[pair_key] = (ev.base_coin, ev.rel_coin)
			rows = store.aggregate_trader_metrics(ev, ev.start, ev.stop, price_cache, pubkey_search=search)
			for r in rows:
				pk = r.get("pubkey")
				if not pk:
					continue
				rec = per_trader.setdefault(pk, {
					"pubkey": pk,
					"trades_as_maker": 0,
					"trades_as_taker": 0,
					"trades_total": 0,
					"last_finished_at": 0,
					"usd_total_value": 0.0,
					"pairs": {},
				})
				# Update root totals
				rec["trades_as_maker"] += int(r.get("trades_as_maker") or 0)
				rec["trades_as_taker"] += int(r.get("trades_as_taker") or 0)
				rec["trades_total"] += int(r.get("trades_total") or 0)
				rec["last_finished_at"] = max(int(rec.get("last_finished_at") or 0), int(r.get("last_finished_at") or 0))
				rec["usd_total_value"] += float(r.get("usd_total_value") or 0.0)
				# Update per-pair breakdown (aggregate if same pair appears in multiple events)
				p = rec["pairs"].setdefault(pair_key, {
					"event_base_coin": ev.base_coin,
					"event_rel_coin": ev.rel_coin,
					"base_coin_volume": 0.0,
					"rel_coin_volume": 0.0,
					"usd_base_value": 0.0,
					"usd_rel_value": 0.0,
					"usd_total_value": 0.0,
					"trades_as_maker": 0,
					"trades_as_taker": 0,
					"trades_total": 0,
					"last_finished_at": 0,
				})
				p["base_coin_volume"] += float(r.get("base_coin_volume") or 0.0)
				p["rel_coin_volume"] += float(r.get("rel_coin_volume") or 0.0)
				p["usd_base_value"] += float(r.get("usd_base_value") or 0.0)
				p["usd_rel_value"] += float(r.get("usd_rel_value") or 0.0)
				p["usd_total_value"] += float(r.get("usd_total_value") or 0.0)
				p["trades_as_maker"] += int(r.get("trades_as_maker") or 0)
				p["trades_as_taker"] += int(r.get("trades_as_taker") or 0)
				p["trades_total"] += int(r.get("trades_total") or 0)
				p["last_finished_at"] = max(int(p.get("last_finished_at") or 0), int(r.get("last_finished_at") or 0))

		# Template missing pairs for each trader with zeroed stats and cached prices
		for rec in per_trader.values():
			pairs_map = rec["pairs"]
			for pair_key, (b_coin, r_coin) in all_pair_keys.items():
				if pair_key in pairs_map:
					# If existing entry has no derived prices, fill with cache values
					entry = pairs_map[pair_key]
					if entry.get("usd_base_price") is None:
						entry["usd_base_price"] = price_cache.get_price_usd(b_coin)
					if entry.get("usd_rel_price") is None:
						entry["usd_rel_price"] = price_cache.get_price_usd(r_coin)
					continue
				pairs_map[pair_key] = {
					"event_base_coin": b_coin,
					"event_rel_coin": r_coin,
					"base_coin_volume": 0.0,
					"rel_coin_volume": 0.0,
					"usd_base_value": 0.0,
					"usd_rel_value": 0.0,
					"usd_total_value": 0.0,
					"trades_as_maker": 0,
					"trades_as_taker": 0,
					"trades_total": 0,
					"last_finished_at": None,
					"usd_base_price": price_cache.get_price_usd(b_coin),
					"usd_rel_price": price_cache.get_price_usd(r_coin),
				}

		# Finalize: compute ranks and pubkey hashes; compute per-pair avg prices
		rows = []
		for pk, rec in per_trader.items():
			# Compute derived per-pair prices
			pairs_detail = {}
			for k, v in rec["pairs"].items():
				base_vol = float(v.get("base_coin_volume") or 0.0)
				rel_vol = float(v.get("rel_coin_volume") or 0.0)
				base_val = float(v.get("usd_base_value") or 0.0)
				rel_val = float(v.get("usd_rel_value") or 0.0)
				# Prefer derived average; if no volume, fall back to existing entry value or cache
				b_coin = v.get("event_base_coin")
				r_coin = v.get("event_rel_coin")
				usd_base_price = (base_val / base_vol) if base_vol else (v.get("usd_base_price") if v.get("usd_base_price") is not None else (price_cache.get_price_usd(b_coin) if b_coin else None))
				usd_rel_price = (rel_val / rel_vol) if rel_vol else (v.get("usd_rel_price") if v.get("usd_rel_price") is not None else (price_cache.get_price_usd(r_coin) if r_coin else None))
				pairs_detail[k] = {
					**v,
					"usd_base_price": usd_base_price,
					"usd_rel_price": usd_rel_price,
					"usd_total_value": round(v.get("usd_total_value") or 0.0, 2),
					"usd_base_value": round(v.get("usd_base_value") or 0.0, 2),
					"usd_rel_value": round(v.get("usd_rel_value") or 0.0, 2),
				}
			if verbose:
				pairs_out = pairs_detail
			else:
				pairs_out = [k for k in pairs_detail.keys()]
			rec["usd_total_value"] = round(rec["usd_total_value"], 2)
			rows.append({
				"pubkey": pk,
				"trades_as_maker": rec.get("trades_as_maker"),
				"trades_as_taker": rec.get("trades_as_taker"),
				"trades_total": rec.get("trades_total"),
				"last_finished_at": rec.get("last_finished_at"),
				"usd_total_value": rec.get("usd_total_value"),
				"pairs": pairs_out,
			})

		# Rank by combined USD total value
		sorted_for_rank = sorted(rows, key=lambda r: float(r.get("usd_total_value") or 0.0), reverse=True)
		pubkey_to_rank = {r.get("pubkey"): idx + 1 for idx, r in enumerate(sorted_for_rank)}
		for r in rows:
			r["rank"] = pubkey_to_rank.get(r.get("pubkey"))
		# Sort and paginate
		rows.sort(key=lambda r: int(r.get("rank") or 0))
		sliced = rows[offset:offset+limit]

		# Hash pubkeys and remove raw pubkey field
		annotated = []
		for r in sliced:
			pubkey = r.get("pubkey")
			h = _hash_pubkey(pubkey)
			out = {k: v for k, v in r.items() if k != "pubkey"}
			out.update({"pubkey_hash": h})
			annotated.append(out)
		return annotated

	# /trader_swaps?event_name=...&pubkey=...&limit=50&offset=0&search=
	@app.get("/trader_swaps")
	def trader_swaps(event_name: str = Query(..., description="Group name or comma-separated list of group names"), pubkey: Optional[str] = Query(None), limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0), search: Optional[str] = Query(None)):
		all_events = app.state.swap_state.store.get_events()  # type: ignore[attr-defined]
		groups: Dict[str, List] = {}
		for ev in all_events:
			g = str(ev.extra.get("group_name") or ev.name)
			groups.setdefault(g, []).append(ev)
		requested_groups = [n.strip() for n in str(event_name).split(",") if n.strip()]
		selected: List = []
		missing: List[str] = []
		for g in requested_groups:
			lst = groups.get(g)
			if not lst:
				missing.append(g)
				continue
			selected.extend(lst)
		if not selected:
			return {"error": f"event `{event_name}` not found"}
		if missing:
			return {"error": f"event `{','.join(missing)}` not found"}

		secret = (config.pubkey_hash_key).encode("utf-8")
		def _hash_pubkey(value: Optional[str]) -> Optional[str]:
			if not value:
				return None
			if not secret:
				return hashlib.sha256(value.encode("utf-8")).hexdigest()
			return hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()

		def _matches_swap_pubkey_field(s_pub: Optional[str], needle_raw_or_hash: str) -> bool:
			if not s_pub:
				return False
			return s_pub == needle_raw_or_hash or _hash_pubkey(s_pub) == needle_raw_or_hash

		def _row_for_event(s: Swap, ev) -> dict:
			b_price = price_cache.get_price_usd(ev.base_coin)
			r_price = price_cache.get_price_usd(ev.rel_coin)
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
			maker_hash = _hash_pubkey(s.maker_pubkey)
			taker_hash = _hash_pubkey(s.taker_pubkey)
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
				"pair": f"{ev.base_coin}/{ev.rel_coin}",
				"event_name": ev.name,
			})
			return payload

		# Single event: preserve original behavior
		if len(selected) == 1:
			ev = selected[0]
			swaps = store.swaps_for_event_pair(ev, ev.start, ev.stop)
			if search:
				needle = search.lower()
				swaps = [s for s in swaps if (s.maker_pubkey and needle in s.maker_pubkey.lower()) or (s.taker_pubkey and needle in s.taker_pubkey.lower())]
			if pubkey:
				needle_raw_or_hash = str(pubkey)
				swaps = [s for s in swaps if _matches_swap_pubkey_field(s.maker_pubkey, needle_raw_or_hash) or _matches_swap_pubkey_field(s.taker_pubkey, needle_raw_or_hash)]
			swaps_sorted = sorted(swaps, key=lambda s: int(s.finished_at or 0), reverse=True)
			rows = [_row_for_event(s, ev) for s in swaps_sorted]
			return rows[offset:offset+limit]

		# Multiple events: gather, annotate with originating event, dedupe by uuid
		rows_all: dict = {}
		needle_lower = search.lower() if search else None
		needle_raw_or_hash = str(pubkey) if pubkey else None
		for ev in selected:
			swaps = store.swaps_for_event_pair(ev, ev.start, ev.stop)
			if needle_lower:
				swaps = [s for s in swaps if (s.maker_pubkey and needle_lower in s.maker_pubkey.lower()) or (s.taker_pubkey and needle_lower in s.taker_pubkey.lower())]
			if needle_raw_or_hash is not None:
				swaps = [s for s in swaps if _matches_swap_pubkey_field(s.maker_pubkey, needle_raw_or_hash) or _matches_swap_pubkey_field(s.taker_pubkey, needle_raw_or_hash)]
			for s in swaps:
				row = _row_for_event(s, ev)
				if s.uuid not in rows_all:
					rows_all[s.uuid] = row
				else:
					# Keep the one with the latest finished_at
					prev = rows_all[s.uuid]
					prev_ts = int(prev.get("finished_at") or 0)
					cur_ts = int(row.get("finished_at") or 0)
					if cur_ts > prev_ts:
						rows_all[s.uuid] = row
		rows_list = list(rows_all.values())
		rows_sorted = sorted(rows_list, key=lambda r: int(r.get("finished_at") or 0), reverse=True)
		return rows_sorted[offset:offset+limit]

	# Removed legacy stats endpoints; new event-based endpoints will be added below

	# Legacy websocket removed with totals endpoint

	# Background watcher for registrations
	async def _registration_watcher() -> None:
		poll_seconds = max(10, int(config.registration_poll_seconds))
		expiry_seconds = max(60, int(config.registration_expiry_hours) * 3600)
		rego_addr = (config.registration_doc_address or "").strip()
		while True:
			try:
				# Skip if not configured
				if not rego_addr:
					await asyncio.sleep(poll_seconds)
					continue
				# Expire old
				try:
					expired = reg_repo.expire_old(expiry_seconds)
					if expired:
						logger.info(f"Expired {expired} pending registrations")
				except Exception as e:
					logger.error(f"Expire old registrations failed: {e}")
				# Process pendings
				pending = reg_repo.list_pending()
				for ru in pending:
					# Derive candidate DOC address from pubkey
					doc_from_addr = calc_addr_from_pubkey("DOC", ru.pubkey)
					if isinstance(doc_from_addr, dict) and doc_from_addr.get("error"):
						logger.error(f"Address derivation failed for {ru.address}: {doc_from_addr}")
						continue
					try:
						resp = insight.addresses_transactions(addresses=rego_addr, from_=doc_from_addr)
					except Exception as e:
						logger.error(f"Insight call failed: {e}")
						continue
					try:
						items = (resp or {}).get("items") or []
					except Exception:
						items = []
					# Find any confirmed tx paying exactly the rego_fee (3dp)
					matched_txid: Optional[str] = None
					for it in items:
						confirmations = int(it.get("confirmations") or 0)
						if confirmations <= 0:
							continue
						# Sum outputs to rego_addr
						total_to_rego = 0.0
						for vout in it.get("vout") or []:
							addrs = (vout.get("scriptPubKey") or {}).get("addresses") or []
							if rego_addr in addrs:
								try:
									total_to_rego += float(vout.get("value"))
								except Exception:
									pass
						# Compare at 3dp
						if round(total_to_rego, 3) == round(float(ru.rego_fee), 3):
							matched_txid = str(it.get("txid"))
							break
					if matched_txid:
						try:
							reg_repo.set_registered(address=ru.address, txid=matched_txid)
							logger.info(f"Registered {ru.address} via tx {matched_txid}")
						except Exception as e:
							logger.error(f"Failed to mark registered for {ru.address}: {e}")
			except Exception as outer:
				logger.error(f"Registration watcher error: {outer}")
			await asyncio.sleep(poll_seconds)

	return app

app = create_app()


