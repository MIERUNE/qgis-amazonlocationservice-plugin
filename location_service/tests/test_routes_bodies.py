import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from location_service.functions.routes_requests import (
    MAX_TRACE_DISTANCE_METERS,
    MAX_TRANSIT_MODES,
    TRACE_DISTANCE_SAFETY_FACTOR,
    TRANSIT_MODE_VALUES,
    IsolineOptions,
    MatrixOptions,
    RouteOptions,
    SnapOptions,
    TracePoint,
    build_isolines_body,
    build_matrix_body,
    build_routes_body,
    build_snap_body,
    haversine_meters,
    parse_iso_time,
)
from location_service.tests import HAS_QGIS

if HAS_QGIS:
    from location_service.functions.routes import RoutesFunctions
    from location_service.utils.configuration_handler import ConfigurationError

ORIGIN = (139.7, 35.6)
DESTINATION = (139.8, 35.7)
WAYPOINT = (139.75, 35.65)
CENTER = (139.7, 35.6)
DEPARTURE_TIME = "2026-08-26T09:00:00+09:00"
ARRIVAL_TIME = "2026-08-26T18:00:00+09:00"
TRACE_PAIR = (
    TracePoint(position=(139.7, 35.6)),
    TracePoint(position=(139.71, 35.61)),
)

ROUTES_HOST = "routes.geo.ap-northeast-1.amazonaws.com"
CREDENTIALS = ("ap-northeast-1", "v1.public.test")  # pragma: allowlist secret
REGION, API_KEY = CREDENTIALS

ROUTE_OPTIONS = RouteOptions(origin=ORIGIN, destination=DESTINATION)
ISOLINE_OPTIONS = IsolineOptions(center=CENTER, thresholds=(600,))
SNAP_OPTIONS = SnapOptions(trace_points=TRACE_PAIR)
MATRIX_OPTIONS = MatrixOptions(origins=(ORIGIN,), destinations=(DESTINATION,))


def _route_options(**overrides):
    """Returns RouteOptions for the shared origin and destination pair."""
    return RouteOptions(origin=ORIGIN, destination=DESTINATION, **overrides)


def _first_trace_point(**fields):
    """Builds a two-point snap body and returns its first TracePoint."""
    points = (TracePoint(position=(139.7, 35.6), **fields), TRACE_PAIR[1])
    return build_snap_body(SnapOptions(trace_points=points))["TracePoints"][0]


# One degree of longitude on the equator, measured the way the trace-length
# check measures it, so a test can ask for a trace of a given length.
DEGREE_METERS = haversine_meters((0.0, 0.0), (1.0, 0.0))


def _trace_spanning(kilometers):
    """Returns a two-point trace of that length along the equator."""
    degrees = kilometers * 1000.0 / DEGREE_METERS
    return (TracePoint(position=(0.0, 0.0)), TracePoint(position=(degrees, 0.0)))


def _matrix_pair_spanning(kilometers):
    """Returns a one-cell matrix spanning that distance on the equator."""
    degrees = kilometers * 1000.0 / DEGREE_METERS
    return MatrixOptions(
        origins=((0.0, 0.0),),
        destinations=((degrees, 0.0),),
    )


class _RecordingConfigurationHandler:
    """Returns fixed credentials and counts every configuration read."""

    def __init__(self):
        self.reads = 0

    def get_credentials(self):
        self.reads += 1
        return CREDENTIALS


class _RecordingApiHandler:
    """Records the requested URL and body instead of sending them."""

    def __init__(self):
        self.url = ""
        self.body = None

    def send_json_post_request(self, url, body):
        self.url = url
        self.body = body
        return {}


def _routes_without_network():
    """Builds a RoutesFunctions whose handlers never leave the process."""
    routes = RoutesFunctions.__new__(RoutesFunctions)
    routes.configuration_handler = _RecordingConfigurationHandler()
    routes.api_handler = _RecordingApiHandler()
    return routes


class TestRoutesBodyForRoadModes(unittest.TestCase):
    """Road modes send Simple geometry, leg summaries and a routing preference."""

    def test_car_sends_the_documented_minimum_body(self):
        assert build_routes_body(_route_options()) == {
            "Origin": [139.7, 35.6],
            "Destination": [139.8, 35.7],
            "TravelMode": "Car",
            "LegGeometryFormat": "Simple",
            "LegAdditionalFeatures": ["Summary"],
            "MaxAlternatives": 0,
            "OptimizeRoutingFor": "FastestRoute",
        }

    def test_optimize_for_is_sent_as_selected(self):
        body = build_routes_body(_route_options(optimize_for="ShortestRoute"))
        assert body["OptimizeRoutingFor"] == "ShortestRoute"

    def test_checked_avoid_options_become_true_flags(self):
        body = build_routes_body(_route_options(avoid=("TollRoads", "UTurns")))
        assert body["Avoid"] == {"TollRoads": True, "UTurns": True}

    def test_waypoints_are_wrapped_in_position_objects(self):
        body = build_routes_body(_route_options(waypoints=(WAYPOINT,)))
        assert body["Waypoints"] == [{"Position": [139.75, 35.65]}]


