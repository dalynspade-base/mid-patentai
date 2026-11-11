"""Score frozen outputs using their material's own profile; never calls a model."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from modules.rsi_experiment import score_predictions, validate_inputs, validate_gold


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--gold', type=Path, required=True)
    parser.add_argument('--predictions', type=Path, required=True)
    parser.add_argument('--reviews', type=Path, help='Separate bound handbook review packet, not executor self-reported points')
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    read = lambda p: json.loads(p.read_text(encoding='utf-8-sig'))
    inputs = validate_inputs(read(args.input)); gold = validate_gold(inputs, read(args.gold))
    reviews = read(args.reviews) if args.reviews else None
    predictions = read(args.predictions)
    result = score_predictions(inputs, gold, predictions, (.5, .5), reviews=reviews)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir/'score.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
