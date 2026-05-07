from __future__ import annotations

"""Pozisyon odaklı yardımcı logic. Ana reconcile akışı ile ilişkilidir."""


"""
PositionManager

Amaç:
- Position lifecycle kontrolü
- Scale-in güvenliği
- Future TP/SL hook noktası

Bu layer:
Execution ile DB arasına girmez,
sadece decision helper olarak çalışır.
"""


class PositionManager:
    def __init__(self, settings):
        # settings burada eşik ve temel davranışları okumak için tutulur.
        self.settings = settings

    def can_scale_in(self, position: dict, last_price: float) -> tuple[bool, str]:
        """
        Scale-in kontrolü

        Kritik kural:
        ✔ Duserken ekleme VAR
        ❌ Yukselirken ekleme YOK

        Bu fonksiyon:
        - fiyat ortalama maliyetin altinda mi?
        - degilse scale-in bloklar
        """

        if not position:
            return False, "no_position"

        avg = float(position.get("avg_entry_price") or 0.0)

        if avg <= 0:
            return False, "invalid_avg"

        if last_price <= 0:
            return False, "invalid_last_price"

        if last_price >= avg:
            return False, "scale_in_only_on_pullback"

        return True, "ok"

    def get_position_state(self, position: dict | None) -> str:
        """
        Position state üretir

        Şimdilik basit:
        - OPEN / CLOSED

        Future:
        - ADDING
        - REDUCING
        """

        if not position:
            return "CLOSED"

        qty = float(position.get("qty") or 0.0)

        if qty > 1e-12:
            return "OPEN"

        return "CLOSED"
