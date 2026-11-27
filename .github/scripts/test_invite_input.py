"""The input `invite` (the scanner's `--no-invite`, 1.26.0), offline. The test (1) checks the input, its description and its
default `true`, that it reaches the shell only through the `env` of the Validate and Scan steps (SW_INVITE), and that the
README has its row and the link to the scanner's README; (2) runs the Validate step's script with `true`, `false` and values
the Action must refuse, the empty one among them, and reads its error and the variables it writes; (3) runs the Scan step's
script with the stand-in for the scanner's Python of test_analyze_inputs.py, which records its arguments: with `true`, the
default, or a value the Validate step would have refused, the command is the one the Action ran before this input, byte for
byte, and `false` adds exactly `--no-invite`, once. Its place is fixed: straight after `--no-telemetry`'s position (after
--locked, --probe, --probe-private and --no-telemetry), so before `--follow-links`, the analyzer's flags (--analyze
--model-endpoint --model), `--preset` and the reports. The summary step and the comment step do not read the input: the
scanner leaves the badge offer out of the comment itself. Usage: python3 .github/scripts/test_invite_input.py [ACTION_FILE].
Standard library only; needs bash."""
from __future__ import annotations

import importlib.util
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESCRIPTION = "false to leave the dashboard line and the badge offer out of the job summary and the comment; true by default"
ERROR = "::error title=Spellwarden::The input invite (expected true or false) has a value the Action does not accept."
SCANNER_README = "https://github.com/spellwarden-ai/spellwarden#links-in-the-markdown-report"
ENDPOINT = "https://llm.example.com/v1"
MODEL = "fixture-model"