class TestRoutesBodyForTransitModes(unittest.TestCase):
    """Transit and Intermodal only send the fields AWS accepts for them."""

    def test_transit_drops_unsupported_fields(self):
        body = build_routes_body(
            _route_options(
                travel_mode="Transit",
                avoid=("TollRoads",),
                waypoints=(WAYPOINT,),
                optimize_for="ShortestRoute",
            )
        )
        assert body == {
            "Origin": [139.7, 35.6],
            "Destination": [139.8, 35.7],
            "TravelMode": "Transit",
            "LegGeometryFormat": "Simple",
            "LegAdditionalFeatures": ["Summary"],
            "MaxAlternatives": 0,
        }

    def test_intermodal_drops_unsupported_fields(self):
        body = build_routes_body(
            _route_options(
                travel_mode="Intermodal",
                avoid=("Ferries",),
                waypoints=(WAYPOINT,),
                optimize_for="ShortestRoute",
            )
        )
        assert body == {
            "Origin": [139.7, 35.6],
            "Destination": [139.8, 35.7],
            "TravelMode": "Intermodal",
            "LegGeometryFormat": "Simple",
            "LegAdditionalFeatures": ["Summary"],
            "MaxAlternatives": 0,
        }

    def test_transit_sends_the_allowed_modes_filter(self):
        body = build_routes_body(
            _route_options(
                travel_mode="Transit",
                transit_allowed_modes=("Bus", "Subway"),
            )
        )
        assert body["TravelModeOptions"] == {
            "Transit": {"AllowedModes": ["Bus", "Subway"]}
        }

    def test_transit_sends_the_excluded_modes_filter(self):
        body = build_routes_body(
            _route_options(
                travel_mode="Transit",
                transit_excluded_modes=("Ferry",),
            )
        )
        assert body["TravelModeOptions"] == {"Transit": {"ExcludedModes": ["Ferry"]}}

    def test_intermodal_sends_no_transit_mode_filter(self):
        body = build_routes_body(
            _route_options(
                travel_mode="Intermodal",
                transit_allowed_modes=("Bus",),
            )
        )
        assert "TravelModeOptions" not in body

    def test_allowed_and_excluded_modes_together_fail(self):
        for travel_mode in ("Transit", "Intermodal"):
            with (
                self.subTest(travel_mode=travel_mode),
                self.assertRaisesRegex(ValueError, "not both"),
            ):
                build_routes_body(
                    _route_options(
                        travel_mode=travel_mode,
                        transit_allowed_modes=("Bus",),
                        transit_excluded_modes=("Subway",),
                    )
                )

    def test_unknown_transit_mode_fails(self):
        with self.assertRaisesRegex(ValueError, "unknown transit mode"):
            build_routes_body(
                _route_options(
                    travel_mode="Transit",
                    transit_allowed_modes=("Rocket",),
                )
            )

    def test_the_documented_transit_modes_include_the_all_shortcut(self):
        # AWS documents 16 mode names, "All" among them, but accepts at
        # most 15 of them per request, so the full list is never sendable.
        assert len(TRANSIT_MODE_VALUES) == 16
        assert "All" in TRANSIT_MODE_VALUES
        assert MAX_TRANSIT_MODES == 15

    def test_the_all_transit_mode_is_accepted_on_its_own(self):
        body = build_routes_body(
            _route_options(travel_mode="Transit", transit_allowed_modes=("All",))
        )
        assert body["TravelModeOptions"] == {"Transit": {"AllowedModes": ["All"]}}

    def test_fifteen_transit_modes_are_accepted(self):
        modes = TRANSIT_MODE_VALUES[:MAX_TRANSIT_MODES]
        body = build_routes_body(
            _route_options(travel_mode="Transit", transit_allowed_modes=modes)
        )
        allowed = body["TravelModeOptions"]["Transit"]["AllowedModes"]
        assert allowed == list(modes)

    def test_listing_all_sixteen_transit_modes_fails(self):
        for field in ("transit_allowed_modes", "transit_excluded_modes"):
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ValueError, "at most 15 modes"),
            ):
                build_routes_body(
                    _route_options(
                        travel_mode="Transit", **{field: TRANSIT_MODE_VALUES}
                    )
                )

    def test_a_repeated_transit_mode_fails(self):
        # Duplicates would let a request exceed the AWS limit of 15 modes
        # while listing far fewer distinct ones.
        for field in ("transit_allowed_modes", "transit_excluded_modes"):
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ValueError, "duplicate transit modes"),
            ):
                build_routes_body(
                    _route_options(travel_mode="Transit", **{field: ("Bus", "Bus")})
                )

    def test_max_alternatives_survives_for_transit(self):
        body = build_routes_body(
            _route_options(travel_mode="Transit", max_alternatives=3)
        )
        assert body["MaxAlternatives"] == 3


