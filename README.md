# Moving Intelligence for Home Assistant

A comprehensive Home Assistant integration for [Moving Intelligence](https://movingintelligence.com/) vehicle tracking and security devices (Mi50, MiBlock).

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=rweijnen&repository=moving-intelligence-ha&category=integration)

## Features

- 🚗 **Live GPS tracking** — current position with address resolution, pushed in real-time over WebSocket (no polling lag)
- ⚡ **Speed sensor** — current vehicle speed
- 🔋 **Battery monitoring** — vehicle battery voltage
- 🛣️ **Journey recording** — store completed journeys with full waypoints, distance, max/avg speed (much more than the official app shows!)
- 🔧 **Engine state** — binary sensor for engine on/off
- 🚓 **Immobilizer control** — block/unblock the engine via switch entity
- 📡 **Jamming detection** — alert when GPS/GSM signal is jammed
- 🔔 **Alarm count** — number of unread alarm messages
- 🎯 **Events** — `mi_home_journey_completed` for automations

## Installation (HACS)

The fastest way: click the button above to open the repository directly in your Home Assistant HACS.

Or manually:

1. Add this repo as a custom repository in HACS:
   - HACS → Integrations → ⋮ → Custom repositories
   - URL: `https://github.com/rweijnen/moving-intelligence-ha`
   - Category: Integration
2. Install "Moving Intelligence"
3. Restart Home Assistant
4. Settings → Devices & Services → Add Integration → Moving Intelligence
5. Enter your Moving Intelligence email + password

## Configuration

Required:
- **Email** — your Moving Intelligence account email
- **Password** — your Moving Intelligence account password

Optional:
- **API key** — for stable access to a subset of endpoints. Request at `aftersales@movingintelligence.nl` (not required; integration works fully without it).

### Options

After installation, you can configure:
- **Update interval** — how often to poll for updates (30–300 seconds, default 60)
- **Maximum stored journeys** — how many journeys to keep per vehicle (10–1000, default 100)

## Entities

For each vehicle on your account:

| Entity | Type | Description |
|--------|------|-------------|
| `device_tracker.<licence>_location` | device_tracker | GPS position, address, accuracy |
| `sensor.<licence>_speed` | sensor | Current speed (km/h) |
| `sensor.<licence>_address` | sensor | Current address or alias |
| `sensor.<licence>_battery_voltage` | sensor | Vehicle battery voltage |
| `sensor.<licence>_last_journey_distance` | sensor | Distance of last journey (km) |
| `sensor.<licence>_last_journey_duration` | sensor | Duration of last journey (min) |
| `sensor.<licence>_last_journey_max_speed` | sensor | Maximum speed during last journey (km/h) |
| `sensor.<licence>_last_journey_avg_speed` | sensor | Average moving speed during last journey (km/h) |
| `sensor.<licence>_alarm_count` | sensor | Number of unread alarms |
| `binary_sensor.<licence>_engine` | binary_sensor | Engine running |
| `binary_sensor.<licence>_jammed` | binary_sensor | GPS/GSM signal jammed |
| `switch.<licence>_immobilizer` | switch | Block/unblock the engine |
| `calendar.<licence>_journeys` | calendar | One event per recorded journey, browseable by date |

The `last_journey_distance` sensor exposes a `geojson` attribute containing
the full driven route as a GeoJSON `LineString`. This can be rendered on a
map by [ha-map-card](https://github.com/nathan-gs/ha-map-card) — see
[Dashboard examples](#dashboard-examples).

## Events

### `mi_home_journey_completed`

Fired whenever a journey is recorded (engine off or new journey detected).

Data:
- `entity_id` — MI vehicle ID
- `distance_km` — journey distance
- `max_speed` — maximum speed during journey
- `avg_speed` — average speed (excluding stops)
- `duration_min` — duration in minutes
- `waypoint_count` — number of GPS waypoints recorded

## Dashboard examples

### Show the last driven route on a map

The `last_journey_distance` sensor exposes the full waypoint trail as a
GeoJSON `LineString` in the `geojson` attribute. With
[ha-map-card](https://github.com/nathan-gs/ha-map-card) (HACS frontend
plugin), you can render the route on a map:

```yaml
type: custom:map-card
entities:
  - entity: device_tracker.h461hn_location
geojson:
  - entity: sensor.h461hn_last_journey_distance
    attribute: geojson
    color: "#df002b"
zoom: 12
```

The card will show your current vehicle position as a marker plus the full
last-journey route drawn as a red polyline.

### Browse historical journeys

Add the `calendar.h461hn_journeys` entity to a Calendar dashboard view in
HA. Each journey appears as a calendar event with distance, duration,
max/avg speed, waypoint count and a Google Maps link with start→end
coordinates.

## Example Automations

### Notify when a journey completes

```yaml
automation:
  - alias: "MI: Journey completed"
    trigger:
      - platform: event
        event_type: mi_home_journey_completed
    action:
      - service: notify.mobile_app_yourphone
        data:
          title: "Journey completed"
          message: >
            {{ trigger.event.data.distance_km }} km in
            {{ trigger.event.data.duration_min }} min
            (max {{ trigger.event.data.max_speed }} km/h)
```

### Alert when engine starts at night

```yaml
automation:
  - alias: "MI: Night engine start alert"
    trigger:
      - platform: state
        entity_id: binary_sensor.xx123xx_engine
        to: "on"
    condition:
      - condition: time
        after: "23:00:00"
        before: "06:00:00"
    action:
      - service: notify.mobile_app_yourphone
        data:
          title: "⚠️ Vehicle engine started"
          message: "At {{ now().strftime('%H:%M') }}"
```

## Roadmap

- [x] Immobilizer switch (block/unblock engine)
- [x] STOMP push for real-time position updates
- [ ] Custom Lovelace card for journey map visualization
- [ ] Calendar entity for journey history
- [ ] Alarm message details sensor

## How it works

This integration uses the same internal API that the official Moving Intelligence mobile app uses (`app.movingintelligence.com`), authenticating with your account credentials and maintaining a session cookie. Live position updates arrive in real time over a STOMP-over-WebSocket connection (the same channel the mobile app uses); slower-changing data like battery voltage, immobilizer status, and alarm messages are refreshed every few minutes. Journey boundaries are detected from the live stream and recorded with all GPS waypoints and per-point speed.

The official REST API (`api-app.movingintelligence.com`) is also supported as an optional supplementary data source if you provide an API key — but the integration is fully functional without it.

## Privacy & Security

- Credentials are stored encrypted in Home Assistant's `config_entries` database
- Session cookies are stored in HA's `.storage/` directory (encrypted at rest)
- No telemetry, no third-party connections
- All API calls go directly to Moving Intelligence servers

## Disclaimer

This is an unofficial integration. It is not affiliated with, endorsed by, or supported by Moving Intelligence B.V.

## License

MIT
