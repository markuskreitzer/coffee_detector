# ASUS X202E deployment

## Hardware and services

The deployed laptop has this audio path:

| Component | Value |
| --- | --- |
| Laptop | ASUS X202E |
| Codec | VIA VT1802 on HDA Intel PCH |
| Microphone | Built-in digital microphone (codec pin `0x30`) |
| PipeWire source | `alsa_input.pci-0000_00_1b.0.analog-stereo` |
| Detector input | PulseAudio-compatible source `default` |
| Detector service | `coffee-detector.service` |
| Checkout | `~/Projects/coffee_detector` |
| Credentials | `~/.config/coffee-detector/env`, mode `600` |

The host packages required for deployment and audio diagnosis are:

```sh
sudo pacman -S --needed ffmpeg python alsa-utils alsa-tools
```

The `shairport-sync-shairport-sync-1` Docker container is intentionally stopped. It is not required by Coffee Detector.

The built-in microphone uses pin `0x30` and capture selector `0x1e`, input `4`.
ALSA selects this route when capture starts. The service must not override it.
The former `ExecStartPost` selected combo-jack input `2` (pin `0x29`), which
recorded only noise when no external microphone was connected.

On this laptop, maximum `Mic Boost` produced silence and clipped artifacts on
the built-in input. Start with boost at zero and Capture at 50%. Do not set the
PipeWire input slider to 100%: it can raise hardware boost as well as capture gain.
The low percentage shown by PipeWire reflects the combined hardware gain range.

## Input validation

Run a finite input check before a roast:

```sh
cd ~/Projects/coffee_detector
.venv/bin/python coffee_detector.py --input-device default --check-input 10
```

The check reports RMS and peak levels plus the fraction of active samples. It fails when the stream produces no frames, sustained digital silence, or sparse clipped artifacts. Passing this check alone does not prove that the microphone hears sound: an unused analog input can produce noise that passes. Also verify a known sound through the microphone.

Measure a live warm-up beep without saving audio or sending an alert:

```sh
.venv/bin/python coffee_detector.py --input-device default --diagnose-tone 15
```

Play the beep during the measurement. Use the reported peak frequency, target-band level, target-to-background ratio, and matching-block count to calibrate the detector.

Confirm the PipeWire source and active clients:

```sh
pactl get-default-source
pactl list short sources
pactl list short source-outputs
```

Confirm the ALSA capture controls:

```sh
amixer -c 0 sget Capture
amixer -c 0 sget 'Mic Boost'
```

Set the tested built-in microphone gain:

```sh
pactl set-source-volume alsa_input.pci-0000_00_1b.0.analog-stereo 6942
pactl set-source-mute alsa_input.pci-0000_00_1b.0.analog-stereo 0
amixer -c PCH sget 'Mic Boost'
amixer -c PCH sget Capture
sudo alsactl store PCH
```

The PulseAudio volume value `6942` is about 11% in the desktop UI. On this codec it sets Mic Boost to 0 dB and Capture to +7.5 dB (ALSA step 16). Setting only ALSA controls is not sufficient: WirePlumber can restore an older volume when it restarts. Use `pactl` so WirePlumber saves the route volume too.

Keep Mic Boost at zero. Adjust Capture only if a real roaster test requires it, and check for clipping. Repeat the sound test after restarting the audio stack.

## Service validation

Check service state and logs:

```sh
systemctl --user status coffee-detector.service
journalctl --user -u coffee-detector.service -n 50 --no-pager
```

Restart the user audio stack and detector without rebooting:

```sh
systemctl --user restart pipewire.service pipewire-pulse.service wireplumber.service
systemctl --user restart coffee-detector.service
```

The detector exits with an error when ffmpeg stops delivering frames or the input health window contains insufficient real samples. The systemd unit restarts it after five seconds and force-stops an unresponsive audio process after five seconds.

## Printer-safe reboot check

The laptop also controls Klipper. Query Moonraker before any reboot:

```sh
curl -fsS \
  'http://127.0.0.1:7125/printer/objects/query?print_stats'
```

Do not reboot when `print_stats.state` is `printing` or `paused`.

Compare the running and installed kernels:

```sh
uname -r
pacman -Q linux
```

Reboot after a kernel update only when the printer is idle. After reboot, run the finite input check, the reference-file dry run, and the service check before depending on alerts.

## Reference verification

Verify the detector without microphone input or Pushover delivery:

```sh
.venv/bin/python coffee_detector.py \
  --file samples/coffee_roaster_beep.m4a \
  --dry-run \
  --once
```

Run all regression tests:

```sh
.venv/bin/python -m unittest -v
```
