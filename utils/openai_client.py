import openai
import os
from utils.logger import logger

def get_completion(prompt, max_tokens=512):
    openai.api_key = os.getenv("OPENAI_API_KEY")

    logger.debug(f"OenAI Completion Prompt:\n{prompt}")
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[{
            "role": "user",
            "content": prompt,
        }],
        max_tokens=max_tokens,
    )

    response_content = response["choices"][0]["message"]["content"]
    logger.debug(f"OpenAI Completion Response:\n{response_content}")
    return response_content
