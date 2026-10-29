# Radxa ZERO 3E — Enable USB OTG port as Host (DietPi)

By default the OTG USB‑C port works in device mode. Follow these steps to switch
it to host mode so you can plug USB devices into it.

## Enable

```bash
# Back up the boot config
sudo cp -a /boot/dietpiEnv.txt /boot/dietpiEnv.txt.bak-otg

# Add "dwc3-host" to the overlays= line
sudo sed -i -E '/^overlays=/{/dwc3-host/!s/$/ dwc3-host/}' /boot/dietpiEnv.txt

# Check it
grep '^overlays=' /boot/dietpiEnv.txt
# overlays= uart4-m1 dwc3-host

sudo reboot
```

You can also edit `/boot/dietpiEnv.txt` by hand and add `dwc3-host` to the
`overlays=` line, separated by a space.

## Check the connected device

```bash
lsusb
```

## Revert

```bash
sudo cp -a /boot/dietpiEnv.txt.bak-otg /boot/dietpiEnv.txt
sudo reboot
```
