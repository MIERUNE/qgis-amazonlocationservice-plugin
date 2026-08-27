import unittest

from location_service.functions.routes_results import (
    DETAILS_BY_LEG_TYPE,
    MATRIX_ERROR_CODES,
    MAX_LONG_VALUE,
    BrokenResponseError,
    collect_attributions,
    collect_notices,
    leg_line_points,
    leg_summary,
    major_road_names,
    normalize_isolines,
    normalize_matrix,
    normalize_snapped_trace_points,
    route_totals,
    snapped_line_points,
    transit_leg_attributes,
    validate_route_count,
)

DISCLAIMER = "Disclaimer"
TARIFF = "Tariff"

MATRIX_ERROR = "NoRoute"

# Official RouteResponseNotice codes. The response-level vocabulary is a
# different enum from the per-travel-mode leg notice codes below.
RESPONSE_NOTICE_CODES = (
    "MainLanguageNotFound",
    "Other",
    "TravelTimeExceedsDriverWorkHours",
    "TransitDataUnavailable",
    "TransitRouteUnavailable",
    "NoTransitStationsFound",
)
VEHICLE_NOTICE_CODE = "SeasonalClosure"
SHARED_NOTICE_CODE = "Other"


class TestMajorRoadNames(unittest.TestCase):
    """A label may carry either field, so RoadName wins and nulls are skipped."""

    def test_prefers_road_name_and_falls_back_to_route_number(self):
        route = {
            "MajorRoadLabels": [
                {"RouteNumber": {"Value": "I-95"}},
                {"RoadName": {"Value": "Main St"}},
            ]
        }

        assert major_road_names(route) == "I-95, Main St"

    def test_prefers_road_name_when_a_label_has_both(self):
        route = {
            "MajorRoadLabels": [
                {"RoadName": {"Value": "Main St"}, "RouteNumber": {"Value": "I-95"}}
            ]
        }

        assert major_road_names(route) == "Main St"

    def test_uses_the_route_number_when_it_is_the_only_field(self):
        route = {"MajorRoadLabels": [{"RouteNumber": {"Value": "E15"}}]}

        assert major_road_names(route) == "E15"

    def test_skips_empty_labels(self):
        assert major_road_names({"MajorRoadLabels": [{}, {}]}) == ""

    def test_handles_null_values(self):
        cases = (
            {},
            {"MajorRoadLabels": None},
            {"MajorRoadLabels": [{"RoadName": None}]},
            {"MajorRoadLabels": [None]},
        )
        for route in cases:
            with self.subTest(route=route):
                assert major_road_names(route) == ""

    def test_skips_malformed_optional_labels(self):
        for labels in (1, {}, "Main Street"):
            with self.subTest(labels=labels):
                assert major_road_names({"MajorRoadLabels": labels}) == ""

        route = {
            "MajorRoadLabels": [
                1,
                {"RoadName": "Main Street"},
                {"RouteNumber": {"Value": 15}},
                {"RoadName": {"Value": "Main Street"}},
            ]
        }
        assert major_road_names(route) == "Main Street"


class TestLegLinePoints(unittest.TestCase):
    """A leg is drawn only when its LineString is a usable list of positions."""

    def test_returns_the_line_string_positions(self):
        leg = {"Geometry": {"LineString": [[139.767, 35.681], [139.774, 35.686]]}}

        assert leg_line_points(leg) == [[139.767, 35.681], [139.774, 35.686]]

    def test_returns_none_without_a_geometry(self):
        for leg in ({}, {"Geometry": None}, {"Geometry": {}}):
            with self.subTest(leg=leg):
                assert leg_line_points(leg) is None

    def test_returns_none_for_an_unusable_line_string(self):
        cases = (
            [[139.767, 35.681]],
            [[139.767, 35.681], ["139.774", "35.686"]],
            [[139.767, 35.681], [139.774]],
            [[139.767, 35.681], [200.0, 35.686]],
        )
        for line in cases:
            with self.subTest(line=line):
                assert leg_line_points({"Geometry": {"LineString": line}}) is None


def _leg(leg_type, details):
    """
    Returns a leg of that type carrying its own details container.

    The leg Type decides which container the parser reads, so every fixture
    has to name the type its details belong to.
    """
    return {"Type": leg_type, DETAILS_BY_LEG_TYPE[leg_type]: details}


def _vehicle_leg(distance=None, duration=None):
    """Returns a Vehicle leg whose Overview holds only the given totals."""
    overview = {}
    if distance is not None:
        overview["Distance"] = distance
    if duration is not None:
        overview["Duration"] = duration
    return _leg("Vehicle", {"Summary": {"Overview": overview}})


def _transit_leg(**details):
    """Returns a Transit leg carrying that TransitLegDetails object."""
    return _leg("Transit", details)


# Values that cannot represent an AWS Long.
UNUSABLE_LONGS = (
    -1,
    MAX_LONG_VALUE + 1,
    10**400,
    1.5,
    float("nan"),
    float("inf"),
    True,
    "1200",
    [1200],
)


def _long_cases():
    """Returns every ``(field, unusable value)`` pair of a totals object."""
    return [
        (key, value) for key in ("Distance", "Duration") for value in UNUSABLE_LONGS
    ]


