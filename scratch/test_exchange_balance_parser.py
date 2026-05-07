from __future__ import annotations

from core.exchange import OKXExchange


def test_okx_raw_details_balance_fallback_when_ccxt_maps_are_empty():
    exchange = object.__new__(OKXExchange)
    balance = {
        "free": {},
        "used": {},
        "total": {},
        "info": {
            "data": [
                {
                    "details": [
                        {
                            "ccy": "XRP",
                            "eq": "2.296559282",
                            "availBal": "2.296559282",
                            "frozenBal": "0",
                        }
                    ]
                }
            ]
        },
    }

    assert exchange.get_asset_total("XRP", balance=balance) == 2.296559282
    assert exchange.get_asset_free("XRP", balance=balance) == 2.296559282
