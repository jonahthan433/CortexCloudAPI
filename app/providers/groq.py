from app.providers.openai import OpenAIProvider


class GroqProvider(OpenAIProvider):
    """
    Provider for Groq API.
    Inherits from OpenAIProvider because Groq exposes an OpenAI-compatible endpoint.
    """

    def __init__(self, base_url: str = "https://api.groq.com/openai/v1"):
        super().__init__(base_url=base_url)
