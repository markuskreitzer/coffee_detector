# Coffee Detector agent instructions

## Scope

- This project targets the Hottop Bean Roaster (1st Gen).
- The deployed host is the Arch Linux laptop named `asus`.
- The deployed checkout is `~/Projects/coffee_detector`.
- Linux audio is provided by PipeWire through its PulseAudio compatibility layer.
- The default source on `asus` is the built-in analog microphone.
- The current implemented event is the 4.10 kHz warm-up beep cadence for `ready_for_beans`.
- Planned events include roaster running, beans charged, first crack, second crack, and cooling.

## Secrets

- Never commit, print, log, or expose Pushover credentials.
- Live alerts require `PUSHOVER_COFFEE_TOKEN` and `PUSHOVER_USER`.
- On `asus`, systemd reads those values from `~/.config/coffee-detector/env`.
- Keep the environment file owned by the user with mode `600`.
- Recordings, datasets, credential files, virtual environments, caches, and logs stay outside Git unless a deliberately sanitized fixture is required.

## Verification

Run the unit tests:

```sh
.venv/bin/python -m unittest -v
```

Verify the reference recording without sending an alert:

```sh
.venv/bin/python coffee_detector.py \
  --file samples/coffee_roaster_beep.m4a \
  --dry-run \
  --once
```

Check the deployed service and audio source:

```sh
systemctl --user status coffee-detector.service
pactl get-default-source
pactl list short sources
```

Follow detector logs:

```sh
journalctl --user -u coffee-detector.service -f
```

## Signal processing

- Preserve deliberate training captures as mono, 48 kHz, 24-bit PCM WAV masters outside Git.
- Keep the warm-up detector at 16 kHz unless its target changes.
- Label complete roasts and split evaluation by roast, not by random audio windows.
- Measure event precision, recall, onset timing error, false alerts per hour, and missed events per roast.
- Include the colocated 3D printer, speech, fans, dishes, alarms, and room activity in negative evaluation audio.
- Prefer inspectable signal-processing baselines before adding trained models.
- Normal monitoring must extract features in memory and discard raw ambient audio.

## Deployment

- Install or refresh the user service with `scripts/install-user-service.sh`.
- Do not place credentials in the repository or the systemd unit.
- Confirm the Git working tree is clean before pulling or deploying changes.
- Treat thresholds and models as specific to the Hottop Bean Roaster (1st Gen) and its deployment room until recordings prove portability.

## License

- The project uses the PolyForm Noncommercial License 1.0.0.
- Commercial use is not granted.
