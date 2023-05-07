# imported so that the exec has access to these
import numpy as np
import pandas as pd

from utils.data import get_history, Candle
from utils.openai_client import get_completion
from utils.logger import logger

def is_hypothesis_true(hypothesis: str, current_metric_value) -> bool:
    init_code = """@dataclass
class Candle:
    timestamp: float
    open: float
    close: float
    high: float
    low: float
    volume: float

def is_idea_true(candles: list[Candle], current_metric_value: float) -> bool:"""

    prompt = f"""You are an expert Python coder for a trading app. Code a function that has the following signature and verifies this idea: "{hypothesis}". Do not include comments or docstrings in your code.

Code snippet:

{init_code}"""

    completed_code = get_completion(prompt, max_tokens=3900)

    full_code = f"""{completed_code}

result = is_idea_true(candles, current_metric_value)
"""

    logger.debug(f"Full code to execute:\n{full_code}")

    candles = get_history()
    result = None
    exec(full_code)
    return result