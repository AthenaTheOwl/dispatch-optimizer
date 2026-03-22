"""City facility locations for simulation — real coordinates."""

from app.models import Location, FacilityType

# Hubs — major distribution centers
HUBS: list[Location] = [
    Location(lat=40.7394, lng=-73.9741, name="Hub Alpha", facility_type=FacilityType.HUB),
    Location(lat=40.7900, lng=-73.9526, name="Hub Bravo", facility_type=FacilityType.HUB),
    Location(lat=40.7645, lng=-73.9537, name="Hub Charlie", facility_type=FacilityType.HUB),
    Location(lat=40.7870, lng=-73.9430, name="Hub Delta", facility_type=FacilityType.HUB),
    Location(lat=40.8440, lng=-73.9392, name="Hub Echo", facility_type=FacilityType.HUB),
    Location(lat=40.7374, lng=-73.9817, name="Hub Foxtrot", facility_type=FacilityType.HUB),
    Location(lat=40.6601, lng=-73.9469, name="Hub Golf", facility_type=FacilityType.HUB),
    Location(lat=40.7490, lng=-73.8735, name="Hub Hotel", facility_type=FacilityType.HUB),
]

# Branches — spread across the city
BRANCHES: list[Location] = [
    Location(lat=40.7580, lng=-73.9855, name="Branch 1", facility_type=FacilityType.BRANCH),
    Location(lat=40.7831, lng=-73.9712, name="Branch 2", facility_type=FacilityType.BRANCH),
    Location(lat=40.7736, lng=-73.9566, name="Branch 3", facility_type=FacilityType.BRANCH),
    Location(lat=40.7282, lng=-73.9942, name="Branch 4", facility_type=FacilityType.BRANCH),
    Location(lat=40.7185, lng=-73.9890, name="Branch 5", facility_type=FacilityType.BRANCH),
    Location(lat=40.6892, lng=-73.9857, name="Branch 6", facility_type=FacilityType.BRANCH),
    Location(lat=40.6782, lng=-73.9442, name="Branch 7", facility_type=FacilityType.BRANCH),
    Location(lat=40.7505, lng=-73.9934, name="Branch 8", facility_type=FacilityType.BRANCH),
    Location(lat=40.8100, lng=-73.9530, name="Branch 9", facility_type=FacilityType.BRANCH),
    Location(lat=40.7024, lng=-73.9870, name="Branch 10", facility_type=FacilityType.BRANCH),
    Location(lat=40.7614, lng=-73.9244, name="Branch 11", facility_type=FacilityType.BRANCH),
    Location(lat=40.7434, lng=-73.9518, name="Branch 12", facility_type=FacilityType.BRANCH),
]

# Destinations — where cargo gets delivered
DESTINATIONS: list[Location] = [
    Location(lat=40.7527, lng=-73.9772, name="Destination A", facility_type=FacilityType.DESTINATION),
    Location(lat=40.6880, lng=-73.9760, name="Destination B", facility_type=FacilityType.DESTINATION),
    Location(lat=40.7560, lng=-73.8720, name="Destination C", facility_type=FacilityType.DESTINATION),
    Location(lat=40.7950, lng=-73.9380, name="Destination D", facility_type=FacilityType.DESTINATION),
    Location(lat=40.7200, lng=-73.9960, name="Destination E", facility_type=FacilityType.DESTINATION),
    Location(lat=40.6500, lng=-73.9500, name="Destination F", facility_type=FacilityType.DESTINATION),
]

# Satellites — outer areas
SATELLITES: list[Location] = [
    Location(lat=40.8300, lng=-73.9410, name="Satellite North", facility_type=FacilityType.SATELLITE),
    Location(lat=40.6400, lng=-73.9600, name="Satellite South", facility_type=FacilityType.SATELLITE),
    Location(lat=40.7700, lng=-73.8800, name="Satellite East", facility_type=FacilityType.SATELLITE),
]

ALL_FACILITIES = HUBS + BRANCHES + DESTINATIONS + SATELLITES

# Pickup sources: hubs, branches, satellites (destinations receive, they don't send)
PICKUP_LOCATIONS = HUBS + BRANCHES + SATELLITES
DELIVERY_LOCATIONS = DESTINATIONS
