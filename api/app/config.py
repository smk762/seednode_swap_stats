from __future__ import annotations

import os
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseSettings, Field, validator


class AppConfig(BaseSettings):
	# Paths
	kdf_db_path: str = Field(
		"/home/komodian/.kdf/DB/9640aa8c78c8f605e990cebcf1d9d4c015bc45e6/MM2.db",
		env="KDF_DB_PATH",
	)
	events_json_path: str = Field("events.json", env="EVENTS_JSON_PATH")

	# Behavior
	kdf_load_history: bool = Field(True, env="KDF_LOAD_HISTORY")
	retention_hours: int = Field(1, env="RETENTION_HOURS")
	backfill_since: Optional[int] = Field(None, env="BACKFILL_SINCE")
	# Privacy/security
	pubkey_hash_key: str = Field("komodian", env="PUBKEY_HASH_KEY")
	# DOC Insight/API + registration
	doc_insight_base_url: str = Field("https://doc.explorer.dexstats.info", env="DOC_INSIGHT_BASE_URL")
	doc_insight_api_path: str = Field("insight-api-komodo", env="DOC_INSIGHT_API_PATH")
	registration_doc_address: str = Field("RGzkzaZcRySBYq4jStV6iVtccztLh51WRt", env="REGISTRATION_DOC_ADDRESS")
	registration_poll_seconds: int = Field(180, env="REGISTRATION_POLL_SECONDS")
	registration_expiry_hours: int = Field(24, env="REGISTRATION_EXPIRY_HOURS")
	registration_amount_min: float = Field(0.001, env="REGISTRATION_AMOUNT_MIN")
	registration_amount_max: float = Field(3.33, env="REGISTRATION_AMOUNT_MAX")
	registration_db_path: str = Field("/app/app/DEX_COMP.db", env="REGISTRATION_DB_PATH")

	@validator("retention_hours", pre=True)
	def _non_negative_hours(cls, v):  # type: ignore
		try:
			iv = int(v)
		except Exception:
			return 1
		return max(0, iv)

	@classmethod
	def load(cls) -> "AppConfig":
		# Load .env optionally from ENV_FILE override
		env_file = os.environ.get("ENV_FILE")
		if env_file:
			load_dotenv(env_file)
		else:
			load_dotenv()
		return cls()


