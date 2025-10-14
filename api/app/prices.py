from __future__ import annotations

import json
import os
import threading
import time
from typing import Dict, Optional, Set

import requests


class CoinConfig:
	"""Maps coin symbols to CoinGecko IDs."""

	def __init__(self, mapping: Dict[str, str]) -> None:
		self._symbol_to_id = {k.upper(): v for k, v in mapping.items() if v}

	@classmethod
	def load(cls) -> "CoinConfig":
		# Prefer local path if provided; else fetch from Komodo coins repo raw URL
		local_path = os.environ.get("COIN_CONFIG_PATH")
		if local_path and os.path.exists(local_path):
			with open(local_path, "r") as f:
				data = json.load(f)
			return cls(_extract_symbol_to_id(data))
		url = os.environ.get(
			"COIN_CONFIG_URL",
			"https://raw.githubusercontent.com/KomodoPlatform/coins/master/utils/coins_config.json",
		)
		try:
			resp = requests.get(url, timeout=15)
			resp.raise_for_status()
			data = resp.json()
			return cls(_extract_symbol_to_id(data))
		except Exception:
			return cls({})

	def get_coingecko_id(self, symbol: str) -> Optional[str]:
		return self._symbol_to_id.get(symbol.upper())


def _extract_symbol_to_id(data: dict) -> Dict[str, str]:
	result: Dict[str, str] = {}
	for sym, cfg in data.items():
		cid = cfg.get("coingecko_id") or cfg.get("coingecko")
		if cid:
			result[sym.upper()] = cid
	return result


class PriceCache:
	"""Caches USD prices from CoinGecko and refreshes periodically."""

	def __init__(self, coin_config: CoinConfig, refresh_seconds: int = 600) -> None:
		self._config = coin_config
		self._refresh_seconds = refresh_seconds
		self._lock = threading.RLock()
		self._symbol_prices: Dict[str, float] = {}
		self._symbols_needed: Set[str] = set()
		self._stop = threading.Event()
		self._thread: Optional[threading.Thread] = None

	def start(self) -> None:
		if self._thread and self._thread.is_alive():
			return
		self._stop.clear()
		self._thread = threading.Thread(target=self._run, name="price-cache", daemon=True)
		self._thread.start()

	def stop(self) -> None:
		self._stop.set()
		if self._thread and self._thread.is_alive():
			self._thread.join(timeout=5)

	def register_symbols(self, symbols: Set[str]) -> None:
		with self._lock:
			for s in symbols:
				self._symbols_needed.add(s.upper())

	def get_price_usd(self, symbol: str) -> Optional[float]:
		with self._lock:
			if symbol.upper() not in self._symbols_needed:
				self._symbols_needed.add(symbol.upper())
			return self._symbol_prices.get(symbol.upper())

	def _run(self) -> None:
		while not self._stop.is_set():
			try:
				self._refresh_once()
			except Exception:
				pass
			time.sleep(self._refresh_seconds)

	def _refresh_once(self) -> None:
		with self._lock:
			symbols = sorted(self._symbols_needed)
		if not symbols:
			return
		# Map symbols to CoinGecko IDs
		ids: Dict[str, str] = {}
		for sym in symbols:
			cid = self._config.get_coingecko_id(sym)
			if cid:
				ids[sym] = cid
		if not ids:
			return
		query_ids = ",".join(sorted(set(ids.values())))
		url = f"https://api.coingecko.com/api/v3/simple/price?ids={query_ids}&vs_currencies=usd"
		resp = requests.get(url, timeout=15)
		resp.raise_for_status()
		data = resp.json()
		new_prices: Dict[str, float] = {}
		for sym, cid in ids.items():
			price = data.get(cid, {}).get("usd")
			if isinstance(price, (int, float)):
				new_prices[sym.upper()] = float(price)
		with self._lock:
			self._symbol_prices.update(new_prices)


