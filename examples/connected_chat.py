"""Call an administrator-configured chat model; credentials remain in ModelDock."""

import os

from openai import OpenAI

client = OpenAI(
    base_url=os.environ.get("MODELDock_BASE_URL", "http://localhost:8000/v1"),
    api_key=os.environ["MODELDock_API_KEY"],
)
model = os.environ["MODELDock_MODEL"]  # ModelDock name, not the upstream model ID.
messages = [{"role": "user", "content": "Hello"}]
print(client.chat.completions.create(model=model, messages=messages).choices[0].message.content)
for chunk in client.chat.completions.create(
    model=model, messages=messages, stream=True, stream_options={"include_usage": True}
):
    if chunk.choices:
        print(chunk.choices[0].delta.content or "", end="", flush=True)
