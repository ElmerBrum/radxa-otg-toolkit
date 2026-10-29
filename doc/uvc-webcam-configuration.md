# uvc-webcam configuration

All settings are environment variables read by `uvc-webcam.sh`. Set them on the
command line, for example:

```bash
sudo RESOLUTIONS="1920x1080 1280x720" QUALITY=3 ./uvc-webcam.sh start video.mp4
```

or change the defaults at the top of `uvc-webcam.sh`.

| Variable | Default | Description |
|----------|---------|-------------|
| `RESOLUTIONS` | `"1280x720 640x360"` | Resolutions offered to the laptop, separated by spaces. The first one is the default. Each one is rendered from the video. Use the video's aspect ratio (for example 16:9) to avoid stretching. |
| `FPS` | *(from the video)* | Frame rate offered and streamed. When empty, it is read from the video with `ffprobe`. Frames are dropped or duplicated to match. |
| `QUALITY` | `5` | JPEG quality, the ffmpeg `-q:v` value: `2` is the best and largest, `31` the worst. Bigger frames use more USB bandwidth (see `MAXPACKET`). |
| `CACHE` | `/var/cache/uvc-webcam` | Where the rendered frames are stored (one subfolder per video path). Delete it to free space or force a new render. |
| `PRODUCT` | `Radxa UVC Webcam` | Camera name shown on the laptop. |
| `MANUFACTURER` | `Toletus` | USB manufacturer string. |
| `VENDOR_ID` | `0x1d6b` | USB vendor ID (Linux Foundation). |
| `PRODUCT_ID` | `0x0104` | USB product ID (Multifunction Composite Gadget). |
| `MAXPACKET` | `3072` | Isochronous packet size: 1024 to 3072 bytes, 3072 being the maximum for USB 2.0 high-speed. At 3072 the bandwidth is about 24 MB/s, enough for 1080p MJPEG. Lower it if the laptop refuses to stream because it lacks USB bandwidth. |
| `UDC` | `fcc00000.usb` | USB device controller of the OTG port. Only change this on another board. |

## Changing a setting

- The video, `RESOLUTIONS`, `FPS` and `QUALITY` all take effect on the next
  `start`. Frames are rendered again when needed.
- The laptop may cache the camera's details. If a new resolution doesn't show
  up, unplug and plug the cable back in.

## Bandwidth reference (USB 2.0, MAXPACKET=3072)

| Resolution | Frame size (`QUALITY=5`; 1080p is an estimate) | at 24 fps |
|------------|-----------------------------|-----------|
| 640x360    | ~20–35 KB  | ~0.8 MB/s |
| 1280x720   | ~50–90 KB  | ~2 MB/s   |
| 1920x1080  | ~120–200 KB | ~5 MB/s  |

All of these fit well within the ~24 MB/s available.
