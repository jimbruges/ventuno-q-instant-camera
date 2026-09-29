# Hardware build

This folder contains the [VENTUNO Q Instant Camera enclosure](ventuno-q-instant-camera.3mf). The enclosure is a three-part print for the VENTUNO Q, Logitech C270 webcam, CSN-A2 58 mm thermal printer, Modulino Buttons, and Modulino Pixels.

## Parts

- Arduino VENTUNO Q
- Logitech C270
- Modulino Buttons
- Modulino Pixels
- Snap-in spacers for Modulino boards and VENTUNO Q, 14
- CSN-A2 58 mm thermal printer
- USB-C PD 9 V trigger adapter and a compatible USB-C PD power source
- USB-C panel-mount extension cable, 1
- Anker USB-C power bank, 1
- Qwiic cables for the Modulino peripherals
- M3 x 5 mm heat-set inserts, 4
- M3 x 5 mm caphead screws, 4
- 15 cm solid-core hookup wires, 2
- Male-to-male jumper wires for the printer serial connection and other signal connections

The complete bill of materials, including product references, is in the [main README](../README.md#bill-of-materials).

## Print the enclosure

1. Open `ventuno-q-instant-camera.3mf` in the slicer.
2. Keep the three enclosure parts on their intended plates and confirm the printer profile matches the selected material.
3. Use supports for the front plate. Check the camera mount, button, and printer openings after slicing.
4. Print the parts and remove support material before installing electronics.
5. Test-fit the VENTUNO Q, printer, webcam, and Modulino parts before tightening the enclosure screws.

The model was tested on a Prusa Core One+. The `.3mf` contains the printable enclosure source; it does not include the electronics or fasteners.

## Install the heat-set inserts

Install four M3 x 5 mm inserts in the enclosure's prepared mounting holes:

1. Heat a soldering iron to the insert manufacturer's recommended temperature.
2. Place each insert squarely over its printed hole.
3. Press it in slowly until it is flush with the plastic surface. Do not force it below the mounting surface.
4. Let the plastic cool completely, then test each thread with an M3 screw.

Keep the insert aligned while it is hot. Misaligned inserts can prevent the shells from closing or damage the printed mounting bosses.

## Mount the components

Before committing the electronics to the enclosure, connect the VENTUNO Q, Modulino boards, thermal printer, and camera on the bench and confirm that the App starts and the camera is visible.

### Front plate

1. Remove the C270 factory mounting clip by popping off the hinge end cap and removing the screw. Reattach the clip to the front plate; its keyed shaft fits in one direction. Feed the USB cable through the enclosure opening before securing the camera.
2. Feed the printer cables through the printer opening. Use double-sided tape on the printer body to hold the printer in position, keeping the paper path aligned with the front slot.
3. Fit the snap-in spacers and attach the Modulino Pixels to the front plate. Feed a long Qwiic cable through from the Pixels board.
4. Set the USB-C PD screw-terminal adapter to its switched 9 V mode. Connect its output to the printer power terminals, then add the two 15 cm solid-core wires for the VENTUNO Q power input.
5. Secure the panel-mount USB-C extension in the enclosure and connect its internal end to the USB-C PD screw-terminal adapter.

### Middle and rear panels

1. Feed all loose cables through the panel gap before fastening the middle mounting panel to the front plate.
2. Connect the printer RX input to VENTUNO Q D1 / TX with a jumper wire. The current sketch only sends print data; leave printer TX disconnected unless it is level-shifted to 3.3 V before connecting to D0 / RX.
3. Connect the USB camera and route its cable clear of the printer and paper path.
4. Connect the two printer power leads to the VENTUNO Q screw terminals marked 5-24 V and GND. The printer and VENTUNO Q must share ground.
5. Fit the snap-in spacers for the Modulino Buttons on the outer side of the rear panel and attach the board.
6. Test-fit the VENTUNO Q in its opening before pressing in its snap-in spacers. The fit can be tight; confirm alignment before committing the spacers.
7. Connect a Qwiic cable from the VENTUNO Q to the Modulino Buttons, then connect the Modulino Pixels to the Buttons board.
8. Check every connector and cable, then fasten the rear panel.
9. Place the Anker power bank in its slot with the built-in USB-C cable un-stowed and the cable end facing outward.
10. Load a paper roll in the thermal printer according to the printer manual.
11. Connect the power bank to the panel-mount USB-C socket and power up the board.

Do not trap cables between the shells. Leave enough slack to remove the top shell without pulling on the Qwiic connectors or printer terminals.

## Wiring

### Modulino peripherals

Connect Modulino Buttons and Modulino Pixels to the VENTUNO Q over Qwiic/I²C. The sketch initializes the Modulino bus on `Wire1`, then polls the three buttons and drives the eight Modulino Pixels.

- Use Qwiic cables between the VENTUNO Q and the Modulino peripherals.
- Keep the connectors fully seated and avoid sharply bending the Qwiic cables at the enclosure edge.
- The three buttons are treated as A, B, and C from left to right in the app controls.

### Webcam

Connect the Logitech C270 to a VENTUNO Q USB host port. The default camera setting is `auto`; this selects the stable USB capture interface and avoids relying on changing `/dev/video*` numbers. The webcam must be visible to the Linux side before starting the App.

### Thermal printer

The printer uses the VENTUNO Q `Serial1` interface at 9600 baud, 8-N-1:

| Printer connection | VENTUNO Q / power connection |
| --- | --- |
| RX | D1 / TX |
| GND | VENTUNO Q GND and external supply GND |
| Power | Separate regulated 5-9 V supply rated for at least 1.5 A |
| TX | Leave disconnected |

The VENTUNO Q sends print data to the printer and does not currently read printer status. If printer TX is connected in a future revision, level-shift its 5 V signal to 3.3 V before connecting it to D0 / RX.

**Do not power the printer from the VENTUNO Q 3.3 V or 5 V logic header.** The printer's motor and print head can draw more current than those rails provide. Verify the external supply voltage and polarity before connecting it, and always share ground with the VENTUNO Q.

### USB-C PD trigger

Use the USB-C PD trigger adapter only with a compatible USB-C PD source and only after confirming its negotiated output. Set the adapter for the voltage required by the printer power input, within the printer's rated 5-9 V range. Do not connect an unverified PD output to the VENTUNO Q or printer.

## First bring-up

1. Load paper and verify that the paper exits through the front slot.
2. Leave the printer TX disconnected and connect the printer's external supply and shared ground.
3. Connect the webcam and Qwiic peripherals.
4. Install the App with `./install.sh`, or start the App from App Lab.
5. Open the Web UI and run **Test Print** before enabling print-after-capture.
6. Check the App logs and MCU monitor if the printer or peripherals do not respond.

The full software installation and operating instructions are in [the main README](../README.md) and [the app README](../ArduinoApps/instant-me-camera/README.md).

## Physical controls

- Short press A, B, or C runs that button's short profile.
- Hold a button for at least 900 ms to run its long profile.
- Press any button while a capture, generation, print, or Wi-Fi operation is active to cancel it.
- Hold A and C together for 1.5 seconds while Ready to start screenless Wi-Fi QR setup.
- The Modulino Pixels provide the capture flash and status lighting; the VENTUNO Q LED matrix shows camera and printer states.

The default profiles are A short Normal, B short Cloud, C short Local, A long Describe, B long Cloud, and C long Local. Cloud mode needs an OpenRouter key and network access; Local mode needs the Standard NPU bundle installed and healthy; Describe mode uses the local VLM Brick.

## Safety checklist

Before closing the enclosure, confirm:

- The printer has a separate regulated supply rated for its load.
- Printer ground and VENTUNO Q ground are connected.
- Printer TX is disconnected.
- No exposed conductor can contact the printer frame or enclosure hardware.
- Wires cannot enter the paper path or touch the hot print head.
- The webcam has a clear view through the front opening.
- The M3 inserts are flush and the caphead screws do not bind.