class TestRoutesBodyDepartureTimes(unittest.TestCase):
    """At most one departure or arrival choice ever reaches the API."""

    def test_omits_every_time_field_when_nothing_is_chosen(self):
        body = build_routes_body(_route_options())
        assert "DepartNow" not in body
        assert "DepartureTime" not in body
        assert "ArrivalTime" not in body

    def test_depart_now_is_sent_as_a_flag(self):
        body = build_routes_body(_route_options(depart_now=True))
        assert body["DepartNow"] is True
        assert "DepartureTime" not in body

    def test_departure_time_is_sent_verbatim(self):
        body = build_routes_body(_route_options(departure_time=DEPARTURE_TIME))
        assert body["DepartureTime"] == DEPARTURE_TIME
        assert "DepartNow" not in body

    def test_arrival_time_is_sent_verbatim(self):
        body = build_routes_body(_route_options(arrival_time=ARRIVAL_TIME))
        assert body["ArrivalTime"] == ARRIVAL_TIME
        assert "DepartureTime" not in body

    def test_two_time_choices_at_once_fail(self):
        cases = (
            {"depart_now": True, "departure_time": DEPARTURE_TIME},
            {"depart_now": True, "arrival_time": ARRIVAL_TIME},
            {"departure_time": DEPARTURE_TIME, "arrival_time": ARRIVAL_TIME},
            {
                "depart_now": True,
                "departure_time": DEPARTURE_TIME,
                "arrival_time": ARRIVAL_TIME,
            },
        )
        for overrides in cases:
            with (
                self.subTest(overrides=sorted(overrides)),
                self.assertRaisesRegex(ValueError, "only one"),
            ):
                build_routes_body(_route_options(**overrides))

    def test_depart_now_must_be_a_boolean(self):
        # Truthy or falsy stand-ins would reach the API as DepartNow: true,
        # or silently disable a departure the caller asked for.
        for depart_now in (1, 0, -1, "true", None):
            with (
                self.subTest(depart_now=depart_now),
                self.assertRaisesRegex(ValueError, "DepartNow must be true or false"),
            ):
                build_routes_body(_route_options(depart_now=depart_now))


def _departure(value):
    """Returns the DepartureTime a body carries for that input."""
    return build_routes_body(_route_options(departure_time=value))["DepartureTime"]


class TestIsoTimeValidation(unittest.TestCase):
    """
    Times are checked against the AWS pattern, calendar, clock and offset.

    The check is written by hand instead of delegating to fromisoformat,
    which accepts a different syntax on every Python version: the plugin
    must judge a time the same way on the 3.9 of QGIS 3.34 LTR and on the
    3.12 of QGIS 4.2.
    """

    def _assert_refused(self, value):
        """Asserts the value is refused as a DepartureTime."""
        with self.assertRaisesRegex(ValueError, "DepartureTime must be a valid"):
            _departure(value)

    def test_utc_and_offset_times_are_sent_verbatim(self):
        for value in ("2026-08-26T09:00:00Z", DEPARTURE_TIME):
            with self.subTest(value=value):
                assert _departure(value) == value

    def test_a_time_without_an_offset_fails(self):
        with self.assertRaisesRegex(ValueError, "timezone offset"):
            _departure("2026-08-26T09:00:00")

    def test_the_year_spans_the_documented_range(self):
        for value in ("1000-01-01T00:00:00Z", "2999-12-31T23:59:59Z"):
            with self.subTest(value=value):
                assert _departure(value) == value

    def test_a_year_outside_the_documented_range_fails(self):
        for value in ("0999-12-31T23:59:59Z", "3000-01-01T00:00:00Z"):
            with self.subTest(value=value):
                self._assert_refused(value)

    def test_a_leap_second_is_accepted(self):
        assert _departure("2026-12-31T23:59:60Z") == "2026-12-31T23:59:60Z"

    def test_a_time_followed_by_a_newline_fails(self):
        for value in ("2026-08-26T09:00:00Z\n", "2026-08-26T09:00:00+09:00\n"):
            with self.subTest(value=value):
                self._assert_refused(value)

    def test_one_to_nine_fraction_digits_are_accepted(self):
        for digits in (1, 9):
            value = f"2026-08-26T09:00:00.{'1' * digits}+09:00"
            with self.subTest(digits=digits):
                assert _departure(value) == value

    def test_an_empty_or_too_long_fraction_fails(self):
        for value in (
            "2026-08-26T09:00:00.+09:00",
            "2026-08-26T09:00:00.1234567890+09:00",
        ):
            with self.subTest(value=value):
                self._assert_refused(value)

    def test_the_offset_spans_the_documented_hour_range(self):
        for value in (
            "2026-08-26T09:00:00+00:00",
            "2026-08-26T09:00:00+14:59",
            "2026-08-26T09:00:00-14:59",
            "2026-08-26T09:00:00+15:00",
            "2026-08-26T09:00:00+23:59",
            "2026-08-26T09:00:00-23:59",
        ):
            with self.subTest(value=value):
                assert _departure(value) == value

    def test_an_offset_outside_the_documented_range_fails(self):
        for value in (
            "2026-08-26T09:00:00+24:00",
            "2026-08-26T09:00:00+14:60",
            "2026-08-26T09:00:00+99:00",
            "2026-08-26T09:00:00+0900",
            "2026-08-26T09:00:00+9:00",
        ):
            with self.subTest(value=value):
                self._assert_refused(value)

    def test_a_calendar_date_that_does_not_exist_fails(self):
        for value in (
            "2026-02-31T09:00:00+09:00",
            "2026-02-29T09:00:00+09:00",
            "2026-13-01T09:00:00+09:00",
            "2026-00-10T09:00:00+09:00",
            "2026-08-00T09:00:00+09:00",
        ):
            with self.subTest(value=value):
                self._assert_refused(value)

    def test_a_leap_day_of_a_leap_year_is_accepted(self):
        assert _departure("2024-02-29T09:00:00Z") == "2024-02-29T09:00:00Z"

    def test_a_clock_time_that_does_not_exist_fails(self):
        for value in (
            "2026-08-26T24:00:00+09:00",
            "2026-08-26T25:00:00+09:00",
            "2026-08-26T09:60:00+09:00",
            "2026-08-26T09:00:61+09:00",
        ):
            with self.subTest(value=value):
                self._assert_refused(value)

    def test_the_rejected_time_is_named_by_its_own_field(self):
        cases = (
            ("departure_time", "DepartureTime must be a valid"),
            ("arrival_time", "ArrivalTime must be a valid"),
        )
        for field, message in cases:
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ValueError, message),
            ):
                build_routes_body(
                    _route_options(**{field: "2026-02-31T18:00:00+09:00"})
                )


