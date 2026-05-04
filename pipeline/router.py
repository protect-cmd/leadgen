from models.contact import EnrichedContact, RoutingOutcome


def route_ec(contact: EnrichedContact) -> RoutingOutcome:
    """EvictionCommand — contacts the landlord."""
    if contact.estimated_rent is None or contact.property_type is None:
        return RoutingOutcome(action="flag", tag="Missing-Data")
    if contact.estimated_rent < 1800:
        return RoutingOutcome(action="skip", tag="Below-Threshold")
    return RoutingOutcome(action="proceed", tag="EC-New-Filing", pipeline="residential")


def route_ng(contact: EnrichedContact) -> RoutingOutcome:
    """Nobles & Greyson — contacts the tenant."""
    if contact.property_type == "commercial":
        return RoutingOutcome(action="proceed", tag="NG-New-Filing", pipeline="commercial")
    return RoutingOutcome(action="proceed", tag="NG-New-Filing", pipeline="residential")
