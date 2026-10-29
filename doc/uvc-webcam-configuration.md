# uvc-webcam configuration

All settings are environment variables read by `uvc-webcam.sh`. Set them on the
command line, for example:

```bash
sudo RESOLUTIONS="1920x1080 1280x720" QUALITY=90 ./uvc-webcam.sh start video.mp4
```

or change the defaults at the top of `uvc-webcam.sh`. Every setting takes effect
on the next `start`.

| Variable | Default | Description |
|----------|---------|-------------|
| `RESOLUTIONS` | `"1280x720 640x360"` | Resolutions offered to the laptop, separated by spaces. The first one is the default. Only the resolution the laptop picks is transcoded. Use the video's aspect ratio (for example 16:9) to avoid stretching. 1080p30 works but is close to the hardware JPEG encoder's limit (about 32–35 fps). |
| `FPS` | *(from the video)* | Frame rate offered and streamed. When empty, it is read from the video with `ffprobe`. Frames are dropped or duplicated to match. |
| `QUALITY` | *(encoder default)* | Hardware JPEG quality, `1`–`99` (the `mjpeg_rkmpp` `-qp_init` value). Higher is better and larger. The default behaves like `80`. Measured at 720p: default ≈ 68 KB per frame, `90` ≈ 92 KB, `95` ≈ 142 KB. Frames bigger than `width × height × 0.5` bytes are skipped, so very high values at low resolutions can cause stutter. |
| `PRODUCT` | `Radxa UVC Webcam` | Camera name shown on the laptop. |
| `MANUFACTURER` | `Toletus` | USB manufacturer string. |
| `VENDOR_ID` | `0x1d6b` | USB vendor ID (Linux Foundation). |
| `PRODUCT_ID` | `0x0104` | USB product ID (Multifunction Composite Gadget). |
| `MAXPACKET` | `3072` | Isochronous packet size: 1024 to 3072 bytes, 3072 being the maximum for USB 2.0 high-speed. At 3072 the bandwidth is about 24 MB/s. Lower it if the laptop refuses to stream because it lacks USB bandwidth. |
| `UDC` | `fcc00000.usb` | USB device controller of the OTG port. Only change this on another board. |

The laptop may cache the camera's details. If a new resolution or name doesn't
show up, unplug and plug the cable back in.

## Bandwidth reference (USB 2.0, MAXPACKET=3072, default quality, 30 fps)

Measured with a 1080p30 H.264 test video:

| Resolution | Average frame | Largest frame | Average bandwidth |
|------------|---------------|---------------|-------------------|
| 640x360    | 25 KB  | 39 KB  | ~0.8 MB/s |
| 1280x720   | 68 KB  | 107 KB | ~2.0 MB/s |
| 1920x1080  | 124 KB | 197 KB | ~3.7 MB/s |

All of these fit well within the ~24 MB/s available.