class TestLegSummary(unittest.TestCase):
    """Leg totals come from the Summary Overview, with a departure-time fallback."""

    def test_reads_the_distance_and_duration_from_the_overview(self):
        assert leg_summary(_vehicle_leg(1200, 300)) == (1200, 300)

    def test_returns_no_totals_without_a_summary(self):
        cases = (
            {},
            {"Type": "Vehicle"},
            {"Type": "Vehicle", "VehicleLegDetails": {}},
            {"Type": "Vehicle", "VehicleLegDetails": None},
        )
        for leg in cases:
            with self.subTest(leg=leg):
                assert leg_summary(leg) == (None, None)

    def test_rejects_malformed_summary_objects(self):
        cases = (
            _transit_leg(Summary="invalid"),
            _transit_leg(Summary={"Overview": []}),
        )
        for leg in cases:
            with (
                self.subTest(leg=leg),
                self.assertRaisesRegex(BrokenResponseError, "malformed"),
            ):
                leg_summary(leg)

    def test_ignores_malformed_optional_time_objects(self):
        leg = _transit_leg(Departure="invalid", Arrival=[])

        assert leg_summary(leg) == (None, None)

    def test_the_largest_long_value_is_still_a_usable_total(self):
        assert leg_summary(_vehicle_leg(MAX_LONG_VALUE, 0)) == (
            float(MAX_LONG_VALUE),
            0.0,
        )

    def test_a_whole_number_sent_as_a_float_is_still_a_usable_total(self):
        # JSON has one number type, so a Long may arrive as 1200.0.
        assert leg_summary(_vehicle_leg(1200.0, 300.0)) == (1200.0, 300.0)

    def test_a_total_that_is_present_but_unusable_is_broken(self):
        # An absent total is a legitimate "unknown"; a present one that no
        # Long can hold would reach the attribute table as a real number.
        for key, value in _long_cases():
            with (
                self.subTest(key=key, value=value),
                self.assertRaisesRegex(BrokenResponseError, f"invalid {key} value"),
            ):
                leg_summary(_vehicle_leg(**{key.lower(): value}))

    def test_derives_the_duration_from_the_departure_and_arrival_times(self):
        leg = _transit_leg(
            Summary={"Overview": {"Distance": 900}},
            Departure={"Time": "2026-01-01T09:00:00Z"},
            Arrival={"Time": "2026-01-01T09:02:00Z"},
        )

        assert leg_summary(leg) == (900, 120.0)

    def test_a_fractional_second_is_read_the_same_on_every_python_version(self):
        leg = _transit_leg(
            Departure={"Time": "2026-01-01T09:00:00Z"},
            Arrival={"Time": "2026-01-01T09:02:00.8Z"},
        )

        assert leg_summary(leg) == (None, 120.8)

    def test_a_leap_second_endpoint_still_yields_a_duration(self):
        leg = _transit_leg(
            Departure={"Time": "2026-12-31T23:59:60Z"},
            Arrival={"Time": "2027-01-01T00:00:30Z"},
        )

        assert leg_summary(leg) == (None, 30.0)

    def test_ignores_an_unparsable_departure_time(self):
        leg = _transit_leg(
            Departure={"Time": "not-a-time"},
            Arrival={"Time": "2026-01-01T09:02:00Z"},
        )

        assert leg_summary(leg) == (None, None)

    def test_an_arrival_before_its_departure_leaves_the_duration_unknown(self):
        leg = _transit_leg(
            Departure={"Time": "2026-01-01T09:02:00Z"},
            Arrival={"Time": "2026-01-01T09:00:00Z"},
        )

        assert leg_summary(leg) == (None, None)

    def test_ignores_mixed_naive_and_offset_aware_times(self):
        leg = _transit_leg(
            Departure={"Time": "2026-01-01T09:00:00"},
            Arrival={"Time": "2026-01-01T09:02:00Z"},
        )

        assert leg_summary(leg) == (None, None)

    def test_ignores_two_times_that_both_lack_an_offset(self):
        leg = _transit_leg(
            Departure={"Time": "2026-01-01T09:00:00"},
            Arrival={"Time": "2026-01-01T09:02:00"},
        )

        assert leg_summary(leg) == (None, None)


class TestRouteTotals(unittest.TestCase):
    """Route totals come from Route.Summary and fall back to summing the legs."""

    def test_prefers_the_route_summary_over_the_legs(self):
        route = {
            "Summary": {"Distance": 5000, "Duration": 900},
            "Legs": [_vehicle_leg(1, 2)],
        }

        assert route_totals(route) == (5000, 900)

    def test_sums_the_legs_when_the_summary_is_empty(self):
        route = {
            "Summary": {},
            "Legs": [_vehicle_leg(1200, 300), _vehicle_leg(800, 600)],
        }

        assert route_totals(route) == (2000, 900)

    def test_a_leg_without_a_distance_leaves_the_total_distance_unknown(self):
        # A partial sum would look like a complete total, so the metric that
        # is missing on one leg stays NULL while the other one is summed.
        route = {"Legs": [_vehicle_leg(1200, 300), _vehicle_leg(None, 600)]}

        assert route_totals(route) == (None, 900)

    def test_a_leg_without_a_duration_leaves_the_total_duration_unknown(self):
        route = {"Legs": [_vehicle_leg(1200, 300), _vehicle_leg(800, None)]}

        assert route_totals(route) == (2000, None)

    def test_legs_missing_both_metrics_leave_both_totals_unknown(self):
        route = {"Legs": [_vehicle_leg(1200, 300), _vehicle_leg()]}

        assert route_totals(route) == (None, None)

    def test_a_summary_metric_survives_an_incomplete_leg_sum(self):
        route = {
            "Summary": {"Distance": 5000},
            "Legs": [_vehicle_leg(1200, 300), _vehicle_leg(800, None)],
        }

        assert route_totals(route) == (5000, None)

    def test_returns_no_totals_without_a_summary_or_legs(self):
        for route in ({}, {"Summary": {}, "Legs": []}, {"Summary": None}):
            with self.subTest(route=route):
                assert route_totals(route) == (None, None)

    def test_rejects_malformed_summary_and_legs_values(self):
        for route in ({"Summary": []}, {"Legs": {}}):
            with (
                self.subTest(route=route),
                self.assertRaisesRegex(BrokenResponseError, "malformed"),
            ):
                route_totals(route)

    def test_a_route_total_that_is_present_but_unusable_is_broken(self):
        for key, value in _long_cases():
            with (
                self.subTest(key=key, value=value),
                self.assertRaisesRegex(BrokenResponseError, f"invalid {key} value"),
            ):
                route_totals({"Summary": {key: value}})

    def test_an_unusable_leg_total_breaks_the_summed_route(self):
        route = {"Legs": [_vehicle_leg(1200, 300), _vehicle_leg(-1, 600)]}

        with self.assertRaisesRegex(BrokenResponseError, "invalid Distance value"):
            route_totals(route)

    def test_a_sum_that_still_fits_a_long_is_kept(self):
        route = {
            "Legs": [
                _vehicle_leg(MAX_LONG_VALUE - 1, 1),
                _vehicle_leg(1, MAX_LONG_VALUE - 1),
            ]
        }

        assert route_totals(route) == (MAX_LONG_VALUE, MAX_LONG_VALUE)

    def test_a_sum_beyond_the_long_range_is_unknown_rather_than_broken(self):
        route = {
            "Legs": [
                _vehicle_leg(MAX_LONG_VALUE, MAX_LONG_VALUE),
                _vehicle_leg(1, 1),
            ]
        }

        assert route_totals(route) == (None, None)


