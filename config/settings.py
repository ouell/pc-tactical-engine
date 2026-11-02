"""Configurações globais do FC-Tactical-Engine (resoluções, thresholds, ROI)."""

from typing import Tuple

# Resolução base de referência (Full HD 1080p)
TARGET_RESOLUTION: Tuple[int, int] = (1920, 1080)
TARGET_FPS: int = 60

# Região de Interesse (ROI) padrão do Radar 2D em 1080p: (left, top, right, bottom)
DEFAULT_RADAR_ROI: Tuple[int, int, int, int] = (760, 820, 1160, 1040)
