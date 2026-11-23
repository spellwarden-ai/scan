"""The inputs `analyze`, `model-endpoint` and `model` (the scanner's model-assisted analyzer, 1.17.0), offline. The test
(1) checks the three inputs and their defaults, that no input takes the analyzer's key, and that every step of the
Action but Validate and Scan sets SPELLWARDEN_MODEL_KEY empty, so the key from the env of the step that uses the Action
reaches those two only; (2) runs the Validate step's script with good and bad values and reads its error and the
variables it writes, and checks that the key shows only in `::add-mask::` lines, one per line of the value; (3) runs the
step that decides on a missing key with the events of a push, a pull request from the repository, one from a fork and
one from Dependabot; (4) runs the Scan step's script with a stand-in for the scanner's Python that records its
arguments: with `analyze: false` the command is the one the Action ran before these inputs, byte for byte, with
`analyze: true` it gains exactly `--analyze --model-endpoint <value> --model <value>`, and the key is in the scanner's
environment, never among its arguments. Usage: python3 .github/scripts/test_analyze_inputs.py [ACTION_FILE].
Standard library only; needs bash."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KEY = "fixture-model-key-" + "k" * 24
ENDPOINT = "https://llm.example.com/v1"
MODEL = "fixture-model"
BOOLEANS = {"SW_FAIL_ON": "high", "SW_LOCKED": "auto", "SW_PROBE": "false", "SW_PROBE_PRIVATE": "false",
            "SW_COMMENT": "true", "SW_SARIF_UPLOAD": "true", "SW_TELEMETRY": "true", "SW_ORG": "",
            "SW_CONNECTED_UPLOAD": "true", "SPELLWARDEN_API_KEY": "", "SW_FOLLOW_LINKS": "false"}
STUB = """#!/usr/bin/env bash
# The scanner's Python, played: a log token for -c, the arguments of -m recorded, stdin read for -.
if [ "$2" = "-c" ]; then echo 0123456789abcdef0123456789abcdef; exit 0; fi
if [ "$2" = "-m" ]; then
  shift 3
  printf '%s\\n' "$@" > "$SW_STUB_ARGV"
  printf '%s' "${SPELLWARDEN_MODEL_KEY-UNSET}" > "$SW_STUB_KEY"
  exit 0