class TestValidateRouteCount(unittest.TestCase):
    """A response must respect the request's alternative route limit."""

    def test_accepts_a_route_count_within_the_request_limit(self):
        for max_alternatives, route_count in ((0, 0), (0, 1), (2, 1), (2, 3)):
            with self.subTest(
                max_alternatives=max_alternatives, route_count=route_count
            ):
                validate_route_count({"Routes": [{}] * route_count}, max_alternatives)

    def test_rejects_a_route_count_above_the_request_limit(self):
        for max_alternatives, route_count in ((0, 2), (2, 4)):
            with (
                self.subTest(
                    max_alternatives=max_alternatives, route_count=route_count
                ),
                self.assertRaisesRegex(BrokenResponseError, "more routes than"),
            ):
                validate_route_count({"Routes": [{}] * route_count}, max_alternatives)

    def test_rejects_a_routes_value_that_is_not_an_array(self):
        for routes in ({}, "route", 1, False):
            with (
                self.subTest(routes=routes),
                self.assertRaisesRegex(BrokenResponseError, "malformed Routes"),
            ):
                validate_route_count({"Routes": routes}, 0)


class TestTransitLegAttributes(unittest.TestCase):
    """The route name falls back through the three names the API may send."""

    def test_reads_the_documented_transit_fields(self):
        leg = _transit_leg(
            Agency={"Name": "Tokyo Metro"},
            Transport={"RouteName": "Ginza Line", "Headsign": "Shibuya"},
            Departure={"Time": "2026-01-01T09:00:00Z"},
            Arrival={"Time": "2026-01-01T09:30:00Z"},
        )

        assert transit_leg_attributes(leg) == {
            "Agency": "Tokyo Metro",
            "RouteName": "Ginza Line",
            "Headsign": "Shibuya",
            "DepartureTime": "2026-01-01T09:00:00Z",
            "ArrivalTime": "2026-01-01T09:30:00Z",
        }

    def test_the_route_name_falls_back_to_the_short_and_long_names(self):
        cases = (
            (
                {"RouteName": "Ginza", "ShortRouteName": "G", "LongRouteName": "L"},
                "Ginza",
            ),
            ({"ShortRouteName": "G", "LongRouteName": "Ginza Line"}, "G"),
            ({"LongRouteName": "Ginza Line"}, "Ginza Line"),
            ({}, ""),
        )
        for transport, expected in cases:
            with self.subTest(transport=sorted(transport)):
                leg = _transit_leg(Transport=transport)

                assert transit_leg_attributes(leg)["RouteName"] == expected

    def test_a_leg_without_details_has_empty_attributes(self):
        cases = (
            {},
            {"Type": "Transit"},
            {"Type": "Transit", "TransitLegDetails": {}},
            {"Type": "Transit", "TransitLegDetails": None},
        )
        for leg in cases:
            with self.subTest(leg=leg):
                assert set(transit_leg_attributes(leg).values()) == {""}

    def test_malformed_optional_objects_have_empty_attributes(self):
        leg = _transit_leg(
            Agency="Tokyo Metro",
            Transport=[],
            Departure=0,
            Arrival=False,
        )

        assert set(transit_leg_attributes(leg).values()) == {""}


class TestLegDetailsFollowTheLegType(unittest.TestCase):
    """
    The leg Type decides which details container is read.

    A response may carry more than one container on the same leg, so
    picking the first one that happens to be present would report another
    leg type's summary and route name as this leg's own.
    """

    def test_every_official_type_reads_its_own_container(self):
        details = {"Summary": {"Overview": {"Distance": 900, "Duration": 300}}}
        for leg_type in DETAILS_BY_LEG_TYPE:
            with self.subTest(leg_type=leg_type):
                assert leg_summary(_leg(leg_type, details)) == (900.0, 300.0)

    def test_a_vehicle_leg_never_borrows_transit_details(self):
        leg = {
            "Type": "Vehicle",
            "TransitLegDetails": {
                "Summary": {"Overview": {"Distance": 900, "Duration": 300}},
                "Agency": {"Name": "Tokyo Metro"},
                "Transport": {"RouteName": "Ginza Line"},
                "Departure": {"Time": "2026-01-01T09:00:00Z"},
                "Arrival": {"Time": "2026-01-01T09:30:00Z"},
            },
        }

        assert leg_summary(leg) == (None, None)
        assert set(transit_leg_attributes(leg).values()) == {""}

    def test_a_leg_without_a_type_reads_no_details_at_all(self):
        leg = {"VehicleLegDetails": {"Summary": {"Overview": {"Distance": 900}}}}

        assert leg_summary(leg) == (None, None)


