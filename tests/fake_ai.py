"""A deterministic stand-in for the AI provider."""

from typing import Any


class FakeAI:
    """Returns queued responses (or raises queued errors), recording prompts."""

    name = "fake"
    model = "fake-model"

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def generate_json(self, *, system: str, prompt: str, schema: dict, max_tokens: int = 16000) -> dict:
        self.calls.append({"system": system, "prompt": prompt, "schema": schema})
        if not self.responses:
            raise AssertionError("FakeAI received an unexpected call")
        response = self.responses.pop(0)
        if callable(response):
            response = response(prompt)
        if isinstance(response, Exception):
            raise response
        return response


def analysis_response(**overrides: Any) -> dict:
    """An analysis citing facts by id (f1 = title, f2 = artist, f3 = album …)."""
    characteristics = {name: {"value": "", "evidence": []} for name in (
        "genre", "subgenre", "mood", "style", "tempo", "energy", "danceability", "instrumentation", "vocals", "language"
    )}
    characteristics.update(overrides)
    return {"characteristics": characteristics}


def evaluations_for(prompt: str, ratings: str = "strong", explanation: str = "Shares a dreamy, synth-driven sound.") -> dict:
    """Evaluates every candidate id that appears in the prompt."""
    import re

    ids = re.findall(r'<candidate id="(c\d+)">', prompt)
    dims = ("genre", "mood", "style", "energy", "instrumentation", "vocals")
    return {
        "evaluations": [
            {"candidate_id": cid, "ratings": dict.fromkeys(dims, ratings), "explanation": f"{explanation} ({cid})"}
            for cid in ids
        ]
    }
