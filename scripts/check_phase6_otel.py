"""Check sanitized API and worker trace markers in the local test collector."""

import json
import re
import subprocess
from pathlib import Path


def main() -> None:
    compose = [
        "docker", "compose", "--env-file", ".env", "--env-file", ".env.phase4.local",
        "-f", "infra/compose.yaml", "-f", "infra/compose.phase4-e2e.yaml",
        "-f", "infra/compose.phase6-e2e.yaml", "-f", "infra/compose.otel-e2e.yaml",
    ]
    output = subprocess.run(
        [*compose, "logs", "--no-log-prefix", "otel-collector"],
        check=True, capture_output=True, text=True,
    ).stdout
    services = sorted(set(re.findall(r"service\.name: Str\((automiq-[a-z]+)\)", output)))
    spans = {
        name: len(re.findall(rf"Name\s+: {re.escape(name)}(?:\r?\n|$)", output))
        for name in (
            "http.request", "webhook.receive", "workflow.start", "activity.load_run",
            "projection.write", "agent.invoke", "provider.action",
        )
    }
    schedule_spans = len(re.findall(r"Name\s+: schedule.fire(?:\r?\n|$)", output))
    traces: dict[str, set[str]] = {name: set() for name in spans}
    for block in re.split(r"(?m)^Span #\d+\s*$", output):
        trace_id = re.search(r"(?m)^\s*Trace ID\s*:\s*([0-9a-f]{32})\s*$", block)
        name = re.search(r"(?m)^\s*Name\s*:\s*([^\r\n]+)", block)
        if trace_id and name and name.group(1).strip() in traces:
            traces[name.group(1).strip()].add(trace_id.group(1))
    shared = set.intersection(
        traces["http.request"], traces["workflow.start"],
        traces["activity.load_run"], traces["projection.write"],
    )
    schedule_traces: set[str] = set()
    for block in re.split(r"(?m)^Span #\d+\s*$", output):
        trace_id = re.search(r"(?m)^\s*Trace ID\s*:\s*([0-9a-f]{32})\s*$", block)
        name = re.search(r"(?m)^\s*Name\s*:\s*([^\r\n]+)", block)
        if trace_id and name and name.group(1).strip() == "schedule.fire":
            schedule_traces.add(trace_id.group(1))
    shared_schedule = schedule_traces & traces["workflow.start"] & traces["projection.write"]
    log_output = subprocess.run(
        [*compose, "logs", "--no-log-prefix", "api", "worker"],
        check=True, capture_output=True, text=True,
    ).stdout
    traced_events = re.findall(
        r'\{"time":"[^"\n]+","event":"(?:http.request|run.projection)",'
        r'"source":"[^"\n]+","trace_id":"[0-9a-f]{32}"[^\n]*',
        log_output,
    )
    run_events = sum('"run_id":' in event for event in traced_events)
    required_spans = (
        "http.request", "webhook.receive", "workflow.start",
        "activity.load_run", "projection.write",
    )
    passed = (
        services == ["automiq-api", "automiq-worker"]
        and all(spans[name] for name in required_spans)
        and bool(shared) and len(traced_events) > 0 and run_events > 0
    )
    report = {
        "status": "passed" if passed else "failed",
        "services": services,
        "span_counts": spans,
        "shared_api_worker_execution_traces": len(shared),
        "schedule_fire_spans": schedule_spans,
        "shared_schedule_worker_execution_traces": len(shared_schedule),
        "structured_events_with_trace_id": len(traced_events),
        "structured_events_with_run_id": run_events,
        "collector_image": "otel/opentelemetry-collector:0.162.0",
        "profile": "local synthetic fake-provider",
    }
    path = Path("artifacts/e2e/phase6-otel/report.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Phase 6 telemetry check: {report['status']}; {len(services)} services")
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
