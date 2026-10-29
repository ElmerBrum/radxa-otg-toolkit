# otg-radxa

Using the USB‑C OTG port of the Radxa ZERO 3E (DietPi) as a USB **host**
(to connect USB devices) or as a USB **webcam** (the board plays a video file).

## Repository

```
.
├── README.md                          # this file
├── doc/
│   ├── otg-host-mode.md               # enable / disable host mode on the OTG port
│   ├── uvc-webcam.md                  # full webcam gadget guide, how it works, troubleshooting
│   └── uvc-webcam-configuration.md    # webcam settings (resolution, FPS, quality, name...)
└── uvc-webcam/
    ├── uvc-webcam.sh                  # start/stop the USB webcam gadget
    └── uvc-stream.py                  # streams the video frames (used by uvc-webcam.sh)
```

## Usage

### OTG as USB host

```bash
sudo sed -i -E '/^overlays=/{/dwc3-host/!s/$/ dwc3-host/}' /boot/dietpiEnv.txt
sudo reboot
lsusb    # list connected devices
```

Details: [doc/otg-host-mode.md](doc/otg-host-mode.md)

### OTG as USB webcam

The OTG port must be in device mode, meaning `dwc3-host` is not in
`/boot/dietpiEnv.txt`. Connect the laptop to the OTG port, copy `uvc-webcam/` to
the board, then run:

```bash
sudo ./uvc-webcam.sh start /path/to/video.mp4   # Ctrl+C to stop
sudo ./uvc-webcam.sh stop                       # or stop from another shell
```

Open the **"Radxa UVC Webcam"** camera on the laptop.

Details: [doc/uvc-webcam.md](doc/uvc-webcam.md) and
[doc/uvc-webcam-configuration.md](doc/uvc-webcam-configuration.md)
