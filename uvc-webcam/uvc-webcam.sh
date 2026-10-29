#!/bin/bash
# Expose the Radxa ZERO 3E OTG port as a USB (UVC) webcam that loops a video file.
# Usage: sudo ./uvc-webcam.sh start <video-file>    (Ctrl+C stops it)
#        sudo ./uvc-webcam.sh stop
# Settings can be overridden with environment variables, see CONFIGURATION.md.
set -e

FPS=${FPS:-}                     # empty = use the video's own frame rate
RESOLUTIONS=${RESOLUTIONS:-"1280x720 640x360"}
QUALITY=${QUALITY:-5}            # ffmpeg -q:v (2 = best, 31 = worst)
CACHE=${CACHE:-/var/cache/uvc-webcam}
MAXPACKET=${MAXPACKET:-3072}     # 3 x 1024 bytes per microframe (USB 2.0 high-speed)
UDC=${UDC:-fcc00000.usb}
MANUFACTURER=${MANUFACTURER:-Toletus}
PRODUCT=${PRODUCT:-Radxa UVC Webcam}
VENDOR_ID=${VENDOR_ID:-0x1d6b}   # Linux Foundation
PRODUCT_ID=${PRODUCT_ID:-0x0104} # Multifunction Composite Gadget

PIDFILE=/run/uvc-webcam.pid
G=/sys/kernel/config/usb_gadget/uvcwebcam
F=$G/functions/uvc.usb0
DIR=$(dirname "$(readlink -f "$0")")

# Render the video into JPEG frames, once per resolution (redone if the video or settings change).
render() {
	local video=$1 res=$2 out=$3/$2
	if [ -f "$out/.done" ] && [ "$out/.done" -nt "$video" ] && [ "$(cat "$out/.done")" = "$FPS $QUALITY" ]; then
		return
	fi
	echo "Rendering $video -> $out"
	rm -rf "$out"; mkdir -p "$out"
	ffmpeg -hide_banner -loglevel error -i "$video" -an -r "$FPS" \
		-vf "scale=${res%x*}:${res#*x}" -pix_fmt yuvj420p -q:v "$QUALITY" "$out/%05d.jpg"
	echo "$FPS $QUALITY" > "$out/.done"
}

start() {
	local video=$1
	[ -f "$video" ] || { echo "Usage: $0 start <video-file>" >&2; exit 1; }
	video=$(readlink -f "$video")
	[ -d /sys/class/udc/$UDC ] || { echo "$UDC is not in device mode (see README.md)" >&2; exit 1; }
	[ -d $G ] && { echo "Already running, use: $0 stop" >&2; exit 1; }

	if [ -z "$FPS" ]; then
		FPS=$(ffprobe -v error -select_streams v:0 -show_entries stream=avg_frame_rate -of csv=p=0 "$video")
		FPS=$(( (${FPS%/*} + ${FPS#*/} / 2) / ${FPS#*/} ))
	fi

	local cache=$CACHE/$(echo "$video" | md5sum | cut -c1-12)
	local interval=$((10000000 / FPS)) dirs=()
	for res in $RESOLUTIONS; do render "$video" "$res" "$cache"; done

	mkdir -p $G
	echo $VENDOR_ID > $G/idVendor
	echo $PRODUCT_ID > $G/idProduct
	echo 0x0100 > $G/bcdDevice
	echo 0x0200 > $G/bcdUSB
	echo 0xEF > $G/bDeviceClass    # Miscellaneous (required for IAD / UVC)
	echo 0x02 > $G/bDeviceSubClass
	echo 0x01 > $G/bDeviceProtocol
	mkdir -p $G/strings/0x409
	echo "$MANUFACTURER" > $G/strings/0x409/manufacturer
	echo "$PRODUCT" > $G/strings/0x409/product
	cut -c1-16 /etc/machine-id > $G/strings/0x409/serialnumber
	mkdir -p $G/configs/c.1/strings/0x409
	echo "UVC" > $G/configs/c.1/strings/0x409/configuration
	echo 500 > $G/configs/c.1/MaxPower

	mkdir -p $F
	mkdir -p $F/control/header/h
	ln -s $F/control/header/h $F/control/class/fs/h
	ln -s $F/control/header/h $F/control/class/ss/h

	for res in $RESOLUTIONS; do
		local w=${res%x*} h=${res#*x} fr=$F/streaming/mjpeg/m/$res
		# Biggest rendered frame + 10% is the buffer size the host has to allocate.
		local max=$(find "$cache/$res" -name '*.jpg' -printf '%s\n' | sort -n | tail -1)
		max=$((max + max / 10))
		mkdir -p $fr
		echo $w > $fr/wWidth
		echo $h > $fr/wHeight
		echo $max > $fr/dwMaxVideoFrameBufferSize
		echo $((max * 8 * FPS)) > $fr/dwMinBitRate
		echo $((max * 8 * FPS)) > $fr/dwMaxBitRate
		echo $interval > $fr/dwDefaultFrameInterval
		echo $interval > $fr/dwFrameInterval
		dirs+=("$cache/$res")
	done

	mkdir -p $F/streaming/header/h
	ln -s $F/streaming/mjpeg/m $F/streaming/header/h/m
	ln -s $F/streaming/header/h $F/streaming/class/fs/h
	ln -s $F/streaming/header/h $F/streaming/class/hs/h
	ln -s $F/streaming/header/h $F/streaming/class/ss/h
	echo $MAXPACKET > $F/streaming_maxpacket

	ln -s $F $G/configs/c.1/uvc.usb0
	echo $UDC > $G/UDC

	# The gadget's V4L2 output node sits under <UDC>/gadget.N.
	local dev=
	for v in /sys/class/video4linux/video*; do
		readlink -f $v/device | grep -q "/$UDC/gadget" && dev=/dev/$(basename $v)
	done
	[ -n "$dev" ] || { echo "UVC gadget video node not found" >&2; remove; exit 1; }
	echo "UVC gadget on $dev, $FPS fps, $RESOLUTIONS (Ctrl+C to stop)"

	echo $$ > $PIDFILE
	python3 "$DIR/uvc-stream.py" "$dev" "$FPS" "$MAXPACKET" "${dirs[@]}" &
	local pid=$!
	trap "kill $pid 2>/dev/null" INT TERM
	wait $pid || true
	wait $pid 2>/dev/null || true
	remove
	rm -f $PIDFILE
}

# Stop the running instance (it removes its own gadget), or clean up a leftover gadget.
stop() {
	local pid=$(cat $PIDFILE 2>/dev/null)
	if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
		kill -TERM "$pid"
		while kill -0 "$pid" 2>/dev/null; do sleep 0.2; done
	else
		remove
	fi
}

remove() {
	[ -d $G ] || return 0
	echo "" > $G/UDC 2>/dev/null || true
	rm -f $G/configs/c.1/uvc.usb0
	rm -f $F/streaming/class/{fs,hs,ss}/h $F/streaming/header/h/m $F/control/class/{fs,ss}/h
	rmdir $F/streaming/header/h $F/streaming/mjpeg/m/* $F/streaming/mjpeg/m $F/control/header/h 2>/dev/null || true
	rmdir $F $G/configs/c.1/strings/0x409 $G/configs/c.1 $G/strings/0x409 $G
	echo "UVC gadget removed"
}

case "$1" in
	start) start "$2" ;;
	stop) stop ;;
	*) echo "Usage: $0 start <video-file> | stop" >&2; exit 1 ;;
esac