UNICODE_DIGIT_TIMES = (
    "202\u0666-08-26T09:00:00Z",
    "2026-08-2\u0666T09:00:00Z",
    "2026-08-26T09:00:00.\u0665Z",
    "2026-08-26T09:00:00+0\u0669:00",
    "2026-08-26T09:00:00+09:0\u0669",
)


class TestParseIsoTime(unittest.TestCase):
    """Request validation and response parsing use the same time rules."""

    def test_reads_the_z_suffix_and_an_explicit_offset(self):
        cases = (
            ("2026-08-26T09:00:00Z", timedelta(0)),
            ("2026-08-26T09:00:00+09:00", timedelta(hours=9)),
            ("2026-08-26T09:00:00-05:30", timedelta(hours=-5, minutes=-30)),
        )
        for value, offset in cases:
            with self.subTest(value=value):
                parsed = parse_iso_time(value)

                assert parsed == datetime(2026, 8, 26, 9, 0, tzinfo=timezone(offset))
                assert parsed.utcoffset() == offset

    def test_every_fraction_length_reads_as_the_same_microsecond(self):
        cases = (
            ("1", 100_000),
            ("12", 120_000),
            ("123456", 123_456),
            ("1234567", 123_456),
            ("123456789", 123_456),
            ("999999999", 999_999),
        )
        for fraction, microsecond in cases:
            with self.subTest(fraction=fraction):
                parsed = parse_iso_time(f"2026-08-26T09:00:00.{fraction}Z")

                assert parsed.microsecond == microsecond

    def test_a_leap_second_is_read_as_the_next_second(self):
        assert parse_iso_time("2026-12-31T23:59:60Z") == datetime(
            2027, 1, 1, 0, 0, tzinfo=timezone.utc
        )
        assert parse_iso_time("2026-08-26T09:00:60+09:00") == datetime(
            2026, 8, 26, 9, 1, tzinfo=timezone(timedelta(hours=9))
        )

    def test_returns_none_for_anything_that_is_not_such_a_time(self):
        cases = (
            None,
            20260826,
            b"2026-08-26T09:00:00Z",
            ["2026-08-26T09:00:00Z"],
            "",
            "not-a-time",
            "2026-08-26T09:00:00",
            "2026-08-26T09:00:00Z\n",
            "2026-02-31T09:00:00Z",
            "2026-08-26T09:00:61Z",
        )
        for value in cases:
            with self.subTest(value=value):
                assert parse_iso_time(value) is None

    def test_a_unicode_digit_is_not_a_digit_of_a_time(self):
        for value in UNICODE_DIGIT_TIMES:
            with self.subTest(value=ascii(value)):
                assert parse_iso_time(value) is None

    def test_the_request_check_refuses_a_unicode_digit_as_well(self):
        for value in UNICODE_DIGIT_TIMES:
            with (
                self.subTest(value=ascii(value)),
                self.assertRaisesRegex(ValueError, "DepartureTime must be a valid"),
            ):
                _departure(value)

    def test_the_request_check_accepts_exactly_what_the_parser_reads(self):
        cases = (
            "2026-08-26T09:00:00Z",
            "2026-08-26T09:00:00+15:00",
            "2026-12-31T23:59:60Z",
            "2026-08-26T09:00:00",
            "2026-08-26T09:00:00Z\n",
            "2026-02-31T09:00:00Z",
            "not-a-time",
        )
        for value in cases:
            with self.subTest(value=value):
                try:
                    accepted = _departure(value) == value
                except ValueError:
                    accepted = False

                assert accepted == (parse_iso_time(value) is not None)


