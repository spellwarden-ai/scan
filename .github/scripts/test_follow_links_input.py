"""The input `follow-links` (the scanner's `--follow-links`, 1.20.0), offline. The test (1) checks the input, its description
and its default `false`, that it reaches the shell only through the `env` of the Validate and Scan steps, and that the README
has its row, its section, an example, what stays unjudged while it is off, the sentence on the lock file and the link to the
scanner's README; (2) runs the Validate step's script with `true`, `false` and values the Action must refuse, the empty one
among them, and reads its error and the variables it writes; (3) runs the Scan step's script with the stand-in for the
scanner's Python of test_analyze_inputs.py, which records its arguments: with `false`, or a value the Validate step would
have refused, the command is the one the Action ran before this input, byte for byte, and `true` adds exactly
`--follow-links`, before the analyzer's flags, the preset and the reports. Usage:
python3 .github/scripts/test_follow_links_input.py [ACTION_FILE]. Standard library only; needs bash."""
from __future__ import annotations

import importlib.util
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESCRIPTION = "true to follow a linked directory in an instruction folder; false by default"
ERROR = "::error title=Spellwarden::The input follow-links (expected true or false) has a value the Action does not accept."
SCANNER_README = "https://github.com/spellwarden-ai/spellwarden#instruction-files"
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
    wanted = f'^  follow-links:\n    description: "{re.escape(DESCRIPTION)}"\n    required: false\n    default: "false"$'
    if not re.search(wanted, text, re.MULTILINE):
        problems.append(f"the input follow-links is missing, or its description is not {DESCRIPTION!r}, or its default is "
                        'not "false"')
    steps = helpers.steps(text)
    for name in ("Validate inputs", "Scan"):
        if not re.search(r"^\s+SW_FOLLOW_LINKS: \$\{\{ inputs\.follow-links \}\}$", helpers.env_block(steps[name]), re.MULTILINE):
            problems.append(f"the step {name!r} does not read the input through env: "
                            "SW_FOLLOW_LINKS: ${{ inputs.follow-links }}")
    # No expression may sit in a script (lint_action.py checks every `run:`); here, no other step reads the input at all.
    for name, step in steps.items():
        if "inputs.follow-links" in step and name not in ("Validate inputs", "Scan"):
            problems.append(f"the step {name!r} reads the input follow-links, which only Validate and Scan need")
    flat = " ".join(readme.split())
    for needle, why in (("| `follow-links` | `false` |", "the input's row in the inputs table"),
                        ("## Linked directories in instruction folders", "a section on linked directories"),
                        ("follow-links: true", "an example"),
                        ("`follow-links: yes`", "an example of a value the Action refuses"),
                        ("The input `follow-links` needs `v1.20.0` or later", "the version the input needs"),
                        ("the notice `symlink_skipped`", "what such a link is while the input is off"),
                        ("are not judged", "that the files behind the link are not judged while the input is off"),
                        ("spellwarden lock --follow-links", "that a job with the lock check needs a lock written with the argument"),
                        (SCANNER_README, "the link to the scanner's README")):
        if needle not in flat:
            problems.append(f"README.md lacks {why}")
    return problems


def validate_problems(text: str, temp: Path, helpers) -> list[str]:
    code = helpers.script(helpers.steps(text)["Validate inputs"])
    base = {**helpers.BOOLEANS, "SW_ANALYZE": "false", "SW_MODEL_ENDPOINT": "", "SW_MODEL": "", "SPELLWARDEN_MODEL_KEY": "",
            "SW_PRESET": ""}
    good = ["true", "false"]
    bad = ["", "yes", "no", "on", "1", "0", "True", "TRUE", "False", "auto", "true ", " true", "true\n", "true\nfalse",
           "--follow-links", "true;true", "*", "true,false"]
    problems: list[str] = []
    for number, value in enumerate(good + bad):
        env_file = temp / f"validate-follow-links-{number}.env"
        result = helpers.bash(code, {**base, "SW_FOLLOW_LINKS": value, "GITHUB_ENV": str(env_file)})
        label = f"validate follow-links {value!r}"
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
    off = {"SW_ANALYZE": "false", "SW_MODEL_ENDPOINT": "", "SW_MODEL": "", "SW_PRESET": ""}
    # `false`, and values the Validate step refuses before this step can run: none of them may add the flag.
    for value in ("false", "", "yes", "True", "TRUE", "1", "true ", "--follow-links"):
        argv, _ = helpers.scan_args(text, temp, {**off, "SW_FOLLOW_LINKS": value})
        if argv != before:
            problems.append(f"follow-links {value!r}: the scanner got {argv}, expected the command of before, {before}")
    argv, _ = helpers.scan_args(text, temp, {**off, "SW_FOLLOW_LINKS": "true"})
    if argv != before[:7] + ["--follow-links"] + before[7:]:
        problems.append(f"follow-links true: the scanner got {argv}, expected the command of before with --follow-links")
    # With the lock check on, the scan the README describes: --locked and --follow-links in one command.
    argv, _ = helpers.scan_args(text, temp, {**off, "SW_FOLLOW_LINKS": "true", "SW_LOCKED": "true"})
    if argv != before[:4] + ["--locked"] + before[4:7] + ["--follow-links"] + before[7:]:
        problems.append(f"follow-links true with locked true: the scanner got {argv}, expected --locked and --follow-links")
    argv, _ = helpers.scan_args(text, temp, {"SW_ANALYZE": "true", "SW_MODEL_ENDPOINT": ENDPOINT, "SW_MODEL": MODEL,
                                             "SW_ANALYZE_RUN": "true", "SW_PRESET": "strict", "SW_FOLLOW_LINKS": "true"})
    if argv != before[:7] + ["--follow-links"] + analyzer + ["--preset", "strict"] + before[7:]:
        problems.append(f"follow-links true with the analyzer and a preset: the scanner got {argv}, expected --follow-links, "
                        "then the analyzer's flags, then --preset strict, then the reports")
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
        print("follow-links-input-ok")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
