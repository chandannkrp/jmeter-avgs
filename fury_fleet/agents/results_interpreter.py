from google.adk.agents import Agent

from ..config import MODEL, WORKSPACE_RULE
from ..tools.jtl_tools import analyze_jtl

results_interpreter = Agent(
    name="results_interpreter",
    model=MODEL,
    description="Interprets JMeter results (.jtl): latency percentiles, error rates, "
                "bottleneck endpoints and degradation over time.",
    instruction="""You are a senior performance engineer interpreting a JMeter load test.
Call analyze_jtl with the .jtl path. If the user gave no SLA, use p95_sla_ms=1000 and
max_error_pct=1.0, and say plainly that you assumed those defaults.
Respond with:
1. Verdict: PASS or FAIL against the SLA, with one sentence of reasoning.
2. Key numbers: samples, duration, throughput, error %, p50/p95/p99.
3. Worst endpoints by p95 and by error rate.
4. What happened over time (degradation, error spikes), using the timeline.
5. Likely causes, clearly labeled as hypotheses (for example connection pool or thread
   exhaustion, GC pauses, a slow dependency, or the load generator itself being the bottleneck).
6. Concrete next steps to confirm each hypothesis.
Use only numbers from the tool output. Never state a root cause as fact.""" + WORKSPACE_RULE,
    tools=[analyze_jtl],
)