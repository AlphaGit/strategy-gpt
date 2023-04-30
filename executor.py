import numpy as np
import pandas as pd

from utils.data import get_history
from utils.openai_client import get_completion

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

prompt = f"""You are an expert Python coder for a trading app. Code a function that has the following signature and {action}:

Code snippet:

{init_code}"""

completed_code = get_completion(prompt)

full_code = f"""{init_code}
{completed_code}

result = run(candles)
"""

print(full_code)
print('')

candles = get_history()
result = None
exec(full_code)
print(f' -> {result}')