"""Build an AstrBot persona JSON from the maintained UTF-8 prompt."""

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROMPT = ROOT / "prompts" / "dafeiyu.md"
OUTPUT = ROOT / "personas" / "dafeiyu_custom.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check without writing")
    args = parser.parse_args()
    prompt = PROMPT.read_text(encoding="utf-8-sig").strip()
    if not prompt:
        parser.error("The prompt is empty.")
    persona = {
        "persona_id": "dafeiyu_custom",
        "system_prompt": prompt,
        # AstrBot uses alternating user/assistant strings, not message objects.
        # Style examples live in the prompt so they cannot become fake history.
        "begin_dialogs": [],
    }
    if args.check:
        try:
            actual = json.loads(OUTPUT.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            parser.exit(1, f"Invalid or missing export: {exc}\n")
        if actual != persona:
            parser.exit(1, "Export is out of date. Run python scripts/build_persona.py\n")
        print("OK: persona JSON matches the prompt and expected import fields.")
        return
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(persona, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Built {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