class TestCollectNotices(unittest.TestCase):
    """Response and leg notices are flattened into one code and impact shape."""

    def test_collects_every_documented_response_level_code(self):
        for code in RESPONSE_NOTICE_CODES:
            with self.subTest(code=code):
                data = {"Notices": [{"Code": code, "Impact": "High"}]}

                assert collect_notices(data) == [
                    {
                        "scope": "response",
                        "route_index": None,
                        "leg_index": None,
                        "leg_type": "",
                        "code": code,
                        "impact": "High",
                        "details": None,
                    }
                ]

    def test_keeps_the_details_a_road_mode_notice_carries(self):
        leg = {
            "Type": "Vehicle",
            "VehicleLegDetails": {
                "Notices": [
                    {
                        "Code": VEHICLE_NOTICE_CODE,
                        "Impact": "High",
                        "Details": [{"Title": "Winter closure"}],
                    }
                ]
            },
        }

        assert collect_notices({"Routes": [{"Legs": [leg]}]}) == [
            {
                "scope": "leg",
                "route_index": 0,
                "leg_index": 0,
                "leg_type": "Vehicle",
                "code": VEHICLE_NOTICE_CODE,
                "impact": "High",
                "details": [{"Title": "Winter closure"}],
            }
        ]

    def test_collects_notices_from_every_leg_type_that_carries_them(self):
        cases = (
            ("Vehicle", "VehicleLegDetails"),
            ("Pedestrian", "PedestrianLegDetails"),
            ("Ferry", "FerryLegDetails"),
            ("Transit", "TransitLegDetails"),
            ("Taxi", "TaxiLegDetails"),
        )
        for leg_type, details_key in cases:
            with self.subTest(details_key=details_key):
                notice = {"Code": SHARED_NOTICE_CODE, "Impact": "Low"}
                leg = {"Type": leg_type, details_key: {"Notices": [notice]}}
                data = {"Routes": [{"Legs": [leg]}]}

                assert collect_notices(data) == [
                    {
                        "scope": "leg",
                        "route_index": 0,
                        "leg_index": 0,
                        "leg_type": leg_type,
                        "code": SHARED_NOTICE_CODE,
                        "impact": "Low",
                        "details": None,
                    }
                ]

    def test_passes_an_unknown_notice_code_through_unchanged(self):
        # Defensive test: each travel mode has its own code vocabulary and
        # AWS keeps adding to them, so a code the plugin does not know must
        # still reach the notices table instead of being dropped.
        leg = {
            "Type": "Vehicle",
            "VehicleLegDetails": {"Notices": [{"Code": "SomeFutureCode"}]},
        }
        data = {"Notices": [{"Code": "AnotherFutureCode"}], "Routes": [{"Legs": [leg]}]}

        codes = [notice["code"] for notice in collect_notices(data)]
        assert codes == ["AnotherFutureCode", "SomeFutureCode"]

    def test_records_the_position_of_each_leg_notice(self):
        leg = {
            "Type": "Vehicle",
            "VehicleLegDetails": {"Notices": [{"Code": VEHICLE_NOTICE_CODE}]},
        }
        data = {"Routes": [{"Legs": [leg]}, {"Legs": [leg, leg]}]}

        positions = [
            (notice["route_index"], notice["leg_index"])
            for notice in collect_notices(data)
        ]
        assert positions == [(0, 0), (1, 0), (1, 1)]

    def test_a_leg_only_collects_the_notices_of_its_own_details(self):
        leg = {
            "Type": "Vehicle",
            "VehicleLegDetails": {"Notices": [{"Code": VEHICLE_NOTICE_CODE}]},
            "TransitLegDetails": {"Notices": [{"Code": SHARED_NOTICE_CODE}]},
        }

        collected = collect_notices({"Routes": [{"Legs": [leg]}]})

        assert [notice["code"] for notice in collected] == [VEHICLE_NOTICE_CODE]
        assert collected[0]["leg_type"] == "Vehicle"

    def test_ignores_notices_placed_in_rental_leg_details(self):
        data = {
            "Routes": [
                {
                    "Legs": [
                        {
                            "Type": "Rental",
                            "RentalLegDetails": {
                                "Notices": [
                                    {"Code": SHARED_NOTICE_CODE, "Impact": "High"}
                                ]
                            },
                        }
                    ]
                }
            ]
        }

        assert collect_notices(data) == []

    def test_collects_nothing_from_a_response_without_notices(self):
        cases = (
            {},
            {"Notices": None, "Routes": None},
            {"Routes": [None, {"Legs": [None]}]},
            {"Routes": [{"Legs": [{"Type": "Vehicle", "VehicleLegDetails": {}}]}]},
        )
        for data in cases:
            with self.subTest(data=data):
                assert collect_notices(data) == []

    def test_ignores_unusable_route_containers(self):
        cases = (
            {"Routes": 1},
            {"Routes": [{"Legs": 1}]},
        )
        for data in cases:
            with self.subTest(data=data):
                assert collect_notices(data) == []

    def test_rejects_malformed_notice_values(self):
        cases = (
            {"Notices": {}},
            {"Notices": [None]},
            {
                "Routes": [
                    {
                        "Legs": [
                            {
                                "Type": "Vehicle",
                                "VehicleLegDetails": {"Notices": "invalid"},
                            }
                        ]
                    }
                ]
            },
            {
                "Routes": [
                    {
                        "Legs": [
                            {
                                "Type": "Vehicle",
                                "VehicleLegDetails": {"Notices": [None]},
                            }
                        ]
                    }
                ]
            },
        )
        for data in cases:
            with (
                self.subTest(data=data),
                self.assertRaisesRegex(
                    BrokenResponseError, "malformed Notices.*no layer was created"
                ),
            ):
                collect_notices(data)


def _attribution(description="Toei Subway", url="https://example.com/toei"):
    """Returns one RouteAttribution with a WebLink the plugin can display."""
    web_link = {"Description": description}
    if url:
        web_link["Url"] = url
    return {"AttributionType": DISCLAIMER, "WebLink": web_link}


def _attributed_leg(details_key, attributions):
    """Returns a leg of the type that owns ``details_key``, with attributions."""
    return {
        "Type": details_key.removesuffix("LegDetails"),
        details_key: {"Attributions": attributions},
    }


def _attributed_route(details_key, attributions):
    """Returns a response whose single leg carries that Attributions value."""
    return {"Routes": [{"Legs": [_attributed_leg(details_key, attributions)]}]}


