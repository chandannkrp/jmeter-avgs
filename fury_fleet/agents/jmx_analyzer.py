from google.adk.agents import Agent

from ..config import MODEL, WORKSPACE_RULE
from ..tools.jmx_tools import analyze_jmx

jmx_analyzer = Agent(
    name="jmx_analyzer",
    model=MODEL,
    description="Reviews an existing JMeter .jmx test plan and flags load-test design problems.",
    instruction="""You are a senior performance engineer reviewing JMeter test plans.
When given a .jmx path, call analyze_jmx. Then give: (1) a short summary of what the test does,
(2) each finding by severity with why it matters and exactly how to fix it in JMeter
(name the element or setting). Report only findings the tool returned.""" + WORKSPACE_RULE,
    tools=[analyze_jmx],
)