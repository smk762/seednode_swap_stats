from __future__ import annotations

import hashlib
import hmac
import os
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Swap


def _expect_hash(value: str) -> str:
	secret = (os.environ.get("PUBKEY_HASH_KEY") or "komodian").encode("utf-8")
	if not secret:
		return hashlib.sha256(value.encode("utf-8")).hexdigest()
	return hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()


def _mk_swap(uuid: str, maker_ticker: str, taker_ticker: str, maker_pub: str | None, taker_pub: str | None) -> Swap:
	return Swap(
		id=1,
		uuid=uuid,
		maker_coin=maker_ticker,
		taker_coin=taker_ticker,
		maker_coin_ticker=maker_ticker,
		taker_coin_ticker=taker_ticker,
		started_at=1,
		finished_at=2,
		maker_amount=Decimal("1"),
		taker_amount=Decimal("1"),
		maker_pubkey=maker_pub,
		taker_pubkey=taker_pub,
	)


def test_identify_returns_hash_for_matching_ticker(monkeypatch):
	# Ensure deterministic hashing (no HMAC key)
	monkeypatch.setenv("PUBKEY_HASH_KEY", "komodian")
	# Avoid network in CoinConfig.load
	monkeypatch.setenv("COIN_CONFIG_URL", "http://127.0.0.1:9/")
	# Prevent pruning of old swaps during test
	monkeypatch.setenv("RETENTION_HOURS", "999999")
	app = create_app()
	store = app.state.swap_state.store  # type: ignore[attr-defined]
	# maker=KMD, taker=ARRR
	s = _mk_swap("u-ok", "KMD", "ARRR", maker_pub="makerPK", taker_pub="takerPK")
	assert store.upsert_swap(s) is True
	with TestClient(app) as client:
		r = client.get("/identify", params={"uuid": "u-ok", "ticker": "ARRR"})
		assert r.status_code == 200
		data = r.json()
		assert data == {"pubkey_hash": _expect_hash("takerPK")}



def test_identify_404_when_swap_not_found(monkeypatch):
	monkeypatch.setenv("PUBKEY_HASH_KEY", "komodian")
	monkeypatch.setenv("COIN_CONFIG_URL", "http://127.0.0.1:9/")
	monkeypatch.setenv("RETENTION_HOURS", "999999")
	app = create_app()
	with TestClient(app) as client:
		r = client.get("/identify", params={"uuid": "missing", "ticker": "ARRR"})
		assert r.status_code == 404
		assert r.json().get("detail") == "swap not found"



def test_identify_400_when_ticker_not_in_swap(monkeypatch):
	monkeypatch.setenv("PUBKEY_HASH_KEY", "komodian")
	monkeypatch.setenv("COIN_CONFIG_URL", "http://127.0.0.1:9/")
	monkeypatch.setenv("RETENTION_HOURS", "999999")
	app = create_app()
	store = app.state.swap_state.store  # type: ignore[attr-defined]
	s = _mk_swap("u-bad-ticker", "KMD", "ARRR", maker_pub="makerPK", taker_pub="takerPK")
	store.upsert_swap(s)
	with TestClient(app) as client:
		r = client.get("/identify", params={"uuid": "u-bad-ticker", "ticker": "BTC"})
		assert r.status_code == 400
		assert r.json().get("detail") == "ticker not part of swap"



def test_identify_404_when_matching_pubkey_missing(monkeypatch):
	monkeypatch.setenv("PUBKEY_HASH_KEY", "komodian")
	monkeypatch.setenv("COIN_CONFIG_URL", "http://127.0.0.1:9/")
	monkeypatch.setenv("RETENTION_HOURS", "999999")
	app = create_app()
	store = app.state.swap_state.store  # type: ignore[attr-defined]
	# taker pubkey missing while querying for taker ticker
	s = _mk_swap("u-missing", "KMD", "ARRR", maker_pub="makerPK", taker_pub=None)
	store.upsert_swap(s)
	with TestClient(app) as client:
		r = client.get("/identify", params={"uuid": "u-missing", "ticker": "ARRR"})
		assert r.status_code == 404
		assert r.json().get("detail") == "pubkey not found for ticker"


