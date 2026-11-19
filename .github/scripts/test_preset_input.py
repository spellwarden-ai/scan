"""The input `preset` (the scanner's presets, 1.18.0), offline. The test (1) checks the input, its description and its empty
default, that it reaches the shell only through the `env` of the Validate and Scan steps, and that the README has its row,
its section, the sentence on connected mode and the link to the guide; (2) runs the Validate step's script with the empty
value, the three presets and values the Action must refuse, and reads its error and the variables it writes; (3) runs the
Scan step's script with the stand-in for the scanner's Python of test_analyze_inputs.py, which records its arguments: with
an empty preset the command is the one the Action ran before this input, byte for byte, and each preset adds exactly
`--preset <value>`, after the analyzer's flags and before the reports. Usage: python3 .github/scripts/test_preset_input.py
[ACTION_FILE]. Standard library only; needs bash."""
from __future__ import annotations

import importlib.util
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRESETS = ("strict", "balanced", "low-noise")
DESCRIPTION = "strict, balanced or low-noise; empty uses the scanner's settings"
ERROR = "::error title=Spellwarden::The input preset (expected strict, balanced, low-noise or empty) has a value the Action does not accept."
GUIDE = "https://spellwarden.ai/docs/presets?utm_source="
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
    wanted = f'^  preset:\n    description: "{re.escape(DESCRIPTION)}"\n    required: false\n    default: ""$'
    if not re.search(wanted, text, re.MULTILINE):
        problems.append(f"the input preset is missing, or its description is not {DESCRIPTION!r}, or its default is not empty")
    steps = helpers.steps(text)
    for name in ("Validate inputs", "Scan"):
        if not re.search(r"^\s+SW_PRESET: \$\{\{ inputs\.preset \}\}$", helpers.env_block(steps[name]), re.MULTILINE):
            problems.append(f"the step {name!r} does not read the input through env: SW_PRESET: ${{{{ inputs.preset }}}}")
    # No expression may sit in a script (lint_action.py checks every `run:`); here, no other step reads the input at all.
    for name, step in steps.items():
        if "inputs.preset" in step and name not in ("Validate inputs", "Scan"):
            problems.append(f"the step {name!r} reads the input preset, which only Validate and Scan need")
    flat = " ".join(readme.split())
    for needle, why in (("| `preset` | empty |", "the input's row in the inputs table"),
                        ("## Presets", "a section on presets"),
                        ("preset: strict", "an example"),
                        ("the upload keeps each rule's own severity", "that the upload of connected mode keeps each rule's own severity"),
                        (GUIDE, "the link to the presets guide")):
        if needle not in flat:
            problems.append(f"README.md lacks {why}")
    return problems


def validate_problems(text: str, temp: Path, helpers) -> list[str]:
    code = helpers.script(helpers.steps(text)["Validate inputs"])
    base = {**helpers.BOOLEANS, "SW_ANALYZE": "false", "SW_MODEL_ENDPOINT": "", "SW_MODEL": "", "SPELLWARDEN_MODEL_KEY": ""}
    good = ["", *PRESETS]
    bad = ["medium", "high", "Strict", "STRICT", "low_noise", "lownoise", "low noise", "strict ", " strict", "strict\n",
           "strict\nbalanced", "--preset", "strict;true", "*", "balanced,strict"]
    problems: list[str] = []
    for number, value in enumerate(good + bad):
        env_file = temp / f"validate-preset-{number}.env"
        result = helpers.bash(code, {**base, "SW_PRESET": value, "GITHUB_ENV": str(env_file)})
        label = f"validate preset {value!r}"
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
    off = {"SW_ANALYZE": "false", "SW_MODEL_ENDPOINT": "", "SW_MODEL": ""}
    argv, _ = helpers.scan_args(text, temp, {**off, "SW_PRESET": ""})
    if argv != before:
        problems.append(f"preset empty: the scanner got {argv}, expected the command of before, {before}")
    for value in PRESETS:
        argv, _ = helpers.scan_args(text, temp, {**off, "SW_PRESET": value})
        if argv != before[:7] + ["--preset", value] + before[7:]:
            problems.append(f"preset {value}: the scanner got {argv}, expected the command of before with --preset {value}")
    argv, _ = helpers.scan_args(text, temp, {"SW_ANALYZE": "true", "SW_MODEL_ENDPOINT": ENDPOINT, "SW_MODEL": MODEL,
                                             "SW_ANALYZE_RUN": "true", "SW_PRESET": "low-noise"})
    if argv != before[:7] + analyzer + ["--preset", "low-noise"] + before[7:]:
        problems.append(f"preset low-noise with the analyzer: the scanner got {argv}, expected the analyzer's flags, then "
                        "--preset low-noise, then the reports")
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
        print("preset-input-ok")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
