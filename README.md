# ACP LabelImg - Custom Image Annotation Tool

Custom version of LabelImg with enhanced features for efficient image annotation.

## Repository Structure

```
acp-labelimg-repo/
├── labelImg/         # Main application package
│   ├── labelImg.py   # Main application file
│   └── __init__.py
└── libs/             # Supporting library package
    ├── utils.py      # Utilities (bright color generation)
    ├── canvas.py     # Drawing canvas
    ├── shape.py      # Bounding box shapes
    └── ...           # Other supporting modules
```

## New Features

- **Color Palette System:** View → Color Palette (Ctrl+L) - customize colors per class
- **Bright Color Scheme:** Auto-generated colors use vivid HSV palette
- **Auto Annotate Button:** Placeholder for future ML integration
- **Quick Delete:** Press Space to delete selected box(es)
- **Simplified UI:** Streamlined toolbar with essential tools only
- **Auto-Update:** Pulls latest changes from git on startup

## Installation

Deploy to your Python site-packages directory:
```
site-packages/
├── labelImg/  (from this repo)
└── libs/      (from this repo)
```

## Usage

Default format: YOLO
- Classes loaded from `classes.txt` in image directory
- Status bar shows only version number
- All colors customizable via Color Palette

## Changes Summary

- Removed unnecessary buttons (Next/Prev, Create, Duplicate, etc.)
- Default format locked to YOLO
- Fixed class visibility list population from classes.txt
- Bright, high-saturation color palette (86-100% brightness)
- Disabled status bar messages (version only)
- Delete shortcut: Space key