class TestRoutesBodyCountLimits(unittest.TestCase):
    """Waypoint and alternative counts stay inside the documented API limits."""

    def test_up_to_23_waypoints_are_accepted(self):
        body = build_routes_body(_route_options(waypoints=(WAYPOINT,) * 23))
        assert len(body["Waypoints"]) == 23

    def test_more_than_23_waypoints_fail(self):
        with self.assertRaisesRegex(ValueError, "at most 23 points"):
            build_routes_body(_route_options(waypoints=(WAYPOINT,) * 24))

    def test_zero_alternatives_is_sent_explicitly(self):
        body = build_routes_body(_route_options(max_alternatives=0))
        assert body["MaxAlternatives"] == 0

    def test_five_alternatives_are_accepted(self):
        body = build_routes_body(_route_options(max_alternatives=5))
        assert body["MaxAlternatives"] == 5

    def test_more_than_five_alternatives_fail(self):
        with self.assertRaisesRegex(ValueError, "between 0 and 5"):
            build_routes_body(_route_options(max_alternatives=6))


class TestIsolinesBody(unittest.TestCase):
    """Isoline bodies name the center by direction and request Simple geometry."""

    def test_direction_becomes_the_position_key(self):
        for direction in ("Origin", "Destination"):
            with self.subTest(direction=direction):
                body = build_isolines_body(
                    IsolineOptions(
                        center=CENTER, direction=direction, thresholds=(600,)
                    )
                )
                assert body == {
                    direction: [139.7, 35.6],
                    "Thresholds": {"Time": [600]},
                    "TravelMode": "Car",
                    "IsolineGeometryFormat": "Simple",
                }

    def test_unknown_direction_fails(self):
        with self.assertRaisesRegex(ValueError, "Direction must be one of"):
            build_isolines_body(
                IsolineOptions(center=CENTER, direction="Middle", thresholds=(600,))
            )

    def test_thresholds_accept_the_documented_maximums(self):
        for threshold_type, value in (("Time", 10800), ("Distance", 300000)):
            with self.subTest(threshold_type=threshold_type):
                body = build_isolines_body(
                    IsolineOptions(
                        center=CENTER,
                        threshold_type=threshold_type,
                        thresholds=(value,),
                    )
                )
                assert body["Thresholds"] == {threshold_type: [value]}

    def test_thresholds_above_the_maximum_fail(self):
        cases = (
            ("Time", 10801, "at most 10800 seconds"),
            ("Distance", 300001, "at most 300000 meters"),
        )
        for threshold_type, value, message in cases:
            with (
                self.subTest(threshold_type=threshold_type),
                self.assertRaisesRegex(ValueError, message),
            ):
                build_isolines_body(
                    IsolineOptions(
                        center=CENTER,
                        threshold_type=threshold_type,
                        thresholds=(value,),
                    )
                )

    def test_between_one_and_five_thresholds_are_accepted(self):
        for count in (1, 5):
            with self.subTest(count=count):
                thresholds = tuple(range(600, 600 + count))
                body = build_isolines_body(
                    IsolineOptions(center=CENTER, thresholds=thresholds)
                )
                assert body["Thresholds"]["Time"] == list(thresholds)

    def test_no_thresholds_and_more_than_five_fail(self):
        for count in (0, 6):
            with (
                self.subTest(count=count),
                self.assertRaisesRegex(ValueError, "between 1 and 5"),
            ):
                build_isolines_body(
                    IsolineOptions(
                        center=CENTER, thresholds=tuple(range(600, 600 + count))
                    )
                )

    def test_transit_travel_modes_are_rejected(self):
        for travel_mode in ("Transit", "Intermodal"):
            with (
                self.subTest(travel_mode=travel_mode),
                self.assertRaisesRegex(ValueError, "TravelMode must be one of"),
            ):
                build_isolines_body(
                    IsolineOptions(
                        center=CENTER, thresholds=(600,), travel_mode=travel_mode
                    )
                )


