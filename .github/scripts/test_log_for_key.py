"""Regression test for finding r0:F2: the `connected` job must prove that the key never shows in the job log, with only the
Action masking it. Checks (1) check_log_for_key.leaks on logs shaped like the runner's: the two step headers printed before
the Action's mask are allowed, a Scan header that shows the key (mask dropped) and a debug echo are reported; (2) the
workflow does not mask the key itself, and has a follow-up job that needs `connected`, has `actions: read`, downloads the
logs of the api-key legs and runs the checker; (3) the same for the analyzer's key: the two headers that print the env of
the step that uses the Action before the mask are allowed, a third is reported, and the job `analyze-log` reads the logs of
the `analyze` job's unreachable legs. Usage: python3 .github/scripts/test_log_for_key.py. Standard library only."""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KEY = "swk_" + "a" * 40
TS = "2026-10-23T10:00:00.0000000Z "
MASKED = [
    TS + "##[group]Run ./", TS + "with:", TS + f"  api-key: {KEY}", TS + "  upload: true", TS + "##[endgroup]",
    TS + "##[group]Run # The key is masked", TS + "env:", TS + f"  SPELLWARDEN_API_KEY: {KEY}", TS + "##[endgroup]",
    TS + "##[group]Run scan", TS + "env:", TS + "  SPELLWARDEN_API_KEY: ***", TS + "##[endgroup]",
    TS + "Connected to Acme as github:github.com/acme/tools (key): ok",
    TS + "##[group]Run python", TS + "env:", TS + "  SW_KEY: ***", TS + "##[endgroup]",
]
MODEL_KEY = "fixture-model-key-" + "k" * 24
MODEL_MASKED = [
    TS + "##[group]Run ./", TS + "with:", TS + "  analyze: true", TS + "env:", TS + f"  SPELLWARDEN_MODEL_KEY: {MODEL_KEY}",
    TS + "##[endgroup]",
    TS + "##[group]Run # The keys are masked", TS + "env:", TS + f"  SPELLWARDEN_MODEL_KEY: {MODEL_KEY}", TS + "##[endgroup]",
    TS + "##[group]Run check='import sys'", TS + "env:", TS + "  SPELLWARDEN_MODEL_KEY: ", TS + "##[endgroup]",
    TS + "##[group]Run out=", TS + "env:", TS + "  SPELLWARDEN_MODEL_KEY: ***", TS + "##[endgroup]",
    TS + "Model-assisted analysis (advisory, its verdicts never change the exit code)",
]


def load():
    spec = importlib.util.spec_from_file_location("check_log_for_key", ROOT / ".github/scripts/check_log_for_key.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def job_text(text: str, name: str) -> str:
    start = re.search(rf"^  {name}:\n", text, re.MULTILINE)
    if not start:
        return ""
    rest = text[start.end():]
    end = re.search(r"^  \S", rest, re.MULTILINE)
    return rest[:end.start()] if end else rest


def main() -> int:
    problems: list[str] = []
    checker = load()
    if checker.leaks("\n".join(MASKED), KEY):
        problems.append("a log with only the two allowed headers was reported")
    scan_header = [line.replace("SPELLWARDEN_API_KEY: ***", f"SPELLWARDEN_API_KEY: {KEY}") if "SPELLWARDEN_API_KEY: ***" in line else line for line in MASKED]
    if not checker.leaks("\n".join(scan_header), KEY):
        problems.append("a Scan header that shows the key (the Action's mask dropped) was not reported")
    check_header = [line.replace("SW_KEY: ***", f"SW_KEY: {KEY}") for line in MASKED]
    if not checker.leaks("\n".join(check_header), KEY):
        problems.append("a later step header that shows the key was not reported")
    echoed = MASKED + [TS + f"debug: key={KEY}"]
    if not checker.leaks("\n".join(echoed), KEY):
        problems.append("an echo of the key was not reported")
    text = (ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8")
    connected = job_text(text, "connected")
    if not connected:
        problems.append("test.yml has no connected job")
    if "::add-mask::" in connected:
        problems.append("the connected job masks the key itself: the Action's own mask is never exercised")
    follow = job_text(text, "connected-log")
    if not follow:
        problems.append("test.yml has no connected-log job that reads the logs of the connected job")
    else:
        for needle, why in (("needs: connected", "needs: connected"), ("actions: read", "permissions: actions: read"),
                            ("for leg in api-key api-key-malformed", "the logs of the api-key and api-key-malformed legs"),
                            ('name="connected ($leg)"', "the lookup of the leg's job by its name"),
                            ("/logs", "the REST log download"), ("check_log_for_key.py", "the checker")):
            if needle not in follow:
                problems.append(f"the connected-log job lacks {why}")
    if checker.leaks("\n".join(MODEL_MASKED), MODEL_KEY):
        problems.append("a log with only the two allowed headers of the analyzer's key was reported")
    model_scan = [line.replace("SPELLWARDEN_MODEL_KEY: ***", f"SPELLWARDEN_MODEL_KEY: {MODEL_KEY}") for line in MODEL_MASKED]
    if not checker.leaks("\n".join(model_scan), MODEL_KEY):
        problems.append("a third header that shows the analyzer's key (the Action's mask dropped) was not reported")
    if not checker.leaks("\n".join(MODEL_MASKED + [TS + f"key={MODEL_KEY}"]), MODEL_KEY):
        problems.append("an echo of the analyzer's key was not reported")
    analyze = job_text(text, "analyze")
    if not analyze:
        problems.append("test.yml has no analyze job")
    if "::add-mask::" in analyze:
        problems.append("the analyze job masks the key itself: the Action's own mask is never exercised")
    follow = job_text(text, "analyze-log")
    if not follow:
        problems.append("test.yml has no analyze-log job that reads the logs of the analyze job")
    else:
        for needle, why in (("needs: analyze", "needs: analyze"), ("actions: read", "permissions: actions: read"),
                            ("for leg in unreachable-linux unreachable-windows", "the logs of the two unreachable legs"),
                            ('name="analyze ($leg)"', "the lookup of the leg's job by its name"),
                            ("/logs", "the REST log download"), ("check_log_for_key.py", "the checker")):
            if needle not in follow:
                problems.append(f"the analyze-log job lacks {why}")
    for problem in problems:
        print("FAIL", problem)
    if not problems:
        print("log-for-key-ok")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
