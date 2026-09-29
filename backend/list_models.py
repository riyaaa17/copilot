from groq import Groq
from app.config import get_settings

client = Groq(api_key=get_settings().groq_api_key)
for m in sorted(client.models.list().data, key=lambda x: x.id):
    print(m.id)