from groq import Groq
from app.config import get_settings

_settings = get_settings()
_client: Groq | None = None


def get_client() -> Groq:
    global _client
    if _client is None:
        if not _settings.groq_api_key:
            raise RuntimeError("GROQ_API_KEY is missing in backend/.env")
        _client = Groq(api_key=_settings.groq_api_key)
    return _client


def chat(
    prompt: str,
    system: str = "You are a helpful assistant.",
    fast: bool = False,
    max_tokens: int = 2000,
) -> str:
    model = _settings.model_fast if fast else _settings.model_smart
    resp = get_client().chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        max_completion_tokens=max_tokens,
        reasoning_effort="low" if fast else "medium",
    )
    return resp.choices[0].message.content or ""


if __name__ == "__main__":
    print(chat("Reply with exactly: Groq is connected."))