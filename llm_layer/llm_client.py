# llm_layer/llm_client.py

"""
OpenAI async client with STRICT JSON output enforcement.
"""

import os
from dotenv import load_dotenv
from openai import AsyncOpenAI
load_dotenv()

class LLMClient:
    """
    Wrapper for OpenAI GPT-4o with strict JSON output.
    """

    def __init__(self, model: str = "gpt-4o"):
        load_dotenv()

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not found in environment variables")

        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model

    async def generate_response(self, prompt: str, temperature: float = 0) -> str:
        """
        Returns STRICT JSON string from GPT-4o.
        """

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                temperature=temperature,

                
                response_format={"type": "json_object"},

                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a certified medical dermatology coding expert and cosmetic procedure expert specialized in CPT, ICD10, and E/M codes including modifiers. "
                            "Return ONLY valid JSON. No markdown, no explanation."
                        )
                    },
                    {"role": "user", "content": prompt},
                ],
            )

            content = response.choices[0].message.content
            if content is None:
                raise RuntimeError("LLM returned empty content")
            return content.strip()

        except Exception as e:
            raise RuntimeError(f"LLM generation failed: {e}")