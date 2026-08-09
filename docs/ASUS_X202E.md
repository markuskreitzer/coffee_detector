# ASUS X202E deployment

## Hardware and services

The deployed laptop has this audio path:

| Component | Value |
| --- | --- |
| Laptop | ASUS X202E |
| Codec | VIA VT1802 on HDA Intel PCH |
| Microphone | External microphone on the analog combo jack |
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

The VIA codec exposes the combo-jack microphone through pin `0x29` and capture selector `0x1e`. PipeWire restores the internal-microphone route when a new capture stream opens. The user service waits for capture to start, then selects pin `0x29`, enables microphone bias, and changes selector `0x1e` to input `2`.

The service uses non-interactive `sudo` for these two `hda-verb` operations. Confirm that they are authorized before installation:

```sh
sudo -n hda-verb /dev/snd/hwC0D0 0x29 GET_PIN_WIDGET_CONTROL 0
sudo -n hda-verb /dev/snd/hwC0D0 0x1e GET_CONNECT_SEL 0
```

## Input validation

Run a finite input check before a roast:

```sh
cd ~/Projects/coffee_detector
.venv/bin/python coffee_detector.py --input-device default --check-input 10
```

A healthy check reports RMS and peak levels plus at least 20% active samples. The command fails when the stream produces no frames, sustained digital silence, or sparse clipped artifacts.

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

Enable capture and maximum diagnostic gain:

```sh
amixer -c 0 sset Capture 100% cap
amixer -c 0 sset 'Mic Boost' 100%
```

Reduce gain after input is working if normal roaster audio clips.

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

The detector exits with an error when ffmpeg stops delivering frames or the input health window contains insufficient real samples. The systemd unit restarts it after five seconds, reapplies the external-microphone route after every start, and force-stops an unresponsive audio process after five seconds.

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
