from pathlib import Path

import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from config.settings import ROI, ROIConfig
from src.capture.screen_capture import crop_rois

RESOLUTIONS = [(1280, 720), (1920, 1080), (2560, 1440), (3840, 2160)]


def _synthetic_screen(width: int, height: int) -> np.ndarray:
    """Tela com um 'radar' desenhado em coordenadas relativas, como faz o HUD do jogo."""
    screen = np.zeros((height, width, 3), np.uint8)
    radar = ROIConfig().radar.to_pixels(width, height)
    cv2.rectangle(screen, radar[:2], radar[2:], (0, 120, 0), -1)
    center = (round(0.5 * width), round(0.85 * height))
    cv2.circle(screen, center, max(2, height // 100), (255, 255, 255), -1)
    return screen


@pytest.mark.parametrize(("width", "height"), RESOLUTIONS)
def test_radar_crop_is_resolution_invariant(width: int, height: int) -> None:
    reference = cv2.resize(
        crop_rois(_synthetic_screen(1920, 1080), ROIConfig())["radar"], (200, 110)
    )
    crop = cv2.resize(crop_rois(_synthetic_screen(width, height), ROIConfig())["radar"], (200, 110))
    # Diferença média abaixo de 2% da escala: mesmo conteúdo, só reamostrado.
    assert np.abs(crop.astype(int) - reference.astype(int)).mean() < 5


def test_pixels_round_trip() -> None:
    box = (760, 820, 1160, 1040)
    assert ROI.from_pixels(box, 1920, 1080).to_pixels(1920, 1080) == box


def test_rejects_inverted_roi() -> None:
    with pytest.raises(ValidationError):
        ROI(left=0.6, top=0.1, right=0.4, bottom=0.2)


def test_save_and_load(tmp_path: Path) -> None:
    path = tmp_path / "rois.json"
    custom = ROIConfig(radar=ROI(left=0.1, top=0.1, right=0.3, bottom=0.3))
    custom.save(path)
    assert ROIConfig.load(path) == custom
    assert ROIConfig.load(tmp_path / "inexistente.json") == ROIConfig()
