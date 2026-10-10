from google.adk.agents import Agent

from .agents.coverage_gap import coverage_gap
from .agents.jmx_analyzer import jmx_analyzer
from .agents.script_generator import script_generator
from .agents.results_interpreter import results_interpreter
from .config import MODEL

root_agent = Agent(
    name="fury_fleet",
    model=MODEL,
    description="Fury Fleet: coordinator for JMeter performance-testing agents.",
    instruction="""You coordinate Fury Fleet, a team of JMeter performance-testing specialists.
Hand off to the right specialist immediately, without answering yourself:
- Create or generate a JMeter script from a Swagger/OpenAPI spec -> script_generator
- Review, analyze or lint an existing .jmx file -> jmx_analyzer
- Compare a spec with a .jmx, or find untested endpoints -> coverage_gap
If the request is unclear, ask one short question.
- Interpret load test results or a .jtl file -> results_interpreter
""",
    sub_agents=[script_generator, jmx_analyzer, coverage_gap, results_interpreter],
)