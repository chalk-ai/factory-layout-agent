"""The plant the agent designs into: one building, its fixed features, and an equipment catalog.

Everything here is fictional and simplified. Coordinates are meters from the building's
south-west corner, x to the east and y to the north. A station's (x, y) is the south-west corner
of its footprint; `rotated` swaps its width and depth.
"""

from __future__ import annotations

from typing import Any

BUILDING = {"width": 72.0, "depth": 40.0}

# A 4 m forklift aisle runs the full length of the building, splitting it into a south band
# (y 0-18) and a north band (y 22-40).
AISLE = {"name": "main aisle", "x0": 0.0, "y0": 18.0, "x1": 72.0, "y1": 22.0}

# Structural columns, 0.6 m square, on a 12 m grid.
COLUMN_SIZE = 0.6
COLUMNS = [
    {"name": f"C{i + 1}{row}", "x": float(x), "y": float(y)}
    for row, y in (("S", 9), ("N", 31))
    for i, x in enumerate((12, 24, 36, 48, 60))
]

# Personnel exit doors; the 2.4 m x 2 m zone inside each must stay clear.
EXITS = [
    {"name": "south exit A", "x0": 22.8, "y0": 0.0, "x1": 25.2, "y1": 2.0},
    {"name": "south exit B", "x0": 46.8, "y0": 0.0, "x1": 49.2, "y1": 2.0},
    {"name": "north exit", "x0": 10.8, "y0": 38.0, "x1": 13.2, "y1": 40.0},
]

# Truck doors. A receiving station must sit against the west wall facing these doors, a shipping
# station against the east wall.
DOCKS = {
    "receiving": {"wall": "west", "y0": 23.0, "y1": 35.0, "within": 2.0},
    "shipping": {"wall": "east", "y0": 3.0, "y1": 15.0, "within": 2.0},
}

# Roof exhaust stacks run along the north wall only; equipment that needs exhaust must touch it.
EXHAUST_WALL = "north"
EXHAUST_WITHIN = 0.5

# Edge-to-edge separation, in meters, between hazard classes, in at least one axis.
HAZARD_RULES = [
    {"a": "flammable", "b": "dust", "min_gap": 6.0, "why": "combustible dust near solvent vapor"},
    {"a": "flammable", "b": "sparks", "min_gap": 6.0, "why": "sparks near solvent vapor"},
    {"a": "flammable", "b": "hot", "min_gap": 3.0, "why": "hot surfaces near solvent vapor"},
]

# Material moves between consecutive process steps by conveyor, cart or forklift: every station
# of a step must have a station of each following step within this center-to-center distance.
MAX_TRANSFER_M = 20.0

