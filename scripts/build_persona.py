"""Build an AstrBot persona JSON from the maintained UTF-8 prompt."""

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check without writing")
    parser.add_argument("--persona", choices=("dafeiyu", "gpt"), default="dafeiyu", help="Persona to build")
    args = parser.parse_args()
    prompt_path = ROOT / "prompts" / (args.persona + ".md")
    output_path = ROOT / "personas" / (args.persona + "_custom.json")
    prompt = prompt_path.read_text(encoding="utf-8-sig").strip()
    if not prompt:
        parser.error("The prompt is empty.")
    persona = {
        "persona_id": args.persona + "_custom",
        "system_prompt": prompt,
        # AstrBot uses alternating user/assistant strings, not message objects.
        # Style examples live in the prompt so they cannot become fake history.
        "begin_dialogs": [],
    }
    if args.check:
        try:
            actual = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            parser.exit(1, f"Invalid or missing export: {exc}\n")
        if actual != persona:
            parser.exit(1, f"Export is out of date. Run python scripts/build_persona.py --persona {args.persona}\n")
        print("OK: persona JSON matches the prompt and expected import fields.")
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(persona, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Built {output_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
