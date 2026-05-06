from __future__ import annotations

import re
import unicodedata

SPANISH_LIKELY = "spanish_likely"

_CORE_SURNAMES = {
    "ACOSTA", "AGUILAR", "AGUIRRE", "ALVAREZ", "ARIAS", "AVILA",
    "BAUTISTA", "BERMUDEZ", "BLANCO", "CABRERA", "CALDERON", "CAMPOS",
    "CARDENAS", "CASTILLO", "CERVANTES", "CHAVEZ", "CISNEROS", "CONTRERAS",
    "CORONADO", "CRUZ", "DE LA CRUZ", "DE LA ROSA", "DEL RIO", "DELGADO",
    "DIAZ", "DOMINGUEZ", "ELIZONDO", "ESCOBAR", "ESPINOZA", "ESTRADA",
    "FIGUEROA", "FLORES", "FONSECA", "FUENTES", "GALVAN", "GAONA",
    "GARCIA", "GOMEZ", "GONZALEZ", "GUERRERO", "GUTIERREZ", "GUZMAN",
    "HERNANDEZ", "HERRERA", "IBARRA", "INFANTE", "JIMENEZ", "JUAREZ",
    "LARA", "LEDESMA", "LEON", "LOPEZ", "LONGORIA", "LUNA", "MACIAS",
    "MALDONADO", "MARIN", "MARTINEZ", "MEDINA", "MEJIA", "MENDEZ",
    "MENDOZA", "MIRANDA", "MOLINA", "MONTOYA", "MORALES", "MORENO",
    "MUNOZ", "NAVA", "NAVARRO", "NEGRON", "NIETO", "NUNEZ", "OCHOA",
    "OROZCO", "ORTIZ", "PACHECO", "PADILLA", "PALACIOS", "PARRA", "PENA",
    "PERALTA", "PEREZ", "QUINTERO", "RAMIREZ", "RAMOS", "RANGEL", "REYES",
    "RIOS", "RIVAS", "RIVERA", "RODRIGUEZ", "ROJAS", "ROMERO", "RUIZ",
    "SALAS", "SALAZAR", "SANCHEZ", "SANDOVAL", "SANTIAGO", "SAUCEDO",
    "SEGURA", "SERRANO", "SOLIS", "SOTO", "SUAREZ", "TAPIA", "TORRES",
    "TREJO", "TREVINO", "TRUJILLO", "URIBE", "VALDEZ", "VALENCIA",
    "VARELA", "VARGAS", "VASQUEZ", "VEGA", "VELASQUEZ", "VELOZ",
    "VILLA", "VILLANUEVA", "ZAMORA", "ZARATE",
}


def _normalize_name(value: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z ]+", " ", ascii_name.upper()).strip()


def language_hint_for_name(full_name: str) -> str | None:
    normalized = _normalize_name(full_name)
    parts = normalized.split()
    if len(parts) < 2:
        return None

    surname = " ".join(parts[1:])
    surname_tail = parts[-1]
    if surname in _CORE_SURNAMES or surname_tail in _CORE_SURNAMES:
        return SPANISH_LIKELY
    return None
