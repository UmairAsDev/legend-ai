# llm_layer/llm_client.py

import os
from dotenv import load_dotenv
from loguru import logger

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from pydantic import SecretStr

load_dotenv()


class LLMClient:

    def __init__(self, model: str = "gpt-4o", temperature: float = 0.2):
        api_key = os.getenv("OPENAI_API_KEY")

        if not api_key:
            raise ValueError("OPENAI_API_KEY not found")

        self.llm = ChatOpenAI(
            model=model,
            temperature=temperature,
            api_key=SecretStr(api_key)
        )

    async def generate_response(self, prompt: str, parser=None):
        """
        Returns structured JSON (dict)
        """

        try:
            messages = [
                SystemMessage(
                    content="Return ONLY valid JSON output."
                ),
                HumanMessage(content=prompt),
            ]

            response = await self.llm.ainvoke(messages)

            content = response.content

            if not content:
                raise RuntimeError("Empty LLM response")

            # 🔹 Parse via JsonOutputParser
            if parser:
                return parser.parse(content)

            return content

        except Exception as e:
            logger.exception(f"❌ LLM failed: {e}")
            raise RuntimeError(f"LLM generation failed: {e}")