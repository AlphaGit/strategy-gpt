from typing import List
from dataclasses import dataclass
import yfinance
import os
import pandas as pd

@dataclass
class Candle:
    timestamp: float
    open: float
    close: float
    high: float
    low: float
    volume: float

def get_history() -> List[Candle]:
    file_name = "cache/VXX.ftr"
    if os.path.isfile(file_name):
        candles = pd.read_feather(file_name)
    else:
        candles = yfinance.download("VXX", period="1y", interval="1d")
        candles.reset_index(inplace=True)
        candles.rename(columns={"Date": "timestamp", "Datetime": "timestamp"}, inplace=True)
        candles["timestamp"] = candles["timestamp"].astype("int64") / 1e9
        candles.to_feather(file_name)

    candles = [
        Candle(
            timestamp=candle["timestamp"],
            open=candle["Open"],
            close=candle["Close"],
            high=candle["High"],
            low=candle["Low"],
            volume=candle["Volume"],
        )
        for candle in candles.to_dict(orient="records")
    ]
    return candles
