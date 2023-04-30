import openai
import os
from utils.logger import logger

def get_completion(prompt):
    openai.api_key = os.getenv("OPENAI_API_KEY")

    logger.debug(f"OenAI Completion Prompt:\n{prompt}")
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[{
            "role": "user",
            "content": prompt,
        }],
        max_tokens=512,
    )

    response_content = response["choices"][0]["message"]["content"]
    logger.debug(f"OpenAI Completion Response:\n{response_content}")
    return response_content
