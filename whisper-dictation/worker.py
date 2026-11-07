"""Persistent local Whisper worker; partial and final transcripts use the same model."""
import json
import math
from pathlib import Path
import sys
import time

import mlx.core as mx
import numpy as np
import webrtcvad
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


def speech_audio(audio):
    """Require sustained speech before Whisper sees the audio or vocabulary hints."""
    if not np.isfinite(audio).all():
        return np.empty(0, dtype=np.float32)
    frame_samples = 480  # 30 ms at 16 kHz, as required by WebRTC VAD.
    pcm = np.rint(np.clip(audio, -1, 1) * 32767).astype('<i2')
    vad = webrtcvad.Vad(3)
    voiced = []
    run = longest = 0
    for start in range(0, len(pcm) - frame_samples + 1, frame_samples):
        if vad.is_speech(pcm[start:start + frame_samples].tobytes(), 16000):
            voiced.append(start)
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    # A brief click must not enable a full prompted transcription.
    if len(voiced) < 10 or longest < 4:  # 300 ms total, including a 120 ms run.
        return np.empty(0, dtype=np.float32)
    padding = 3200  # Keep 200 ms around speech so word edges survive trimming.
    return audio[max(0, voiced[0] - padding):min(len(audio), voiced[-1] + frame_samples + padding)]


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
        audio = speech_audio(audio)
        text = ''
        if len(audio):
            result = transcribe(audio, path_or_hf_repo=model_path, initial_prompt=vocabulary(),
                                verbose=None, condition_on_previous_text=False, temperature=0.0,
                                no_speech_threshold=0.4, logprob_threshold=None)
            # Model confidence is a second check, never a substitute for detected speech.
            segments = [segment['text'] for segment in result.get('segments', [])
                        if segment.get('no_speech_prob', 1.0) <= 0.4
                        and segment.get('avg_logprob', -math.inf) >= -1.0
                        and segment.get('compression_ratio', math.inf) <= 2.4]
            text = ' '.join(' '.join(segments).split())
        print(json.dumps({'session': request['session'], 'final': request['final'],
                          'text': text, 'seconds': round(time.monotonic() - started, 2)}), flush=True)
    except Exception as error:
        print(json.dumps({'session': request.get('session'), 'final': request.get('final', False),
                          'error': str(error)}), flush=True)
