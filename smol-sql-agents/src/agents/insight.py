import logging
import os
from typing import Any, Dict

from .base import BaseAgent

logger = logging.getLogger(__name__)


class InsightAgent(BaseAgent):
    """Suggest one useful next insight based on the current SQL question."""

    def __init__(self, shared_llm_model=None):
        super().__init__(
            shared_llm_model=shared_llm_model,
            additional_imports=["json"],
            agent_name="Insight Agent",
        )

    def _setup_agent_components(self):
        pass

    def _setup_tools(self):
        self.tools = []

    def propose_insight(self, user_query: str, sql_results: Dict[str, Any]) -> Dict[str, Any]:
        """Return one concise insight and a question that could explore it further."""
        try:
            import json
            import openai

            execution = sql_results.get("query_execution", {})
            is_discovery = sql_results.get("is_discovery", False)
            answer = sql_results.get("answer", "")

            # Build context based on query type
            if is_discovery and answer:
                result_summary = f"Discovery answer: {answer}"
            else:
                result_summary = str(execution)

            prompt = f"""
    The user asked: {user_query}

    The result was:
    {result_summary}

    Propose exactly one interesting, actionable insight the user could investigate next.
    Return valid JSON with exactly these string fields:
    {{"insight": "...", "follow_up_question": "..."}}
    Do not invent facts that are not present in the question or result summary.
    """
            client = openai.OpenAI(
                api_key=os.getenv("OPENAI_API_KEY"),
                base_url=os.getenv("OPENAI_API_BASE"),
            )
            response = client.chat.completions.create(
                model=os.getenv("GROQ_MODEL_FAST", "openai/gpt-oss-20b"),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=180,
                response_format={"type": "json_object"},
            )

            result = json.loads(response.choices[0].message.content)
            insight = str(result.get("insight", "")).strip()
            follow_up_question = str(result.get("follow_up_question", "")).strip()
            if not insight or not follow_up_question:
                raise ValueError("Insight response is missing required fields")

            return {
                "success": True,
                "insight": insight,
                "follow_up_question": follow_up_question,
            }
        except Exception as exc:
            logger.warning("Insight generation failed: %s", exc)
            return {
                "success": False,
                "insight": None,
                "follow_up_question": None,
                "error": str(exc),
            }