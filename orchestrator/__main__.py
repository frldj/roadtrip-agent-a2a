"""
CLI de l'orchestrateur.

Lancer les agents dans des terminaux séparés avant d'utiliser le CLI :
    python -m route_agent          # port 9001
    python -m vehicle_agent        # port 9002
    python -m accommodation_agent  # port 9003 (optionnel)

Exemples :
    python -m orchestrator --origin Paris --destination Barcelone \\
        --waypoint Lyon --vehicle electric --battery-kwh 60 --consumption 17

    python -m orchestrator --origin Paris --destination Nice \\
        --vehicle thermal --accommodation camping --format json
"""
from __future__ import annotations

import asyncio
import json

import click
from dotenv import load_dotenv

load_dotenv()

from common.schemas import AccommodationType, RoadtripPlan, RoadtripRequest, VehicleType
from orchestrator.core import plan_roadtrip


def _hm(minutes: float) -> str:
    h, m = int(minutes // 60), int(minutes % 60)
    return f"{h}h{m:02d}"


def format_plan(plan: RoadtripPlan, req: RoadtripRequest) -> str:
    sep = "─" * 54
    lines: list[str] = []

    waypoints_str = " → ".join(req.waypoints) if req.waypoints else ""
    route_str = (
        f"{req.origin} → {waypoints_str} → {req.destination}"
        if waypoints_str
        else f"{req.origin} → {req.destination}"
    )
    vt_label = "Électrique" if req.vehicle_type == VehicleType.ELECTRIC else "Thermique"
    days = len(plan.route.segments)

    lines += [
        "╔" + "═" * 54 + "╗",
        f"║  PLAN DE ROADTRIP : {route_str[:32]:<32}  ║",
        f"║  {vt_label} · {days} jour{'s' if days > 1 else ''} · {plan.route.total_distance_km:.0f} km{'':<14}  ║",
        "╚" + "═" * 54 + "╝",
        "",
    ]

    # Itinéraire
    lines.append("ITINÉRAIRE")
    lines.append(sep)
    for seg in plan.route.segments:
        lines.append(
            f"  Jour {seg.day_index:2d}  {seg.start_location} → {seg.end_location}"
        )
        lines.append(
            f"          {seg.distance_km:.0f} km  ·  {_hm(seg.duration_minutes)}"
        )
    lines.append(
        f"  Total   {plan.route.total_distance_km:.0f} km  ·  {_hm(plan.route.total_duration_minutes)}"
    )
    lines.append("")

    # Énergie / recharge
    if plan.charging:
        vt = "Électrique" if plan.charging.vehicle_type == VehicleType.ELECTRIC else "Thermique"
        lines.append(f"ÉNERGIE ({vt})")
        lines.append(sep)
        if plan.charging.fuel_or_charge_stops:
            for stop in plan.charging.fuel_or_charge_stops:
                lines.append(
                    f"  Jour {stop.day_index:2d}  {stop.location_hint}"
                )
                lines.append(
                    f"          {stop.charge_from_percent:.0f}% → {stop.charge_to_percent:.0f}%"
                    f"  ·  {stop.estimated_charging_minutes:.0f} min"
                )
                if stop.station_provider_hint:
                    lines.append(f"          {stop.station_provider_hint}")
        elif plan.charging.vehicle_type == VehicleType.ELECTRIC and plan.charging.feasible:
            lines.append("  ✓  Autonomie suffisante, aucun arrêt de recharge nécessaire.")
        for warning in plan.charging.warnings:
            lines.append(f"  ℹ  {warning}")
        lines.append("")

    # Hébergement
    if plan.accommodation and plan.accommodation.options:
        lines.append("HÉBERGEMENT")
        lines.append(sep)
        by_day: dict[int, list] = {}
        for opt in plan.accommodation.options:
            by_day.setdefault(opt.day_index, []).append(opt)
        for day_idx in sorted(by_day):
            opts = by_day[day_idx]
            lines.append(f"  Jour {day_idx:2d}")
            for opt in opts:
                stars = f" ★{opt.rating:.0f}" if opt.rating else ""
                type_label = "Camping" if opt.type == AccommodationType.CAMPING else "Hôtel"
                if opt.price_min_eur and opt.price_max_eur:
                    price = f"  {opt.price_min_eur:.0f}–{opt.price_max_eur:.0f} €/nuit"
                elif opt.estimated_price_eur:
                    price = f"  ~{opt.estimated_price_eur:.0f} €/nuit"
                else:
                    price = ""
                lines.append(f"    • {opt.name}{stars}  ({type_label}){price}")
                if opt.location_hint:
                    lines.append(f"      {opt.location_hint}")
        lines.append("")

    return "\n".join(lines)


@click.command()
@click.option("--origin", required=True, help="Ville de départ")
@click.option("--destination", required=True, help="Ville d'arrivée")
@click.option("--waypoint", "waypoints", multiple=True, help="Étape (répétable)")
@click.option(
    "--vehicle",
    type=click.Choice(["electric", "thermal"]),
    default="thermal",
    show_default=True,
)
@click.option("--battery-kwh", type=float, default=None, help="Capacité batterie kWh (électrique)")
@click.option("--consumption", type=float, default=None, help="Consommation kWh/100km (électrique)")
@click.option("--max-hours-per-day", type=float, default=6.0, show_default=True)
@click.option(
    "--accommodation",
    type=click.Choice(["hotel", "camping", "no_preference"]),
    default="no_preference",
    show_default=True,
)
@click.option("--budget", type=float, default=None, help="Budget hébergement €/nuit")
@click.option("--start-date", default=None, help="Date de départ ISO (ex: 2026-08-10)")
@click.option(
    "--tesla-supercharger",
    "tesla_supercharger_only",
    is_flag=True,
    default=False,
    help="Rechercher uniquement des Superchargeurs Tesla (électrique)",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
    help="Format de sortie",
)
def main(
    origin: str,
    destination: str,
    waypoints: tuple[str, ...],
    vehicle: str,
    battery_kwh: float | None,
    consumption: float | None,
    max_hours_per_day: float,
    accommodation: str,
    budget: float | None,
    start_date: str | None,
    tesla_supercharger_only: bool,
    output_format: str,
) -> None:
    req = RoadtripRequest(
        origin=origin,
        destination=destination,
        waypoints=list(waypoints),
        vehicle_type=VehicleType(vehicle),
        battery_capacity_kwh=battery_kwh,
        consumption_kwh_per_100km=consumption,
        max_driving_hours_per_day=max_hours_per_day,
        accommodation_type=AccommodationType(accommodation),
        budget_per_night_eur=budget,
        start_date=start_date,
        tesla_supercharger_only=tesla_supercharger_only,
    )
    plan = asyncio.run(plan_roadtrip(req))

    if output_format == "json":
        click.echo(json.dumps(plan.model_dump(), indent=2, ensure_ascii=False))
    else:
        click.echo(format_plan(plan, req))


if __name__ == "__main__":
    main()
