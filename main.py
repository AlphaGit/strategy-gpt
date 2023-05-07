from utils.logger import logger
from strategy import strategy, parameters, metrics
from evaluator import evaluate_metrics
from ideator import get_hypotheses
from code_executor import is_hypothesis_true

logger.setLevel("DEBUG")
logger.info(f"Strategy:\n{strategy}")

# --------------------
# logger.info(f"Evaluating metrics:\n{metrics}")
# to_improve_metrics = evaluate_metrics(metrics)

# if len(to_improve_metrics) == 0:
#     logger.info("All metrics are acceptable.")
#     exit(0)
# else:
#     to_improve_metrics_to_print = "\n- " + "\n- ".join(to_improve_metrics)
#     logger.info(f'Metrics to improve:{to_improve_metrics_to_print}')
# --------------------

to_improve_metrics = ["Sharpe Ratio"]
metric_value: float = 0.5

for metric in to_improve_metrics:
    logger.info(f"Creating hypothesis to improve the {metric} metric.")

    hypotheses = get_hypotheses(strategy, parameters, metric)
    hypotheses_to_print = "\n- " + "\n- ".join(hypotheses)
    logger.info(f'Hypotheses:{hypotheses_to_print}')

    # for hypothesis in hypotheses:
    for hypothesis in hypotheses[:1]:
        logger.info(f"Evaluating hypothesis: {hypothesis}")

        is_true = is_hypothesis_true(hypothesis, metric_value)
        logger.info(f"Hypothesis is {is_true}. ({hypothesis})")
