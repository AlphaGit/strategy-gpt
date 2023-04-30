import openai
import os

strategy = """
This is a strategy that detects ranges on the volatility of the VXX ETF.
It shorts the action when possible, and keeps the position open until the relative profit is below 10%.
It never shorts so much that the risk of loss goes beyond 25%.
It hedges using treasury bonds for the unused capital.
"""

metrics = """
- Sharpe ratio: 1.5.
- Sortino ratio: 2.0.
- Trade length: 10 days.
- Maximum drawdown: 10%.
- Annualized return: 9%.
- Profit factor: 1.09.
- Win ratio: 55%.
"""

parameters = """
- Volatility range for entries: 10% to 20%.
- Relative profit for exits: 10%.
- Maximum risk of loss: 25%.
- Hedging instrument: US treasury bonds.
- Hedging ratio: 1:5.
"""

prompt = f"""You are an expert financial investor. Your objective is to improve the metrics of a strategy.

See the strategy description:

{strategy}

See the metrics:

{metrics}

List the metrics that are not acceptable and need to be improved. Do not include the metric value. Use a format like this:

- Sharpe Ratio
- Win Ratio"""

openai.api_key = os.getenv("OPENAI_API_KEY")

response = openai.ChatCompletion.create(
    model="gpt-3.5-turbo",
    messages=[{
        "role": "user",
        "content": prompt,
    }],
    max_tokens=512,
)

to_improve_metrics = response["choices"][0]["message"]["content"]
to_improve_metrics = to_improve_metrics.split("\n")
to_improve_metrics = [metric.replace("- ", "", 1) for metric in to_improve_metrics]
to_improve_metrics = [metric.strip() for metric in to_improve_metrics]

print("Metrics to improve:")
for metric in to_improve_metrics:
    print("- ", metric)

# prompt = f"""You are an expert financial investor. Your objective is to improve the metrics of a strategy.

# See the strategy description:

# {strategy}

# See the folowing parameters:

# {parameters}

# 10 testable hypotheses for parameters to improve the {to_improve_metrics[0]} metric:

# - """

# hypotheses = f'- {response["choices"][0]["text"]}'
# hypotheses = hypotheses.split("\n")
# hypotheses = [hypothesis.replace("- ", "", 1) for hypothesis in hypotheses]
# hypotheses = [hypothesis.strip() for hypothesis in hypotheses]

# for hypothesis in hypotheses:
#     print("- ", hypothesis)

