from utils.openai_client import get_completion
from utils.logger import logger

def evaluate_metrics(metrics) -> list[str]:
    prompt = f"""You are an expert financial investor. Your objective is to evaluate the metrics of a strategy.

Metrics:

{metrics}

These are the acceptable range for metrics:

- Sharpe Ratio: 1.0 or higher
- Win Ratio: 0.5 or higher
- Profit Factor: 1.0 or higher
- Max Drawdown: 0.3 or lower

List the metrics that need to be improved. If all of the metrics are acceptable, write "None". Do not include the metric value. Use a format like this:

- Sharpe Ratio
- Win Ratio
"""

    to_improve_metrics = get_completion(prompt)
    to_improve_metrics = to_improve_metrics.split("\n")
    to_improve_metrics = [metric.replace("- ", "", 1) for metric in to_improve_metrics]
    to_improve_metrics = [metric.strip() for metric in to_improve_metrics]
    to_improve_metrics = [metric for metric in to_improve_metrics if "None" not in metric]

    if len(to_improve_metrics) == 0:
        logger.debug("All metrics are acceptable.")
    else:
        to_improve_metrics_to_print = "\n- " + "\n- ".join(to_improve_metrics)
        logger.debug(f'Metrics to improve:{to_improve_metrics_to_print}')

    return to_improve_metrics
