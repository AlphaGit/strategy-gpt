from typing import List
from dataclasses import dataclass
import yfinance
import os
import pandas as pd

@dataclass
class Candle:
    open: float
    close: float
    high: float
    low: float
    volume: float

def get_history() -> List[Candle]:
    if os.path.isfile("VXX.ftr"):
        candles = pd.read_feather("VXX.ftr")
    else:
        candles = yfinance.download("VXX", period="1y", interval="1d")
        candles.to_feather("VXX.ftr")

    candles = [
        Candle(
            open=candle["Open"],
            close=candle["Close"],
            high=candle["High"],
            low=candle["Low"],
            volume=candle["Volume"],
        )
        for candle in candles.to_dict(orient="records")
    ]
    return candles
