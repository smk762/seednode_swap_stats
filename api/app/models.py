from __future__ import annotations

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class Swap(BaseModel):
	# Core identifiers
	id: int
	uuid: str

	# Coins
	maker_coin: str
	taker_coin: str
	maker_coin_ticker: Optional[str] = None
	maker_coin_platform: Optional[str] = None
	taker_coin_ticker: Optional[str] = None
	taker_coin_platform: Optional[str] = None

	# Timing
	started_at: Optional[int] = None
	finished_at: Optional[int] = None

	# Amounts and prices
	maker_amount: Decimal = Field(..., description="Amount of maker coin in base units")
	taker_amount: Decimal = Field(..., description="Amount of taker coin in base units")
	maker_coin_usd_price: Optional[Decimal] = None
	taker_coin_usd_price: Optional[Decimal] = None

	# Outcome and metadata
	is_success: Optional[bool] = None
	maker_pubkey: Optional[str] = None
	taker_pubkey: Optional[str] = None
	maker_gui: Optional[str] = None
	taker_gui: Optional[str] = None
	maker_version: Optional[str] = None
	taker_version: Optional[str] = None

	class Config:
		json_encoders = {
			Decimal: lambda v: format(v, 'f')
		}


class TotalCount(BaseModel):
	total: int


