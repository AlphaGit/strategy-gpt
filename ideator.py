import openai
import os
from strategy import strategy, parameters

metric = "Win ratio"

print(f"Improving metric: {metric}")

prompt = f"""You are an expert financial investor. Your objective is to improve the {metric} of a strategy.

See the strategy description:

{strategy}

See the folowing parameters:

{parameters}

Write testable ideas to change the parameters to improve the {metric}, in the following format:

- A bigger position size will improve the {metric}.
- Allowing a maximum risk of loss of 50% will improve the {metric}.
- Add a filter to only enter when the 14-day RSI is below to improve the {metric}."""

openai.api_key = os.getenv("OPENAI_API_KEY")

response = openai.ChatCompletion.create(
    model="gpt-3.5-turbo",
    messages=[{
        "role": "user",
        "content": prompt,
    }],
    max_tokens=512,
)

hypotheses = response["choices"][0]["message"]["content"]
hypotheses = hypotheses.split("\n")
hypotheses = [hypothesis.replace("- ", "", 1) for hypothesis in hypotheses]
hypotheses = [hypothesis.strip() for hypothesis in hypotheses]

for hypothesis in hypotheses:
    print("- ", hypothesis)