fi
cat > /dev/null
"""


def steps(text: str) -> dict[str, str]:
    """The steps of the composite action by name: the text from `- name:` to the next step."""
    body = text[text.index("\n  steps:\n"):]
    parts = re.split(r"\n    - name: ", body)[1:]
    return {part.split("\n", 1)[0].strip(): part for part in parts}


def script(step: str) -> str:
    """The `run: |` block of a step, without its indentation."""
    lines = step[step.index("      run: |\n") + len("      run: |\n"):].splitlines()
    kept = []
    for line in lines:
        if line.strip() and not line.startswith("        "):
            break
        kept.append(line[8:])
    return "\n".join(kept) + "\n"


def env_block(step: str) -> str:
    if "      env:\n" not in step:
        return ""
    start = step.index("      env:\n")
    end = min(i for i in (step.find("      run:", start), step.find("      with:", start), len(step)) if i != -1)
    return step[start:end]


def bash(code: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    base = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp")}
    return subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", code], env={**base, **env},
                          capture_output=True, text=True)


def read_env(path: Path) -> dict[str, str]:
    """The last value each variable got in a GITHUB_ENV file."""
    values: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            values[name] = value
    return values


def static_problems(text: str) -> list[str]:
    problems: list[str] = []
    for name, default in (("analyze", '"false"'), ("model-endpoint", '""'), ("model", '""')):
        if not re.search(rf"^  {re.escape(name)}:\n    description: .+\n    required: false\n    default: {re.escape(default)}$",
                         text, re.MULTILINE):
            problems.append(f"the input {name} is missing or its default is not {default}")
    inputs = text[text.index("\ninputs:\n"):text.index("\noutputs:\n")]
    for name in re.findall(r"^  ([a-z-]+):$", inputs, re.MULTILINE):
        if "model" in name and re.search(r"key|token|secret|password", name):
            problems.append(f"the input {name} could carry the analyzer's key, which comes from SPELLWARDEN_MODEL_KEY only")
    for name, step in steps(text).items():
        blank = re.search(r'^\s+SPELLWARDEN_MODEL_KEY: ""$', env_block(step), re.MULTILINE)
        mentioned = re.search(r"^\s+SPELLWARDEN_MODEL_KEY:", env_block(step), re.MULTILINE)
        if name in ("Validate inputs", "Scan"):
            if mentioned:
                problems.append(f"the step {name!r} sets SPELLWARDEN_MODEL_KEY: it must read the one the caller's env holds")
        elif not blank:
            problems.append(f"the step {name!r} does not set SPELLWARDEN_MODEL_KEY empty: the key would reach it")
    return problems


def validate_problems(text: str, temp: Path) -> list[str]:
    code = script(steps(text)["Validate inputs"])
    problems: list[str] = []
    good = {"SW_ANALYZE": "true", "SW_MODEL_ENDPOINT": ENDPOINT, "SW_MODEL": MODEL, "SPELLWARDEN_MODEL_KEY": KEY}
    cases = [
        # (values over the good ones, the error expected or None, SW_ANALYZE_RUN expected)
        ({}, None, "true"),
        ({"SPELLWARDEN_MODEL_KEY": ""}, None, "no-key"),
        ({"SW_ANALYZE": "false", "SW_MODEL_ENDPOINT": "", "SW_MODEL": "", "SPELLWARDEN_MODEL_KEY": ""}, None, ""),
        ({"SW_ANALYZE": "false", "SW_MODEL_ENDPOINT": "http://not checked", "SW_MODEL": "not checked"}, None, ""),
        ({"SW_ANALYZE": "yes"}, "The input analyze (expected true or false) has a value the Action does not accept.", ""),
        ({"SW_ANALYZE": "True"}, "The input analyze (expected true or false) has a value the Action does not accept.", ""),
        ({"SW_MODEL_ENDPOINT": ""}, "The input analyze is true, but model-endpoint is empty. Set it to the https:// "
                                    "address of your OpenAI-compatible endpoint.", ""),
        ({"SW_MODEL": ""}, "The input analyze is true, but model is empty. Set it to the model name your endpoint expects.", ""),
        ({"SW_MODEL_ENDPOINT": "http://127.0.0.1:8080/v1"}, "The input model-endpoint (expected the https:// address of your "
                                                           "endpoint, without spaces or line breaks) has a value the Action "
                                                           "does not accept.", ""),
        ({"SW_MODEL_ENDPOINT": "https://"}, "The input model-endpoint (expected", ""),
        ({"SW_MODEL_ENDPOINT": "https://llm.example.com/v1 extra"}, "The input model-endpoint (expected", ""),
        ({"SW_MODEL_ENDPOINT": "https://llm.example.com/v1\nhttps://other.example.com"}, "The input model-endpoint (expected", ""),
        ({"SW_MODEL": "fixture model"}, "The input model (expected the model name your endpoint expects, without spaces or "
                                        "line breaks) has a value the Action does not accept.", ""),
        ({"SW_MODEL": "fixture-model\n"}, "The input model (expected", ""),
        ({"SW_MODEL": "fixture\tmodel"}, "The input model (expected", ""),
        ({"SPELLWARDEN_MODEL_KEY": KEY + "\nsecond-line-of-the-key"}, "The environment variable SPELLWARDEN_MODEL_KEY holds "
                                                                      "a line break, which the Action does not accept. The "
                                                                      "value is not shown.", ""),
        ({"SPELLWARDEN_MODEL_KEY": KEY + "\r\n"}, "The environment variable SPELLWARDEN_MODEL_KEY holds a line break", ""),
    ]
    for number, (values, error, run) in enumerate(cases):
        env_file = temp / f"validate-{number}.env"
        env = {**BOOLEANS, **good, **values, "GITHUB_ENV": str(env_file)}
        result = bash(code, env)
        label = f"validate {values or 'with every value good'}"
        if result.returncode != 0:
            problems.append(f"{label}: the step exited {result.returncode}: {result.stderr.strip()}")
            continue
        written = read_env(env_file)
        errors = [line for line in result.stdout.splitlines() if line.startswith("::error")]
        if error is None:
            if errors or written.get("SW_VALID") != "true":
                problems.append(f"{label}: refused ({errors}), expected accepted")
            elif written.get("SW_ANALYZE_RUN", "") != run:
                problems.append(f"{label}: SW_ANALYZE_RUN is {written.get('SW_ANALYZE_RUN')!r}, expected {run!r}")
        else:
            wanted = "::error title=Spellwarden::" + error
            if len(errors) != 1 or not errors[0].startswith(wanted):
                problems.append(f"{label}: the errors are {errors}, expected one starting {wanted!r}")
            if written.get("SW_VALID") or written.get("SW_ANALYZE_RUN"):
                problems.append(f"{label}: refused, but SW_VALID or SW_ANALYZE_RUN is set")
        key = env["SPELLWARDEN_MODEL_KEY"]
        lines = [part for part in key.replace("\r", "\n").split("\n") if part]
        masks = [line[len("::add-mask::"):] for line in result.stdout.splitlines() if line.startswith("::add-mask::")]
        if masks != lines:
            problems.append(f"{label}: masked {len(masks)} lines, expected each of the {len(lines)} lines of the key")
        for line in result.stdout.splitlines() + result.stderr.splitlines():
            if not line.startswith("::add-mask::") and any(part in line for part in lines):
                problems.append(f"{label}: a line other than ::add-mask:: shows the key")
    return problems


def decide_problems(text: str, temp: Path) -> list[str]:
    code = script(steps(text)["Decide whether the analyzer can run"])
    problems: list[str] = []

    def event(number: int, head: str | None) -> str:
        path = temp / f"event-{number}.json"
        payload = {} if head is None else {"pull_request": {"head": {"repo": {"full_name": head}}}}
        path.write_text(json.dumps(payload), encoding="utf-8")
        return str(path)

    cases = [
        ("push", "push", None, "octocat", False),
        ("pull request from the repository", "pull_request", "acme/tools", "octocat", False),
        ("pull_request_target from the repository", "pull_request_target", "acme/tools", "octocat", False),
        ("pull request from a fork", "pull_request", "forker/tools", "octocat", True),
        ("pull_request_target from a fork", "pull_request_target", "forker/tools", "octocat", True),
        ("Dependabot pull request", "pull_request", "acme/tools", "dependabot[bot]", True),
        ("pull request whose event cannot be read", "pull_request", None, "octocat", True),
    ]
    for number, (label, name, head, actor, notice) in enumerate(cases):
        env_file, notes = temp / f"decide-{number}.env", temp / "spellwarden-annotations.txt"
        notes.unlink(missing_ok=True)
        env = {"SW_PY": sys.executable, "GITHUB_EVENT_NAME": name, "GITHUB_EVENT_PATH": event(number, head),
               "GITHUB_REPOSITORY": "acme/tools", "GITHUB_ACTOR": actor, "RUNNER_TEMP": str(temp),
               "GITHUB_ENV": str(env_file)}
        result = bash(code, env)
        written = read_env(env_file)
        if result.returncode != 0:
            problems.append(f"decide, {label}: the step exited {result.returncode}: {result.stderr.strip()}")
            continue
        if written.get("SW_ANALYZE_RUN", "missing") != "":
            problems.append(f"decide, {label}: SW_ANALYZE_RUN is {written.get('SW_ANALYZE_RUN')!r}, expected empty")
        if notice:
            if not result.stdout.startswith("::notice title=Spellwarden::analyzer_no_key: "):
                problems.append(f"decide, {label}: no analyzer_no_key notice: {result.stdout!r}")
            if "SW_READY" in written:
                problems.append(f"decide, {label}: the scan was stopped, expected it to run without the analyzer")
            if not notes.is_file() or "analyzer_no_key" not in notes.read_text(encoding="utf-8").split():
                problems.append(f"decide, {label}: analyzer_no_key is not in the annotations file")
        else:
            how = "env: SPELLWARDEN_MODEL_KEY: " + "$" + "{{ secrets.<NAME> }}"
            if not result.stdout.startswith("::error title=Spellwarden::The input analyze is true, but "
                                            "SPELLWARDEN_MODEL_KEY is not set.") or how not in result.stdout:
                problems.append(f"decide, {label}: no error that says how to set the key: {result.stdout!r}")
            if written.get("SW_READY", "missing") != "":
                problems.append(f"decide, {label}: SW_READY is not cleared, so the scan would run")
    return problems


def scan_args(text: str, temp: Path, extra: dict[str, str]) -> tuple[list[str], str]:
    """The scanner's arguments and the SPELLWARDEN_MODEL_KEY it saw, from the Scan step's script."""
    stub = temp / "python-stub"
    stub.write_text(STUB, encoding="utf-8")
    stub.chmod(0o755)
    project = temp / "project"
    project.mkdir(exist_ok=True)
    argv_file, key_file = temp / "argv.txt", temp / "key.txt"
    argv_file.unlink(missing_ok=True)
    key_file.unlink(missing_ok=True)
    env = {"SW_PY": str(stub), "SW_STUB_ARGV": str(argv_file), "SW_STUB_KEY": str(key_file), "RUNNER_TEMP": str(temp),
           "GITHUB_ENV": str(temp / "scan.env"), "SW_PATH": str(project), "SW_FAIL_ON": "high", "SW_LOCKED": "auto",
           "SW_PROBE": "false", "SW_PROBE_PRIVATE": "false", "SW_TELEMETRY": "false", "SW_ORG": "", "SW_API_KEY": "",
           "SW_CONNECTED_UPLOAD": "true", "SW_DEFAULT_BRANCH": "", **extra}
    result = bash(script(steps(text)["Scan"]), env)
    if result.returncode != 0 or not argv_file.is_file():
        raise SystemExit(f"FAIL the Scan step did not run the scanner: {result.stderr.strip()}")
    return argv_file.read_text(encoding="utf-8").splitlines(), key_file.read_text(encoding="utf-8")


def scan_problems(text: str, temp: Path) -> list[str]:
    problems: list[str] = []
    out = str(temp / "spellwarden")
    # The command of the Action before these inputs, for the inputs these runs pass.
    before = ["scan", str(temp / "project"), "--fail-on", "high", "--no-probe", "--no-probe-private", "--no-telemetry",
              "--format", "sarif", "--output", f"{out}.sarif", "--format", "markdown", "--output", f"{out}.md",
              "--format", "json", "--output", f"{out}.json", "--format", "text"]
    analyzer = ["--analyze", "--model-endpoint", ENDPOINT, "--model", MODEL]
    off = [
        ("analyze false", {"SW_ANALYZE": "false", "SW_MODEL_ENDPOINT": "", "SW_MODEL": ""}),
        ("analyze false with the other two set", {"SW_ANALYZE": "false", "SW_MODEL_ENDPOINT": ENDPOINT, "SW_MODEL": MODEL,
                                                  "SPELLWARDEN_MODEL_KEY": KEY}),
        ("analyze false and SW_ANALYZE_RUN=true in the job", {"SW_ANALYZE": "false", "SW_MODEL_ENDPOINT": ENDPOINT,
                                                              "SW_MODEL": MODEL, "SW_ANALYZE_RUN": "true"}),
        ("analyze true, skipped for want of a key", {"SW_ANALYZE": "true", "SW_MODEL_ENDPOINT": ENDPOINT, "SW_MODEL": MODEL,
                                                     "SW_ANALYZE_RUN": ""}),
    ]
    for label, extra in off:
        argv, _ = scan_args(text, temp, extra)
        if argv != before:
            problems.append(f"{label}: the scanner got {argv}, expected the command of before, {before}")
    argv, seen = scan_args(text, temp, {"SW_ANALYZE": "true", "SW_MODEL_ENDPOINT": ENDPOINT, "SW_MODEL": MODEL,
                                        "SW_ANALYZE_RUN": "true", "SPELLWARDEN_MODEL_KEY": KEY})
    if argv != before[:7] + analyzer + before[7:]:
        problems.append(f"analyze true: the scanner got {argv}, expected the command of before with {analyzer}")
    if any(KEY in token for token in argv):
        problems.append("analyze true: the key is among the scanner's arguments")
    if seen != KEY:
        problems.append("analyze true: the scanner's environment does not hold SPELLWARDEN_MODEL_KEY")
    return problems


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else ROOT / "action.yml"
    text = path.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        problems = static_problems(text) + validate_problems(text, temp) + decide_problems(text, temp)
        problems += scan_problems(text, temp)
    for problem in problems:
        print(f"FAIL {problem}")
    if not problems:
        print("analyze-inputs-ok")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
