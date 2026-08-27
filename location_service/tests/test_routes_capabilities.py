import unittest

from location_service.functions.routes_capabilities import (
    allowed_avoid_keys,
    allowed_travel_modes,
    arrival_time_allowed,
    available_operations,
    matrix_axis_limits,
    max_alternatives,
    validate_region_options,
)

GRAB_REGIONS = ("ap-southeast-1", "ap-southeast-5")
STANDARD_REGION = "ap-northeast-1"


class TestAvailableOperations(unittest.TestCase):
    """GrabMaps regions offer only the two Routes operations Grab serves."""

    def test_standard_regions_offer_every_operation(self):
        assert available_operations(STANDARD_REGION) == (
            "CalculateRoutes",
            "CalculateIsolines",
            "SnapToRoads",
            "CalculateRouteMatrix",
        )

    def test_grab_regions_offer_only_routes_and_the_matrix(self):
        for region in GRAB_REGIONS:
            with self.subTest(region=region):
                assert available_operations(region) == (
                    "CalculateRoutes",
                    "CalculateRouteMatrix",
                )


class TestAllowedTravelModes(unittest.TestCase):
    """Transit modes exist only for CalculateRoutes, and never on GrabMaps."""

    def test_calculate_routes_offers_the_transit_modes(self):
        assert allowed_travel_modes(STANDARD_REGION, "CalculateRoutes") == (
            "Car",
            "Pedestrian",
            "Scooter",
            "Truck",
            "Transit",
            "Intermodal",
        )

    def test_other_operations_offer_only_the_vehicle_modes(self):
        for operation in ("CalculateIsolines", "SnapToRoads", "CalculateRouteMatrix"):
            with self.subTest(operation=operation):
                assert allowed_travel_modes(STANDARD_REGION, operation) == (
                    "Car",
                    "Pedestrian",
                    "Scooter",
                    "Truck",
                )

    def test_grab_regions_drop_truck_from_every_operation(self):
        for region in GRAB_REGIONS:
            for operation in ("CalculateRoutes", "CalculateRouteMatrix"):
                with self.subTest(region=region, operation=operation):
                    assert allowed_travel_modes(region, operation) == (
                        "Car",
                        "Pedestrian",
                        "Scooter",
                    )

    def test_an_unknown_operation_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown operation"):
            allowed_travel_modes(STANDARD_REGION, "UnknownOperation")


class TestRegionLimits(unittest.TestCase):
    """GrabMaps regions cap avoidance, alternatives and matrix size more tightly."""

    def test_standard_regions_allow_every_avoidance_option(self):
        assert allowed_avoid_keys(STANDARD_REGION) == (
            "TollRoads",
            "Ferries",
            "Tunnels",
            "ControlledAccessHighways",
            "DirtRoads",
            "UTurns",
        )

    def test_grab_regions_allow_three_avoidance_options(self):
        for region in GRAB_REGIONS:
            with self.subTest(region=region):
                assert allowed_avoid_keys(region) == (
                    "TollRoads",
                    "Ferries",
                    "ControlledAccessHighways",
                )

    def test_grab_regions_allow_fewer_alternatives(self):
        assert max_alternatives(STANDARD_REGION) == 5
        assert max_alternatives("ap-southeast-1") == 3

    def test_arrival_time_is_refused_only_on_grab_maps(self):
        assert arrival_time_allowed(STANDARD_REGION) is True
        assert arrival_time_allowed("ap-southeast-1") is False

    def test_matrix_axis_limits_differ_per_data_provider(self):
        assert matrix_axis_limits(STANDARD_REGION) == (15, 100)
        assert matrix_axis_limits("ap-southeast-1") == (350, 350)


class TestValidateRegionOptions(unittest.TestCase):
    """Options the configured region cannot serve raise before a request is sent."""

    def test_grab_regions_reject_isolines(self):
        with self.assertRaisesRegex(ValueError, "CalculateIsolines is not supported"):
            validate_region_options("ap-southeast-1", "CalculateIsolines")

    def test_grab_regions_reject_the_truck_travel_mode(self):
        with self.assertRaisesRegex(ValueError, "Travel mode 'Truck' is not supported"):
            validate_region_options(
                "ap-southeast-1", "CalculateRoutes", travel_mode="Truck"
            )

    def test_grab_regions_reject_avoiding_tunnels(self):
        with self.assertRaisesRegex(ValueError, "Avoid options not supported"):
            validate_region_options(
                "ap-southeast-1", "CalculateRoutes", avoid=("Tunnels",)
            )

    def test_grab_regions_reject_a_fourth_alternative(self):
        with self.assertRaisesRegex(ValueError, "MaxAlternatives must be at most 3"):
            validate_region_options(
                "ap-southeast-1", "CalculateRoutes", max_alternatives_value=4
            )

    def test_grab_regions_reject_an_arrival_time(self):
        with self.assertRaisesRegex(ValueError, "ArrivalTime is not supported"):
            validate_region_options(
                "ap-southeast-1",
                "CalculateRoutes",
                arrival_time="2026-01-01T09:00:00Z",
            )

    def test_standard_regions_reject_more_than_fifteen_origins(self):
        with self.assertRaisesRegex(ValueError, "Origins must contain at most 15"):
            validate_region_options(
                STANDARD_REGION,
                "CalculateRouteMatrix",
                origins_count=16,
                destinations_count=1,
            )

    def test_standard_regions_reject_more_than_a_hundred_destinations(self):
        with self.assertRaisesRegex(
            ValueError, "Destinations must contain at most 100"
        ):
            validate_region_options(
                STANDARD_REGION,
                "CalculateRouteMatrix",
                origins_count=1,
                destinations_count=101,
            )

    def test_standard_regions_accept_the_full_route_option_set(self):
        validate_region_options(
            STANDARD_REGION,
            "CalculateRoutes",
            travel_mode="Transit",
            avoid=("Tunnels", "UTurns"),
            max_alternatives_value=5,
            arrival_time="2026-01-01T09:00:00Z",
        )

    def test_standard_regions_accept_a_matrix_at_the_axis_limits(self):
        validate_region_options(
            STANDARD_REGION,
            "CalculateRouteMatrix",
            travel_mode="Truck",
            origins_count=15,
            destinations_count=100,
        )


if __name__ == "__main__":
    unittest.main()
