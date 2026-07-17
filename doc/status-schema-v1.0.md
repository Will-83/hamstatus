HamStatus JSON Specification v1.0

Required Fields

version
String
Example:
"1.0"

callsign
String
Example:
"W8MB"

state
Enum

Allowed Values

off_air
monitoring
on_air
portable
mobile
event
emcomm

frequency
Object

value
Decimal

unit
String

band

JSON example

{
  "version": "1.0",
  "callsign": "W8MB",
  "state": "monitoring",
  "activity": "Listening",
  "frequency": {
    "value": 147.090,
    "unit": "MHz",
    "band": "2m"
  },
  "mode": "FM",
  "location": {
    "description": "Mansfield, MO",
    "grid_square": "EM37"
  },
  "comment": "Monitoring local repeater.",
  "updated": "2026-07-17T12:00:00Z"
}
String

...
