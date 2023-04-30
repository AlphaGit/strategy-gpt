import openai
import os
from typing import List
from dataclasses import dataclass
import numpy as np
import pandas as pd
import yfinance

@dataclass
class Candle:
    open: float
    close: float
    high: float
    low: float
    volume: float

action = "check if the RSI indicator is above 70.0 for any point"

init_code = """
@dataclass
class Candle:
    open: float
    close: float
    high: float
    low: float
    volume: float

def run(candles: List[Candle]) -> bool:"""

prompt = f"""You are an expert Python coder. Code a function that has the following signature and {action}:

Code snippet:

{init_code}"""

openai.api_key = os.getenv("OPENAI_API_KEY")

response = openai.Completion.create(
    engine="text-davinci-003",
    prompt=prompt,
    max_tokens=512,
)

result = None

full_code = f"""{init_code}
{response["choices"][0]["text"]}

result = run(candles)
"""

# check if cache file exists
if os.path.isfile("VXX.ftr"):
    # load VXX from cache
    candles = pd.read_feather("VXX.ftr")
else:
    # load VXX from Yahoo Finance using yfinance
    candles = yfinance.download("VXX", period="1y", interval="1d")
    # save VXX to cache
    candles.to_feather("VXX.ftr")

# transform them into Candle objects
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

print(full_code)
print('')

exec(full_code)

print(f' -> {result}')