# width x depth in meters, `access` is the operator/maintenance zone kept clear on every side,
# `uph` is units per hour of the line's product through one machine. Docks move whole pallets, so
# their uph is high enough never to bound a line.
EQUIPMENT: dict[str, dict[str, Any]] = {
    "receiving_bay": {
        "name": "Receiving bay with staging racks",
        "w": 8.0,
        "d": 6.0,
        "access": 0.5,
        "uph": 20000,
        "tags": ["receive"],
    },
    "shipping_bay": {
        "name": "Shipping bay with pallet staging",
        "w": 8.0,
        "d": 6.0,
        "access": 0.5,
        "uph": 20000,
        "tags": ["ship"],
    },
    "panel_saw": {
        "name": "Vertical panel saw (sheet goods)",
        "w": 6.0,
        "d": 3.0,
        "access": 1.0,
        "uph": 70,
        "tags": ["dust"],
    },
    "cnc_router": {
        "name": "CNC router (shaping, radiused corners)",
        "w": 5.0,
        "d": 3.0,
        "access": 1.0,
        "uph": 40,
        "tags": ["dust"],
    },
    "wide_belt_sander": {
        "name": "Wide-belt sander",
        "w": 4.0,
        "d": 3.0,
        "access": 1.0,
        "uph": 90,
        "tags": ["dust"],
    },
    "spray_booth": {
        "name": "Spray booth (primer or chalkboard coating)",
        "w": 7.0,
        "d": 5.0,
        "access": 1.0,
        "uph": 40,
        "tags": ["flammable", "exhaust"],
    },
    "roll_coater": {
        "name": "Roll coater (coating sheet stock)",
        "w": 6.0,
        "d": 2.5,
        "access": 1.0,
        "uph": 120,
        "tags": ["flammable", "exhaust"],
    },
    "flash_off_tunnel": {
        "name": "Flash-off tunnel",
        "w": 6.0,
        "d": 2.5,
        "access": 0.5,
        "uph": 120,
        "tags": [],
    },
    "ir_cure_oven": {
        "name": "Infrared cure oven (conveyorized)",
        "w": 10.0,
        "d": 3.0,
        "access": 1.0,
        "uph": 80,
        "tags": ["hot"],
    },
    "cooling_conveyor": {
        "name": "Cooling conveyor",
        "w": 6.0,
        "d": 2.0,
        "access": 0.5,
        "uph": 120,
        "tags": [],
    },
    "extrusion_saw": {
        "name": "Aluminum miter saw (frame extrusions)",
        "w": 6.0,
        "d": 2.0,
        "access": 1.0,
        "uph": 50,
        "tags": ["sparks"],
    },
    "corner_crimper": {
        "name": "Frame corner crimper",
        "w": 3.0,
        "d": 2.0,
        "access": 0.5,
        "uph": 60,
        "tags": [],
    },
    "assembly_bench": {
        "name": "Framing and assembly bench",
        "w": 4.0,
        "d": 2.0,
        "access": 0.75,
        "uph": 30,
        "tags": [],
    },
    "hardware_press": {
        "name": "Hanger and tray rivet press",
        "w": 2.0,
        "d": 2.0,
        "access": 0.5,
        "uph": 120,
        "tags": [],
    },
    "qc_station": {
        "name": "Write/erase QC station with light table",
        "w": 4.0,
        "d": 3.0,
        "access": 0.75,
        "uph": 60,
        "tags": [],
    },
    "packaging_cell": {
        "name": "Packaging cell (boxing, corner guards, strapping)",
        "w": 6.0,
        "d": 4.0,
        "access": 0.75,
        "uph": 75,
        "tags": [],
    },
    "powder_mixer": {
        "name": "Powder mixer (calcium carbonate or gypsum slurry)",
        "w": 4.0,
        "d": 4.0,
        "access": 1.0,
        "uph": 3000,
        "tags": ["dust"],
    },
    "chalk_extruder": {
        "name": "Chalk extruder",
        "w": 6.0,
        "d": 2.0,
        "access": 0.75,
        "uph": 1500,
        "tags": [],
    },
    "stick_cutter": {
        "name": "Stick cutter",
        "w": 3.0,
        "d": 2.0,
        "access": 0.5,
        "uph": 3000,
        "tags": [],
    },
    "drying_kiln": {
        "name": "Drying kiln",
        "w": 10.0,
        "d": 4.0,
        "access": 1.0,
        "uph": 2500,
        "tags": ["hot"],
    },
    "dip_coater": {
        "name": "Anti-dust dip coater",
        "w": 4.0,
        "d": 2.0,
        "access": 0.75,
        "uph": 2000,
        "tags": ["flammable", "exhaust"],
    },
    "stick_packer": {
        "name": "Stick counter and box packer",
        "w": 5.0,
        "d": 3.0,
        "access": 0.75,
        "uph": 2400,
        "tags": [],
    },
    "felt_slitter": {
        "name": "Felt slitter",
        "w": 4.0,
        "d": 2.0,
        "access": 0.75,
        "uph": 400,
        "tags": ["dust"],
    },
    "block_saw": {
        "name": "Wood block saw",
        "w": 5.0,
        "d": 2.0,
        "access": 1.0,
        "uph": 350,
        "tags": ["dust"],
    },
    "glue_press": {
        "name": "Hot-melt glue press",
        "w": 3.0,
        "d": 2.0,
        "access": 0.75,
        "uph": 200,
        "tags": ["hot"],
    },
}

PRESETS = [
    {
        "id": "chalkboard",
        "label": "Chalkboards",
        "prompt": "Set up a manufacturing line for a chalkboard.",
    },
    {
        "id": "chalkboard-90",
        "label": "Chalkboards, 90/h",
        "prompt": "Set up a line for framed chalkboards at 90 boards an hour. Primer and chalkboard "
        + "coat are separate spray coats.",
    },
    {
        "id": "chalk-sticks",
        "label": "Chalk sticks",
        "prompt": "Set up a manufacturing line for chalk sticks, 3,000 sticks an hour.",
    },
    {
        "id": "erasers",
        "label": "Erasers",
        "prompt": "Set up a manufacturing line for felt chalkboard erasers, 300 an hour.",
    },
]


def site_description() -> dict[str, Any]:
    """Everything the agent needs to know about the building, as returned by `get_site`."""
    return {
        "units": "meters; origin at the south-west corner; x east, y north; a station's x,y is "
        + "the south-west corner of its footprint",
        "building": BUILDING,
        "aisle": AISLE,
        "columns": {"size": COLUMN_SIZE, "centers": COLUMNS},
        "exits": EXITS,
        "docks": DOCKS,
        "exhaust": f"roof exhaust only along the {EXHAUST_WALL} wall: equipment tagged 'exhaust' "
        + f"must have its footprint within {EXHAUST_WITHIN} m of that wall",
        "hazard_separation": HAZARD_RULES,
        "max_transfer_m": MAX_TRANSFER_M,
        "rules": [
            "Footprints stay inside the building.",
            "Access zones (footprint grown by each machine's `access` on every side) may not "
            + "overlap each other, the main aisle, or an exit's clear zone.",
            "Footprints may not touch a column.",
            "Hazard classes keep the listed edge-to-edge gap in at least one axis.",
            "Equipment tagged 'exhaust' touches the north wall.",
            "Receiving stations sit within 2 m of the west wall, overlapping the receiving "
            + "doors; shipping stations within 2 m of the east wall, overlapping the shipping "
            + "doors.",
            "Every station of a step has a station of each following step within "
            + f"{MAX_TRANSFER_M:g} m, center to center, Manhattan distance.",
            "The machines on each step together meet the target rate (sum of uph).",
        ],
    }


def equipment_list(category: str | None = None) -> list[dict[str, Any]]:
    out = []
    for key, eq in EQUIPMENT.items():
        if category and category.lower() not in (key + " " + eq["name"]).lower():
            continue
        out.append({"type": key, **eq})
    return out
