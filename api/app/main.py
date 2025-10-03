from __future__ import annotations

import os
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from dataclasses import dataclass

from .db_monitor import SQLiteSwapMonitor
from .models import Swap, TotalCount
from .store import SwapStore


@dataclass
class AppState:
	# Simple holder for app state
	store: SwapStore
	monitor: SQLiteSwapMonitor


def create_app() -> FastAPI:
	app = FastAPI(title="Swap Tracker API", version="0.1.0")

	# Determine DB path (check KDF logs, will match "Public key hash" on launch)
	db_path = os.environ.get("KDF_DB_PATH") or \
		"/home/komodian/.kdf/DB/a8768e7ff55c6c5041bf79d06b74aeed1bb7aa91/MM2.db"
	store = SwapStore()
	load_history = (os.environ.get("KDF_LOAD_HISTORY", "true").lower() in ("1", "true", "yes"))
	monitor = SQLiteSwapMonitor(db_path=db_path, callback=lambda s: store.upsert_swap(s), load_history=load_history)
	monitor.start()

	state = AppState(store=store, monitor=monitor)
	app.state.swap_state = state  # type: ignore[attr-defined]

	@app.on_event("shutdown")
	def _on_shutdown() -> None:
		state.monitor.stop()

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

	@app.get("/stats/pair")
	def stats_pair(
		maker_coin: str = Query(..., description="Maker coin symbol"),
		taker_coin: str = Query(..., description="Taker coin symbol"),
		start_ts: int = Query(..., ge=0),
		end_ts: int = Query(..., ge=0),
		store: SwapStore = Depends(get_store),
	):
		if end_ts < start_ts:
			raise HTTPException(status_code=400, detail="end_ts must be >= start_ts")
		return store.stats_for_pair(maker_coin, taker_coin, start_ts, end_ts)

	@app.get("/swaps/total", response_model=TotalCount)
	def total_swaps(store: SwapStore = Depends(get_store)):
		return TotalCount(total=store.total_count())

	@app.websocket("/ws/total")
	async def ws_total(websocket: WebSocket):
		await websocket.accept()
		# Naive push loop with periodic timer to reduce lag without requiring client messages.
		import asyncio
		try:
			last = -1
			while True:
				current = app.state.swap_state.store.total_count()  # type: ignore[attr-defined]
				if current != last:
					await websocket.send_json({"total": current})
					last = current
				await asyncio.sleep(1.0)
		except WebSocketDisconnect:
			return

	return app


app = create_app()


