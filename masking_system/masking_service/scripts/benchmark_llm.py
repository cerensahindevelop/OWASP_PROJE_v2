"""Synthetic LLM-only benchmark; no database, masking or publication writes.

Run from masking_service: .venv/bin/python scripts/benchmark_llm.py --help
Only generated data is sent to the configured endpoint. Output is JSON;
per-chunk timing logs go to stderr. This does not measure detection accuracy.
"""
import argparse
import asyncio
import json
import logging
from pathlib import Path
import sys
from time import monotonic

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import VLLMSettings
from app.services.llm_recognizer import find_llm_detections, LLMRecognitionError
from app.services.audit_reviewer import audit_masked_text
from app.services.llm_runtime import llm_file_context


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--concurrency', type=int, nargs='+', default=[1])
    parser.add_argument('--chars', type=int, default=7000)
    parser.add_argument('--files', type=int, default=1)
    parser.add_argument('--chunk-chars', type=int, default=6000)
    parser.add_argument('--max-tokens', type=int, default=512)
    args = parser.parse_args()
    if min(*args.concurrency, args.chars, args.files, args.chunk_chars, args.max_tokens) <= 0:
        parser.error('All numeric values must be positive')
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger('uvicorn.error.llm').setLevel(logging.INFO)
    base = VLLMSettings()
    if not base.enabled:
        parser.error('VLLM_ENABLED=true required; select a test endpoint explicitly')
    unit = '# Public example: sort numbers in ascending order.\n'
    text = (unit*((args.chars//len(unit))+1))[:args.chars]
    text += '\n# Internal project codename: ZEPHYR_DEMO_741\n'
    results = []
    async def run(s):
        async def one(index):
            name = f'synthetic-{index}.txt'
            start = monotonic()
            try:
                detections = await find_llm_detections(text, [], s, {'file_path': name})
                # Synthetic audit input only; real mask/mapping is intentionally
                # excluded from a benchmark that does not touch the DB.
                with llm_file_context(name):
                    verdict = await audit_masked_text(text.replace('ZEPHYR_DEMO_741', 'mask_kod_1'), s)
                return {'file': name, 'status': 'ok', 'seconds': round(monotonic()-start, 3),
                        'detections': len(detections), 'audit_risky': verdict.risky}
            except LLMRecognitionError:
                return {'file': name, 'status': 'error', 'seconds': round(monotonic()-start, 3)}
        return await asyncio.gather(*(one(i) for i in range(args.files)))
    for concurrency in args.concurrency:
        s = base.model_copy(update={'max_concurrent_requests': concurrency,
                                    'max_file_chars': args.chunk_chars, 'max_tokens': args.max_tokens})
        started = monotonic()
        files = asyncio.run(run(s))
        results.append({'concurrency': concurrency, 'file_chars': len(text),
                        'chunk_chars': s.max_file_chars, 'overlap': s.chunk_overlap_chars,
                        'max_tokens': s.max_tokens, 'timeout_seconds': s.timeout_seconds,
                        'elapsed_seconds': round(monotonic()-started, 3), 'files': files})
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
