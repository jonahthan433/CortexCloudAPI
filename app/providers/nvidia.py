from app.providers.openai import OpenAIProvider


class NvidiaProvider(OpenAIProvider):
    """
    Provider for NVIDIA NIM (inference microservices).
    NIM exposes an OpenAI-compatible endpoint, so we inherit OpenAIProvider.
    """

    def __init__(self, base_url: str = "https://integrate.api.nvidia.com/v1"):
        super().__init__(base_url=base_url)
