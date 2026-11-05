"""Prepare either material as separate public input and private scoring standard; no model calls."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from modules.rsi_datasets import archive_dataset, pr8_dataset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--archive', type=Path)
    group.add_argument('--pr8-dir', type=Path)
    parser.add_argument('--input-text', type=Path, help='Optional extracted public PDF text for the v1.2 archive')
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    source = (args.archive or args.pr8_dir).resolve(); output = args.output_dir.resolve()
    if output.is_relative_to(source) or source.is_relative_to(output): parser.error('Keep derived files separate from original materials.')
    if args.input_text and not args.archive: parser.error('--input-text only applies to handbook material.')
    if args.archive:
        text = args.input_text.read_text(encoding='utf-8-sig') if args.input_text else None
        inputs, gold, provenance = archive_dataset(source, text)
    else:
        inputs, gold, provenance = pr8_dataset(source)
    output.mkdir(parents=True, exist_ok=False)
    for relative, value in [('public/input.json', inputs), ('private/gold.json', gold), ('provenance.json', provenance)]:
        path = output/relative; path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    (output/'README.md').write_text('# RSI 评分输入\n\n仅 public/input.json 可交给执行者。private/gold.json 仅供评分。\n'
        '工具已选择材料对应的评分协议；尚未执行真实检索或模型。来源见 provenance.json。\n', encoding='utf-8')
    print(json.dumps({'output_dir': str(output), 'profile': gold['cases'][0]['assessment']['profile'],
                      'reference_targets': len(gold['cases'][0]['targets']), 'model_executed': False}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
