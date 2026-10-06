import logging
import concurrent.futures
import os
from typing import Dict, List, Optional, Any

# Import smolagents tools
from smolagents.tools import tool

# Import base classes
from .base import BaseAgent, CachingMixin, ValidationMixin
from .tools.shared import DatabaseTools
from ..validation.business_validator import BusinessValidator
from ..validation.tsql_validator import TSQLValidator

logger = logging.getLogger(__name__)

class NL2SQLAgent(BaseAgent, CachingMixin, ValidationMixin):
    """Streamlined NL2SQL Agent with consistent dictionary returns."""
    
    def __init__(self, database_tools: DatabaseTools, shared_llm_model=None):
        # Initialize mixins
        CachingMixin.__init__(self, cache_size=50)
        ValidationMixin.__init__(self)
        
        self.database_tools = database_tools
        
        # Initialize base agent with unified database tools
        super().__init__(
            shared_llm_model=shared_llm_model,
            additional_imports=['json'],
            agent_name="NL2SQL Agent",
            database_tools=self.database_tools
        )

    def _get_schema_depth(self, user_query: str) -> int:
        """Determine how many table schemas to fetch based on query complexity."""
        query_lower = user_query.lower()
        
        # Signals of increasing complexity
        join_signals = ['join', 'together with', 'related', 'linked', 'combined', 
                        'and their', 'with their', 'along with', 'including their']
        multi_signals = ['compare', 'versus', 'vs', 'difference between', 'both']
        aggregate_signals = ['per', 'each', 'breakdown', 'distribution across', 'group by']
        
        score = 2  # default top 2
        
        if any(word in query_lower for word in join_signals):
            score += 1
        if any(word in query_lower for word in multi_signals):
            score += 1
        if any(word in query_lower for word in aggregate_signals):
            score += 1
        
        return min(score, 4)  # cap at 4    

    def _setup_agent_components(self):
        """Setup agent-specific components."""
        self.business_validator = BusinessValidator()
        self.tsql_validator = TSQLValidator()
        
        # Add validators to mixin
        self.add_validator("syntax", self.tsql_validator.validate_syntax)
        self.add_validator("security", self.tsql_validator.validate_security)
        self.add_validator("performance", self.tsql_validator.check_performance_patterns)
    
    def _setup_tools(self):
        """Setup essential NL2SQL tools."""
        self.tools = []
        
        # Database tools will be integrated automatically by BaseAgent
        # Unified database tools include: get_table_schema_unified_tool, get_all_tables_unified_tool, get_relationships_unified_tool
        
        @tool
        def execute_query_and_return_results(query: str, max_rows: int = 100) -> Dict:
            """Execute query and return results.
            
            Args:
                query: The SQL query to execute.
                max_rows: Maximum number of rows to return.
                
            Returns:
                Dictionary with query execution results and sample data.
            """
            try:
                result = self.database_tools.execute_query_safe(query, max_rows)
                
                if not result.get("success"):
                    return result
                
                rows = result.get("rows", [])
                columns = result.get("columns", [])
                
                return {
                    "success": True,
                    "total_rows": len(rows),
                    "returned_rows": len(rows),
                    "truncated": len(rows) >= max_rows,
                    "sample_data": self._create_sample_summary(rows, columns)
                }
                
            except Exception as e:
                logger.error(f"Query execution failed: {e}")
                return {"success": False, "error": str(e)}
        
        # Add final answer tool for SQL response
        @tool
        def final_answer(sql_query: str) -> Dict[str, Any]:
            """Return the final SQL answer.
            
            Args:
                sql_query: The final SQL query to return.
                
            Returns:
                Dictionary with the final SQL query and success status.
            """
            try:
                return {
                    "success": True,
                    "final_sql": sql_query.strip(),
                    "message": "Final SQL query generated"
                }
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        self.tools.extend([
            execute_query_and_return_results,
            final_answer
        ])

    def _route_query(self, user_query: str) -> str:
        query_lower = user_query.lower()
        
        # Explicit discovery — user is asking about the schema itself
        explicit_discovery = [
            'what tables', 'which tables', 'describe', 'explain',
            'what is the relationship', 'how are', 'tell me about',
            'what does', 'summarize', 'analyze the schema',
            'what columns', 'show me the structure', 'list all tables',
            'what data', 'what information'
        ]
        
        # Implicit discovery — business concept with no obvious table anchor
        # These need the agent to figure out WHICH table to use
        implicit_discovery = [
            'related records in', 'linked to', 'connected to',
            'find all tables', 'how they connect', 'data lineage',
            'staging history', 'trace', 'track'
        ]
        
        if any(signal in query_lower for signal in explicit_discovery):
            return "agent"
        
        if any(signal in query_lower for signal in implicit_discovery):
            return "agent"
        
        return "direct"

    def _build_discovery_prompt(self, user_query: str, entity_context: Dict) -> str:
        entities = entity_context.get("entities", [])
        entity_list = ", ".join(entities) if entities else "not yet identified"

        return f"""
        The user is asking a discovery question about the database:
        "{user_query}"

        Semantic search has already identified these relevant tables: {entity_list}

        Answer in 2-3 sentences in plain English.
        Do NOT write SQL. Do NOT write code.
        Just describe what tables exist and what they contain.
        Be direct and concise.
        """

    def _extract_discovery_answer(self, response) -> str:
        """Extract plain text answer from discovery response."""
        if isinstance(response, dict):
            return response.get("final_sql", response.get("answer", str(response)))
        if isinstance(response, str):
            import re
            # Try to extract from final_answer() call
            match = re.search(r'final_answer\s*\(\s*["\'](.+?)["\']\s*\)', response, re.DOTALL)
            if match:
                return match.group(1).strip()
            return response.strip()
        return str(response)    

    def _get_db_dialect(self) -> str:
        """Detect database dialect from DATABASE_URL."""
        db_url = os.getenv("DATABASE_URL", "")
        if "oracle" in db_url.lower():
            return "oracle"
        elif "sqlite" in db_url.lower():
            return "sqlite"
        elif "postgresql" in db_url.lower():
            return "postgresql"
        elif "mysql" in db_url.lower():
            return "mysql"
        else:
            return "unknown"


    def generate_sql_optimized(self, user_query: str, business_context: Dict, entity_context: Dict) -> Dict[str, Any]:
        """Direct SQL generation bypassing CodeAgent, with agent fallback for discovery queries."""
        import openai
    
        route = self._route_query(user_query)
        if route == "agent":
            print(f"🔍 Routing to direct Groq for discovery query", flush=True)
            import openai
            client = openai.OpenAI(
                api_key=os.getenv("OPENAI_API_KEY"),
                base_url=os.getenv("OPENAI_API_BASE")
            )
            prompt = self._build_discovery_prompt(user_query, entity_context)  #model=os.getenv("GROQ_MODEL_FAST", "openai/gpt-oss-20b"),
            response = client.chat.completions.create(
                model=os.getenv("GROQ_MODEL_SLOW", "xiyan-sql"),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500
            )
            answer = response.choices[0].message.content.strip()
            return {
                "success": True,
                "generated_sql": "",
                "answer": answer,
                "is_discovery": True,
                "query_execution": {"success": True, "total_rows": 0}
            }
    
        print(f"⚡ Routing to direct Groq call for SQL query", flush=True)
        logger.info(f"Starting direct SQL generation for: {user_query}")
        
        try:
            # Build prompt
            prompt = self._build_query_prompt(user_query, business_context, entity_context)
            
            models_to_try = self._choose_model_order(user_query)
            
            for model_name in models_to_try:
                print(f"Trying model: {model_name}", flush=True)
                
                try:
                    client = openai.OpenAI(
                        api_key=os.getenv("OPENAI_API_KEY"),
                        base_url=os.getenv("OPENAI_API_BASE")
                    )
                    
                    response = client.chat.completions.create(
                        model=model_name,
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=500,
                    )
                    
                    generated_sql = response.choices[0].message.content.strip()
                    
                    # Clean up — remove any markdown or final_answer() wrapper if model adds it
                    generated_sql = self._clean_sql(generated_sql)
                    
                    if not generated_sql:
                        print(f"⚠️ {model_name} returned empty SQL", flush=True)
                        continue
                    
                    print(f"Generated SQL: {generated_sql}", flush=True)
                    
                    # Execute and validate
                    result = self._execute_parallel_validation(generated_sql, business_context)
                    
                    if result.get("query_execution", {}).get("success"):
                        print(f"✅ Success with {model_name}", flush=True)
                        
                        # Only judge if 0 rows returned — trust successful results with data
                        rows = result.get("query_execution", {}).get("total_rows", 0)
                        fast_model = os.getenv("GROQ_MODEL_FAST", "qwen2.5-coder:7b")
                        if model_name == fast_model and rows == 0:
                            judged = self._judge_and_maybe_fix(
                                user_query,
                                generated_sql,
                                result.get("query_execution", {}),
                                business_context
                            )
                            if judged:
                                print(f"🔧 Judge replaced instant's SQL", flush=True)
                                judged["model_used"] = model_name + "_judged"
                                return judged
                        
                        result["model_used"] = model_name
                        return result
                    # if result.get("query_execution", {}).get("success"):     #
                    #     print(f"✅ Success with {model_name}", flush=True)
                    #     result["model_used"] = model_name
                    #     return result                    
                    else:
                        error = result.get("query_execution", {}).get("error", "")
                        print(f"⚠️ {model_name} SQL failed: {error}", flush=True)
                        
                        # Retry with versatile if fast model failed
                        if model_name == os.getenv("GROQ_MODEL_FAST", "openai/gpt-oss-20b"):
                            continue  # will try next model in loop
                            
                except Exception as e:
                    print(f"❌ {model_name} error: {e}", flush=True)
                    continue
            
            return {
                "success": False,
                "error": "All models failed to generate valid SQL",
                "generated_sql": "",
                "is_valid": False
            }
            
        except Exception as e:
            logger.error(f"SQL generation failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "generated_sql": "",
                "is_valid": False
            }

    def _clean_sql(self, text: str) -> str:
        """Extract clean SQL from LLM response."""
        import re
        
        # Remove markdown code blocks
        text = re.sub(r'```sql\s*', '', text)
        text = re.sub(r'```python\s*', '', text)
        text = re.sub(r'```\s*', '', text)
        
        # Remove final_answer() wrapper if model still adds it
        match = re.search(r'final_answer\s*\(\s*["\'](.+?)["\']\s*\)', text, re.DOTALL)
        if match:
            return match.group(1).strip()
        
        # Remove explanation text — keep only SQL
        lines = text.strip().split('\n')
        sql_lines = []
        in_sql = False
        for line in lines:
            upper = line.upper().strip()
            if any(upper.startswith(kw) for kw in ['SELECT', 'WITH', 'INSERT', 'UPDATE', 'DELETE']):
                in_sql = True
            if in_sql:
                sql_lines.append(line)
        
        if sql_lines:
            return '\n'.join(sql_lines).strip().rstrip(';')
        
        return text.strip().rstrip(';')


    
    
    
    
    
    def _execute_parallel_validation(self, sql: str, business_context: Dict) -> Dict[str, Any]:
        """Execute parallel validation and query execution."""
        logger.info(f"Starting parallel validation for SQL: {sql[:100]}...")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            # Submit validation tasks
            futures = {
                "syntax": executor.submit(self.validate, sql, "syntax"),
                "business": executor.submit(self._check_business_compliance, sql, business_context),
                "security": executor.submit(self.validate, sql, "security"),
                "performance": executor.submit(self.validate, sql, "performance"),
                "execution": executor.submit(self._execute_query_impl, sql, 100)
            }
            
            # Collect results
            results = {name: future.result() for name, future in futures.items()}
            
            # Debug logging
            logger.info(f"Validation results: {results}")
            
            # Cache results
            cache_key = self._get_cache_key(f"{sql}:{hash(str(business_context))}")
            self._cache_result(cache_key, results)
            
            return self._format_validation_response(sql, results)
    
    def _format_validation_response(self, sql: str, results: Dict) -> Dict[str, Any]:
        """Format validation response."""
        # Handle both boolean and dictionary validation results
        def get_validation_result(result, key, default=False):
            if isinstance(result, bool):
                return result
            elif isinstance(result, dict):
                return result.get(key, default)
            else:
                return default
        
        validation = {
            "syntax_valid": get_validation_result(results["syntax"], "valid", False),
            "business_compliant": get_validation_result(results["business"], "valid", False),
            "security_valid": get_validation_result(results["security"], "valid", False),
            "performance_issues": results["performance"].get("issues", []) if isinstance(results["performance"], dict) else []
        }
        
        # Debug logging
        logger.info(f"Formatted validation: {validation}")
        
        final_result = {
            "success": True,
            "generated_sql": sql,
            "validation": validation,
            "query_execution": results["execution"],
            "is_valid": all([
                validation["syntax_valid"],
                validation["business_compliant"],
                validation["security_valid"]
            ])
        }
        
        logger.info(f"Final SQL generation result: {final_result}")
        return final_result
    
    def _format_response_with_cache(self, sql: str, cached_results: Dict) -> Dict[str, Any]:
        """Format response using cached validation results."""
        return {
            "success": True,
            "generated_sql": sql,
            "validation": cached_results,
            "cached": True,
            "is_valid": cached_results.get("syntax_valid", False)
        }
    
    
    
    def _build_query_prompt(self, user_query: str, business_context: Dict, entity_context: Dict) -> str:
        """Build query prompt."""
        business_instructions = business_context.get("business_instructions", [])
        
        # Get top 2 entities and fetch their schemas efficiently
        entities = entity_context.get("entities", [])
        schema_info = "No schema information available"
        
        if entities:
            schema_parts = []
            for top_entity in entities[:2]:
                try:
                    schema_result = self.database_tools.get_table_schema_unified(top_entity)
                    columns = schema_result.get('columns', [])
                    
                    # M-Schema format
                    lines = [f"# Table: {top_entity}"]
                    for col in columns:
                        name = col.get('name', '')
                        dtype = col.get('type', '')
                        nullable = col.get('nullable', True)
                        is_pk = col.get('primary_key', False)
                        
                        tags = []
                        if is_pk:
                            tags.append('PK')
                        if not nullable:
                            tags.append('NOT NULL')
                        tag_str = ', '.join(tags)
                        tag_str = f', {tag_str}' if tag_str else ''
                        
                        lines.append(f"  ({name}, {dtype}{tag_str})")
                    
                    schema_parts.append('\n'.join(lines))
                except Exception as e:
                    logger.error(f"Failed to fetch schema for {top_entity}: {e}")
            
            # Add foreign keys from YAML business context if available
            fk_lines = []
            business_instructions = business_context.get("business_instructions", [])
            for instruction in business_instructions:
                concept = instruction.get("concept", "")
                if "join" in concept.lower():
                    instructions_text = instruction.get("instructions", "")
                    for line in instructions_text.split('\n'):
                        if '=' in line and ('identity' in line.lower() or 'key' in line.lower()):
                            fk_lines.append(f"  {line.strip()}")
            
            if fk_lines:
                schema_parts.append("[Foreign Keys]\n" + '\n'.join(fk_lines))
            
            schema_info = "\n\n".join(schema_parts) if schema_parts else "No schema information available"
        
        business_context_str = ""
        if business_instructions:
            business_context_str = "Business context:\n"
            for instruction in business_instructions[:3]:
                business_context_str += f"- {instruction.get('instructions', '')}\n"

        feedback_examples = self._get_feedback_examples(user_query)
        lessons = self._load_lessons()
        dialect = self._get_db_dialect()
        if dialect == "oracle":
            sql_rules = """- Use FETCH FIRST N ROWS ONLY instead of TOP N
                - Use SYSDATE instead of NOW() or GETDATE()
                - Use NVL() instead of ISNULL()
                - SYSDATE has no parentheses, never write SYSDATE()
                - Never add WHERE conditions not explicitly asked for by the user
                - Write SQL as a single clean string"""
        else:  # sqlite, postgresql, mysql, unknown
            sql_rules = """- Use LIMIT N instead of FETCH FIRST N ROWS ONLY or TOP N
                - Use date('now') instead of SYSDATE
                - Use COALESCE() instead of NVL()
                - Never add WHERE conditions not explicitly asked for by the user
                - Write SQL as a single clean string"""

        
        return f"""
            Generate {dialect.upper()} SQL for the following request: {user_query}
            
            Database schema (M-Schema format):
            {schema_info}
            
            {business_context_str}
            {feedback_examples}
            {lessons}
            
            Return ONLY the SQL query, nothing else. No explanation, no markdown, no final_answer() wrapper.
            Use foreign keys shown in the schema above for JOIN conditions.
            
            IMPORTANT {dialect.upper()} SQL rules:
            {sql_rules}
            """


    
    
    def _format_schema_info(self, table_schemas: Dict) -> str:
        """Format schema information for prompt."""
        if not table_schemas:
            return "No schema information available"
        
        schema_lines = []
        for table_name, schema in table_schemas.items():
            columns = schema.get("columns", [])
            column_names = [col.get("name", "") for col in columns if col.get("name")]
            if column_names:
                schema_lines.append(f"{table_name}: {', '.join(column_names)}")
        
        return "\n".join(schema_lines) if schema_lines else "No valid schema information"
    
    def _extract_sql_from_response(self, response) -> Optional[str]:
        """Extract SQL from agent response."""
        if hasattr(response, 'text'):
            response = response.text
        
        # Handle dictionary response (from final_answer tool)
        if isinstance(response, dict):
            if 'final_sql' in response:
                return response['final_sql']
            elif 'sql' in response:
                return response['sql']
            elif 'query' in response:
                return response['query']
            elif 'error' in response:
                logger.error(f"Agent returned error: {response['error']}")
                return None
        
        if isinstance(response, str):
            import re
            sql_pattern = r'```sql\s*(.*?)\s*```'
            match = re.search(sql_pattern, response, re.DOTALL)
            if match:
                return match.group(1).strip()
            
            final_answer_pattern = r'final_answer\s*\(\s*["\']([^"\']*)["\']'
            match = re.search(final_answer_pattern, response)
            if match:
                return match.group(1).strip()
            
            get_accurate_schema_pattern = r'get_accurate_schema\s*\(\s*["\']([^"\']*)["\']'
            match = re.search(get_accurate_schema_pattern, response)
            if match:
                return match.group(1).strip()
            
            lines = response.split('\n')
            sql_lines = []
            for line in lines:
                if any(keyword in line.upper() for keyword in ['SELECT', 'FROM', 'WHERE', 'JOIN', 'GROUP', 'ORDER']):
                    sql_lines.append(line.strip())
            
            if sql_lines:
                return '\n'.join(sql_lines)
        
        logger.warning(f"Could not extract SQL from response: {type(response)} - {response}")
        return None

    def _check_business_compliance(self, query: str, business_context: Dict) -> Dict:
        matched_concepts = business_context.get("matched_concepts", [])
        # If no concepts defined, always pass compliance
        if not matched_concepts:
            return {"valid": True, "message": "No concepts defined, compliance check skipped"}
        try:
            return self.business_validator.validate_against_concepts(query, matched_concepts)
        except Exception as e:
            logger.error(f"Business compliance check failed: {e}")
            return {"valid": True, "error": str(e)}

    def _get_feedback_examples(self, user_query: str) -> str:
        if not hasattr(self, 'feedback_store') or not self.feedback_store:
            return ""
        try:
            similar = self.feedback_store.find_similar(user_query, n_results=3)
            if not similar:
                return ""
            examples = "Verified examples from past queries:\n"
            for ex in similar:
                examples += f'Q: "{ex["query"]}"\nSQL: {ex["sql"]}\n\n'
            return examples
        except Exception as e:
            logger.error(f"Failed to get feedback examples: {e}")
            return ""       

    def _load_lessons(self) -> str:
        """Load lessons.md from the agents directory."""
        try:
            lessons_path = os.path.join(os.path.dirname(__file__), 'lessons.md')
            with open(lessons_path, 'r', encoding='utf-8') as f:
                return f.read().strip()
        except Exception as e:
            logger.warning(f"Could not load lessons.md: {e}")
            return ""


    def _check_business_compliance2(self, query: str, business_context: Dict) -> Dict:
        """Check business compliance of query."""
        matched_concepts = business_context.get("matched_concepts", [])
        try:
            return self.business_validator.validate_against_concepts(query, matched_concepts)
        except Exception as e:
            logger.error(f"Business compliance check failed: {e}")
            return {"success": False, "error": str(e)}
    
    def _execute_query_impl(self, query: str, max_rows: int = 100) -> Dict:
        """Implementation of query execution."""
        try:
            result = self.database_tools.execute_query_safe(query, max_rows)
            
            if not result.get("success"):
                return result
            
            rows = result.get("rows", [])
            columns = result.get("columns", [])
            
            return {
                "success": True,
                "total_rows": len(rows),
                "returned_rows": len(rows),
                "truncated": len(rows) >= max_rows,
                "sample_data": self._create_sample_summary(rows, columns)
            }
            
        except Exception as e:
            logger.error(f"Query execution failed: {e}")
            return {"success": False, "error": str(e)}
    
    def _create_sample_summary(self, rows: List[Dict], columns: List[str]) -> Dict[str, Any]:
        """Create summary of sample data."""
        if not rows:
            return {"message": "No data returned"}
        
        sample_rows = rows[:5]  # First 5 rows
        
        # Calculate numeric statistics
        numeric_stats = {}
        for col in columns:
            numeric_values = [
                row.get(col) for row in rows 
                if row.get(col) is not None and isinstance(row.get(col), (int, float))
            ]
            if numeric_values:
                numeric_stats[col] = {
                    "min": min(numeric_values),
                    "max": max(numeric_values),
                    "avg": round(sum(numeric_values) / len(numeric_values), 2)
                }
        
        return {
            "sample_rows": sample_rows,
            "columns": columns,
            "numeric_stats": numeric_stats
        }
    
    # Legacy method for compatibility
    def generate_sql(self, user_query: str, business_context: Dict, entity_context: Dict) -> Dict[str, Any]:
        """Legacy SQL generation method."""
        return self.generate_sql_optimized(user_query, business_context, entity_context)
    
    def _format_final_sql_response(self, sql_query: str) -> Dict:
        """Return the final SQL answer.
        
        Args:
            sql_query: The final SQL query to return.
            
        Returns:
            Dictionary with the final SQL query and success status.
        """
        try:
            return {
                "success": True,
                "final_sql": sql_query.strip(),
                "message": "Final SQL query generated"
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
        


    def _choose_model_order(self, user_query: str):
        """Decide model order based on query complexity."""
        
        fast = os.getenv("GROQ_MODEL_FAST", "openai/gpt-oss-20b")
        slow = os.getenv("GROQ_MODEL_SLOW", "openai/gpt-oss-120b")

        query_lower = user_query.lower()

        # simple heuristic (you already do similar logic!)
        complex_signals = [
            "join", "group by", "trend", "over time",
            "aggregation", "per", "each", "distribution"
        ]

        if any(word in query_lower for word in complex_signals):
            return [slow]  # skip small model entirely
        
        if len(user_query.split()) < 8:
            return [fast, slow]

        return [fast, slow]
    

    
    def _run_with_model(self, model_name: str, prompt: str):
        """Run prompt with a specific model using a fresh agent instance."""
        from smolagents.models import OpenAIModel
        from smolagents.agents import CodeAgent
        
        temp_model = OpenAIModel(
            model_id=model_name,
            api_key=os.getenv("OPENAI_API_KEY"),
            api_base=os.getenv("OPENAI_API_BASE")
        )
        
        # Create fresh agent — do NOT swap self.agent.model
        temp_agent = CodeAgent(
            tools=self.tools,
            model=temp_model,
            additional_authorized_imports=['json']
        )
        
        response = temp_agent.run(prompt)
        return response


    def _retry_sql_fix(self, sql: str, error: str, business_context: Dict, model_name: str):
        """Retry SQL generation with error feedback."""
        fix_prompt = f"""
        You are fixing an Oracle SQL query.
        
        Original SQL:
        {sql}
        
        Error:
        {error}
        
        Fix the query so it runs correctly on Oracle.
        
        Rules:
        - Keep the same intent as the original query
        - Use valid Oracle SQL syntax
        - FETCH FIRST N ROWS ONLY instead of TOP N
        - SYSDATE not SYSDATE()
        - Never add WHERE conditions not in the original query
        - Return ONLY the fixed SQL using final_answer()
        
        Example:
        final_answer("SELECT * FROM address WHERE paid = 'X' FETCH FIRST 100 ROWS ONLY")
        """
        
        response = self._run_with_model(model_name, fix_prompt)
        fixed_sql = self._extract_sql_from_response(response)
        
        if not fixed_sql:
            return {"success": False, "error": "Retry produced no SQL"}
        
        return self._execute_parallel_validation(fixed_sql, business_context)


    def _judge_and_maybe_fix(self, user_query: str, generated_sql: str, execution_result: Dict, business_context: Dict) -> Dict[str, Any]:
        """Ask versatile to judge if instant's SQL actually answers the question."""
        import openai
        
        dialect = self._get_db_dialect()
        if dialect == "oracle":
            slow = os.getenv("GROQ_MODEL_SLOW", "qwen2.5-coder:14b")
        else:
            slow = "xiyan-sql"  # XiYanSQL is better for SQLite/standard SQL
        
        sample = execution_result.get("sample_data", {}).get("sample_rows", [])
        sample_str = str(sample[:3]) if sample else "no rows returned"
        
        execution_success = execution_result.get("success", False)
        rows_returned = execution_result.get("total_rows", 0)

        judge_prompt = f"""
        Original question: "{user_query}"
        
        Generated SQL: {generated_sql}
        
        Execution status: {"SUCCESS" if execution_success else "FAILED"}
        Rows returned: {rows_returned}
        Sample results: {sample_str}
        
        Does this SQL fully and correctly answer the question?
        If execution succeeded and results look reasonable, reply CORRECT.
        Only reply INCORRECT if the SQL is clearly wrong or missing filters.
        
        Reply with either:
        - CORRECT
        - INCORRECT: <fixed SQL query only, no explanation>
        """
        
        try:
            client = openai.OpenAI(
                api_key=os.getenv("OPENAI_API_KEY"),
                base_url=os.getenv("OPENAI_API_BASE")
            )
            
            response = client.chat.completions.create(
                model=slow,
                messages=[{"role": "user", "content": judge_prompt}],
                max_tokens=300
            )
            
            verdict = response.choices[0].message.content.strip()
            print(f"🧑‍⚖️ Judge verdict: {verdict[:80]}", flush=True)
            
            if verdict.upper().startswith("CORRECT"):
                return None  # original result is fine
            
            # Extract fixed SQL
            fixed_sql = verdict.replace("INCORRECT:", "").strip()
            fixed_sql = self._clean_sql(fixed_sql)
            
            if not fixed_sql:
                return None  # judge failed to produce SQL, keep original
            
            print(f"🔧 Judge produced fixed SQL: {fixed_sql}", flush=True)
            return self._execute_parallel_validation(fixed_sql, business_context)
            
        except Exception as e:
            print(f"⚠️ Judge failed: {e}", flush=True)
            return None  # on any error, keep original result


           