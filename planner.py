from utils.openai_client import get_completion
from utils.logger import logger

def plan_hypothesis_testing(strategy, parameters, hypothesis):
    prompt = f"""Given the following strategy:

{strategy}

With the following parameters:

{parameters}

Come up with the list of steps that need to be done to test the hypothesis: "{hypothesis}". Write it as a list of steps, one per line, without numbering."""

    steps = get_completion(prompt)
    steps = [l.replace("- ", "") for l in steps.splitlines()]

    logger.info(f"Steps to execute:\n{steps}")

    system_parts = [
        "data_retrieval",
        "strategy",
        "metric_calculation",
        "backtesting",
    ]
    system_parts_string = "\n".join([f"- {p}" for p in system_parts])

    for step in steps:
        prompt = f"""In order to validate this hypothesis: "{hypothesis}", you need to do the following step: "{step}". Which of the following system modules should be modified to execute this step?

{system_parts_string}
        """
        module_to_modify = get_completion(prompt)
        logger.info(f"Step: {step}\nModule to modify: {module_to_modify}")

    return steps

if __name__ == '__main__':
    logger.setLevel("DEBUG")
    from strategy import strategy, parameters
    plan_hypothesis_testing(strategy, parameters, "Decreasing the volatility range for entries to 5% to 15% will improve the Sharpe Ratio.")
    logger.info("Done.")

