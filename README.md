# Xbox Controller Tweaks

A focused, free controller-remapping utility for Windows. It creates a virtual Xbox 360 controller and lets you tune stick response curves, deadzones, trigger behavior, axis multipliers, inversion, and rumble forwarding.

I created it primarily to make steering in games such as Forza easier to control. A custom response curve can provide fine movement around the center while still preserving full steering near the edge.

This is a focused alternative for axis tuning, not a complete replacement for every reWASD feature. It does not currently provide macros, keyboard mapping, combo systems, or automatic per-game profile switching.

## Features

- Independent response curves for both axes of both sticks
- Separate trigger curves
- Multiplier mode from 0.1× to 3.0×
- Configurable left-stick, right-stick, and trigger deadzones
- Optional Y-axis inversion
- Live raw/output input visualizer
- Physical-to-virtual rumble forwarding and intensity control
- Four quick profile slots
- JSON profile import and export
- System-tray operation
- Detailed input and diagnostic log
- English interface throughout

## Requirements

- Windows 10 or Windows 11, 64-bit
- Python 3.10–3.13 x64; Python 3.12 is recommended
- A controller recognized by pygame/SDL
- ViGEmBus, required for the virtual Xbox 360 controller
- HidHide, optional but recommended if a game detects both the physical and virtual controller

The Python packages are installed locally into `.venv`; they are not copied into your global Python installation.

## Installation

1. Download and extract the release ZIP.
2. Install [Python 3.12 x64](https://www.python.org/downloads/windows/) if it is not already available.
3. Run `Setup.bat`.
4. The setup creates a local environment and installs the packages listed in `requirements.txt`.
5. If ViGEmBus is missing, setup offers the [official v1.22.0 release page](https://github.com/nefarius/ViGEmBus/releases/tag/v1.22.0).
6. Install ViGEmBus, then run `Start Xbox Controller Tweaks.bat`.

Driver installation may require administrator approval and a restart. The setup script does not silently install or alter system drivers.

## Basic use

1. Connect the physical controller.
2. Start the application and select it under **Input device**.
3. Adjust deadzones, multipliers, or curve control points.
4. Use **Raw / Output** in the visualizer to compare physical input with the transformed result.
5. Select **Start** to create and feed the virtual Xbox 360 controller.
6. Configure the game to use the virtual controller.

Each curve maps input magnitude from 0 to 1 onto an output magnitude from 0 to 1. Double-click the graph to add a point, drag points to adjust the response, right-click a point to remove it, or use Copy/Paste between axes.

[Detailed documentation](README-DETAILS.md) · [All releases](https://github.com/FajkPes/Xbox-Controller-Tweaks/releases)

## Preventing double input with HidHide

Some games see both the real controller and the virtual Xbox controller. If that happens:

1. Install HidHide from the [official repository](https://github.com/nefarius/HidHide/releases).
2. Open the HidHide Configuration Client.
3. On **Applications**, add this release's `.venv\Scripts\pythonw.exe` so the remapper may still read the physical controller.
4. On **Devices**, select the physical controller that should be hidden from games.
5. Enable device hiding and reconnect the controller if required.

Do not hide the virtual Xbox 360 controller. Review the selections carefully so you do not lock another controller utility out of its device.

## Example profiles

The `examples` folder contains two profiles recovered from the latest project folder:

- `Forza-Steering-Profile.json` — a more sensitive initial left-stick response with a restrained middle range.
- `All-Sticks-Curve-Profile.json` — applies shaped curves to both sticks while leaving triggers linear.

Import them with **Import profile**. Treat them as starting points and adjust them for your controller, game, and preferred steering feel.

## Credits

**Idea, concept and design:** FajkPes  
**Coding:** ChatGPT Codex, Claude and Google Antigravity
