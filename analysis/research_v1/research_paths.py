"""Shared research artifact paths, configurable without editing learning code."""
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
_desktop_output=Path(r'C:\Users\admin\Documents\Codex\2026-11-01\new-chat\outputs')
_default=_desktop_output if _desktop_output.exists() else ROOT/'analysis_output/research_exports'
EXPORT=Path(os.environ.get('NETWORK_TRACKER_RESEARCH_OUTPUT',str(_default))).expanduser().resolve()
