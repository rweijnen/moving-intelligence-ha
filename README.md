# Moving Intelligence for Home Assistant

A comprehensive Home Assistant integration for [Moving Intelligence](https://movingintelligence.com/) vehicle tracking and security devices (Mi50, MiBlock).

> **Status**: Early development. Phase 1 (core MVP) is in place.

## Features

- 🚗 **Live GPS tracking** — current position with address resolution
- ⚡ **Speed sensor** — current vehicle speed
- 🔋 **Battery monitoring** — vehicle battery voltage
- 🛣️ **Journey recording** — store completed journeys with full waypoints, distance, max/avg speed (much more than the official app shows!)
- 🔧 **Engine state** — binary sensor for engine on/off
- 📡 **Jamming detection** — alert when GPS/GSM signal is jammed
- 🔔 **Alarm count** — number of unread alarm messages
- 🎯 **Events** — `mi_home_journey_completed` for automations

## Installation (HACS)

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
| `sensor.<licence>_alarm_count` | sensor | Number of unread alarms |
| `binary_sensor.<licence>_engine` | binary_sensor | Engine running |
| `binary_sensor.<licence>_jammed` | binary_sensor | GPS/GSM signal jammed |

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

- [ ] Phase 2: Immobilizer switch entity (block/unblock engine)
- [ ] Phase 2: Alarm message sensor with details
- [ ] Phase 3: Custom Lovelace card for journey map visualization
- [ ] Phase 4: Calendar entity for journey history
- [ ] Phase 4: Push API webhook receiver for faster updates

## How it works

This integration uses the same internal API that the official Moving Intelligence mobile app uses (`app.movingintelligence.com`), authenticating with your account credentials and maintaining a session cookie. It polls live data periodically and detects journey boundaries to record complete trip data including all GPS waypoints with per-point speed.

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
