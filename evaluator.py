from strategy import strategy, metrics
from utils.openai_client import get_completion

prompt = f"""You are an expert financial investor. Your objective is to evaluate the metrics of a strategy.

See the strategy description:

{strategy}

See the metrics:

{metrics}

List the metrics that are not acceptable and need to be improved. If all of the metrics are acceptable, write "None". Do not include the metric value. Use a format like this:

- Sharpe Ratio
- Win Ratio
"""

to_improve_metrics = get_completion(prompt)
to_improve_metrics = to_improve_metrics.split("\n")
to_improve_metrics = [metric.replace("- ", "", 1) for metric in to_improve_metrics]
to_improve_metrics = [metric.strip() for metric in to_improve_metrics]
to_improve_metrics = [metric for metric in to_improve_metrics if metric != "None"]

if len(to_improve_metrics) == 0:
    print("All metrics are acceptable.")
    exit(0)
else:
    print("Metrics to improve:")
    for metric in to_improve_metrics:
        print("- ", metric)