def load_helpers():
    """The helpers of test_analyze_inputs.py: the steps of the action, their scripts, bash, and the Scan step's runner."""
    spec = importlib.util.spec_from_file_location("analyze_inputs", Path(__file__).resolve().parent / "test_analyze_inputs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def static_problems(text: str, readme: str, helpers) -> list[str]:
    problems: list[str] = []
    wanted = f'^  invite:\n    description: "{re.escape(DESCRIPTION)}"\n    required: false\n    default: "true"$'
    if not re.search(wanted, text, re.MULTILINE):
        problems.append(f"the input invite is missing, or its description is not {DESCRIPTION!r}, or its default is "
                        'not "true"')
    if not re.search(r'^  telemetry:\n(?:    .+\n)+  invite:\n', text, re.MULTILINE):
        problems.append("the input invite does not come straight after the input telemetry")
    steps = helpers.steps(text)
    for name in ("Validate inputs", "Scan"):
        if not re.search(r"^\s+SW_INVITE: \$\{\{ inputs\.invite \}\}$", helpers.env_block(steps[name]), re.MULTILINE):
            problems.append(f"the step {name!r} does not read the input through env: SW_INVITE: ${{{{ inputs.invite }}}}")
    # No expression may sit in a script (lint_action.py checks every `run:`); here, no other step reads the input at all.
    for name, step in steps.items():
        if "inputs.invite" in step and name not in ("Validate inputs", "Scan"):
            problems.append(f"the step {name!r} reads the input invite, which only Validate and Scan need")
    flat = " ".join(readme.split())
    row = re.search(r"\| `invite` \| `true` \| [^|]+\|", flat)
    if not row:
        problems.append("README.md lacks the input's row in the inputs table, with the default `true`")
    else:
        for needle in ("dashboard line", "badge offer", "job summary", "pull request comment", "rule links", "v1.26.0",
                       SCANNER_README):
            if needle not in row.group(0):
                problems.append(f"the README row of invite lacks {needle!r}")
    return problems


def validate_problems(text: str, temp: Path, helpers) -> list[str]:
    code = helpers.script(helpers.steps(text)["Validate inputs"])
    base = {**helpers.BOOLEANS, "SW_ANALYZE": "false", "SW_MODEL_ENDPOINT": "", "SW_MODEL": "", "SPELLWARDEN_MODEL_KEY": "",
            "SW_PRESET": ""}
    good = ["true", "false"]
    bad = ["", "yes", "no", "maybe", "on", "1", "0", "True", "TRUE", "False", "auto", "true ", " true", "true\n", "true\nfalse",
           "--no-invite", "true;true", "*", "true,false"]
    problems: list[str] = []
    for number, value in enumerate(good + bad):
        env_file = temp / f"validate-invite-{number}.env"
        result = helpers.bash(code, {**base, "SW_INVITE": value, "GITHUB_ENV": str(env_file)})
        label = f"validate invite {value!r}"
        if result.returncode != 0:
            problems.append(f"{label}: the step exited {result.returncode}: {result.stderr.strip()}")
            continue
        written = helpers.read_env(env_file)
        errors = [line for line in result.stdout.splitlines() if line.startswith("::error")]
        if value in good:
            if errors or written.get("SW_VALID") != "true":
                problems.append(f"{label}: refused ({errors}), expected accepted")
        else:
            if errors != [ERROR]:
                problems.append(f"{label}: the errors are {errors}, expected [{ERROR!r}]")
            if written.get("SW_VALID"):
                problems.append(f"{label}: refused, but SW_VALID is set, so the scan would run")
    return problems


def scan_problems(text: str, temp: Path, helpers) -> list[str]:
    problems: list[str] = []
    out = str(temp / "spellwarden")
    # The command of the Action before this input, for the inputs these runs pass (as in test_analyze_inputs.py).
    before = ["scan", str(temp / "project"), "--fail-on", "high", "--no-probe", "--no-probe-private", "--no-telemetry",
              "--format", "sarif", "--output", f"{out}.sarif", "--format", "markdown", "--output", f"{out}.md",
              "--format", "json", "--output", f"{out}.json", "--format", "text"]
    analyzer = ["--analyze", "--model-endpoint", ENDPOINT, "--model", MODEL]
    off = {"SW_ANALYZE": "false", "SW_MODEL_ENDPOINT": "", "SW_MODEL": "", "SW_PRESET": "", "SW_FOLLOW_LINKS": "false"}
    # `true`, and values the Validate step refuses before this step can run: none of them may add the flag.
    for value in ("true", "", "yes", "maybe", "False", "FALSE", "0", "false ", "--no-invite"):
        argv, _ = helpers.scan_args(text, temp, {**off, "SW_INVITE": value})
        if argv != before:
            problems.append(f"invite {value!r}: the scanner got {argv}, expected the command of before, {before}")
    argv, _ = helpers.scan_args(text, temp, {**off, "SW_INVITE": "false"})
    if argv != before[:7] + ["--no-invite"] + before[7:]:
        problems.append(f"invite false: the scanner got {argv}, expected the command of before with --no-invite after "
                        "--no-telemetry")
    elif argv.count("--no-invite") != 1:
        problems.append(f"invite false: --no-invite is in the command {argv.count('--no-invite')} times, expected once")
    # With the other inputs on: --no-invite sits after --locked and --no-telemetry and before --follow-links, the analyzer's
    # flags, the preset and the reports.
    argv, _ = helpers.scan_args(text, temp, {"SW_ANALYZE": "true", "SW_MODEL_ENDPOINT": ENDPOINT, "SW_MODEL": MODEL,
                                             "SW_ANALYZE_RUN": "true", "SW_PRESET": "strict", "SW_FOLLOW_LINKS": "true",
                                             "SW_LOCKED": "true", "SW_INVITE": "false"})
    expected = (before[:4] + ["--locked"] + before[4:7] + ["--no-invite", "--follow-links"] + analyzer
                + ["--preset", "strict"] + before[7:])
    if argv != expected:
        problems.append(f"invite false with every other input on: the scanner got {argv}, expected {expected}")
    argv, _ = helpers.scan_args(text, temp, {"SW_ANALYZE": "true", "SW_MODEL_ENDPOINT": ENDPOINT, "SW_MODEL": MODEL,
                                             "SW_ANALYZE_RUN": "true", "SW_PRESET": "strict", "SW_FOLLOW_LINKS": "true",
                                             "SW_LOCKED": "true", "SW_INVITE": "true"})
    if "--no-invite" in argv:
        problems.append(f"invite true with every other input on: the scanner got {argv}, expected no --no-invite")
    return problems


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else ROOT / "action.yml"
    text = path.read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    helpers = load_helpers()
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        problems = static_problems(text, readme, helpers) + validate_problems(text, temp, helpers)
        problems += scan_problems(text, temp, helpers)
    for problem in problems:
        print(f"FAIL {problem}")
    if not problems:
        print("invite-input-ok")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
