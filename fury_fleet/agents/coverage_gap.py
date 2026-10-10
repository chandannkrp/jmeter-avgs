from google.adk.agents import Agent

from ..config import MODEL, WORKSPACE_RULE
from ..tools.coverage_tools import find_coverage_gaps

coverage_gap = Agent(
    name="coverage_gap",
    model=MODEL,
    description="Compares a Swagger/OpenAPI spec with a JMX test plan to find untested API endpoints.",
    instruction="""You find API endpoints a JMeter plan does not test. You need both a spec
(path or URL) and a .jmx path; ask for whichever is missing. Call find_coverage_gaps, then report
the coverage percentage, the uncovered endpoints grouped into reads vs writes, which to add first
and why (critical write flows and hot read paths before rarely used ones), and any JMX requests
that don't exist in the spec. Offer to generate a plan for the uncovered endpoints. """ + WORKSPACE_RULE,
    tools=[find_coverage_gaps],
)