class TestCollectAttributions(unittest.TestCase):
    """Only transit, taxi and rental legs carry attributions, without duplicates."""

    def test_collects_transit_taxi_and_rental_attributions(self):
        data = {
            "Routes": [
                {
                    "Legs": [
                        _attributed_leg(
                            "TransitLegDetails",
                            [_attribution("Toei Subway", "https://example.com/1")],
                        ),
                        _attributed_leg(
                            "TaxiLegDetails",
                            [_attribution("Tokyo Taxi", "https://example.com/2")],
                        ),
                        _attributed_leg(
                            "RentalLegDetails",
                            [_attribution("Bike Share", "https://example.com/3")],
                        ),
                    ]
                }
            ]
        }

        assert collect_attributions(data) == [
            {
                "type": DISCLAIMER,
                "description": "Toei Subway",
                "url": "https://example.com/1",
            },
            {
                "type": DISCLAIMER,
                "description": "Tokyo Taxi",
                "url": "https://example.com/2",
            },
            {
                "type": DISCLAIMER,
                "description": "Bike Share",
                "url": "https://example.com/3",
            },
        ]

    def test_reports_a_repeated_attribution_once(self):
        leg = _attributed_leg("TransitLegDetails", [_attribution()])
        data = {"Routes": [{"Legs": [leg]}, {"Legs": [leg]}]}

        assert collect_attributions(data) == [
            {
                "type": DISCLAIMER,
                "description": "Toei Subway",
                "url": "https://example.com/toei",
            }
        ]

    def test_keeps_both_documented_attribution_types(self):
        for attribution_type in (DISCLAIMER, TARIFF):
            with self.subTest(attribution_type=attribution_type):
                attribution = _attribution()
                attribution["AttributionType"] = attribution_type
                data = _attributed_route("TaxiLegDetails", [attribution])

                assert collect_attributions(data)[0]["type"] == attribution_type

    def test_passes_an_unknown_attribution_type_through_unchanged(self):
        # AttributionType is documented as Disclaimer or Tariff. Should AWS
        # add a value, the parser must carry it to the layer rather than drop
        # an attribution the plugin is required to display.
        attribution = _attribution()
        attribution["AttributionType"] = "SomeFutureType"
        data = _attributed_route("TransitLegDetails", [attribution])

        assert collect_attributions(data)[0]["type"] == "SomeFutureType"

    def test_keeps_an_attribution_that_has_no_url(self):
        data = _attributed_route(
            "TransitLegDetails", [_attribution("Toei Subway", url="")]
        )

        assert collect_attributions(data) == [
            {"type": DISCLAIMER, "description": "Toei Subway", "url": ""}
        ]

    def test_ignores_attributions_on_road_and_ferry_legs(self):
        keys = ("VehicleLegDetails", "PedestrianLegDetails", "FerryLegDetails")
        for key in keys:
            with self.subTest(key=key):
                data = _attributed_route(key, [_attribution()])

                assert collect_attributions(data) == []

    def test_a_route_without_those_legs_has_no_attributions(self):
        leg = {"Type": "Vehicle", "VehicleLegDetails": {"Notices": []}}

        assert collect_attributions({"Routes": [{"Legs": [leg]}]}) == []

    def test_rejects_malformed_route_and_leg_arrays(self):
        for data in ({"Routes": {}}, {"Routes": [{"Legs": 1}]}):
            with (
                self.subTest(data=data),
                self.assertRaisesRegex(BrokenResponseError, "malformed"),
            ):
                collect_attributions(data)

    def test_a_leg_type_that_needs_no_details_has_no_attributions(self):
        for leg_type in ("Vehicle", "Pedestrian", "Ferry"):
            with self.subTest(leg_type=leg_type):
                data = {"Routes": [{"Legs": [{"Type": leg_type}]}]}

                assert collect_attributions(data) == []

    def test_a_leg_without_its_required_attributions_array_is_broken(self):
        # The API requires displaying these attributions, so a transit, taxi
        # or rental leg without a well-formed array is a broken response
        # rather than a leg that happens to have nothing to display.
        keys = ("TransitLegDetails", "TaxiLegDetails", "RentalLegDetails")
        for key in keys:
            with (
                self.subTest(key=key),
                self.assertRaisesRegex(
                    BrokenResponseError, "missing its required attributions"
                ),
            ):
                leg = {"Type": key.removesuffix("LegDetails"), key: {}}
                collect_attributions({"Routes": [{"Legs": [leg]}]})

    def test_a_leg_missing_the_details_its_type_requires_is_broken(self):
        for leg_type in ("Transit", "Taxi", "Rental"):
            with (
                self.subTest(leg_type=leg_type),
                self.assertRaisesRegex(
                    BrokenResponseError, "missing its required details"
                ),
            ):
                collect_attributions({"Routes": [{"Legs": [{"Type": leg_type}]}]})

    def test_details_that_are_not_an_object_are_broken(self):
        for details in (None, [], "Toei Subway", 0):
            with (
                self.subTest(details=details),
                self.assertRaisesRegex(
                    BrokenResponseError, "missing its required details"
                ),
            ):
                leg = {"Type": "Transit", "TransitLegDetails": details}
                collect_attributions({"Routes": [{"Legs": [leg]}]})

    def test_the_leg_type_decides_which_details_are_required(self):
        leg = _attributed_leg("TaxiLegDetails", [_attribution()])
        leg["Type"] = "Transit"

        with self.assertRaisesRegex(
            BrokenResponseError, "missing its required details"
        ):
            collect_attributions({"Routes": [{"Legs": [leg]}]})

    def test_an_attributions_value_that_is_not_an_array_is_broken(self):
        for attributions in (None, {}, "Toei Subway"):
            with (
                self.subTest(attributions=attributions),
                self.assertRaisesRegex(
                    BrokenResponseError, "missing its required attributions"
                ),
            ):
                collect_attributions(
                    _attributed_route("TransitLegDetails", attributions)
                )

    def test_an_attribution_without_a_usable_description_is_broken(self):
        cases = (
            None,
            {},
            {"AttributionType": DISCLAIMER},
            {"AttributionType": DISCLAIMER, "WebLink": None},
            {"AttributionType": DISCLAIMER, "WebLink": {}},
            {"AttributionType": DISCLAIMER, "WebLink": {"Url": "https://a.test"}},
            {"AttributionType": DISCLAIMER, "WebLink": {"Description": ""}},
            {"AttributionType": DISCLAIMER, "WebLink": "https://a.test"},
        )
        for attribution in cases:
            with (
                self.subTest(attribution=attribution),
                self.assertRaisesRegex(
                    BrokenResponseError, "required attribution is malformed"
                ),
            ):
                collect_attributions(
                    _attributed_route("TransitLegDetails", [attribution])
                )


# Closed rings of two detached reachable areas of the same isoline.
RING = [[139.7, 35.6], [139.8, 35.6], [139.8, 35.7], [139.7, 35.6]]
OTHER_RING = [[140.1, 36.1], [140.2, 36.1], [140.2, 36.2], [140.1, 36.1]]
# An unreachable hole inside RING, a ring of too few points, and a ring
# whose last point does not close it.
HOLE = [[139.72, 35.62], [139.74, 35.62], [139.74, 35.64], [139.72, 35.62]]
SHORT_RING = [[139.7, 35.6], [139.8, 35.6], [139.7, 35.6]]
OPEN_RING = [[139.7, 35.6], [139.8, 35.6], [139.8, 35.7], [139.7, 35.7]]


