# Grainline

Grainline opens a sliced G-code file and writes a new one where the hotend temperature moves between a low and a high you choose. Wood filament gets lighter and darker as the temperature changes, so the print picks up a grain instead of one flat color.

The original file is left alone. The new file is saved next to it, with `_grainline` added to the name.

## Download

The Windows app is a single file: [Grainline.exe on the latest release](https://github.com/epjarv/grainline/releases/latest).

The first time you open it, Windows may take a few seconds while it checks the new file.

## Use it

1. Slice the model and export G-code. In Bambu Studio use **File > Export > Export plate sliced file**, which saves a `.gcode.3mf`.
2. Open that file in Grainline. You can browse, drop the file on the window, or drop it on `Grainline.exe`.
3. Set the low and high temperature. Classic is 210–240 °C. Use the range printed on your spool.
4. Check the preview. Pale is cooler, dark is hotter.
5. Click **Make grain file**. For a Bambu file, import the new `.gcode.3mf` back into Bambu Studio.

### What to adjust

- **Grain width** is how tall one pale-to-dark cycle is. A smaller width means more bands on the print.
- **New temperature every** is how often a new temperature is sent. Every layer lets the color drift. A larger gap holds each shade and looks more like stripes.
- **By time** does the same thing against print time instead of layers. Use it for vase mode, which is often a single layer.
- **Variation** and **Shuffle** keep the pattern from looking mechanical. The pattern number can be reused if you want the same grain again.
- **Drift while printing** (the default) sends `M104`, so the printer keeps moving while the hotend catches up. That lag is the soft grain. **Pause until hot** sends `M109` and waits at each change. The stripes are harder and the print takes longer.

If the preview barely moves, the cycle is long compared with the print. Shorten the grain width.

## Bambu Studio

Bambu Studio does not export a plain `.gcode` file. Export a sliced plate as `.gcode.3mf`, open that in Grainline, then import the new `.gcode.3mf` with **File > Import > Import plate sliced file**. The model, thumbnails, and other plates stay in the file. Only the G-code temperatures change.

If a file has more than one plate, choose one plate or update all of them.

## Slicers

Layer comments from PrusaSlicer, OrcaSlicer, Bambu Studio, Cura, IdeaMaker, and Simplify3D are picked up automatically. If a file has no layer comments, Grainline uses Z height changes instead.

Warmup commands before the first layer, and cooldown commands such as `M104 S0` at the end, stay in the file. Temperature lines inside the print are set aside so they don't cancel the grain. Bed temperatures are never changed.

## Build from source

```powershell
python -m pip install -r requirements.txt -r requirements-build.txt
python -m unittest discover -s tests
python scripts/make_icon.py
python -m PyInstaller --noconfirm Grainline.spec
```

`scripts/build.ps1` does the install, icon, and EXE build. The app is `dist/Grainline.exe`.

To run the window without packaging:

```powershell
$env:PYTHONPATH = "src"
python -m pip install -r requirements.txt
python -m grainline
```

A file can also be processed from the command line:

```powershell
$env:PYTHONPATH = "src"
python -m grainline model.gcode --min 210 --max 240 --spacing 36
```
