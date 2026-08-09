# Coffee Detector

Coffee Detector is an acoustic monitor for the **Hottop Bean Roaster (1st Gen)**. It listens for the roaster's 4.10 kHz warm-up beep cadence and sends a Pushover notification when the machine is ready for beans. The current detector requires three correctly timed beeps to limit false alerts.

The project is designed to grow into local acoustic monitoring for roaster state, first crack, second crack, and cooling. See [Signal processing](docs/SIGNAL_PROCESSING.md) for the event model, recording format, and evaluation plan.

## Requirements

- Python 3.10 or newer
- NumPy
- ffmpeg
- A Pushover application token and user key for live alerts

On Arch Linux:

```sh
sudo pacman -S --needed ffmpeg python
```

On macOS:

```sh
brew install ffmpeg
```

Create the Python environment:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Verify the detector

The included metadata-stripped reference recording should produce one dry-run alert:

```sh
.venv/bin/python coffee_detector.py \
  --file samples/coffee_roaster_beep.m4a \
  --dry-run \
  --once
```

Run the unit tests:

```sh
.venv/bin/python -m unittest -v
```

## Configure Pushover

Never commit credentials. The detector reads them from the process environment:

```sh
export PUSHOVER_COFFEE_TOKEN='your application token'
export PUSHOVER_USER='your user key'
```

For the systemd service, store the same two assignments in:

```text
~/.config/coffee-detector/env
```

Limit the file to its owner:

```sh
chmod 600 ~/.config/coffee-detector/env
```

## Linux microphone

PipeWire's PulseAudio compatibility layer is used on Linux. Show the default source and available sources with:

```sh
pactl get-default-source
pactl list short sources
```

Run against the default microphone without sending a notification:

```sh
.venv/bin/python coffee_detector.py --input-device default --dry-run
```

Pass a source name from `pactl list short sources` to select a particular microphone.

Run a finite microphone health check before relying on live alerts:

```sh
.venv/bin/python coffee_detector.py --input-device default --check-input 10
```

The command reports RMS level, peak level, and the percentage of active samples. It exits with an error when ffmpeg produces no frames or the stream is sustained digital silence. Live monitoring applies the same checks continuously, and the systemd service restarts after an unhealthy input failure.

Measure the received warm-up tone without recording ambient audio or sending an alert:

```sh
.venv/bin/python coffee_detector.py --input-device default --diagnose-tone 15
```

The diagnosis reports the strongest frequency from 2-6 kHz, target-band level, target-to-background ratio, and the number of blocks accepted by the current tone threshold.

The ASUS X202E deployment, audio controls, printer-safe recovery procedure, and service checks are documented in [ASUS X202E deployment](docs/ASUS_X202E.md).

## macOS microphone

macOS will request microphone access on the first run. List the devices recognized by ffmpeg with:

```sh
ffmpeg -f avfoundation -list_devices true -i ""
```

Select a device by name or index:

```sh
.venv/bin/python coffee_detector.py --input-device ':1'
```

## Run as a Linux user service

After creating `~/.config/coffee-detector/env`, install and start the service:

```sh
scripts/install-user-service.sh
```

Inspect it with:

```sh
systemctl --user status coffee-detector.service
journalctl --user -u coffee-detector.service -f
```

The service runs while the desktop user session is active, restarts after failures, uses PipeWire's default source, reapplies the ASUS X202E combo-jack microphone route after every start, and force-stops an unresponsive audio process after five seconds.

## Security and recordings

Credential files, local recordings, datasets, virtual environments, caches, and logs are excluded from Git. Treat ambient recordings as private by default; publish only deliberately selected and sanitized samples.

## License

Coffee Detector is source-available under the [PolyForm Noncommercial License 1.0.0](LICENSE). Personal research, experimentation, study, and hobby use are permitted. Commercial use is not granted.