class TestSnapBody(unittest.TestCase):
    """Snap bodies keep every optional trace field inside its documented range."""

    def test_snap_body_carries_the_optional_trace_fields(self):
        points = (
            TracePoint(
                position=(139.7, 35.6),
                timestamp=DEPARTURE_TIME,
                heading=90,
                speed=12.5,
            ),
            TracePoint(position=(139.71, 35.61)),
        )
        assert build_snap_body(SnapOptions(trace_points=points)) == {
            "TracePoints": [
                {
                    "Position": [139.7, 35.6],
                    "Timestamp": DEPARTURE_TIME,
                    "Heading": 90.0,
                    "Speed": 12.5,
                },
                {"Position": [139.71, 35.61]},
            ],
            "SnapRadius": 300,
            "TravelMode": "Car",
            "SnappedGeometryFormat": "Simple",
        }

    def test_two_and_5000_trace_points_are_accepted(self):
        for count in (2, 5000):
            with self.subTest(count=count):
                points = (TracePoint(position=(139.7, 35.6)),) * count
                body = build_snap_body(SnapOptions(trace_points=points))
                assert len(body["TracePoints"]) == count

    def test_fewer_than_two_and_more_than_5000_trace_points_fail(self):
        for count in (1, 5001):
            with (
                self.subTest(count=count),
                self.assertRaisesRegex(ValueError, "between 2 and 5000"),
            ):
                points = (TracePoint(position=(139.7, 35.6)),) * count
                build_snap_body(SnapOptions(trace_points=points))

    def test_snap_radius_may_span_zero_to_10000_meters(self):
        for radius in (0, 10000):
            with self.subTest(radius=radius):
                body = build_snap_body(
                    SnapOptions(trace_points=TRACE_PAIR, snap_radius=radius)
                )
                assert body["SnapRadius"] == radius

    def test_snap_radius_outside_the_api_range_fails(self):
        for radius in (-1, 10001):
            with (
                self.subTest(radius=radius),
                self.assertRaisesRegex(ValueError, "between 0 and 10000"),
            ):
                build_snap_body(
                    SnapOptions(trace_points=TRACE_PAIR, snap_radius=radius)
                )

    def test_trace_timestamp_keeps_its_utc_offset(self):
        for timestamp in ("2026-08-26T09:00:00Z", DEPARTURE_TIME):
            with self.subTest(timestamp=timestamp):
                point = _first_trace_point(timestamp=timestamp)
                assert point["Timestamp"] == timestamp

    def test_trace_timestamp_without_an_offset_fails(self):
        with self.assertRaisesRegex(ValueError, "timezone offset"):
            _first_trace_point(timestamp="2026-08-26T09:00:00")

    def test_trace_timestamp_of_the_right_shape_that_does_not_exist_fails(self):
        with self.assertRaisesRegex(ValueError, "Timestamp must be a valid"):
            _first_trace_point(timestamp="2026-02-31T09:00:00Z")

    def test_heading_may_span_a_full_turn(self):
        for heading in (0, 360):
            with self.subTest(heading=heading):
                assert _first_trace_point(heading=heading)["Heading"] == heading

    def test_heading_outside_a_full_turn_fails(self):
        for heading in (-1, 361):
            with (
                self.subTest(heading=heading),
                self.assertRaisesRegex(ValueError, "between 0 and 360"),
            ):
                _first_trace_point(heading=heading)

    def test_speed_may_be_zero(self):
        assert _first_trace_point(speed=0)["Speed"] == 0.0

    def test_negative_speed_fails(self):
        with self.assertRaisesRegex(ValueError, "zero or more"):
            _first_trace_point(speed=-0.5)


class TestSnapTraceDistanceLimit(unittest.TestCase):
    """The trace-length check keeps a safety margin below the AWS 500 km limit."""

    def test_the_effective_limit_is_a_half_percent_below_the_aws_limit(self):
        assert MAX_TRACE_DISTANCE_METERS == 500_000
        assert MAX_TRACE_DISTANCE_METERS * TRACE_DISTANCE_SAFETY_FACTOR == 497_500

    def test_a_trace_below_the_effective_limit_is_accepted(self):
        body = build_snap_body(SnapOptions(trace_points=_trace_spanning(497)))

        assert len(body["TracePoints"]) == 2

    def test_a_trace_between_the_effective_and_aws_limits_fails(self):
        # The check measures an approximate distance, so it stops short of
        # 500 km rather than risk sending a trace AWS would reject.
        with self.assertRaisesRegex(ValueError, "safety margin"):
            build_snap_body(SnapOptions(trace_points=_trace_spanning(499)))

    def test_a_trace_longer_than_the_aws_limit_fails(self):
        with self.assertRaisesRegex(ValueError, "500 km"):
            build_snap_body(SnapOptions(trace_points=_trace_spanning(600)))

    def test_the_whole_trace_counts_not_just_the_longest_step(self):
        # Six short steps stay well inside the limit individually and only
        # exceed it once they are added up.
        points = tuple(
            TracePoint(position=(step * 100_000.0 / DEGREE_METERS, 0.0))
            for step in range(6)
        )

        with self.assertRaisesRegex(ValueError, "500 km"):
            build_snap_body(SnapOptions(trace_points=points))


