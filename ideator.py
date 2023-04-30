from utils.openai_client import get_completion
from utils.logger import logger

def get_hypotheses(strategy, parameters, metric) -> list[str]:
    prompt = f"""You are an expert financial investor. Your objective is to improve the {metric} of a strategy.

See the strategy description:

{strategy}

See the folowing parameters:

{parameters}

Write testable ideas to change the parameters to improve the {metric}, in the following format:

- A bigger position size will improve the {metric}.
- Allowing a maximum risk of loss of 50% will improve the {metric}.
- Add a filter to only enter when the 14-day RSI is below to improve the {metric}."""

    hypotheses = get_completion(prompt)
    hypotheses = hypotheses.split("\n")
    hypotheses = [hypothesis.replace("- ", "", 1) for hypothesis in hypotheses]
    hypotheses = [hypothesis.strip() for hypothesis in hypotheses]

    hypotheses_to_print = "\n- " + "\n- ".join(hypotheses)
    logger.debug(f'Hypotheses:{hypotheses_to_print}')

    return hypotheses
