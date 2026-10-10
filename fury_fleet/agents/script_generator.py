from google.adk.agents import Agent

from ..config import MODEL, WORKSPACE_RULE
from ..tools.jmx_tools import analyze_jmx
from ..tools.jmx_generator import generate_jmx
from ..tools.openapi_tools import parse_openapi

script_generator = Agent(
    name="script_generator",
    model=MODEL,
    description="Generates a ready-to-run JMeter .jmx load test script from a Swagger/OpenAPI spec.",
    instruction="""You generate JMeter test plans from OpenAPI/Swagger specs (file path or URL).
1. Call parse_openapi to understand the API.
2. Call generate_jmx with output_path <output directory>/<api-title-in-kebab-case>.jmx (use output/ if no output directory is listed).
3. Call analyze_jmx on the generated file to sanity-check it.
Then explain briefly: what was generated, which values are parameterized and how to override
them with -J properties, anything the user must replace (e.g. the auth token), and the run command.
Never claim the test was executed; you only generated the script.""" + WORKSPACE_RULE,
    tools=[parse_openapi, generate_jmx, analyze_jmx],
)