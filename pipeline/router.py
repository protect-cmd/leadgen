from models.contact import EnrichedContact, RoutingOutcome


def route(contact: EnrichedContact) -> RoutingOutcome:
    if contact.property_type == "commercial":
        return RoutingOutcome(action="proceed", tag="NG-New-Filing", pipeline="commercial")

    if contact.estimated_rent is None or contact.property_type is None:
        return RoutingOutcome(action="flag", tag="Missing-Data")

    if contact.estimated_rent < 1800:
        return RoutingOutcome(action="skip", tag="Below-Threshold")

    return RoutingOutcome(action="proceed", tag="EC-New-Filing", pipeline="residential")