def _isoline(value=600, threshold_key="TimeThreshold", polygons=((RING,),)):
    """Returns one response isoline with that threshold and those polygons."""
    return {
        threshold_key: value,
        "Geometries": [{"Polygon": list(polygon)} for polygon in polygons],
    }


class TestNormalizeIsolines(unittest.TestCase):
    """
    The isolines must answer the requested thresholds exactly.

    Every isoline is one billed reachable area the user asked for, so a
    response that answers a different threshold, drops one, or carries an
    unusable polygon is refused as a whole: publishing the part that is
    still drawable would silently show a smaller reachable area than the
    one that was requested.
    """

    def _assert_broken(self, data, thresholds, threshold_type="Time"):
        """Asserts the response is refused instead of partly published."""
        with self.assertRaisesRegex(BrokenResponseError, "no layer was created"):
            normalize_isolines(data, threshold_type, thresholds)

    def test_reports_the_requested_threshold_type_and_value(self):
        cases = (
            ("Time", "TimeThreshold", 600),
            ("Distance", "DistanceThreshold", 5000),
        )
        for threshold_type, threshold_key, value in cases:
            with self.subTest(threshold_type=threshold_type):
                data = {"Isolines": [_isoline(value, threshold_key)]}

                normalized = normalize_isolines(data, threshold_type, (value,))

                assert len(normalized) == 1
                assert normalized[0]["threshold_type"] == threshold_type
                assert normalized[0]["threshold_value"] == float(value)
                assert normalized[0]["polygons"] == [[RING]]

    def test_answers_are_kept_in_the_order_the_response_returns_them(self):
        data = {"Isolines": [_isoline(1200), _isoline(600)]}

        normalized = normalize_isolines(data, "Time", (600, 1200))

        assert [isoline["threshold_value"] for isoline in normalized] == [1200.0, 600.0]

    def test_a_threshold_requested_twice_is_answered_twice(self):
        data = {"Isolines": [_isoline(600), _isoline(600)]}

        normalized = normalize_isolines(data, "Time", (600, 600))

        assert [isoline["threshold_value"] for isoline in normalized] == [600.0, 600.0]

    def test_keeps_every_detached_polygon_of_one_isoline(self):
        data = {"Isolines": [_isoline(polygons=((RING,), (OTHER_RING,)))]}

        normalized = normalize_isolines(data, "Time", (600,))

        assert normalized[0]["polygons"] == [[RING], [OTHER_RING]]

    def test_keeps_an_unreachable_hole_with_its_outer_ring(self):
        data = {"Isolines": [_isoline(polygons=((RING, HOLE),))]}

        normalized = normalize_isolines(data, "Time", (600,))

        assert normalized[0]["polygons"] == [[RING, HOLE]]

    def test_rejects_a_response_that_answers_another_threshold(self):
        self._assert_broken({"Isolines": [_isoline(900)]}, (600,))

    def test_rejects_a_response_with_the_wrong_number_of_isolines(self):
        cases = (
            ([_isoline(600)], (600, 1200)),
            ([_isoline(600), _isoline(1200)], (600,)),
            ([], (600,)),
        )
        for isolines, thresholds in cases:
            with self.subTest(isolines=len(isolines), thresholds=thresholds):
                self._assert_broken({"Isolines": isolines}, thresholds)

    def test_rejects_an_answer_that_repeats_one_requested_threshold(self):
        data = {"Isolines": [_isoline(600), _isoline(600)]}

        self._assert_broken(data, (600, 1200))

    def test_rejects_a_response_without_any_isolines(self):
        for data in ({}, {"Isolines": None}, {"Isolines": {}}):
            with self.subTest(data=data):
                self._assert_broken(data, (600,))

    def test_rejects_an_isoline_that_is_not_an_object(self):
        for isoline in (None, [], "600"):
            with self.subTest(isoline=isoline):
                self._assert_broken({"Isolines": [isoline]}, (600,))

    def test_rejects_an_isoline_without_the_requested_threshold_field(self):
        data = {"Isolines": [_isoline(600, "TimeThreshold")]}

        self._assert_broken(data, (600,), threshold_type="Distance")

    def test_rejects_a_threshold_value_no_long_can_hold(self):
        for value in UNUSABLE_LONGS:
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(
                    BrokenResponseError, "invalid TimeThreshold value"
                ),
            ):
                normalize_isolines({"Isolines": [_isoline(value)]}, "Time", (600,))

    def test_rejects_an_isoline_without_geometries(self):
        for geometries in (None, [], {}, "Polygon"):
            with self.subTest(geometries=geometries):
                isoline = {"TimeThreshold": 600, "Geometries": geometries}
                self._assert_broken({"Isolines": [isoline]}, (600,))

    def test_rejects_a_geometry_that_carries_no_polygon(self):
        for geometry in (None, [], "Polygon", {}, {"Polygon": None}, {"Polygon": []}):
            with self.subTest(geometry=geometry):
                isoline = {"TimeThreshold": 600, "Geometries": [geometry]}
                self._assert_broken({"Isolines": [isoline]}, (600,))

    def test_rejects_a_ring_that_is_too_short_or_left_open(self):
        for name, ring in (("short", SHORT_RING), ("open", OPEN_RING)):
            with self.subTest(ring=name):
                data = {"Isolines": [_isoline(polygons=((ring,),))]}
                self._assert_broken(data, (600,))

    def test_a_broken_ring_refuses_the_whole_response(self):
        # Keeping the polygons that are still usable would draw a reachable
        # area smaller than the one the user paid for.
        data = {"Isolines": [_isoline(polygons=((SHORT_RING, HOLE), (RING,)))]}

        self._assert_broken(data, (600,))

    def test_rejects_a_ring_with_an_unusable_position(self):
        cases = (
            [[139.7, 35.6], [139.8, 35.6], ["139.8", "35.7"], [139.7, 35.6]],
            [[139.7, 35.6], [139.8, 35.6], [200.0, 35.7], [139.7, 35.6]],
            [[139.7, 35.6], [139.8, 35.6], [139.8], [139.7, 35.6]],
        )
        for ring in cases:
            with self.subTest(ring=ring):
                data = {"Isolines": [_isoline(polygons=((ring,),))]}
                self._assert_broken(data, (600,))


