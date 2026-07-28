"""
Logique de validation d'un plan de roadtrip.

Vérifie la cohérence du plan assemblé par l'orchestrateur :
- durée de conduite par jour
- faisabilité de la recharge
- couverture hébergement
- avertissements d'autonomie insuffisante
"""
from __future__ import annotations

from common.schemas import (
    PlanValidationRequest,
    PlanValidationResponse,
    ValidationIssue,
)


def validate_plan(req: PlanValidationRequest) -> PlanValidationResponse:
    issues: list[ValidationIssue] = []
    max_min = req.max_driving_hours_per_day * 60.0

    # ── Durée de conduite par jour ────────────────────────────────────────────
    for seg in req.segments:
        if seg.duration_minutes > max_min * 1.15:
            overshoot = seg.duration_minutes - max_min
            issues.append(ValidationIssue(
                severity="warning",
                code="SEGMENT_TOO_LONG",
                message=(
                    f"Jour {seg.day_index} : {seg.duration_minutes / 60:.1f} h de conduite "
                    f"(+{overshoot / 60:.1f} h au-delà de la limite)."
                ),
                day_index=seg.day_index,
            ))

    # ── Faisabilité de la recharge ────────────────────────────────────────────
    if not req.charging_feasible:
        issues.append(ValidationIssue(
            severity="error",
            code="CHARGING_INFEASIBLE",
            message="Le vehicle_agent a signalé que la recharge est impossible sur ce trajet.",
        ))

    for w in req.charging_warnings:
        if "autonomie insuffisante" in w.lower():
            issues.append(ValidationIssue(
                severity="error",
                code="RANGE_INSUFFICIENT",
                message=w,
            ))
        elif "route_agent" in w.lower() or "route_correction" in w.lower():
            issues.append(ValidationIssue(
                severity="info",
                code="ROUTE_CORRECTED",
                message=w,
            ))

    # ── Couverture hébergement ────────────────────────────────────────────────
    # Tous les jours sauf le dernier nécessitent un hébergement
    overnight_days = {seg.day_index for seg in req.segments[:-1]}
    missing = overnight_days - set(req.days_with_accommodation)
    for day in sorted(missing):
        if day not in req.days_needing_accommodation:
            continue
        issues.append(ValidationIssue(
            severity="warning",
            code="ACCOMMODATION_MISSING",
            message=f"Jour {day} : aucun hébergement trouvé pour la nuit.",
            day_index=day,
        ))

    errors = [i for i in issues if i.severity == "error"]
    return PlanValidationResponse(valid=len(errors) == 0, issues=issues)