class TestMatrixBody(unittest.TestCase):
    """Matrix requests stay within the Unbounded usage limits."""

    def test_matrix_is_always_unbounded(self):
        options = MatrixOptions(origins=(ORIGIN,), destinations=(DESTINATION,))
        assert build_matrix_body(options) == {
            "Origins": [{"Position": [139.7, 35.6]}],
            "Destinations": [{"Position": [139.8, 35.7]}],
            "TravelMode": "Car",
            "RoutingBoundary": {"Unbounded": True},
        }

    def test_a_100_cell_matrix_is_accepted(self):
        options = MatrixOptions(
            origins=(ORIGIN,) * 10, destinations=(DESTINATION,) * 10
        )
        body = build_matrix_body(options)
        assert len(body["Origins"]) == 10
        assert len(body["Destinations"]) == 10

    def test_more_than_100_cells_fail(self):
        options = MatrixOptions(
            origins=(ORIGIN,) * 11, destinations=(DESTINATION,) * 10
        )
        with self.assertRaisesRegex(ValueError, "must not exceed 100"):
            build_matrix_body(options)

    def test_a_pair_at_10000_km_is_accepted(self):
        body = build_matrix_body(_matrix_pair_spanning(10_000))

        assert len(body["Origins"]) == 1
        assert len(body["Destinations"]) == 1

    def test_a_pair_beyond_10000_km_fails(self):
        with self.assertRaisesRegex(ValueError, "10,000 km"):
            build_matrix_body(_matrix_pair_spanning(10_000.001))

    def test_an_almost_antipodal_pair_reports_the_usage_limit(self):
        options = MatrixOptions(
            origins=((-34.60610402371694, 58.87631793541516),),
            destinations=((145.39389597444995, -58.87631794344365),),
        )

        with self.assertRaisesRegex(ValueError, "10,000 km"):
            build_matrix_body(options)

    def test_every_origin_destination_pair_is_checked(self):
        options = MatrixOptions(
            origins=((0.0, 0.0), (-10.0, 0.0)),
            destinations=((10.0, 0.0), (80.0, 0.0)),
        )

        with self.assertRaisesRegex(ValueError, "origin 2 and destination 2"):
            build_matrix_body(options)

    def test_an_empty_side_fails(self):
        cases = (
            MatrixOptions(origins=(), destinations=(DESTINATION,)),
            MatrixOptions(origins=(ORIGIN,), destinations=()),
        )
        for options in cases:
            with (
                self.subTest(origins=len(options.origins)),
                self.assertRaisesRegex(ValueError, "at least one origin"),
            ):
                build_matrix_body(options)