class TestSnappedLinePoints(unittest.TestCase):
    """A missing snapped line is an empty answer; a malformed one is broken."""

    def test_returns_the_snapped_line_positions(self):
        data = {
            "SnappedGeometry": {"LineString": [[139.767, 35.681], [139.774, 35.68]]}
        }

        assert snapped_line_points(data) == [[139.767, 35.681], [139.774, 35.68]]

    def test_an_absent_geometry_or_line_is_not_an_error(self):
        cases = (
            {},
            {"SnappedGeometry": None},
            {"SnappedGeometry": {}},
            {"SnappedGeometry": {"LineString": None}},
            {"SnappedGeometry": {"LineString": []}},
        )
        for data in cases:
            with self.subTest(data=data):
                assert snapped_line_points(data) is None

    def test_a_line_with_a_single_vertex_is_broken(self):
        # A LineString the API returns carries at least two positions, so one
        # vertex is a malformed geometry rather than a line with nothing to
        # draw: half of a snapped route must not be published as the whole.
        data = {"SnappedGeometry": {"LineString": [[139.767, 35.681]]}}

        with self.assertRaisesRegex(
            BrokenResponseError, "snapped geometry is malformed"
        ):
            snapped_line_points(data)

    def test_a_geometry_that_is_present_but_malformed_is_broken(self):
        cases = (
            {"SnappedGeometry": []},
            {"SnappedGeometry": "139.767,35.681"},
            {"SnappedGeometry": {"LineString": "139.767,35.681"}},
            {"SnappedGeometry": {"LineString": {}}},
            {"SnappedGeometry": {"LineString": [["139.767", "35.681"]]}},
            {"SnappedGeometry": {"LineString": [[139.767, 35.681], [139.774]]}},
            {"SnappedGeometry": {"LineString": [[139.767, 35.681], [200.0, 35.68]]}},
        )
        for data in cases:
            with (
                self.subTest(data=data),
                self.assertRaisesRegex(
                    BrokenResponseError, "snapped geometry is malformed"
                ),
            ):
                snapped_line_points(data)


SENT_POSITION = [139.767, 35.681]


def _snap_response(confidence):
    """Returns a one-point SnapToRoads response carrying that Confidence."""
    return {
        "SnappedTracePoints": [
            {
                "OriginalPosition": [139.767, 35.681],
                "SnappedPosition": [139.7671, 35.6811],
                "Confidence": confidence,
            }
        ]
    }


class TestNormalizeSnappedTracePoints(unittest.TestCase):
    """Snapped trace points are usable only while they still match the request."""

    def test_pairs_each_snapped_point_with_the_position_that_was_sent(self):
        sent = [[139.767, 35.681], [139.774, 35.686]]
        data = {
            "SnappedTracePoints": [
                {
                    "OriginalPosition": [139.7670001, 35.681],
                    "SnappedPosition": [139.7671, 35.6811],
                    "Confidence": 0.9,
                },
                {
                    "OriginalPosition": [139.774, 35.686],
                    "SnappedPosition": [139.7741, 35.6861],
                    "Confidence": 0.75,
                },
            ]
        }

        assert normalize_snapped_trace_points(data, sent) == [
            {
                "index": 0,
                "confidence": 0.9,
                "snapped": [139.7671, 35.6811],
                "original": [139.7670001, 35.681],
            },
            {
                "index": 1,
                "confidence": 0.75,
                "snapped": [139.7741, 35.6861],
                "original": [139.774, 35.686],
            },
        ]

    def test_rejects_a_response_with_a_different_number_of_points(self):
        data = _snap_response(0.9)

        with self.assertRaisesRegex(
            BrokenResponseError, "do not match the sent trace points"
        ):
            normalize_snapped_trace_points(data, [[139.767, 35.681], [139.774, 35.686]])

    def test_rejects_a_point_whose_original_position_moved(self):
        data = {
            "SnappedTracePoints": [
                {
                    "OriginalPosition": [139.8, 35.681],
                    "SnappedPosition": [139.7671, 35.6811],
                    "Confidence": 0.9,
                }
            ]
        }

        with self.assertRaisesRegex(
            BrokenResponseError, "do not match the sent trace points"
        ):
            normalize_snapped_trace_points(data, [SENT_POSITION])

    def test_rejects_a_response_that_carries_no_snapped_trace_points(self):
        for data in ({}, {"SnappedTracePoints": None}, {"SnappedTracePoints": []}):
            with (
                self.subTest(data=data),
                self.assertRaisesRegex(
                    BrokenResponseError, "do not match the sent trace points"
                ),
            ):
                normalize_snapped_trace_points(data, [SENT_POSITION])

    def test_rejects_a_malformed_point_entry(self):
        data = {"SnappedTracePoints": [None]}

        with self.assertRaisesRegex(
            BrokenResponseError, "do not match the sent trace points"
        ):
            normalize_snapped_trace_points(data, [SENT_POSITION])

    def test_keeps_a_confidence_anywhere_in_the_documented_range(self):
        for confidence in (0, 0.5, 1):
            with self.subTest(confidence=confidence):
                normalized = normalize_snapped_trace_points(
                    _snap_response(confidence), [SENT_POSITION]
                )

                assert normalized[0]["confidence"] == confidence

    def test_rejects_a_point_without_a_confidence(self):
        # Confidence is a required field, and the point layer is built around
        # it, so a point without one cannot be published as "unknown".
        data = {
            "SnappedTracePoints": [
                {
                    "OriginalPosition": [139.767, 35.681],
                    "SnappedPosition": [139.7671, 35.6811],
                }
            ]
        }

        with self.assertRaisesRegex(BrokenResponseError, "invalid confidence"):
            normalize_snapped_trace_points(data, [SENT_POSITION])

    def test_rejects_a_confidence_outside_zero_to_one(self):
        for confidence in (-0.1, 1.1, 2.5, 100):
            with (
                self.subTest(confidence=confidence),
                self.assertRaisesRegex(BrokenResponseError, "invalid confidence"),
            ):
                normalize_snapped_trace_points(
                    _snap_response(confidence), [SENT_POSITION]
                )

    def test_rejects_a_confidence_that_is_not_a_finite_number(self):
        for confidence in (None, "0.9", True, 10**400, float("nan"), float("inf")):
            with (
                self.subTest(confidence=confidence),
                self.assertRaisesRegex(BrokenResponseError, "invalid confidence"),
            ):
                normalize_snapped_trace_points(
                    _snap_response(confidence), [SENT_POSITION]
                )


