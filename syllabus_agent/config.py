import sys
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Provider-agnostic config. Field names deliberately avoid "gemini" so that
    swapping the LLM provider (Groq, OpenRouter, local Ollama, Claude, ...) is a
    .env change only — point these three at the new provider's OpenAI-compatible
    endpoint and nothing downstream needs to change.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    gemini_model: str = "gemini-3.6-flash"

    tavily_api_key: str = ""

    # Cache. Local mongod by default; MongoDB Atlas's free M0 tier works fine
    # here too (paste its mongodb+srv:// string). The cache is an optimisation,
    # not a store of record — an unreachable Mongo degrades to "no caching".
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "syllabus_agent"
    cache_ttl_days: int = 30

    @property
    def llm_api_key(self) -> str:
        return self.gemini_api_key

    @property
    def llm_base_url(self) -> str:
        return self.gemini_base_url

    @property
    def llm_model(self) -> str:
        return self.gemini_model


@lru_cache
def get_settings() -> Settings:
    return Settings()


def warn_on_missing_keys(settings: Settings) -> list[str]:
    """Warn loudly to stderr about unset API keys, so a run fails with a clear
    cause instead of an opaque 401 from httpx. Returns the missing key names.
    """
    missing = [
        name
        for name, value in (
            ("GEMINI_API_KEY", settings.gemini_api_key),
            ("TAVILY_API_KEY", settings.tavily_api_key),
        )
        if not value.strip() or value.strip().startswith("your-")
    ]

    for name in missing:
        print(
            f"WARNING: {name} is missing or still set to the .env.example placeholder. "
            f"Calls that need it will fail.",
            file=sys.stderr,
        )

    return missing
