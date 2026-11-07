"""uv run python examples/chat.py; set MODELDock_API_KEY first."""

import os

from openai import OpenAI

client = OpenAI(
    base_url=os.getenv("MODELDock_BASE_URL", "http://localhost:8000") + "/v1",
    api_key=os.environ["MODELDock_API_KEY"],
)
stream = client.chat.completions.create(
    model="mock-chat",
    messages=[{"role": "user", "content": "你好，ModelDock"}],
    stream=True,
    stream_options={"include_usage": True},
)
for chunk in stream:
    if chunk.choices:
        print(chunk.choices[0].delta.content or "", end="", flush=True)
    elif chunk.usage:
        print("\nMock usage:", chunk.usage.total_tokens)
