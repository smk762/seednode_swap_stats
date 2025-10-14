from __future__ import annotations

import os
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseSettings, Field, validator


class AppConfig(BaseSettings):
	# Paths
	kdf_db_path: str = Field(
		"/home/komodian/.kdf/DB/a8768e7ff55c6c5041bf79d06b74aeed1bb7aa91/MM2.db",
		env="KDF_DB_PATH",
	)
	events_json_path: str = Field("events.json", env="EVENTS_JSON_PATH")

	# Behavior
	kdf_load_history: bool = Field(True, env="KDF_LOAD_HISTORY")
	retention_hours: int = Field(1, env="RETENTION_HOURS")
	backfill_since: Optional[int] = Field(None, env="BACKFILL_SINCE")

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


