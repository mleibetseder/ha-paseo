# Paseo for Home Assistant

A Home Assistant custom integration that shows what [Paseo](https://paseo.sh) agents are doing and lets you answer their questions from your phone.

- **Sensors** for the agent fleet, progress, calibrated ETAs, deliverables, open questions, quota waits and the CI queue.
- **Question notifications** with up to three answer buttons. A tap answers the question, tells the agent and clears the push.
- **Services**: `paseo.answer_question`, `paseo.resume_quota_agent`, `paseo.cancel_ci_run`, `paseo.get_morning_brief`.

## Requirements

- Home Assistant 2026.10.0 or newer, with an internal URL set (Settings, System, Network), because the webhook is local-only.
- The companion Paseo plugin `hass-bridge` is required and is **not published yet**. This repository holds only the Home Assistant integration, which is useful only once that plugin is installed on the Paseo host. From the plugin the integration needs a base URL (for example `http://paseo-host.local:7421`) and a bridge token; the two must be set up in the same version.
- The two must reach each other over your local network.
- The quota and CI entities need optional companion plugins on the Paseo host (`usage-auto-resume` for quota waits, a compatible Paseo CI plugin for the CI queue). Without `usage-auto-resume` the quota sensor reads 0; without a CI plugin the CI queue sensor is unavailable. The progress entities use Paseo's progress state.

## How it works

The plugin sends a JSON payload to a webhook that this integration registers in Home Assistant, and Home Assistant calls the plugin's bearer-authenticated REST API to answer questions, resume quota waits and cancel CI runs. The two sides share only a bridge token. The plugin holds no Home Assistant token.

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

Settings, Devices & services, Paseo, **Configure**:

| Option | Default | Meaning |
|---|---|---|
| Phones to notify (`notify_targets`) | every `notify.mobile_app_*` service found | Phones that get the question pushes. |
| `bedtime_entity` | `input_boolean.bedtime` (an `input_boolean`, `binary_sensor` or `switch`) | When it is `on`, questions are held as set below and no reminder is sent. |
| `reminder_minutes` | 15 | One reminder for a blocking question still open after this long; 0 turns it off. |
| While bedtime is on (`quiet_hold`) | `hold_non_blocking` | `hold_non_blocking` holds questions that do not block the agent, `hold_all` holds all, `never` holds none. |

The texts are available in English and German.

## Entities

The aggregate entities belong to the hub device, **Paseo**. State entities are unavailable until the first push and once the data is stale (no push for twice the announced interval).

| Entity | State |
|---|---|
| `binary_sensor.paseo_bridge_online` | on while pushes keep arriving (diagnostic) |
| `sensor.paseo_last_push` | time of the last accepted push (diagnostic) |
| `binary_sensor.paseo_agent_working` | on while an agent is busy or a non-idle project works (updated within 60 minutes) |
| `binary_sensor.paseo_needs_attention` | on when a run is stuck, a ticket is overdue, a question blocks or an agent waits for a permission |
| `sensor.paseo_active_agents` | number of busy agents (mid-turn), named "Busy agents" |
| `sensor.paseo_open_agents` | number of open agents (running or idle); unavailable with an older plugin |
| `sensor.paseo_current_activity` | what the agent is doing now |
| `sensor.paseo_active_ticket` | id of the working ticket |
| `sensor.paseo_run_progress` | progress of the most recent run in % |
| `sensor.paseo_raw_eta`, `sensor.paseo_calibrated_eta`, `sensor.paseo_calibrated_finish` | minutes left, calibrated minutes left, finish time |
| `sensor.paseo_deliverables` | number of deliverables in the open runs |
| `sensor.paseo_open_questions` | number of open questions |
| `sensor.paseo_held_questions` | number of questions held back during bedtime |
| `sensor.paseo_projects_overview` | number of open, non-idle runs |
| `sensor.paseo_quota_waiting` | number of rate-limited agents waiting for their quota |
| `sensor.paseo_ci_queue` | `idle`, `running`, `queued` or `blocked` |

Details are in the entity attributes.

### Per-run devices

Every Paseo run gets its own device, **Paseo \<worktree\>**, linked to the hub. Its entities carry the worktree name, for example `sensor.paseo_<worktree>_progress` for a worktree called `my-project`:

| Entity | State |
|---|---|
| `sensor.paseo_<worktree>_progress` | progress of the run in % |
| `sensor.paseo_<worktree>_calibrated_eta`, `sensor.paseo_<worktree>_calibrated_finish` | calibrated minutes left, finish time |
| `sensor.paseo_<worktree>_active_ticket` | id of the working ticket |
| `sensor.paseo_<worktree>_open_questions` | number of open questions |
| `sensor.paseo_<worktree>_stuck` | number of stuck tickets |
| `sensor.paseo_<worktree>_deliverables` | number of deliverables |
| `sensor.paseo_<worktree>_last_update` | time of the last update of the run |
| `binary_sensor.paseo_<worktree>_working` | on while the run is working |

New runs appear with the push that brings them. A run that is gone becomes unavailable and its device can be deleted; the device comes back if the run returns. When two worktrees have the same name, Home Assistant adds its usual `_2` suffix to the second set of entity ids.

## Services

Every service takes an optional `config_entry_id` (only needed when more than one Paseo entry is loaded).

| Service | What it does |
|---|---|
| `paseo.answer_question` | Answers an open question (`worktree`, `question_id`, `choice`, optional `words` and `fingerprint`), for example `worktree: myproject-1a2b3c4d`, `question_id: Q1`, `choice: A`. |
| `paseo.resume_quota_agent` | Asks the plugin to resume rate-limited agents now (`agent_id`, default `all`). Returns the outcome per agent. |
| `paseo.cancel_ci_run` | Cancels a queued or running CI run (`run_id`). |
| `paseo.get_morning_brief` | Returns the waiting questions and a one-line text, for example for a morning automation. |

An unreachable plugin raises a Home Assistant error, and a rejected token starts reauthentication.

## Notifications

Every open question is pushed to your phones through the legacy `notify.mobile_app_*` services, with up to three answer buttons (the recommended option first). A tap fires `mobile_app_notification_action` and the integration answers the question; no automation is needed. The push is cleared after the answer or when the question disappears. One reminder is sent for a blocking question that stays open (not while bedtime is on). Held questions raise no push and stay in `sensor.paseo_held_questions` and the morning brief.

Morning brief automation snippet:

```yaml
- action: paseo.get_morning_brief
  response_variable: brief
- if:
    - condition: template
      value_template: "{{ brief.count > 0 }}"
  then:
    - action: notify.mobile_app_my_phone
      data:
        message: "{{ brief.text }}"
```

## Data sent

The plugin sends, in clear text over your local network (HTTP, no TLS) and only to Home Assistant: run, ticket, question, agent, deliverable, quota and CI-queue information, including titles, question texts and file paths of your Paseo work. Home Assistant holds it in its state machine; list attributes are excluded from the recorder. In the other direction Home Assistant sends the bridge token as a bearer header, and for answers the question key, id, chosen letter and optional free text.

Notification texts (question title, the first 400 characters of the question, option labels) travel to your phones through the Home Assistant mobile app's push channel, which for many phones passes through the vendor's push service. Treat question texts as data that may reach those services. This integration sends nothing to anyone else.

Diagnostics contain the plugin host, counts and state, and leave out the token, the webhook id, phone names and all question, ticket and agent texts.

## Removal

Delete the integration under Settings, Devices & services, Paseo. Home Assistant tells the plugin to stop pushing and forgets its stored notification state. If you installed it by hand or through HACS, remove the integration files afterwards and restart Home Assistant. Remove the plugin and its token on the Paseo host.

## License

MIT, see [LICENSE](LICENSE).
