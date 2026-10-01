from unittest.mock import Mock

from src.agents.integration import SQLAgentPipeline


def test_pipeline_adds_insight_without_changing_sql_result():
    pipeline = SQLAgentPipeline.__new__(SQLAgentPipeline)
    pipeline.insight_agent = Mock()
    pipeline.insight_agent.propose_insight.return_value = {
        "success": True,
        "insight": "Compare this trend by customer segment.",
        "follow_up_question": "Which segment changed the most?",
    }

    sql_results = {
        "success": True,
        "generated_sql": "SELECT COUNT(*) FROM orders",
        "is_valid": True,
        "query_execution": {"success": True, "total_rows": 1},
    }
    response = pipeline._format_final_response("How many orders exist?", {}, {}, sql_results)

    assert response["sql_generation"]["generated_sql"] == sql_results["generated_sql"]
    assert response["insight"]["insight"] == "Compare this trend by customer segment."
    pipeline.insight_agent.propose_insight.assert_called_once_with(
        "How many orders exist?", sql_results
    )