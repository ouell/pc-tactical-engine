"""Decide qual evento tático vira alerta falado, e quando."""

import math
from dataclasses import dataclass

from config.settings import CoachConfig
from src.models import EventType, FrameTelemetry, TacticalEvent

MESSAGES: dict[EventType, str] = {
    EventType.CENTRO_EXPOSTO: "Centro aberto, fecha a zaga!",
    EventType.PASSE_RUPTURA: "Passe livre na frente!",
    EventType.TIME_ESPACADO: "Time espaçado, compacta!",
}
PRIORITY: tuple[EventType, ...] = tuple(MESSAGES)  # mais urgente primeiro
ATTACKING: frozenset[EventType] = frozenset({EventType.PASSE_RUPTURA})


@dataclass(frozen=True, slots=True)
class Alert:
    type: EventType
    message: str
    timestamp: float


class CoachHeuristics:
    """Filtra eventos frame a frame: no máximo um alerta por chamada.

    - Debounce: o evento precisa aparecer em frames seguidos por `debounce_s`.
    - Cooldown: o mesmo tipo não repete antes de `cooldown_s`.
    - Intervalo global: nenhum alerta antes de `min_gap_s` do anterior.
    - Contexto: vencendo nos minutos finais, alertas de ataque são suprimidos.
    """

    def __init__(self, config: CoachConfig | None = None) -> None:
        self.config = config or CoachConfig()
        self._active_since: dict[EventType, float] = {}
        self._last_fired: dict[EventType, float] = {}
        self._last_alert = -math.inf

    def process(self, frame: FrameTelemetry, events: list[TacticalEvent]) -> Alert | None:
        now, cfg = frame.timestamp, self.config
        # Evento que sumiu reinicia o debounce.
        self._active_since = {e.type: self._active_since.get(e.type, now) for e in events}
        if now - self._last_alert < cfg.min_gap_s:
            return None

        for kind in PRIORITY:
            since = self._active_since.get(kind)
            if (
                since is None
                or now - since < cfg.debounce_s
                or now - self._last_fired.get(kind, -math.inf) < cfg.cooldown_s
                or (kind in ATTACKING and self._protecting_lead(frame))
            ):
                continue
            self._last_fired[kind] = self._last_alert = now
            return Alert(kind, MESSAGES[kind], now)
        return None

    def _protecting_lead(self, frame: FrameTelemetry) -> bool:
        winning = frame.score is not None and frame.score[0] > frame.score[1]
        late = frame.minute is not None and frame.minute >= self.config.late_minute
        return winning and late
