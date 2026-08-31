import importlib.util
import json
import sys
import types
from pathlib import Path


class _Buttons:
    XUSB_GAMEPAD_A = 1
    XUSB_GAMEPAD_B = 2
    XUSB_GAMEPAD_X = 4
    XUSB_GAMEPAD_Y = 8
    XUSB_GAMEPAD_LEFT_SHOULDER = 16
    XUSB_GAMEPAD_RIGHT_SHOULDER = 32
    XUSB_GAMEPAD_BACK = 64
    XUSB_GAMEPAD_START = 128
    XUSB_GAMEPAD_LEFT_THUMB = 256
    XUSB_GAMEPAD_RIGHT_THUMB = 512
    XUSB_GAMEPAD_GUIDE = 1024


fake_vgamepad = types.ModuleType("vgamepad")
fake_vgamepad.XUSB_BUTTON = _Buttons
sys.modules["vgamepad"] = fake_vgamepad

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("xbox_controller_tweaks", root / "xbox_controller_tweaks.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.eval_curve([[0.0, 0.0], [1.0, 1.0]], 0.5) == 0.5
assert module.apply_deadzone(0.01, 0.02) == 0.0
assert module.apply_deadzone(0.03, 0.02) == 0.03

default = module.default_config()
snapshot = module.config_to_snapshot(default)
assert len(snapshot.lut_left_x) == 256
assert snapshot.lut_left_x[0] == 0.0
assert snapshot.lut_left_x[-1] == 1.0

for profile_path in sorted((root / "examples").glob("*.json")):
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    merged = {**module.default_config(), **profile}
    tested = module.config_to_snapshot(merged)
    assert len(tested.lut_left_x) == 256
    assert all(0.0 <= point[0] <= 1.0 and 0.0 <= point[1] <= 1.0 for point in merged["curve_left_x"])

print("XBOX_CONTROLLER_TWEAKS_CORE_TEST_OK")

# Created by FajkPes with assistance from ChatGPT Codex and Claude Code.
