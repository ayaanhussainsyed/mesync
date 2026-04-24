import numpy as np
from openai import OpenAI
from config import Config

openai_client = OpenAI(api_key=Config.OPENAI_API_KEY)


def embed(text: str):
    resp = openai_client.embeddings.create(
        model=Config.OPENAI_EMBEDDING_MODEL,
        input=text,
    )
    return resp.data[0].embedding


def compute_big_five(answers: dict) -> dict:
    q1 = int(answers.get("q1", 3))
    q2 = int(answers.get("q2", 3))
    q3 = int(answers.get("q3", 3))
    q4 = int(answers.get("q4", 3))
    q5 = int(answers.get("q5", 5))

    def to100(x, mx):
        return round((x / mx) * 100)

    return {
        "extraversion": to100(q1, 5),
        "agreeableness": to100(q2, 5),
        "openness": to100(q3, 5),
        "conscientiousness": to100(q4, 5),
        "neuroticism": to100(q5, 10),
    }