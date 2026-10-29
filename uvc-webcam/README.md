# Radxa ZERO 3E as a USB webcam (UVC gadget)

These scripts make the Radxa ZERO 3E show up on a laptop as a normal USB webcam
that plays a video file in a loop. The laptop reads it like any webcam, with no
driver needed (Camera app, OBS, ffplay, browser...).

Tested on DietPi (Debian 12), kernel `6.1.115-vendor-rk35xx`. The laptop was
connected to the OTG USB‑C port.

## Files

| File | What it does |
|------|--------------|
| `uvc-webcam.sh`  | Renders the video into JPEG frames, creates the UVC gadget (configfs) and starts the streamer |
| `uvc-stream.py`  | Answers the host's UVC requests and sends the frames in a loop at the video's frame rate |
| `CONFIGURATION.md` | Settings you can change (resolutions, FPS, quality, USB name...) |

No packages to install: it only needs `bash`, `python3` and `ffmpeg`, which are
already on the board.

## 1. Put the OTG port in device mode

The OTG port must **not** be in host mode. If you enabled host mode
(see `../README.md`), remove `dwc3-host` from the `overlays=` line and reboot:

```bash
sudo sed -i -E '/^overlays=/s/ ?dwc3-host//' /boot/dietpiEnv.txt
sudo reboot
```

Check:

```bash
ls /sys/class/udc
# fcc00000.usb
```

## 2. Copy the scripts to the board

```bash
scp uvc-webcam.sh uvc-stream.py toletus@192.168.27.65:~/uvc-webcam/
```

## 3. Start

```bash
cd ~/uvc-webcam
sudo ./uvc-webcam.sh start /usr/local/share/ihm-facial/videos/actuar-video.mp4
```

```
Rendering /usr/local/share/ihm-facial/videos/actuar-video.mp4 -> /var/cache/uvc-webcam/107b12a098e0/1280x720
Rendering /usr/local/share/ihm-facial/videos/actuar-video.mp4 -> /var/cache/uvc-webcam/107b12a098e0/640x360
UVC gadget on /dev/video0, 24 fps, 1280x720 640x360 (Ctrl+C to stop)
1280x720: 338 frames, max 92658 bytes
640x360: 338 frames, max 35511 bytes
host connected
```

The first run of a video takes about 30 s to render the frames. Later runs reuse
the cache in `/var/cache/uvc-webcam` and start right away.

## 4. Connect the laptop

Connect a USB cable from the laptop to the board's **OTG USB‑C port**. The laptop
sees a new camera called **"Radxa UVC Webcam"**. Open it with any camera app.

On a Linux laptop:

```bash
lsusb                       # ... ID 1d6b:0104 Linux Foundation Multifunction Composite Gadget
v4l2-ctl --list-devices     # find the /dev/videoX of "Radxa UVC Webcam"
ffplay /dev/videoX
```

## 5. Stop

Press **Ctrl+C**, or from another shell:

```bash
sudo ./uvc-webcam.sh stop
```

Both stop the streamer and remove the USB gadget.

## How it works

1. `ffmpeg` decodes the video and saves it as JPEG frames, one folder per resolution.
2. The script creates a UVC function in configfs
   (`/sys/kernel/config/usb_gadget/uvcwebcam`) with an MJPEG format, one frame
   descriptor per resolution and the video's frame interval. Then it binds the
   gadget to the OTG controller `fcc00000.usb`.
3. The kernel creates a V4L2 output node (`/dev/videoN`) for the gadget.
   `uvc-stream.py` handles the UVC probe/commit requests on that node. When the
   host starts streaming, it queues one JPEG frame per frame period and loops
   back to the start at the end of the video.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `fcc00000.usb is not in device mode` | `dwc3-host` is still in `/boot/dietpiEnv.txt`. See step 1. |
| `Already running` | Run `sudo ./uvc-webcam.sh stop` first. |
| Laptop sees nothing | Check that you are using the OTG port and a data cable (not charge-only). Run `cat /sys/class/udc/fcc00000.usb/state`: it must say `configured`. |
| Camera listed but black | Look at the script output on the board. You should see `format committed` and `streaming started`. |
| Changed the video file | Nothing to do. Frames are rendered again when the file is newer than the cache. |
