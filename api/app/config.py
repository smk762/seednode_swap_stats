from __future__ import annotations

import os
from typing import Optional

from dotenv import load_dotenv
from pydantic import Field
from pydantic import field_validator
from pydantic_settings import BaseSettings


class AppConfig(BaseSettings):
	# Paths
	kdf_db_path: str = Field(
		"/home/komodian/.kdf/DB/9640aa8c78c8f605e990cebcf1d9d4c015bc45e6/MM2.db",
		validation_alias="KDF_DB_PATH",
	)
	events_json_path: str = Field("events.json", validation_alias="EVENTS_JSON_PATH")

	# Behavior
	kdf_load_history: bool = Field(True, validation_alias="KDF_LOAD_HISTORY")
	retention_hours: int = Field(1, validation_alias="RETENTION_HOURS")
	backfill_since: Optional[int] = Field(None, validation_alias="BACKFILL_SINCE")
	# Privacy/security
	pubkey_hash_key: str = Field("komodian", validation_alias="PUBKEY_HASH_KEY")
	# DOC Insight/API + registration
	doc_insight_base_url: str = Field("https://doc.explorer.dexstats.info", validation_alias="DOC_INSIGHT_BASE_URL")
	doc_insight_api_path: str = Field("insight-api-komodo", validation_alias="DOC_INSIGHT_API_PATH")
	registration_doc_address: str = Field("RGzkzaZcRySBYq4jStV6iVtccztLh51WRt", validation_alias="REGISTRATION_DOC_ADDRESS")
	registration_poll_seconds: int = Field(180, validation_alias="REGISTRATION_POLL_SECONDS")
	registration_expiry_hours: int = Field(24, validation_alias="REGISTRATION_EXPIRY_HOURS")
	registration_amount_min: float = Field(0.001, validation_alias="REGISTRATION_AMOUNT_MIN")
	registration_amount_max: float = Field(3.33, validation_alias="REGISTRATION_AMOUNT_MAX")
	registration_db_path: str = Field("/app/app/DEX_COMP.db", validation_alias="REGISTRATION_DB_PATH")

	@field_validator("retention_hours", mode="before")
	@classmethod
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


