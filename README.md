# Paseo for Home Assistant

A Home Assistant custom integration that shows what [Paseo](https://paseo.sh) agents are doing.

- **Sensors** for the agent fleet, quota waits and the CI queue, and a bridge-online sensor.
- **Services**: `paseo.resume_quota_agent`, `paseo.cancel_ci_run`.

## Requirements

- Home Assistant 2026.10.0 or newer, with an internal URL set (Settings, System, Network), because the webhook is local-only.
- The companion Paseo plugin `hass-bridge` is required and is **not published yet**. This repository holds only the Home Assistant integration, which is useful only once that plugin is installed on the Paseo host. From the plugin the integration needs a base URL (for example `http://paseo-host.local:7421`) and a bridge token; the two must be set up in the same version.
- The two must reach each other over your local network.
- The quota and CI entities need optional companion plugins on the Paseo host (`usage-auto-resume` for quota waits, a compatible Paseo CI plugin for the CI queue). Without `usage-auto-resume` the quota sensor reads 0; without a CI plugin the CI queue sensor is unavailable.

## How it works

The plugin sends a JSON payload to a webhook that this integration registers in Home Assistant, and Home Assistant calls the plugin's bearer-authenticated REST API to resume quota waits and cancel CI runs. The two sides share only a bridge token. The plugin holds no Home Assistant token.

## Install

### HACS (custom repository)

1. In HACS, open the three-dot menu, **Custom repositories**.
2. Add `https://github.com/mleibetseder/ha-paseo` with category **Integration**.
3. Install **Paseo** and restart Home Assistant.

### Manual

1. Download `paseo.zip` from the latest release and unpack it into `custom_components/paseo/` in your Home Assistant configuration folder (or copy that folder from this repository).
2. Restart Home Assistant.

## Configuration (pairing)

1. In Home Assistant: Settings, Devices & services, **Add integration**, **Paseo**.
2. Enter the plugin URL (for example `http://paseo-host.local:7421`) and the bridge token that you created for the plugin.
3. Home Assistant checks the plugin, creates its own webhook and registers the webhook URL with the plugin. The entities fill in after the first push.

Errors: **Failed to connect** (URL wrong or the plugin not running), **Invalid authentication** (wrong token), an unsupported contract version (update the plugin and the integration together), or no internal URL (set one under Settings, System, Network). **Reconfigure** changes the URL or token of the same plugin instance; a rejected token starts reauthentication.

## Options

The integration has no options. Version 0.4.0 removed the notification options together with the question notifications.

The texts are available in English and German.

## Entities

All entities belong to one device, **Paseo**. State entities are unavailable until the first push and once the data is stale (no push for twice the announced interval).

| Entity | State |
|---|---|
| `binary_sensor.paseo_bridge_online` | on while pushes keep arriving (diagnostic) |
| `sensor.paseo_last_push` | time of the last accepted push (diagnostic) |
| `binary_sensor.paseo_agent_working` | on while an agent is busy (mid-turn) |
| `binary_sensor.paseo_needs_attention` | on when an agent waits for a permission |
| `sensor.paseo_active_agents` | number of busy agents (mid-turn), named "Busy agents" |
| `sensor.paseo_open_agents` | number of open agents (running or idle); unavailable with an older plugin |
| `sensor.paseo_quota_waiting` | number of rate-limited agents waiting for their quota |
| `sensor.paseo_ci_queue` | `idle`, `running`, `queued` or `blocked` |

Details are in the entity attributes.

## Services

Every service takes an optional `config_entry_id` (only needed when more than one Paseo entry is loaded).

| Service | What it does |
|---|---|
| `paseo.resume_quota_agent` | Asks the plugin to resume rate-limited agents now (`agent_id`, default `all`). Returns the outcome per agent. |
| `paseo.cancel_ci_run` | Cancels a queued or running CI run (`run_id`). |

An unreachable plugin raises a Home Assistant error, and a rejected token starts reauthentication.

## Data sent

The plugin sends, in clear text over your local network (HTTP, no TLS) and only to Home Assistant: agent, quota and CI-queue information, including agent ids, workspace and branch names. Home Assistant holds it in its state machine; list attributes are excluded from the recorder. In the other direction Home Assistant sends the bridge token as a bearer header, the agent id (quota resume) and the CI run id (cancel).

Diagnostics contain the plugin host, counts and state, and leave out the token, the webhook id and all agent texts.

## Removal

Delete the integration under Settings, Devices & services, Paseo. Home Assistant tells the plugin to stop pushing. If you installed it by hand or through HACS, remove the integration files afterwards and restart Home Assistant. Remove the plugin and its token on the Paseo host.

## License

MIT, see [LICENSE](LICENSE).
