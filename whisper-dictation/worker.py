"""Persistent local Whisper worker; partial and final transcripts use the same model."""
import json
import math
from pathlib import Path
import sys
import time

import mlx.core as mx
import numpy as np
from scipy.signal import resample_poly
from mlx_whisper.load_models import load_model
from mlx_whisper.transcribe import ModelHolder, transcribe


def vocabulary():
    config = Path.home() / '.config' / 'whisper-dictation'
    words = []
    source_list = config / 'vocabulary-sources'
    sources = [config / 'vocabulary']
    if source_list.exists():
        sources += [Path(p).expanduser() for p in source_list.read_text().splitlines() if p.strip()]
    for source in sources:
        if source.is_file():
            words.extend(w.strip() for w in source.read_text().splitlines() if w.strip() and not w.startswith('#'))
    directories = config / 'project-directories'
    if directories.exists():
        for name in directories.read_text().splitlines():
            directory = Path(name).expanduser()
            if directory.is_dir():
                words.extend(p.name for p in sorted(directory.iterdir()) if p.is_dir())
    return 'Terminology: ' + ', '.join(dict.fromkeys(words)) + '.' if words else None


model_path = sys.argv[1]
ModelHolder.model = load_model(model_path, dtype=mx.float16)
ModelHolder.model_path = model_path
mx.eval(ModelHolder.model.parameters())
print(json.dumps({'ready': True}), flush=True)
for line in sys.stdin:
    request = {}
    try:
        request = json.loads(line)
        started = time.monotonic()
        audio = np.fromfile(request['path'], dtype=np.float32)
        rate = int(request['sample_rate'])
        divisor = math.gcd(rate, 16000)
        if rate != 16000:
            audio = resample_poly(audio, 16000 // divisor, rate // divisor).astype(np.float32)
        # Quiet recordings should not turn vocabulary hints into hallucinated speech.
        text = ''
        if len(audio) >= 4000 and float(np.sqrt(np.mean(audio * audio))) > 0.0005:
            result = transcribe(audio, path_or_hf_repo=model_path, initial_prompt=vocabulary(),
                                verbose=None, condition_on_previous_text=False, temperature=0.0)
            text = ' '.join(result['text'].split())
        print(json.dumps({'session': request['session'], 'final': request['final'],
                          'text': text, 'seconds': round(time.monotonic() - started, 2)}), flush=True)
    except Exception as error:
        print(json.dumps({'session': request.get('session'), 'final': request.get('final', False),
                          'error': str(error)}), flush=True)
