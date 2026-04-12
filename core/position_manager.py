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
        ❌ Loser'a ekleme YOK
        ✔ Winner'a ekleme VAR

        Bu fonksiyon:
        - mevcut pozisyon kârlı mı?
        - değilse scale-in bloklar
        """

        if not position:
            return False, "no_position"

        avg = float(position.get("avg_entry_price") or 0.0)

        if avg <= 0:
            return False, "invalid_avg"

        # 🔴 CORE LOGIC
        if last_price < avg:
            # Fiyat ortalama maliyetin altındaysa zarardayız.
            # Bu tasarıma göre zarar eden pozisyona ekleme yapmıyoruz.
            return False, "no_scale_in_on_loser"

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
