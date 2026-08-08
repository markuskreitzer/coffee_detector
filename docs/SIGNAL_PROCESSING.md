# Signal processing

This signal model targets the **Hottop Bean Roaster (1st Gen)** in its actual deployment room. Frequencies, timing, phase behavior, and trained models must not be assumed to apply to other Hottop generations or other roaster models without new recordings and evaluation.

## Event model

The detector uses these event names:

| Event | Meaning | Initial signal approach |
| --- | --- | --- |
| `ready_for_beans` | Warm-up is complete | Narrowband 4.10 kHz tone plus beep cadence |
| `roaster_running` | Drum or fan is active | Sustained spectral and energy profile |
| `beans_charged` | Beans enter the drum | Short broadband transient plus state change |
| `first_crack` | First-crack activity begins | Repeated transient events within a rolling window |
| `second_crack` | Second-crack activity begins | Faster transient rate and a distinct spectral envelope |
| `cooling` | Cooling cycle is active | Fan-dominant steady-state profile |

The warm-up beep is the only implemented event. New detectors should emit an event name, confidence, monotonic timestamp, and detector version so alerts and evaluation data use one contract.

## Audio capture

Use the laptop's built-in microphone in a fixed position relative to the roaster. Record new training and evaluation audio as mono, 48 kHz, 24-bit PCM WAV. Preserve the original capture and derive lower-rate analysis inputs from it.

The warm-up detector processes 16 kHz mono audio because its target tone is near 4.10 kHz. Crack analysis should retain the 48 kHz master recording because the useful transient spectrum has not been established.

Each labeled roast should have a sidecar JSON Lines file. One object represents one event interval:

```json
{"recording":"roast-001.wav","event":"first_crack","start_seconds":412.8,"end_seconds":468.2,"labeler":"human","notes":""}
```

Record representative negative audio from fans, speech, dishes, alarms, the 3D printer, and room activity. Negative examples are required to measure false-alert behavior in the actual room.

## Processing stages

1. Capture audio continuously into bounded in-memory blocks.
2. Compute level, spectral, and transient features without saving ambient audio during normal monitoring.
3. Track the roaster phase so detectors are evaluated only in plausible states.
4. Combine per-block evidence in rolling windows.
5. Emit a debounced event with confidence and supporting measurements.
6. Send alerts through a separate output layer.

Start with inspectable baselines: band energy, spectral flux, onset rate, crest factor, and change-point detection. Add log-mel features and a trained classifier only when labeled recordings show that deterministic features are insufficient.

## Evaluation

Split evaluation by complete roast, not random audio windows. Audio from one roast must not appear in both training and evaluation sets.

Track these measures for every detector version:

- event precision and recall
- onset timing error
- false alerts per monitoring hour
- missed events per roast
- behavior under speech, 3D-printer noise, fans, and kitchen impacts

Keep operating thresholds in configuration and record them with each evaluation result. A detector is ready for unattended alerts only after it performs acceptably on complete held-out roasts recorded in the deployment room.

## Privacy

Normal monitoring should extract features in memory and discard raw audio. Store recordings only for deliberate data collection, keep them outside Git, and review every sample before publication for speech and identifying metadata.
