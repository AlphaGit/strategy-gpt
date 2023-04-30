import openai
import os

def get_completion(prompt):
    openai.api_key = os.getenv("OPENAI_API_KEY")

    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[{
            "role": "user",
            "content": prompt,
        }],
        max_tokens=512,
    )

    return response["choices"][0]["message"]["content"]