class TestOptionsSnapshotTheirInput(unittest.TestCase):
    """
    Options copy every collection they are given.

    The dialog captures the inputs once and the body is built later, so a
    list the caller keeps editing must not change the request that was
    captured.
    """

    def test_route_options_cannot_be_reassigned(self):
        options = _route_options()
        with self.assertRaisesRegex(FrozenInstanceError, "travel_mode"):
            options.travel_mode = "Truck"

    def test_every_collection_is_stored_as_a_tuple(self):
        options = RouteOptions(
            origin=[139.7, 35.6],
            destination=[139.8, 35.7],
            waypoints=[[139.75, 35.65]],
            avoid=["TollRoads"],
            transit_allowed_modes=["Bus"],
            transit_excluded_modes=["Ferry"],
        )

        assert options.origin == (139.7, 35.6)
        assert options.destination == (139.8, 35.7)
        assert options.waypoints == ((139.75, 35.65),)
        assert options.avoid == ("TollRoads",)
        assert options.transit_allowed_modes == ("Bus",)
        assert options.transit_excluded_modes == ("Ferry",)

    def test_editing_the_route_lists_afterwards_leaves_the_body_unchanged(self):
        origin = [139.7, 35.6]
        waypoint = [139.75, 35.65]
        waypoints = [waypoint]
        avoid = ["TollRoads"]
        options = RouteOptions(
            origin=origin,
            destination=[139.8, 35.7],
            waypoints=waypoints,
            avoid=avoid,
        )
        before = build_routes_body(options)

        origin[0] = 0.0
        waypoint[0] = 0.0
        waypoints.append([1.0, 1.0])
        avoid.append("Ferries")

        assert build_routes_body(options) == before

    def test_editing_the_isoline_lists_afterwards_leaves_the_body_unchanged(self):
        center = [139.7, 35.6]
        thresholds = [600]
        options = IsolineOptions(center=center, thresholds=thresholds)
        before = build_isolines_body(options)

        center[0] = 0.0
        thresholds.append(1200)

        assert build_isolines_body(options) == before

    def test_editing_the_snap_lists_afterwards_leaves_the_body_unchanged(self):
        position = [139.7, 35.6]
        trace_points = [TracePoint(position=position), TRACE_PAIR[1]]
        options = SnapOptions(trace_points=trace_points)
        before = build_snap_body(options)

        position[0] = 0.0
        trace_points.append(TracePoint(position=(139.72, 35.62)))

        assert build_snap_body(options) == before

    def test_editing_the_matrix_lists_afterwards_leaves_the_body_unchanged(self):
        origin = [139.7, 35.6]
        origins = [origin]
        destinations = [[139.8, 35.7]]
        options = MatrixOptions(origins=origins, destinations=destinations)
        before = build_matrix_body(options)

        origin[0] = 0.0
        origins.append([1.0, 1.0])
        destinations.append([2.0, 2.0])

        assert build_matrix_body(options) == before


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestRoutesEndpoints(unittest.TestCase):
    """Every operation posts to its own path on the regional Routes host."""

    def test_each_operation_posts_to_its_documented_path(self):
        cases = (
            ("request_routes", ROUTE_OPTIONS, "/v2/routes"),
            ("request_isolines", ISOLINE_OPTIONS, "/v2/isolines"),
            ("request_snap_to_roads", SNAP_OPTIONS, "/v2/snap-to-roads"),
            ("request_route_matrix", MATRIX_OPTIONS, "/v2/route-matrix"),
        )
        for method_name, options, path in cases:
            with self.subTest(method=method_name):
                routes = _routes_without_network()
                getattr(routes, method_name)(options, credentials=CREDENTIALS)

                parsed = urlparse(routes.api_handler.url)
                assert parsed.hostname == ROUTES_HOST
                assert parsed.path == path
                assert routes.api_handler.body
                assert routes.configuration_handler.reads == 0

    def test_the_api_key_is_url_encoded(self):
        routes = _routes_without_network()
        # A key containing "&" or "#" must not alter the query structure.
        routes.request_routes(ROUTE_OPTIONS, credentials=("us-east-1", "a&b#c"))

        url = routes.api_handler.url
        assert urlparse(url).hostname == "routes.geo.us-east-1.amazonaws.com"
        assert url.endswith("/v2/routes?key=a%26b%23c")

    def test_calculate_routes_delegates_to_the_request_path(self):
        routes = _routes_without_network()
        routes.calculate_routes(139.7, 35.6, 139.8, 35.7, credentials=CREDENTIALS)

        parsed = urlparse(routes.api_handler.url)
        assert parsed.hostname == ROUTES_HOST
        assert parsed.path == "/v2/routes"
        assert routes.api_handler.body["Origin"] == [139.7, 35.6]
        assert routes.api_handler.body["Destination"] == [139.8, 35.7]
        assert routes.configuration_handler.reads == 0


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestCapturedCredentialsAreChecked(unittest.TestCase):
    """
    Captured credentials skip the settings read, not the safety checks.

    The region is interpolated into the request hostname, so a caller that
    passes its own pair must not be able to point the API key elsewhere.
    """

    def test_a_valid_pair_builds_the_regional_url(self):
        url = _routes_without_network().build_endpoint(
            "v2/routes", credentials=CREDENTIALS
        )

        assert urlparse(url).hostname == ROUTES_HOST

    def test_a_region_that_is_not_a_region_code_is_refused(self):
        cases = (
            "",
            None,
            "evil.test/",
            "ap-northeast-1.evil.test",
            "ap-northeast-1:8080",
            "ap_northeast_1",
            "AP-NORTHEAST-1",
            "ap-northeast-1\n",
        )
        for region in cases:
            with (
                self.subTest(region=region),
                self.assertRaisesRegex(ConfigurationError, "not a valid region code"),
            ):
                _routes_without_network().build_endpoint(
                    "v2/routes", credentials=(region, API_KEY)
                )

    def test_an_empty_api_key_is_refused(self):
        for apikey in ("", None):
            with (
                self.subTest(apikey=apikey),
                self.assertRaisesRegex(ConfigurationError, "API key is empty"),
            ):
                _routes_without_network().build_endpoint(
                    "v2/routes", credentials=(REGION, apikey)
                )

    def test_a_key_of_only_whitespace_is_refused(self):
        for apikey in (" ", "\n", "\t", "  \n "):
            with (
                self.subTest(apikey=repr(apikey)),
                self.assertRaisesRegex(ConfigurationError, "API key is empty"),
            ):
                _routes_without_network().build_endpoint(
                    "v2/routes", credentials=(REGION, apikey)
                )

    def test_a_key_pasted_with_whitespace_is_stripped_into_the_url(self):
        url = _routes_without_network().build_endpoint(
            "v2/routes", credentials=(REGION, f" {API_KEY} ")
        )

        assert url.endswith(f"/v2/routes?key={API_KEY}")

    def test_an_unchecked_region_never_reaches_the_network(self):
        routes = _routes_without_network()

        with self.assertRaisesRegex(ConfigurationError, "not a valid region code"):
            routes.request_routes(ROUTE_OPTIONS, credentials=("evil.test/", API_KEY))

        assert routes.api_handler.url == ""
        assert routes.api_handler.body is None


if __name__ == "__main__":
    unittest.main()
