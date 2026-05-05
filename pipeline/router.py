from models.contact import EnrichedContact, RoutingOutcome


def route_ec(contact: EnrichedContact) -> RoutingOutcome:
    """Grant Ellis Group contacts the landlord."""
    property_type = (contact.property_type or "").strip().lower()
    if property_type in {"commercial", "retail", "office"}:
        return RoutingOutcome(action="proceed", tag="Commercial", pipeline="commercial")
    if contact.estimated_rent is not None and contact.estimated_rent < 1800:
        return RoutingOutcome(action="skip", tag="Below-Threshold")
    return RoutingOutcome(action="proceed", tag="EC-New-Filing", pipeline="residential")


def route_ng(contact: EnrichedContact) -> RoutingOutcome:
    """Vantage Defense Group contacts the tenant."""
    property_type = (contact.property_type or "").strip().lower()
    if property_type in {"commercial", "retail", "office"}:
        return RoutingOutcome(action="proceed", tag="NG-New-Filing", pipeline="commercial")
    return RoutingOutcome(action="proceed", tag="NG-New-Filing", pipeline="residential")