def _one_cell_matrix(cell):
    """Returns a one-origin, one-destination RouteMatrix response."""
    return {"RouteMatrix": [[cell]]}


class TestNormalizeMatrix(unittest.TestCase):
    """A matrix must keep the requested shape, while cell errors are results."""

    def test_keeps_a_cell_error_as_an_empty_result(self):
        data = {
            "RouteMatrix": [
                [
                    {"Distance": 1000, "Duration": 120},
                    {"Distance": 0, "Duration": 0, "Error": MATRIX_ERROR},
                ]
            ]
        }

        assert normalize_matrix(data, 1, 2) == [
            [
                {"distance": 1000.0, "duration": 120.0, "error": ""},
                {"distance": None, "duration": None, "error": MATRIX_ERROR},
            ]
        ]

    def test_an_error_cell_hides_the_values_the_api_still_filled_in(self):
        # AWS also fills Distance and Duration on error cells; keeping their
        # zeros would make a failed pair look like a zero-length route.
        data = _one_cell_matrix({"Distance": 0, "Duration": 0, "Error": MATRIX_ERROR})

        assert normalize_matrix(data, 1, 1) == [
            [{"distance": None, "duration": None, "error": MATRIX_ERROR}]
        ]

    def test_rejects_an_error_cell_with_an_unusable_value(self):
        for key, value in _long_cases():
            with (
                self.subTest(key=key, value=value),
                self.assertRaisesRegex(BrokenResponseError, f"invalid {key} value"),
            ):
                cell = {"Distance": 0, "Duration": 0, "Error": MATRIX_ERROR}
                cell[key] = value
                normalize_matrix(_one_cell_matrix(cell), 1, 1)

    def test_rejects_an_error_cell_without_both_values(self):
        cases = (
            {"Error": MATRIX_ERROR},
            {"Distance": 0, "Error": MATRIX_ERROR},
            {"Duration": 0, "Error": MATRIX_ERROR},
            {"Distance": 0, "Duration": None, "Error": MATRIX_ERROR},
        )
        for cell in cases:
            with (
                self.subTest(cell=cell),
                self.assertRaisesRegex(BrokenResponseError, "malformed cell"),
            ):
                normalize_matrix(_one_cell_matrix(cell), 1, 1)

    def test_the_official_error_codes_are_the_nine_documented_values(self):
        assert MATRIX_ERROR_CODES == (
            "NoMatch",
            "NoMatchDestination",
            "NoMatchOrigin",
            "NoRoute",
            "OutOfBounds",
            "OutOfBoundsDestination",
            "OutOfBoundsOrigin",
            "Other",
            "Violation",
        )

    def test_keeps_every_official_error_code(self):
        for code in MATRIX_ERROR_CODES:
            with self.subTest(code=code):
                data = _one_cell_matrix({"Distance": 0, "Duration": 0, "Error": code})

                assert normalize_matrix(data, 1, 1) == [
                    [{"distance": None, "duration": None, "error": code}]
                ]

    def test_rejects_an_error_outside_the_official_codes(self):
        # Error is an enum, not free text: a value the plugin cannot explain
        # to the user, and a falsy one that would quietly read as "no error"
        # while the cell carries no distance or duration, are both broken.
        cases = (
            "No route could be calculated.",
            "noroute",
            "SomeFutureCode",
            0,
            False,
            None,
            "",
            {},
            [],
        )
        for error in cases:
            with (
                self.subTest(error=error),
                self.assertRaisesRegex(BrokenResponseError, "invalid error code"),
            ):
                normalize_matrix(_one_cell_matrix({"Error": error}), 1, 1)

    def test_a_zero_length_pair_is_a_valid_result(self):
        data = _one_cell_matrix({"Distance": 0, "Duration": 0})

        assert normalize_matrix(data, 1, 1) == [
            [{"distance": 0.0, "duration": 0.0, "error": ""}]
        ]

    def test_rejects_a_successful_cell_without_both_values(self):
        # Without an Error, Distance and Duration are required: a cell that
        # is missing one would be drawn as a successful OD line.
        cases = (
            {},
            {"Distance": 1000},
            {"Duration": 120},
            {"Distance": 1000, "Duration": None},
        )
        for cell in cases:
            with (
                self.subTest(cell=cell),
                self.assertRaisesRegex(BrokenResponseError, "malformed cell"),
            ):
                normalize_matrix(_one_cell_matrix(cell), 1, 1)

    def test_rejects_a_successful_cell_with_an_unusable_value(self):
        for key, value in _long_cases():
            with (
                self.subTest(key=key, value=value),
                self.assertRaisesRegex(BrokenResponseError, f"invalid {key} value"),
            ):
                cell = {"Distance": 1000, "Duration": 120}
                cell[key] = value
                normalize_matrix(_one_cell_matrix(cell), 1, 1)

    def test_keeps_a_whole_number_sent_as_a_float(self):
        data = _one_cell_matrix({"Distance": 1000.0, "Duration": 120.0})

        assert normalize_matrix(data, 1, 1) == [
            [{"distance": 1000.0, "duration": 120.0, "error": ""}]
        ]

    def test_rejects_a_matrix_with_the_wrong_number_of_rows(self):
        data = _one_cell_matrix({"Distance": 1000, "Duration": 120})

        with self.assertRaisesRegex(
            BrokenResponseError, "route matrix size does not match"
        ):
            normalize_matrix(data, 2, 1)

    def test_rejects_a_row_with_the_wrong_number_of_cells(self):
        data = _one_cell_matrix({"Distance": 1000, "Duration": 120})

        with self.assertRaisesRegex(
            BrokenResponseError, "route matrix size does not match"
        ):
            normalize_matrix(data, 1, 2)

    def test_rejects_a_row_that_contains_a_malformed_cell(self):
        data = {"RouteMatrix": [[{"Distance": 1000, "Duration": 120}, None]]}

        with self.assertRaisesRegex(BrokenResponseError, "malformed cell"):
            normalize_matrix(data, 1, 2)


if __name__ == "__main__":
    unittest.main()
